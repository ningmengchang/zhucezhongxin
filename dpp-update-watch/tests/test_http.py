"""Loopback HTTP integration; no live EU calls, timer changes or shared state."""
from copy import deepcopy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from watchlib.storage import Store


SETTINGS = {
    'source_page': 'https://single-market-economy.ec.europa.eu/resources_en',
    'watch': [], 'desktop_notifications': False,
    'allowed_hosts': ['192.168.110.79', '127.0.0.1', 'localhost'],
    'allowed_networks': ['127.0.0.0/8'],
}
STATE = {'running': False, 'checks': [], 'events': [], 'artifacts': [], 'scheduler': {'installed': False}, 'config': SETTINGS}
UNSENT = object()


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='dpp-http-test-')
        self.store = Store(Path(self.temporary.name) / 'data')
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.done = threading.Event()
        self.gate.set()

        def fake_check(store, settings):
            self.entered.set()
            self.gate.wait(4)
            self.done.set()
            return {'status': 'unchanged'}

        self.patches = [patch.object(app, 'config', return_value=deepcopy(SETTINGS)),
                        patch.object(app, 'state', return_value=deepcopy(STATE)),
                        patch.object(app, 'check', side_effect=fake_check)]
        self.config_mock, self.state_mock, self.check_mock = [p.start() for p in self.patches]
        self.server = app.create_server(self.store, '127.0.0.1', 0)
        self.port = self.server.server_address[1]
        self.lan_host = '192.168.110.79:' + str(self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.gate.set()
        if self.entered.is_set():
            self.done.wait(2)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        for p in reversed(self.patches):
            p.stop()
        self.temporary.cleanup()

    def request(self, method, path, data=UNSENT, headers=None, default_guard=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        outbound = {'Host': self.lan_host}
        if method == 'POST' and default_guard:
            outbound['X-DPP-Watch'] = 'local'
            outbound['Content-Type'] = 'application/json'
        outbound.update(headers or {})
        body = json.dumps(data).encode() if data is not UNSENT else None
        try:
            connection.request(method, path, body=body, headers=outbound)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def test_lan_host_alias_allowed_from_loopback_client(self):
        status, headers, body = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['running'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_unknown_host_rejected(self):
        self.assertEqual(self.request('GET', '/api/state', headers={'Host': 'evil.example:' + str(self.port)})[0], 403)
        self.assertEqual(self.request('GET', '/api/state', headers={'Host': '192.168.110.79:8769'})[0], 403)
        self.state_mock.assert_not_called()

    def test_duplicate_host_headers_rejected(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.putrequest('GET', '/api/state', skip_host=True)
            connection.putheader('Host', self.lan_host)
            connection.putheader('Host', 'evil.example:' + str(self.port))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

    def test_cross_origin_state_change_rejected_even_for_another_allowed_alias(self):
        status, _, _ = self.request('POST', '/api/check', {}, {'Origin': 'http://localhost:' + str(self.port)})
        self.assertEqual(status, 403)
        self.check_mock.assert_not_called()

    def test_review_event_using_same_lan_origin(self):
        self.store.put('events', 'event-1', {'id': 'event-1', 'review_status': 'pending', 'title': 'Fixture event'})
        status, _, body = self.request('POST', '/api/events/event-1', {'review_status': 'reviewed'}, {'Origin': 'http://' + self.lan_host})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['review_status'], 'reviewed')
        self.assertEqual(self.store.get('events', 'event-1')['review_status'], 'reviewed')
        self.check_mock.assert_not_called()

    def test_non_object_json_rejected_with_400(self):
        for data in [None, [], 'text', 1, True]:
            with self.subTest(data=data):
                status, _, body = self.request('POST', '/api/events/event-1', data)
                self.assertEqual(status, 400)
                self.assertIn('JSON', json.loads(body)['error'])
        self.check_mock.assert_not_called()

    def test_custom_header_required_even_without_origin(self):
        status, _, _ = self.request('POST', '/api/check', {}, default_guard=False)
        self.assertEqual(status, 403)
        self.check_mock.assert_not_called()

    def test_status_reads_do_not_trigger_online_checks(self):
        for _ in range(3):
            self.assertEqual(self.request('GET', '/api/state')[0], 200)
        self.assertEqual(self.state_mock.call_count, 3)
        self.check_mock.assert_not_called()

    def test_manual_check_queues_single_stub_and_rejects_concurrent_request(self):
        self.gate.clear()
        status, _, body = self.request('POST', '/api/check', {}, {'Origin': 'http://' + self.lan_host})
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)['running'])
        self.assertTrue(self.entered.wait(2))
        self.assertEqual(self.request('POST', '/api/check', {})[0], 409)
        self.check_mock.assert_called_once()
        self.gate.set()
        self.assertTrue(self.done.wait(2))

    def test_invalid_review_status_and_missing_event_have_useful_http_statuses(self):
        self.store.put('events', 'event-1', {'id': 'event-1', 'review_status': 'pending'})
        self.assertEqual(self.request('POST', '/api/events/event-1', {'review_status': 'untrusted'})[0], 400)
        self.assertEqual(self.request('POST', '/api/events/unknown', {'review_status': 'reviewed'})[0], 404)

    def test_content_addressed_snapshot_download_retains_original_bytes(self):
        original = b'%PDF-1.7\nfixture data\n%%EOF'
        digest = self.store.blob(original)
        status, headers, body = self.request('GET', '/snapshots/' + digest + '.pdf')
        self.assertEqual(status, 200)
        self.assertEqual(body, original)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.check_mock.assert_not_called()


if __name__ == '__main__':
    unittest.main()
