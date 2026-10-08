"""Import the conversation's research as an explicitly subjective baseline."""
import json
from pathlib import Path
from datetime import datetime, timezone
BASE=Path(__file__).resolve().parent

def evidence(article_id, stance, note):
    return {'article_id':article_id,'stance':stance,'note':note}

def trend(id,title,status,confidence,thesis,reason,invalidation,assets,ev):
    return dict(id=id,title=title,status=status,confidence=confidence,thesis=thesis,
                confidence_reason=reason,invalidation=invalidation,assets=assets,evidence=ev)
trends=[
trend('programmable-payments','Stablecoins enter business workflows','strengthening','high',
      'Existing enterprise distribution makes programmable payments easier to adopt. Customer pilots must become retained paid volume.',
      'Several concrete integrations support the direction; scale and margins remain uncertain.',
      'Pilot conversions stall, retained payment volume falls, or integration costs erase the savings.', ['COIN','CRCL','HOOD'],
      [evidence('9d0c3cf7cd5fb925ab26','supports','Circle–Tereina distribution into existing business applications; proof-of-value work remains ahead.'),evidence('7973a43e276d22b0a32a','context','BlackRock sees agent payments as nearer-term than tokenized compute.'),evidence('309a9793ba4010543ba2','context','Kazakhstan agreement is an exploration, not a live stablecoin.')]),
trend('tokenized-assets','Tokenization moves toward usable collateral','strengthening','medium',
      'Distribution, asset servicing and interoperable settlement can grow before governance tokens capture value.',
      'Reported utilization and infrastructure milestones are substantive, but much coverage comes from interested vendors.',
      'Net issuance and liquid depth fail to grow, or custody/redemption problems prevent repeat use.', ['LINK','SOL','ETH','ONDO'],
      [evidence('6058e09a6f18c3dd19f6','supports','Binance reports increased tokenized-equity value and collateral use; transfers are not unique economic activity.'),evidence('e00a114cb7fc5daa37dd','supports','Solana DvP standardizes atomic settlement; other investment risks remain.'),evidence('1fab798ccbb25a2c734d','challenges','Korean-equity MOU is conditional on future commercialization.'),evidence('c248788e089f7059c305','context','OKX–ICE planned market still faces liquidity and regulatory constraints.')]),
trend('agent-economics','Cheaper AI favors useful workflows','strengthening','high',
      'Lower task costs expand viable automation. Distribution and verified completed-task economics matter more than a generic chatbot.',
      'Price reductions are concrete; benchmarks and projected savings do not prove customer ROI.',
      'Retries, human review and errors consume savings, or customers fail to renew.', ['NVDA','NET','COIN'],
      [evidence('59efb43f5ff3947880c3','supports','Small-model pricing competition; account for tokenization and retries.'),evidence('3c96fdd2b5f5a05433fa','supports','Agents edit in existing productivity workflows.'),evidence('530fe825fff813b4a0d2','context','Contributed ServiceNow case study supports narrow workflows; savings are partly projected.')]),
trend('market-demand','Crypto recovery lacks durable cash demand','weakening','medium',
      'Structural adoption is expanding while thin spot participation and crowded leverage weaken near-term breakout quality.',
      'On-chain research and contemporaneous reporting agree on fragility; this does not forecast a four-year outcome.',
      'Sustained spot and ETF demand returns, US-session buying improves, and market breadth expands.', ['BTC','ETH','SOL','COIN','HOOD'],
      [evidence('ae0567678857dfcbfdd6','supports','Thin spot volume, limited measured fresh money and stretched altcoin leverage.'),evidence('438d6a1d44f059fa1890','supports','Latest BTC report breaks the earlier $83K range support.'),evidence('425c3208ea6349554f08','challenges','September ETF demand was positive; different observation windows matter.')]),
trend('treasury-buyers','Treasury accumulation faces limits','mixed','medium',
      'Corporate balance-sheet adoption can coexist with expensive financing and a declining marginal bid.',
      'Specific financing constraints and accumulation caps are visible; no forced sales are inferred.',
      'Financing improves and new buyers replace capped accumulation with sustained net purchases.', ['BTC','ETH','COIN'],
      [evidence('56337ebeede63f212524','supports','Bitmine intends to stop accumulating at its 5% cap; not an announcement of selling.'),evidence('71f3201f90f67694851d','supports','Strategy NAV premium and financing costs limit dependable buying.'),evidence('743458efb3d1d8503057','challenges','Robinhood reported BTC purchase shows adoption but limited flow significance.')]),
trend('security','Agent permissions become adoption infrastructure','early','medium',
      'Money-moving agents need identity, spending limits, auditability and operational safeguards.',
      'Current exploit and authorization risks are observable; speculative cryptography breakthroughs are not established.',
      'Customers will not pay, controls fail in production, or incumbents bundle equivalent protection.', ['NET','LINK','ETH'],
      [evidence('317a9dbccef30454ec8b','context','Voluntary safety audits do not establish enforceable protection.'),evidence('295fa53afc81b1fc8f33','supports','Phased wallet migration work illustrates security coordination needs.'),evidence('a14de9c2dfe22e508323','challenges','AI math claims require verification and do not demonstrate broken wallet signatures.')])]
