"""Durable DingTalk personal-message outbox, independent of any language model.

Use the previously verified DWS profile and stable user ID. Never read/export OAuth
credentials. A submit acknowledgement is not delivery: query its real task receipt.
Ambiguous sends are not replayed automatically, including after the 24h UUID window.
"""
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from zoneinfo import ZoneInfo

from .storage import now

DEFAULT_EVENTS = ['resource_changed', 'page_changed', 'new_resource', 'check_failed']
TERMINAL = {'delivered', 'blocked', 'unknown'}


def settings(config):
    return config.get('dingtalk', {})


def _safe_error(text):
    text = str(text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----', '[已隐藏私钥]', text)
    text = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [已隐藏]', text)
    text = re.sub(r'''(?ix)\b(access[_-]?token|refresh[_-]?token|app[_-]?secret|client[_-]?secret|authorization|webhook[_-]?token|token|password|passwd|passphrase|private[_-]?key|api[_-]?key|secret)\b["']?\s*[:=]\s*(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[^\s,;}]+)''', r'\1=[已隐藏]', text)
    return text[:600]


def _time(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _text(config, events, test=False):
    address = config.get('public_url', '监控页面')
    if test:
        return ('欧委会 DPP 更新监控的钉钉提醒已接通。\n\n'
                '以后发现语义模型变化、资源页面更新或检查异常，会在这里发你。没有变化时不发消息。\n\n'
                '查看记录：' + address + '\n\n'
                '这是一条连通测试，不代表官方发布了新版。')
    failures_only = all(e.get('kind') == 'check_failed' for e in events)
    title = '欧委会 DPP 更新检查遇到问题' if failures_only else '欧委会 DPP 资料有变化'
    first_time = events[0].get('created_at') or now()
    try:
        stamp = _time(first_time).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%m月%d日 %H:%M')
    except (ValueError, TypeError):
        stamp = '刚才'
    lines = [title, '', stamp + '，监控记录了以下情况：']
    for event in events[:6]:
        lines.append('• ' + str(event.get('title', '资源变化'))[:160])
        lines.append(str(event.get('summary', ''))[:320])
        if event.get('severity') == 'high':
            lines.append('涉及可能影响兼容性的规则，请研发重点核对。')
        for change in event.get('changes', [])[:2]:
            if event.get('kind') == 'resource_changed':
                lines.append('  ' + str(change.get('path', ''))[:140] + '：' + str(change.get('rule', change.get('impact', '内容调整')))[:100])
    if len(events) > 6:
        lines.append(f'还有 {len(events) - 6} 项，见监控页面。')
    report = events[0].get('report_url', '')
    lines += ['', '查看记录：' + address]
    if report.startswith('/reports/'):
        lines.append('查看报告：' + address.rstrip('/') + report)
    lines += ['', '原文和记录已保存。' + ('检查失败的资源仍保留上次成功版本。' if failures_only else '这条提醒不会自动改动业务模型。')]
    return '\n'.join(lines)[:3800]


def enqueue(store, config, events, test=False):
    cfg = settings(config)
    if not cfg.get('enabled'):
        return None
    profile, recipient = cfg.get('profile', ''), cfg.get('recipient_user_id', '')
    if not profile or not recipient:
        raise ValueError('钉钉推送缺少已核实的账号或接收人')
    enabled_at = cfg.get('enabled_at')
    eligible = [e for e in events if test or (e.get('kind') in cfg.get('events', DEFAULT_EVENTS)
                and (not enabled_at or e.get('created_at', '') >= enabled_at))]
    if not eligible:
        return None
    eligible.sort(key=lambda e: e['id'])
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        existing = [json.loads(r[0]) for r in c.execute('SELECT body FROM notifications')]
        previous = [n for n in existing if n['profile'] == profile and n['recipient_user_id'] == recipient]
        # A configuration change must not replay old events to another person or
        # organisation. Forwarding historical events requires a separate operator action.
        used = {eid for n in existing for eid in n['event_ids']}
        new_events = [e for e in eligible if e['id'] not in used]
        if not new_events:
            return next((n for n in previous if any(e['id'] in n['event_ids'] for e in eligible)), None)
        ids = sorted(e['id'] for e in new_events)
        unique = str(uuid.uuid5(uuid.NAMESPACE_URL, 'futris-dpp-watch|' + profile + '|' + recipient + '|' + '|'.join(ids)))
        delivery = dict(id=unique, uuid=unique, text=_text(config, new_events, test), event_ids=ids,
                        created_at=now(), status='pending', stage='send', attempts=0, query_attempts=0,
                        last_attempt_at=None, next_retry_at=None, open_task_id=None,
                        open_message_id=None, open_conversation_id=None, delivered_at=None,
                        error=None, profile=profile, recipient_user_id=recipient,
                        recipient_name=cfg.get('recipient_name', '本人'), summary='连通测试' if test else f'{len(ids)} 条更新／异常提醒')
        c.execute('INSERT INTO notifications VALUES (?,?)', (unique, json.dumps(delivery, ensure_ascii=False)))
        return delivery


def _invoke(config, arguments, runner):
    cfg = settings(config)
    executable = cfg.get('executable')
    if not executable or not Path(executable).is_absolute():
        return {'error': {'category': 'configuration', 'message': 'DWS可执行文件需要已核实的绝对路径', 'retryable': False}}, None
    env = dict(os.environ)
    runtime = Path('/run/user') / str(os.getuid())
    if (runtime / 'bus').exists():
        env.setdefault('XDG_RUNTIME_DIR', str(runtime))
        env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=' + str(runtime / 'bus'))
    command = [executable, *arguments, '--profile', cfg['profile'], '--format', 'json']
    try:
        result = runner(command, capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        return None, '调用钉钉超时，发送结果尚不明确。'
    except OSError as e:
        # Process did not start. This is a local configuration failure, not an unknown send.
        return {'error': {'category': 'configuration', 'message': _safe_error(e), 'retryable': False}}, None
    try:
        body = json.loads(result.stdout if result.stdout.strip() else result.stderr)
    except (json.JSONDecodeError, TypeError):
        return None, '钉钉返回无法识别的回执，未保存原始输出；不能认定已送达。'
    if not isinstance(body, dict):
        return None, '钉钉回执不是预期的JSON对象。'
    if result.returncode and not body.get('error'):
        return None, '钉钉命令未成功结束，发送结果尚不明确。'
    return body, None


def _error(body):
    error = body.get('error')
    if isinstance(error, dict):
        return {k: error[k] for k in ['category', 'reason', 'retryable', 'retry_after_seconds', 'message'] if k in error}
    if body.get('success') is False:
        result = body.get('result') if isinstance(body.get('result'), dict) else {}
        return dict(category='delivery', message=body.get('errorMsg') or result.get('friendlyHint') or '钉钉未接受此操作', retryable=result.get('retryable') is True)
    return None


def _set_error(delivery, error, config):
    error = {**error, 'message': _safe_error(error.get('message', '发送未完成'))}
    delivery['error'] = error
    if error.get('retryable') is True and datetime.now(timezone.utc) - _time(delivery['created_at']) < timedelta(hours=23):
        delay = max(60, int(settings(config).get('retry_seconds', 300)))
        retry_after = error.get('retry_after_seconds')
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            delay = max(delay, int(retry_after))
        delivery.update(status='retry_wait', next_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec='seconds'))
    else:
        delivery.update(status='blocked', next_retry_at=None)


def _process(store, config, delivery, runner):
    cfg = settings(config)
    if delivery['profile'] != cfg.get('profile') or delivery['recipient_user_id'] != cfg.get('recipient_user_id'):
        delivery.update(status='blocked', error={'category': 'configuration', 'retryable': False, 'message': '推送账号或接收人已变更，旧提醒保留，不转发给其他目标。'})
        return delivery
    if delivery['status'] == 'sending':
        delivery.update(status='unknown', error={'category': 'unknown', 'retryable': False, 'message': '上次进程在发送中断；投递结果未知，未自动重发。'})
        return delivery
    if delivery['status'] in TERMINAL:
        return delivery
    if delivery.get('next_retry_at') and _time(delivery['next_retry_at']) > datetime.now(timezone.utc):
        return delivery
    if not delivery.get('open_task_id'):
        if datetime.now(timezone.utc) - _time(delivery['created_at']) >= timedelta(hours=23) and delivery['attempts']:
            delivery.update(status='unknown', error={'category': 'idempotency', 'retryable': False, 'message': '超过可安全自动重发的时限；保留原幂等UUID，不生成新消息重发。'})
            return delivery
        delivery.update(status='sending', stage='send', attempts=delivery['attempts'] + 1, last_attempt_at=now(), next_retry_at=None)
        store.put('notifications', delivery['id'], delivery)  # Commit before any network effect.
        body, unknown = _invoke(config, ['chat', 'message', 'send', '--user', delivery['recipient_user_id'], '--text', delivery['text'], '--uuid', delivery['uuid']], runner)
        if unknown:
            delivery.update(status='unknown', error={'category': 'unknown', 'message': unknown, 'retryable': False})
            return delivery
        error = _error(body)
        if error:
            _set_error(delivery, error, config)
            return delivery
        result = body.get('result') if isinstance(body.get('result'), dict) else {}
        if body.get('success') is not True or not result.get('openTaskId'):
            delivery.update(status='unknown', error={'category': 'unknown', 'message': '未取得有效发送任务ID，不认定已送达，不自动重发。', 'retryable': False})
            return delivery
        delivery.update(status='submitted', stage='query', open_task_id=result['openTaskId'], error=None)
        store.put('notifications', delivery['id'], delivery)
    delivery.update(stage='query', query_attempts=delivery.get('query_attempts', 0) + 1, last_attempt_at=now())
    body, unknown = _invoke(config, ['chat', 'message', 'query-send-status', '--open-task-id', delivery['open_task_id']], runner)
    if unknown:
        # Only a read will be repeated; the message submission is never repeated here.
        delivery.update(status='submitted', error={'category': 'query', 'message': unknown, 'retryable': False}, next_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(timespec='seconds'))
        return delivery
    error = _error(body)
    if error:
        _set_error(delivery, error, config)
        return delivery
    result = body.get('result') if isinstance(body.get('result'), dict) else {}
    status = result.get('sendStatus')
    if body.get('success') is True and status == 'SUCCESS':
        delivery.update(status='delivered', delivered_at=now(), next_retry_at=None, error=None,
                        open_message_id=result.get('openMessageId'), open_conversation_id=result.get('openConversationId'))
    elif status in {'FAILED', 'FAIL', 'FAILURE'}:
        delivery.update(status='blocked', next_retry_at=None, error={'category': 'delivery', 'message': _safe_error(result.get('friendlyHint') or '钉钉返回投递失败；未自动创建新消息重发。'), 'retryable': False})
    else:
        delivery.update(status='submitted', error={'category': 'query', 'message': '已受理，尚未取得明确成功回执。', 'retryable': False}, next_retry_at=(datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat(timespec='seconds'))
    return delivery


def flush(store, config, runner=subprocess.run, max_items=10):
    if not settings(config).get('enabled'):
        return []
    results = []
    with (store.directory / 'notifications.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return []
        try:
            # Recover events saved just before a crash between detection and outbox enqueue.
            enqueue(store, config, store.all('events', 10000))
            for delivery in sorted(store.all('notifications', 10000), key=lambda d: d.get('last_attempt_at') or d['created_at']):
                if delivery['status'] in TERMINAL:
                    continue
                if delivery.get('next_retry_at') and _time(delivery['next_retry_at']) > datetime.now(timezone.utc):
                    continue
                delivery = _process(store, config, delivery, runner)
                store.put('notifications', delivery['id'], delivery)
                results.append(delivery)
                if len(results) >= max_items:
                    break
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return results


def resume_blocked(store, config):
    """After an operator fixes authentication/config, requeue safe blocked operations."""
    count = 0
    for delivery in store.all('notifications', 10000):
        error = delivery.get('error') or {}
        if delivery['status'] == 'blocked' and error.get('category') in {'auth', 'authentication', 'permission', 'authorization', 'configuration'}:
            # Keep the original UUID and body. _process enforces the replay window.
            delivery.update(status='submitted' if delivery.get('open_task_id') else 'pending', next_retry_at=None)
            store.put('notifications', delivery['id'], delivery)
            count += 1
    return count


def summary(store, config):
    cfg = settings(config)
    rows = sorted(store.all('notifications', 10000), key=lambda n: n.get('last_attempt_at') or n['created_at'], reverse=True)
    allowed = ['id', 'status', 'summary', 'created_at', 'last_attempt_at', 'delivered_at', 'error', 'open_message_id']
    recent = [{k: n.get(k) for k in allowed} for n in rows[:5]]
    return dict(enabled=bool(cfg.get('enabled')), recipient_name=cfg.get('recipient_name', '本人'),
                pending=sum(n['status'] in {'pending', 'sending', 'submitted', 'retry_wait'} for n in rows),
                delivered=sum(n['status'] == 'delivered' for n in rows),
                blocked=sum(n['status'] in {'blocked', 'unknown'} for n in rows),
                latest=recent[0] if recent else None, recent=recent,
                message='只在发现更新或检查异常时推送，未发现变化不发消息。' if cfg.get('enabled') else '尚未启用钉钉推送。')
