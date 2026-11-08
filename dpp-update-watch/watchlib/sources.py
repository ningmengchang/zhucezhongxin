"""Official source discovery and bounded HTTPS download, using only Python stdlib."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
import time
import urllib.error
import urllib.parse
import urllib.request


def checked_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or not (p.hostname == 'ec.europa.eu' or (p.hostname or '').endswith('.ec.europa.eu')) or p.username or p.password:
        raise ValueError('来源链接不是允许的欧委会 HTTPS 地址：' + url)
    return url


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, timeout=40, attempts=2, limit=24 * 1024 * 1024):
    checked_url(url)
    opener = urllib.request.build_opener(OfficialRedirects())
    for i in range(attempts):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'FutrisDPPWatch/1.0 (independent update monitor)', 'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'})
            with opener.open(request, timeout=timeout) as response:
                checked_url(response.geturl())
                declared = response.headers.get('Content-Length')
                if declared and int(declared) > limit:
                    raise ValueError('下载超过大小上限')
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise ValueError('下载超过大小上限')
                return body, dict(response.headers), response.geturl()
        except urllib.error.HTTPError as e:
            if i + 1 == attempts or e.code not in {429, 500, 502, 503, 504}:
                raise
            retry = e.headers.get('Retry-After', '')
            if retry and (not retry.isdigit() or int(retry) > 60):
                raise RuntimeError('官网要求稍后重试（Retry-After=' + retry + '）；本次记录失败，保留旧基线。') from e
            time.sleep(max(int(retry or 0), i + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if i + 1 == attempts:
                raise
            time.sleep(i + 1)


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return ' '.join(''.join(c if isinstance(c, str) else c.text() for c in self.children).split())

    def nodes(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.nodes()


class PageTree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, value):
        if self.stack[-1].tag not in {'script', 'style'}:
            self.stack[-1].children.append(value)


def discover(page_bytes, page_url, watch):
    parser = PageTree()
    parser.feed(page_bytes.decode('utf-8-sig'))
    cards = []
    for node in parser.root.nodes():
        if 'ecl-file' not in node.attrs.get('class', '').split():
            continue
        children = list(node.nodes())
        title = next((n.text() for n in children if 'ecl-file__title' in n.attrs.get('class', '').split()), '')
        date = next((n.text() for n in children if 'ecl-file__primary-meta-item' in n.attrs.get('class', '').split() and re.search(r'\b20\d\d\b', n.text())), '')
        desc = next((n.text() for n in children if 'ecl-file__description' in n.attrs.get('class', '').split()), '')
        links = [n for n in children if n.tag == 'a' and '/document/download/' in n.attrs.get('href', '')]
        for link in links:
            cards.append({'title': title or link.attrs.get('data-untranslated-label', ''), 'date': date, 'description': desc, 'url': urllib.parse.urljoin(page_url, link.attrs['href'])})
    # A conservative fallback when the official card layout changes: use explicit link labels.
    if not cards:
        for n in parser.root.nodes():
            if n.tag == 'a' and '/document/download/' in n.attrs.get('href', ''):
                title = n.attrs.get('data-untranslated-label', '')
                if title:
                    cards.append({'title': title, 'date': '', 'description': '', 'url': urllib.parse.urljoin(page_url, n.attrs['href'])})
    found = {}
    missing = []
    for item in watch:
        matches = [c for c in cards if item['title'].casefold() in c['title'].casefold()]
        if not matches:
            missing.append(item['title'])
            continue
        # Prefer the newest dated publication, then an explicit version if present.
        def rank(card):
            versions = re.findall(r'\b(\d+)\.(\d+)(?:\.(\d+))?', card['title'])
            months = {name.casefold(): i for i, name in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'], 1)}
            match = re.search(r'(\d{1,2})\s+([A-Za-z]+)\s+(20\d\d)', card['date'])
            date_rank = (int(match[3]), months.get(match[2].casefold(), 0), int(match[1])) if match else (0, 0, 0)
            version_rank = max((tuple(int(x or 0) for x in v) for v in versions), default=(0, 0, 0))
            return date_rank, version_rank
        card = max(matches, key=rank)
        checked_url(card['url'])
        found[item['key']] = {**item, **card, 'source_url': card['url']}
    if not found:
        raise ValueError('未识别到语义资源下载入口，可能官网布局变化或返回了验证页；本次不更新基线。')
    inventory = sorted(cards, key=lambda c: (c['title'], c['url']))
    return found, inventory, missing
