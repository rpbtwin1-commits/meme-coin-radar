import streamlit as st
import requests, time, math, json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

st.set_page_config(page_title='Meme Coin Radar V2', page_icon='📡', layout='wide')
DEX='https://api.dexscreener.com'; DB=Path('paper_portfolio.json')
S=requests.Session(); S.headers.update({'Accept':'application/json','User-Agent':'MemeCoinRadar/2.0'})

def fresh(): return {'version':2,'start':1000.0,'cash':1000.0,'positions':[],'trades':[],'high':1000.0,'blocked_quotes':[]}
def load():
    if 'pf' in st.session_state:return st.session_state.pf
    try:d=json.loads(DB.read_text())
    except:d=fresh()
    d.setdefault('blocked_quotes',[]); d.setdefault('high',d.get('start',1000.0)); st.session_state.pf=d; return d
def save(d):
    st.session_state.pf=d
    try:DB.write_text(json.dumps(d,indent=2))
    except:pass
def money(x):
    try:return f'${float(x):,.2f}'
    except:return '—'
def now():return datetime.now(timezone.utc).isoformat()
def ageh(ts):
    try:
        ts=float(ts)/(1000 if float(ts)>1e10 else 1); return max(0,(datetime.now(timezone.utc).timestamp()-ts)/3600)
    except:return 999

def get(url):
    try:r=S.get(url,timeout=12);r.raise_for_status();return r.json()
    except:return None

@st.cache_data(ttl=12,show_spinner=False)
def discovery():
    out={}
    for ep in ['/token-profiles/latest/v1','/token-boosts/latest/v1']:
        for x in get(DEX+ep) or []:
            if x.get('chainId')=='solana' and x.get('tokenAddress'):out[x['tokenAddress']]=1
    return list(out)[:35]

@st.cache_data(ttl=8,show_spinner=False)
def token_pairs(token):
    d=get(f'{DEX}/token-pairs/v1/solana/{token}')
    return (d.get('pairs',[]) if isinstance(d,dict) else d) or []

def choose_pair(token):
    good=[]
    for p in token_pairs(token):
        base=(p.get('baseToken') or {}).get('address'); pa=p.get('pairAddress'); px=p.get('priceUsd'); liq=float((p.get('liquidity') or {}).get('usd') or 0)
        if base==token and pa and px not in (None,'','0') and liq>0:good.append(p)
    return max(good,key=lambda p:float((p.get('liquidity') or {}).get('usd') or 0)) if good else None

def exact_pair(pa):
    d=get(f'{DEX}/latest/dex/pairs/solana/{pa}')
    ps=d.get('pairs',[]) if isinstance(d,dict) else (d or [])
    for p in ps:
        if p.get('pairAddress')==pa:return p
    return None

def parse(token,p):
    if not p or (p.get('baseToken') or {}).get('address')!=token:return None
    try:px=float(p.get('priceUsd') or 0)
    except:px=0
    if px<=0:return None
    liq=float((p.get('liquidity') or {}).get('usd') or 0); vol=float((p.get('volume') or {}).get('h1') or 0); pc=float((p.get('priceChange') or {}).get('h1') or 0)
    tx=p.get('txns') or {}; h=tx.get('h1') or {}; m=tx.get('m5') or {}; b=int(h.get('buys') or 0); s=int(h.get('sells') or 0); b5=int(m.get('buys') or 0); s5=int(m.get('sells') or 0)
    age=ageh(p.get('pairCreatedAt')); risk=20
    if liq<10000:risk+=45
    elif liq<50000:risk+=25
    elif liq<100000:risk+=10
    elif liq>=250000:risk-=8
    if age<1/6:risk+=20
    elif age<.5:risk+=10
    elif age<1:risk+=5
    if b+s<40:risk+=15
    if (b+1)/(s+1)<.8:risk+=15
    risk=round(max(0,min(100,risk)))
    act=max(0,min(100,math.log10(max(vol,1))*14+min(b+s,150)*.28)); ls=max(0,min(100,math.log10(max(liq,1))*18-38)); mom=max(0,min(100,50+max(-50,min(50,pc))))
    opp=round(max(0,min(100,.35*act+.30*ls+.20*mom+.15*(100-risk))))
    base=p.get('baseToken') or {}
    return {'address':token,'symbol':base.get('symbol',''),'name':base.get('name',''),'pair':p.get('pairAddress'),'dex':p.get('dexId',''),'price':px,'liq':liq,'vol':vol,'pc':pc,'b':b,'s':s,'b5':b5,'s5':s5,'age':age,'risk':risk,'opp':opp,'bs':(b+1)/(s+1),'bs5':(b5+1)/(s5+1),'mc':float(p.get('marketCap') or p.get('fdv') or 0)}

