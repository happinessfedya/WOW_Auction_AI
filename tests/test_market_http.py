"""Transport boundaries exercised with in-memory responses, never live services."""
import gzip
import http.client
import io
import socket
import unittest
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from unittest.mock import patch

from wow_auction.market_common import MarketError
from wow_auction.market_http import HttpClient, NoRedirect, http_timestamp, retry_deadline


PUBLIC_URL = 'https://public-data.tradeskillmaster.com/retail/eu/commodities.csv'
SECRET = 'synthetic-secret-must-not-appear-in-diagnostics'


def response_headers(**values):
    result = Message()
    for key, value in values.items():
        result[key.replace('_', '-')] = value
    return result


class Response:
    def __init__(self, body=b'', *, status=200, headers=None, chunk_size=None, read_error=None):
        self.status = status
        self.headers = {} if headers is None else headers
        self.stream = io.BytesIO(body)
        self.chunk_size = chunk_size
        self.read_error = read_error
        self.read_calls = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, size):
        self.read_calls += 1
        if self.read_error is not None and self.read_calls > 1:
            raise self.read_error
        return self.stream.read(min(size, self.chunk_size) if self.chunk_size else size)


class Opener:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class NoReadBody(io.BytesIO):
    def read(self, *_):
        raise AssertionError('Error bodies must never be read')


class RedirectScenario(urllib.request.HTTPSHandler):
    """Use urllib's real error/redirect chain while replacing only network I/O."""
    def __init__(self, status, target):
        super().__init__()
        self.status, self.target = status, target
        self.requests = []
        self.body = NoReadBody(SECRET.encode())

    def https_open(self, request):
        self.requests.append(request)
        headers = response_headers(Location=self.target)
        result = urllib.response.addinfourl(self.body, headers, request.full_url, self.status)
        result.msg = 'Synthetic redirect'
        return result


