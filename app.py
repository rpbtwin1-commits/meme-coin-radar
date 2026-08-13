
import os, time, math, json, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv, set_key

APP_DIR = Path(__file__).resolve().parent
ENV_FILE = APP_DIR / ".env"
load_dotenv(ENV_FILE)

BIRDEYE_BASE = "https://public-api.birdeye.so"
DEX_BASE = "https://api.dexscreener.com"
SOLANA_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

def key(name):
    return os.getenv(name, "").strip()


st.set_page_config(page_title="Meme Coin Radar", page_icon="📡", layout="wide")

st.markdown("""
<style>
/* Mobile-first tuning */
.block-container {padding-top: 1rem; padding-bottom: 4rem; max-width: 1400px;}
[data-testid="stMetric"] {
    border: 1px solid rgba(128,128,128,.22);
    border-radius: 14px;
    padding: .75rem;
}
div.stButton > button, div.stLinkButton > a {
    min-height: 46px;
    border-radius: 12px;
    font-weight: 700;
}
div[data-baseweb="tab-list"] {gap: 4px; overflow-x: auto;}
button[data-baseweb="tab"] {min-height: 44px; white-space: nowrap;}
code {word-break: break-all; white-space: pre-wrap;}
@media (max-width: 700px) {
    .block-container {padding-left: .75rem; padding-right: .75rem; padding-top: .5rem;}
    h1 {font-size: 1.75rem !important;}
    h2 {font-size: 1.35rem !important;}
    h3 {font-size: 1.15rem !important;}
    [data-testid="stMetricValue"] {font-size: 1.25rem;}
    [data-testid="stMetricLabel"] {font-size: .78rem;}
    div[data-testid="stHorizontalBlock"] {gap: .4rem;}
}
</style>
""", unsafe_allow_html=True)


def clamp(x, lo=0, hi=100): return max(lo, min(hi, x))

def fmt_money(v):
    try: v=float(v)
    except: return "—"
    if abs(v)>=1e9:return f"${v/1e9:.2f}B"
    if abs(v)>=1e6:return f"${v/1e6:.2f}M"
    if abs(v)>=1e3:return f"${v/1e3:.1f}K"
    return f"${v:,.2f}"

def fmt_pct(v):
    try:return f"{float(v):.1f}%"
    except:return "—"

def age_string(ts):
    if not ts:return "—"
    try:
        ts=float(ts)
        if ts>1e10:ts/=1000
        s=max(0,datetime.now(timezone.utc).timestamp()-ts)
        if s<60:return f"{int(s)} sec"
        if s<3600:return f"{int(s/60)} min"
        if s<86400:return f"{s/3600:.1f} hr"
        return f"{s/86400:.1f} d"
    except:return "—"

def get(url, headers=None, params=None, timeout=12):
    try:
        r=requests.get(url,headers=headers or {},params=params or {},timeout=timeout)
        r.raise_for_status()
        return r.json(),None
    except Exception as e:return None,str(e)

def post(url, payload, timeout=12):
    try:
        r=requests.post(url,json=payload,timeout=timeout,headers={"Content-Type":"application/json"})
        r.raise_for_status()
        return r.json(),None
    except Exception as e:return None,str(e)

def bird_headers(): return {"X-API-KEY":key("BIRDEYE_API_KEY"),"x-chain":"solana"}

@st.cache_data(ttl=12,show_spinner=False)
def new_candidates():
    # Best mode: true fresh Birdeye listings.
    if key("BIRDEYE_API_KEY"):
        data,err=get(f"{BIRDEYE_BASE}/defi/v2/tokens/new_listing",headers=bird_headers(),
                     params={"limit":20,"meme_platform_enabled":"true"})
        if not err:
            d=(data or {}).get("data",{})
            items=d.get("items",[]) if isinstance(d,dict) else (d or [])
            if items:
                return [{"address":x.get("address") or x.get("tokenAddress"),
                         "symbol":x.get("symbol",""),"name":x.get("name",""),
                         "time":x.get("liquidityAddedAt") or x.get("createdAt") or x.get("blockUnixTime"),
                         "source":"Birdeye Fresh Listing"} for x in items],None
    # Zero-key discovery feed: latest DexScreener token profiles + latest boosted tokens.
    merged={}
    for endpoint,label in [("/token-profiles/latest/v1","DexScreener latest"),
                           ("/token-boosts/latest/v1","DexScreener boosted")]:
        data,err=get(DEX_BASE+endpoint)
        if not err:
            for x in data or []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):
                    a=x["tokenAddress"]
                    merged[a]={"address":a,"symbol":"","name":x.get("description","")[:45],
                               "time":None,"source":label}
    return list(merged.values())[:30],None

