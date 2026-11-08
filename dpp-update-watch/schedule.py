"""Install this project's systemd user units; no root access or model API needed."""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

TIMER = 'futris-dpp-watch.timer'
CHECK_SERVICE = 'futris-dpp-watch.service'
WEB_SERVICE = 'futris-dpp-watch-web.service'
NOTIFY_SERVICE = 'futris-dpp-watch-notify.service'
NOTIFY_TIMER = 'futris-dpp-watch-notify.timer'
UNIT_NAMES = (CHECK_SERVICE, TIMER, WEB_SERVICE, NOTIFY_SERVICE, NOTIFY_TIMER)
MARKER = '# Managed by Futris DPP Update Watch'
CALENDAR = '*-*-* 18:30:00 Europe/Brussels'


def user_environment() -> dict[str, str]:
    """The desktop bus is often absent from headless tool environments."""
    env = os.environ.copy()
    runtime = Path('/run/user') / str(os.getuid())
    if (runtime / 'bus').is_socket():
        env.setdefault('XDG_RUNTIME_DIR', str(runtime))
        env.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')
    env['LC_ALL'] = 'C'
    # Makes systemctl timestamps unambiguous for conversion to ISO 8601.
    env['TZ'] = 'UTC'
    return env


def _units_dir() -> Path:
    config = os.environ.get('XDG_CONFIG_HOME')
    return (Path(config) if config else Path.home() / '.config') / 'systemd/user'


def _quote(value: str | Path) -> str:
    """Quote a systemd scalar/argument, including systemd's % specifiers."""
    text = str(value)
    if any(c in text for c in ['\n', '\r', '\0']):
        raise ValueError('服务路径不能包含换行或空字符。')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def _working_directory(root: Path) -> str:
    # WorkingDirectory is a scalar path, not an ExecStart argument list. Systemd
    # keeps quotation marks here; spaces and Unicode need no surrounding quotes.
    text = str(root)
    if any(c in text for c in ['\n', '\r', '\0']):
        raise ValueError('服务路径不能包含换行或空字符。')
    return text.replace('\\', '\\\\').replace('%', '%%')


def _stamp(root: Path) -> str:
    return f'{MARKER}\n# Root: {root.resolve()}\n'


def _web_settings(root: Path) -> dict:
    path = root / 'config.json'
    config = json.loads(path.read_text()) if path.exists() else {}
    host = str(config.get('bind_host', '127.0.0.1'))
    ipaddress.ip_address(host)
    port = config.get('port', 8768)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('网页端口必须是1～65535之间的整数。')
    address = f'[{host}]' if ':' in host else host
    fallback = '127.0.0.1' if host in {'0.0.0.0', '::'} else address
    public_url = str(config.get('public_url') or f'http://{fallback}:{port}')
    if not public_url.startswith(('http://', 'https://')) or any(c in public_url for c in ['\n', '\r', '\0']):
        raise ValueError('public_url需要是完整的HTTP或HTTPS访问地址。')
    return {'host': host, 'port': port, 'public_url': public_url}


def _notify_interval(root: Path) -> int:
    path = root / 'config.json'
    config = json.loads(path.read_text()) if path.exists() else {}
    seconds = config.get('dingtalk', {}).get('retry_seconds', 300)
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 30 <= seconds <= 86400:
        raise ValueError('通知重试间隔必须是30～86400秒之间的整数。')
    return seconds


def build_units(root: Path, python_executable: str | None = None) -> dict[str, str]:
    root = root.resolve()
    py = _quote(python_executable or sys.executable)
    script = _quote(root / 'app.py')
    cwd = _working_directory(root)
    stamp = _stamp(root)
    web = _web_settings(root)
    retry_seconds = _notify_interval(root)
    retry_interval = '5min' if retry_seconds == 300 else f'{retry_seconds}s'
    return {
        CHECK_SERVICE: stamp + f'''[Unit]
Description=Futris DPP official semantic resource check

[Service]
Type=oneshot
WorkingDirectory={cwd}
ExecStart={py} {script} check
Environment=PYTHONUNBUFFERED=1
TimeoutStartSec=900
''',
        TIMER: stamp + f'''[Unit]
Description=Check DPP resources daily at 18:30 Brussels time

[Timer]
OnCalendar={CALENDAR}
Persistent=true
RandomizedDelaySec=0
AccuracySec=1min
Unit={CHECK_SERVICE}

[Install]
WantedBy=timers.target
''',
        WEB_SERVICE: stamp + f'''[Unit]
Description=Futris DPP update tracking LAN dashboard

[Service]
Type=simple
WorkingDirectory={cwd}
ExecStart={py} {script} serve --host {_quote(web['host'])} --port {web['port']}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=default.target
''',
        NOTIFY_SERVICE: stamp + f'''[Unit]
Description=Deliver queued Futris DPP DingTalk notifications

[Service]
Type=oneshot
WorkingDirectory={cwd}
ExecStart={py} {script} notify-pending
Environment=PYTHONUNBUFFERED=1
TimeoutStartSec=240
''',
        NOTIFY_TIMER: stamp + f'''[Unit]
Description=Retry queued DPP notifications without fetching official resources

[Timer]
OnBootSec=2min
OnUnitActiveSec={retry_interval}
RandomizedDelaySec=0
AccuracySec=30s
Unit={NOTIFY_SERVICE}

[Install]
WantedBy=timers.target
''',
    }


