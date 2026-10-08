#!/usr/bin/env python3
"""On-demand public news crawler. Python 3.10+, no dependencies."""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
AGENT = 'SourceCrawl/1.0'
ASSETS = {'BTC': ['bitcoin', 'BTC'], 'SOL': ['solana', 'SOL'],
          'LINK': ['chainlink'], 'ETH': ['ethereum', 'ether', 'ETH'], 'ZEC': ['zcash', 'ZEC']}
COINS = {'BTC': 'btc-bitcoin', 'SOL': 'sol-solana', 'LINK': 'link-chainlink',
         'ETH': 'eth-ethereum', 'ZEC': 'zec-zcash'}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(url):
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_')
             and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', urlencode(query), ''))


def same_host(a, b):
    # Publisher-owned aliases used by the supplied sources, not arbitrary siblings.
    aliases = {'blog.chain.link': 'chain.link', 'blog.ondo.finance': 'ondo.finance',
               'research.glassnode.com': 'glassnode.com', 'app.rwa.xyz': 'rwa.xyz'}
    def host(url):
        value = (urlsplit(url).hostname or '').removeprefix('www.')
        return aliases.get(value, value)
    return host(a) == host(b)


def sources(path):
    result = []
    section = ''
    for line in path.read_text().splitlines():
        if line.startswith('## '):
            section = line[3:]
        match = re.match(r'^\| \[([^]]+)\]\((https?://[^)]+)\) \|', line)
        if match:
            name, url = match.groups()
            note = line.split('|')[-2].strip()
            low = note.lower()
            kind = ('project_or_vendor' if any(x in low for x in ('project', 'vendor', 'issuer', 'company'))
                    else 'investor_research' if 'investor perspective' in low
                    else 'academic' if 'academic' in low or 'unreviewed' in low
                    else 'independent_reporting')
            if urlsplit(url).hostname in ('www.near.org', 'huggingface.co', 'www.chainalysis.com', 'research.glassnode.com'):
                kind = 'project_or_vendor'
            if urlsplit(url).hostname in ('www.coinbase.com', 'www.binance.com'):
                kind = 'exchange_research'
            if urlsplit(url).hostname == 'www.coingecko.com':
                kind = 'data_provider_research'
            result.append({'name': name, 'url': canonical(url), 'category': section,
                           'source_type': kind, 'access_notes': note})
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    def __init__(self, delay=1.5, timeout=20):
        self.delay, self.timeout = delay, timeout
        self.opener = build_opener(NoRedirect)
        self.robots, self.last = {}, {}

    def raw(self, url, delay=None):
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname:
            raise ValueError('Only HTTP(S) URLs are supported')
        host = p.netloc
        time.sleep(max(0, self.last.get(host, 0) + (self.delay if delay is None else delay) - time.monotonic()))
        self.last[host] = time.monotonic()
        req = Request(url, headers={'User-Agent': AGENT, 'Accept': 'text/html,application/xml,application/json,text/plain;q=0.8', 'Accept-Encoding': 'identity'})
        with self.opener.open(req, timeout=self.timeout) as response:
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise ValueError('Response exceeds 5 MB limit')
            return body.decode(response.headers.get_content_charset() or 'utf-8', errors='replace'), response.headers.get('Content-Type', '')

    def rules(self, url):
        p = urlsplit(url)
        origin = f'{p.scheme}://{p.netloc}'
        if origin not in self.robots:
            rp = RobotFileParser(origin + '/robots.txt')
            robots_url = rp.url
            for hop in range(6):
                try:
                    text, _ = self.raw(robots_url)
                    rp.parse(text.splitlines())
                    break
                except HTTPError as exc:
                    if exc.code in (301, 302, 303, 307, 308) and exc.headers.get('Location') and hop < 5:
                        robots_url = urljoin(robots_url, exc.headers['Location'])
                        continue
                    if exc.code in (404, 410):
                        rp.parse([])
                        break
                    raise RuntimeError(f'robots.txt unavailable: HTTP {exc.code}') from exc
            self.robots[origin] = rp
        return self.robots[origin]

    def get(self, url):
        for _ in range(6):
            rp = self.rules(url)
            if not rp.can_fetch(AGENT, url):
                raise PermissionError('Disallowed by robots.txt')
            rate = rp.request_rate(AGENT)
            delay = max(self.delay, rp.crawl_delay(AGENT) or 0, rate.seconds / rate.requests if rate else 0)
            try:
                text, content_type = self.raw(url, delay)
                return text, content_type, url
            except HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308) and exc.headers.get('Location'):
                    url = urljoin(url, exc.headers['Location'])
                    continue
                raise
        raise RuntimeError('Too many redirects')