@st.cache_data(ttl=10,show_spinner=False)
def dex(address):
    data,err=get(f"{DEX_BASE}/token-pairs/v1/solana/{address}")
    if err:return None,err
    pairs=data if isinstance(data,list) else (data or {}).get("pairs",[])
    if not pairs:return None,"Not indexed on DexScreener yet."
    return max(pairs,key=lambda p:float((p.get("liquidity") or {}).get("usd") or 0)),None

@st.cache_data(ttl=20,show_spinner=False)
def solana_concentration(address):
    # Works without an account/API key. Largest token accounts are not necessarily
    # unique beneficial owners, so label it account concentration, not "unique wallets."
    largest,err1=post(SOLANA_RPC,{"jsonrpc":"2.0","id":1,"method":"getTokenLargestAccounts",
                                 "params":[address,{"commitment":"confirmed"}]})
    supply,err2=post(SOLANA_RPC,{"jsonrpc":"2.0","id":1,"method":"getTokenSupply",
                                "params":[address,{"commitment":"confirmed"}]})
    if err1 or err2 or "result" not in (largest or {}) or "result" not in (supply or {}):
        return {},err1 or err2 or "Solana RPC did not return token concentration."
    vals=(largest["result"]["value"] or [])
    s=float(supply["result"]["value"].get("uiAmount") or 0)
    rows=[]
    for x in vals:
        amt=float(x.get("uiAmount") or 0)
        rows.append({"Token Account":x.get("address",""),"Amount":amt,
                     "% Supply":(amt/s*100 if s else None)})
    return {
        "supply":s,
        "top1":sum(r["% Supply"] or 0 for r in rows[:1]),
        "top5":sum(r["% Supply"] or 0 for r in rows[:5]),
        "top10":sum(r["% Supply"] or 0 for r in rows[:10]),
        "top20":sum(r["% Supply"] or 0 for r in rows[:20]),
        "rows":rows,
        "source":"Solana RPC token-account concentration"
    },None

@st.cache_data(ttl=30,show_spinner=False)
def bird(address, endpoint, params=None):
    if not key("BIRDEYE_API_KEY"):return {}, "Birdeye key not configured."
    data,err=get(BIRDEYE_BASE+endpoint,headers=bird_headers(),params={"address":address,**(params or {})})
    return ((data or {}).get("data") or {}),err

@st.cache_data(ttl=30,show_spinner=False)
def overview(address):return bird(address,"/defi/token_overview",{"frames":"1m,5m,15m,1h,24h"})

@st.cache_data(ttl=30,show_spinner=False)
def security(address):return bird(address,"/defi/token_security")

@st.cache_data(ttl=30,show_spinner=False)
def holders(address):
    if not key("BIRDEYE_API_KEY"):return {}, "Birdeye key not configured."
    data,err=get(f"{BIRDEYE_BASE}/defi/v3/token/holder",headers=bird_headers(),
                 params={"address":address,"mode":"wallet","limit":20,"get_holder_infos":"true"})
    return ((data or {}).get("data") or {}),err

@st.cache_data(ttl=45,show_spinner=False)
def x_social(address,symbol):
    if not key("X_BEARER_TOKEN"):return {"available":False},None
    terms=[f'"{address}"']
    if symbol:terms += [f'"${symbol}"',f'"{symbol}"']
    data,err=get("https://api.x.com/2/tweets/search/recent",
        headers={"Authorization":f'Bearer {key("X_BEARER_TOKEN")}'},
        params={"query":"("+" OR ".join(terms)+") -is:retweet","max_results":50,
                "tweet.fields":"created_at,public_metrics,author_id",
                "expansions":"author_id","user.fields":"username,name,verified,public_metrics"})
    if err:return {"available":False,"error":err},err
    users={u["id"]:u for u in data.get("includes",{}).get("users",[])}
    posts=data.get("data",[])
    eng=0; creators=[]; seen=set(); maxf=0
    for p in posts:
        m=p.get("public_metrics",{})
        eng += m.get("like_count",0)+2*m.get("retweet_count",0)+1.5*m.get("reply_count",0)+1.5*m.get("quote_count",0)
        u=users.get(p.get("author_id"),{}); f=(u.get("public_metrics") or {}).get("followers_count",0); maxf=max(maxf,f)
        if f>=5000 and u.get("id") not in seen:
            seen.add(u.get("id")); creators.append({"Creator":"@"+u.get("username",""),"Followers":f,"Verified":u.get("verified",False)})
    ss=clamp(len(posts)*1.2+math.log10(max(1,eng))*12+len(creators)*7+math.log10(max(1,maxf))*4)
    return {"available":True,"posts":len(posts),"engagement":round(eng),"creator_count":len(creators),
            "social_score":round(ss),"creators":sorted(creators,key=lambda z:z["Followers"],reverse=True)},None

