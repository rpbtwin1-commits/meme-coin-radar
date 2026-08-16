# Early Gems V6 — Always On

## Worker on Railway
- Start command: `python worker_api.py`
- Attach a Railway Volume at `/data`
- Set `DB_PATH=/data/gems.db`
- Optional: `BIRDEYE_API_KEY`
- Optional: `API_TOKEN`
- Optional: `SCAN_SECONDS=20`
- Generate a public Railway domain.

## Streamlit dashboard
Use `dashboard_app.py` as the Streamlit main file or merge it into the existing dashboard.
Set:
- `EARLY_GEMS_API_URL=https://YOUR-WORKER.up.railway.app`
- `EARLY_GEMS_API_TOKEN=...` only if API_TOKEN is set.

The worker scans continuously and stores score history in SQLite on the mounted Railway Volume.
Public DEX Screener discovery does not see every newly created token; Birdeye improves launch coverage.