class ConnectorFetcher(Fetcher):
    """File-backed request/response bridge to the host's connected web tool.

    The Python crawler retains robots and pacing policy. The host runner handles
    transport only, and returns unprocessed HTML or plain API/robots text.
    """
    def __init__(self, bridge, delay=1.5, timeout=120):
        super().__init__(delay, timeout)
        self.bridge = bridge
        self.bridge.mkdir(parents=True, exist_ok=True)

    def raw(self, url, delay=None):
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname:
            raise ValueError('Only HTTP(S) URLs are supported')
        time.sleep(max(0, self.last.get(p.netloc, 0) + (self.delay if delay is None else delay) - time.monotonic()))
        self.last[p.netloc] = time.monotonic()
        ident = uuid.uuid4().hex
        request = self.bridge / (ident + '.request.json')
        response = self.bridge / (ident + '.response.json')
        temporary = request.with_suffix('.tmp')
        temporary.write_text(json.dumps({'id': ident, 'url': url}))
        temporary.replace(request)
        deadline = time.monotonic() + self.timeout
        while not response.exists():
            host = self.bridge / 'host.json'
            if host.exists():
                try:
                    if json.loads(host.read_text()).get('status') == 'paused':
                        deadline = time.monotonic() + self.timeout
                except (OSError, ValueError):
                    pass  # A transient marker write cannot invalidate a request.
            if time.monotonic() >= deadline:
                request.unlink(missing_ok=True)
                raise TimeoutError('Connected transport unavailable; start run_connected.js in the tool host')
            time.sleep(0.1)
        try:
            data = json.loads(response.read_text())
        finally:
            request.unlink(missing_ok=True)
            response.unlink(missing_ok=True)
        if data.get('error'):
            raise RuntimeError(data['error'])
        status = data.get('status', 200)
        final = data.get('url', url)
        if final != url:
            # Keep redirect permission checks in the parent Fetcher.get path.
            raise HTTPError(url, 302, 'Connected redirect', {'Location': final}, None)
        if status >= 400:
            raise HTTPError(url, status, 'Connected HTTP error', {}, None)
        body = data.get('body', '')
        if len(body.encode('utf-8')) > 5_000_000:
            raise ValueError('Response exceeds 5 MB limit')
        return body, data.get('content_type', 'text/html')