def first(d,*ks,default=None):
    for k in ks:
        if isinstance(d,dict) and d.get(k) is not None:return d[k]
    return default

def yes(v):
    if isinstance(v,bool):return v
    return str(v).lower() in {"1","true","yes","enabled"} if v is not None else False

def scan(address):
    p,perr=dex(address)
    o,_=overview(address)
    sec,_=security(address)
    hs,_=holders(address)
    conc,cerr=solana_concentration(address)
    base=(p or {}).get("baseToken",{})
    symbol=base.get("symbol") or o.get("symbol") or ""
    name=base.get("name") or o.get("name") or ""
    liq=first(o,"liquidity",default=((p or {}).get("liquidity") or {}).get("usd"))
    mc=first(o,"marketCap","mc",default=(p or {}).get("marketCap") or (p or {}).get("fdv"))
    hcount=first(o,"holder","holderCount",default=None)
    # Prefer Birdeye unique-wallet concentration if supplied, otherwise public Solana token-account concentration.
    top10=first(hs,"top10HoldPercent",default=None)
    try:
        top10=float(top10)
        if top10<=1:top10*=100
    except: top10=conc.get("top10")
    pair_created=(p or {}).get("pairCreatedAt")
    tx=(p or {}).get("txns",{}); vol=(p or {}).get("volume",{}); ch=(p or {}).get("priceChange",{})
    h1=tx.get("h1",{}); buys=h1.get("buys",0); sells=h1.get("sells",0); trades=buys+sells
    v1=float(vol.get("h1") or 0); pc1=float(ch.get("h1") or 0)

    risk=18; reasons=[]
    if top10 is not None:
        if top10>=70:risk+=32;reasons.append(f"Top 10 token accounts/wallets control {top10:.1f}%")
        elif top10>=50:risk+=23;reasons.append(f"Top 10 concentration is {top10:.1f}%")
        elif top10>=35:risk+=13;reasons.append(f"Top 10 concentration is {top10:.1f}%")
        elif top10<=20:risk-=7
    else:risk+=8;reasons.append("Holder concentration could not be verified")
    L=float(liq or 0)
    if L<5000:risk+=22;reasons.append("Liquidity below $5K")
    elif L<20000:risk+=13;reasons.append("Liquidity below $20K")
    elif L>=100000:risk-=8
    for val,pts,msg in [
        (first(sec,"mintable","isMintable"),20,"Mint authority/mintability risk"),
        (first(sec,"freezable","isFreezable"),22,"Freeze authority risk"),
        (first(sec,"honeypot","isHoneypot"),40,"Honeypot signal"),
        (first(sec,"fakeToken","isFakeToken"),40,"Fake/imitation token signal"),
        (first(sec,"mutableInfo","isMutable"),5,"Mutable token metadata"),
    ]:
        if yes(val):risk+=pts;reasons.append(msg)
    if not key("BIRDEYE_API_KEY"):
        risk+=5;reasons.append("Deep security checks not connected yet")
    if pair_created:
        ts=float(pair_created)/(1000 if float(pair_created)>1e10 else 1)
        hrs=(datetime.now(timezone.utc).timestamp()-ts)/3600
        if hrs<.25:risk+=10;reasons.append("Pool is under 15 minutes old")
        elif hrs<1:risk+=6;reasons.append("Pool is under 1 hour old")
        elif hrs>24:risk-=3
    if trades<10:risk+=6;reasons.append("Very low 1-hour trading activity")
    risk=round(clamp(risk))
    social,_=x_social(address,symbol)
    ss=social.get("social_score") if social.get("available") else None
    activity=clamp(math.log10(max(v1,1))*14+min(trades,100)*.35)
    lscore=clamp(math.log10(max(L,1))*18-38)
    momentum=clamp(50+max(-50,min(50,pc1)))
    social_component=ss if ss is not None else 45
    opp=round(clamp(.27*activity+.23*lscore+.18*momentum+.17*social_component+.15*(100-risk)))
    return locals()

