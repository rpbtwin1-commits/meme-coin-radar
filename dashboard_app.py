import os, requests, streamlit as st
st.set_page_config(page_title='Early Gems V6',page_icon='💎',layout='wide')
API=os.getenv('EARLY_GEMS_API_URL','').rstrip('/'); TOKEN=os.getenv('EARLY_GEMS_API_TOKEN','')
def money(x):
    try:
        x=float(x)
        return f'${x/1e6:.2f}M' if x>=1e6 else f'${x/1e3:.1f}K' if x>=1e3 else f'${x:,.2f}'
    except:return '—'
def api(path):
    if not API:return None,'EARLY_GEMS_API_URL is not configured yet.'
    try:
        h={'X-API-Token':TOKEN} if TOKEN else {}; r=requests.get(API+path,headers=h,timeout=12); r.raise_for_status(); return r.json(),None
    except Exception as e:return None,str(e)
st.title('💎 Early Gems V6 — Always On')
st.caption('The scanner runs on the server even when your phone is locked. This screen only displays the latest results.')
data,err=api('/gems?limit=30')
if err: st.error(err)
else:
    st.success(f"Worker online • {data.get('count',0)} ranked gems • updated {data.get('updated','')[:19]} UTC")
    for g in data.get('items',[]):
        st.markdown(f"### {g.get('status')} — ${g.get('symbol','')} — {g.get('score',0)}/100\nFirst seen: {g.get('first_seen','')[:19]} UTC • Scans: {g.get('scan_count',0)} • High score: {g.get('high_score',0)}  \nAge {g.get('age_min',0):.1f}m • MCap {money(g.get('market_cap'))} • Liquidity {money(g.get('liquidity'))} • 1h volume {money(g.get('volume_h1'))}  \nBuys/Sells {g.get('buys_h1',0)}/{g.get('sells_h1',0)} • B/S {g.get('ratio',0):.2f} • 5m {g.get('p5',0):+.1f}% • 1h {g.get('p1',0):+.1f}%  \nImproving: {', '.join(g.get('good',[])[:6]) or 'none yet'}  \nFlags: {', '.join(g.get('flags',[])[:6]) or 'none detected'}  \nContract: `{g.get('address')}`")
st.caption('High scores are screening signals, not guarantees of safety or profit.')
