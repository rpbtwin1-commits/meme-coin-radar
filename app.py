import streamlit as st, pandas as pd, numpy as np, yfinance as yf, requests, json, time, math, os
from pathlib import Path
from datetime import datetime, timezone

st.set_page_config(page_title="Multi-Asset Radar V6",page_icon="📡",layout="wide")
DB=Path("portfolio_v4.json")  # keep V4 state file so the upgrade can preserve paper data
DEX="https://api.dexscreener.com"
SOL_RPC=os.getenv("SOLANA_RPC_URL","https://api.mainnet-beta.solana.com")
BIRD_KEY=os.getenv("BIRDEYE_API_KEY","")
CG_KEY=os.getenv("COINGECKO_DEMO_API_KEY","")
GT="https://api.geckoterminal.com/api/v2"
STOCKS=["SPY","QQQ","IWM","AAPL","MSFT","NVDA","AMZN","GOOGL","META","AVGO","JPM","COST","LLY","XOM","GLD","TLT"]
CRYPTOS=["BTC-USD","ETH-USD","SOL-USD","XRP-USD","BNB-USD","ADA-USD","LINK-USD","AVAX-USD","DOGE-USD"]

def fresh(): return {"version":6,"start":10000.0,"cash":10000.0,"high":10000.0,"positions":[],"trades":[],"gem_snapshots":{},"gem_history":{},"gem_watchlist":{}}
def load():
    if "pf" in st.session_state:return st.session_state.pf
    try:d=json.loads(DB.read_text())
    except:d=fresh()
    d.setdefault("gem_snapshots",{});d.setdefault("gem_history",{});d.setdefault("gem_watchlist",{})
    if d.get("version") in (4,5):d["version"]=6
    st.session_state.pf=d; return d
def save(d):
    st.session_state.pf=d
    try:DB.write_text(json.dumps(d,indent=2))
    except:pass
def money(x): return f"${float(x):,.2f}"
def now(): return datetime.now(timezone.utc).isoformat()

def rsi(s,n=14):
    d=s.diff();g=d.clip(lower=0).rolling(n).mean();l=(-d.clip(upper=0)).rolling(n).mean()
    return 100-(100/(1+g/l.replace(0,np.nan)))