def risk_label(n):
    return "🔴 EXTREME" if n>=75 else "🟠 HIGH" if n>=55 else "🟡 MEDIUM" if n>=35 else "🟢 LOWER"

st.title("📡 Meme Coin Radar")
st.caption("📱 Mobile radar • new launches • rug risk • holders • liquidity • momentum")

with st.sidebar:
    st.header("Status")
    st.success("DEX market data: ON")
    st.success("Solana concentration: ON")
    st.write("Birdeye deep scan:", "✅ ON" if key("BIRDEYE_API_KEY") else "◻️ Optional")
    st.write("X social scan:", "✅ ON" if key("X_BEARER_TOKEN") else "◻️ Optional")
    auto=st.toggle("Auto refresh",False)
    seconds=st.slider("Refresh seconds",10,120,20,5)
    st.caption("No API keys are required to start scanning. Optional keys unlock deeper coverage.")

tabs=st.tabs(["🚨 Radar","🔎 Inspect Token","⚙️ Optional Upgrades","🧠 How Scores Work"])

with tabs[0]:
    a,b,c=st.columns(3)
    min_liq=a.number_input("Min liquidity ($)",0,10_000_000,5000,1000)
    max_risk=b.slider("Max rug risk",0,100,70)
    min_opp=c.slider("Min opportunity",0,100,0)
    if st.button("Refresh Radar",use_container_width=True):
        st.cache_data.clear();st.rerun()
    candidates,_=new_candidates()
    rows=[]; prog=st.progress(0) if candidates else None
    for i,x in enumerate(candidates):
        if not x.get("address"):continue
        try:m=scan(x["address"])
        except Exception:continue
        if prog:prog.progress((i+1)/len(candidates))
        if float(m["liq"] or 0)<min_liq or m["risk"]>max_risk or m["opp"]<min_opp:continue
        rows.append({"Token":f'{m["name"]} (${m["symbol"]})',"Age":age_string(m["pair_created"]),
                     "Liquidity":fmt_money(m["liq"]),"MCap":fmt_money(m["mc"]),
                     "1h Vol":fmt_money(m["v1"]),"1h Trades":m["trades"],
                     "Top 10":fmt_pct(m["top10"]),"Rug Risk":m["risk"],"Opportunity":m["opp"],
                     "Social":m["ss"] if m["ss"] is not None else "—","Address":x["address"]})
    if prog:prog.empty()
    if rows:
        df=pd.DataFrame(rows).sort_values(["Opportunity","Rug Risk"],ascending=[False,True])
        st.dataframe(df,use_container_width=True,hide_index=True,column_config={
            "Rug Risk":st.column_config.ProgressColumn("Rug Risk",min_value=0,max_value=100),
            "Opportunity":st.column_config.ProgressColumn("Opportunity",min_value=0,max_value=100)})
        chosen=st.selectbox("Send a coin to Inspector",df["Address"].tolist(),
                            format_func=lambda q:next(r["Token"] for r in rows if r["Address"]==q))
        if st.button("Inspect selected coin"):
            st.session_state["inspect"]=chosen; st.info("Open the Inspect Token tab.")
    else:
        st.warning("Nothing in the current discovery batch passes those filters. Lower the filters or refresh.")

    if not key("BIRDEYE_API_KEY"):
        st.info("Zero-setup mode uses DexScreener discovery. Add Birdeye in Optional Upgrades for a dedicated new-listing feed.")