def entry_scan(token):return parse(token,choose_pair(token))
def mark(pos):
    if not pos.get('pair'):return None,'old position: no locked pair'
    m=parse(pos['address'],exact_pair(pos['pair']))
    if not m:return None,'exact pair unavailable/mismatch'
    return m,None

def slip(cost,liq):
    if liq<=0:return 8.0
    return min(8.0,max(.5,.5+(cost/liq)*200))

def validate(pos,m):
    ch=(m['price']/pos['entry_mark']-1)*100
    if -80<ch<300:return True,'normal move'
    time.sleep(.8); m2=parse(pos['address'],exact_pair(pos['pair']))
    if not m2:return False,'second quote unavailable'
    disagree=abs(m2['price']/m['price']-1)*100 if m['price'] else 999
    if disagree>5:return False,f'quotes disagree {disagree:.1f}%'
    if ch<=-80:
        el=max(float(pos.get('entry_liq') or 0),1); em=max(float(pos.get('entry_mc') or 0),1)
        lchg=(m2['liq']/el-1)*100; mchg=(m2['mc']/em-1)*100 if em>1 else ch
        if lchg>-35 and mchg>-70:return False,'crash not confirmed by liquidity/market cap'
    return True,'extreme move confirmed twice'

def equity(d):
    e=d['cash']; rows=[]
    for p in d['positions']:
        m,err=mark(p)
        if m:
            ex=m['price']*(1-slip(p['cost'],m['liq'])/100); v=p['qty']*ex
        else:ex=p.get('entry_exec',p.get('entry',0));v=p['cost']
        e+=v;rows.append((p,m,err,ex,v))
    return e,rows

def hours(t):
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(t)).total_seconds()/3600
    except:return 0
def streak(d):
    n=0
    for t in reversed([x for x in d['trades'] if x.get('engine_version')==2]):
        if float(t.get('pnl',0))<0:n+=1
        else:break
    return n
def breaker(d,eq):
    dd=(eq/max(float(d.get('high') or d['start']),1)-1)*100; rs=[]; n=streak(d)
    if n>=3:rs.append(f'{n} consecutive V2 losses')
    if dd<=-5:rs.append(f'{dd:.1f}% drawdown')
    return bool(rs),rs,dd

d=load(); st.title('📡 Meme Coin Radar — V2 Fixed Engine'); st.caption('Exact-pair locking • quote validation • slippage • circuit breaker • PAPER MONEY ONLY')
legacy=[p for p in d['positions'] if not p.get('pair')]
if legacy:st.error(f'⚠️ {len(legacy)} old-engine open position(s) have no locked pair. V2 refuses to price or sell them. Reset the PAPER account below before testing V2.')
with st.sidebar:
    st.header('V2 Strategy'); rmax=st.slider('Max risk',0,100,35); omin=st.slider('Min opportunity',0,100,68); minliq=st.number_input('Min liquidity ($)',10000,2000000,50000,10000); minage=st.slider('Min pair age (minutes)',5,180,15,5); minvol=st.number_input('Min 1h volume ($)',5000,1000000,15000,5000); size=st.slider('Position size %',.5,5.0,2.0,.5); maxpos=st.slider('Max positions',1,8,4); stop=st.slider('Stop loss %',5,40,15); take=st.slider('Take profit %',10,150,30); hold=st.slider('Max hold hours',1,48,10); auto=st.toggle('Auto scan while open',False); secs=st.slider('Refresh seconds',20,120,30,5)

