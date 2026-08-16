import os, json, time, sqlite3, threading
from datetime import datetime, timezone
from typing import Optional
import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

DEX='https://api.dexscreener.com'
SOL_RPC=os.getenv('SOLANA_RPC_URL','https://api.mainnet-beta.solana.com')
BIRD_KEY=os.getenv('BIRDEYE_API_KEY','').strip()
API_TOKEN=os.getenv('API_TOKEN','').strip()
SCAN_SECONDS=int(os.getenv('SCAN_SECONDS','20'))
DB_PATH=os.getenv('DB_PATH','/data/gems.db')
S=requests.Session(); S.headers.update({'Accept':'application/json','User-Agent':'EarlyGemsV6/1.0'})
app=FastAPI(title='Early Gems V6',version='1.0')
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['GET'],allow_headers=['*'])

def utc(): return datetime.now(timezone.utc).isoformat()
def db():
    os.makedirs(os.path.dirname(DB_PATH),exist_ok=True)
    c=sqlite3.connect(DB_PATH,check_same_thread=False); c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS gems(address TEXT PRIMARY KEY,symbol TEXT,name TEXT,pair TEXT,first_seen TEXT,last_seen TEXT,age_min REAL,market_cap REAL,liquidity REAL,volume_h1 REAL,buys_h1 INTEGER,sells_h1 INTEGER,buys_m5 INTEGER,sells_m5 INTEGER,p5 REAL,p1 REAL,top1 REAL,top10 REAL,score INTEGER,status TEXT,source TEXT,flags TEXT,good TEXT,scan_count INTEGER DEFAULT 1,high_score INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,address TEXT,ts TEXT,score INTEGER,status TEXT,market_cap REAL,liquidity REAL,volume_h1 REAL,buys_h1 INTEGER,sells_h1 INTEGER,p5 REAL,p1 REAL)''')
    c.commit(); return c

def getj(url,params=None,headers=None):
    try:
        r=S.get(url,params=params,headers=headers or {},timeout=10); r.raise_for_status(); return r.json()
    except: return None

def discover():
    out={}
    if BIRD_KEY:
        d=getj('https://public-api.birdeye.so/defi/v2/tokens/new_listing',params={'limit':50,'meme_platform_enabled':'true'},headers={'X-API-KEY':BIRD_KEY,'x-chain':'solana'})
        payload=(d or {}).get('data',{}); items=payload.get('items',[]) if isinstance(payload,dict) else (payload or [])
        for x in items:
            a=x.get('address') or x.get('tokenAddress')
            if a: out[a]='Birdeye fresh listing'
    for ep,label in [('/token-profiles/latest/v1','DEX profile'),('/token-boosts/latest/v1','DEX boost'),('/community-takeovers/latest/v1','DEX CTO')]:
        d=getj(DEX+ep)
        for x in d or []:
            if x.get('chainId')=='solana' and x.get('tokenAddress'): out.setdefault(x['tokenAddress'],label)
    return out

def best_pair(token):
    d=getj(f'{DEX}/token-pairs/v1/solana/{token}'); ps=d if isinstance(d,list) else (d or {}).get('pairs',[]); vals=[]
    for p in ps or []:
        if (p.get('baseToken') or {}).get('address')!=token: continue
        try: px=float(p.get('priceUsd') or 0)
        except: px=0
        liq=float((p.get('liquidity') or {}).get('usd') or 0)
        if px>0 and liq>0 and p.get('pairAddress'): vals.append(p)
    return max(vals,key=lambda p:float((p.get('liquidity') or {}).get('usd') or 0)) if vals else None

def concentration(token):
    try:
        a=S.post(SOL_RPC,json={'jsonrpc':'2.0','id':1,'method':'getTokenLargestAccounts','params':[token,{'commitment':'confirmed'}]},timeout=10).json()
        b=S.post(SOL_RPC,json={'jsonrpc':'2.0','id':1,'method':'getTokenSupply','params':[token,{'commitment':'confirmed'}]},timeout=10).json()
        vals=a['result']['value']; supply=float(b['result']['value'].get('uiAmount') or 0); pcts=[float(x.get('uiAmount') or 0)/supply*100 for x in vals] if supply else []
        return sum(pcts[:1]),sum(pcts[:10])
    except: return None,None

def metrics(token,source):
    p=best_pair(token)
    if not p: return None
    liq=float((p.get('liquidity') or {}).get('usd') or 0); mc=float(p.get('marketCap') or p.get('fdv') or 0)
    vol=p.get('volume') or {}; tx=p.get('txns') or {}; pc=p.get('priceChange') or {}; h1=tx.get('h1') or {}; m5=tx.get('m5') or {}
    buys=int(h1.get('buys') or 0); sells=int(h1.get('sells') or 0); buys5=int(m5.get('buys') or 0); sells5=int(m5.get('sells') or 0)
    v1=float(vol.get('h1') or 0); p5=float(pc.get('m5') or 0); p1=float(pc.get('h1') or 0); ts=p.get('pairCreatedAt'); age=999999
    if ts:
        ts=float(ts)/(1000 if float(ts)>1e10 else 1); age=max(0,(datetime.now(timezone.utc).timestamp()-ts)/60)
    top1,top10=concentration(token); base=p.get('baseToken') or {}
    return dict(address=token,symbol=base.get('symbol',''),name=base.get('name',''),pair=p.get('pairAddress'),age_min=age,market_cap=mc,liquidity=liq,volume_h1=v1,buys_h1=buys,sells_h1=sells,buys_m5=buys5,sells_m5=sells5,p5=p5,p1=p1,top1=top1,top10=top10,source=source)

def score(m,old=None):
    s=25; good=[]; flags=[]; age=m['age_min']; liq=m['liquidity']; mc=m['market_cap']; ratio=(m['buys_h1']+1)/(m['sells_h1']+1); ratio5=(m['buys_m5']+1)/(m['sells_m5']+1)
    if 2<=age<=15:s+=7;good.append('very early')
    elif 15<age<=60:s+=12;good.append('early + some history')
    elif age<2:s-=8;flags.append('too new to trust')
    if liq>=100000:s+=14;good.append('strong liquidity')
    elif liq>=50000:s+=10
    elif liq>=20000:s+=5
    else:s-=18;flags.append('thin liquidity')
    if 10000<=mc<=150000:s+=10;good.append('micro-cap')
    elif mc<=500000:s+=6
    elif mc>2000000:s-=7
    if 1.3<=ratio<=2.7:s+=10;good.append('healthy buy pressure')
    elif ratio>4:s-=8;flags.append('extreme one-sided flow')
    elif ratio<.9:s-=12;flags.append('sell pressure')
    if 1.1<=ratio5<=2.8:s+=7
    if m['buys_h1']+m['sells_h1']>=100:s+=5
    if m['volume_h1']>=50000:s+=7
    elif m['volume_h1']>=15000:s+=4
    if -3<=m['p5']<=8:s+=5
    elif m['p5']>20:s-=12;flags.append('5m vertical pump')
    if -5<=m['p1']<=35:s+=3
    elif m['p1']>100:s-=10;flags.append('already exploded')
    if m['top10'] is not None:
        if m['top10']<=20:s+=12;good.append('low top-10 concentration')
        elif m['top10']<=35:s+=5
        elif m['top10']>=60:s-=25;flags.append('top-10 concentration extreme')
        elif m['top10']>=45:s-=14;flags.append('top-10 concentration high')
    else:s-=5;flags.append('holder concentration unverified')
    if m['top1'] is not None and m['top1']>=20:s-=15;flags.append('single account >=20%')
    if 'boost' in m['source'].lower(): flags.append('paid DEX boost; not organic proof')
    if old:
        if liq>float(old['liquidity'] or 0)*1.20:s+=8;good.append('liquidity accelerating')
        elif liq<float(old['liquidity'] or 0)*.80:s-=10;flags.append('liquidity falling')
        if m['volume_h1']>float(old['volume_h1'] or 0)*1.25:s+=7;good.append('volume accelerating')
        if m['buys_h1']>int(old['buys_h1'] or 0)*1.20:s+=5;good.append('buyers accelerating')
    s=max(0,min(100,round(s))); severe=any(x in ' '.join(flags).lower() for x in ['extreme','single account','thin liquidity'])
    status='🔥 HIGH PRIORITY' if s>=80 and not severe else '👀 CONFIRMING' if s>=65 else '🟡 WATCH' if s>=45 else '🔴 AVOID'
    return s,status,good,flags

def upsert(c,m):
    old=c.execute('SELECT * FROM gems WHERE address=?',(m['address'],)).fetchone(); sc,status,good,flags=score(m,old); first=old['first_seen'] if old else utc(); high=max(sc,int(old['high_score'] or 0)) if old else sc; count=int(old['scan_count'] or 0)+1 if old else 1
    c.execute('''INSERT INTO gems(address,symbol,name,pair,first_seen,last_seen,age_min,market_cap,liquidity,volume_h1,buys_h1,sells_h1,buys_m5,sells_m5,p5,p1,top1,top10,score,status,source,flags,good,scan_count,high_score) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(address) DO UPDATE SET symbol=excluded.symbol,name=excluded.name,pair=excluded.pair,last_seen=excluded.last_seen,age_min=excluded.age_min,market_cap=excluded.market_cap,liquidity=excluded.liquidity,volume_h1=excluded.volume_h1,buys_h1=excluded.buys_h1,sells_h1=excluded.sells_h1,buys_m5=excluded.buys_m5,sells_m5=excluded.sells_m5,p5=excluded.p5,p1=excluded.p1,top1=excluded.top1,top10=excluded.top10,score=excluded.score,status=excluded.status,source=excluded.source,flags=excluded.flags,good=excluded.good,scan_count=excluded.scan_count,high_score=excluded.high_score''',(m['address'],m['symbol'],m['name'],m['pair'],first,utc(),m['age_min'],m['market_cap'],m['liquidity'],m['volume_h1'],m['buys_h1'],m['sells_h1'],m['buys_m5'],m['sells_m5'],m['p5'],m['p1'],m['top1'],m['top10'],sc,status,m['source'],json.dumps(flags),json.dumps(good),count,high))
    c.execute('INSERT INTO snapshots(address,ts,score,status,market_cap,liquidity,volume_h1,buys_h1,sells_h1,p5,p1) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(m['address'],utc(),sc,status,m['market_cap'],m['liquidity'],m['volume_h1'],m['buys_h1'],m['sells_h1'],m['p5'],m['p1'])); c.commit()

def scanner():
    c=db()
    while True:
        try:
            for token,source in discover().items():
                m=metrics(token,source)
                if m and m['age_min']<=720 and (m['market_cap']<=5_000_000 or m['market_cap']<=0): upsert(c,m)
                time.sleep(.12)
        except Exception as e: print('scan error',repr(e),flush=True)
        time.sleep(max(5,SCAN_SECONDS))

def auth(tok):
    if API_TOKEN and tok!=API_TOKEN: raise HTTPException(status_code=401,detail='bad token')
@app.on_event('startup')
def start(): db().close(); threading.Thread(target=scanner,daemon=True).start()
@app.get('/health')
def health():
    c=db(); n=c.execute('SELECT COUNT(*) n FROM gems').fetchone()['n']; c.close(); return {'ok':True,'gems':n,'time':utc(),'birdeye':bool(BIRD_KEY)}
@app.get('/gems')
def gems(limit:int=25,min_score:int=0,x_api_token:Optional[str]=Header(default=None)):
    auth(x_api_token); c=db(); rows=c.execute('SELECT * FROM gems WHERE score>=? AND age_min<=720 ORDER BY score DESC,last_seen DESC LIMIT ?',(min_score,min(max(limit,1),100))).fetchall(); out=[]
    for r in rows:
        d=dict(r); d['flags']=json.loads(d['flags'] or '[]'); d['good']=json.loads(d['good'] or '[]'); d['ratio']=(d['buys_h1']+1)/(d['sells_h1']+1); out.append(d)
    c.close(); return {'updated':utc(),'count':len(out),'items':out}
@app.get('/gem/{address}')
def gem(address:str,x_api_token:Optional[str]=Header(default=None)):
    auth(x_api_token); c=db(); r=c.execute('SELECT * FROM gems WHERE address=?',(address,)).fetchone(); snaps=c.execute('SELECT ts,score,status,market_cap,liquidity,volume_h1,buys_h1,sells_h1,p5,p1 FROM snapshots WHERE address=? ORDER BY id DESC LIMIT 100',(address,)).fetchall(); c.close()
    if not r: raise HTTPException(status_code=404,detail='not found')
    d=dict(r); d['flags']=json.loads(d['flags'] or '[]'); d['good']=json.loads(d['good'] or '[]'); return {'gem':d,'history':[dict(x) for x in reversed(snaps)]}
if __name__=='__main__': uvicorn.run(app,host='0.0.0.0',port=int(os.getenv('PORT','8000')))
