import streamlit as st, requests, time, math, json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

st.set_page_config(page_title="Meme Coin Radar + Paper Trader",page_icon="📡",layout="wide")
DEX="https://api.dexscreener.com"; DB=Path("paper_portfolio.json")

def fresh():
    return {"start":1000.0,"cash":1000.0,"positions":[],"trades":[],"high":1000.0}
def load():
    if "pf" in st.session_state:return st.session_state.pf
    try:d=json.loads(DB.read_text())
    except:d=fresh()
    st.session_state.pf=d;return d
def save(d):
    st.session_state.pf=d
    try:DB.write_text(json.dumps(d,indent=2))
    except:pass
def money(x):return f"${x:,.2f}"
def now():return datetime.now(timezone.utc).isoformat()

@st.cache_data(ttl=8)
def pair(a):
    try:
        r=requests.get(f"{DEX}/token-pairs/v1/solana/{a}",timeout=10);r.raise_for_status();q=r.json() or []
        return max(q,key=lambda x:float((x.get("liquidity") or {}).get("usd") or 0)) if q else None
    except:return None

@st.cache_data(ttl=12)
def discovery():
    out={}
    for ep in ["/token-profiles/latest/v1","/token-boosts/latest/v1"]:
        try:
            for x in requests.get(DEX+ep,timeout=10).json() or []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):out[x["tokenAddress"]]=1
        except:pass
    return list(out)[:30]

def scan(a):
    p=pair(a)
    if not p:return None
    px=float(p.get("priceUsd") or 0);liq=float((p.get("liquidity") or {}).get("usd") or 0)
    vol=float((p.get("volume") or {}).get("h1") or 0);pc=float((p.get("priceChange") or {}).get("h1") or 0)
    h=(p.get("txns") or {}).get("h1") or {};m=(p.get("txns") or {}).get("m5") or {}
    b=int(h.get("buys") or 0);s=int(h.get("sells") or 0);b5=int(m.get("buys") or 0);s5=int(m.get("sells") or 0)
    ts=p.get("pairCreatedAt");age=999
    if ts:
        ts=float(ts)/(1000 if float(ts)>1e10 else 1);age=(datetime.now(timezone.utc).timestamp()-ts)/3600
    risk=20+(35 if liq<5000 else 18 if liq<20000 else -8 if liq>=100000 else 0)+(10 if age<.25 else 5 if age<1 else 0)+(12 if b+s<15 else 0)+(12 if (b+1)/(s+1)<.65 else 0)
    risk=round(max(0,min(100,risk)))
    activity=max(0,min(100,math.log10(max(vol,1))*14+min(b+s,120)*.3))
    ls=max(0,min(100,math.log10(max(liq,1))*18-38));mom=max(0,min(100,50+max(-50,min(50,pc))))
    opp=round(max(0,min(100,.35*activity+.30*ls+.20*mom+.15*(100-risk))))
    base=p.get("baseToken") or {}
    return {"address":a,"symbol":base.get("symbol",""),"name":base.get("name",""),"price":px,"liq":liq,"vol":vol,"pc":pc,"b":b,"s":s,"b5":b5,"s5":s5,"age":age,"risk":risk,"opp":opp}

def equity(d):
    e=d["cash"];marks=[]
    for x in d["positions"]:
        m=scan(x["address"]);px=m["price"] if m and m["price"] else x["entry"]
        v=x["qty"]*px;e+=v;marks.append((x,m,px,v))
    return e,marks

d=load()
st.title("📡 Meme Coin Radar + Paper Trader")
st.caption("Simulated $1,000 account — no real money or wallet permissions.")

with st.sidebar:
    st.header("Strategy")
    rmax=st.slider("Max risk",0,100,45); omin=st.slider("Min opportunity",0,100,62)
    minliq=st.number_input("Min liquidity ($)",5000,1000000,20000,5000)
    size=st.slider("Position size %",0.5,10.0,2.0,.5);maxpos=st.slider("Max positions",1,10,5)
    stop=st.slider("Stop loss %",5,50,18);take=st.slider("Take profit %",10,200,35);hold=st.slider("Max hold hours",1,72,12)
    auto=st.toggle("Auto scan while open",False);secs=st.slider("Refresh seconds",15,120,30,5)

