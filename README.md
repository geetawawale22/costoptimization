# CostOptima — PCM Modernization

FastAPI + MySQL + React modernization of the legacy PCM (Piece Cost Model) system.

```
CostOptima/
├── costoptima_backend/     FastAPI + SQLAlchemy backend, PCM calculation engine
├── costoptima_frontend/    React + Vite frontend
├── legacy_reference/       Legacy PCM_5 scripts + PCM_master rate tree (read-only reference)
├── Master_Rate_Data.xlsx   Consolidated all-commodity master/rate workbook
├── S302 BSO COVER_Sprint Input_Rev 2_09.09.2026 (1).xlsx      Reference input fixture
├── 2302C S302 FENDER OUTER TRIMS ... _50002264.xlsx           Reference expected output
└── U172UkayINS150626_3 B&H_2 - Copy.xlsx                      Additional sample input
```

The three workbooks at the top level are **not samples** — the parity harness resolves
them by relative path (`../<name>.xlsx` from the backend root). Renaming or moving them
will make `tools/verify_pcm_parity.py` report `Fixture Missing`.

---

## Prerequisites

- Python 3.11+ (3.12/3.13 tested)
- Node 18+
- MySQL 8+ or MariaDB 10.6+

---

## Database setup

```sql
CREATE DATABASE costoptima CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'costoptima'@'localhost' IDENTIFIED BY '<choose-a-password>';
GRANT ALL PRIVILEGES ON costoptima.* TO 'costoptima'@'localhost';
FLUSH PRIVILEGES;
```

Schema is created by the application on startup (SQLAlchemy metadata), including the
`pcm_run_snapshots` table. There is no separate migration step.

---

## Environment variables

Copy `costoptima_backend/.env.example` to `.env` and set at least:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | e.g. `mysql+pymysql://costoptima:<password>@localhost/costoptima` |
| `SECRET_KEY` | JWT signing key — generate a fresh random value per environment |
| `UPLOAD_DIR` | Directory for uploaded workbooks |

Frontend: copy `costoptima_frontend/.env.example` to `.env` and point `VITE_API_URL` at
the backend.

**Do not commit `.env`.** No credentials are included in this repository; supply your own.

---

## Backend startup

```bash
cd costoptima_backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m app.seed                # roles, permissions, initial admin
uvicorn app.main:app --reload
```

API docs at `http://localhost:8000/docs`.

---

## Frontend startup

```bash
cd costoptima_frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

---

## PCM master-data import

The calculation engine reads rates from the database, never from a workbook at runtime.
`Master_Rate_Data.xlsx` is a one-time (and subsequently re-runnable) import source.

Validate without touching the database:

```bash
python -m app.import_pcm_master "../Master_Rate_Data.xlsx"
```

Publish the normalized master plus the compatible formula definition in one transaction:

```bash
python -m app.import_pcm_master "../Master_Rate_Data.xlsx" --apply --with-definition
```

Add `--replace` to intentionally supersede an existing active definition or master
version. Saved calculation snapshots retain their original configuration, rates and
results, so historical results do not change. An untranslatable consumption formula
aborts the import rather than being silently skipped.

Formula-only, insert-only SQL alternative: `data/pcm_reference_seed.sql`. SQL alone does
not import rates and does not replace an existing definition.

---

## Excel upload and calculation

1. Sign in, open **New Calculation**, upload a BOM workbook (e.g. the S302 input).
2. The backend detects sheets, maps columns, extracts rows and persists them.
3. Run the calculation. Rates come from the imported master; formulas, dispatch, input
   mappings, variant selection and applicability mappings come from `app_config` group
   `PCM_ENGINE`, key `definition`.
4. Rows that fail produce structured issues (line, part, code, message). A run with any
   failure is marked **Needs Review** and its totals are partial, never a fabricated
   successful estimate.
5. Download the generated workbook from the calculation view. Historical exports
   regenerate from the saved snapshot, never from current rates.

A missing or disabled definition returns HTTP 422 `CONFIG_NOT_AVAILABLE` rather than
silently falling back.

---

## Administrator-editable configuration

Changing these does **not** require a source change:

| Where | What |
|---|---|
| Settings → App Config, group `PCM_ENGINE`, key `definition` | Formulas, dispatch profiles, parameters, master-source bindings, joins, output variants |
| Settings → App Config, group `PCM_ENGINE`, key `commodity_aliases` | Commodity name aliases (e.g. `hard` → `trim`). Merged over the built-in default; an absent key preserves legacy behaviour |
| Settings → App Config, group `PCM_HARD` | Allowance factor, shift factor/rate, efficiency, shift seconds, deflashing threshold/cost, assembly CT defaults, paint process cost, active RM/paint rate period |
| `data/pcm_parity_cases.json` | Legacy-parity regression cases |

---

## Legacy-parity testing

The harness imports the real master into a throwaway SQLite database, calculates the real
input, exports the result, and only then opens the legacy expected workbook to compare.

```bash
python tools/verify_pcm_parity.py                      # every enabled case
python tools/verify_pcm_parity.py --list               # cases + profiles lacking a fixture
python tools/verify_pcm_parity.py --case s302_bso_cover
```

Current verified result for the supplied fixture:

```
rows 14 · errors 0 · comparisons 270 · mismatches 0 · max abs diff 1.14e-13
profiles exercised: assy, buy_std, end_item, frt, hard, pkg, special_process
```

Exit status is non-zero on any mismatch, calculation error, or missing fixture.

`tools/verify_pcm_reference.py` remains as the original documented entry point and
delegates to the same runner.

### Adding a parity case for another commodity

Supply a legacy input workbook **and** the output the legacy system produced from it,
then append a case to `data/pcm_parity_cases.json`. No Python change is required.

Profiles that execute but have **no** parity fixture, so remain unverified:
`stamping`, `soft`, `forging`, `sand`, `gdc`, `pdc`, `thermal`, `thermal_make`, `pcba`,
`seat`, `buy_non_std`, `consign`.

---

## Test suite

```bash
python -m pytest -q --tb=short --disable-warnings
python -m unittest discover -s tests -p '*_unittest.py' -v
```

`tests/test_engine_independence.py` enforces that no module under `app/services/` imports
`app.routers.*` and that importing the calculation engine does not pull in FastAPI. That
layering was previously violated, which made the engine impossible to run or test without
the whole web stack.

---

## Default administrator

`python -m app.seed` creates the initial roles, permissions and an administrator account.
Credentials are defined in `app/seed.py`. **Change the administrator password
immediately after first sign-in**, and do not reuse seed credentials in any shared or
production environment.