def atr(df,n=14):
    h,l,c=df["High"],df["Low"],df["Close"]
    tr=pd.concat([(h-l).abs(),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.rolling(n).mean()

@st.cache_data(ttl=300,show_spinner=False)
def hist(t):
    try:
        d=yf.download(t,period="1y",interval="1d",auto_adjust=True,progress=False,threads=False)
        if isinstance(d.columns,pd.MultiIndex):d.columns=d.columns.get_level_values(0)
        return d.dropna() if d is not None and len(d)>=70 else None
    except:return None

def metrics(t,cls):
    d=hist(t)
    if d is None:return None
    c=d["Close"].astype(float);v=d["Volume"].astype(float)
    p=float(c.iloc[-1]);s20=float(c.rolling(20).mean().iloc[-1]);s50=float(c.rolling(50).mean().iloc[-1])
    s200=float(c.rolling(200).mean().iloc[-1]) if len(c)>=200 else s50
    rv=float(rsi(c).iloc[-1]); av=float(atr(d).iloc[-1])
    r5=(p/float(c.iloc[-6])-1)*100;r20=(p/float(c.iloc[-21])-1)*100
    vol=float(c.pct_change().rolling(20).std().iloc[-1]*np.sqrt(252)*100)
    score=50
    score+=18 if p>s20>s50 else -12
    score+=8 if p>s200 else -8
    score+=10 if 52<=rv<=68 else -10 if rv>75 or rv<40 else 0
    score+=10 if 1<=r20<=18 else -10 if r20>25 else 0
    score+=6 if -1<=r5<=8 else -8 if r5>12 else 0
    score+=5 if (p/s20-1)*100<=8 else -8
    return {"ticker":t,"class":cls,"price":p,"s20":s20,"s50":s50,"s200":s200,"rsi":rv,"atr":av,"r5":r5,"r20":r20,"vol":vol,"score":max(0,min(100,round(score)))}

def regime(cls):
    b=metrics("SPY","Stock/ETF") if cls=="Stock/ETF" else metrics("BTC-USD","Core Crypto")
    return bool(b and b["price"]>b["s50"])

def candidate(m):
    return bool(m and regime(m["class"]) and m["price"]>m["s20"]>m["s50"] and m["price"]>m["s200"] and m["score"]>=72 and 48<=m["rsi"]<=70 and -1<=m["r5"]<=9 and m["r20"]>0 and (m["price"]/m["s20"]-1)*100<=8 and not(m["class"]=="Core Crypto" and m["vol"]>95))

def mark(p):
    if p["class"] in ("Stock/ETF","Core Crypto"):
        m=metrics(p["symbol"],p["class"]);return (m["price"] if m else p["entry"]),m
    try:
        j=requests.get(f"{DEX}/latest/dex/pairs/solana/{p['pair']}",timeout=10).json()
        q=(j.get("pairs") or [None])[0]
        px=float(q.get("priceUsd") or 0) if q else p["entry"]
        return px,q
    except:return p["entry"],None

def equity(d):
    e=d["cash"];marks=[]
    for p in d["positions"]:
        px,m=mark(p);v=p["qty"]*px;e+=v;marks.append((p,m,px,v))
    return e,marks

def loss_streak(d):
    n=0
    for t in reversed(d["trades"]):
        if t["pnl"]<0:n+=1
        else:break
    return n

@st.cache_data(ttl=15,show_spinner=False)
def meme_feed():
    out={}
    for ep in ["/token-profiles/latest/v1","/token-boosts/latest/v1"]:
        try:
            for x in requests.get(DEX+ep,timeout=10).json() or []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):out[x["tokenAddress"]]=1
        except:pass
    return list(out)[:25]

def meme_candidate(token):
    try:
        ps=requests.get(f"{DEX}/token-pairs/v1/solana/{token}",timeout=10).json() or []
        good=[]
        for p in ps:
            if (p.get("baseToken") or {}).get("address")!=token:continue
            liq=float((p.get("liquidity") or {}).get("usd") or 0);vol=float((p.get("volume") or {}).get("h1") or 0)
            tx=(p.get("txns") or {}).get("h1") or {};b=int(tx.get("buys") or 0);s=int(tx.get("sells") or 0)
            pc=p.get("priceChange") or {};p5=float(pc.get("m5") or 0);p1=float(pc.get("h1") or 0)
            ts=p.get("pairCreatedAt");age=999
            if ts:age=(datetime.now(timezone.utc).timestamp()-float(ts)/1000)/3600
            mc=float(p.get("marketCap") or p.get("fdv") or 0);ratio=(b+1)/(s+1)
            if liq>=150000 and vol>=75000 and age>=1 and 1.3<=ratio<=2.5 and -2<=p5<=5 and 3<=p1<=18 and (mc<=0 or 1<=mc/liq<=12):
                good.append((liq,p,ratio,age,p5,p1,vol))
        if not good:return None
        _,p,ratio,age,p5,p1,vol=max(good,key=lambda x:x[0])
        return {"symbol":"$"+(p.get("baseToken") or {}).get("symbol",""),"pair":p.get("pairAddress"),"price":float(p.get("priceUsd") or 0),"liq":float((p.get("liquidity") or {}).get("usd") or 0),"ratio":ratio,"age":age,"p5":p5,"p1":p1,"vol":vol}
    except:return None


# ---------- V6 Early Gems ----------
def _f(x,default=0.0):
    try:return float(x if x is not None else default)
    except:return float(default)
def _i(x,default=0):
    try:return int(x if x is not None else default)
    except:return int(default)
def _token_from_gt_id(v):
    return v.split("_",1)[1] if isinstance(v,str) and v.startswith("solana_") else v

@st.cache_data(ttl=12,show_spinner=False)
def gecko_new_pools():
    """Keyless GeckoTerminal first; optional CoinGecko Demo endpoint fallback/upgrade."""
    out={}
    urls=[]
    if CG_KEY:
        urls.append(("https://api.coingecko.com/api/v3/onchain/networks/solana/new_pools",{"x-cg-demo-api-key":CG_KEY},"CoinGecko new pool"))
    urls.append((f"{GT}/networks/solana/new_pools",{},"GeckoTerminal new pool"))
    for url,headers,label in urls:
        for page in (1,2,3):
            try:
                r=requests.get(url,headers=headers,params={"page":page,"include":"base_token","include_gt_community_data":"true"},timeout=12)
                if r.status_code!=200:break
                j=r.json() or {}; included={x.get("id"):x for x in j.get("included",[]) or []}
                for row in j.get("data",[]) or []:
                    rel=((row.get("relationships") or {}).get("base_token") or {}).get("data") or {}
                    token=_token_from_gt_id(rel.get("id")); a=row.get("attributes") or {}
                    if not token:continue
                    ta=(included.get(rel.get("id")) or {}).get("attributes") or {}
                    tx=a.get("transactions") or {}; t5=tx.get("m5") or {}; t1=tx.get("h1") or {}
                    out[token]={"source":label,"boost":False,"gt":{
                        "pair":a.get("address"),"name":ta.get("name"),"symbol":ta.get("symbol"),
                        "buyers5":_i(t5.get("buyers")),"sellers5":_i(t5.get("sellers")),
                        "buyers1":_i(t1.get("buyers")),"sellers1":_i(t1.get("sellers")),
                        "reserve":_f(a.get("reserve_in_usd")),"sus":_i(a.get("community_sus_report")),
                        "created":a.get("pool_created_at"),"fdv":_f(a.get("fdv_usd")),"mc":_f(a.get("market_cap_usd")),
                    }}
                if not j.get("data"):break
            except:break
        if out:break
    return out

@st.cache_data(ttl=10,show_spinner=False)
def gem_universe():
    out={}
    # Fresh pool feeds are the main discovery path. DEX profile/boost/takeover feeds widen coverage.
    try:out.update(gecko_new_pools())
    except:pass
    for ep,label in [("/token-profiles/latest/v1","DEX profile"),("/token-boosts/latest/v1","DEX boost"),("/community-takeovers/latest/v1","community takeover")]:
        try:
            for x in requests.get(DEX+ep,timeout=10).json() or []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):
                    a=x["tokenAddress"]
                    old=out.get(a,{})
                    old.update({"source":old.get("source",label),"boost":old.get("boost",False) or ep.startswith("/token-boosts")})
                    out[a]=old
        except:pass
    if BIRD_KEY:
        try:
            r=requests.get("https://public-api.birdeye.so/defi/v2/tokens/new_listing",headers={"X-API-KEY":BIRD_KEY,"x-chain":"solana"},params={"limit":20,"meme_platform_enabled":"true"},timeout=12).json()
            data=(r or {}).get("data",{});items=data.get("items",[]) if isinstance(data,dict) else (data or [])
            for x in items:
                a=x.get("address") or x.get("tokenAddress")
                if a:
                    old=out.get(a,{})
                    old.update({"source":"Birdeye fresh listing","boost":old.get("boost",False)})
                    out[a]=old
        except:pass
    return out