class Page(HTMLParser):
    """Extract readable article blocks, metadata and discovery links."""
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.links, self.feeds, self.meta, self.blocks, self.jsonld = [], [], {}, [], []
        self.stack, self.buffer = [], []
        self.title = ''
        self.heading = ''
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.stack.append((tag, a))
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if tag == 'link':
            rel = a.get('rel', '').split()
            if 'canonical' in rel:
                self.meta.setdefault('canonical', a.get('href', ''))
            if 'alternate' in rel and any(x in a.get('type', '') for x in ('rss', 'atom')):
                self.feeds.append(a.get('href', ''))
        if tag == 'meta':
            self.meta.setdefault(a.get('property', a.get('name', '')), a.get('content', ''))
        if tag == 'time' and a.get('datetime'):
            self.meta.setdefault('date', a['datetime'])
        if tag in ('p', 'h1', 'h2', 'h3', 'li', 'title', 'script'):
            self.buffer = []
        if tag in ('meta', 'link', 'img', 'br', 'hr', 'input', 'source', 'wbr'):
            self.stack.pop()

    def handle_data(self, data):
        if any(t in ('nav', 'footer', 'header', 'aside', 'style', 'noscript') for t, _ in self.stack):
            return
        if any(re.search(r'(?:^|[\s_-])(?:related|recommended|trending|popular|newsletter|advertisement|sidebar|footer)(?:$|[\s_-])',
                         a.get('class', '') + ' ' + a.get('id', ''), re.I) for _, a in self.stack):
            return
        self.buffer.append(data)

    def handle_endtag(self, tag):
        text = re.sub(r'\s+', ' ', ''.join(self.buffer)).strip()
        if tag == 'script':
            attrs = next((a for t, a in reversed(self.stack) if t == 'script'), {})
            if attrs.get('type') == 'application/ld+json':
                try:
                    self.jsonld.append(json.loads(''.join(self.buffer)))
                except ValueError:
                    pass
        elif not any(t == 'script' for t, _ in self.stack):
            if tag == 'title':
                if not self.title:
                    self.title = text
            elif tag in ('p', 'h1', 'h2', 'h3', 'li') and text:
                if tag == 'h1' and not self.heading:
                    self.heading = text
                scope = 2 if any(t == 'article' for t, _ in self.stack) else 1 if any(t == 'main' for t, _ in self.stack) else 0
                self.blocks.append((scope, text))
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in ('p', 'h1', 'h2', 'h3', 'li', 'title', 'script'):
            self.buffer = []

    def article(self):
        def walk(obj):
            if isinstance(obj, list):
                for child in obj:
                    yield from walk(child)
            elif isinstance(obj, dict):
                yield obj
                for child in obj.values():
                    if isinstance(child, (dict, list)):
                        yield from walk(child)
        for obj in walk(self.jsonld):
            types = obj.get('@type', [])
            if isinstance(types, str):
                types = [types]
            if any(x in types for x in ('NewsArticle', 'Article', 'BlogPosting', 'ScholarlyArticle')):
                return obj
        return {}

    def text(self):
        scope = max((scope for scope, _ in self.blocks), default=0)
        selected = [text for level, text in self.blocks if level == scope]
        return clean_article_body('\n\n'.join(dict.fromkeys(selected)))


def clean_article_body(body):
    """Remove recognizable repeated widgets without inferring missing content."""
    blocks = body.split('\n\n')
    numbered_news = [i for i, block in enumerate(blocks)
                     if re.match(r'^\d{1,2}[^\d]', block) and
                     re.search(r'\d+\s*(?:minutes?|hours?|days?) ago$', block)]
    if len(numbered_news) >= 3:
        blocks = blocks[:numbered_news[0]]
    return '\n\n'.join(block for block in blocks if block.strip().lower() not in ('newsletters', 'advertisement'))


def matches(text, words):
    return [word for word in words if re.search(r'(?<!\w)' + re.escape(word) + r'(?!\w)', text, re.I)]


def classify(title, body, topics):
    # Limit body evidence to reduce matches from long footers and unrelated links.
    text = title + '\n' + body[:16000]
    found = {topic: matches(text, words) for topic, words in topics.items()}
    found = {k: v for k, v in found.items() if v}
    assets = [asset for asset, words in ASSETS.items() if matches(text, words)]
    if re.search(r'\bLINK\b', title):
        assets = sorted(set(assets + ['LINK']))
    relevant = bool(assets or 'tokenization' in found or 'crypto' in found or 'ai' in found)
    if 'ai' in found and ('crypto' in found or assets):
        found['ai_crypto_intersection'] = ['AI and crypto evidence in article']
    return relevant, found, assets


