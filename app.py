import streamlit as st, pandas as pd, numpy as np, yfinance as yf, requests, json, time, math, os
from pathlib import Path
from datetime import datetime, timezone

st.set_page_config(page_title="Multi-Asset Radar V5",page_icon="📡",layout="wide")
DB=Path("portfolio_v4.json")  # keep V4 state file so the upgrade can preserve paper data
DEX="https://api.dexscreener.com"
SOL_RPC=os.getenv("SOLANA_RPC_URL","https://api.mainnet-beta.solana.com")
BIRD_KEY=os.getenv("BIRDEYE_API_KEY","")
STOCKS=["SPY","QQQ","IWM","AAPL","MSFT","NVDA","AMZN","GOOGL","META","AVGO","JPM","COST","LLY","XOM","GLD","TLT"]
CRYPTOS=["BTC-USD","ETH-USD","SOL-USD","XRP-USD","BNB-USD","ADA-USD","LINK-USD","AVAX-USD","DOGE-USD"]

def fresh(): return {"version":5,"start":10000.0,"cash":10000.0,"high":10000.0,"positions":[],"trades":[],"gem_snapshots":{},"gem_watchlist":{}}
def load():
    if "pf" in st.session_state:return st.session_state.pf
    try:d=json.loads(DB.read_text())
    except:d=fresh()
    d.setdefault("gem_snapshots",{});d.setdefault("gem_watchlist",{})
    if d.get("version")==4:d["version"]=5
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


# ---------- V5 Early Gems ----------
@st.cache_data(ttl=10,show_spinner=False)
def gem_universe():
    out={}
    for ep,label in [("/token-profiles/latest/v1","DEX profile"),("/token-boosts/latest/v1","DEX boost"),("/community-takeovers/latest/v1","community takeover")]:
        try:
            for x in requests.get(DEX+ep,timeout=10).json() or []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):
                    out[x["tokenAddress"]]={"source":label,"boost":ep.startswith("/token-boosts")}
        except:pass
    if BIRD_KEY:
        try:
            r=requests.get("https://public-api.birdeye.so/defi/v2/tokens/new_listing",headers={"X-API-KEY":BIRD_KEY,"x-chain":"solana"},params={"limit":50,"meme_platform_enabled":"true"},timeout=12).json()
            data=(r or {}).get("data",{});items=data.get("items",[]) if isinstance(data,dict) else (data or [])
            for x in items:
                a=x.get("address") or x.get("tokenAddress")
                if a:out[a]={"source":"Birdeye fresh listing","boost":False}
        except:pass
    return out

@st.cache_data(ttl=30,show_spinner=False)
def gem_concentration(token):
    try:
        a=requests.post(SOL_RPC,json={"jsonrpc":"2.0","id":1,"method":"getTokenLargestAccounts","params":[token,{"commitment":"confirmed"}]},timeout=10).json()
        b=requests.post(SOL_RPC,json={"jsonrpc":"2.0","id":1,"method":"getTokenSupply","params":[token,{"commitment":"confirmed"}]},timeout=10).json()
        vals=a["result"]["value"];supply=float(b["result"]["value"].get("uiAmount") or 0)
        ps=[float(x.get("uiAmount") or 0)/supply*100 for x in vals] if supply else []
        return {"top1":sum(ps[:1]),"top10":sum(ps[:10])}
    except:return {"top1":None,"top10":None}

def gem_data(token,meta):
    try:
        ps=requests.get(f"{DEX}/token-pairs/v1/solana/{token}",timeout=10).json() or []
        valid=[]
        for p in ps:
            if (p.get("baseToken") or {}).get("address")!=token:continue
            liq=float((p.get("liquidity") or {}).get("usd") or 0)
            if liq>0 and p.get("priceUsd"):valid.append(p)
        if not valid:return None
        p=max(valid,key=lambda x:float((x.get("liquidity") or {}).get("usd") or 0))
        liq=float((p.get("liquidity") or {}).get("usd") or 0);mc=float(p.get("marketCap") or p.get("fdv") or 0)
        vol=p.get("volume") or {};tx=p.get("txns") or {};pc=p.get("priceChange") or {}
        h=tx.get("h1") or {};m=tx.get("m5") or {};b=int(h.get("buys") or 0);sel=int(h.get("sells") or 0);b5=int(m.get("buys") or 0);s5=int(m.get("sells") or 0)
        ts=p.get("pairCreatedAt");age=999
        if ts:
            ts=float(ts)/(1000 if float(ts)>1e10 else 1);age=max(0,(datetime.now(timezone.utc).timestamp()-ts)/60)
        c=gem_concentration(token);base=p.get("baseToken") or {};info=p.get("info") or {}
        return {"address":token,"symbol":base.get("symbol",""),"pair":p.get("pairAddress"),"age":age,"liq":liq,"mc":mc,"v1":float(vol.get("h1") or 0),"v5":float(vol.get("m5") or 0),"buys":b,"sells":sel,"ratio":(b+1)/(sel+1),"ratio5":(b5+1)/(s5+1),"p1":float(pc.get("h1") or 0),"p5":float(pc.get("m5") or 0),"top1":c["top1"],"top10":c["top10"],"socials":len(info.get("socials") or []),"websites":len(info.get("websites") or []),"source":meta.get("source"),"boost":meta.get("boost",False)}
    except:return None