def next_scheduled(now: datetime | None = None) -> datetime:
    """Next wall-clock 18:30 in Brussels, accounting for DST every day."""
    tz = ZoneInfo('Europe/Brussels')
    local = (now or datetime.now(tz)).astimezone(tz)
    candidate = datetime.combine(local.date(), time(18, 30), tz)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), time(18, 30), tz)
    return candidate


def _run(args: list[str], *, timeout: int = 20, required: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                env=user_environment(), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if required:
            raise RuntimeError(f'定时服务命令未完成：{exc}') from exc
        return subprocess.CompletedProcess(args, 1, '', str(exc))
    if required and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f'定时服务操作失败：{detail[:1000]}')
    return result


def _systemctl(*args: str, required: bool = True) -> subprocess.CompletedProcess:
    executable = shutil.which('systemctl')
    if not executable:
        if required:
            raise RuntimeError('本机没有 systemctl；请使用系统计划任务运行 app.py check。')
        return subprocess.CompletedProcess(['systemctl'], 1, '', 'systemctl unavailable')
    return _run([executable, '--user', *args], required=required)


def _assert_owned(path: Path, root: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f'拒绝覆盖符号链接服务文件：{path}')
    if path.exists() and not path.read_text().startswith(_stamp(root)):
        raise RuntimeError(f'同名服务不属于本项目目录，保留原文件：{path}')


