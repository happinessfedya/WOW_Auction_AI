import unittest
from unittest.mock import patch

from wow_auction.knowledge_transport import public_get, SourceTransportError, MAX_BYTES
from wow_auction.market_http import HttpClient, HttpResponse, ALLOWED_HOSTS
from wow_auction.market_common import MarketError


SHA = 'a' * 40
TREE = 'https://api.github.com/repos/ATTWoWAddon/AllTheThings/git/trees/' + SHA + '?recursive=1'
RAW = 'https://raw.githubusercontent.com/ATTWoWAddon/AllTheThings/' + SHA + '/LICENSE'


def response(body=b'ok', status=200, wire_bytes=None):
    return HttpResponse(status, body, {}, 123, 0.001, len(body) if wire_bytes is None else wire_bytes)


class KnowledgeTransportTests(unittest.TestCase):
    def test_allowed_public_repository_routes(self):
        for url in (TREE, RAW,
            'https://api.github.com/repos/ATTWoWAddon/AllTheThings/commits/master',
            'https://api.github.com/repos/ATTWoWAddon/AllTheThings/commits/' + SHA,
            'https://api.github.com/repos/ATTWoWAddon/AllTheThings/git/commits/' + SHA,
            RAW.replace('githubusercontent.com/', 'githubusercontent.com:443/'),
            RAW.replace('/LICENSE', '/.contrib/.db/standard/Old%20Expansion/%D0%A2%D0%B5%D1%81%D1%82.lua')):
            with self.subTest(url=url):
                self.assertEqual(public_get(url, 20, _transport=lambda *args: response()), b'ok')

    def test_untrusted_url_rejected_before_transport(self):
        invalid = [
            RAW.replace('https:', 'http:'), RAW.replace('raw.githubusercontent.com', 'raw.githubusercontent.com.evil.test'),
            RAW.replace('raw.githubusercontent.com', 'user:SECRET@raw.githubusercontent.com'),
            RAW.replace('raw.githubusercontent.com', '@raw.githubusercontent.com'),
            RAW.replace('raw.githubusercontent.com', 'raw.githubusercontent.com:444'),
            RAW.replace('raw.githubusercontent.com', 'raw.githubusercontent.com:'),
            RAW + '#fragment', RAW + '#', RAW + '?token=SECRET', RAW + '?',
            RAW.replace('AllTheThings', 'OtherRepo'), RAW.replace(SHA, 'master'),
            RAW.replace('/LICENSE', '/../LICENSE'), RAW.replace('/LICENSE', '/a/../LICENSE'),
            RAW.replace('/LICENSE', '/%2e%2e/LICENSE'), RAW.replace('/LICENSE', '/a%2Fb.lua'),
            RAW.replace('/LICENSE', '/%252e%252e/LICENSE'),
            RAW.replace('/LICENSE', '/%00file.lua'), RAW.replace('/LICENSE', '/%5Cfile.lua'),
            RAW.replace('/LICENSE', '/%ZZfile.lua'), RAW.replace('/LICENSE', '//file.lua'),
            TREE + '&token=SECRET', TREE.replace('recursive=1', 'recursive=0'),
            TREE.replace('recursive=1', '%72ecursive=1'), TREE.replace('?recursive=1', ''),
            TREE.replace('/git/trees/', '/issues/'), '\n' + RAW, RAW + '\r', RAW + ' ']
        for url in invalid:
            with self.subTest(url=url), patch.object(HttpClient, '_isolated_request') as transport:
                with self.assertRaises(SourceTransportError) as error:
                    public_get(url, 100)
                transport.assert_not_called()
                self.assertNotIn('SECRET', str(error.exception))

    def test_invalid_limits_rejected_before_transport(self):
        for limit in (0, -1, True, 1.5, MAX_BYTES + 1, '20'):
            with self.subTest(limit=limit), patch.object(HttpClient, '_isolated_request') as transport:
                with self.assertRaises(SourceTransportError):
                    public_get(RAW, limit)
                transport.assert_not_called()

    def test_production_delegates_to_isolated_deadline_without_widening_market_hosts(self):
        original_hosts = set(ALLOWED_HOSTS)
        def isolated(client, url, headers, started, deadline):
            self.assertEqual(url, TREE)
            self.assertEqual((client.timeout, client.max_body_bytes, client.max_wire_bytes), (30, 4096, 4096))
            self.assertFalse(client._injected_opener)
            self.assertEqual((started, deadline), (10, 40))
            self.assertEqual(set(headers), {'User-Agent', 'Accept', 'Accept-Encoding'})
            return response()
        with patch('wow_auction.knowledge_transport.time.monotonic', return_value=10), \
                patch.object(HttpClient, '_isolated_request', isolated):
            self.assertEqual(public_get(TREE, 4096), b'ok')
        self.assertEqual(ALLOWED_HOSTS, original_hosts)
        self.assertNotIn('api.github.com', ALLOWED_HOSTS)

    def test_redirects_and_error_payload_never_returned(self):
        for status in (301, 302, 304, 400, 401, 403, 429, 500):
            with self.subTest(status=status), self.assertRaises(SourceTransportError) as error:
                public_get(RAW, 100, _transport=lambda *args: response(b'PRIVATE_ECHO', status=status))
            self.assertNotIn('PRIVATE_ECHO', str(error.exception))

    def test_postconditions_enforce_wire_body_and_response_contract(self):
        for reply in (response(b'12345'), response(b'ok', wire_bytes=5),
                      response(b'ok', wire_bytes=-1), response(b'ok', status=True), None):
            with self.subTest(reply=reply), self.assertRaises(SourceTransportError):
                public_get(RAW, 4, _transport=lambda *args: reply)
        self.assertEqual(public_get(RAW, 4, _transport=lambda *args: response(b'1234')), b'1234')

    def test_deadline_error_is_safe_and_late_hook_result_is_rejected(self):
        def timeout(*args):
            raise MarketError('market_request_deadline_exceeded')
        with self.assertRaisesRegex(SourceTransportError, '^source_request_deadline_exceeded$'):
            public_get(RAW, 100, _transport=timeout)
        with patch('wow_auction.knowledge_transport.time.monotonic', side_effect=[10, 40]):
            with self.assertRaisesRegex(SourceTransportError, '^source_request_deadline_exceeded$'):
                public_get(RAW, 100, _transport=lambda *args: response())


if __name__ == '__main__':
    unittest.main()
