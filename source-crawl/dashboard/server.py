"""Local read-only HTTP dashboard. Updates are committed with research.py."""
import argparse
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE=Path(__file__).resolve().parent

class Handler(SimpleHTTPRequestHandler):
    store=BASE.parent/'data/dashboard'
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(BASE/'web'),**kwargs)
    def json_response(self,value,status=200):
        payload=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        path=urlparse(self.path).path
        try:
            if path=='/api/history':
                rows=[]
                for p in sorted((self.store/'snapshots').glob('*.json'),reverse=True):
                    s=json.loads(p.read_text())
                    rows.append({k:s[k] for k in ('id','created_at','previous_id','review','delta')})
                    rows[-1]['articles']=len(s['articles'])
                    rows[-1]['summary']=s['analysis']['summary']
                return self.json_response(rows)
            if path=='/api/latest':
                pointer=json.loads((self.store/'latest.json').read_text())
                return self.json_response(json.loads((self.store/'snapshots'/f"{pointer['id']}.json").read_text()))
            match=re.fullmatch(r'/api/snapshots/([A-Za-z0-9-]+)',path)
            if match:
                return self.json_response(json.loads((self.store/'snapshots'/f'{match[1]}.json').read_text()))
            match=re.fullmatch(r'/api/evidence/([a-f0-9]{64})',path)
            if match:
                return self.json_response(json.loads((self.store/'evidence'/f'{match[1]}.json').read_text()))
            if path.startswith('/api/'):
                return self.json_response({'error':'Unknown route'},404)
            if path not in ('/','/index.html','/app.js','/style.css'):
                self.send_error(404);return
            return super().do_GET()
        except FileNotFoundError:
            return self.json_response({'error':'Snapshot or evidence not found. Capture the baseline first.'},404)
    def do_POST(self):
        self.json_response({'error':'Read-only preview. Commit updates through the daily research workflow.'},405)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--store',type=Path,default=Handler.store)
    args=parser.parse_args()
    Handler.store=args.store.resolve()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    print(f'Research dashboard: http://{args.host}:{args.port}',flush=True)
    server.serve_forever()
