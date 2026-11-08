"""Durable DingTalk delivery tests. Every DWS invocation is simulated locally."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from watchlib.storage import Store, now
from watchlib.notifications import enqueue, flush, summary, resume_blocked


def ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec='seconds')


SETTINGS = {
    'public_url': 'http://192.168.110.79:8768',
    'port': 8768,
    'dingtalk': {
        'enabled': True, 'enabled_at': '2026-01-01T00:00:00+00:00',
        'executable': '/test-only/dws', 'profile': 'test-profile',
        'recipient_user_id': 'test-recipient-001', 'recipient_name': '测试收件人',
        'events': ['resource_changed', 'page_changed', 'new_resource', 'check_failed'],
        'retry_seconds': 300,
    },
}
ACK = {'success': True, 'result': {'openTaskId': 'test-task-001'}}
DELIVERED = {'success': True, 'result': {'sendStatus': 'SUCCESS', 'openMessageId': 'test-message-001', 'openConversationId': 'test-conversation-001'}}
PENDING = {'success': True, 'result': {'sendStatus': 'PENDING'}}


class FakeRunner:
    def __init__(self, sends=None, queries=None):
        self.sends = list([ACK] if sends is None else sends)
        self.queries = list([DELIVERED] if queries is None else queries)
        self.calls = []

    def __call__(self, args, **kwargs):
        # subprocess is the only boundary: no CLI process or message is sent.
        command = list(args)
        action = 'query' if any('query-send-status' in arg for arg in command) else 'send'
        self.calls.append((action, command, deepcopy(kwargs)))
        choices = self.queries if action == 'query' else self.sends
        if not choices:
            raise AssertionError('Unexpected repeated ' + action + ': ' + repr(command))
        result = choices.pop(0)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, subprocess.CompletedProcess):
            return result
        if isinstance(result, str):
            return subprocess.CompletedProcess(command, 0, result, '')
        return subprocess.CompletedProcess(command, 0 if result.get('success') else 1, json.dumps(result), '')

    def count(self, action):
        return sum(item[0] == action for item in self.calls)


class NotificationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='dpp-notify-test-')
        self.store = Store(Path(self.temporary.name) / 'data')
        self.config = deepcopy(SETTINGS)

    def tearDown(self):
        self.temporary.cleanup()

    def event(self, event_id='event-1', kind='resource_changed', title='语义模型发生变化'):
        item = {'id': event_id, 'created_at': now(), 'kind': kind, 'title': title,
                'summary': '新增必填字段，请核对。', 'severity': 'high', 'changes': [],
                'report_url': '/reports/' + event_id + '.html', 'review_status': 'pending'}
        self.store.put('events', event_id, item)
        return item

    def record(self, delivery):
        return self.store.get('notifications', delivery['id'])

    def due(self, delivery):
        record = self.record(delivery)
        record['next_retry_at'] = ago(1)
        self.store.put('notifications', delivery['id'], record)
        return record

    def finish(self, delivery, runner, steps=4):
        for _ in range(steps):
            record = self.record(delivery)
            if record['status'] in {'delivered', 'blocked', 'unknown'}:
                return record
            self.due(delivery)
            flush(self.store, self.config, runner=runner)
        return self.record(delivery)

    def test_missing_dingtalk_configuration_is_disabled_and_never_calls_cli(self):
        event = self.event()
        runner = FakeRunner(sends=[], queries=[])
        self.assertIsNone(enqueue(self.store, {}, [event]))
        self.assertEqual(flush(self.store, {}, runner=runner), [])
        self.assertEqual(runner.calls, [])

    def test_explicitly_disabled_never_queues_or_sends(self):
        self.config['dingtalk']['enabled'] = False
        event = self.event()
        runner = FakeRunner(sends=[], queries=[])
        self.assertIsNone(enqueue(self.store, self.config, [event]))
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.store.all('notifications'), [])
        self.assertEqual(runner.calls, [])

    def test_no_events_or_unwatched_event_kind_do_not_queue(self):
        self.assertIsNone(enqueue(self.store, self.config, []))
        self.assertIsNone(enqueue(self.store, self.config, [self.event(kind='unchanged')]))
        self.assertEqual(self.store.all('notifications'), [])

    def test_multiple_changes_form_one_batch_with_stable_uuid_and_human_text(self):
        first, second = self.event(), self.event('event-2', 'page_changed', '官网资源目录变化')
        delivery = enqueue(self.store, self.config, [first, second])
        self.assertEqual(set(delivery['event_ids']), {'event-1', 'event-2'})
        self.assertEqual(delivery['id'], delivery['uuid'])
        self.assertEqual(str(uuid.UUID(delivery['uuid'])), delivery['uuid'])
        self.assertEqual(delivery['status'], 'pending')
        self.assertIn('语义模型', delivery['text'])
        self.assertIn('官网资源目录', delivery['text'])
        self.assertIn(self.config['public_url'], delivery['text'])
        self.assertEqual(len(self.store.all('notifications')), 1)

    def test_same_batch_reenqueue_does_not_create_duplicate_deliveries(self):
        event = self.event()
        delivery = enqueue(self.store, self.config, [event])
        enqueue(self.store, self.config, [event])
        self.assertEqual(len(self.store.all('notifications')), 1)
        self.assertEqual(self.store.all('notifications')[0]['uuid'], delivery['uuid'])

    def test_acknowledgement_alone_does_not_mark_delivery_successful(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner(queries=[PENDING] * 4)
        flush(self.store, self.config, runner=runner)
        record = self.record(delivery)
        self.assertNotEqual(record['status'], 'delivered')
        self.assertEqual(record.get('open_task_id'), 'test-task-001')
        self.assertFalse(record.get('delivered_at'))
        self.assertEqual(runner.count('send'), 1)

    def test_only_success_receipt_marks_delivered_and_retains_identifiers(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner()
        record = self.finish(delivery, runner)
        self.assertEqual(record['status'], 'delivered')
        self.assertEqual(record['open_task_id'], 'test-task-001')
        self.assertEqual(record['open_message_id'], 'test-message-001')
        self.assertEqual(record['open_conversation_id'], 'test-conversation-001')
        self.assertTrue(record['delivered_at'])
        self.assertEqual(runner.count('send'), 1)
        self.assertGreaterEqual(runner.count('query'), 1)

    def test_delivered_notification_is_never_resent(self):
        event = self.event()
        delivery = enqueue(self.store, self.config, [event])
        runner = FakeRunner()
        self.assertEqual(self.finish(delivery, runner)['status'], 'delivered')
        enqueue(self.store, self.config, [event])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_submit_timeout_is_unknown_and_never_automatically_resent(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner(sends=[subprocess.TimeoutExpired('/test-only/dws', 1)])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'unknown')
        self.due(delivery)
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_invalid_json_after_send_is_ambiguous_and_not_retried(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner(sends=['not-json: gateway failure'])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'unknown')
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_interrupted_sending_becomes_unknown_without_new_send(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        interrupted = self.record(delivery)
        interrupted.update(status='sending', stage='send', attempts=1, last_attempt_at=ago(60))
        self.store.put('notifications', delivery['id'], interrupted)
        runner = FakeRunner(sends=[], queries=[])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'unknown')
        self.assertEqual(runner.calls, [])

    def test_structured_retryable_error_retries_same_uuid_and_exact_text(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        retry = {'success': False, 'error': {'code': 'RATE_LIMITED', 'message': 'Rate limited', 'retryable': True}}
        runner = FakeRunner(sends=[retry, ACK])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'retry_wait')
        before = self.record(delivery)
        record = self.finish(delivery, runner)
        self.assertEqual(record['status'], 'delivered')
        self.assertEqual(record['uuid'], before['uuid'])
        self.assertEqual(record['text'], before['text'])
        commands = [call[1] for call in runner.calls if call[0] == 'send']
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0], commands[1])

    def test_retry_wait_does_not_retry_before_due_time(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        retry = {'success': False, 'error': {'code': 'RATE_LIMITED', 'message': 'Rate limited', 'retryable': True}}
        runner = FakeRunner(sends=[retry])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'retry_wait')
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_non_retryable_authorization_failure_is_blocked_without_resend(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        denied = {'success': False, 'error': {'code': 'FORBIDDEN', 'message': 'Not authorized', 'retryable': False}}
        runner = FakeRunner(sends=[denied])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'blocked')
        self.due(delivery)
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_expired_retry_window_does_not_risk_duplicate_resend(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        expired = self.record(delivery)
        expired.update(status='retry_wait', stage='send', created_at=ago(25 * 3600), last_attempt_at=ago(25 * 3600), next_retry_at=ago(1), attempts=1)
        self.store.put('notifications', delivery['id'], expired)
        runner = FakeRunner(sends=[], queries=[])
        flush(self.store, self.config, runner=runner)
        self.assertIn(self.record(delivery)['status'], {'blocked', 'unknown'})
        self.assertEqual(runner.calls, [])

    def test_submitted_record_queries_receipt_without_resending(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        submitted = self.record(delivery)
        submitted.update(status='submitted', stage='query', open_task_id='test-task-001', last_attempt_at=ago(10), next_retry_at=ago(1), attempts=1)
        self.store.put('notifications', delivery['id'], submitted)
        runner = FakeRunner(sends=[])
        record = self.finish(delivery, runner)
        self.assertEqual(record['status'], 'delivered')
        self.assertEqual(runner.count('send'), 0)
        self.assertEqual(runner.count('query'), 1)

    def test_send_is_committed_as_sending_before_process_invocation(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        underlying = FakeRunner()

        def runner(args, **kwargs):
            if 'query-send-status' not in args:
                self.assertEqual(self.record(delivery)['status'], 'sending')
                self.assertEqual(self.record(delivery)['attempts'], 1)
            return underlying(args, **kwargs)

        record = self.finish(delivery, runner)
        self.assertEqual(record['status'], 'delivered')

    def test_query_timeout_retries_only_receipt_query_never_message_submission(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner(queries=[subprocess.TimeoutExpired('/test-only/dws', 1), DELIVERED])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'submitted')
        self.assertEqual(self.record(delivery)['open_task_id'], 'test-task-001')
        self.assertEqual(self.finish(delivery, runner)['status'], 'delivered')
        self.assertEqual(runner.count('send'), 1)
        self.assertEqual(runner.count('query'), 2)

    def test_provider_delivery_failure_is_blocked_and_not_new_message_retry(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        failed = {'success': True, 'result': {'sendStatus': 'FAILED', 'friendlyHint': '用户不可达'}}
        runner = FakeRunner(queries=[failed])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'blocked')
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_success_without_task_id_is_unknown_not_delivered(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner(sends=[{'success': True, 'result': {}}], queries=[])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'unknown')
        self.assertEqual(runner.count('query'), 0)

    def test_missing_local_executable_is_blocked_without_ambiguous_retry(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner(sends=[FileNotFoundError('DWS missing')])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.record(delivery)['status'], 'blocked')
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_crash_before_enqueue_recovers_eligible_event_once(self):
        self.event()
        runner = FakeRunner()
        flush(self.store, self.config, runner=runner)
        self.assertEqual(len(self.store.all('notifications')), 1)
        delivery = self.store.all('notifications')[0]
        self.assertEqual(self.finish(delivery, runner)['status'], 'delivered')
        flush(self.store, self.config, runner=runner)
        self.assertEqual(runner.count('send'), 1)

    def test_pending_receipt_stays_unconfirmed_and_only_queries_again(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        runner = FakeRunner(queries=[PENDING, DELIVERED])
        flush(self.store, self.config, runner=runner)
        self.assertNotEqual(self.record(delivery)['status'], 'delivered')
        self.assertEqual(self.finish(delivery, runner)['status'], 'delivered')
        self.assertEqual(runner.count('send'), 1)
        self.assertEqual(runner.count('query'), 2)

    def test_no_historical_events_before_enabled_at_are_replayed(self):
        old = self.event()
        old['created_at'] = ago(7200)
        self.store.put('events', old['id'], old)
        self.config['dingtalk']['enabled_at'] = ago(3600)
        runner = FakeRunner(sends=[], queries=[])
        flush(self.store, self.config, runner=runner)
        self.assertEqual(self.store.all('notifications'), [])
        self.assertEqual(runner.calls, [])

    def test_test_message_is_explicit_not_part_of_regular_event_filter(self):
        event = self.event(kind='notification_test')
        self.assertIsNone(enqueue(self.store, self.config, [event]))
        delivery = enqueue(self.store, self.config, [event], test=True)
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery['event_ids'], [event['id']])

    def test_raw_cli_diagnostics_and_credentials_are_not_saved(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        denied = {'success': False, 'error': {'code': 'FORBIDDEN', 'message': 'Bearer test-private-token; password=test-secret', 'retryable': False}, 'access_token': 'test-private-token'}
        runner = FakeRunner(sends=[denied])
        flush(self.store, self.config, runner=runner)
        stored = json.dumps(self.store.all('notifications'), ensure_ascii=False)
        self.assertNotIn('test-private-token', stored)
        self.assertNotIn('test-secret', stored)

    def test_summary_is_passive_and_does_not_invoke_subprocess(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        before = self.record(delivery)
        from unittest.mock import patch
        with patch('subprocess.run', side_effect=AssertionError('Passive summary must never call CLI')):
            value = summary(self.store, self.config)
        self.assertIsInstance(value, dict)
        self.assertEqual(self.record(delivery), before)

    def test_json_quoted_tokens_and_multiline_private_key_are_hidden_before_storage(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        message = ('{"access_token": "quoted-token-123", "private_key": "quoted-private-456"}\n'
                   '-----BEGIN PRIVATE KEY-----\nPEMSECRET789\n-----END PRIVATE KEY-----')
        runner = FakeRunner(sends=[{'success': False, 'error': {'category': 'auth', 'message': message, 'retryable': False}}])
        flush(self.store, self.config, runner=runner)
        stored = json.dumps(self.store.all('notifications'))
        for secret in ['quoted-token-123', 'quoted-private-456', 'PEMSECRET789']:
            self.assertNotIn(secret, stored)

    def test_future_retry_does_not_starve_another_pending_item_with_max_items_one(self):
        first = enqueue(self.store, self.config, [self.event('event-first')])
        waiting = self.record(first)
        waiting.update(created_at=ago(60), status='retry_wait', stage='send', attempts=1,
                       next_retry_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.store.put('notifications', first['id'], waiting)
        second = enqueue(self.store, self.config, [self.event('event-second')])
        runner = FakeRunner()
        flush(self.store, self.config, runner=runner, max_items=1)
        self.assertEqual(self.record(first)['status'], 'retry_wait')
        self.assertEqual(self.record(first)['attempts'], 1)
        self.assertEqual(self.record(second)['status'], 'delivered')
        self.assertEqual(runner.count('send'), 1)
        sent = next(command for action, command, _ in runner.calls if action == 'send')
        self.assertIn(second['uuid'], sent)

    def test_resume_authorization_block_preserves_uuid_text_and_never_resumes_unknown(self):
        blocked = enqueue(self.store, self.config, [self.event('event-blocked')])
        data = self.record(blocked)
        data.update(status='blocked', error={'category': 'auth', 'retryable': False, 'message': '需要重新登录'})
        self.store.put('notifications', blocked['id'], data)
        unknown = enqueue(self.store, self.config, [self.event('event-unknown')])
        data = self.record(unknown)
        data.update(status='unknown', error={'category': 'auth', 'retryable': False, 'message': '发送结果未知'})
        self.store.put('notifications', unknown['id'], data)
        self.assertEqual(resume_blocked(self.store, self.config), 1)
        recovered = self.record(blocked)
        self.assertEqual(recovered['status'], 'pending')
        self.assertEqual(recovered['uuid'], blocked['uuid'])
        self.assertEqual(recovered['text'], blocked['text'])
        self.assertEqual(self.record(unknown)['status'], 'unknown')

    def test_resume_blocked_receipt_only_queries_existing_task(self):
        delivery = enqueue(self.store, self.config, [self.event()])
        data = self.record(delivery)
        data.update(status='blocked', open_task_id='test-task-001', stage='query', attempts=1,
                    error={'category': 'permission', 'retryable': False, 'message': '查询权限失效'})
        self.store.put('notifications', delivery['id'], data)
        self.assertEqual(resume_blocked(self.store, self.config), 1)
        self.assertEqual(self.record(delivery)['status'], 'submitted')
        runner = FakeRunner(sends=[])
        self.assertEqual(self.finish(delivery, runner)['status'], 'delivered')
        self.assertEqual(runner.count('send'), 0)
        self.assertEqual(runner.count('query'), 1)

    def test_target_change_blocks_old_delivery_without_rerouting_to_new_target(self):
        for field, value in [('profile', 'different-profile'), ('recipient_user_id', 'different-recipient')]:
            with self.subTest(field=field):
                delivery = enqueue(self.store, self.config, [self.event('event-' + field)])
                changed = deepcopy(self.config)
                changed['dingtalk'][field] = value
                runner = FakeRunner(sends=[], queries=[])
                flush(self.store, changed, runner=runner)
                self.assertEqual(self.record(delivery)['status'], 'blocked')
                self.assertEqual(self.record(delivery)[field], delivery[field])
                self.assertEqual(runner.calls, [])

    def test_monitor_passes_saved_events_to_notification_hook_once_without_real_dws(self):
        from unittest.mock import patch
        from watchlib import monitor
        config = deepcopy(self.config)
        config.update(source_page='https://single-market-economy.ec.europa.eu/test', desktop_notifications=False)

        def offline_fetch(*args, **kwargs):
            raise TimeoutError('offline fixture only')

        with patch.object(monitor, 'enqueue') as queued, patch.object(monitor, 'flush', return_value=[]) as flushed:
            run = monitor.check(self.store, config, fetch=offline_fetch)
        self.assertEqual(run['status'], 'error')
        queued.assert_called_once()
        self.assertIs(queued.call_args.args[0], self.store)
        passed = queued.call_args.args[2]
        self.assertEqual([event['kind'] for event in passed], ['check_failed'])
        self.assertEqual(passed[0]['id'], run['event_ids'][0])
        self.assertIsNotNone(self.store.get('events', passed[0]['id']))
        flushed.assert_called_once_with(self.store, config, max_items=2)


if __name__ == '__main__':
    unittest.main()
