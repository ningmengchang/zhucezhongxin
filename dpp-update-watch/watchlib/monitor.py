"""One check: discover, download, compare, record evidence. No model involvement."""
from pathlib import Path
import hashlib
import html
import json
import os
import subprocess
import traceback

from .diffing import inspect_artifact, compare_artifacts
from .sources import discover, download
from .storage import identifier, now
from .notifications import enqueue, flush


def versions(info):
    return '、'.join(str(v) for v in info.get('versions', [])[:8]) or '未识别版本号，以文件校验值为准'


def render_report(store, event):
    esc = lambda x: html.escape(str(x))
    changes = event.get('changes', [])
    entries = []
    for change in changes:
        entries.append('<article><h3>' + esc(change.get('path', '')) + '</h3><p>' + esc(change.get('rule', change.get('impact', '内容变更'))) + '</p><p>' + esc(change.get('impact', '')) + '</p><div class="values"><section><b>原值</b><pre>' + esc(change.get('old', '')) + '</pre></section><section><b>新值</b><pre>' + esc(change.get('new', '')) + '</pre></section></div></article>')
    downloads = []
    for label, key in [('变更前原文', 'before_sha256'), ('变更后原文', 'after_sha256')]:
        if event.get(key):
            extension = '.zip' if event.get('artifact_kind', '').endswith('zip') else '.pdf'
            downloads.append('<a href="/snapshots/' + event[key] + extension + '">' + label + '</a>')
    source = event.get('source_url')
    if source:
        downloads.append('<a href="' + esc(source) + '" target="_blank" rel="noopener noreferrer">官网原始链接</a>')
    doc = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + esc(event['title']) + '''</title><style>body{font:16px/1.7 system-ui,"Noto Sans CJK SC",sans-serif;color:#203337;background:#f5f7f6;margin:0}main{max-width:1100px;margin:40px auto;padding:24px}h1{font-size:28px}a{color:#176c60;margin-right:22px}article{background:white;padding:20px;margin:20px 0;border:1px solid #dce4df;border-radius:8px}h3{font:14px ui-monospace,monospace;overflow-wrap:anywhere}.values{display:grid;grid-template-columns:1fr 1fr;gap:18px}pre{font:13px/1.7 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f4;padding:14px}section{min-width:0}.note{color:#65716c}@media(max-width:700px){.values{grid-template-columns:1fr}main{margin:0;padding:18px}}</style><main><a href="/">返回更新跟踪</a><h1>''' + esc(event['title']) + '</h1><p>' + esc(event['created_at']) + '</p><p>' + esc(event['summary']) + '</p><p>' + ' '.join(downloads) + '</p><p class="note">由本地规则比较生成，未调用大模型。兼容性判断为规则推断，不能代替完整 SHACL 校验或官方迁移说明。原文快照及完整 JSON 报告已保留。</p>' + ''.join(entries) + ('<p>本记录没有逐字段差异，请核对原文或错误说明。</p>' if not entries else '') + '</main></html>'
    report = store.directory / 'reports' / event['id']
    report.with_suffix('.html').write_text(doc, encoding='utf-8')
    report.with_suffix('.json').write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding='utf-8')


def make_event(store, title, summary, kind, severity='info', **extra):
    eid = identifier()
    event = {**dict(id=eid, created_at=now(), title=title, summary=summary, kind=kind,
                   severity=severity, review_status='pending', changes=[], report_url=f'/reports/{eid}.html'), **extra}
    render_report(store, event)
    store.put('events', eid, event)
    return event