def gem_rank(m,old=None):
    score=25;good=[];flags=[]
    if 2<=m["age"]<=15:score+=8;good.append("very early")
    elif 15<m["age"]<=60:score+=12;good.append("early + some history")
    elif 60<m["age"]<=180:score+=5
    elif m["age"]<2:score-=8;flags.append("too new to trust")
    if m["liq"]>=100000:score+=14;good.append("strong liquidity")
    elif m["liq"]>=50000:score+=10
    elif m["liq"]>=20000:score+=5
    else:score-=18;flags.append("thin liquidity")
    if 10000<=m["mc"]<=150000:score+=10;good.append("micro-cap")
    elif m["mc"]<=500000:score+=6
    elif m["mc"]>1500000:score-=6
    if 1.3<=m["ratio"]<=2.7:score+=10;good.append("healthy buy pressure")
    elif m["ratio"]>4:score-=8;flags.append("extreme one-sided flow")
    elif m["ratio"]<.9:score-=12;flags.append("sell pressure")
    if 1.1<=m["ratio5"]<=2.8:score+=7
    if m["buys"]+m["sells"]>=100:score+=5
    if m["v1"]>=50000:score+=7
    elif m["v1"]>=15000:score+=4
    if -3<=m["p5"]<=8:score+=5
    elif m["p5"]>20:score-=12;flags.append("5m vertical pump")
    if m["p1"]>100:score-=10;flags.append("already exploded")
    if m["top10"] is not None:
        if m["top10"]<=20:score+=12;good.append("low top-10 concentration")
        elif m["top10"]<=35:score+=5
        elif m["top10"]>=60:score-=25;flags.append("top-10 concentration extreme")
        elif m["top10"]>=45:score-=14;flags.append("top-10 concentration high")
    else:score-=5;flags.append("concentration unverified")
    if m["top1"] is not None and m["top1"]>=20:score-=15;flags.append("single account >=20%")
    if m["socials"]>=2:score+=3
    if m["websites"]>=1:score+=2
    if m["boost"]:flags.append("paid DEX boost — not organic proof")
    if old:
        if m["liq"]>old.get("liq",0)*1.20:score+=8;good.append("liquidity +20% vs prior scan")
        elif old.get("liq",0)>0 and m["liq"]<old["liq"]*.80:score-=10;flags.append("liquidity falling")
        if m["v1"]>old.get("v1",0)*1.25:score+=7;good.append("volume accelerating")
        if m["buys"]>old.get("buys",0)*1.20:score+=5;good.append("buyers accelerating")
    score=round(max(0,min(100,score)))
    status="🔥 HIGH PRIORITY" if score>=80 and not any(x in " ".join(flags) for x in ["extreme","thin liquidity","single account"]) else "👀 CONFIRMING" if score>=65 else "🟡 WATCH" if score>=45 else "🔴 AVOID"
    return score,status,good,flags

d=load()
st.title("📡 Multi-Asset Radar — V5")
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

if d.get("version")==5 and not breaker:
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
    st.subheader("💎 Early Gems — Watch First, Buy Never Automatically")
    st.caption("Ranks very-new Solana coins. HIGH PRIORITY means stronger early confirmation, not guaranteed profit.")
    if BIRD_KEY:st.success("Birdeye fresh-listing connection detected.")
    else:st.info("Zero-key mode uses DEX Screener discovery. Birdeye can later add a true fresh-listing feed and deeper holder intelligence.")
    if st.button("SCAN EARLY GEMS NOW",use_container_width=True):st.cache_data.clear()
    found=[]
    for token,meta in list(gem_universe().items())[:45]:
        m=gem_data(token,meta)
        if not m or m["age"]>360 or (m["mc"]>0 and m["mc"]>2000000):continue
        old=d["gem_snapshots"].get(token);score,status,good,flags=gem_rank(m,old)
        found.append((score,status,m,good,flags))
        d["gem_snapshots"][token]={"liq":m["liq"],"mc":m["mc"],"v1":m["v1"],"buys":m["buys"],"time":now()}
        d["gem_watchlist"][token]={"score":score,"status":status,"symbol":m["symbol"],"time":now()}
    save(d);found.sort(key=lambda x:x[0],reverse=True)
    if not found:st.warning("No micro-cap launches in the current discovery batch passed the age/market-cap scope.")
    for score,status,m,good,flags in found[:15]:
        top10=f"{m['top10']:.1f}%" if m["top10"] is not None else "unverified"
        st.markdown(f"### {status} — ${m['symbol']} — {score}/100")
        st.write(f"**Age:** {m['age']:.1f} min • **MCap:** {money(m['mc'])} • **Liquidity:** {money(m['liq'])} • **1h volume:** {money(m['v1'])}")
        st.write(f"**Buys/Sells:** {m['buys']}/{m['sells']} • **B/S:** {m['ratio']:.2f} • **5m:** {m['p5']:+.1f}% • **1h:** {m['p1']:+.1f}% • **Top 10:** {top10}")
        st.write("✅ "+(" • ".join(good[:5]) if good else "No strong confirmations yet"))
        st.write("⚠️ "+(" • ".join(flags[:5]) if flags else "No major flags detected by available data"))
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
    st.markdown("**Stocks/ETFs:** daily trend-following, up to ~30 days.\n\n**Core crypto:** established liquid assets, up to ~14 days.\n\n**Meme coins:** maximum 10% portfolio exposure and ~1% sizing per entry.\n\nV5 keeps the diversified portfolio logic and adds Early Gems as a separate WATCH-FIRST scanner. Early Gems does not auto-buy. No strategy can guarantee profit.")
with tabs[7]:
    st.warning("Reset starts a clean $10,000 PAPER portfolio.")
    ok=st.checkbox("Reset V5")
    if st.button("RESET V5 TO $10,000",disabled=not ok):save(fresh());st.rerun()

if auto:
    time.sleep(secs);st.cache_data.clear();st.rerun()
