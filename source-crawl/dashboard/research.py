"""Append-only evidence and research snapshots; no external dependencies."""
import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(Path(path).read_text())

def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def utc(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamps must include timezone')
    return dt.astimezone(timezone.utc)

def save_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    try:
        with path.open('x') as f:
            f.write(payload)
    except FileExistsError:
        if read(path) != value:
            raise ValueError(f'Immutable artifact collision: {path}')

def changes(old, new, key):
    a = {x['id']: x for x in (old or {}).get(key, [])}
    b = {x['id']: x for x in new.get(key, [])}
    return [{'id': k, 'before': a.get(k), 'after': b.get(k)}
            for k in sorted(a.keys() | b.keys()) if a.get(k) != b.get(k)]

def validate_analysis(analysis, articles, cutoff):
    ids = {a['id'] for a in articles}
    for key in ('author','reviewed_at'):
        if not isinstance(analysis.get(key),str) or not analysis[key].strip():
            raise ValueError(f'Analysis requires {key}')
    for key in ('trends','forecasts'):
        if not isinstance(analysis.get(key),list):
            raise ValueError(f'Analysis requires a {key} list')
    if not isinstance(analysis.get('summary'), str) or not analysis['summary'].strip():
        raise ValueError('Analysis requires a summary')
    if utc(analysis['reviewed_at']) > cutoff:
        raise ValueError('Analysis review is after snapshot cutoff')
    for key in ('trends', 'forecasts'):
        items = analysis.get(key, [])
        keys = [x['id'] for x in items]
        if len(keys) != len(set(keys)):
            raise ValueError(f'Duplicate {key} IDs')
    for t in analysis['trends']:
        for key in ('id','title','thesis','confidence_reason','invalidation'):
            if not isinstance(t.get(key),str) or not t[key].strip():
                raise ValueError(f'Trend requires {key}')
        if not isinstance(t.get('assets'),list):
            raise ValueError('Trend needs named asset list')
        if t['confidence'] not in ('high', 'medium', 'low'):
            raise ValueError('Confidence must be high, medium or low')
        if t['status'] not in ('strengthening', 'mixed', 'weakening', 'early'):
            raise ValueError('Invalid trend status')
        if not t.get('invalidation') or not t.get('confidence_reason'):
            raise ValueError('Trend needs confidence reasoning and invalidation')
        if not t.get('evidence'):
            raise ValueError('Trend needs linked evidence')
        for e in t['evidence']:
            if e['article_id'] not in ids or e['stance'] not in ('supports','challenges','context'):
                raise ValueError('Unknown article or evidence stance')
    for f in analysis['forecasts']:
        for key in ('id','symbol','name','method','assumptions','invalidation'):
            if not isinstance(f.get(key),str) or not f[key].strip():
                raise ValueError(f'Forecast requires {key}')
        if f.get('currency') != 'USD' or f.get('kind') not in ('stock','coin'):
            raise ValueError('This dashboard supports USD stock/coin scenarios')
        if not all(isinstance(u,str) and u.startswith(('https://','http://')) for u in f['sources']):
            raise ValueError('Forecast sources must be HTTP URLs')
        if f.get('reference_quote') and utc(f['reference_quote']['updated_at']) > cutoff:
            raise ValueError('Reference quote is after snapshot cutoff')
        if not f.get('assumptions') or not f.get('method') or not f.get('sources'):
            raise ValueError('Forecast requires method, assumptions and sources')
        if datetime.fromisoformat(f['horizon']).date() <= cutoff.date():
            raise ValueError('Forecast horizon must follow snapshot')
        last = -1
        for scenario in ('bear','base','bull'):
            lo, hi = f[scenario]
            if any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in (lo,hi)):
                raise ValueError('Forecast values must be finite numbers')
            if lo < 0 or hi < lo or lo < last:
                raise ValueError('Invalid forecast ordering')
            last = hi