with tabs[1]:
    addr=st.text_input("Token mint address",value=st.session_state.get("inspect",""),placeholder="Paste a Solana token address")
    if addr:
        with st.spinner("Scanning..."):m=scan(addr)
        st.markdown(f"## {m['name']} {('$'+m['symbol']) if m['symbol'] else ''}")
        st.code(addr)
        c1,c2,c3,c4,c5=st.columns(5)
        c1.metric("Opportunity",f"{m['opp']}/100")
        c2.metric("Rug Risk",f"{m['risk']}/100",risk_label(m["risk"]))
        c3.metric("Liquidity",fmt_money(m["liq"]))
        c4.metric("MCap / FDV",fmt_money(m["mc"]))
        c5.metric("Age",age_string(m["pair_created"]))
        c1,c2,c3,c4,c5=st.columns(5)
        c1.metric("Top 1",fmt_pct(m["conc"].get("top1")))
        c2.metric("Top 5",fmt_pct(m["conc"].get("top5")))
        c3.metric("Top 10",fmt_pct(m["top10"]))
        c4.metric("1h Buys / Sells",f"{m['buys']} / {m['sells']}")
        c5.metric("1h Volume",fmt_money(m["v1"]))
        st.subheader("🚩 Why the rug score moved")
        for r in m["reasons"]:st.write("• "+r)

        t1,t2,t3,t4=st.tabs(["Concentration","Social / Creators","Trading","Security"])
        with t1:
            if m["conc"].get("rows"):
                st.caption("Zero-key fallback shows largest SPL token accounts. One owner can control multiple token accounts, so this is not proof of unique-wallet ownership.")
                st.dataframe(pd.DataFrame(m["conc"]["rows"]),use_container_width=True,hide_index=True)
            if m["hs"].get("items"):
                st.markdown("**Birdeye wallet-enriched holders**")
                st.dataframe(pd.DataFrame(m["hs"]["items"]),use_container_width=True,hide_index=True)
        with t2:
            s=m["social"]
            if s.get("available"):
                a,b,c=st.columns(3);a.metric("Score",f"{s['social_score']}/100");b.metric("Posts",s["posts"]);c.metric("Creators ≥5K",s["creator_count"])
                if s["creators"]:st.dataframe(pd.DataFrame(s["creators"]),use_container_width=True,hide_index=True)
            else:
                st.info("X creator-level engagement is optional. Add an X bearer token in Optional Upgrades.")
        with t3:
            p=m["p"] or {}; tx=p.get("txns",{}); vol=p.get("volume",{}); ch=p.get("priceChange",{})
            rr=[]
            for f in ["m5","h1","h6","h24"]:
                q=tx.get(f,{})
                rr.append({"Window":f,"Buys":q.get("buys",0),"Sells":q.get("sells",0),"Volume":vol.get(f),"Price %":ch.get(f)})
            st.dataframe(pd.DataFrame(rr),use_container_width=True,hide_index=True)
            if p.get("url"):st.link_button("Open on DexScreener",p["url"])
        with t4:
            if m["sec"]:st.json(m["sec"])
            else:st.info("Deep mint/freeze/security fields unlock when Birdeye is connected.")

with tabs[2]:
    st.subheader("You do not need these to start")
    st.write("The radar works immediately for DEX data and Solana concentration. These optional connections improve coverage.")
    with st.form("keys"):
        bird_key=st.text_input("Birdeye API key",value=key("BIRDEYE_API_KEY"),type="password")
        x_key=st.text_input("X API bearer token",value=key("X_BEARER_TOKEN"),type="password")
        rpc=st.text_input("Custom Solana RPC URL (optional)",value=os.getenv("SOLANA_RPC_URL",""))
        save=st.form_submit_button("Save connections")
    if save:
        if not ENV_FILE.exists():ENV_FILE.write_text("")
        set_key(str(ENV_FILE),"BIRDEYE_API_KEY",bird_key)
        set_key(str(ENV_FILE),"X_BEARER_TOKEN",x_key)
        set_key(str(ENV_FILE),"SOLANA_RPC_URL",rpc)
        os.environ["BIRDEYE_API_KEY"]=bird_key;os.environ["X_BEARER_TOKEN"]=x_key
        if rpc:os.environ["SOLANA_RPC_URL"]=rpc
        st.cache_data.clear()
        st.success("Saved locally on this computer. Restart the radar once for every connection to be picked up everywhere.")

with tabs[3]:
    st.markdown("""
### Rug Risk — 0 to 100
Higher is worse. The score increases for concentrated ownership, thin liquidity, mint/freeze authority, honeypot/fake-token signals, extreme newness, low trade activity, and missing deep-security data.

### Opportunity — 0 to 100
A ranking tool, not a profit prediction:
- **27%** trading activity
- **23%** liquidity depth
- **18%** price momentum
- **17%** social/creator momentum
- **15%** inverse rug risk

### Important limitation
A sophisticated team can split holdings across related addresses. Top-account percentages alone cannot prove independent ownership. Deep wallet-clustering/funding-source analysis is the next layer for detecting that behavior.
""")

if auto:
    time.sleep(seconds);st.cache_data.clear();st.rerun()
