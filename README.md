# CostOptima

CostOptima provides a React/Vite frontend and FastAPI backend for master-data management, guarded development costing, workbook reports, user access, and audit history.

## Local setup

Requires Python 3.11+, Node.js, and a local MySQL/MariaDB database.

### Backend

```powershell
cd costoptima_backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env`: set `DATABASE_URL` to the local `costoptima` database and provide a random `CSRF_SIGNING_KEY` of at least 64 characters. Then initialize the development roles/users and start the API:

```powershell
.\.venv\Scripts\python.exe -m app.seed
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8017
```

API docs: http://127.0.0.1:8017/docs.

### Frontend

```powershell
cd costoptima_frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5187. The Vite `/api` proxy targets the backend at `127.0.0.1:8017`; update `vite.config.js` if that port changes.

## Verification

```powershell
cd costoptima_backend
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m pytest
```

```powershell
cd costoptima_frontend
npm run build
node --test tests/*.test.mjs
```

Calculations are guarded by the configured environment and remain development-unverified. Review the backend analysis reports and resolve missing source/master coverage before relying on costs for production decisions.