def capture(data_dir, store, analysis_file=None, at=None):
    data_dir, store = Path(data_dir), Path(store)
    cutoff = utc(at) if at else datetime.now(timezone.utc)
    store.mkdir(parents=True, exist_ok=True)
    lock = store / '.capture.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('Another capture is active; inspect before removing lock')
    try:
        os.close(fd)
        prior = read(store/'latest.json') if (store/'latest.json').exists() else None
        previous = read(store/'snapshots'/f"{prior['id']}.json") if prior else None
        pointer = read(data_dir/'latest.json')
        run = (data_dir/pointer['run']).resolve()
        if not run.is_relative_to(data_dir.resolve()):
            raise ValueError('Run pointer escapes data directory')
        report = read(run/'report.json')
        markets = read(run/'market_snapshots.json')
        articles, excluded = [], {'undated':0,'outside_window':0}
        for path in sorted((data_dir/'articles').rglob('*.json')):
            meta = read(path)
            published = meta.get('published_at')
            if not published:
                excluded['undated'] += 1
                continue
            try:
                stamp = utc(published)
            except (ValueError, TypeError):
                excluded['undated'] += 1
                continue
            retrieved = meta.get('retrieved_at')
            if retrieved and utc(retrieved) > cutoff:
                excluded['outside_window'] += 1
                continue
            if stamp.year < 2025 or stamp > cutoff:
                excluded['outside_window'] += 1
                continue
            body = path.with_suffix('.md').read_text()
            revision = digest({'metadata':meta,'body':body})
            save_new(store/'evidence'/f'{revision}.json', {'metadata':meta,'body':body})
            articles.append({'id':meta['id'],'title':meta['title'],'url':meta['url'],
                             'source':meta['source'],'source_type':meta['source_type'],
                             'published_at':published,'modified_at':meta.get('modified_at'),
                             'topics':meta.get('topics',[]),'revision':revision})
        articles.sort(key=lambda a:(a['published_at'],a['id']), reverse=True)
        if analysis_file:
            analysis = read(analysis_file)
        elif previous:
            analysis = previous['analysis']
        else:
            raise ValueError('First capture requires --analysis')
        validate_analysis(analysis, articles, cutoff)
        old = {a['id']:a for a in (previous or {}).get('articles',[])}
        current = {a['id']:a for a in articles}
        added = [a['id'] for a in articles if a['id'] not in old]
        revised = [a['id'] for a in articles if a['id'] in old and a['revision'] != old[a['id']]['revision']]
        removed = sorted(old.keys()-current.keys())
        # Keep original provider times; freshness is computed at viewing time.
        market_clean = [{k:v for k,v in m.items() if k not in ('raw','stale_or_missing_timestamp')} for m in markets
                        if not m.get('updated_at') or utc(m['updated_at']) <= cutoff]
        coverage = {'sources':report.get('sources',[]),'scope':report.get('coverage',report.get('scope','')),
                    'excluded':excluded,'collection_finished_at':pointer.get('finished_at'), 'run':pointer['run']}
        payload = {'articles':articles,'markets':market_clean,'coverage':coverage,'analysis':analysis}
        fingerprint = digest(payload)
        if previous and previous['fingerprint'] == fingerprint:
            return {'status':'unchanged','id':previous['id']}
        changed = set(added+revised)
        affected = [t['id'] for t in analysis['trends'] if any(e['article_id'] in changed for e in t['evidence'])]
        if previous and analysis_file and (changed or removed) and utc(analysis['reviewed_at']) <= utc(previous['created_at']):
            raise ValueError('New evidence requires a fresh analyst review timestamp')
        pending = bool(previous and not analysis_file and ((changed or removed) or previous['review']['status'] == 'pending'))
        price_changes=[]
        oldmarkets={m['symbol']:m for m in (previous or {}).get('markets',[])}
        for m in market_clean:
            op=oldmarkets.get(m['symbol'],{}).get('metrics',{}).get('price')
            np=m.get('metrics',{}).get('price')
            if op and np is not None and np != op:
                price_changes.append({'symbol':m['symbol'],'before':op,'after':np,'percent':(np/op-1)*100})
        analysis_changed = previous is not None and previous['analysis'] != analysis
        alerts = [{'kind':'price_move','symbol':p['symbol'],'message':f"{p['symbol']} moved {p['percent']:+.1f}% between collected quotes; review assumptions, not just targets."}
                  for p in price_changes if abs(p['percent']) >= 5]
        if previous and previous['coverage']['sources'] != coverage['sources']:
            alerts.append({'kind':'coverage','message':'Source coverage changed; apparent topic shifts may reflect collection differences.'})
        snapshot_id = cutoff.strftime('%Y%m%dT%H%M%S%fZ')+'-'+fingerprint[:10]
        snapshot={'schema_version':1,'id':snapshot_id,'created_at':cutoff.isoformat(),
                  'previous_id':prior['id'] if prior else None,'fingerprint':fingerprint,**payload,
                  'review':{'status':'pending' if pending else 'reviewed','affected_trends':affected,
                            'reason':'New or revised evidence needs analyst review; prior opinions retained.' if pending else 'Analyst baseline or explicit review.',
                            'alerts':alerts,'unlinked_new_ids':[x for x in sorted(changed) if not any(e['article_id']==x for t in analysis['trends'] for e in t['evidence'])]},
                  'delta':{'baseline':previous is None,'new_articles':added,'revised_articles':revised,'removed_articles':removed,
                           'prices':price_changes,'trends':changes(previous.get('analysis') if previous else None,analysis,'trends') if previous else [],
                           'forecasts':changes(previous.get('analysis') if previous else None,analysis,'forecasts') if previous else [],
                           'analysis_changed':analysis_changed,'coverage_changed':bool(previous and previous['coverage']!=coverage)}}
        save_new(store/'snapshots'/f'{snapshot_id}.json',snapshot)
        temp=store/'latest.tmp'
        temp.write_text(json.dumps({'id':snapshot_id})+'\n')
        temp.replace(store/'latest.json')
        return {'status':'created','id':snapshot_id,'review':snapshot['review']['status'],'articles':len(articles)}
    finally:
        lock.unlink(missing_ok=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    parser.add_argument('--store',type=Path,default=ROOT/'data/dashboard')
    parser.add_argument('--analysis',type=Path)
    parser.add_argument('--at',help='UTC cutoff, primarily for reproducible capture tests')
    args=parser.parse_args()
    try:
        print(json.dumps(capture(args.data,args.store,args.analysis,args.at)))
    except (ValueError,KeyError,FileNotFoundError) as e:
        parser.exit(2,f'Capture failed: {e}\n')
