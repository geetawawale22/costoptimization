import React from 'react'
import { useAuth } from '../auth/AuthContext'
import { usePageCrumb } from '../layouts/ProtectedLayout'

const ACTION_LABELS = {
  create: 'Create',
  view: 'View',
  update: 'Update',
  delete: 'Delete',
}

function formatResource(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

export default function Profile() {
  usePageCrumb('Profile')
  const { user, permissions } = useAuth()

  const initials = user?.name ? user.name.split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase() : ''
  const roleName = user?.role?.name || 'No role assigned'
  const permissionGroups = permissions.reduce((groups, permission) => {
    const resource = permission.resource || 'general'
    groups[resource] = groups[resource] || []
    groups[resource].push(permission.action)
    return groups
  }, {})
  const permissionCount = permissions.length
  const contactItems = [
    { label: 'Full Name', value: user?.name || 'Not available' },
    { label: 'Email', value: user?.email || 'Not available' },
    { label: 'Employee ID', value: user?.employee_id || 'Not configured' },
    { label: 'Job Title', value: user?.job_title || 'Not configured' },
    { label: 'Department', value: user?.department || 'Not configured' },
    { label: 'Company', value: user?.company || 'Not configured' },
    { label: 'Mobile', value: user?.mobile || 'Not configured' },
  ]

  return (
    <div className="profile-page">
      <div className="profile-heading">
        <div>
          <h1 className="page-title">User Profile</h1>
          <p className="page-sub">Review your account identity, assigned role, and access coverage.</p>
        </div>
        <span className="chip blue">Active session</span>
      </div>

      <section className="profile-hero">
        <div className="profile-identity">
          <div className="profile-avatar">{initials}</div>
          <div>
            <h2>{user?.name || 'User'}</h2>
            <p>{user?.job_title || roleName}</p>
            <div className="profile-meta">
              <span>{user?.department || 'Department not configured'}</span>
              <span>{user?.company || 'Company not configured'}</span>
              <span>{user?.email || 'Email not available'}</span>
            </div>
          </div>
        </div>
      </section>

      <div className="profile-layout">
        <section className="card profile-panel">
          <div className="profile-panel-head">
            <div>
              <h3>Personal Information</h3>
              <p>Core user details synced from your platform account.</p>
            </div>
          </div>
          <div className="profile-info-grid">
            {contactItems.map((item) => (
              <div className="profile-info-field" key={item.label}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </div>
            ))}
          </div>
        </section>

        <aside className="profile-side">
          <section className="card profile-panel">
            <div className="profile-panel-head">
              <div>
                <h3>Account Status</h3>
                <p>Current authentication and profile state.</p>
              </div>
            </div>
            <div className="profile-status-list">
              <div>
                <span>Session</span>
                <strong>Signed in</strong>
              </div>
              <div>
                <span>Profile</span>
                <strong>{user?.email ? 'Configured' : 'Incomplete'}</strong>
              </div>
              <div>
                <span>Role Assignment</span>
                <strong>{user?.role?.name ? 'Configured' : 'Pending'}</strong>
              </div>
              <div>
                <span>Member Since</span>
                <strong>{user?.created_at ? new Date(user.created_at).toLocaleDateString() : '—'}</strong>
              </div>
            </div>
          </section>
        </aside>
      </div>

      <section className="card profile-panel">
        <div className="profile-panel-head">
          <div>
            <h3>Role & Access</h3>
            <p>Permissions granted through your assigned role.</p>
          </div>
          <span className="chip blue">{roleName}</span>
        </div>

        {permissionCount === 0 ? (
          <div className="profile-empty-state">
            <strong>No special permissions assigned</strong>
            <span>Your account currently has no explicit permission grants.</span>
          </div>
        ) : (
          <div className="profile-access-grid">
            {Object.entries(permissionGroups).map(([resource, actions]) => (
              <div className="profile-access-row" key={resource}>
                <span>{formatResource(resource)}</span>
                <div>
                  {[...new Set(actions)].map((action) => (
                    <span className={`action-badge ${action}`} key={action}>{ACTION_LABELS[action] || formatResource(action)}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
