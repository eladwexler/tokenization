import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.robotparser import RobotFileParser
import crawl


class Fixtures:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url not in self.pages:
            raise RuntimeError('fixture URL missing')
        return self.pages[url], 'text/html', url

    def rules(self, url):
        rp = RobotFileParser()
        rp.parse([])
        return rp


def article(title, body, date='2026-10-08T10:00:00Z', free=True):
    metadata = {'@type': 'NewsArticle', 'headline': title, 'articleBody': body,
                'datePublished': date, 'isAccessibleForFree': free}
    return '<html><title>' + title + '</title><script type="application/ld+json">' + json.dumps(metadata) + '</script><article><p>' + body + '</p></article></html>'


class Tests(unittest.TestCase):
    def test_connected_transport_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            ident = 'test-request'
            response = directory / (ident + '.response.json')
            response.write_text(json.dumps({'status': 200, 'url': 'https://example.com/news',
                                            'body': '<article>Bitcoin</article>', 'content_type': 'text/html'}))
            fetcher = crawl.ConnectorFetcher(directory, delay=0)
            with patch.object(crawl.uuid, 'uuid4', return_value=SimpleNamespace(hex=ident)):
                self.assertEqual(fetcher.raw('https://example.com/news'), ('<article>Bitcoin</article>', 'text/html'))
            self.assertEqual(list(directory.iterdir()), [])

    def test_connected_transport_http_error_redirect_and_service_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            ident = 'test-request'
            fetcher = crawl.ConnectorFetcher(directory, delay=0)
            cases = [({'status': 404}, HTTPError),
                     ({'status': 200, 'url': 'https://other.com/story'}, HTTPError),
                     ({'error': 'Rate limit exceeded'}, RuntimeError)]
            for payload, error in cases:
                (directory / (ident + '.response.json')).write_text(json.dumps(payload))
                with patch.object(crawl.uuid, 'uuid4', return_value=SimpleNamespace(hex=ident)):
                    with self.assertRaises(error):
                        fetcher.raw('https://example.com/news')
                self.assertEqual(list(directory.iterdir()), [])

    def test_connected_transport_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetcher = crawl.ConnectorFetcher(Path(tmp), delay=0, timeout=0)
            with self.assertRaises(TimeoutError):
                fetcher.raw('https://example.com/news')
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_sources(self):
        rows = crawl.sources(crawl.BASE.parent / 'crypto_ai_tokenization_sources.md')
        self.assertGreaterEqual(len(rows), 28)
        self.assertEqual(len({s['url'] for s in rows}), len(rows))
        self.assertEqual(next(s for s in rows if s['name'].startswith('NEAR'))['source_type'], 'project_or_vendor')

    def test_topic_boundaries_and_assets(self):
        topics = json.loads((crawl.BASE / 'topics.json').read_text())
        self.assertFalse(crawl.classify('An ordinary newsletter', 'Email and plain links.', topics)[0])
        for name, symbol in [('Bitcoin', 'BTC'), ('Solana', 'SOL'), ('Chainlink', 'LINK'), ('Ethereum', 'ETH'), ('Zcash', 'ZEC')]:
            self.assertIn(symbol, crawl.classify(name + ' trends', 'Market liquidity.', topics)[2])
        tags = crawl.classify('AI agents use stablecoins', 'Blockchain payments.', topics)[1]
        self.assertIn('ai_crypto_intersection', tags)

    def test_jsonld_and_navigation(self):
        p = crawl.Page('<nav><p>Unrelated advertisement</p></nav>' + article('BTC flows', 'Bitcoin flows increase.'))
        self.assertEqual(p.article()['headline'], 'BTC flows')
        self.assertNotIn('advertisement', p.text())

    def test_article_body_excludes_related_assets(self):
        p = crawl.Page('<main><article><p>Bitcoin lending grows.</p></article><section><h2>Zcash headlines</h2><p>Ethereum news.</p></section></main>')
        self.assertEqual(p.text(), 'Bitcoin lending grows.')
        p = crawl.Page('<main><p>Bitcoin lending grows.</p><div class="related-stories"><p>Zcash headlines</p></div></main>')
        self.assertEqual(p.text(), 'Bitcoin lending grows.')
        body = 'Bitcoin lending grows.\n\n1Zcash headline1 hour ago\n\n2Ethereum headline2 hours ago\n\n3Chainlink headline3 hours ago'
        self.assertEqual(crawl.clean_article_body(body), 'Bitcoin lending grows.')

    def test_embedded_metadata_cannot_replace_publisher_metadata(self):
        page = crawl.Page('<head><title>Publisher article</title><meta property="og:type" content="article"><link rel="canonical" href="https://example.com/article"></head><body><svg><title>Icon</title></svg><meta property="og:type" content="website"><link rel="canonical" href="https://video.example.com/"></body>')
        self.assertEqual(page.title, 'Publisher article')
        self.assertEqual(page.meta['og:type'], 'article')
        self.assertEqual(page.meta['canonical'], 'https://example.com/article')

    def test_paywall(self):
        p = crawl.Page(article('Bitcoin', 'Bitcoin story', free=False))
        self.assertTrue(crawl.access_blocked(p, p.article()))

    def test_canonical_dates_feeds(self):
        self.assertTrue(crawl.same_host('https://blog.chain.link/', 'https://chain.link/blog/news'))
        self.assertTrue(crawl.same_host('https://centrifuge.io/', 'https://www.centrifuge.io/blog/news'))
        self.assertFalse(crawl.same_host('https://blog.chain.link/', 'https://attacker.chain.link/'))
        self.assertFalse(crawl.same_host('https://www.coindesk.com/', 'https://coindesk.com.evil.example/'))
        self.assertEqual(crawl.canonical('https://A.com/news?utm_source=x&id=1#section'), 'https://a.com/news?id=1')
        self.assertEqual(crawl.parse_date('2026-10-08T02:00:00+02:00').hour, 0)
        self.assertIsNone(crawl.parse_date('invalid'))
        links, kind = crawl.xml_links('<feed xmlns="http://www.w3.org/2005/Atom"><entry><link href="/article-one"/></entry></feed>', 'https://example.com/feed')
        self.assertEqual(links, ['https://example.com/article-one'])
        self.assertEqual(kind, 'feed')

    def test_robots_and_redirect_target(self):
        f = crawl.Fetcher(delay=0)
        allowed = RobotFileParser(); allowed.parse(['User-agent: *', 'Disallow: /blocked'])
        with patch.object(f, 'rules', return_value=allowed), patch.object(f, 'raw') as raw:
            with self.assertRaises(PermissionError):
                f.get('https://example.com/blocked')
            raw.assert_not_called()
        denied = RobotFileParser(); denied.parse(['User-agent: *', 'Disallow: /'])
        redirect = HTTPError('https://example.com/start', 302, 'redirect', {'Location': 'https://other.com/article'}, None)
        with patch.object(f, 'rules', side_effect=[allowed, denied]), patch.object(f, 'raw', side_effect=redirect):
            with self.assertRaises(PermissionError):
                f.get('https://example.com/start')

    def test_robots_failure_closed_and_missing(self):
        f = crawl.Fetcher(delay=0)
        with patch.object(f, 'raw', side_effect=HTTPError('x', 503, 'failed', {}, None)):
            with self.assertRaises(RuntimeError):
                f.rules('https://example.com/news')
        with patch.object(f, 'raw', side_effect=HTTPError('x', 404, 'missing', {}, None)):
            self.assertTrue(f.rules('https://example.com/news').can_fetch(crawl.AGENT, 'https://example.com/news'))

    def test_robots_redirect(self):
        f = crawl.Fetcher(delay=0)
        redirect = HTTPError('https://blog.chain.link/robots.txt', 301, 'redirect',
                             {'Location': 'https://chain.link/robots.txt'}, None)
        with patch.object(f, 'raw', side_effect=[redirect, ('User-agent: *\nDisallow: /private/', 'text/plain')]) as raw:
            rules = f.rules('https://blog.chain.link/news')
            self.assertFalse(rules.can_fetch(crawl.AGENT, 'https://blog.chain.link/private/news'))
            self.assertEqual(raw.call_args_list[1].args[0], 'https://chain.link/robots.txt')

    def test_pipeline_dedup_update_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_file = root / 'sources.md'
            source_file.write_text('## Test\n| [Fixture](https://example.com/news) | News | Public articles. |\n')
            seed = 'https://example.com/news'
            url = 'https://example.com/bitcoin-trend'
            pages = {seed: '<a href="/bitcoin-trend">BTC</a><a href="/paywalled-news">Premium</a><a href="/ordinary-story">Other</a>',
                     url: article('Bitcoin and Solana market trends', ('Bitcoin Solana market liquidity rises. ' * 40)),
                     'https://example.com/paywalled-news': article('Bitcoin premium', 'bitcoin ' * 150, free=False),
                     'https://example.com/ordinary-story': article('Cooking ideas', 'Vegetables and cooking recipes. ' * 40)}
            args = crawl.parser().parse_args(['crawl', '--sources', str(source_file), '--output', str(root / 'data'), '--no-market-data', '--all-history'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawl.crawl(args, Fixtures(pages)), 0)
            data = root / 'data'
            self.assertEqual(len(list((data / 'articles' / '2026-10-08_news').glob('*.md'))), 1)
            db = sqlite3.connect(data / 'index.sqlite3')
            self.assertEqual(db.execute("SELECT count(*) FROM search WHERE search MATCH 'Solana'").fetchone()[0], 1)
            db.close()
            with contextlib.redirect_stdout(io.StringIO()):
                crawl.crawl(args, Fixtures(pages))
            latest = json.loads((data / 'latest.json').read_text())
            report = json.loads((data / latest['run'] / 'report.json').read_text())
            self.assertEqual(report['new_or_updated_articles'], 0)
            pages[url] = article('Bitcoin and Solana market trends', 'Bitcoin Solana new developments. ' * 40)
            with contextlib.redirect_stdout(io.StringIO()):
                crawl.crawl(args, Fixtures(pages))
            db = sqlite3.connect(data / 'index.sqlite3')
            self.assertEqual(db.execute('SELECT count(*) FROM articles').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM search').fetchone()[0], 1)
            db.execute('UPDATE articles SET body=body || ?', ('\n\n1Zcash headline1 hour ago\n\n2Ethereum headline2 hours ago\n\n3Chainlink headline3 hours ago',))
            db.commit()
            db.close()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawl.reindex(data, crawl.BASE / 'topics.json'), 0)
            db = sqlite3.connect(data / 'index.sqlite3')
            self.assertNotIn('Zcash headline', db.execute('SELECT body FROM articles').fetchone()[0])
            self.assertEqual(db.execute("SELECT count(*) FROM search WHERE search MATCH 'Zcash'").fetchone()[0], 0)
            db.close()
            import bundle as corpus_bundle
            with contextlib.redirect_stdout(io.StringIO()):
                combined = corpus_bundle.bundle(data, source_file, all_history=True)
            report = json.loads((combined / 'report.json').read_text())
            self.assertEqual(report['indexed_articles'], 1)
            self.assertEqual(report['sources_attempted'], 1)
            self.assertEqual(json.loads((data / 'latest.json').read_text())['run'], str(combined.relative_to(data)))
            self.assertIn('Bitcoin and Solana', (combined / 'INDEX.md').read_text())

    def test_default_two_hour_window_and_repeat_view(self):
        from datetime import datetime, timezone, timedelta
        anchor = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'sources.md'
            file.write_text('| [Fixture](https://example.com/news) | news | public |\n')
            pages = {'https://example.com/news': ''.join(
                f'<a href="/{name}-bitcoin-story">Story</a>' for name in ('recent', 'old', 'unknown', 'updated', 'future'))}
            dates = {'recent': anchor - timedelta(minutes=30), 'old': anchor - timedelta(hours=3),
                     'unknown': None, 'updated': anchor - timedelta(days=5), 'future': anchor + timedelta(days=1)}
            for name, date in dates.items():
                html = article('Bitcoin market news', 'Bitcoin market liquidity. ' * 120,
                               date=date.isoformat() if date else None)
                if name == 'updated':
                    html = html.replace('"isAccessibleForFree": true',
                                        '"dateModified": "' + (anchor - timedelta(minutes=20)).isoformat() + '", "isAccessibleForFree": true')
                pages[f'https://example.com/{name}-bitcoin-story'] = html
            args = crawl.parser().parse_args(['crawl', '--sources', str(file), '--output', str(root / 'data'), '--no-market-data'])
            self.assertEqual(args.lookback_hours, 2)
            for expected_changes in (2, 0):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(crawl.crawl(args, Fixtures(pages)), 0)
                run = json.loads((root / 'data/latest.json').read_text())['run']
                report = json.loads((root / 'data' / run / 'report.json').read_text())
                self.assertEqual(report['selected_articles'], 2)
                self.assertEqual(report['new_or_updated_articles'], expected_changes)
            import bundle as corpus_bundle
            with contextlib.redirect_stdout(io.StringIO()):
                folder = corpus_bundle.bundle(root / 'data', file)
            self.assertEqual(json.loads((folder / 'report.json').read_text())['indexed_articles'], 2)

    def test_total_failure_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'sources.md'
            file.write_text('| [Failed](https://example.com/news) | news | public |\n')
            args = crawl.parser().parse_args(['crawl', '--sources', str(file), '--output', str(root / 'data'), '--no-market-data', '--all-history'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawl.crawl(args, Fixtures({})), 2)
            run = json.loads((root / 'data/latest.json').read_text())['run']
            self.assertTrue(json.loads((root / 'data' / run / 'events.json').read_text()))

    def test_market_snapshot_identity_and_staleness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'sources.md'
            file.write_text('| [Fixture](https://example.com/news) | news | public |\n')
            pages = {'https://example.com/news': '<html><p>No articles today.</p></html>'}
            for symbol, coin_id in crawl.COINS.items():
                pages[f'https://api.coinpaprika.com/v1/tickers/{coin_id}'] = json.dumps({
                    'id': coin_id, 'symbol': symbol, 'last_updated': '2020-01-01T00:00:00Z',
                    'quotes': {'USD': {'price': 10, 'percent_change_24h': 2, 'percent_change_7d': 4}}})
            args = crawl.parser().parse_args(['crawl', '--sources', str(file), '--output', str(root / 'data')])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawl.crawl(args, Fixtures(pages)), 0)
            run = json.loads((root / 'data/latest.json').read_text())['run']
            snapshots = json.loads((root / 'data' / run / 'market_snapshots.json').read_text())
            self.assertEqual({s['symbol'] for s in snapshots}, set(crawl.COINS))
            self.assertTrue(all(s['stale_or_missing_timestamp'] for s in snapshots))
            pages['https://api.coinpaprika.com/v1/tickers/btc-bitcoin'] = json.dumps({'id': 'fake', 'symbol': 'BTC'})
            with contextlib.redirect_stdout(io.StringIO()):
                crawl.crawl(args, Fixtures(pages))
            run = json.loads((root / 'data/latest.json').read_text())['run']
            snapshots = json.loads((root / 'data' / run / 'market_snapshots.json').read_text())
            self.assertNotIn('BTC', [s['symbol'] for s in snapshots])

    def test_since_excludes_old_and_unknown_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'sources.md'
            file.write_text('| [Fixture](https://example.com/news) | news | public |\n')
            pages = {'https://example.com/news': '<a href="/old-bitcoin-story">Old</a><a href="/undated-bitcoin-story">Undated</a>',
                     'https://example.com/old-bitcoin-story': article('Bitcoin', 'Bitcoin ' * 120, date='2020-01-01'),
                     'https://example.com/undated-bitcoin-story': article('Bitcoin', 'Bitcoin ' * 120, date=None)}
            args = crawl.parser().parse_args(['crawl', '--sources', str(file), '--output', str(root / 'data'), '--since', '2026-10-01', '--no-market-data'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawl.crawl(args, Fixtures(pages)), 0)
            run = json.loads((root / 'data/latest.json').read_text())['run']
            events = json.loads((root / 'data' / run / 'events.json').read_text())
            self.assertEqual(len(events), 2)
            self.assertTrue(all('Outside --since' in e['reason'] for e in events))

    def test_public_blog_post_without_article_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / 'sources.md'
            file.write_text('| [Project](https://example.com/blog) | news | Public project source. |\n')
            url = 'https://example.com/blog/chainlink-agents'
            pages = {'https://example.com/blog': '<a href="/blog/chainlink-agents">Article</a>',
                     url: '<html><head><title>Generic site title</title><meta property="og:type" content="website"></head><main><h1>Chainlink AI agents</h1><p>' + 'Chainlink AI blockchain infrastructure. ' * 40 + '</p></main></html>'}
            args = crawl.parser().parse_args(['crawl', '--sources', str(file), '--output', str(root / 'data'), '--no-market-data', '--all-history'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(crawl.crawl(args, Fixtures(pages)), 0)
            records = list((root / 'data/articles/undated_news').glob('*.json'))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text())
            self.assertEqual(record['title'], 'Chainlink AI agents')
            self.assertIsNone(record['published_at'])
            self.assertIn('LINK', record['assets'])
            self.assertIn('ai_crypto_intersection', record['topics'])


if __name__ == '__main__':
    unittest.main()
