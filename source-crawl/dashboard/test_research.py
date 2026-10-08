import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from dashboard.research import capture, read
from dashboard.server import Handler
from dashboard.export import export

class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name);self.data=self.root/'data';self.store=self.root/'store'
        (self.data/'articles').mkdir(parents=True);(self.data/'runs/a').mkdir(parents=True)
        self.write(self.data/'latest.json',{'run':'runs/a','finished_at':'2026-10-08T07:00:00Z'})
        self.write(self.data/'runs/a/report.json',{'sources':[{'source':'Example','index_accessible':True}],'coverage':'Bounded fixture'})
        self.markets=[{'symbol':'BTC','source':'Example','updated_at':'2026-10-08T07:00:00Z','metrics':{'price':80000}}]
        self.write(self.data/'runs/a/market_snapshots.json',self.markets)
        self.article('a')
        self.analysis={'summary':'Testable thesis','reviewed_at':'2026-10-08T08:00:00Z','author':'Test analyst','trends':[{'id':'t','title':'Thesis','status':'mixed','confidence':'medium','confidence_reason':'Limited evidence','thesis':'Some adoption','invalidation':'Failed retention','assets':['BTC'],'evidence':[{'article_id':'a','stance':'supports','note':'Observation'}]}],
                       'forecasts':[{'id':'btc-2030','symbol':'BTC','name':'Bitcoin','kind':'coin','currency':'USD','horizon':'2030-12-31','bear':[1,2],'base':[3,4],'bull':[5,6],'method':'Scenario','assumptions':'Demand','invalidation':'No demand','sources':['https://example.com']}],
                       'limitations':[],'article_summaries':{}}
        self.input=self.root/'analysis.json';self.write(self.input,self.analysis)
    def tearDown(self):self.temp.cleanup()
    def write(self,path,obj):path.write_text(json.dumps(obj))
    def article(self,id,published='2026-10-08T06:00:00Z',text='Source body'):
        self.write(self.data/f'articles/{id}.json',{'id':id,'title':f'Article {id}','url':'https://example.com/'+id,'source':'Example','source_type':'independent_reporting','published_at':published,'retrieved_at':'2026-10-08T07:00:00Z','topics':['crypto']})
        (self.data/f'articles/{id}.md').write_text(text)
    def baseline(self):return capture(self.data,self.store,self.input,'2026-10-08T09:00:00Z')
    def latest(self):return read(self.store/'snapshots'/f"{read(self.store/'latest.json')['id']}.json")
    def test_unchanged_capture_is_noop(self):
        first=self.baseline();second=capture(self.data,self.store,at='2026-10-08T10:00:00Z')
        self.assertEqual(second,{'status':'unchanged','id':first['id']})
        self.assertEqual(len(list((self.store/'snapshots').glob('*.json'))),1)
    def test_source_revision_preserves_previous_body_and_flags_review(self):
        first=self.baseline();before=self.latest();self.article('a',text='Changed claim')
        capture(self.data,self.store,at='2026-10-09T09:00:00Z');after=self.latest()
        self.assertEqual(after['previous_id'],first['id']);self.assertEqual(after['delta']['revised_articles'],['a'])
        self.assertEqual(after['review']['status'],'pending');self.assertEqual(after['analysis'],before['analysis'])
        self.assertEqual(read(self.store/'evidence'/f"{before['articles'][0]['revision']}.json")['body'],'Source body')
        self.assertEqual(read(self.store/'snapshots'/f"{first['id']}.json"),before)
    def test_new_old_dated_article_still_requires_review(self):
        self.baseline();self.article('b',published='2025-02-01T00:00:00Z')
        capture(self.data,self.store,at='2026-10-09T09:00:00Z')
        self.assertEqual(self.latest()['review']['status'],'pending')
        self.assertEqual(self.latest()['review']['unlinked_new_ids'],['b'])
    def test_pending_review_survives_market_only_update(self):
        self.baseline();self.article('b');capture(self.data,self.store,at='2026-10-09T09:00:00Z')
        self.markets[0]['metrics']['price']=82000;self.write(self.data/'runs/a/market_snapshots.json',self.markets)
        capture(self.data,self.store,at='2026-10-10T09:00:00Z')
        self.assertEqual(self.latest()['review']['status'],'pending')
        self.assertAlmostEqual(self.latest()['delta']['prices'][0]['percent'],2.5)
        self.assertEqual(self.latest()['analysis']['forecasts'][0]['base'],[3,4])
    def test_explicit_review_tracks_thesis_and_forecast_changes(self):
        self.baseline();self.analysis['reviewed_at']='2026-10-09T08:00:00Z'
        self.analysis['trends'][0]['status']='strengthening';self.analysis['forecasts'][0]['base']=[3.5,4.5]
        self.write(self.input,self.analysis);capture(self.data,self.store,self.input,'2026-10-09T09:00:00Z')
        s=self.latest();self.assertEqual(s['review']['status'],'reviewed')
        self.assertEqual(s['delta']['trends'][0]['before']['status'],'mixed')
        self.assertEqual(s['delta']['forecasts'][0]['before']['base'],[3,4])
    def test_stale_review_cannot_accept_new_evidence(self):
        self.baseline();original=read(self.store/'latest.json');self.article('b')
        with self.assertRaises(ValueError):capture(self.data,self.store,self.input,'2026-10-09T09:00:00Z')
        self.assertEqual(read(self.store/'latest.json'),original)
    def test_invalid_forecast_and_links_leave_latest_unchanged(self):
        self.baseline();original=read(self.store/'latest.json')
        for mutate in [lambda a:a['forecasts'][0].update(base=[8,4]),lambda a:a['trends'][0]['evidence'][0].update(article_id='missing'),lambda a:a['forecasts'][0].update(base=[float('nan'),4])]:
            bad=copy.deepcopy(self.analysis);mutate(bad);self.write(self.input,bad)
            with self.assertRaises(ValueError):capture(self.data,self.store,self.input,'2026-10-09T09:00:00Z')
            self.assertEqual(read(self.store/'latest.json'),original)
    def test_date_boundaries_and_future_retrieval_are_excluded(self):
        self.article('old',published='2024-12-31T23:59:59Z');self.article('unknown',published=None)
        self.article('future',published='2026-10-10T00:00:00Z');self.article('backfill')
        m=read(self.data/'articles/backfill.json');m['retrieved_at']='2026-10-11T00:00:00Z';self.write(self.data/'articles/backfill.json',m)
        self.baseline();s=self.latest()
        self.assertEqual([a['id'] for a in s['articles']],['a']);self.assertEqual(s['coverage']['excluded'],{'undated':1,'outside_window':3})
    def test_writer_lock_blocks_second_capture(self):
        self.store.mkdir();(self.store/'.capture.lock').write_text('')
        with self.assertRaisesRegex(ValueError,'Another capture'):self.baseline()
    def test_self_contained_export_escapes_script_content(self):
        self.article('a',text='</script><script>alert(1)</script>');self.baseline()
        destination=self.root/'index.html';result=export(self.store,destination);s=destination.read_text()
        self.assertEqual(result['snapshots'],1);self.assertNotIn('src="/app.js"',s)
        self.assertNotIn('</script><script>alert(1)',s);self.assertIn('\\u003c/script',s)
    def request(self,path):
        class Connection:
            def __init__(self):self.response=bytearray()
            def makefile(self,*args):return io.BytesIO(f'GET {path} HTTP/1.0\r\n\r\n'.encode())
            def sendall(self,data):self.response.extend(data)
        c=Connection();old=Handler.store;Handler.store=self.store
        try:Handler(c,('127.0.0.1',1234),SimpleNamespace(server_name='test',server_port=8765))
        finally:Handler.store=old
        return bytes(c.response)
    def test_http_read_routes_and_path_guards_without_network(self):
        self.baseline()
        for route in ['/','/app.js','/style.css','/api/latest','/api/history','/api/snapshots/'+self.latest()['id'],'/api/evidence/'+self.latest()['articles'][0]['revision']]:
            with self.subTest(route=route):self.assertIn(b'200 OK',self.request(route).split(b'\r\n')[0])
        for route in ['/api/unknown','/api/snapshots/../../latest','/../research.py']:
            with self.subTest(route=route):self.assertIn(b'404',self.request(route).split(b'\r\n')[0])

if __name__=='__main__':unittest.main()
