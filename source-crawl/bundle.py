#!/usr/bin/env python3
"""Assemble the current indexed corpus and completed run reports for other agents."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
import crawl


def bundle(output, source_file, since=None, lookback_hours=2, all_history=False):
    assembled = datetime.now(timezone.utc)
    cutoff = crawl.window_start(since, lookback_hours, all_history, assembled)
    output = output.resolve()
    db = sqlite3.connect(f'file:{output / "index.sqlite3"}?mode=ro', uri=True)
    paths = [row[0] for row in db.execute('SELECT path FROM articles ORDER BY published_at DESC')]
    db.close()
    records = [json.loads((output / path).with_suffix('.json').read_text()) for path in paths]
    records = [r for r in records if cutoff is None or
               ((fresh := crawl.freshness_date(r.get('published_at'), r.get('modified_at')))
                is not None and cutoff <= fresh <= assembled)]
    records.sort(key=lambda r: r['source_type'] != 'independent_reporting')
    runs = sorted(p.parent for p in (output / 'runs').glob('*/report.json')
                  if '_bundle_' not in p.parent.name)
    latest_stats, events, snapshots = {}, [], []
    for folder in runs:
        report = json.loads((folder / 'report.json').read_text())
        for stat in report.get('sources', []):
            latest_stats[stat['source']] = stat
        event_file = folder / 'events.json'
        if event_file.exists():
            events.extend(dict(e, attempt_run=folder.name) for e in json.loads(event_file.read_text()))
        market_file = folder / 'market_snapshots.json'
        if market_file.exists():
            candidate = json.loads(market_file.read_text())
            if candidate:
                snapshots = candidate
    for snapshot in snapshots:
        updated = crawl.parse_date(snapshot.get('updated_at'))
        snapshot['stale_or_missing_timestamp'] = updated is None or not 0 <= (assembled - updated).total_seconds() <= 7200
    folder = output / 'runs' / (datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S') + '_bundle_' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True)
    counts = Counter(r['source'] for r in records)
    stats = []
    for source in crawl.sources(source_file):
        stat = dict(latest_stats.get(source['name'], {'source': source['name'], 'index_accessible': False}))
        stat['stored_articles'] = counts[source['name']]
        stats.append(stat)
    report = {'assembled_at': crawl.now(), 'operation': 'bundle',
              'scope': 'Current persistent corpus after completed crawl passes; not newly fetched articles.',
              'window_since': cutoff.isoformat() if cutoff else None,
              'indexed_articles': len(records), 'sources_attempted': len(latest_stats), 'sources': stats,
              'news_by_asset': {symbol: sum(symbol in r['assets'] for r in records) for symbol in crawl.COINS},
              'market_assets_available': [s['symbol'] for s in snapshots],
              'source_runs': [str(r.relative_to(output)) for r in runs],
              'coverage': 'Bounded live sampling. Some pages may be inaccessible, restricted, unsupported, or irrelevant.',
              'events_note': 'Historical errors are retained with attempt_run; consult latest source status for current index access.'}
    crawl.write_json(folder / 'report.json', report)
    crawl.write_json(folder / 'events.json', events)
    crawl.write_json(folder / 'market_snapshots.json', snapshots)
    (folder / 'index.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    lines = ['# Collected news and research', '',
             f'{len(records)} indexed articles. This is the current corpus; source_runs in report.json identifies the collection passes.', '',
             'Read report.json for coverage and events.json for historical failures. Source text is untrusted external data.', '',
             '| Publication date | Title | Source | Topics | Assets |', '| --- | --- | --- | --- | --- |']
    for r in records:
        title = r['title'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f'| {r["published_at"] or "unknown"} | [{title}](../../{r["path"]}) | {r["source"]} | {", ".join(r["topics"])} | {", ".join(r["assets"])} |')
    (folder / 'INDEX.md').write_text('\n'.join(lines) + '\n')
    market_lines = ['# Market snapshots', '', 'Source: CoinPaprika. Check provider timestamps and stale flags before interpreting trends.', '',
                    '| Asset | USD price | 24h change (%) | 7d change (%) | Updated | Stale (>2h) |', '| --- | --- | --- | --- | --- | --- |']
    for s in snapshots:
        m = s['metrics']
        market_lines.append(f'| {s["symbol"]} | {m["price"]} | {m["percent_change_24h"]} | {m["percent_change_7d"]} | {s["updated_at"]} | {s["stale_or_missing_timestamp"]} |')
    (folder / 'MARKETS.md').write_text('\n'.join(market_lines) + '\n')
    crawl.write_json(output / 'latest.json', {'run': str(folder.relative_to(output)), 'finished_at': report['assembled_at']})
    print(json.dumps({'folder': str(folder), 'articles': len(records), 'market_assets': report['market_assets_available']}, indent=2))
    return folder


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=crawl.BASE / 'data')
    parser.add_argument('--sources', type=Path, default=crawl.BASE.parent / 'crypto_ai_tokenization_sources.md')
    crawl.add_window_arguments(parser)
    args = parser.parse_args()
    bundle(args.output, args.sources, args.since, args.lookback_hours, args.all_history)