eq,marks=equity(d); d['high']=max(float(d.get('high') or d['start']),eq); blocked,reasons,dd=breaker(d,eq); actions=[]; warns=[]
for p in list(d['positions']):
    if not p.get('pair'):continue
    m,err=mark(p)
    if not m:warns.append(f"${p.get('symbol','?')}: {err}");continue
    ok,vwhy=validate(p,m)
    if not ok:
        d['blocked_quotes'].append({'time':now(),'symbol':p.get('symbol'),'pair':p.get('pair'),'reason':vwhy,'price':m.get('price')});warns.append(f"${p.get('symbol','?')}: blocked — {vwhy}");continue
    sp=slip(p['cost'],m['liq']); ex=m['price']*(1-sp/100); ch=(ex/p['entry_exec']-1)*100; why=None
    if ch<=-stop:why='stop loss'
    elif ch>=take:why='take profit'
    elif hours(p['time'])>=hold:why='max hold'
    elif m['liq']<minliq*.5:why='liquidity collapse'
    elif m['risk']>=80:why='risk spike'
    if why:
        proceeds=p['qty']*ex;pnl=proceeds-p['cost'];d['cash']+=proceeds
        d['trades'].append({**p,'exit_time':now(),'exit_mark':m['price'],'exit_exec':ex,'exit_liq':m['liq'],'exit_mc':m['mc'],'exit_slip_pct':sp,'pnl':pnl,'pnl_pct':pnl/p['cost']*100,'exit_reason':why,'validation':vwhy});d['positions'].remove(p);actions.append(f"SELL ${p['symbol']} {pnl/p['cost']*100:+.1f}%")

eq,marks=equity(d);blocked,reasons,dd=breaker(d,eq)
if not blocked and not legacy:
    held={p['address'] for p in d['positions']};recent={t['address'] for t in d['trades'][-25:]}
    for token in discovery():
        if len(d['positions'])>=maxpos:break
        if token in held or token in recent:continue
        m=entry_scan(token)
        if not m:continue
        good=m['risk']<=rmax and m['opp']>=omin and m['liq']>=minliq and m['age']>=minage/60 and m['vol']>=minvol and m['b']+m['s']>=50 and m['bs']>=1.30 and m['bs5']>=1.10
        if not good:continue
        eq,_=equity(d);cost=min(d['cash'],eq*size/100)
        if cost<5:break
        sp=slip(cost,m['liq']);entry_exec=m['price']*(1+sp/100);qty=cost/entry_exec;d['cash']-=cost
        d['positions'].append({'engine_version':2,'address':token,'symbol':m['symbol'],'name':m['name'],'pair':m['pair'],'dex':m['dex'],'time':now(),'cost':cost,'qty':qty,'entry_mark':m['price'],'entry_exec':entry_exec,'entry_slip_pct':sp,'entry_liq':m['liq'],'entry_mc':m['mc'],'entry_vol':m['vol'],'entry_age':m['age'],'entry_risk':m['risk'],'entry_opp':m['opp'],'entry_bs':m['bs'],'entry_bs5':m['bs5']});actions.append(f"BUY ${m['symbol']} {money(cost)} — pair locked");held.add(token)

