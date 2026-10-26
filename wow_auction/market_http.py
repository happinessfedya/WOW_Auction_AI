"""Bounded HTTPS transport. Credentials never enter diagnostics or redirects."""
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import http.client
import json
import math
import multiprocessing
from multiprocessing import shared_memory
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Mapping

from .market_common import MarketError

ALLOWED_HOSTS = {'public-data.tradeskillmaster.com', 'api.undermine.exchange'}
SAFE_HEADERS = {'date', 'last-modified', 'etag', 'content-type', 'content-length',
                'content-encoding', 'retry-after', 'cache-control'}
_CONTROL_BYTES = 65536
_COPY_BYTES = 65536
_PROCESS_CLEANUP_SECONDS = 1.0


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict
    received_at_ns: int
    elapsed_seconds: float
    wire_bytes: int


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_timestamp(value):
    if not value or not isinstance(value, str) or len(value) > 128:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return max(0, int(parsed.astimezone(timezone.utc).timestamp()))
    except (ValueError, TypeError, OverflowError):
        return None


def retry_deadline(value, now_seconds):
    """Do not cap away a server's minimum delay; schedule it, never block sleep."""
    if isinstance(value, str):
        value = value.strip()
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 1024:
        return now_seconds + int(value)
    timestamp = http_timestamp(value)
    return max(now_seconds, timestamp) if timestamp is not None else None


def _response_headers(headers, *, strict_framing=False):
    result = {}
    for key, value in (() if headers is None else headers.items()):
        key = key.lower()
        if key in SAFE_HEADERS:
            if strict_framing and key in {'content-length', 'content-encoding'} and key in result:
                raise MarketError('ambiguous_market_response_framing')
            result[key] = str(value)[:1024]
    return result


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise MarketError('market_request_deadline_exceeded')
    return remaining


