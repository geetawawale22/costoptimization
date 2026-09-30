# Running CostOptima on localhost

Everything here uses local defaults. No secrets are committed; you supply your own.

---

## 1. Database

```sql
CREATE DATABASE costoptima CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'costoptima'@'localhost' IDENTIFIED BY 'change-me-locally';
GRANT ALL PRIVILEGES ON costoptima.* TO 'costoptima'@'localhost';
FLUSH PRIVILEGES;
```

No MySQL handy? SQLite works for everything except MySQL-specific SQL:

```
DATABASE_URL=sqlite:///./costoptima.db
```

---

## 2. Environment

```bash
cd costoptima_backend
cp .env.example .env
```

Edit `.env`:

```
DATABASE_URL=mysql+pymysql://costoptima:change-me-locally@localhost:3306/costoptima
SECRET_KEY=<generate: python -c "import secrets;print(secrets.token_urlsafe(48))">
SESSION_COOKIE_SECURE=false
FRONTEND_ORIGIN=http://localhost:5173
UPLOAD_DIR=./uploads
```

`FRONTEND_ORIGIN` must be an explicit origin, never `*` — session cookies use
`allow_credentials=True`, which browsers only honour for a named origin. If login
"succeeds" but every later call returns 401, this is almost always the cause.

Frontend:

```bash
cd ../costoptima_frontend
cp .env.example .env          # VITE_API_BASE_URL=http://localhost:8000
```

---

## 3. Install and seed

```bash
cd costoptima_backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

python -m app.seed                # roles, permissions, users, workbook + commodity scaffolding
```

`app/seed.py` creates the login accounts. They are **development credentials** — change
them before exposing this beyond localhost:

| Email | Password | Role |
|---|---|---|
| `superadmin@costoptima.com` | `superadmin123` | Super Admin |
| `admin@costoptima.com` | `admin123` | Admin |

Install the PCM calculation definition:

```bash
python -m app.seed_pcm                # validate only, no DB connection
python -m app.seed_pcm --apply        # insert (refuses to overwrite existing config)
```

---

## 4. Load the master rate data

The engine reads rates from the database, never from a workbook at runtime.

```bash
python -m app.import_pcm_master "../Master_Rate_Data.xlsx"                       # dry run
python -m app.import_pcm_master "../Master_Rate_Data.xlsx" --apply --with-definition
```

An untranslatable consumption formula aborts the import rather than being skipped, so a
clean run means every rule was translated.

### Temporary / placeholder master data

You mentioned wanting temporary data you will replace later. Import the real
`Master_Rate_Data.xlsx` above rather than inventing placeholder rates — placeholder rates
produce plausible-looking but wrong costs, which is worse than a visible failure. The
importer is re-runnable, so re-importing a corrected workbook later supersedes it, and
saved calculation snapshots keep their original rates so historical results never shift.

Where master data is genuinely absent, the engine already reports it per row
(`MISSING_MASTER`, `MISSING_MASTER_FIELD`) instead of silently substituting zero. That is
the behaviour you want while the master is incomplete.

---

## 5. Run

```bash
# terminal 1
cd costoptima_backend && uvicorn app.main:app --reload --port 8000

# terminal 2
cd costoptima_frontend && npm install && npm run dev
```

- App: http://localhost:5173
- API docs: http://localhost:8000/docs

---

## 6. End-to-end check

1. Sign in, open **New Calculation**, upload
   `S302 BSO COVER_Sprint Input_Rev 2_09.09.2026 (1).xlsx`.
2. Run the calculation; review any flagged rows.
3. Download the output and compare against
   `2302C S302 FENDER OUTER TRIMS ..._50002264.xlsx`.
4. Repeat with `U172UkayINS150626_3 B&H_2 - Copy.xlsx` — it should produce its **own**
   sheets (Make, Soft, Buy Non Std) and its **own** variants
   (`SHEV - G12-DHT MX3/MX5/AX7/AX7L`), not S302's.

Headless equivalent, no browser or MySQL needed:

```bash
python tools/verify_pcm_parity.py
```

Expected: `rows 14 · comparisons 270 · mismatches 0 · max abs diff 1.14e-13`.

---

## 7. What is configurable without touching code

| Where | Controls |
|---|---|
| `app_config` → `PCM_ENGINE.definition` | Formulas, dispatch profiles, parameters, master bindings, output variants |
| `app_config` → `PCM_ENGINE.commodity_aliases` | Commodity name aliases (`hard` → `trim`) |
| `app_config` → `PCM_HARD` | Allowance/shift/efficiency factors, deflashing, assembly CT, paint process cost, rate period |
| `data/pcm_legacy_format.json` | Fonts, number formats, amber total fill, annotation blocks — transcribed from legacy source |
| `data/pcm_output_layout.json` | Legacy sheet order, per-sheet column layout and widths |
| `data/pcm_parity_cases.json` | Legacy-parity regression cases |

`definition.output` switches: `render_legacy_layout`, `apply_legacy_formats`,
`apply_legacy_template_styles`.

---

## 8. Troubleshooting

| Symptom | Cause |
|---|---|
| 401 on every call after login | `FRONTEND_ORIGIN` not an exact origin match |
| `CONFIG_NOT_AVAILABLE` (422) | PCM definition not installed — run `seed_pcm --apply` |
| Rows flagged `MISSING_MASTER` | Master lacks that lookup key — expected while master is incomplete |
| Rows flagged `MISSING_MASTER_FIELD` | Input uses a rate period the master does not carry (U172 wants `Rate/UOM_Jan#24`) |
| `INVALID_INPUT: '#REF!'` | The uploaded workbook contains a broken Excel formula — fix at source |
| Output missing a commodity sheet | No rows dispatched to that profile for this input; sheets follow the data |
