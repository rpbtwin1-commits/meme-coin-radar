import streamlit as st, requests, time, math, json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

st.set_page_config(page_title='Meme Coin Radar + Paper Trader', page_icon='📡', layout='wide')
DEX='https://api.dexscreener.com'; DB=Path('paper_portfolio.json')

def fresh(): return {'start':1000.0,'cash':1000.0,'positions':[],'trades':[],'high':1000.0}
def load():
    if 'pf' in st.session_state: return st.session_state.pf
    try: d=json.loads(DB.read_text())
    except: d=fresh()
    st.session_state.pf=d; return d
def save(d):
    st.session_state.pf=d
    try: DB.write_text(json.dumps(d,indent=2))
    except: pass
def money(x):
    try: return f'${float(x):,.2f}'
    except: return '—'
def now(): return datetime.now(timezone.utc).isoformat()

@st.cache_data(ttl=8, show_spinner=False)
def pair(a):
    try:
        r=requests.get(f'{DEX}/token-pairs/v1/solana/{a}',timeout=10); r.raise_for_status(); q=r.json() or []
        if isinstance(q,dict): q=q.get('pairs',[])
        return max(q,key=lambda x:float((x.get('liquidity') or {}).get('usd') or 0)) if q else None
    except: return None

@st.cache_data(ttl=12, show_spinner=False)
def discovery():
    out={}
    for ep in ['/token-profiles/latest/v1','/token-boosts/latest/v1']:
        try:
            for x in requests.get(DEX+ep,timeout=10).json() or []:
                if x.get('chainId')=='solana' and x.get('tokenAddress'): out[x['tokenAddress']]=1
        except: pass
    return list(out)[:30]

def scan(a):
    p=pair(a)
    if not p: return None
    px=float(p.get('priceUsd') or 0); liq=float((p.get('liquidity') or {}).get('usd') or 0); vol=float((p.get('volume') or {}).get('h1') or 0); pc=float((p.get('priceChange') or {}).get('h1') or 0)
    tx=p.get('txns') or {}; h=tx.get('h1') or {}; m=tx.get('m5') or {}
    b=int(h.get('buys') or 0); s=int(h.get('sells') or 0); b5=int(m.get('buys') or 0); s5=int(m.get('sells') or 0)
    ts=p.get('pairCreatedAt'); age=999
    if ts:
        ts=float(ts)/(1000 if float(ts)>1e10 else 1); age=max(0,(datetime.now(timezone.utc).timestamp()-ts)/3600)
    risk=20+(35 if liq<5000 else 18 if liq<20000 else -8 if liq>=100000 else 0)+(10 if age<.25 else 5 if age<1 else 0)+(12 if b+s<15 else 0)+(12 if (b+1)/(s+1)<.65 else 0)
    risk=round(max(0,min(100,risk)))
    activity=max(0,min(100,math.log10(max(vol,1))*14+min(b+s,120)*.3)); ls=max(0,min(100,math.log10(max(liq,1))*18-38)); mom=max(0,min(100,50+max(-50,min(50,pc))))
    opp=round(max(0,min(100,.35*activity+.30*ls+.20*mom+.15*(100-risk))))
    base=p.get('baseToken') or {}
    return {'address':a,'symbol':base.get('symbol',''),'name':base.get('name',''),'price':px,'liq':liq,'vol':vol,'pc':pc,'b':b,'s':s,'b5':b5,'s5':s5,'age':age,'risk':risk,'opp':opp,'bs':(b+1)/(s+1),'bs5':(b5+1)/(s5+1)}

def held_hours(start,end=None):
    try:
        a=datetime.fromisoformat(start); b=datetime.fromisoformat(end) if end else datetime.now(timezone.utc); return (b-a).total_seconds()/3600
    except: return None

def equity(d):
    e=d['cash']; marks=[]
    for x in d['positions']:
        m=scan(x['address']); px=m['price'] if m and m['price'] else x['entry']; v=x['qty']*px; e+=v; marks.append((x,m,px,v))
    return e,marks

def analytics_df(trades):
    rows=[]
    for t in trades:
        rows.append({'Coin':'$'+t.get('symbol',''),'Return %':float(t.get('pnl_pct',0)),'P&L':float(t.get('pnl',0)),'Entry Risk':t.get('entry_risk'),'Entry Opp':t.get('entry_opp'),'Liquidity':t.get('entry_liq'),'1h B/S':t.get('entry_bs'),'Age h':t.get('entry_age'),'1h Vol':t.get('entry_vol'),'Held h':held_hours(t.get('time'),t.get('exit_time')),'Exit Reason':t.get('exit_reason',''),'Win':1 if float(t.get('pnl',0))>0 else 0})
    return pd.DataFrame(rows)

