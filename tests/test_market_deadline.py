"""S2-03: total time, slow chunks, and cancellation of blocking subprocesses.

No requests to any network service. Synthetic process targets use the same
bounded shared-memory handshake as the production transport.
"""
import gzip
import multiprocessing
from multiprocessing import shared_memory
import time
import unittest
from unittest.mock import patch

from wow_auction.market_common import MarketError
from wow_auction.market_http import HttpClient, _read_packet, _write_packet, _wait_packet
from tests.test_market_http import Opener, Response, PUBLIC_URL


class Clock:
    def __init__(self): self.now = 10.0
    def __call__(self): return self.now


class SlowResponse(Response):
    def __init__(self, clock, body, *, delay=29, headers=None):
        super().__init__(body, headers=headers, chunk_size=1)
        self.clock, self.delay, self.timeouts = clock, delay, []
        self.fp = type('FP', (), {'raw': type('Raw', (), {'_sock': self})()})()
    def settimeout(self, value): self.timeouts.append(value)
    def read1(self, size):
        delay = min(self.delay, self.timeouts[-1])
        self.clock.now += delay
        return super().read(size)
    def read(self, size):
        raise AssertionError('Buffered read must not be used when read1 is available')


def _synthetic_process(control_name, outbound, inbound, request_args, started, deadline, limits):
    control = shared_memory.SharedMemory(name=control_name)
    body_memory = None
    try:
        scenario = request_args['headers'].get('X-Synthetic-Scenario', 'success')
        if scenario == 'blocked-before-headers':
            time.sleep(60)
            return
        size = 131073 if scenario == 'success' else 10
        _write_packet(control, {'state': 'allocate', 'body_bytes': size})
        outbound.set()
        allocation = _wait_packet(control, inbound, deadline)
        body_memory = shared_memory.SharedMemory(name=allocation['name'])
        if scenario == 'blocked-body-transfer':
            time.sleep(60)
            return
        body_memory.buf[:size] = b'A' * size
        _write_packet(control, {'state': 'ready', 'status': 200, 'headers': {'content-type': 'text/csv'},
                               'received_at_ns': 123, 'wire_bytes': size})
        outbound.set()
    finally:
        if body_memory is not None: body_memory.close()
        control.close()


class DeadlineTests(unittest.TestCase):
    def test_regular_slow_bytes_cannot_reset_total_deadline_without_length(self):
        clock = Clock()
        response = SlowResponse(clock, b'0123456789')
        with patch('wow_auction.market_http.time.monotonic', side_effect=clock):
            with self.assertRaisesRegex(MarketError, '^market_request_deadline_exceeded$'):
                HttpClient(timeout=30, opener=Opener(response)).request(PUBLIC_URL)
        self.assertEqual(response.timeouts, [30, 1])
        self.assertEqual(clock.now, 40)
        self.assertEqual(response.read_calls, 2)
        self.assertTrue(response.closed)

    def test_gzip_and_declared_length_share_the_same_total_deadline(self):
        for headers in ({'Content-Encoding': 'gzip'}, {'Content-Encoding': 'gzip', 'Content-Length': '30'}):
            with self.subTest(headers=headers):
                clock = Clock()
                response = SlowResponse(clock, gzip.compress(b'payload'), delay=6, headers=headers)
                with patch('wow_auction.market_http.time.monotonic', side_effect=clock):
                    with self.assertRaisesRegex(MarketError, '^market_request_deadline_exceeded$'):
                        HttpClient(timeout=30, opener=Opener(response)).request(PUBLIC_URL)
                self.assertEqual(response.timeouts, [30, 24, 18, 12, 6])
                self.assertTrue(response.closed)

    def test_late_headers_are_closed_and_never_return_a_success(self):
        clock = Clock()
        response = Response(b'payload')
        class SlowHeaders:
            def open(self, request, *, timeout):
                self.received_timeout = timeout
                clock.now += 31
                return response
        opener = SlowHeaders()
        with patch('wow_auction.market_http.time.monotonic', side_effect=clock):
            with self.assertRaisesRegex(MarketError, '^market_request_deadline_exceeded$'):
                HttpClient(timeout=30, opener=opener).request(PUBLIC_URL)
        self.assertEqual(opener.received_timeout, 30)
        self.assertEqual(response.read_calls, 0)
        self.assertTrue(response.closed)

    def test_connect_and_headers_consume_body_budget(self):
        clock = Clock()
        response = SlowResponse(clock, b'abc', delay=4)
        class SlowHeaders:
            def open(self, request, *, timeout):
                clock.now += 28
                return response
        with patch('wow_auction.market_http.time.monotonic', side_effect=clock):
            with self.assertRaisesRegex(MarketError, '^market_request_deadline_exceeded$'):
                HttpClient(timeout=30, opener=SlowHeaders()).request(PUBLIC_URL)
        self.assertEqual(response.timeouts, [2])
        self.assertTrue(response.closed)

    def test_fast_small_response_still_succeeds_with_declining_body_timeouts(self):
        clock = Clock()
        response = SlowResponse(clock, b'abc', delay=1)
        with patch('wow_auction.market_http.time.monotonic', side_effect=clock):
            result = HttpClient(timeout=30, opener=Opener(response)).request(PUBLIC_URL)
        self.assertEqual(result.body, b'abc')
        self.assertEqual(result.elapsed_seconds, 4)
        self.assertEqual(response.timeouts, [30, 29, 28, 27])

    def test_process_protocol_moves_large_body_without_a_blocking_pipe_receive(self):
        client = HttpClient(timeout=10)
        started = time.monotonic()
        result = client._isolated_request(PUBLIC_URL, {}, started, started + 10,
                                          process_target=_synthetic_process)
        self.assertEqual(result.body, b'A' * 131073)
        self.assertEqual(result.wire_bytes, 131073)
        self.assertEqual(result.received_at_ns, 123)
        self.assertLess(result.elapsed_seconds, 10)

    def test_blocked_process_is_killed_and_shared_memory_removed_for_headers_and_body(self):
        original_memory = shared_memory.SharedMemory
        for scenario in ('blocked-before-headers', 'blocked-body-transfer'):
            names = []
            def capture_memory(*args, **kwargs):
                result = original_memory(*args, **kwargs)
                if kwargs.get('create'): names.append(result.name)
                return result
            before = {p.pid for p in multiprocessing.active_children()}
            started = time.monotonic()
            with self.subTest(scenario=scenario), \
                    patch('wow_auction.market_http.shared_memory.SharedMemory', side_effect=capture_memory):
                with self.assertRaisesRegex(MarketError, '^market_request_deadline_exceeded$'):
                    HttpClient(timeout=1)._isolated_request(PUBLIC_URL, {'X-Synthetic-Scenario': scenario},
                        started, started + 1, process_target=_synthetic_process)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
            self.assertEqual(len(names), 1 if scenario == 'blocked-before-headers' else 2)
            for name in names:
                with self.assertRaises(FileNotFoundError): original_memory(name=name)


if __name__ == '__main__':
    unittest.main()
