"""LAN network, Host and Origin policies are explicit, rather than all private IPs."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from watchlib.access import AccessPolicy


class AccessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = AccessPolicy({
            'allowed_hosts': ['192.168.110.79', '127.0.0.1', 'localhost'],
            'allowed_networks': ['192.168.110.0/24', '127.0.0.0/8'],
        }, 8768)

    def test_only_configured_lan_and_loopback_networks_allowed(self):
        for peer in ['192.168.110.79', '192.168.110.200', '127.0.0.1', '127.0.0.8']:
            with self.subTest(peer=peer):
                self.assertTrue(self.policy.permits_peer(peer))
        for peer in ['192.168.111.1', '192.168.1.2', '10.0.0.1', '172.16.0.1', '8.8.8.8', 'not-an-ip']:
            with self.subTest(peer=peer):
                self.assertFalse(self.policy.permits_peer(peer))

    def test_ipv4_mapped_loopback_uses_configured_ipv4_policy(self):
        self.assertTrue(self.policy.permits_peer('::ffff:127.0.0.1'))
        self.assertTrue(self.policy.permits_peer('::ffff:192.168.110.3'))
        self.assertFalse(self.policy.permits_peer('::ffff:8.8.8.8'))
        self.assertFalse(self.policy.permits_peer('::1'))

    def test_exact_configured_host_and_port_required(self):
        self.assertEqual(self.policy.authority('192.168.110.79:8768'), ('192.168.110.79', 8768))
        self.assertEqual(self.policy.authority('LOCALHOST:8768'), ('localhost', 8768))
        for value in ['', '192.168.110.79', '192.168.110.79:80', '192.168.110.79:8769', 'other.example:8768', 'localhost.evil.example:8768']:
            with self.subTest(value=value):
                self.assertIsNone(self.policy.authority(value))

    def test_host_rejects_userinfo_paths_queries_fragments_and_whitespace(self):
        for value in ['user@localhost:8768', 'user:password@localhost:8768', 'localhost:8768/', 'localhost:8768/path', 'localhost:8768?x=1', 'localhost:8768#part', ' localhost:8768', 'localhost:8768\t', 'localhost:8768\r\nX:evil', 'localhost:bad', 'localhost:99999']:
            with self.subTest(value=value):
                self.assertIsNone(self.policy.authority(value))

    def test_request_requires_both_allowed_peer_and_host(self):
        self.assertTrue(self.policy.permits_request('192.168.110.2', '192.168.110.79:8768'))
        self.assertFalse(self.policy.permits_request('8.8.8.8', '192.168.110.79:8768'))
        self.assertFalse(self.policy.permits_request('127.0.0.1', 'evil.example:8768'))

    def test_origin_must_match_current_request_authority_not_merely_any_allowed_host(self):
        host = '192.168.110.79:8768'
        self.assertTrue(self.policy.permits_origin('http://' + host, host))
        self.assertFalse(self.policy.permits_origin('http://localhost:8768', host))
        self.assertFalse(self.policy.permits_origin('http://127.0.0.1:8768', host))
        self.assertFalse(self.policy.permits_origin('https://' + host, host))
        self.assertFalse(self.policy.permits_origin('http://192.168.110.79:8769', host))

    def test_origin_rejects_null_userinfo_paths_queries_fragments(self):
        host = '192.168.110.79:8768'
        for origin in ['null', 'http://user@' + host, 'http://' + host + '/', 'http://' + host + '?x=1', 'http://' + host + '#x', 'file:///tmp/test.html']:
            with self.subTest(origin=origin):
                self.assertFalse(self.policy.permits_origin(origin, host))

    def test_absent_origin_allowed_for_cli_header_enforced_in_http_handler(self):
        self.assertTrue(self.policy.permits_origin(None, '127.0.0.1:8768'))
        self.assertTrue(self.policy.permits_origin('', '127.0.0.1:8768'))
        self.assertFalse(self.policy.permits_origin('http://evil.example:8768', 'evil.example:8768'))


if __name__ == '__main__':
    unittest.main()