actions=[]
# exits
for x in list(d["positions"]):
    m=scan(x["address"])
    if not m or not m["price"]:continue
    ch=(m["price"]/x["entry"]-1)*100
    hrs=(datetime.now(timezone.utc)-datetime.fromisoformat(x["time"])).total_seconds()/3600
    why="stop loss" if ch<=-stop else "take profit" if ch>=take else "max hold" if hrs>=hold else "risk spike" if m["risk"]>=80 else None
    if why:
        proceeds=x["qty"]*m["price"];pnl=proceeds-x["cost"];d["cash"]+=proceeds
        d["trades"].append({**x,"exit_time":now(),"exit":m["price"],"pnl":pnl,"pnl_pct":pnl/x["cost"]*100,"exit_reason":why})
        d["positions"].remove(x);actions.append(f"SELL ${x['symbol']} ({why})")
# entries
held={x["address"] for x in d["positions"]};recent={x["address"] for x in d["trades"][-20:]}
for a in discovery():
    if len(d["positions"])>=maxpos:break
    if a in held or a in recent:continue
    m=scan(a)
    if not m or not m["price"]:continue
    ok=m["risk"]<=rmax and m["opp"]>=omin and m["liq"]>=minliq and (m["b"]+1)/(m["s"]+1)>=1.2 and (m["b5"]+1)/(m["s5"]+1)>=1.05 and m["vol"]>=5000
    if ok:
        eq,_=equity(d);cost=min(d["cash"],eq*size/100)
        if cost<5:break
        d["cash"]-=cost;d["positions"].append({"address":a,"symbol":m["symbol"],"name":m["name"],"time":now(),"entry":m["price"],"qty":cost/m["price"],"cost":cost,"entry_risk":m["risk"],"entry_opp":m["opp"],"why":f"Opp {m['opp']}, risk {m['risk']}, B/S {m['b']}/{m['s']}, liq {money(m['liq'])}"})
        actions.append(f"BUY ${m['symbol']} for {money(cost)}")

eq,marks=equity(d);d["high"]=max(d.get("high",d["start"]),eq);save(d)
pnl=sum(x.get("pnl",0) for x in d["trades"]);wins=sum(x.get("pnl",0)>0 for x in d["trades"]);wr=100*wins/len(d["trades"]) if d["trades"] else 0
c1,c2,c3,c4=st.columns(4);c1.metric("Paper Equity",money(eq),f"{(eq/d['start']-1)*100:+.1f}%");c2.metric("Cash",money(d["cash"]));c3.metric("Realized P&L",money(pnl));c4.metric("Win Rate",f"{wr:.1f}%")
if actions:st.success(" • ".join(actions))

t1,t2,t3,t4=st.tabs(["🤖 Positions","🚨 Radar","📒 History","💾 Backup"])
with t1:
    if marks:
        rows=[]
        for x,m,px,v in marks:rows.append({"Coin":"$"+x["symbol"],"Cost":x["cost"],"Value":v,"P&L":v-x["cost"],"P&L %":(v/x["cost"]-1)*100,"Entry Risk":x["entry_risk"],"Entry Opp":x["entry_opp"],"Why bought":x["why"]})
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    else:st.info("No qualifying paper positions right now.")
    st.caption("Auto-trading checks run only while this Streamlit app is active. This version does not trade real money.")
with t2:
    rows=[]
    for a in discovery()[:20]:
        m=scan(a)
        if m:rows.append({"Coin":"$"+m["symbol"],"Age h":round(m["age"],2),"Liquidity":m["liq"],"1h Vol":m["vol"],"Buys":m["b"],"Sells":m["s"],"1h %":m["pc"],"Risk":m["risk"],"Opportunity":m["opp"],"Address":a})
    if rows:st.dataframe(pd.DataFrame(rows).sort_values(["Opportunity","Risk"],ascending=[False,True]),use_container_width=True,hide_index=True)
with t3:
    if d["trades"]:st.dataframe(pd.DataFrame(d["trades"]).iloc[::-1],use_container_width=True,hide_index=True)
    else:st.info("Closed paper trades will appear here.")
with t4:
    st.warning("Streamlit Community Cloud does not guarantee local-file persistence. Download backups until persistent cloud storage is connected.")
    st.download_button("Download portfolio backup",json.dumps(d,indent=2),"paper_portfolio_backup.json","application/json",use_container_width=True)
    f=st.file_uploader("Restore backup",type="json")
    if f and st.button("Restore"):
        save(json.load(f));st.rerun()
    if st.button("Reset to $1,000"):save(fresh());st.rerun()

if auto:
    time.sleep(secs);st.cache_data.clear();st.rerun()
