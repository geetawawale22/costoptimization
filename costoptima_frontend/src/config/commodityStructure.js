/**
 * Mirrors app/seed.py's COMMODITY_STRUCTURE on the backend exactly — commodity code,
 * display name, which categories it has (master_user / pce), and its master file list.
 * Adding a new commodity later means adding one entry here AND seeding the matching
 * WorkbookType rows on the backend — no other frontend code changes needed, since
 * every page below is powered by the generic <WorkbookTable> component.
 */
export const CATEGORY_LABEL = {
  self: null, // special-cased: label uses the commodity's own name, not a fixed label
  master_user: 'Master User',
  pce: 'PCE',
}

/** Returns the display label for a commodity + category, matching pcm_v5.html's rule exactly. */
export function categoryLabel(category, commodityName) {
  return category === 'self' ? commodityName : CATEGORY_LABEL[category]
}

export const COMMODITY_STRUCTURE = [
  { code: 'forging', name: 'Casting Forging Machining', categories: ['self', 'master_user', 'pce'] },
  { code: 'dac_hvac_other', name: 'DAC HVAC Other', categories: ['self', 'master_user', 'pce'] },
  { code: 'ee', name: 'E&E', categories: ['self', 'master_user', 'pce'] },
  { code: 'seat_mechanism', name: 'Seat Mechanism', categories: ['self', 'master_user', 'pce'] },
  { code: 'sheet_metal', name: 'SheetMetal', categories: ['self', 'master_user', 'pce'] },
  { code: 'trim', name: 'Trim', categories: ['self', 'master_user', 'pce'] },
  { code: 'soft_trim', name: 'Trim Soft', categories: ['self', 'master_user'] },
  { code: 'gdc_pdc', name: 'GDC PDC', categories: ['self', 'master_user'] },
  { code: 'special_process', name: 'Special Process', categories: ['self', 'master_user'] },
]

/** Deterministic workbook-type code for a commodity + category, matches the backend's f"{code}_{category}" */
export const workbookCodeFor = (commodityCode, category) => `${commodityCode}_${category}`

/** The 6 flat Masters pages shown in the sidebar — matches pcm_v5.html's MASTERS_PAGES exactly
    (Authority Matrix, Logbook, and Shift Factor Rate Prev were removed per an earlier revision). */
export const MASTERS_PAGES = [
  { code: 'column_position', label: 'Column Position' },
  { code: 'master_output_matrix', label: 'Master Output Matrix' },
  { code: 'sand_columns', label: 'Sand Columns' },
  { code: 'sbc_format', label: 'SBC Format' },
  { code: 'sheet_name', label: 'Sheet Name' },
  { code: 'shift_factor_rate', label: 'Shift Factor Rate' },
]

export const PCM_MASTER_CODE = 'pcm_master'

export const SETTINGS_PAGES = [
  { key: 'formula-repository', label: 'Formula Repository' },
  { key: 'master-configuration', label: 'Master Configuration' },
  { key: 'workbook-configuration', label: 'Workbook Configuration' },
  { key: 'output-templates', label: 'Output Templates' },
  { key: 'app-configuration', label: 'Key-Value Configuration' },
  { key: 'audit-logs', label: 'Audit Logs' },
  { key: 'users', label: 'Users' },
  { key: 'roles-permissions', label: 'Roles & Permissions' },
]