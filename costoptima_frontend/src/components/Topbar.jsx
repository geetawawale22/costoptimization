import React from 'react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function Topbar({ crumb }) {
  const { user, logout } = useAuth()
  const [logoutError, setLogoutError] = useState('')
  async function signOut() { try { await logout() } catch (error) { setLogoutError(error.message) } }
  const [open, setOpen] = useState(false)
  const menuRef = useRef(null)
  const initials = user?.name ? user.name.split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase() : ''

  useEffect(() => {
    function closeOnOutsideClick(event) {
      if (menuRef.current && !menuRef.current.contains(event.target)) setOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => document.removeEventListener('mousedown', closeOnOutsideClick)
  }, [])

  const profileRows = [
    ['Employee ID', user?.employee_id],
    ['Email', user?.email],
    ['Department', user?.department],
    ['Job Title', user?.job_title],
    ['Company', user?.company],
    ['Mobile', user?.mobile],
    ['Role', user?.role?.name],
    ['Status', user?.status],
  ]

  return (
    <div className="topbar">
      <div className="crumb" aria-label="Breadcrumb">{String(crumb||'Workspace').replace(/<[^>]*>/g,'').split(' / ').map((part,index,parts)=><React.Fragment key={index}>{index>0&&<span className="crumb-separator" aria-hidden="true">/</span>}{index===parts.length-1?<strong>{part}</strong>:<span>{part}</span>}</React.Fragment>)}</div>
      {logoutError && <p role="alert">{logoutError}</p>}
      <div className="topbar-right">
        <span className="chip blue" title="Signed in role">{user?.role?.name || 'No role'}</span>
        <div className="profile-menu-wrap" ref={menuRef}>
          <button className="avatar avatar-button" title="Open profile" aria-label="Open profile" onClick={() => setOpen((value) => !value)} type="button">
            {initials}
          </button>
          {open && (
            <div className="profile-popover">
              <div className="profile-popover-head">
                <div className="profile-popover-avatar">{initials}</div>
                <div>
                  <strong>{user?.name || 'Signed in user'}</strong>
                  <span>{user?.email || 'No email available'}</span>
                </div>
              </div>
              <div className="profile-popover-list">
                {profileRows.map(([label, value]) => (
                  <div key={label}>
                    <span>{label}</span>
                    <strong>{value || 'Not available'}</strong>
                  </div>
                ))}
              </div>
              <div className="profile-popover-actions">
                <Link className="btn secondary" to="/profile" onClick={() => setOpen(false)}>View Profile</Link>
                <button className="btn danger" type="button" onClick={signOut}>Logout</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