eq,marks=equity(d);d['high']=max(float(d.get('high') or d['start']),eq);blocked,reasons,dd=breaker(d,eq);save(d)
v2=[t for t in d['trades'] if t.get('engine_version')==2];rp=sum(float(t.get('pnl',0)) for t in v2);wins=sum(float(t.get('pnl',0))>0 for t in v2);wr=100*wins/len(v2) if v2 else 0
c1,c2,c3,c4=st.columns(4);c1.metric('Paper Equity',money(eq),f"{(eq/d['start']-1)*100:+.1f}%");c2.metric('Cash',money(d['cash']));c3.metric('V2 Realized',money(rp));c4.metric('V2 Win Rate',f'{wr:.1f}%',f'{len(v2)} closes')
if blocked:st.error('🛑 CIRCUIT BREAKER — '+'; '.join(reasons))
if actions:st.success(' • '.join(actions))
if warns:st.warning('Quote protection: '+' | '.join(warns))

t1,t2,t3,t4,t5=st.tabs(['🤖 V2 Positions','🚨 Radar','📒 V2 History','🧪 Audit','💾 Reset / Backup'])
with t1:
    if marks:
        rr=[]
        for p,m,err,ex,v in marks:rr.append({'Coin':'$'+p.get('symbol',''),'Engine':p.get('engine_version','OLD'),'Pair':p.get('pair','NOT LOCKED'),'Entry exec':p.get('entry_exec',p.get('entry')),'Current exec':ex if m else None,'Cost':p.get('cost'),'Value':v,'P&L':v-p.get('cost',0),'P&L %':(v/p.get('cost',1)-1)*100,'Liquidity':m.get('liq') if m else None,'Status':err or 'validated exact pair'})
        st.dataframe(pd.DataFrame(rr),use_container_width=True,hide_index=True)
    else:st.info('No open V2 positions.')
with t2:
    rr=[]
    for token in discovery()[:25]:
        m=entry_scan(token)
        if m:rr.append({'Coin':'$'+m['symbol'],'Pair':m['pair'][:10]+'…','Age min':round(m['age']*60,1),'Liquidity':m['liq'],'1h Vol':m['vol'],'Buys':m['b'],'Sells':m['s'],'1h B/S':round(m['bs'],2),'5m B/S':round(m['bs5'],2),'Risk':m['risk'],'Opportunity':m['opp']})
    if rr:st.dataframe(pd.DataFrame(rr).sort_values(['Opportunity','Risk'],ascending=[False,True]),use_container_width=True,hide_index=True)
with t3:
    if v2:
        rr=[]
        for t in reversed(v2):rr.append({'Coin':'$'+t.get('symbol',''),'Pair':t.get('pair'),'Entry mark':t.get('entry_mark'),'Entry exec':t.get('entry_exec'),'Exit mark':t.get('exit_mark'),'Exit exec':t.get('exit_exec'),'Return %':t.get('pnl_pct'),'P&L':t.get('pnl'),'Exit reason':t.get('exit_reason'),'Validation':t.get('validation')})
        st.dataframe(pd.DataFrame(rr),use_container_width=True,hide_index=True)
    else:st.info('No V2 trades closed yet. Old-engine trades are excluded.')
with t4:
    st.write('Every V2 trade stores the exact pair, raw market mark, simulated execution price, liquidity and slippage.')
    if d['blocked_quotes']:st.dataframe(pd.DataFrame(d['blocked_quotes'][-50:]).iloc[::-1],use_container_width=True,hide_index=True)
    else:st.info('No suspicious quotes blocked yet.')
with t5:
    st.warning('Reset only the PAPER test before V2. This removes old buggy paper results and starts a clean fake $1,000 account.')
    st.download_button('Download old paper backup',json.dumps(d,indent=2),'paper_backup_before_v2.json','application/json',use_container_width=True)
    confirm=st.checkbox('I understand this resets fake money only')
    if st.button('RESET PAPER TEST TO $1,000',disabled=not confirm,use_container_width=True):save(fresh());st.rerun()
if auto:
    time.sleep(secs);st.cache_data.clear();st.rerun()