def parse_date(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def xml_links(text, base):
    root = ET.fromstring(text)
    links = []
    for node in root.iter():
        tag = node.tag.split('}')[-1]
        if tag in ('loc', 'link'):
            value = node.get('href') or (node.text or '').strip()
            if value:
                links.append(urljoin(base, value))
    return list(dict.fromkeys(links)), root.tag.split('}')[-1]


def candidate(url, seed):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not same_host(url, seed):
        return False
    if canonical(url) == canonical(seed):
        return False
    if re.search(r'/(?:login|signin|subscribe|pricing|account|author|authors|tag|tags|category|categories|search|privacy|terms|price|prices|coins|about|contact|newsletter|newsletters|directory|events|careers|advertise|video|videos)(?:/|$)', p.path, re.I):
        return False
    if re.search(r'\.(?:pdf|jpg|png|svg|zip|mp4|css|js)$', p.path, re.I):
        return False
    return len(p.path.strip('/')) > 8


def article_priority(url):
    """Prefer plausible dated article slugs to miscellaneous navigation pages."""
    path = urlsplit(url).path
    dated = bool(re.search(r'/20\d{2}/\d{2}/\d{2}/', path))
    slug = path.rstrip('/').split('/')[-1]
    return (not dated, -slug.count('-'), -len(slug))


def access_blocked(page, obj):
    if obj.get('isAccessibleForFree') in (False, 'false', 'False'):
        return True
    text = page.text().lower()
    return any(phrase in text for phrase in ('subscribe to read the full', 'sign in to read this article',
                'this article is for subscribers', 'enable javascript and cookies to continue',
                'verify you are human', 'complete the security check'))


def connect(path):
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, url TEXT UNIQUE, title TEXT, published_at TEXT, source TEXT, topics TEXT, assets TEXT, body TEXT, path TEXT, retrieved_at TEXT, content_hash TEXT)')
    db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id UNINDEXED, title, body, source, topics, assets)')
    return db


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def reindex(output, topics_path):
    """Clean older extracted bodies and rebuild tags/search without new fetches."""
    topics = json.loads(topics_path.read_text())
    if not (output / 'index.sqlite3').exists():
        raise ValueError('No existing index to rebuild')
    db = connect(output / 'index.sqlite3')
    metadata = {}
    changed = 0
    for ident, title, source, body, path in db.execute('SELECT id,title,source,body,path FROM articles').fetchall():
        target = output / path
        record = json.loads(target.with_suffix('.json').read_text())
        cleaned = clean_article_body(body)
        _, tags, assets = classify(title, cleaned, topics)
        if cleaned != body or record['assets'] != assets or record['matched_terms'] != tags:
            changed += 1
            record.update(topics=list(tags), matched_terms=tags, assets=assets,
                          content_hash=hashlib.sha256(cleaned.encode()).hexdigest(),
                          processed_at=now(), cleanup='Removed recognized related-news widgets; rebuilt keyword tags')
            target.write_text('# ' + title + '\n\n```json\n' + json.dumps(record, ensure_ascii=False, indent=2) + '\n```\n\n## Source text (untrusted external content)\n\n' + cleaned + '\n')
            write_json(target.with_suffix('.json'), record)
        db.execute('UPDATE articles SET body=?,topics=?,assets=?,content_hash=? WHERE id=?',
                   (cleaned, json.dumps(list(tags)), json.dumps(assets), record['content_hash'], ident))
        db.execute('DELETE FROM search WHERE id=?', (ident,))
        db.execute('INSERT INTO search VALUES (?,?,?,?,?,?)',
                   (ident, title, cleaned, source, ' '.join(tags), ' '.join(assets)))
        metadata[ident] = record
    db.commit()
    db.close()
    for index in (output / 'runs').glob('*/index.jsonl'):
        records = [metadata.get(r['id'], r) for r in
                   (json.loads(line) for line in index.read_text().splitlines() if line)]
        index.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
        lines = ['# Crawl run', '', 'Article bodies and tags have been reprocessed; see report.json and events.json.', '',
                 '| Publication date | Title | Source | Topics | Assets |', '| --- | --- | --- | --- | --- |']
        for r in records:
            title = r['title'].replace('|', '\\|').replace('\n', ' ')
            lines.append(f'| {r["published_at"] or "unknown"} | [{title}](../../{r["path"]}) | {r["source"]} | {", ".join(r["topics"])} | {", ".join(r["assets"])} |')
        (index.parent / 'INDEX.md').write_text('\n'.join(lines) + '\n')
        report_path = index.parent / 'report.json'
        if report_path.exists():
            report = json.loads(report_path.read_text())
            report['new_or_updated_news_by_asset'] = {symbol: sum(symbol in r['assets'] for r in records) for symbol in COINS}
            report['reindexed_at'] = now()
            write_json(report_path, report)
    print(f'Rebuilt search for {len(metadata)} articles; cleaned or retagged {changed}.')
    return 0