@st.cache_data(ttl=45,show_spinner=False)
def gem_chain_risk(token):
    out={"top1":None,"top10":None,"mint_auth":None,"freeze_auth":None}
    try:
        a=requests.post(SOL_RPC,json={"jsonrpc":"2.0","id":1,"method":"getTokenLargestAccounts","params":[token,{"commitment":"confirmed"}]},timeout=10).json()
        b=requests.post(SOL_RPC,json={"jsonrpc":"2.0","id":2,"method":"getTokenSupply","params":[token,{"commitment":"confirmed"}]},timeout=10).json()
        vals=(a.get("result") or {}).get("value") or [];sv=(b.get("result") or {}).get("value") or {};supply=_f(sv.get("uiAmount"))
        ps=[_f(x.get("uiAmount"))/supply*100 for x in vals] if supply else []
        if ps:out.update({"top1":sum(ps[:1]),"top10":sum(ps[:10])})
    except:pass
    try:
        g=requests.post(SOL_RPC,json={"jsonrpc":"2.0","id":3,"method":"getAccountInfo","params":[token,{"encoding":"jsonParsed","commitment":"confirmed"}]},timeout=10).json()
        info=((((g.get("result") or {}).get("value") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        if info:
            out["mint_auth"]=info.get("mintAuthority")
            out["freeze_auth"]=info.get("freezeAuthority")
    except:pass
    return out

def gem_data(token,meta):
    try:
        ps=requests.get(f"{DEX}/token-pairs/v1/solana/{token}",timeout=10).json() or []
        valid=[]
        for p in ps:
            if (p.get("baseToken") or {}).get("address")!=token:continue
            liq=_f((p.get("liquidity") or {}).get("usd"))
            if liq>0 and p.get("priceUsd"):valid.append(p)
        if not valid:return None
        p=max(valid,key=lambda x:_f((x.get("liquidity") or {}).get("usd")))
        liq=_f((p.get("liquidity") or {}).get("usd"));mc=_f(p.get("marketCap") or p.get("fdv"))
        vol=p.get("volume") or {};tx=p.get("txns") or {};pc=p.get("priceChange") or {}
        h=tx.get("h1") or {};m=tx.get("m5") or {};b=_i(h.get("buys"));sel=_i(h.get("sells"));b5=_i(m.get("buys"));s5=_i(m.get("sells"))
        ts=p.get("pairCreatedAt");age=999
        if ts:
            ts=_f(ts)/(1000 if _f(ts)>1e10 else 1);age=max(0,(datetime.now(timezone.utc).timestamp()-ts)/60)
        gt=meta.get("gt") or {}
        if age==999 and gt.get("created"):
            try:age=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(gt["created"].replace("Z","+00:00"))).total_seconds()/60)
            except:pass
        # Avoid hammering the public Solana RPC for obviously out-of-scope/dead pools.
        chain_ok=(5000<=mc<=250000 and liq>=3000 and (b+sel)>=5 and age<=720)
        c=gem_chain_risk(token) if chain_ok else {"top1":None,"top10":None,"mint_auth":None,"freeze_auth":None}
        base=p.get("baseToken") or {};info=p.get("info") or {}
        v1=_f(vol.get("h1"));v5=_f(vol.get("m5"))
        # 5-minute pace relative to the 1-hour average. >1 means recent volume is accelerating.
        pace=(v5*12/v1) if v1>0 else (3.0 if v5>0 else 0.0)
        txpace=((b5+s5)*12/(b+sel)) if (b+sel)>0 else (3.0 if b5+s5>0 else 0.0)
        return {"address":token,"symbol":base.get("symbol") or gt.get("symbol") or "?","name":base.get("name") or gt.get("name") or "",
                "pair":p.get("pairAddress") or gt.get("pair"),"dex":p.get("dexId") or "","age":age,"price":_f(p.get("priceUsd")),"liq":liq,"mc":mc,
                "v1":v1,"v5":v5,"volpace":pace,"txpace":txpace,"buys":b,"sells":sel,"buys5":b5,"sells5":s5,
                "ratio":(b+1)/(sel+1),"ratio5":(b5+1)/(s5+1),"buyers5":_i(gt.get("buyers5")),"buyers1":_i(gt.get("buyers1")),
                "p1":_f(pc.get("h1")),"p5":_f(pc.get("m5")),"top1":c["top1"],"top10":c["top10"],"mint_auth":c["mint_auth"],"freeze_auth":c["freeze_auth"],
                "socials":len(info.get("socials") or []),"websites":len(info.get("websites") or []),"source":meta.get("source","unknown"),"boost":meta.get("boost",False),"sus":_i(gt.get("sus"))}
    except:return None

def snapshot_delta(m,hist):
    if not hist:return {}
    old=hist[-1];
    try:mins=max(.1,(datetime.now(timezone.utc)-datetime.fromisoformat(old["time"])).total_seconds()/60)
    except:mins=1
    def pct(key):
        ov=_f(old.get(key));return ((m[key]/ov)-1)*100 if ov>0 else None
    return {"mins":mins,"liq":pct("liq"),"mc":pct("mc"),"v1":pct("v1"),"buys":pct("buys"),"buyers1":pct("buyers1") if m.get("buyers1") else None}

def gem_rank(m,hist=None):
    score=30;good=[];flags=[];hard=[];delta=snapshot_delta(m,hist or [])
    # Scope: we want to find these BEFORE they are obvious.
    if 15000<=m["mc"]<=75000:score+=18;good.append("target $15K–$75K cap")
    elif 75000<m["mc"]<=150000:score+=8;good.append("still micro-cap")
    elif m["mc"]>300000:score-=12;flags.append("outside early-cap target")
    elif 0<m["mc"]<10000:score-=8;flags.append("extremely tiny cap")
    if 3<=m["age"]<=20:score+=8;good.append("very early")
    elif 20<m["age"]<=120:score+=6
    elif m["age"]<2:score-=10;flags.append("under 2 minutes old")
    elif m["age"]>360:score-=8
    if m["liq"]>=30000:score+=10;good.append("usable liquidity")
    elif m["liq"]>=15000:score+=6
    elif m["liq"]<7000:score-=25;hard.append("dangerously thin liquidity")
    else:score-=8;flags.append("thin liquidity")
    if m["mc"]>0:
        lm=m["liq"]/m["mc"]
        if .18<=lm<=1.5:score+=7;good.append("healthy liquidity/cap")
        elif lm<.08:score-=12;flags.append("low liquidity vs cap")
    if 1.15<=m["ratio"]<=3.0:score+=7;good.append("1h buy pressure")
    elif m["ratio"]<.75:score-=12;flags.append("1h sell pressure")
    elif m["ratio"]>5:score-=8;flags.append("extreme one-sided flow")
    if 1.15<=m["ratio5"]<=3.5:score+=7;good.append("5m buyers leading")
    elif m["ratio5"]<.7:score-=10;flags.append("5m sellers leading")
    # Acceleration: compare recent five-minute pace with the hourly average.
    if 1.35<=m["volpace"]<=4.5:score+=11;good.append(f"volume pace {m['volpace']:.1f}×")
    elif m["volpace"]>7:score-=8;flags.append("volume spike may be artificial")
    if 1.25<=m["txpace"]<=4.5:score+=7;good.append("transactions accelerating")
    if m["buyers5"]>=8:score+=5;good.append(f"{m['buyers5']} unique 5m buyers")
    if m["buyers1"]>=25:score+=4
    if m["v5"]>=2500:score+=4
    if m["v1"]>=10000:score+=4
    if -5<=m["p5"]<=18:score+=4
    elif m["p5"]>35:score-=15;flags.append("5m vertical pump")
    elif m["p5"]<-18:score-=12;flags.append("5m breakdown")
    if m["p1"]>150:score-=15;flags.append("already exploded")
    if m["p1"]<-35:score-=12;flags.append("1h collapse")
    # Raw largest-account concentration can include pool/bonding-curve accounts; treat as a warning, not proof of insiders.
    if m["top10"] is not None:
        if m["top10"]<=35:score+=6;good.append("raw top-10 concentration moderate")
        elif m["top10"]>=80:score-=20;hard.append("raw top-10 concentration extreme")
        elif m["top10"]>=60:score-=10;flags.append("raw top-10 concentration high")
    else:flags.append("holder concentration unavailable")
    if m["top1"] is not None and m["top1"]>=45:score-=15;flags.append("raw largest account >=45%")
    if m["freeze_auth"]:score-=25;hard.append("freeze authority active")
    if m["mint_auth"]:score-=18;hard.append("mint authority active")
    if m["socials"]>=1:score+=2
    if m["websites"]>=1:score+=2
    if m["boost"]:flags.append("paid DEX boost")
    if m["sus"]>0:score-=8;flags.append("GeckoTerminal suspicious reports")
    if delta:
        if delta.get("liq") is not None and delta["liq"]>=10:score+=6;good.append("liquidity growing vs prior scan")
        if delta.get("liq") is not None and delta["liq"]<=-15:score-=12;flags.append("liquidity falling vs prior scan")
        if delta.get("v1") is not None and delta["v1"]>=15:score+=6;good.append("1h volume rising vs prior scan")
        if delta.get("buyers1") is not None and delta["buyers1"]>=15:score+=6;good.append("unique buyers rising vs prior scan")
    score=round(max(0,min(100,score)))
    dangerous=bool(hard)
    too_late=(m["mc"]>150000 or m["p1"]>150 or m["p5"]>45)
    if dangerous:status="🔴 DANGEROUS"
    elif too_late:status="⚪ TOO LATE"
    elif score>=78 and 15000<=m["mc"]<=100000 and m["volpace"]>=1.2:status="🔥 BREAKOUT"
    elif score>=62:status="🟢 BUILDING"
    else:status="🟡 NEW"
    return score,status,good,flags+hard,delta

def record_gem_snapshot(d,m,score,status):
    h=d["gem_history"].setdefault(m["address"],[])
    h.append({"time":now(),"liq":m["liq"],"mc":m["mc"],"v1":m["v1"],"v5":m["v5"],"buys":m["buys"],"buyers1":m.get("buyers1",0),"score":score,"status":status})
    d["gem_history"][m["address"]]=h[-12:]
    d["gem_snapshots"][m["address"]]=h[-1]
    d["gem_watchlist"][m["address"]]={"score":score,"status":status,"symbol":m["symbol"],"mc":m["mc"],"time":now()}

d=load()
st.title("📡 Multi-Asset Radar — V6")
st.caption("Stocks/ETFs • established crypto • capped memes • 💎 Early Gems scouting • PAPER MONEY ONLY")

with st.sidebar:
    auto=st.toggle("Run while app is open",False)
    secs=st.slider("Refresh seconds",15,120,30,15)
    st.write("Max allocation: 55% stocks/ETFs • 35% core crypto • 10% memes")
    st.write("No leverage, margin, options, or real-money execution.")

eq,marks=equity(d);d["high"]=max(d.get("high",d["start"]),eq);dd=(eq/d["high"]-1)*100
breaker=loss_streak(d)>=4 or dd<=-7
actions=[]

for p in list(d["positions"]):
    px,m=mark(p);ret=(px/p["entry"]-1)*100;days=(datetime.now(timezone.utc)-datetime.fromisoformat(p["time"])).total_seconds()/86400
    peak=max(p.get("peak",p["entry"]),px);p["peak"]=peak;trail=(px/peak-1)*100;reason=None
    if p["class"]=="Stock/ETF":
        if ret<=-p["stop"]:reason="risk stop"
        elif ret>=p["take"]:reason="profit target"
        elif peak/p["entry"]>=1.08 and trail<=-5:reason="trailing exit"
        elif m and px<m["s20"] and days>=3:reason="trend break"
        elif days>=30:reason="max hold"
    elif p["class"]=="Core Crypto":
        if ret<=-p["stop"]:reason="risk stop"
        elif ret>=p["take"]:reason="profit target"
        elif peak/p["entry"]>=1.10 and trail<=-7:reason="trailing exit"
        elif m and px<m["s20"] and days>=1:reason="trend break"
        elif days>=14:reason="max hold"
    else:
        if ret<=-12:reason="observed stop"
        elif ret>=22:reason="profit target"
        elif peak/p["entry"]>=1.10 and trail<=-7:reason="trailing exit"
        elif days>=.35:reason="max meme hold"
    if reason:
        proceeds=p["qty"]*px;pnl=proceeds-p["cost"];d["cash"]+=proceeds
        d["trades"].append({**p,"exit_time":now(),"exit":px,"pnl":pnl,"pnl_pct":pnl/p["cost"]*100,"reason":reason})
        d["positions"].remove(p);actions.append(f"SELL {p['symbol']} {pnl/p['cost']*100:+.1f}%")

eq,marks=equity(d);d["high"]=max(d["high"],eq);dd=(eq/d["high"]-1)*100;breaker=loss_streak(d)>=4 or dd<=-7

if d.get("version")==6 and not breaker:
    held={p["symbol"] for p in d["positions"]}
    ranked=[]
    for t in STOCKS:
        m=metrics(t,"Stock/ETF")
        if m:ranked.append(m)
    for t in CRYPTOS:
        m=metrics(t,"Core Crypto")
        if m:ranked.append(m)
    for m in sorted(ranked,key=lambda x:x["score"],reverse=True):
        if m["ticker"] in held or not candidate(m):continue
        cls=m["class"];eq,_=equity(d)
        exposure=sum(p["qty"]*mark(p)[0] for p in d["positions"] if p["class"]==cls)/max(eq,1)
        cap=.55 if cls=="Stock/ETF" else .35
        if exposure>=cap:continue
        cost=min(d["cash"],eq*(.05 if cls=="Stock/ETF" else .035))
        if cost<50:continue
        entry=m["price"]*(1.0006 if cls=="Stock/ETF" else 1.0025)
        ap=m["atr"]/m["price"]*100;stop=max(4,min(12,ap*2.2)) if cls=="Stock/ETF" else max(7,min(18,ap*2.4));take=max(stop*2,12 if cls=="Stock/ETF" else 18)
        d["cash"]-=cost;d["positions"].append({"class":cls,"symbol":m["ticker"],"time":now(),"cost":cost,"qty":cost/entry,"entry":entry,"peak":entry,"stop":stop,"take":take,"score":m["score"]})
        held.add(m["ticker"]);actions.append(f"BUY {m['ticker']} {money(cost)}")
        if len([p for p in d["positions"] if p["class"]==cls])>=5:break
    if sum(p["class"]=="Meme" for p in d["positions"])<2:
        for tok in meme_feed():
            m=meme_candidate(tok)
            if not m:continue
            eq,_=equity(d);exposure=sum(p["qty"]*mark(p)[0] for p in d["positions"] if p["class"]=="Meme")/max(eq,1)
            if exposure>=.10:break
            cost=min(d["cash"],eq*.01);entry=m["price"]*1.006
            d["cash"]-=cost;d["positions"].append({"class":"Meme","symbol":m["symbol"],"address":tok,"pair":m["pair"],"time":now(),"cost":cost,"qty":cost/entry,"entry":entry,"peak":entry})
            actions.append(f"BUY {m['symbol']} {money(cost)}");break

eq,marks=equity(d);d["high"]=max(d["high"],eq);dd=(eq/d["high"]-1)*100;save(d)
real=sum(t["pnl"] for t in d["trades"]);wins=sum(t["pnl"]>0 for t in d["trades"]);wr=100*wins/len(d["trades"]) if d["trades"] else 0
c1,c2,c3,c4=st.columns(4)
c1.metric("Portfolio",money(eq),f"{(eq/d['start']-1)*100:+.1f}%");c2.metric("Cash",money(d["cash"]));c3.metric("Realized P&L",money(real));c4.metric("Win Rate",f"{wr:.1f}%",f"{len(d['trades'])} closed")
if breaker:st.error(f"🛑 Circuit breaker — {loss_streak(d)} consecutive losses / {dd:.1f}% drawdown")
if actions:st.success(" • ".join(actions))

tabs=st.tabs(["💎 Early Gems","🏦 Portfolio","📊 Stocks","₿ Crypto","🚀 Meme","📒 History","🧠 Strategy","💾 Reset"])
with tabs[0]:
    st.subheader("💎 Early Gems V6 — Continuous Early-Launch Radar")
    st.caption("Discovery target: roughly $15K–$75K market cap. It ranks momentum + structural risk; it does NOT auto-buy or predict guaranteed winners.")
    src=[]
    src.append("GeckoTerminal/CoinGecko fresh pools")
    src.append("DEX Screener profiles/boosts")
    if BIRD_KEY:src.append("Birdeye fresh listings")
    st.info("Discovery: "+" + ".join(src)+". Raw largest-account concentration can include bonding-curve/pool accounts, so treat it as a warning rather than proof of insider ownership.")
    cscan,cmin,cmax=st.columns([1.2,1,1])
    with cscan:
        scan=st.button("⚡ SCAN EARLY GEMS NOW",use_container_width=True)
    with cmin:
        min_mc=st.number_input("Min market cap",min_value=5000,value=15000,step=5000)
    with cmax:
        max_mc=st.number_input("Discovery cap",min_value=25000,value=150000,step=25000)
    if scan:st.cache_data.clear()
    uni=gem_universe();found=[]
    for token,meta in list(uni.items())[:120]:
        m=gem_data(token,meta)
        if not m or m["age"]>720:continue
        if m["mc"]>0 and (m["mc"]<min_mc*.35 or m["mc"]>max_mc):continue
        hist=d["gem_history"].get(token,[])
        score,status,good,flags,delta=gem_rank(m,hist)
        found.append((score,status,m,good,flags,delta))
        record_gem_snapshot(d,m,score,status)
    save(d)
    priority={"🔥 BREAKOUT":0,"🟢 BUILDING":1,"🟡 NEW":2,"⚪ TOO LATE":3,"🔴 DANGEROUS":4}
    found.sort(key=lambda x:(priority.get(x[1],9),-x[0]))
    # Compact scoreboard first.
    rows=[]
    for score,status,m,good,flags,delta in found[:30]:
        rows.append({"Status":status,"Token":"$"+m["symbol"],"Score":score,"MCap":round(m["mc"]),"Liq":round(m["liq"]),"Age min":round(m["age"],1),"5m vol":round(m["v5"]),"Vol pace":round(m["volpace"],2),"5m B/S":round(m["ratio5"],2),"5m buyers":m["buyers5"] or "—","5m %":round(m["p5"],1)})
    if rows:st.dataframe(rows,use_container_width=True,hide_index=True)
    else:st.warning("No fresh Solana pools in the current discovery batch fit the selected scope. Try again on the next refresh; new-pool feeds rotate quickly.")
    st.markdown("---")
    for score,status,m,good,flags,delta in found[:18]:
        top10=f"{m['top10']:.1f}%" if m["top10"] is not None else "unverified"
        auth="✅ revoked" if not m["mint_auth"] and not m["freeze_auth"] else "⚠️ active authority"
        st.markdown(f"### {status} — ${m['symbol']} — {score}/100")
        st.write(f"**MCap:** {money(m['mc'])} • **Liquidity:** {money(m['liq'])} • **Age:** {m['age']:.1f} min • **Source:** {m['source']}")
        st.write(f"**5m vol:** {money(m['v5'])} • **1h vol:** {money(m['v1'])} • **Volume pace:** {m['volpace']:.2f}× • **Txn pace:** {m['txpace']:.2f}×")
        buyers=f" • **Unique buyers:** {m['buyers5']} (5m) / {m['buyers1']} (1h)" if m["buyers1"] or m["buyers5"] else ""
        st.write(f"**Buys/Sells:** {m['buys']}/{m['sells']} • **5m B/S:** {m['ratio5']:.2f} • **5m:** {m['p5']:+.1f}% • **1h:** {m['p1']:+.1f}%{buyers}")
        st.write(f"**Raw Top 10:** {top10} • **Mint/freeze:** {auth} • **DEX:** {m['dex'] or 'unknown'}")
        st.write("✅ "+(" • ".join(good[:6]) if good else "No strong confirmations yet"))
        st.write("⚠️ "+(" • ".join(flags[:6]) if flags else "No major flags detected from available data"))
        st.caption("Contract / mint address")
        st.code(m["address"],language=None)
        st.divider()

with tabs[1]:
    if not marks:st.info("No open positions.")
    for p,m,px,val in marks:st.markdown(f"### {p['symbol']} — {p['class']} — {(val/p['cost']-1)*100:+.1f}%\nCost {money(p['cost'])} • Value {money(val)}")
with tabs[2]:
    rows=[]
    for t in STOCKS:
        m=metrics(t,"Stock/ETF")
        if m:rows.append({"Ticker":t,"Price":m["price"],"Score":m["score"],"RSI":round(m["rsi"],1),"5d %":round(m["r5"],1),"20d %":round(m["r20"],1),"Entry":candidate(m)})
    st.dataframe(rows,use_container_width=True,hide_index=True)
with tabs[3]:
    rows=[]
    for t in CRYPTOS:
        m=metrics(t,"Core Crypto")
        if m:rows.append({"Asset":t.replace("-USD",""),"Price":m["price"],"Score":m["score"],"RSI":round(m["rsi"],1),"5d %":round(m["r5"],1),"20d %":round(m["r20"],1),"Entry":candidate(m)})
    st.dataframe(rows,use_container_width=True,hide_index=True)
with tabs[4]:
    rows=[]
    for tok in meme_feed():
        m=meme_candidate(tok)
        if m:rows.append({"Coin":m["symbol"],"Liq":money(m["liq"]),"Vol":money(m["vol"]),"Age h":round(m["age"],1),"5m %":m["p5"],"1h %":m["p1"],"B/S":round(m["ratio"],2)})
    st.dataframe(rows,use_container_width=True,hide_index=True)
with tabs[5]:
    if not d["trades"]:st.info("No closed trades.")
    for t in reversed(d["trades"]):st.markdown(f"### {t['symbol']} — {t['class']} — {t['pnl_pct']:+.1f}%\nP&L {money(t['pnl'])} • Exit: {t['reason']}")
with tabs[6]:
    st.markdown("**Stocks/ETFs:** daily trend-following, up to ~30 days.\n\n**Core crypto:** established liquid assets, up to ~14 days.\n\n**Meme coins:** maximum 10% portfolio exposure and ~1% sizing per entry.\n\nV6 keeps the diversified portfolio logic and upgrades Early Gems with fresh-pool discovery, $15K–$75K targeting, 5m-vs-1h acceleration, unique-buyer data when available, persistent scan history, raw holder concentration, and mint/freeze authority checks. Early Gems remains WATCH-FIRST and never auto-buys. No strategy can guarantee profit.")
with tabs[7]:
    st.warning("Reset starts a clean $10,000 PAPER portfolio.")
    ok=st.checkbox("Reset V6")
    if st.button("RESET V6 TO $10,000",disabled=not ok):save(fresh());st.rerun()

if auto:
    time.sleep(secs);st.cache_data.clear();st.rerun()