def grouped(df,label,series):
    if df.empty: return pd.DataFrame()
    q=df.copy(); q['Bucket']=series
    o=q.groupby('Bucket',dropna=False,observed=False).agg(Trades=('Return %','count'),Win_Rate=('Win','mean'),Avg_Return=('Return %','mean'),Total_PnL=('P&L','sum')).reset_index(); o['Win_Rate']*=100
    return o.rename(columns={'Bucket':label})

d=load()
st.title('📡 Meme Coin Radar + Paper Trader')
st.caption('Simulated account only — now measuring which signals are actually working.')

with st.sidebar:
    st.header('Strategy')
    rmax=st.slider('Max risk',0,100,45); omin=st.slider('Min opportunity',0,100,62); minliq=st.number_input('Min liquidity ($)',5000,1000000,20000,5000)
    size=st.slider('Position size %',0.5,10.0,2.0,.5); maxpos=st.slider('Max positions',1,10,5); stop=st.slider('Stop loss %',5,50,18); take=st.slider('Take profit %',10,200,35); hold=st.slider('Max hold hours',1,72,12)
    auto=st.toggle('Auto scan while open',False); secs=st.slider('Refresh seconds',15,120,30,5)

actions=[]
for x in list(d['positions']):
    m=scan(x['address'])
    if not m or not m['price']: continue
    ch=(m['price']/x['entry']-1)*100; hrs=held_hours(x['time']) or 0
    why='stop loss' if ch<=-stop else 'take profit' if ch>=take else 'max hold' if hrs>=hold else 'risk spike' if m['risk']>=80 else None
    if why:
        proceeds=x['qty']*m['price']; pnl=proceeds-x['cost']; d['cash']+=proceeds
        d['trades'].append({**x,'exit_time':now(),'exit':m['price'],'pnl':pnl,'pnl_pct':pnl/x['cost']*100,'exit_reason':why,'exit_risk':m['risk'],'exit_opp':m['opp']})
        d['positions'].remove(x); actions.append(f"SELL ${x['symbol']} ({why})")

held={x['address'] for x in d['positions']}; recent={x['address'] for x in d['trades'][-20:]}
for a in discovery():
    if len(d['positions'])>=maxpos: break
    if a in held or a in recent: continue
    m=scan(a)
    if not m or not m['price']: continue
    if m['risk']<=rmax and m['opp']>=omin and m['liq']>=minliq and m['bs']>=1.20 and m['bs5']>=1.05 and m['vol']>=5000:
        eq,_=equity(d); cost=min(d['cash'],eq*size/100)
        if cost<5: break
        d['cash']-=cost
        d['positions'].append({'address':a,'symbol':m['symbol'],'name':m['name'],'time':now(),'entry':m['price'],'qty':cost/m['price'],'cost':cost,'entry_risk':m['risk'],'entry_opp':m['opp'],'entry_liq':m['liq'],'entry_bs':m['bs'],'entry_bs5':m['bs5'],'entry_age':m['age'],'entry_vol':m['vol'],'entry_pc':m['pc'],'why':f"Opp {m['opp']}, risk {m['risk']}, 1h B/S {m['b']}/{m['s']}, 5m B/S {m['b5']}/{m['s5']}, liq {money(m['liq'])}"})
        actions.append(f"BUY ${m['symbol']} for {money(cost)}")

eq,marks=equity(d); d['high']=max(d.get('high',d['start']),eq); save(d)
pnl=sum(x.get('pnl',0) for x in d['trades']); wins=sum(x.get('pnl',0)>0 for x in d['trades']); wr=100*wins/len(d['trades']) if d['trades'] else 0
c1,c2,c3,c4=st.columns(4); c1.metric('Paper Equity',money(eq),f"{(eq/d['start']-1)*100:+.1f}%"); c2.metric('Cash',money(d['cash'])); c3.metric('Realized P&L',money(pnl)); c4.metric('Win Rate',f'{wr:.1f}%',f"{len(d['trades'])} closed")
if actions: st.success(' • '.join(actions))

