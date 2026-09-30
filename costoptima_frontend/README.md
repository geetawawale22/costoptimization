# CostOptima — Frontend (React + Vite)

React implementation wired to the CostOptima FastAPI backend. Session-cookie
authentication (no token to manage manually) and a permission model driven
entirely by the backend's `permissions` table.

## Quick start

```bash
npm install
cp .env.example .env      # points at http://127.0.0.1:8000 by default
npm run dev
```

Open **http://localhost:5173**. Start the backend first (see `costoptima_backend/README.md`).

Seeded logins (one per role): `superadmin@costoptima.com` / `superadmin123`,
`admin@costoptima.com` / `admin123`, `hod@costoptima.com` / `hod123`,
`masters@costoptima.com` / `masters123`.

```bash
npm run build     # production build -> dist/
npm run preview   # serve the production build locally
```

## Session-cookie auth (not JWT)

`api/client.js`'s `request()` sends every call with `credentials: 'include'` — the
browser automatically attaches the HttpOnly `session_id` cookie the backend set on
`POST /auth/login`. There's no access/refresh token pair to store, refresh, or attach
manually. `AuthContext` calls `GET /auth/me` on load, which (like the login response
itself) returns the user **and their resolved permissions** in one call.

## Permission model

`useAuth().can(resource, action)` checks the permissions array returned by the
backend — e.g. `can('masters', 'update')`, `can('calculations', 'create')`,
`can('users', 'delete')`. This mirrors the backend's `require_permission(resource,
action)` dependency exactly, so a button being enabled in the UI and the
corresponding API call succeeding are backed by the *same* grant — the frontend
check is a convenience, never the real security boundary (every mutating endpoint
re-checks independently server-side).

`pages/settings/RolesPermissions.jsx` renders one full resource × action matrix per
role and calls `PUT /roles/{id}/permissions` to persist changes — this is a real
CRUD UI over the `role_permissions` table, not a static list.

## Calculations run synchronously — no polling

`api/calculations.js`'s `calculate(bomId)` calls `POST /boms/{id}/calculate` and gets
the **finished** calculation back in one response (status, per-commodity results, and
the final cost breakdown). `pages/NewCalculation.jsx`'s Step 4 button is a single
`await` — there's no `setInterval`/polling loop, because the backend has nothing to
poll: by the time the request returns, the calculation already happened.

## The `WorkbookTable` component — now with two import modes

Every Masters and Commodity Types page is still rendered by the one generic
`<WorkbookTable typeCode="..." />` component (see the earlier project docs for why).
Its Import button now offers **"This file only"** (imports just the currently-open
tab via `POST /workbook-types/{type}/tabs/{tab}/import` — e.g. re-upload only
"Net Consumption Base") or **"Whole workbook"** (all tabs at once, the original
multi-tab import) — matching the backend's two import endpoints.

## Architecture (unchanged from the earlier delivery, updated for the above)

```
src/
  api/                       auth.js, workbooks.js, commodities.js, boms.js,
                               calculations.js (was jobs.js), misc.js (users, roles,
                               permissions, notifications, dashboard, output templates)
  context/
    AuthContext.jsx            Session login/logout + can(resource, action)
    ToastContext.jsx
  components/
    Sidebar.jsx, Topbar.jsx, Layout.jsx, ProtectedRoute.jsx
    WorkbookTable.jsx           Generic Masters/Commodity Types table + import/export/CRUD
    modals/ImportModal.jsx       Single-tab vs whole-workbook import
    modals/RowModal.jsx
  pages/
    Login.jsx, Calculations.jsx, NewCalculation.jsx, Masters.jsx, CommodityTypes.jsx,
    Outputs.jsx, History.jsx, Profile.jsx
    settings/  FormulaRepository, MasterConfiguration, Database, Storage, AuditLogs,
                Users, RolesPermissions (now a real resource x action grid)
  config/
    commodityStructure.js      Mirrors the backend's COMMODITY_STRUCTURE exactly
```

## What's simplified for this deliverable

- No client-side pagination on tables yet — relies on the backend returning
  reasonably-sized result sets.
- `RolesPermissions.jsx` fetches each role's granted permissions individually on
  load (`N` requests for `N` roles) — fine at the current scale (4 roles), but worth
  batching server-side if the role count grows significantly.
- The Formula Repository's "Test" feature sends whatever JSON you type as the sample
  row — there's no schema-aware form builder for it.