def notify(events, config):
    if not events or not config.get('desktop_notifications', True):
        return
    env = dict(os.environ)
    runtime = Path(f'/run/user/{os.getuid()}')
    if (runtime / 'bus').exists():
        env.setdefault('XDG_RUNTIME_DIR', str(runtime))
        env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=' + str(runtime / 'bus'))
    try:
        subprocess.run(['notify-send', '--app-name=DPP更新跟踪', '--expire-time=12000',
                        'DPP 官网更新检查', f'{len(events)} 条新记录。' + events[0]['title'] + '。打开 ' + config.get('public_url', 'http://localhost:' + str(config.get('port', 8768))) + ' 查看。'],
                       env=env, timeout=5, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass  # Desktop notification is best effort; records and reports are durable.


def seed(store, config, root, manifest_path=None):
    manifest = Path(manifest_path) if manifest_path else root / config['baseline_manifest']
    manifest = manifest.resolve()
    if not manifest.exists():
        return {'seeded': 0, 'message': '没有本地旧包；首次成功下载将建立基线。'}
    data = json.loads(manifest.read_text())
    added = 0
    with store.run_lock():
        for spec in config['watch']:
            if store.get('artifacts', spec['key']):
                continue
            file = next((f for f in data.get('files', []) if f['title'] == spec['title']), None)
            if not file:
                continue
            path = (manifest.parent / file['localPath']).resolve()
            if not path.is_relative_to(manifest.parent):
                raise ValueError('基线文件路径越界')
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != file['sha256']:
                raise ValueError('本地旧包与下载清单校验值不符：' + file['filename'])
            info = inspect_artifact(body, spec['kind'], spec['title'])
            digest = store.blob(body)
            store.put('artifacts', spec['key'], {**spec, 'source_url': file['url'], 'sha256': digest,
                      'payload_sha256': info['payload_sha256'], 'version': versions(info),
                      'last_changed_at': None, 'last_checked_at': None,
                      'baseline_date': data.get('publicationDate'), 'baseline_imported_at': now(),
                      'snapshot_url': '/snapshots/' + digest + ('.zip' if spec['kind'].endswith('zip') else '.pdf')})
            added += 1
    return {'seeded': added, 'message': '已从本地官方原文导入基线；尚需联网检查当前版本。'}


def check(store, config, fetch=download):
    with store.run_lock():
        # An interrupted process must not remain permanently displayed as 'running'.
        for previous in store.all('checks', 10):
            if previous.get('status') == 'running':
                previous.update(status='error', finished_at=now(), error='上次检查进程中断；未完成的下载不作为新基线。', summary='上次检查中断')
                store.put('checks', previous['id'], previous)
        run = dict(id=identifier(), started_at=now(), finished_at=None, status='running', summary='正在检查官网与附件', artifacts=[], error=None)
        store.put('checks', run['id'], run)
        events, errors = [], []
        opts = dict(timeout=config.get('timeout_seconds', 40), attempts=config.get('attempts', 2))
        try:
            page, headers, final_url = fetch(config['source_page'], limit=4 * 1024 * 1024, **opts)
            found, inventory, missing = discover(page, final_url, config['watch'])
            previous_page = store.get('meta', 'inventory')
            signature = hashlib.sha256(json.dumps(inventory, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            # Ignore navigation, cookies, analytics; compare only official document cards.
            if previous_page and previous_page['signature'] != signature:
                old = previous_page['inventory']
                events.append(make_event(store, '官网文档目录发生变化', '官方资源标题、下载入口、日期或说明有变化，请核对；附件内容另行检查。', 'page_changed',
                    source_url=config['source_page'], changes=[{'path': '官网文档目录', 'rule': '下载目录变化', 'old': json.dumps(old, ensure_ascii=False, indent=2), 'new': json.dumps(inventory, ensure_ascii=False, indent=2), 'impact': '页面变化不等于模型已更新。', 'severity': 'info'}]))
            store.blob(page)
            store.put('meta', 'inventory', dict(signature=signature, inventory=inventory, checked_at=now()))
            errors.extend('官网未找到资源：' + title + '；保留该资源旧基线。' for title in missing)
            for key, item in found.items():
                try:
                    body, response_headers, url = fetch(item['source_url'], **opts)
                    content_type = next((v for k, v in response_headers.items() if k.lower() == 'content-type'), '')
                    if 'text/html' in content_type.lower() or body.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                        raise ValueError('附件地址返回HTML页面，未取得模型文件；保留旧基线。')
                    info = inspect_artifact(body, item['kind'], item['title'])
                    old = store.get('artifacts', key)
                    digest = store.blob(body)
                    item_result = dict(key=key, title=item['title'], source_url=url, sha256=digest, status='unchanged', summary='内容无变化')
                    changed_at = old.get('last_changed_at') if old else None
                    if old and old['sha256'] != digest:
                        diff = compare_artifacts(store.read_blob(old['sha256']), body, item['kind'], item['title'])
                        if diff['changed']:
                            changed_at = now()
                            event = make_event(store, item['title'] + ' 内容更新', diff['summary'], 'resource_changed', diff['severity'],
                                artifact_key=key, artifact_kind=item['kind'], source_url=url,
                                before_sha256=old['sha256'], after_sha256=digest, changes=diff.get('changes', []),
                                versions_before=diff.get('versions_before', []), versions_after=diff.get('versions_after', []), counts=diff.get('counts', {}))
                            events.append(event)
                            item_result.update(status='changed', summary=diff['summary'])
                        else:
                            item_result['summary'] = '原始文件校验值变化，解包后逻辑内容无变化（重打包／JSON排版变化）。'
                            item_result['packaging_only'] = True
                    elif not old:
                        events.append(make_event(store, item['title'] + ' 已建立基线', '首次成功下载，没有可比较的旧版本；不代表今天发布了新版。', 'new_resource', artifact_key=key, artifact_kind=item['kind'], after_sha256=digest, source_url=url))
                        item_result.update(status='baseline', summary='首次成功下载，建立基线')
                    store.put('artifacts', key, {**item, 'source_url': url, 'sha256': digest, 'payload_sha256': info['payload_sha256'],
                        'version': versions(info), 'last_checked_at': now(), 'last_changed_at': changed_at,
                        'baseline_date': old.get('baseline_date') if old else None,
                        'snapshot_url': '/snapshots/' + digest + ('.zip' if item['kind'].endswith('zip') else '.pdf')})
                    run['artifacts'].append(item_result)
                except Exception as e:
                    errors.append(item['title'] + '：' + str(e))
                    run['artifacts'].append({'key': key, 'title': item['title'], 'status': 'error', 'error': str(e)})
        except Exception as e:
            errors.append(str(e))
        if errors:
            message = '\n'.join(errors)
            if store.get('meta', 'last_failure') != message:
                events.append(make_event(store, '官网更新检查未全部完成', message, 'check_failed', 'medium', source_url=config['source_page']))
            store.put('meta', 'last_failure', message)
            run.update(status='partial' if any(a['status'] != 'error' for a in run['artifacts']) else 'error', error=message,
                       summary='检查未全部成功；失败资源保留旧基线，请查看原因。')
        else:
            store.put('meta', 'last_failure', None)
            changed = any(e['kind'] in {'resource_changed', 'page_changed'} for e in events)
            run.update(status='changed' if changed else 'unchanged', summary='发现更新，已生成待核对记录。' if changed else '检查完成，未发现文档目录或模型内容变化。')
        run.update(finished_at=now(), event_ids=[e['id'] for e in events])
        store.put('checks', run['id'], run)
        notify(events, config)
        try:
            enqueue(store, config, events)
            flush(store, config, max_items=2)
        except Exception:
            # Checking models remains successful even if the independent push channel
            # encounters a problem. Saved events will be recovered by notify-pending.
            store.put('meta', 'notification_error', '钉钉通知处理未完成，更新记录已保留；请查看推送发件箱和服务日志。')
        return run
