"""Unauthenticated, pinned GitHub reads with a killable overall request deadline.

The stage-2 transport's isolated GET engine is reused without broadening its
public market host policy. This module owns a separate, narrower URL boundary.
"""
import re
import time
from urllib.parse import quote, unquote, urlsplit

from .market_common import MarketError
from .market_http import HttpClient, HttpResponse


class SourceTransportError(ValueError):
    """Safe code only: no remote body, source URL, credentials or local paths."""


MAX_BYTES = 32 * 1024 * 1024
TIMEOUT_SECONDS = 30
_API_ROOT = '/repos/ATTWoWAddon/AllTheThings/'
_SHA = r'[0-9a-f]{40}'


def _validate(url, limit):
    if type(limit) is not int or not 1 <= limit <= MAX_BYTES:
        raise SourceTransportError('source_invalid_byte_limit')
    if (not isinstance(url, str) or not 1 <= len(url) <= 8192 or not url.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or '\\' in url or '#' in url):
        raise SourceTransportError('source_url_forbidden')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in {'api.github.com', 'raw.githubusercontent.com'}
                or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.netloc.lower() not in {parsed.hostname, parsed.hostname + ':443'}):
            raise ValueError()
    except ValueError:
        raise SourceTransportError('source_url_forbidden') from None
    if parsed.hostname == 'api.github.com':
        if re.fullmatch(_API_ROOT + 'git/trees/' + _SHA, parsed.path):
            if parsed.query != 'recursive=1':
                raise SourceTransportError('source_query_forbidden')
        elif (re.fullmatch(_API_ROOT + 'git/commits/' + _SHA, parsed.path)
              or re.fullmatch(_API_ROOT + 'commits/(?:master|' + _SHA + ')', parsed.path)):
            if '?' in url:
                raise SourceTransportError('source_query_forbidden')
        else:
            raise SourceTransportError('source_path_forbidden')
    else:
        if '?' in url:
            raise SourceTransportError('source_query_forbidden')
        if not re.fullmatch('/ATTWoWAddon/AllTheThings/' + _SHA + '/.+', parsed.path):
            raise SourceTransportError('source_path_forbidden')
        try:
            decoded = unquote(parsed.path, encoding='utf-8', errors='strict')
        except UnicodeError:
            raise SourceTransportError('source_path_forbidden') from None
        if (quote(decoded, safe='/') != parsed.path or '\\' in decoded or re.search(r'%[0-9a-fA-F]{2}', decoded)
                or any(ord(char) <= 31 or ord(char) == 127 for char in decoded)
                or any(part in {'', '.', '..'} for part in decoded.split('/')[1:])):
            raise SourceTransportError('source_path_forbidden')


def public_get(url: str, limit: int, *, _transport=None) -> bytes:
    """GET only, fixed public headers, no redirects, <=30s plus bounded cleanup.

    ``_transport`` is a synchronous test hook after URL/size validation. It does
    not replace the production timeout mechanism: the default always delegates
    to the existing killable process, including DNS, headers and body transfer.
    A test hook must return promptly; its late result is rejected as well.
    """
    _validate(url, limit)
    started = time.monotonic()
    deadline = started + TIMEOUT_SECONDS
    headers = {'User-Agent': 'WoWAuctionIntelligence/0.3 public-source-reader',
               'Accept': 'application/json, text/plain', 'Accept-Encoding': 'gzip'}
    try:
        client = HttpClient(timeout=TIMEOUT_SECONDS, max_wire_bytes=limit, max_body_bytes=limit)
        response = (client._isolated_request(url, headers, started, deadline) if _transport is None
                    else _transport(client, url, headers, started, deadline))
    except MarketError as exc:
        code = {'market_request_deadline_exceeded': 'source_request_deadline_exceeded',
                'market_request_cleanup_failed': 'source_request_cleanup_failed',
                'market_body_size_limit': 'source_body_limit',
                'market_wire_size_limit': 'source_wire_limit'}.get(exc.code, 'source_network_or_response_failure')
        raise SourceTransportError(code) from None
    except (OSError, ValueError, TypeError):
        raise SourceTransportError('source_network_or_response_failure') from None
    if time.monotonic() >= deadline:
        raise SourceTransportError('source_request_deadline_exceeded')
    if (not isinstance(response, HttpResponse) or type(response.status) is not int
            or type(response.body) is not bytes or type(response.wire_bytes) is not int or response.wire_bytes < 0):
        raise SourceTransportError('source_invalid_transport_response')
    if response.status != 200:
        # Neither error bodies nor redirect locations enter diagnostics.
        raise SourceTransportError('source_http_status_not_200')
    if len(response.body) > limit:
        raise SourceTransportError('source_body_limit')
    if response.wire_bytes > limit:
        raise SourceTransportError('source_wire_limit')
    return response.body