class HttpClientTests(unittest.TestCase):
    def assert_transport_error(self, response, code, **limits):
        opener = Opener(response)
        with self.assertRaises(MarketError) as caught:
            HttpClient(opener=opener, **limits).request(PUBLIC_URL)
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertTrue(response.closed)
        return response

    def test_identity_and_case_insensitive_safe_headers(self):
        response = Response(b'abcde', chunk_size=2, headers=response_headers(
            CONTENT_LENGTH='5', ETAG='"synthetic"', Set_Cookie=SECRET, X_Debug=SECRET))
        opener = Opener(response)
        clock_calls = []
        def clock():
            clock_calls.append(None)
            return 10.0 if len(clock_calls) <= 2 else 10.25
        with patch('wow_auction.market_http.time.time_ns', return_value=123456), \
                patch('wow_auction.market_http.time.monotonic', side_effect=clock):
            result = HttpClient(opener=opener, timeout=12).request(PUBLIC_URL)
        self.assertEqual((result.status, result.body, result.wire_bytes), (200, b'abcde', 5))
        self.assertEqual(result.headers, {'content-length': '5', 'etag': '"synthetic"'})
        self.assertEqual((result.received_at_ns, result.elapsed_seconds), (123456, .25))
        self.assertEqual(opener.calls[0][1], 12)
        self.assertTrue(response.closed)

    def test_valid_gzip_fragmentation_counts_wire_length(self):
        body = b'payload\n' * 50
        compressed = gzip.compress(body)
        response = Response(compressed, chunk_size=3, headers=response_headers(
            Content_Encoding='GZIP', Content_Length=str(len(compressed))))
        result = HttpClient(opener=Opener(response), max_body_bytes=len(body)).request(PUBLIC_URL)
        self.assertEqual(result.body, body)
        self.assertEqual(result.wire_bytes, len(compressed))
        self.assertGreater(response.read_calls, 2)

    def test_http_errors_never_read_or_expose_payloads(self):
        for status in (401, 403, 429, 500, 502, 503, 504, 304, 302):
            with self.subTest(status=status):
                body = NoReadBody(SECRET.encode())
                headers = response_headers(Retry_After='900', Set_Cookie=SECRET)
                error = urllib.error.HTTPError(PUBLIC_URL, status, SECRET, headers, body)
                result = HttpClient(opener=Opener(error)).request(
                    PUBLIC_URL, headers={'Authorization': f'Bearer {SECRET}'})
                self.assertEqual((result.status, result.body, result.wire_bytes), (status, b'', 0))
                self.assertEqual(result.headers, {'retry-after': '900'})
                self.assertTrue(body.closed)
                self.assertNotIn(SECRET, repr(result))

    def test_error_without_headers_is_safely_closed(self):
        body = NoReadBody(SECRET.encode())
        result = HttpClient(opener=Opener(urllib.error.HTTPError(
            PUBLIC_URL, 403, SECRET, None, body))).request(PUBLIC_URL)
        self.assertEqual(result.headers, {})
        self.assertTrue(body.closed)

    def test_non_200_response_objects_also_ignore_body(self):
        for status in (201, 204, 304, 401, 403, 429, 503):
            with self.subTest(status=status):
                response = Response(SECRET.encode(), status=status)
                result = HttpClient(opener=Opener(response)).request(PUBLIC_URL)
                self.assertEqual((result.status, result.body, response.read_calls), (status, b'', 0))
                self.assertTrue(response.closed)

    def test_redirects_never_forward_credentials_even_to_an_allowed_host(self):
        for status in (301, 302, 303, 307, 308):
            for target in ('https://untrusted.example/collect', 'https://eu.api.blizzard.com/collect'):
                with self.subTest(status=status, target=target):
                    network = RedirectScenario(status, target)
                    opener = urllib.request.build_opener(NoRedirect(), network)
                    result = HttpClient(opener=opener).request(
                        PUBLIC_URL, headers={'Authorization': f'Bearer {SECRET}'})
                    self.assertEqual(result.status, status)
                    self.assertEqual(len(network.requests), 1)
                    self.assertEqual(network.requests[0].full_url, PUBLIC_URL)
                    self.assertEqual(network.requests[0].get_header('Authorization'), f'Bearer {SECRET}')
                    self.assertTrue(network.body.closed)
                    self.assertNotIn(SECRET, repr(result))

    def test_default_opener_disables_redirects(self):
        redirects = [handler for handler in HttpClient().opener.handlers
                     if isinstance(handler, urllib.request.HTTPRedirectHandler)]
        self.assertEqual(len(redirects), 1)
        self.assertIsInstance(redirects[0], NoRedirect)

    def test_gzip_truncated_concatenated_and_trailing_data_are_rejected(self):
        compressed = gzip.compress(b'synthetic payload')
        for body in (compressed[:-1], compressed[:-8], b'', compressed + compressed, compressed + b'junk'):
            with self.subTest(body=body):
                self.assert_transport_error(Response(body, headers={'Content-Encoding': 'gzip'}),
                                            'incomplete_or_concatenated_gzip')

    def test_malformed_gzip_is_a_safe_read_error(self):
        self.assert_transport_error(Response(SECRET.encode(), headers={'Content-Encoding': 'gzip'}),
                                    'market_response_read_failed')

    def test_gzip_expansion_is_bounded(self):
        compressed = gzip.compress(b'A' * 1_000_000)
        response = Response(compressed, headers={'Content-Encoding': 'gzip'})
        self.assert_transport_error(response, 'market_body_size_limit', max_body_bytes=1024)
        self.assertEqual(response.read_calls, 1)

    def test_wire_and_uncompressed_body_limits(self):
        self.assert_transport_error(Response(b'12345'), 'market_wire_size_limit', max_wire_bytes=4)
        self.assert_transport_error(Response(b'12345'), 'market_body_size_limit', max_body_bytes=4)
        self.assert_transport_error(Response(gzip.compress(b'12345'), headers={'Content-Encoding': 'gzip'}),
                                    'market_wire_size_limit', max_wire_bytes=4)

    def test_declared_oversize_is_rejected_before_reading(self):
        response = Response(b'', headers={'Content-Length': '5000'})
        self.assert_transport_error(response, 'market_wire_size_limit', max_wire_bytes=1024)
        self.assertEqual(response.read_calls, 0)

    def test_missing_length_is_allowed_but_incorrect_length_is_rejected(self):
        result = HttpClient(opener=Opener(Response(b'chunked result'))).request(PUBLIC_URL)
        self.assertEqual(result.body, b'chunked result')
        for length in ('0', '2', '4', '-1', '3.0', 'three', '٣', '', '3, 3', '00000000003'):
            with self.subTest(length=length):
                self.assert_transport_error(Response(b'abc', headers={'Content-Length': length}),
                                            'incomplete_market_response')

    def test_duplicate_framing_headers_fail_closed(self):
        for field in ('Content-Length', 'Content-Encoding'):
            with self.subTest(field=field):
                headers = Message()
                headers[field] = '3' if field == 'Content-Length' else 'identity'
                headers[field.lower()] = '3' if field == 'Content-Length' else 'gzip'
                self.assert_transport_error(Response(b'abc', headers=headers), 'ambiguous_market_response_framing')

    def test_unsupported_encoding_is_not_silently_treated_as_text(self):
        self.assert_transport_error(Response(b'data', headers={'Content-Encoding': 'br'}),
                                    'unsupported_content_encoding')

    def test_partial_read_failure_is_never_returned_as_a_snapshot(self):
        for error in (socket.timeout(SECRET), OSError(SECRET),
                      http.client.IncompleteRead(b'partial', 100), urllib.error.URLError(SECRET)):
            with self.subTest(error=type(error).__name__):
                self.assert_transport_error(Response(b'partial data', chunk_size=2, read_error=error),
                                            'market_response_read_failed')

    def test_network_errors_are_safe_and_never_retried_in_transport(self):
        for error in (socket.timeout(SECRET), OSError(SECRET), urllib.error.URLError(SECRET),
                      http.client.BadStatusLine(SECRET)):
            with self.subTest(error=type(error).__name__):
                opener = Opener(error)
                with self.assertRaises(MarketError) as caught:
                    HttpClient(opener=opener).request(PUBLIC_URL)
                self.assertEqual(str(caught.exception), 'market_network_unavailable')
                self.assertEqual(len(opener.calls), 1)

    def test_untrusted_and_malformed_urls_are_rejected_without_network(self):
        for url in (None, b'https://api.undermine.exchange/', [], 'http://api.undermine.exchange/',
                    'https://api.undermine.exchange.evil.example/', 'https://api.undermine.exchange:bad/',
                    'https://api.undermine.exchange:65536/', 'https://api.undermine.exchange:444/',
                    'https://[malformed/', 'https://@api.undermine.exchange/',
                    f'https://{SECRET}@api.undermine.exchange/', 'https://api.undermine.exchange/#fragment',
                    '\nhttps://api.undermine.exchange/', 'https://api.undermine.exchange/line\npath',
                    'https://api.undermine.exchange/неэкранировано'):
            with self.subTest(url=url):
                opener = Opener(Response())
                with self.assertRaises(MarketError) as caught:
                    HttpClient(opener=opener).request(url)
                self.assertEqual(str(caught.exception), 'untrusted_market_url')
                self.assertEqual(opener.calls, [])

    def test_query_credentials_are_forbidden_including_empty_and_encoded_keys(self):
        for query in ('access_token=' + SECRET, 'CLIENT_SECRET=' + SECRET, 'api%5Fkey=' + SECRET,
                      'apikey=', 'token'):
            with self.subTest(query=query):
                opener = Opener(Response())
                with self.assertRaises(MarketError) as caught:
                    HttpClient(opener=opener).request(PUBLIC_URL + '?' + query)
                self.assertEqual(str(caught.exception), 'credential_in_query_forbidden')
                self.assertEqual(opener.calls, [])

    def test_request_header_injection_and_invalid_types_are_rejected(self):
        for headers in ({'X\r\nInjected': SECRET}, {'X-Test': SECRET + '\r\nInjected: yes'},
                        {4: SECRET}, {'X-Test': 4}, [], {'Host': 'evil.example'},
                        {'Proxy-Authorization': SECRET}, {'Content-Length': '100'},
                        {'Transfer-Encoding': 'chunked'}, {'X-Test': 'не latin1'},
                        {'Authorization': SECRET, 'authorization': SECRET}):
            with self.subTest(headers=headers):
                opener = Opener(Response())
                with self.assertRaises(MarketError) as caught:
                    HttpClient(opener=opener).request(PUBLIC_URL, headers=headers)
                self.assertEqual(str(caught.exception), 'invalid_http_header')
                self.assertEqual(opener.calls, [])

    def test_only_get_requests_without_bodies_are_supported(self):
        for options in ({'method': 'DELETE'}, {'method': []}, {'method': 'POST'}, {'data': b'body'},
                        {'method': 'POST', 'data': 'body'}, {'data': b''}):
            with self.subTest(options=options):
                rejected = Opener(Response())
                with self.assertRaises(MarketError):
                    HttpClient(opener=rejected).request(PUBLIC_URL, **options)
                self.assertEqual(rejected.calls, [])

    def test_blizzard_and_oauth_are_denied_before_opener_even_with_credentials(self):
        urls = ['https://oauth.battle.net/token', 'https://www.battle.net/oauth/token'] + [
            f'https://{region}.api.blizzard.com/data/wow/auctions/commodities'
            for region in ('eu', 'us', 'kr', 'tw')]
        for url in urls:
            for method in ('GET', 'POST'):
                with self.subTest(url=url, method=method):
                    opener = Opener(Response())
                    with self.assertRaises(MarketError) as caught:
                        HttpClient(opener=opener).request(url, method=method,
                            headers={'Authorization': 'Bearer ' + SECRET},
                            data=b'client_secret=' + SECRET.encode() if method == 'POST' else None)
                    self.assertEqual(str(caught.exception), 'untrusted_market_url')
                    self.assertEqual(opener.calls, [])

    def test_timeout_and_byte_limit_types_and_bounds(self):
        for key, values in (('timeout', (None, True, '30', 0, 61, 10**1000, float('nan'), float('inf'))),
                            ('max_wire_bytes', (None, True, '10', 1.5, 0, 256 * 1024**2 + 1)),
                            ('max_body_bytes', (None, True, '10', 1.5, 0, 512 * 1024**2 + 1))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(MarketError) as caught:
                    HttpClient(**{key: value})
                self.assertEqual(str(caught.exception), 'invalid_http_limits')
        self.assertEqual(HttpClient(timeout=60).timeout, 60)
        self.assertEqual(HttpClient(timeout=1.5).timeout, 1.5)


class RetryDeadlineTests(unittest.TestCase):
    def test_retry_seconds_are_not_capped_or_slept(self):
        with patch('wow_auction.market_http.time.sleep', side_effect=AssertionError('No transport sleeping')):
            self.assertEqual(retry_deadline('900', 1000), 1900)
            self.assertEqual(retry_deadline('9999999999', 1000), 10000000999)
            self.assertEqual(retry_deadline('99999999999', 1000), 100000000999)
            self.assertEqual(retry_deadline(' 120 ', 1000), 1120)
            self.assertEqual(retry_deadline('0', 1000), 1000)

    def test_http_dates_future_and_past_keep_server_minimum(self):
        date = 'Mon, 26 Oct 2026 10:00:00 GMT'
        timestamp = http_timestamp(date)
        self.assertEqual(timestamp, 1793008800)
        self.assertEqual(retry_deadline(date, timestamp - 3600), timestamp)
        self.assertEqual(retry_deadline(date, timestamp + 3600), timestamp + 3600)

    def test_invalid_retry_headers_do_not_invent_a_deadline(self):
        for value in (None, 20, [], '-1', '+5', '1.5', '١٢', 'not a date', '9' * 1025):
            with self.subTest(value=value):
                self.assertIsNone(retry_deadline(value, 1000))
        self.assertIsNone(http_timestamp('Mon, 26 Oct 2026 10:00:00'))


if __name__ == '__main__':
    unittest.main()
