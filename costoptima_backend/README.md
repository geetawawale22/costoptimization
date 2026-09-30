# CostOptima — Backend (FastAPI)

Modernized backend for the CostOptima cost-calculation platform. Session-cookie
authentication, a resource/action Permissions table (Super Admin / Admin / HOD /
Masters Update), full audit fields on every table, MySQL as the database, and
calculations that run synchronously — no job queue, no polling.

## Quick start

### PCM database formulas and migration coverage

The optional PCM database runtime reads its calculation definition from
`app_config` (`PCM_ENGINE.definition`) and rates from configured `workbook_rows`.
It saves formula/master snapshots and exports calculated values. See
[the implementation and coverage report](docs/pcm/IMPLEMENTATION.md) for setup,
normalized master contracts, tests, and the legacy branches still requiring
translation and parity validation. Read that report before enabling it for
production estimates.

Validate with `python -m app.seed_pcm`; install with `python -m app.seed_pcm --apply`.
Alternatively use [the MySQL/MariaDB seed SQL](data/pcm_seed.sql).
[The readable formula/configuration payload](data/pcm_definition.json) is provided
for review. Seeding preserves existing configuration and does not generate rates.

### 1. Database (MySQL/MariaDB)

```bash
sudo apt-get install mariadb-server   # or use an existing MySQL 8+ server
sudo service mariadb start

mysql -u root -e "
  CREATE DATABASE costoptima CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER 'costoptima'@'localhost' IDENTIFIED BY 'costoptima_pw';
  GRANT ALL PRIVILEGES ON costoptima.* TO 'costoptima'@'localhost';
  FLUSH PRIVILEGES;
"
```

### 2. Backend

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # defaults match the database created above

python -m app.seed               # creates tables + seeds permissions, roles, starter
                                   # users, Masters config, commodities

uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/docs** for the interactive Swagger UI.

### Seeded logins (one per role, for testing)

| Role | Email | Password |
|---|---|---|
| Super Admin | superadmin@costoptima.com | superadmin123 |
| Admin | admin@costoptima.com | admin123 |
| HOD | hod@costoptima.com | hod123 |
| Masters Update | masters@costoptima.com | masters123 |

Login is **session-cookie based**, not JWT: `POST /auth/login` sets an HttpOnly
`session_id` cookie backed by a `sessions` table row. Every subsequent request just
needs that cookie (the browser/fetch handles this automatically with
`credentials: 'include'`) — there's no bearer token to store or attach manually.
`GET /auth/me` (and the login response itself) returns the user plus their resolved
`permissions` list in one call, so the frontend never needs a second round-trip to
know what the current user can do.

## Running the test suite

```bash
pip install -r requirements-test.txt
pytest
```

67 tests, run against an isolated SQLite file (`tests/costoptima_test.db`) so they're
fast and never touch your real MySQL database. Covers session login/logout, the full
Permissions/RolePermission model (including a role-permission-replace test), Workbook
CRUD + both import styles (whole-workbook and single-tab), BOM upload/detection/
validation, and the synchronous calculation engine — including a test that checks the
real documented Forging formula chain (`calculate_forging_line`) produces the exact
expected numbers by hand-worked example.

## What changed from the earlier PCM prototype (and why)

| Area | Before | Now |
|---|---|---|
| Auth | JWT access/refresh tokens | Server-side `sessions` table + HttpOnly cookie |
| Roles | Admin / Master Data Editor / PCE / Viewer with boolean flags on Role | **Super Admin / Admin / HOD / Masters Update**, permissions resolved through a real `permissions` + `role_permissions` table — one row per (page/model, action) |
| Calculations | Async `EstimationJob` + `BackgroundTasks`, client polls `GET /jobs/{id}` | `POST /boms/{id}/calculate` runs **synchronously** and returns the finished result in one response — see `app/calc_engine.py` |
| Every table | `id` + business columns only | Every table also has `created_by`, `created_at`, `modified_by`, `modified_at`, `is_deleted` (see `app/models/mixins.py`); deletes are soft (`is_deleted=True`), never a real `DELETE` |
| Database | SQLite | MySQL/MariaDB (`mysql+pymysql://...`) — every `String` column now has an explicit length, required by MySQL |
| Master file import | Only whole-workbook import | **Every individual file/tab can also be imported entirely on its own** — `POST /workbook-types/{type}/tabs/{tab}/import` — without touching any other sheet in its parent workbook |

## The calculation engine (`app/calc_engine.py`)

Implements the documented **Forging** costing formula in full, as a concrete real
example (see the project's reference document, "Forging" section):

```
Yield %        = Part Wt / Input Wt
Scrap Wt        = (Input Wt - Part Wt) * 0.8
Scrap Recovery   = Scrap Rate * Scrap Wt
RM Cost/Pc (MAKE) = (Rate/UOM * Input Wt) + Heat Treatment Cost - Scrap Recovery
RM Cost/Pc (other) = (Rate/UOM * Input Wt) - Scrap Recovery
Basic Cost/Pc      = RM Cost/Pc + Process Cost
Total Markup        = ICC + REJ + OH + Profit
Total                 = Basic Cost/Pc + Tool Cost + Packing + Freight + Total Markup
```

**Honest scope note:** the reference document specifies this same step-by-step
pattern for ~15 commodities (Soft, Stamping, Sand Casting, GDC, PDC, Thermal, PCBA,
Seat, etc.). Implementing every one of those exactly was outside what could be
verified correct in this delivery — Forging is fully implemented and unit-tested
against the documented formula by hand-worked example (`tests/test_calculations.py`).
Every other commodity currently falls back to `generic_commodity_calc`, which
produces a clearly-labeled illustrative total so the full upload -> calculate ->
result -> export flow still works end-to-end for them. To add another commodity's
real formula: write a `calculate_<commodity>_line(row, master_lookup)` function the
same way `calculate_forging_line` is written, and add one line to the `DISPATCH`
dict at the bottom of `app/calc_engine.py` — no other code needs to change.

## Permissions model

```
permissions        (id, resource, action, description)
role_permissions    (id, role_id, permission_id)
roles                (id, name, description)
```

`resource` is a page or model (`masters`, `commodity_config`, `calculations`,
`outputs`, `history`, `settings`, `users`, `roles`); `action` is `create` / `view` /
`update` / `delete`. `core/deps.py`'s `require_permission(resource, action)` gates
every mutating (and most read) endpoints by looking the grant up through
`Role -> RolePermission -> Permission`. Manage a role's permissions via
`GET/PUT /roles/{id}/permissions` — `PUT` replaces the role's entire permission set
with the given list of permission ids (see `app/seed.py`'s `ROLE_MATRIX` for the
default assignment per role).
