"""Build a self-contained dashboard for offline review or static HTTP hosting."""
import argparse
import json
from pathlib import Path
BASE = Path(__file__).resolve().parent

def export(store, destination):
    store=Path(store)
    snapshots={}
    history=[]
    for p in sorted((store/'snapshots').glob('*.json'),reverse=True):
        s=json.loads(p.read_text());snapshots[s['id']]=s
        history.append({k:s[k] for k in ('id','created_at','previous_id','review','delta')})
        history[-1].update(articles=len(s['articles']),summary=s['analysis']['summary'])
    latest=json.loads((store/'latest.json').read_text())['id']
    revisions={a['revision'] for s in snapshots.values() for a in s['articles']}
    evidence={r:json.loads((store/'evidence'/f'{r}.json').read_text()) for r in revisions}
    data=json.dumps({'latest':latest,'history':history,'snapshots':snapshots,'evidence':evidence},ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    html=(BASE/'web/index.html').read_text()
    html=html.replace('<link rel="stylesheet" href="/style.css">','<style>'+(BASE/'web/style.css').read_text()+'</style>')
    html=html.replace('<script defer src="/app.js"></script>', '<script>window.RESEARCH_BUNDLE='+data+';</script><script defer>'+ (BASE/'web/app.js').read_text()+'</script>')
    # Inline scripts execute as parsed despite defer; place the app after the DOM.
    marker='<script defer>'+ (BASE/'web/app.js').read_text()+'</script>'
    html=html.replace(marker,'').replace('</body>',marker.replace('<script defer>','<script>')+'</body>')
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(html)
    return {'path':str(destination),'snapshots':len(snapshots),'evidence_revisions':len(evidence),'bytes':destination.stat().st_size}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store',type=Path,default=BASE.parent/'data/dashboard')
    p.add_argument('--output',type=Path,default=BASE/'dist/index.html')
    args=p.parse_args();print(json.dumps(export(args.store,args.output)))
