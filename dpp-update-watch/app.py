#!/usr/bin/env python3
"""Standalone EU DPP watcher: Python 3.10+, standard library, no model keys."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import threading
from urllib.parse import urlsplit

from watchlib.storage import Store
from watchlib.monitor import check, seed
from watchlib.access import AccessPolicy
from watchlib.notifications import summary as notification_summary, enqueue as enqueue_notification, flush as flush_notifications, resume_blocked
from watchlib.storage import identifier, now

ROOT = Path(__file__).resolve().parent


def config():
    return json.loads((ROOT / 'config.json').read_text())


def state(store):
    try:
        import schedule
        scheduler = schedule.status(ROOT)
    except Exception as e:
        scheduler = {'installed': False, 'active': False, 'next_run': None, 'message': str(e)}
    current_config = config()
    return dict(config=current_config, checks=store.all('checks', 100), events=store.all('events', 2000),
                artifacts=store.all('artifacts', 100), scheduler=scheduler, running=store.running(),
                notifications=notification_summary(store, current_config))


def create_server(store, host, port):
    guard = threading.Lock()
    policy = AccessPolicy(config(), port)

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, content_type='application/json; charset=utf-8', attachment=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if attachment:
                self.send_header('Content-Disposition', 'attachment; filename="' + attachment + '"')
            self.end_headers()
            self.wfile.write(body)

        def allowed_request(self):
            return len(self.headers.get_all('Host', [])) == 1 and policy.permits_request(self.client_address[0], self.headers.get('Host'))

        def do_GET(self):
            if not self.allowed_request():
                return self.send(403, {'error': '来源网段或访问地址未在内网允许列表中'})
            path = urlsplit(self.path).path
            if path == '/api/state':
                return self.send(200, state(store))
            if path in {'/', '/index.html'}:
                return self.send(200, (ROOT / 'web/index.html').read_bytes(), 'text/html; charset=utf-8')
            m = re.fullmatch(r'/reports/([A-Za-z0-9-]+)\.(html|json)', path)
            if m:
                file = store.directory / 'reports' / (m[1] + '.' + m[2])
                if file.exists():
                    return self.send(200, file.read_bytes(), 'text/html; charset=utf-8' if m[2] == 'html' else 'application/json; charset=utf-8')
            m = re.fullmatch(r'/snapshots/([a-f0-9]{64})\.(zip|pdf)', path)
            if m:
                try:
                    body = store.read_blob(m[1])
                    return self.send(200, body, 'application/octet-stream', 'dpp-' + m[1][:12] + '.' + m[2])
                except (OSError, ValueError):
                    pass
            return self.send(404, {'error': '未找到页面或记录'})

        def do_POST(self):
            if not self.allowed_request() or self.headers.get('X-DPP-Watch') != 'local':
                return self.send(403, {'error': '请求未通过内网来源校验'})
            origin = self.headers.get('Origin')
            if not policy.permits_origin(origin, self.headers.get('Host')):
                return self.send(403, {'error': '不允许跨站请求'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > 8192:
                    return self.send(413, {'error': '请求过长'})
                data = json.loads(self.rfile.read(length)) if length else {}
                if not isinstance(data, dict):
                    raise ValueError('请求内容必须是JSON对象')
                path = urlsplit(self.path).path
                if path == '/api/check':
                    if not guard.acquire(blocking=False):
                        return self.send(409, {'error': '已有检查正在执行'})
                    if store.running():
                        guard.release()
                        return self.send(409, {'error': '定时检查正在执行'})
                    def task():
                        try:
                            check(store, config())
                        finally:
                            guard.release()
                    threading.Thread(target=task, daemon=True).start()
                    return self.send(202, {'running': True})
                m = re.fullmatch(r'/api/events/([A-Za-z0-9-]+)', path)
                if m:
                    return self.send(200, store.review(m[1], data.get('review_status')))
                return self.send(404, {'error': '未找到操作'})
            except (ValueError, TypeError) as e:
                return self.send(400, {'error': str(e)})
            except KeyError:
                return self.send(404, {'error': '未找到记录'})

    server = ThreadingHTTPServer((host, port), Handler)
    policy.port = server.server_port
    return server


def serve(store, port, host=None):
    host = host or config().get('bind_host', '127.0.0.1')
    server = create_server(store, host, port)
    print('DPP更新跟踪：' + config().get('public_url', f'http://{host}:{port}') + f'（监听 {host}:{port}）', flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description='欧委会DPP语义资源更新跟踪，不依赖大模型')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='执行一次检查')
    sub.add_parser('status', help='输出当前状态')
    sub.add_parser('notify-pending', help='处理钉钉待发送通知及投递回执，不检查官网')
    sub.add_parser('notify-test', help='给已配置接收人发送一条连通测试')
    sub.add_parser('notify-resume', help='修复钉钉登录或配置后恢复受阻通知，保留原幂等UUID')
    seeder = sub.add_parser('seed', help='从已有官方原文导入基线')
    seeder.add_argument('--manifest')
    web = sub.add_parser('serve', help='启动监控查看服务')
    web.add_argument('--port', type=int, default=config().get('port', 8768))
    web.add_argument('--host', default=config().get('bind_host', '127.0.0.1'))
    args = parser.parse_args()
    store = Store(ROOT / 'data')
    if args.command == 'serve':
        serve(store, args.port, args.host)
        return
    if args.command == 'seed':
        result = seed(store, config(), ROOT, args.manifest)
    elif args.command == 'notify-pending':
        deliveries = flush_notifications(store, config(), max_items=2)
        result = notification_summary(store, config())
    elif args.command == 'notify-test':
        delivery = enqueue_notification(store, config(), [dict(id='notification-test-' + identifier(), created_at=now(), kind='notification_test')], test=True)
        if delivery is None:
            raise ValueError('尚未启用钉钉推送')
        flush_notifications(store, config(), max_items=2)
        result = store.get('notifications', delivery['id'])
        # Do not echo the entire message/profile in service logs.
        result = {k: result.get(k) for k in ['id', 'status', 'open_task_id', 'open_message_id', 'delivered_at', 'error']}
    elif args.command == 'notify-resume':
        result = {'resumed': resume_blocked(store, config())}
        flush_notifications(store, config(), max_items=2)
    elif args.command == 'check':
        result = check(store, config())
    else:
        result = state(store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('status') in {'error', 'partial'}:
        sys.exit(1)


if __name__ == '__main__':
    main()