def _write_packet(control, packet):
    raw = json.dumps(packet, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    if len(raw) > _CONTROL_BYTES - 4:
        raise MarketError('market_transport_control_limit')
    control.buf[4:4 + len(raw)] = raw
    control.buf[:4] = len(raw).to_bytes(4, 'little')


def _read_packet(control):
    size = int.from_bytes(control.buf[:4], 'little')
    if not 0 < size <= _CONTROL_BYTES - 4:
        raise MarketError('invalid_market_transport_control')
    try:
        packet = json.loads(bytes(control.buf[4:4 + size]))
    except (ValueError, RecursionError):
        raise MarketError('invalid_market_transport_control') from None
    if not isinstance(packet, dict):
        raise MarketError('invalid_market_transport_control')
    return packet


def _wait_packet(control, event, deadline):
    if not event.wait(_remaining(deadline)):
        raise MarketError('market_request_deadline_exceeded')
    _remaining(deadline)
    event.clear()
    return _read_packet(control)


def _network_process(control_name, outbound, inbound, request_args, started, deadline, limits):
    """One killable request; shared memory is owned and removed by the parent.

    DNS and HTTP header parsing can block inside stdlib routines without a
    cancellable operation-level API. Process isolation bounds that entire path
    without abandoning a thread which could continue making network requests.
    """
    control = shared_memory.SharedMemory(name=control_name)
    body_memory = None
    try:
        opener = urllib.request.build_opener(NoRedirect())
        client = HttpClient(opener=opener, **limits)
        request = urllib.request.Request(request_args['url'], headers=request_args['headers'], method='GET')
        response = client._execute(request, started, deadline)
        _remaining(deadline)
        _write_packet(control, {'state': 'allocate', 'body_bytes': len(response.body)})
        outbound.set()
        packet = _wait_packet(control, inbound, deadline)
        if packet.get('state') != 'allocation':
            raise MarketError('invalid_market_transport_control')
        if response.body:
            body_memory = shared_memory.SharedMemory(name=packet['name'])
            if len(body_memory.buf) < len(response.body):
                raise MarketError('invalid_market_transport_control')
            for offset in range(0, len(response.body), _COPY_BYTES):
                _remaining(deadline)
                chunk = response.body[offset:offset + _COPY_BYTES]
                body_memory.buf[offset:offset + len(chunk)] = chunk
        _remaining(deadline)
        _write_packet(control, {'state': 'ready', 'status': response.status, 'headers': response.headers,
                               'received_at_ns': response.received_at_ns, 'wire_bytes': response.wire_bytes})
        outbound.set()
    except BaseException as exc:
        # No server body, URL, headers, credential, or exception text crosses an
        # error boundary. Errors from the bounded local protocol are safe codes.
        code = exc.code if isinstance(exc, MarketError) else 'market_network_unavailable'
        try:
            _write_packet(control, {'state': 'error', 'code': code})
            outbound.set()
        except BaseException:
            pass
    finally:
        if body_memory is not None:
            body_memory.close()
        control.close()


class HttpClient:
    def __init__(self, *, timeout=30, max_wire_bytes=64 * 1024 * 1024,
                 max_body_bytes=256 * 1024 * 1024, opener=None):
        if (type(timeout) not in (int, float) or not 1 <= timeout <= 60 or not math.isfinite(timeout)
                or type(max_wire_bytes) is not int or not 1 <= max_wire_bytes <= 256 * 1024 * 1024
                or type(max_body_bytes) is not int or not 1 <= max_body_bytes <= 512 * 1024 * 1024):
            raise MarketError('invalid_http_limits')
        self.timeout, self.max_wire_bytes, self.max_body_bytes = timeout, max_wire_bytes, max_body_bytes
        self._injected_opener = opener is not None
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def request(self, url, *, headers=None, data=None, method='GET'):
        if (not isinstance(url, str) or not url or len(url) > 8192 or not url.isascii()
                or any(ord(character) <= 32 or ord(character) == 127 for character in url)):
            raise MarketError('untrusted_market_url')
        try:
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.port not in (None, 443)
                    or parsed.username is not None or parsed.password is not None or parsed.fragment):
                raise ValueError
        except ValueError:
            raise MarketError('untrusted_market_url') from None
        if any(key.casefold() in {'access_token', 'client_secret', 'apikey', 'api_key', 'token'}
               for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)):
            raise MarketError('credential_in_query_forbidden')
        if headers is not None and not isinstance(headers, Mapping):
            raise MarketError('invalid_http_header')
        actual = {'User-Agent': 'WoWAuctionIntelligence/0.2 local-market-reader', 'Accept-Encoding': 'gzip', **(headers or {})}
        seen = set()
        for key, value in actual.items():
            if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                    or key.lower() in {'host', 'proxy-authorization', 'content-length', 'transfer-encoding'}
                    or key.lower() in seen or not isinstance(value, str)
                    or any(ord(character) < 32 or ord(character) > 255 or ord(character) == 127 for character in value)):
                raise MarketError('invalid_http_header')
            seen.add(key.lower())
        if not isinstance(method, str) or method != 'GET' or data is not None:
            raise MarketError('unsupported_market_request')
        started = time.monotonic()
        deadline = started + self.timeout
        request = urllib.request.Request(url, headers=actual, data=data, method=method)
        if self._injected_opener:
            # Test doubles run synchronously for deterministic monotonic clocks.
            # Production always uses the killable process, including DNS/headers.
            return self._execute(request, started, deadline)
        return self._isolated_request(url, actual, started, deadline)

    def _isolated_request(self, url, headers, started, deadline, *, process_target=None):
        context = multiprocessing.get_context('spawn')
        control = shared_memory.SharedMemory(create=True, size=_CONTROL_BYTES)
        body_memory, process, process_started = None, None, False
        try:
            outbound, inbound = context.Event(), context.Event()
            limits = {'timeout': self.timeout, 'max_wire_bytes': self.max_wire_bytes,
                      'max_body_bytes': self.max_body_bytes}
            process = context.Process(target=process_target or _network_process,
                args=(control.name, outbound, inbound, {'url': url, 'headers': headers}, started, deadline, limits))
            process.start()
            process_started = True
            packet = _wait_packet(control, outbound, deadline)
            if packet.get('state') == 'error':
                raise MarketError(packet['code'])
            size = packet.get('body_bytes')
            if packet.get('state') != 'allocate' or type(size) is not int or not 0 <= size <= self.max_body_bytes:
                raise MarketError('invalid_market_transport_control')
            if size:
                body_memory = shared_memory.SharedMemory(create=True, size=size)
            _remaining(deadline)
            _write_packet(control, {'state': 'allocation', 'name': body_memory.name if size else None})
            inbound.set()
            packet = _wait_packet(control, outbound, deadline)
            if packet.get('state') == 'error':
                raise MarketError(packet['code'])
            if packet.get('state') != 'ready':
                raise MarketError('invalid_market_transport_control')
            parts = []
            for offset in range(0, size, _COPY_BYTES):
                _remaining(deadline)
                parts.append(bytes(body_memory.buf[offset:min(offset + _COPY_BYTES, size)]))
            body = b''.join(parts)
            _remaining(deadline)
            return HttpResponse(packet['status'], body, packet['headers'], packet['received_at_ns'],
                                time.monotonic() - started, packet['wire_bytes'])
        except MarketError:
            raise
        except (OSError, ValueError, EOFError):
            raise MarketError('market_network_unavailable') from None
        finally:
            alive = False
            if process is not None and process_started:
                process.join(0)
                if process.is_alive():
                    process.terminate()
                    process.join(_PROCESS_CLEANUP_SECONDS / 2)
                if process.is_alive():
                    process.kill()
                    process.join(_PROCESS_CLEANUP_SECONDS / 2)
                alive = process.is_alive()
                if not alive:
                    process.close()
            for memory in (body_memory, control):
                if memory is not None:
                    memory.close()
                    memory.unlink()
            if alive:
                raise MarketError('market_request_cleanup_failed')

    def _execute(self, request, started, deadline):
        try:
            response = self.opener.open(request, timeout=_remaining(deadline))
        except urllib.error.HTTPError as exc:
            # Error payloads can echo credentials. Never read/store/log them.
            try:
                metadata = _response_headers(exc.headers)
            finally:
                exc.close()
            _remaining(deadline)
            return HttpResponse(exc.code, b'', metadata, time.time_ns(), time.monotonic() - started, 0)
        except (urllib.error.URLError, socket.timeout, OSError, http.client.HTTPException):
            _remaining(deadline)
            raise MarketError('market_network_unavailable') from None
        try:
            with response:
                _remaining(deadline)
                code = response.status
                metadata = _response_headers(response.headers, strict_framing=code == 200)
                if code != 200:
                    _remaining(deadline)
                    return HttpResponse(code, b'', metadata, time.time_ns(), time.monotonic() - started, 0)
                length = metadata.get('content-length')
                if length is not None:
                    if not length.isascii() or not length.isdecimal() or len(length) > 10:
                        raise MarketError('incomplete_market_response')
                    if int(length) > self.max_wire_bytes:
                        raise MarketError('market_wire_size_limit')
                encoding = metadata.get('content-encoding', 'identity').lower()
                if encoding not in {'gzip', 'identity'}:
                    raise MarketError('unsupported_content_encoding')
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else None
                wire, size, parts = 0, 0, []
                while True:
                    remaining = _remaining(deadline)
                    # HTTPResponse.read may wait for an entire requested buffer.
                    # read1 limits a body step to a single underlying read.
                    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                    if sock is not None:
                        sock.settimeout(remaining)
                    reader = getattr(response, 'read1', response.read)
                    chunk = reader(65536)
                    _remaining(deadline)
                    if not chunk:
                        break
                    wire += len(chunk)
                    if wire > self.max_wire_bytes:
                        raise MarketError('market_wire_size_limit')
                    decoded = decoder.decompress(chunk, self.max_body_bytes - size + 1) if decoder else chunk
                    _remaining(deadline)
                    size += len(decoded)
                    if size > self.max_body_bytes or (decoder and decoder.unconsumed_tail):
                        raise MarketError('market_body_size_limit')
                    parts.append(decoded)
                if decoder and (not decoder.eof or decoder.unused_data):
                    raise MarketError('incomplete_or_concatenated_gzip')
                if length is not None and int(length) != wire:
                    raise MarketError('incomplete_market_response')
                body = b''.join(parts)
                _remaining(deadline)
                return HttpResponse(code, body, metadata, time.time_ns(), time.monotonic() - started, wire)
        except (urllib.error.URLError, socket.timeout, OSError, http.client.HTTPException, zlib.error):
            _remaining(deadline)
            raise MarketError('market_response_read_failed') from None
