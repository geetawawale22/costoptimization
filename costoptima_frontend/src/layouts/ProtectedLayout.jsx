import React from 'react'
import { Outlet, useOutletContext, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Sidebar from '../components/Sidebar'
import Topbar from '../components/Topbar'

export default function ProtectedLayout() {
  const { pathname } = useLocation()
  const [crumb, setCrumb] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { const saved=localStorage.getItem('costoptima.sidebar.collapsed'); return saved===null ? window.matchMedia('(max-width: 800px)').matches : saved==='true' } catch { return window.matchMedia('(max-width: 800px)').matches }
  })
  useEffect(() => { if (window.matchMedia('(max-width: 800px)').matches) setSidebarCollapsed(true) }, [pathname])
  function toggleSidebar() {
    setSidebarCollapsed(value => {
      try { localStorage.setItem('costoptima.sidebar.collapsed', String(!value)) } catch {}
      return !value
    })
  }
  return (
    <div className="app-shell">
      <Sidebar collapsed={sidebarCollapsed} onToggleCollapse={toggleSidebar} />
      {!sidebarCollapsed&&<button className="mobile-nav-backdrop" aria-label="Close navigation" onClick={toggleSidebar} />}
      <div className="main-col">
        <Topbar crumb={crumb} />
        <div className="content">
          <Outlet context={{ setCrumb }} />
        </div>
      </div>
    </div>
  )
}

export function usePageCrumb(crumb) {
  const { setCrumb } = useOutletContext()
  useEffect(() => {
    setCrumb(crumb)
  }, [crumb, setCrumb])
}
