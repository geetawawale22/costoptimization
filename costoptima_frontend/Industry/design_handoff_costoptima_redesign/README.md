# Handoff: CostOptima Frontend Redesign

## Overview
A visual redesign of all major screens in the CostOptima React frontend (`costoptima_frontend/`) — the manufacturing BOM cost-management platform. No functionality was added or removed; every field, table column, action, and state from the existing React pages is preserved. This is a **visual restyle only**.

## About the Design Files
The files in `screens/` are **design references built in static HTML/CSS** — not production code to copy directly. They show the intended look (colors, spacing, typography, component shapes) and layout for each screen. The task for Claude Code is to **recreate this styling inside the existing React codebase** — i.e. update `costoptima_frontend/src/styles/global.css` (and any component-level styles) so the real React components (`Sidebar.jsx`, `Topbar.jsx`, `Layout.jsx`, `WorkbookTable.jsx`, the page files under `src/pages/`, etc.) render with this look, using React's existing structure — not by replacing components with this static HTML.

## Fidelity
**High-fidelity.** Colors, type, spacing and component shapes below are final — implement pixel-accurately using the values in "Design Tokens".

## Design Tokens

Colors:
- Background: `#F7F8FA`
- Surface / card: `#FFFFFF`
- Border: `#E4E7EC`
- Text (primary): `#1C2430`
- Text (muted): `#6B7686`
- Accent: `#3E6E8E` (hover/darker: `#2D5470`)
- Accent tint (chips/badges): `#EAF1F5` bg / `#2D5470` text
- Success: `#2E8B57` text / `#E7F5EC` bg
- Warning: `#B8860B` text / `#FBF3DF` bg
- Danger: `#C0392B` text / `#FBEAE8` bg
- Sidebar background: `#182430`, sidebar muted text `#9AA7B7`

Typography: **Manrope** (Google Font), weights 400–800 for both headings and body. Headings 800 weight, `-0.01em` letter-spacing. Body/table text 500–600 weight.

Radius: cards/buttons/inputs `10px`–`16px` (cards 16px, buttons/inputs 10px, chips/pills fully rounded `999px`). No square corners anywhere (this replaces the old flat/no-radius look).

Shadows: cards use a very soft `0 1px 3px rgba(20,30,45,.05)`; stat cards `0 1px 2px rgba(20,30,45,.04)`. Nothing heavier.

Shell: sidebar fixed `240px` wide, dark navy (`#182430`); topbar `64px` tall, white, `1px solid #E4E7EC` bottom border; content area padded `32px 36px` on `#F7F8FA`.

## Screens (see `screens/*.html`)

1. **Login.html** — Split card: dark marketing panel (brand, headline) + form panel (email, password, remember me, Sign In, divider, "Sign in with SSO" secondary button). Maps to `src/pages/Login.jsx`.
2. **Calculations.html** — Dashboard: 4 stat cards (Total / Pending / Approved / This Month) + filterable table of calculations with status chips. Maps to `src/pages/Calculations.jsx`.
3. **NewCalculation.html** — 5-step vertical stepper: Calculation Info → Upload PCM file (dropzone, detected sheet pills, input preview table) → Validation checklist → Calculate → Result tabs (Done/Email/Download). Maps to `src/pages/NewCalculation.jsx`.
4. **Masters.html** — Generic workbook-table pattern: sub-tabs, search, Import/Export/Add Row toolbar, data table, pagination. Reused verbatim by `WorkbookTable.jsx` across all Masters pages.
5. **CommodityTypes.html** — Same WorkbookTable pattern, scoped to a commodity/category (e.g. Forging → Process Master), with the deeper sidebar nav tree expanded. Maps to `src/pages/CommodityTypes.jsx`.
6. **MasterRateData.html** — WorkbookTable pattern plus a merge toolbar (Show Version History / Export All / Start Merge). Maps to `src/pages/MasterRateData.jsx`.
7. **RolesPermissions.html** — Roles table + selected-role detail panel with a resource × action (Create/View/Update/Delete) permission-toggle matrix, plus a Permissions Catalogue tab. Maps to `src/pages/settings/RolesPermissions.jsx`.
8. **Users.html** — Stat cards + searchable user table with role, status, Deactivate All / Invite User actions. Maps to `src/pages/settings/Users.jsx`.
9. **AppConfig.html** — Config-form pattern (field grid + toggle tiles + Save/Cancel) plus a configured-values list below. This pattern is shared by `WorkbookConfig.jsx`, `FormulaRepository.jsx`, `MasterConfiguration.jsx`, and `OutputTemplates.jsx` — restyle all of them the same way.
10. **AuditLogs.html** — Filterable log table (actor, action, resource, detail, timestamp). Maps to `src/pages/settings/AuditLogs.jsx`.
11. **OutputsHistory.html** — Two-tab page: Outputs (completed calculation outputs + upload repository tables) and History & Logs (calculation history + execution log table) — same visual pattern, tab-switched. Maps to `src/pages/Outputs.jsx` and `src/pages/History.jsx`.
12. **Profile.html** — Dark gradient hero (avatar, name, role, stat pills) + Personal Info panel + Account Status panel + Access Coverage (permission chips by resource). Maps to `src/pages/Profile.jsx`.

All screens share one **app shell**: fixed dark sidebar (nav items listed in `Sidebar.jsx` — Calculations, New Calculation, Masters, PDF Convertor, Commodity Types, Master Rate Data, Outputs, History, Settings, Profile) + white topbar (breadcrumb, search, avatar). Build this shell once as the restyled `Layout.jsx`/`Sidebar.jsx`/`Topbar.jsx` and every page inherits it — don't restyle it per page.

## Interactions & Behavior
No new interactions — preserve all existing React state/handlers (search, pagination, modals, toasts, tab switching, permission toggles, file upload, wizard step locking, etc.) exactly as implemented today. This handoff only changes CSS classes/values, not logic.

## Files
- `screens/*.html` — one static reference per screen (open directly in a browser).
- Source design files (for context, not needed to implement): `templates/costoptima-*/*.dc.html` in the design project.
- Real target files to edit: `costoptima_frontend/src/styles/global.css`, `src/components/Sidebar.jsx`, `src/components/Topbar.jsx`, `src/components/Layout.jsx`, `src/components/WorkbookTable.jsx`, and each page under `src/pages/`.