def _atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.' + path.name + '.', delete=False) as file:
        file.write(content)
        temporary = Path(file.name)
    try:
        os.chmod(temporary, 0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def install(root: Path) -> dict:
    """Enable only this project's timers and dashboard in the current user session."""
    root = root.resolve()
    if not (root / 'app.py').is_file():
        raise RuntimeError(f'没有找到监控入口：{root / "app.py"}')
    units = build_units(root)
    directory = _units_dir()
    # Validate the bus and the complete ownership set before making any writes.
    _systemctl('show', 'default.target', '--property=LoadState', '--no-pager')
    for name in UNIT_NAMES:
        _assert_owned(directory / name, root)
    directory.mkdir(parents=True, exist_ok=True)
    web_changed = ((directory / WEB_SERVICE).exists()
                   and (directory / WEB_SERVICE).read_text() != units[WEB_SERVICE])
    notify_timer_changed = ((directory / NOTIFY_TIMER).exists()
                            and (directory / NOTIFY_TIMER).read_text() != units[NOTIFY_TIMER])
    for name, content in units.items():
        path = directory / name
        if path.exists() and path.read_text() != content:
            backup = path.with_name(path.name + '.bak.' + datetime.now().strftime('%Y%m%d%H%M%S%f'))
            shutil.copy2(path, backup)
        _atomic_write(path, content)
    _systemctl('daemon-reload')
    _systemctl('enable', '--now', TIMER, WEB_SERVICE, NOTIFY_TIMER)
    if web_changed:
        # enable --now leaves an already running service's old arguments in use.
        _systemctl('restart', WEB_SERVICE)
    if notify_timer_changed:
        _systemctl('restart', NOTIFY_TIMER)
    result = status(root)
    if not result['active'] or not result['web_active'] or not result['notify_timer_active']:
        raise RuntimeError('服务文件已保存，但尚未全部正常启动。' + result['message'] + ' 请查看本项目的 systemctl status 与 journalctl 日志。')
    return result


def uninstall(root: Path) -> dict:
    """Remove these five confirmed units; retain all snapshots, history and reports."""
    root = root.resolve()
    directory = _units_dir()
    for name in UNIT_NAMES:
        _assert_owned(directory / name, root)
    existing = [name for name in UNIT_NAMES if (directory / name).exists()]
    if not existing:
        return status(root)
    if TIMER in existing:
        _systemctl('disable', '--now', TIMER)
    if NOTIFY_TIMER in existing:
        _systemctl('disable', '--now', NOTIFY_TIMER)
    if WEB_SERVICE in existing:
        _systemctl('disable', '--now', WEB_SERVICE)
    if CHECK_SERVICE in existing:
        _systemctl('stop', CHECK_SERVICE, required=False)
    if NOTIFY_SERVICE in existing:
        _systemctl('stop', NOTIFY_SERVICE, required=False)
    for name in existing:
        (directory / name).unlink()
    _systemctl('daemon-reload')
    return status(root)


def _properties(name: str, properties: list[str]) -> dict:
    result = _systemctl('show', name, '--no-pager', *['--property=' + p for p in properties], required=False)
    parsed = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode:
        parsed['_error'] = (result.stderr or result.stdout).strip()
    return parsed


def _parse_systemd_time(value: str) -> str | None:
    if not value or value == 'n/a':
        return None
    match = re.search(r'(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.\d+)? UTC', value)
    if match:
        dt = datetime.fromisoformat(f'{match[1]}T{match[2]}+00:00')
        return dt.astimezone(ZoneInfo('Asia/Shanghai')).isoformat()
    return None


def status(root: Path) -> dict:
    root = root.resolve()
    web_settings = _web_settings(root)
    retry_seconds = _notify_interval(root)
    directory = _units_dir()
    conflicts = []
    installed = True
    for name in UNIT_NAMES:
        path = directory / name
        if not path.exists():
            installed = False
            continue
        try:
            _assert_owned(path, root)
        except (RuntimeError, OSError) as exc:
            conflicts.append(str(exc))
            installed = False
    common = {'installed': installed, 'active': False, 'next_run': None,
              'timezone': 'Europe/Brussels', 'calendar': CALENDAR,
              'schedule_label': '每天布鲁塞尔时间 18:30（北京次日 00:30／01:30）',
              'web_active': False, 'web_url': web_settings['public_url'],
              'bind_host': web_settings['host'], 'port': web_settings['port'],
              'notify_timer_active': False, 'notify_retry_seconds': retry_seconds}
    if conflicts:
        return {**common, 'message': '；'.join(conflicts)}
    if not installed:
        return {**common, 'message': '尚未安装自动检查；可运行 python schedule.py install。'}
    timer = _properties(TIMER, ['ActiveState', 'UnitFileState', 'NextElapseUSecRealtime', 'LastTriggerUSec'])
    web = _properties(WEB_SERVICE, ['ActiveState', 'SubState'])
    notify = _properties(NOTIFY_TIMER, ['ActiveState', 'NextElapseUSecRealtime', 'LastTriggerUSec'])
    active = timer.get('ActiveState') == 'active'
    next_value = _parse_systemd_time(timer.get('NextElapseUSecRealtime', ''))
    messages = []
    if active:
        messages.append('自动检查已运行；按布鲁塞尔时区处理夏令时，关机期间错过的检查会在定时器恢复后补跑一次。')
    else:
        messages.append('定时器未运行；请检查用户服务状态。')
    if timer.get('_error'):
        messages.append(timer['_error'][:400])
    if notify.get('ActiveState') != 'active':
        messages.append('通知重试定时器未运行，待发送消息需手动恢复或重新安装服务。')
    messages.append('本机需开机并联网；用户服务需有可用的用户会话，未自动修改用户驻留设置。')
    return {**common, 'active': active, 'web_active': web.get('ActiveState') == 'active',
            'notify_timer_active': notify.get('ActiveState') == 'active',
            'notify_last_trigger': notify.get('LastTriggerUSec'),
            'next_run': next_value, 'next_run_raw': timer.get('NextElapseUSecRealtime'),
            'last_trigger': timer.get('LastTriggerUSec'), 'message': ' '.join(messages)}


def main() -> int:
    parser = argparse.ArgumentParser(description='DPP 更新监控的本机定时任务')
    parser.add_argument('command', choices=['install', 'status', 'uninstall'], nargs='?', default='status')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    try:
        result = globals()[args.command](root)
    except (RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