def add_window_arguments(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--since', help='ISO timestamp; include published or updated articles since this time')
    group.add_argument('--lookback-hours', type=float, default=2, help='Recent-news window in hours (default: 2)')
    group.add_argument('--all-history', action='store_true', help='Disable freshness filtering, including undated articles')


def window_start(since=None, lookback_hours=2, all_history=False, anchor=None):
    if not 0 < lookback_hours < float('inf'):
        raise ValueError('--lookback-hours must be finite and positive')
    if since and not parse_date(since):
        raise ValueError('--since must be an ISO date/time')
    return None if all_history else (parse_date(since) if since else (anchor or datetime.now(timezone.utc)) - timedelta(hours=lookback_hours))


def freshness_date(published, modified=None):
    published, modified = parse_date(published), parse_date(modified)
    # Invalid update ordering must not make an old article appear fresh.
    return modified if modified and (published is None or modified >= published) else published


def crawl(args, fetcher=None):
    topics = json.loads(args.topics.read_text())
    listing = sources(args.sources)
    if args.source:
        listing = [s for s in listing if any(x.lower() in (s['name'] + ' ' + s['url']).lower() for x in args.source)]
    if not listing:
        raise ValueError('No matching sources')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run = output / 'runs' / (datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    run.mkdir(parents=True)
    db = connect(output / 'index.sqlite3')
    fetcher = fetcher or (ConnectorFetcher(args.bridge, args.delay, max(300, args.timeout))
                         if args.backend == 'connected' else Fetcher(args.delay, args.timeout))
    records, events, stats = [], [], []
    seen = set()
    started = datetime.now(timezone.utc)
    cutoff = window_start(args.since, args.lookback_hours, args.all_history, started)
    for source in listing:
        if args.backend == 'connected':
            write_json(args.bridge / 'progress.json', {'run': str(run), 'source': source['name'],
                       'sources_completed': len(stats), 'sources_total': len(listing), 'new_or_updated_articles': len(records)})
        seed = source['url']
        stat = {'source': source['name'], 'index_checked_at': now(), 'index_accessible': False, 'discovered': 0, 'fetched': 0, 'indexed': 0, 'duplicates': 0, 'skipped': 0, 'errors': 0, 'limit_reached': False}
        print(f"Crawling {source['name']}", flush=True)
        def log(url, status, reason):
            events.append({'source': source['name'], 'url': url, 'status': status, 'reason': str(reason)})
            stat['errors' if status == 'error' else 'skipped'] += 1
        try:
            html, _, final_seed = fetcher.get(seed)
            stat['index_accessible'] = True
            page = Page(html)
            pending = [canonical(urljoin(final_seed, x)) for x in page.links if candidate(urljoin(final_seed, x), final_seed)]
            discovery = [urljoin(final_seed, x) for x in page.feeds]
            discovery += fetcher.rules(final_seed).site_maps() or []
            discovery = list(dict.fromkeys(discovery))
            visited_discovery = set()
            while discovery and len(visited_discovery) < args.discovery_pages:
                url = discovery.pop(0)
                if url in visited_discovery or not same_host(url, final_seed):
                    continue
                visited_discovery.add(url)
                try:
                    text, _, final = fetcher.get(url)
                    links, kind = xml_links(text, final)
                    if kind == 'sitemapindex':
                        discovery.extend(links)
                    else:
                        pending.extend(canonical(x) for x in links if candidate(x, final_seed))
                except Exception as exc:
                    log(url, 'error', exc)
            pending = list(dict.fromkeys(pending))
            pending.sort(key=article_priority)
            stat['discovered'] = len(pending)
            stat['limit_reached'] = bool(discovery) or (args.max_articles > 0 and len(pending) > args.max_articles)
            if args.max_articles:
                pending = pending[:args.max_articles]
            for url in pending:
                if url in seen:
                    stat['duplicates'] += 1
                    continue
                seen.add(url)
                try:
                    html, content_type, final = fetcher.get(url)
                    stat['fetched'] += 1
                    if 'html' not in content_type:
                        log(url, 'skip', 'Unsupported article format; HTML only')
                        continue
                    page = Page(html)
                    obj = page.article()
                    if access_blocked(page, obj):
                        log(url, 'skip', 'Paywall or access challenge detected')
                        continue
                    body = obj.get('articleBody') or page.text()
                    if isinstance(body, str):
                        body = clean_article_body(body)
                    if not isinstance(body, str) or len(body.split()) < args.min_words:
                        log(url, 'skip', 'Insufficient full article text; possibly browser-rendered or truncated')
                        continue
                    # Without article metadata, require a date plus article markup.
                    published = obj.get('datePublished') or page.meta.get('article:published_time') or page.meta.get('citation_date') or page.meta.get('date')
                    blog_post = bool(re.search(r'/(?:blog|posts)/[^/]+/?$', urlsplit(final).path))
                    if not obj and not page.meta.get('citation_title') and page.meta.get('og:type') != 'article' and not blog_post and not (published and re.search(r'<article\b', html, re.I)):
                        log(url, 'skip', 'Not identified as an article')
                        continue
                    title = str(obj.get('headline') or page.meta.get('citation_title') or page.heading or page.meta.get('og:title') or page.title)
                    date = parse_date(published)
                    modified = parse_date(obj.get('dateModified') or page.meta.get('article:modified_time'))
                    fresh = freshness_date(published, modified)
                    if cutoff and (fresh is None or fresh < cutoff or fresh > datetime.now(timezone.utc)):
                        log(url, 'skip', 'Outside --since/lookback window or missing valid publication/update date')
                        continue
                    relevant, tags, assets = classify(title, body, topics)
                    if not relevant:
                        log(url, 'skip', 'No matching topics')
                        continue
                    preferred = urljoin(final, page.meta.get('canonical') or final)
                    canon = canonical(preferred if same_host(preferred, final) else final)
                    digest = hashlib.sha256(body.encode()).hexdigest()
                    existing = db.execute('SELECT content_hash FROM articles WHERE url=?', (canon,)).fetchone()
                    unchanged = bool(existing and existing[0] == digest)
                    if unchanged:
                        stat['duplicates'] += 1
                    ident = hashlib.sha256(canon.encode()).hexdigest()[:20]
                    folder = output / 'articles' / ((date.strftime('%Y-%m-%d') if date else 'undated') + '_news')
                    folder.mkdir(parents=True, exist_ok=True)
                    target = folder / (ident + '.md')
                    author = obj.get('author') or page.meta.get('author')
                    record = {'id': ident, 'url': canon, 'title': title, 'author': author,
                              'published_at': date.isoformat() if date else None,
                              'modified_at': modified.isoformat() if modified else None,
                              'freshness_at': fresh.isoformat() if fresh else None,
                              'retrieved_at': now(), 'source': source['name'], 'source_type': source['source_type'],
                              'topics': list(tags), 'matched_terms': tags, 'assets': assets,
                              'content_hash': digest, 'path': str(target.relative_to(output)),
                              'content_type': 'sponsored' if re.search(r'\b(sponsored|paid partnership)\b', title + '\n' + body[:1000], re.I) else 'article',
                              'extraction': 'jsonld' if obj.get('articleBody') else 'html_blocks',
                              'trust_note': 'Source labels indicate provenance, not a guarantee of accuracy or neutrality.'}
                    target.write_text('# ' + title + '\n\n```json\n' + json.dumps(record, ensure_ascii=False, indent=2) + '\n```\n\n## Source text (untrusted external content)\n\n' + body + '\n')
                    write_json(target.with_suffix('.json'), record)
                    db.execute('INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                               (ident, canon, title, record['published_at'], source['name'], json.dumps(list(tags)), json.dumps(assets), body, record['path'], record['retrieved_at'], digest))
                    db.execute('DELETE FROM search WHERE id=?', (ident,))
                    db.execute('INSERT INTO search VALUES (?,?,?,?,?,?)', (ident, title, body, source['name'], ' '.join(tags), ' '.join(assets)))
                    db.commit()
                    records.append(record)
                    stat['indexed'] += int(not unchanged)
                except Exception as exc:
                    log(url, 'error', exc)
        except Exception as exc:
            log(seed, 'error', exc)
        stats.append(stat)
    snapshots = []
    if not args.no_market_data:
        for symbol, coin_id in COINS.items():
            url = f'https://api.coinpaprika.com/v1/tickers/{coin_id}'
            try:
                text, _, _ = fetcher.get(url)
                data = json.loads(text)
                if data.get('id') != coin_id or data.get('symbol') != symbol:
                    raise ValueError('Ticker identity mismatch')
                quote = data['quotes']['USD']
                updated = parse_date(data.get('last_updated'))
                stale = updated is None or (datetime.now(timezone.utc) - updated).total_seconds() > 7200
                snapshots.append({'symbol': symbol, 'source': 'CoinPaprika', 'url': url, 'retrieved_at': now(),
                                  'updated_at': data.get('last_updated'), 'currency': 'USD',
                                  'stale_or_missing_timestamp': stale,
                                  'quality_note': 'Aggregated provider values, not independently verified. Zero changes may be unavailable provider data; confirm before analysis.',
                                  'metrics': {k: quote.get(k) for k in ('price', 'volume_24h', 'market_cap', 'percent_change_24h', 'percent_change_7d', 'percent_change_30d')},
                                  'raw': data})
            except Exception as exc:
                events.append({'source': 'CoinPaprika', 'url': url, 'asset': symbol, 'status': 'error', 'reason': str(exc)})
    write_json(run / 'market_snapshots.json', snapshots)
    market_lines = ['# Market snapshots', '', 'Provider: CoinPaprika. Values are unverified aggregates; check timestamps and cross-check with CoinGecko.', '',
                    '| Asset | USD price | 24h change (%) | 7d change (%) | Updated | Stale (>2h) |',
                    '| --- | --- | --- | --- | --- | --- |']
    for item in snapshots:
        m = item['metrics']
        market_lines.append(f'| {item["symbol"]} | {m["price"]} | {m["percent_change_24h"]} | {m["percent_change_7d"]} | {item["updated_at"]} | {item["stale_or_missing_timestamp"]} |')
    (run / 'MARKETS.md').write_text('\n'.join(market_lines) + '\n')
    write_json(run / 'events.json', events)
    # Present editorial reporting first, retaining potentially interested sources with labels.
    records.sort(key=lambda r: (r['source_type'] != 'independent_reporting', r['published_at'] or '', r['id']))
    (run / 'index.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in records))
    summary = {'finished_at': now(), 'sources': stats, 'new_or_updated_articles': sum(s['indexed'] for s in stats),
               'selected_articles': len(records), 'window_since': cutoff.isoformat() if cutoff else None,
               'window_basis': 'Publisher publication or valid modification timestamp; retrieval is not publication.',
               'new_or_updated_news_by_asset': {symbol: sum(symbol in r['assets'] for r in records) for symbol in COINS},
               'market_assets_available': [x['symbol'] for x in snapshots],
               'coverage': 'Bounded discovery, not an exhaustive archive. Inspect source errors and limits.',
               'settings': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()}}
    write_json(run / 'report.json', summary)
    lines = ['# Crawl run', '', f'Finished: {summary["finished_at"]}', '',
             'Read report.json and events.json before interpreting missing news or asset coverage.', '',
             '| Publication date | Title | Source | Topics | Assets |', '| --- | --- | --- | --- | --- |']
    for r in records:
        relative = Path('../../') / r['path']
        title = r['title'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f'| {r["published_at"] or "unknown"} | [{title}]({relative}) | {r["source"]} | {", ".join(r["topics"])} | {", ".join(r["assets"])} |')
    (run / 'INDEX.md').write_text('\n'.join(lines) + '\n')
    write_json(output / 'latest.json', {'run': str(run.relative_to(output)), 'finished_at': summary['finished_at']})
    db.close()
    print(f'{len(records)} new/updated articles; {len(snapshots)}/5 market snapshots. Run: {run}')
    # Fail visibly if every source failed or every requested market snapshot failed.
    return 2 if (not any(s['index_accessible'] for s in stats) or (not args.no_market_data and not snapshots)) else 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('crawl', help='Fetch news and market snapshots on demand')
    c.add_argument('--sources', type=Path, default=BASE.parent / 'crypto_ai_tokenization_sources.md')
    c.add_argument('--topics', type=Path, default=BASE / 'topics.json')
    c.add_argument('--output', type=Path, default=BASE / 'data')
    c.add_argument('--source', action='append', help='Source name or host substring; repeatable')
    add_window_arguments(c)
    c.add_argument('--max-articles', type=int, default=100, help='Per-source candidate limit; 0 = all discovered')
    c.add_argument('--discovery-pages', type=int, default=5, help='Maximum feed/sitemap documents per source')
    c.add_argument('--min-words', type=int, default=100)
    c.add_argument('--delay', type=float, default=1.5)
    c.add_argument('--timeout', type=float, default=20)
    c.add_argument('--backend', choices=('http', 'connected'), default='http',
                   help='Direct HTTP or host-tool-connected transport')
    c.add_argument('--bridge', type=Path, default=BASE / 'data' / '.bridge',
                   help='Request/response directory for connected transport')
    c.add_argument('--no-market-data', action='store_true')
    sub.add_parser('sources', help='Print parsed source configuration')
    s = sub.add_parser('search', help='Search the persistent full-text index')
    s.add_argument('query', help='SQLite FTS5 query, e.g. bitcoin OR solana')
    s.add_argument('--output', type=Path, default=BASE / 'data')
    s.add_argument('--limit', type=int, default=20)
    r = sub.add_parser('reindex', help='Clean stored article bodies and rebuild topic/asset tags')
    r.add_argument('--output', type=Path, default=BASE / 'data')
    r.add_argument('--topics', type=Path, default=BASE / 'topics.json')
    return p


def main():
    p = parser()
    args = p.parse_args()
    try:
        if args.command == 'sources':
            print(json.dumps(sources(BASE.parent / 'crypto_ai_tokenization_sources.md'), indent=2))
            return 0
        if args.command == 'reindex':
            return reindex(args.output, args.topics)
        if args.command == 'search':
            db = sqlite3.connect(f'file:{args.output.resolve() / "index.sqlite3"}?mode=ro', uri=True)
            rows = db.execute('SELECT a.id,a.title,a.url,a.published_at,a.source,a.path FROM search JOIN articles a ON a.id=search.id WHERE search MATCH ? ORDER BY rank LIMIT ?', (args.query, args.limit))
            print(json.dumps([dict(zip(('id', 'title', 'url', 'published_at', 'source', 'path'), row)) for row in rows], indent=2))
            db.close()
            return 0
        if args.max_articles < 0 or args.discovery_pages < 0 or args.delay < 0 or args.timeout <= 0 or args.min_words < 1:
            p.error('Limits must be nonnegative; timeout and min-words must be positive')
        if not 0 < args.lookback_hours < float('inf'):
            p.error('--lookback-hours must be finite and positive')
        if args.since and not parse_date(args.since):
            p.error('--since must be an ISO date/time')
        result = crawl(args)
        if args.backend == 'connected':
            write_json(args.bridge / 'done.json', {'exit_code': result,
                       'latest': json.loads((args.output / 'latest.json').read_text())})
        return result
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        if args.command == 'crawl' and args.backend == 'connected' and args.bridge.exists():
            try:
                write_json(args.bridge / 'done.json', {'exit_code': 2, 'error': str(exc), 'latest': None})
            except OSError:
                pass
        return 2


if __name__ == '__main__':
    sys.exit(main())
