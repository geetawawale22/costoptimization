# CostOptima Frontend Redesign — Implementation Spec

## Goal
Restyle the existing CostOptima React frontend (`costoptima_frontend/src/`) to match the
pixel-final design reference in `Industry/design_handoff_costoptima_redesign/` (temporary
folder, to be deleted once implementation is complete — do not treat it as a permanent
part of the codebase).

This is a **visual restyle only**: no functionality, routes, state, or API calls change.
Every field, table column, action, and state from the existing pages is preserved exactly.

## Source of truth
- `Industry/design_handoff_costoptima_redesign/README.md` — design tokens + screen-to-file mapping
- `Industry/design_handoff_costoptima_redesign/screens/*.html` — static reference per screen (12 files)

### Design tokens (from the handoff README)
- Background `#F7F8FA`, surface/card `#FFFFFF`, border `#E4E7EC`
- Text primary `#1C2430`, text muted `#6B7686`
- Accent `#3E6E8E` (hover `#2D5470`), accent tint `#EAF1F5` bg / `#2D5470` text
- Success `#2E8B57`/`#E7F5EC`, Warning `#B8860B`/`#FBF3DF`, Danger `#C0392B`/`#FBEAE8`
- Sidebar bg `#182430`, sidebar muted text `#9AA7B7`
- Font: Manrope (Google Font), 400–800; headings 800 + `-0.01em` letter-spacing; body 500–600
- Radius: cards 16px, buttons/inputs 10px, chips/pills 999px — no square corners
- Shadows: cards `0 1px 3px rgba(20,30,45,.05)`, stat cards `0 1px 2px rgba(20,30,45,.04)`
- Shell: sidebar fixed 240px, dark navy; topbar 64px, white, 1px bottom border; content padded `32px 36px`

## Approach
Implement incrementally, one unit at a time, with a manual visual check (dev server +
browser) after each before moving to the next. This lets design drift get caught early
instead of after a big-bang restyle across 12+ pages.

## Build order
1. **Shell** — `src/styles/global.css` (tokens: colors, type, radius, shadow as CSS
   variables/base styles) + `src/components/Sidebar.jsx` + `Topbar.jsx` + `Layout.jsx`
2. **Login.jsx** — standalone, simplest screen, validates the token set in isolation
3. **Calculations.jsx** — stat cards + filterable table + status chips (pattern reused elsewhere)
4. **WorkbookTable.jsx** — shared component; restyling it covers Masters.jsx,
   CommodityTypes.jsx, and MasterRateData.jsx (MasterRateData also needs its merge toolbar)
5. **NewCalculation.jsx** — 5-step stepper (most complex: dropzone, pills, preview table, result tabs)
6. Remaining pages, same session: `settings/RolesPermissions.jsx`, `settings/Users.jsx`,
   the AppConfig pattern (`WorkbookConfig.jsx`, `settings/FormulaRepository.jsx`,
   `settings/MasterConfiguration.jsx`, `OutputTemplates.jsx`), `settings/AuditLogs.jsx`,
   `Outputs.jsx` + `History.jsx` (tab-switched, shared pattern), `Profile.jsx`

## Non-goals
- No new interactions, routes, or state changes — preserve all existing React
  handlers/logic exactly (search, pagination, modals, toasts, tab switching, permission
  toggles, file upload, wizard step locking).
- No copy-pasting the static HTML in as markup — real React components are restyled in
  place (className/CSS changes), keeping their existing JSX structure and data bindings.
- `PdfConverter.jsx` and `SsoCallback.jsx` are not covered by the handoff (no reference
  screen) — leave them on shared shell/base styles only unless the user asks otherwise.

## Verification
After each step: start the Vite dev server, open the affected page(s) in a browser,
compare against the corresponding `screens/*.html` reference, and get user confirmation
before proceeding to the next step in the build order.