quotes={'NVDA':237.47,'COIN':178.45,'CRCL':80.84,'HOOD':109.51,'NET':342.97}
names={'NVDA':'Nvidia','COIN':'Coinbase','CRCL':'Circle','HOOD':'Robinhood','NET':'Cloudflare','BTC':'Bitcoin','ETH':'Ethereum','SOL':'Solana','LINK':'Chainlink'}
ranges12={'NVDA':[[160,200],[280,330],[360,420]],'COIN':[[100,140],[200,240],[280,350]],'CRCL':[[40,60],[95,115],[140,180]],'HOOD':[[65,85],[120,145],[165,200]],'NET':[[200,260],[320,380],[430,500]],'LINK':[[7,10],[16,22],[28,38]],'SOL':[[55,80],[140,180],[220,280]],'ETH':[[1400,1900],[3000,3800],[4500,5500]],'BTC':[[50000,65000],[90000,110000],[125000,150000]]}
ranges30={'NVDA':[[150,220],[350,500],[650,850]],'COIN':[[80,150],[300,450],[600,850]],'CRCL':[[30,65],[120,200],[300,450]],'HOOD':[[50,90],[180,280],[400,550]],'NET':[[140,240],[300,500],[650,900]],'BTC':[[35000,70000],[140000,220000],[300000,450000]],'ETH':[[800,1800],[5000,8000],[12000,18000]],'SOL':[[25,70],[250,400],[600,900]],'LINK':[[4,9],[30,50],[80,120]]}
assumptions={'NVDA':'2030 base: EPS $14–20 × 25 P/E. Continued earnings growth; no large multiple expansion required.',
'COIN':'2030 base: net income $3–4.5B / 300M diluted shares × 30 P/E. Needs durable profit beyond trading booms.',
'CRCL':'2030 base: net income $1.2–2B / 300M diluted shares × 30 P/E. USDC growth must offset rate and distribution pressure.',
'HOOD':'2030 base: EPS $6–8 × 30–35 P/E. Requires monetization and retention without excessive dilution.',
'NET':'2030 base: revenue $8–10B / 400M diluted shares × 15–20 revenue multiple. Valuation remains demanding.',
'BTC':'2030 base implies $2.9–4.5T at illustrative 20.5M supply. Requires sustained institutional and investment demand.',
'ETH':'2030 base implies $600–960B at illustrative 120M supply. Supply depends on issuance and burning.',
'SOL':'2030 base implies $163–260B at illustrative 650M supply, not a verified supply projection.',
'LINK':'2030 base implies $30–50B fully diluted value at 1B total tokens. Paid service demand must translate to LINK purchases.'}
forecasts=[]
for horizon, ranges in [('2027-10-08',ranges12),('2030-12-31',ranges30)]:
 for symbol,scenarios in ranges.items():
  forecasts.append({'id':symbol+'-'+horizon,'symbol':symbol,'name':names[symbol],'kind':'stock' if symbol in quotes else 'coin','horizon':horizon,'currency':'USD',
    'bear':scenarios[0],'base':scenarios[1],'bull':scenarios[2],
    'method':'Subjective scenario; earnings/revenue sensitivity' if symbol in quotes and horizon.startswith('2030') else 'Subjective adoption and market-value scenario; not independently validated',
    'assumptions':assumptions[symbol] if horizon.startswith('2030') else 'Bear: macro pressure and compression. Base: adoption with moderate recovery. Bull: strong adoption plus investor demand. Judgmental ranges from prior conversation, not a complete fair-value model.',
    'sources':['https://stockanalysis.com/stocks/'+symbol.lower()+'/'] if symbol in quotes else ['https://api.coinpaprika.com/v1/tickers/'+{'BTC':'btc-bitcoin','ETH':'eth-ethereum','SOL':'sol-solana','LINK':'link-chainlink'}[symbol]],
    'invalidation':'Reassess when adoption, margins, supply/dilution, or valuation assumptions materially change.',
    'reference_quote':{'price':quotes[symbol],'updated_at':'2026-10-07T20:00:00Z','source':'Stock Analysis / October 7 close'} if symbol in quotes else None})
analysis={'version':'1.0','reviewed_at':datetime.now(timezone.utc).isoformat(),'author':'Codex research baseline, imported from this conversation',
'summary':'AI execution is cheaper and finance is more programmable. Infrastructure adoption is stronger than the evidence for token price appreciation; near-term crypto demand remains fragile.',
'limitations':['Snapshot is assembled from saved articles, not a fresh crawl.','Only one qualified article is from 2025; most evidence is September–October 2026.','Confidence is qualitative analyst judgment, not a calibrated probability.','Forecast scenarios are illustrative and need deeper financial modeling.','First snapshot is a real baseline, not a reconstructed daily history.'],
'non_news_ids':['4e6b4ba27d4eb5318dab','9dd6b6690677c19d1603','81b6ac23a9a54436fea7','6134e1eac7002dc1dec5'],
'article_summaries':{r['id']:r['summary'] for r in json.loads((BASE.parent/'data/analysis/evidence-ledger-2025-2026.json').read_text())['records']},
'trends':trends,'forecasts':forecasts}
(BASE/'analysis/baseline.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2)+'\n')
print('Baseline written:',len(trends),'trends and',len(forecasts),'forecast scenarios.')