t1,t2,t3,t4,t5=st.tabs(['🤖 Positions','🚨 Radar','📒 History','📊 Analytics','💾 Backup'])
with t1:
    if marks:
        rows=[]
        for x,m,px,v in marks: rows.append({'Coin':'$'+x['symbol'],'Entry':x['entry'],'Now':px,'Cost':x['cost'],'Value':v,'P&L':v-x['cost'],'P&L %':(v/x['cost']-1)*100,'Risk':x['entry_risk'],'Opp':x['entry_opp'],'Held h':round(held_hours(x['time']) or 0,2),'Why bought':x['why']})
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    else: st.info('No qualifying paper positions right now.')
with t2:
    rows=[]
    for a in discovery()[:20]:
        m=scan(a)
        if m: rows.append({'Coin':'$'+m['symbol'],'Age h':round(m['age'],2),'Liquidity':m['liq'],'1h Vol':m['vol'],'Buys':m['b'],'Sells':m['s'],'1h B/S':round(m['bs'],2),'5m B/S':round(m['bs5'],2),'1h %':m['pc'],'Risk':m['risk'],'Opportunity':m['opp'],'Address':a})
    if rows: st.dataframe(pd.DataFrame(rows).sort_values(['Opportunity','Risk'],ascending=[False,True]),use_container_width=True,hide_index=True)
with t3:
    if d['trades']:
        hist=[]
        for t in reversed(d['trades']): hist.append({'Coin':'$'+t.get('symbol',''),'Entry':t.get('entry'),'Exit':t.get('exit'),'Return %':t.get('pnl_pct'),'P&L':t.get('pnl'),'Held h':held_hours(t.get('time'),t.get('exit_time')),'Entry Risk':t.get('entry_risk'),'Entry Opp':t.get('entry_opp'),'Exit Reason':t.get('exit_reason'),'Why bought':t.get('why')})
        st.dataframe(pd.DataFrame(hist),use_container_width=True,hide_index=True)
    else: st.info('Closed paper trades will appear here.')
with t4:
    df=analytics_df(d['trades'])
    # Older trades may not contain the newer analytics fields. Coerce missing/legacy values
    # to NaN instead of crashing pd.cut; they remain visible in History.
    for col in ['Entry Risk','Entry Opp','Liquidity','1h B/S','Age h','1h Vol','Held h','Return %','P&L']:
        if col in df.columns:
            df[col]=pd.to_numeric(df[col],errors='coerce')
    if len(df)<5: st.info(f"We only have {len(df)} closed trades. Analytics become more meaningful after roughly 20–50 trades. Older trades with missing entry metrics are preserved but excluded from the affected bucket table.")
    if not df.empty:
        a,b,c,e=st.columns(4); a.metric('Best trade',f"{df['Return %'].max():+.1f}%"); b.metric('Worst trade',f"{df['Return %'].min():+.1f}%"); c.metric('Avg return/trade',f"{df['Return %'].mean():+.1f}%"); e.metric('Avg hold',f"{df['Held h'].mean():.1f}h")
        st.markdown('### By entry risk')
        st.dataframe(grouped(df,'Risk bucket',pd.cut(df['Entry Risk'],[-1,29,39,49,59,100],labels=['<30','30–39','40–49','50–59','60+'])),use_container_width=True,hide_index=True)
        st.markdown('### By buy/sell pressure')
        st.dataframe(grouped(df,'1h B/S bucket',pd.cut(df['1h B/S'],[0,1.19,1.49,1.99,2.99,999],labels=['<1.2','1.2–1.49','1.5–1.99','2.0–2.99','3.0+'])),use_container_width=True,hide_index=True)
        st.markdown('### By coin age at entry')
        st.dataframe(grouped(df,'Age bucket',pd.cut(df['Age h'],[-1,.1667,.5,1,3,12,999],labels=['<10m','10–30m','30–60m','1–3h','3–12h','12h+'])),use_container_width=True,hide_index=True)
        st.markdown('### By liquidity')
        st.dataframe(grouped(df,'Liquidity bucket',pd.cut(df['Liquidity'],[0,19999,49999,99999,249999,999999999],labels=['<$20K','$20–50K','$50–100K','$100–250K','$250K+'])),use_container_width=True,hide_index=True)
        st.markdown('### By exit reason')
        ex=df.groupby('Exit Reason').agg(Trades=('Return %','count'),Win_Rate=('Win','mean'),Avg_Return=('Return %','mean'),Total_PnL=('P&L','sum')).reset_index(); ex['Win_Rate']*=100
        st.dataframe(ex,use_container_width=True,hide_index=True)
with t5:
    st.warning('Streamlit Community Cloud does not guarantee local-file persistence. Download backups until persistent storage is connected.')
    st.download_button('Download portfolio backup',json.dumps(d,indent=2),'paper_portfolio_backup.json','application/json',use_container_width=True)
    f=st.file_uploader('Restore backup',type='json')
    if f and st.button('Restore'): save(json.load(f)); st.rerun()
    if st.button('Reset to $1,000'): save(fresh()); st.rerun()

if auto:
    time.sleep(secs); st.cache_data.clear(); st.rerun()
