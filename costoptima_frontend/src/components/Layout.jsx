import { Outlet, useOutletContext } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Sidebar from './Sidebar'
import Topbar from './Topbar'

export default function Layout() {
  const [crumb, setCrumb] = useState('')
  return (
    <div className="app-shell">
      <Sidebar />
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
