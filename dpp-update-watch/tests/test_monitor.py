"""Offline end-to-end checks: discovery -> download -> compare -> durable state."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from watchlib.diffing import inspect_artifact
from watchlib.monitor import check, seed
from watchlib.sources import checked_url, discover
from watchlib.storage import Store

PAGE = 'https://single-market-economy.ec.europa.eu/resources_en'
ZIP = 'https://single-market-economy.ec.europa.eu/document/download/semantic_en?filename=semantic.zip'
PDF = 'https://single-market-economy.ec.europa.eu/document/download/guide_en?filename=guide.pdf'
CONFIG = {
    'source_page': PAGE, 'timeout_seconds': 1, 'attempts': 1,
    'desktop_notifications': False, 'port': 8768, 'baseline_manifest': 'manifest.json',
    'watch': [
        {'key': 'semantic', 'title': 'Semantic Data Artifacts', 'kind': 'semantic_zip'},
        {'key': 'guide', 'title': 'Semantic Data Guide', 'kind': 'guide_pdf'},
    ],
}


def zipped(minimum=0, version='1.0.0', description='A field'):
    buffer = io.BytesIO()
    schema = {'$id': f'https://repository.product-passport.ec.europa.eu/core/{version}/schemas/a.schema.json',
              'type': 'object', 'properties': {'period': {'type': 'integer', 'minimum': minimum, 'description': description}}}
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'core/{version}/schemas/a.schema.json', json.dumps(schema))
    return buffer.getvalue()


def page(include_guide=True, date='30 October 2026', description='Official semantic resources', zip_title='DPP Registry – Semantic Data Artifacts', zip_url=ZIP):
    cards = [(zip_title, zip_url)]
    if include_guide:
        cards.append(('DPP Registry – Semantic Data Guide', PDF))
    result = '<!doctype html><html><body><nav>Example navigation</nav>'
    for title, url in cards:
        result += f'<article class="ecl-file"><h3 class="ecl-file__title">{title}</h3><span class="ecl-file__primary-meta-item">{date}</span><p class="ecl-file__description">{description}</p><a href="{url}">Download</a></article>'
    return (result + '</body></html>').encode()


class FakeFetch:
    def __init__(self, zip_data=None, page_data=None, pdf_data=b'%PDF-1.7\nfixture guide\n%%EOF'):
        self.values = {PAGE: page_data if page_data is not None else page(), ZIP: zip_data or zipped(), PDF: pdf_data}
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        value = self.values[url]
        if isinstance(value, BaseException):
            raise value
        content_type = 'text/html' if url == PAGE else 'application/zip' if url == ZIP else 'application/pdf'
        return value, {'Content-Type': content_type}, url


class MonitorIntegrationTests(unittest.TestCase):
    def test_new_publication_date_wins_when_titles_have_no_versions(self):
        old = page(include_guide=False, date='30 October 2026')
        new_url = ZIP.replace('semantic_en', 'new_release_en')
        newer = page(include_guide=False, date='6 November 2026', zip_url=new_url)
        found, inventory, missing = discover(old + newer, PAGE, CONFIG['watch'])
        self.assertEqual(found['semantic']['source_url'], new_url)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='dpp-watch-test-')
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / 'data')
        self.config = deepcopy(CONFIG)
        self.fetch = FakeFetch()

    def tearDown(self):
        self.temporary.cleanup()

    def seed_manifest(self, corrupt=False):
        files = []
        for spec, url in zip(self.config['watch'], [ZIP, PDF]):
            body = self.fetch.values[url]
            path = self.root / (spec['key'] + ('.zip' if spec['kind'].endswith('zip') else '.pdf'))
            path.write_bytes(body)
            files.append({'title': spec['title'], 'filename': path.name, 'localPath': path.name,
                          'url': url, 'sha256': '0' * 64 if corrupt else hashlib.sha256(body).hexdigest()})
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'publicationDate': '2026-10-30', 'files': files}))
        return manifest

    def setup_baseline_and_inventory(self):
        seed(self.store, self.config, self.root, self.seed_manifest())
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'unchanged')
        self.assertEqual(self.store.all('events'), [])

    def test_seed_checks_hash_and_records_real_logical_baseline(self):
        result = seed(self.store, self.config, self.root, self.seed_manifest())
        self.assertEqual(result['seeded'], 2)
        artifact = self.store.get('artifacts', 'semantic')
        self.assertEqual(artifact['sha256'], hashlib.sha256(self.fetch.values[ZIP]).hexdigest())
        self.assertEqual(artifact['payload_sha256'], inspect_artifact(self.fetch.values[ZIP], 'semantic_zip')['payload_sha256'])
        self.assertEqual(artifact['version'], '1.0.0')
        self.assertIsNone(artifact['last_checked_at'])
        self.assertEqual(self.store.read_blob(artifact['sha256']), self.fetch.values[ZIP])

    def test_hash_mismatch_rejected_before_import(self):
        with self.assertRaisesRegex(ValueError, '校验值不符'):
            seed(self.store, self.config, self.root, self.seed_manifest(corrupt=True))
        self.assertEqual(self.store.all('artifacts'), [])

    def test_two_unchanged_checks_do_not_emit_repeat_events(self):
        self.setup_baseline_and_inventory()
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'unchanged')
        self.assertEqual(run['event_ids'], [])
        self.assertEqual(len(self.store.all('checks')), 2)
        self.assertEqual(self.fetch.calls.count(ZIP), 2)  # Do not trust publication date alone.
        self.assertEqual(self.store.all('events'), [])

    def test_same_url_without_date_change_still_detects_rule_change(self):
        self.setup_baseline_and_inventory()
        before = self.store.get('artifacts', 'semantic')['sha256']
        self.fetch.values[ZIP] = zipped(minimum=1)
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'changed')
        events = self.store.all('events')
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['kind'], 'resource_changed')
        self.assertEqual(event['severity'], 'high')
        self.assertEqual(event['before_sha256'], before)
        self.assertTrue(any('/minimum' in c['path'] for c in event['changes']))
        self.assertNotEqual(self.store.get('artifacts', 'semantic')['sha256'], before)
        report = self.root / 'data' / event['report_url'].lstrip('/')
        self.assertTrue(report.exists())
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'unchanged')
        self.assertEqual(len(self.store.all('events')), 1)

    def test_bad_page_preserves_inventory_and_all_artifact_baselines(self):
        self.setup_baseline_and_inventory()
        artifacts = self.store.all('artifacts')
        inventory = self.store.get('meta', 'inventory')
        self.fetch.values[PAGE] = b'<!doctype html><html><h1>Access denied</h1></html>'
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'error')
        self.assertTrue(run['error'])
        self.assertEqual(self.store.all('artifacts'), artifacts)
        self.assertEqual(self.store.get('meta', 'inventory'), inventory)
        self.assertEqual(self.store.all('events')[0]['kind'], 'check_failed')

    def test_download_failure_is_partial_and_keeps_failed_resource_baseline(self):
        self.setup_baseline_and_inventory()
        previous = self.store.get('artifacts', 'semantic')
        self.fetch.values[ZIP] = TimeoutError('下载超时')
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'partial')
        self.assertEqual(self.store.get('artifacts', 'semantic'), previous)
        self.assertIn('下载超时', run['error'])
        results = {item['key']: item['status'] for item in run['artifacts']}
        self.assertEqual(results, {'semantic': 'error', 'guide': 'unchanged'})

    def test_bad_attachment_html_cannot_replace_snapshot_or_fake_no_change(self):
        self.setup_baseline_and_inventory()
        previous = self.store.get('artifacts', 'semantic')
        self.fetch.values[ZIP] = b'<html>Gateway problem</html>'
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'partial')
        self.assertEqual(self.store.get('artifacts', 'semantic'), previous)
        self.assertIn('HTML', run['error'])

    def test_missing_resource_does_not_remove_previous_artifact(self):
        self.setup_baseline_and_inventory()
        previous = self.store.get('artifacts', 'guide')
        self.fetch.values[PAGE] = page(include_guide=False)
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'partial')
        self.assertEqual(self.store.get('artifacts', 'guide'), previous)
        self.assertIn('官网未找到资源', run['error'])
        self.assertTrue(any(e['kind'] == 'page_changed' for e in self.store.all('events')))

    def test_identical_failures_deduplicate_and_recovery_rearms_failure_notice(self):
        self.setup_baseline_and_inventory()
        self.fetch.values[PAGE] = TimeoutError('offline')
        first = check(self.store, self.config, fetch=self.fetch)
        second = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(first['status'], second['status'])
        self.assertEqual(len(self.store.all('events')), 1)
        self.fetch.values[PAGE] = page()
        recovered = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(recovered['status'], 'unchanged')
        self.assertIsNone(self.store.get('meta', 'last_failure'))
        self.fetch.values[PAGE] = TimeoutError('offline')
        check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(len(self.store.all('events')), 2)

    def test_reviewed_status_survives_unchanged_checks(self):
        self.setup_baseline_and_inventory()
        self.fetch.values[ZIP] = zipped(minimum=1)
        check(self.store, self.config, fetch=self.fetch)
        event = self.store.all('events')[0]
        self.store.review(event['id'], 'reviewed')
        check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(self.store.get('events', event['id'])['review_status'], 'reviewed')
        self.assertEqual(len(self.store.all('events')), 1)
        self.assertRaises(ValueError, self.store.review, event['id'], 'invalid')

    def test_page_only_date_change_is_not_reported_as_model_change(self):
        self.setup_baseline_and_inventory()
        self.fetch.values[PAGE] = page(date='6 November 2026')
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'changed')
        events = self.store.all('events')
        self.assertEqual([e['kind'] for e in events], ['page_changed'])
        self.assertEqual([a['status'] for a in run['artifacts']], ['unchanged', 'unchanged'])

    def test_interrupted_check_marked_failed_on_next_run(self):
        self.setup_baseline_and_inventory()
        self.store.put('checks', '0000-interrupted', {'id': '0000-interrupted', 'status': 'running'})
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'unchanged')
        self.assertEqual(self.store.get('checks', '0000-interrupted')['status'], 'error')

    def test_corrupt_stored_blob_detected_when_comparing_new_resource(self):
        self.setup_baseline_and_inventory()
        artifact = self.store.get('artifacts', 'semantic')
        (self.root / 'data' / 'snapshots' / artifact['sha256']).write_bytes(b'corrupted snapshot')
        self.fetch.values[ZIP] = zipped(minimum=1)
        run = check(self.store, self.config, fetch=self.fetch)
        self.assertEqual(run['status'], 'partial')
        self.assertEqual(self.store.get('artifacts', 'semantic'), artifact)
        self.assertIn('历史快照校验失败', run['error'])

    def test_source_discovery_rejects_non_official_links(self):
        for bad in ['http://ec.europa.eu/a', 'https://ec.europa.eu.evil.example/a', 'https://attacker.example/a', 'https://user:password@ec.europa.eu/a']:
            with self.subTest(url=bad), self.assertRaises(ValueError):
                checked_url(bad)
        with self.assertRaises(ValueError):
            discover(page(zip_url='https://attacker.example/document/download/file'), PAGE, self.config['watch'])

    def test_latest_named_release_chosen_when_multiple_versions_visible(self):
        old = page(zip_title='DPP Registry – Semantic Data Artifacts 1.0.0')
        newer = page(zip_title='DPP Registry – Semantic Data Artifacts 1.2.0', zip_url=ZIP + '&version=1.2.0')
        found, inventory, missing = discover(old + newer, PAGE, self.config['watch'])
        self.assertEqual(found['semantic']['source_url'], ZIP + '&version=1.2.0')
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
