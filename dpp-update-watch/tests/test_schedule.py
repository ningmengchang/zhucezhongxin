import importlib.util
from datetime import datetime
from pathlib import Path
import json
import tempfile
import unittest
from zoneinfo import ZoneInfo


SPEC = importlib.util.spec_from_file_location('dpp_watch_schedule', Path(__file__).resolve().parents[1] / 'schedule.py')
schedule = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(schedule)


class ScheduleTests(unittest.TestCase):
    def test_brussels_winter_is_beijing_next_day_0130(self):
        now = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        result = schedule.next_scheduled(now).astimezone(ZoneInfo('Asia/Shanghai'))
        self.assertEqual(result.isoformat(), '2026-11-07T01:30:00+08:00')

    def test_brussels_summer_is_beijing_next_day_0030(self):
        now = datetime(2026, 7, 6, 10, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        result = schedule.next_scheduled(now).astimezone(ZoneInfo('Asia/Shanghai'))
        self.assertEqual(result.isoformat(), '2026-07-07T00:30:00+08:00')

    def test_next_occurrence_after_dst_boundary_uses_new_offset(self):
        now = datetime(2026, 10, 24, 19, 0, tzinfo=ZoneInfo('Europe/Brussels'))
        result = schedule.next_scheduled(now).astimezone(ZoneInfo('Asia/Shanghai'))
        self.assertEqual(result.isoformat(), '2026-10-26T01:30:00+08:00')

    def test_calendar_persists_without_manual_china_offset(self):
        units = schedule.build_units(Path('/tmp/DPP 项目'))
        self.assertIn('OnCalendar=*-*-* 18:30:00 Europe/Brussels', units[schedule.TIMER])
        self.assertIn('Persistent=true', units[schedule.TIMER])
        self.assertIn('TimeoutStartSec=900', units[schedule.CHECK_SERVICE])

    def test_notification_timer_only_retries_outbox(self):
        units = schedule.build_units(Path('/tmp/DPP 项目'))
        self.assertEqual(set(units), set(schedule.UNIT_NAMES))
        notify = units[schedule.NOTIFY_SERVICE]
        timer = units[schedule.NOTIFY_TIMER]
        self.assertIn('app.py" notify-pending', notify)
        self.assertNotIn('app.py" check', notify)
        self.assertIn('OnBootSec=2min', timer)
        self.assertIn('OnUnitActiveSec=5min', timer)
        self.assertNotIn('Persistent=', timer)
        self.assertNotIn('OnCalendar=', timer)
        self.assertIn('Unit=' + schedule.NOTIFY_SERVICE, timer)

    def test_notification_retry_interval_is_configurable_without_changing_official_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.json').write_text(json.dumps({'dingtalk': {'retry_seconds': 600}}))
            units = schedule.build_units(root)
            self.assertIn('OnUnitActiveSec=600s', units[schedule.NOTIFY_TIMER])
            self.assertIn('OnCalendar=*-*-* 18:30:00 Europe/Brussels', units[schedule.TIMER])
            self.assertEqual(schedule.status(root)['notify_retry_seconds'], 600)

    def test_invalid_notification_interval_is_rejected_before_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.json').write_text(json.dumps({'dingtalk': {'retry_seconds': 0}}))
            with self.assertRaises(ValueError):
                schedule.build_units(root)

    def test_systemd_quotes_spaces_unicode_and_specifier(self):
        units = schedule.build_units(Path('/tmp/DPP 项目 100%'), '/tmp/Python env/python')
        self.assertIn('WorkingDirectory=/tmp/DPP 项目 100%%\n', units[schedule.CHECK_SERVICE])
        self.assertIn('ExecStart="/tmp/Python env/python" "/tmp/DPP 项目 100%%/app.py" check', units[schedule.CHECK_SERVICE])
        self.assertIn('serve --host "127.0.0.1" --port 8768', units[schedule.WEB_SERVICE])

    def test_web_unit_reads_lan_bind_and_port_from_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.json').write_text(json.dumps({
                'bind_host': '0.0.0.0', 'port': 9876,
                'public_url': 'http://192.168.110.79:9876',
            }))
            units = schedule.build_units(root)
            self.assertIn('serve --host "0.0.0.0" --port 9876', units[schedule.WEB_SERVICE])
            state = schedule.status(root)
            self.assertEqual(state['web_url'], 'http://192.168.110.79:9876')
            self.assertEqual(state['bind_host'], '0.0.0.0')
            self.assertEqual(state['port'], 9876)

    def test_web_config_rejects_invalid_port_before_unit_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'config.json').write_text(json.dumps({'bind_host': '0.0.0.0', 'port': 70000}))
            with self.assertRaises(ValueError):
                schedule.build_units(root)

    def test_foreign_unit_is_never_claimed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / schedule.TIMER
            target.write_text(schedule._stamp(root / 'other-project') + '[Timer]\n')
            with self.assertRaises(RuntimeError):
                schedule._assert_owned(target, root)
            self.assertIn('other-project', target.read_text())

    def test_symlink_unit_is_never_claimed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'real'
            target.write_text(schedule._stamp(root))
            link = root / schedule.TIMER
            link.symlink_to(target)
            with self.assertRaises(RuntimeError):
                schedule._assert_owned(link, root)

    def test_systemd_next_run_utc_is_converted_to_beijing(self):
        self.assertEqual(schedule._parse_systemd_time('Fri 2026-11-06 17:30:00 UTC'), '2026-11-07T01:30:00+08:00')
        self.assertIsNone(schedule._parse_systemd_time('n/a'))


if __name__ == '__main__':
    unittest.main()
