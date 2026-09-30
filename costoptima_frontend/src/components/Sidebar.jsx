import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { COMMODITY_STRUCTURE, categoryLabel, MASTERS_PAGES, SETTINGS_PAGES, PCM_MASTER_CODE } from '../config/commodityStructure'
import { useAuth } from '../context/AuthContext'
 
function NavItem({ to, icon, label, hasChildren, open, onToggle, active = false }) {
  const chevron = hasChildren && (
    <span
      className={`chev ${open ? 'open' : ''}`}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        onToggle()
      }}
    >
      ▶
    </span>
  )
 
  if (to) {
    return (
      <NavLink to={to} className={({ isActive }) => `nav-item${isActive || active ? ' active' : ''}`} title={label}>
        <span className="ic">{icon}</span>
        <span className="nav-label">{label}</span>
        {chevron}
      </NavLink>
    )
  }
  // Pure group header (Masters / Commodity Types / Settings) — no route, toggles only.
  return (
    <div className={`nav-item${active ? ' active' : ''}`} onClick={onToggle} title={label}>
      <span className="ic">{icon}</span>
      <span className="nav-label">{label}</span>
      {chevron}
    </div>
  )
}
 
export default function Sidebar() {
  const location = useLocation()
  const { can } = useAuth()
  const [expanded, setExpanded] = useState(new Set(['commodityTypes', 'pcmMaster']))
  const canViewConverters = can('outputs', 'view')
  const converterActive = location.pathname.startsWith('/pdf-converter')
    || location.pathname.startsWith('/converter/ftg-fixture-input-to-rpa')
    || location.pathname.startsWith('/converter/ftg-fixture-ppt-to-input-excel')
  const converterOpen = expanded.has('converter') || converterActive
 
  const toggle = (key) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }
 
  return (
    <div className="sidebar">
      <div className="brand">
        <div className="brand-icon">▦</div>
        <div className="brand-text">
          <b>CostOptima</b>
          <span>Cost Management</span>
        </div>
      </div>
 
      <div className="nav-tree">
        <NavItem to="/calculations" icon="☰" label="Calculations" />
        <NavItem to="/new-calculation" icon="＋" label="New Calculation" />
        <NavItem hasChildren icon="⛁" label="Masters" open={expanded.has('masters')} onToggle={() => toggle('masters')} />
        <div className="nav-children lvl1" style={{ display: expanded.has('masters') ? 'block' : 'none' }}>
          {MASTERS_PAGES.map((m) => (
            <NavItem key={m.code} to={`/masters/${m.code}`} icon="▫" label={m.label} />
          ))}
        </div>
        {canViewConverters && (
          <>
            <NavItem
              hasChildren
              icon="▤"
              label="Converter"
              open={converterOpen}
              active={converterActive}
              onToggle={() => toggle('converter')}
            />
            <div className="nav-children lvl1" style={{ display: converterOpen ? 'block' : 'none' }}>
              <NavItem to="/pdf-converter" icon="▫" label="PDF Converter" />
              <NavItem to="/converter/ftg-fixture-input-to-rpa" icon="▫" label="FTG Fixture - Input to RPA" />
              <NavItem to="/converter/ftg-fixture-ppt-to-input-excel" icon="▫" label="FTG Fixture - PPT to Input Excel" />
            </div>
          </>
        )}

        {/* Commodity Types -> PCM Master (its own page + parent grouping node) -> each commodity -> its categories */}
        <NavItem
          hasChildren
          icon="◈"
          label="Commodity Types"
          open={expanded.has('commodityTypes')}
          onToggle={() => toggle('commodityTypes')}
        />
        <div className="nav-children lvl1" style={{ display: expanded.has('commodityTypes') ? 'block' : 'none' }}>
          <NavItem
            to={`/commodity-types/${PCM_MASTER_CODE}`}
            icon="▦"
            label="PCM Master"
            hasChildren
            open={expanded.has('pcmMaster')}
            onToggle={() => toggle('pcmMaster')}
          />
          <div className="nav-children lvl2" style={{ display: expanded.has('pcmMaster') ? 'block' : 'none' }}>
            {COMMODITY_STRUCTURE.map((c) => {
              const nodeKey = `commodity-${c.code}`
              return (
                <div key={c.code}>
                  <NavItem hasChildren icon="◇" label={c.name} open={expanded.has(nodeKey)} onToggle={() => toggle(nodeKey)} />
                  <div className="nav-children lvl2" style={{ display: expanded.has(nodeKey) ? 'block' : 'none' }}>
                    {c.categories.map((cat) => (
                      <NavItem
                        key={cat}
                        to={`/commodity-types/${c.code}/${cat}`}
                        icon={cat === 'self' ? '▦' : '•'}
                        label={categoryLabel(cat, c.name)}
                      />
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
 
      <div className="nav-bottom">
        <NavItem to="/history" icon="↻" label="History" />
        <NavItem hasChildren icon="⚙" label="Settings" open={expanded.has('settings')} onToggle={() => toggle('settings')} />
        <div className="nav-children lvl1" style={{ display: expanded.has('settings') ? 'block' : 'none' }}>
          {SETTINGS_PAGES.map((s) => (
            <NavItem key={s.key} to={`/settings/${s.key}`} icon="▫" label={s.label} />
          ))}
        </div>
 
        <NavItem to="/profile" icon="◍" label="Profile" />
      </div>
    </div>
  )
}
