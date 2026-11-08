"""Small durable SQLite store, content-addressed originals and cross-process run lock."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import sqlite3
import uuid


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def identifier():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:10]


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / 'snapshots').mkdir(exist_ok=True)
        (self.directory / 'reports').mkdir(exist_ok=True)
        with self.connect() as c:
            for table in ['artifacts', 'checks', 'events', 'meta', 'notifications']:
                c.execute(f'CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def connect(self):
        c = sqlite3.connect(self.directory / 'watch.sqlite3', timeout=15)
        c.execute('PRAGMA journal_mode=WAL')
        return c

    def get(self, table, key, default=None):
        assert table in {'artifacts', 'checks', 'events', 'meta', 'notifications'}
        with self.connect() as c:
            row = c.execute(f'SELECT body FROM {table} WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, table, key, value):
        assert table in {'artifacts', 'checks', 'events', 'meta', 'notifications'}
        with self.connect() as c:
            c.execute(f'INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (key, json.dumps(value, ensure_ascii=False)))

    def all(self, table, limit=500):
        assert table in {'artifacts', 'checks', 'events', 'meta', 'notifications'}
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute(f'SELECT body FROM {table} ORDER BY id DESC LIMIT ?', (limit,))]

    def review(self, key, status):
        if status not in {'pending', 'reviewed', 'ignored'}:
            raise ValueError('无效处理状态')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM events WHERE id=?', (key,)).fetchone()
            if not row:
                raise KeyError('未找到变更记录')
            body = json.loads(row[0])
            body.update(review_status=status, reviewed_at=now())
            c.execute('UPDATE events SET body=? WHERE id=?', (json.dumps(body, ensure_ascii=False), key))
        return body

    def blob(self, body):
        digest = hashlib.sha256(body).hexdigest()
        p = self.directory / 'snapshots' / digest
        if not p.exists():
            tmp = p.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            tmp.write_bytes(body)
            tmp.replace(p)
        return digest

    def read_blob(self, digest):
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('无效快照标识')
        body = (self.directory / 'snapshots' / digest).read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            raise ValueError('历史快照校验失败')
        return body

    @contextmanager
    def run_lock(self):
        with (self.directory / 'check.lock').open('a') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise RuntimeError('已有检查正在执行') from e
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def running(self):
        try:
            with self.run_lock():
                return False
        except RuntimeError:
            return True
