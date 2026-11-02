import React from 'react'
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import * as authApi from '../services/api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [permissions, setPermissions] = useState([])
  const [loading, setLoading] = useState(true)
  const [authError, setAuthError] = useState('')
  const [appearance, setAppearance] = useState({ 'app.display_name': 'Cost Pilot', 'ui.page_size': 25 })
  async function reloadAppearance() { const value=await authApi.appearance(); setAppearance({...value,'app.display_name':String(value['app.display_name']||'Cost Pilot').replace(/CostOptima/gi,'Cost Pilot')}) }

  const applyMe = (me) => {
    const { permissions: perms, ...rest } = me
    authApi.setCsrf(me.csrf_token)
    setUser(rest)
    reloadAppearance().catch(() => {})
    setPermissions(perms || [])
  }

  const loadUser = useCallback(async () => {
    setLoading(true)
    setAuthError('')
    try {
      const me = await authApi.me()
      applyMe(me)
      return me
    } catch (error) {
      if(error.status === 401) {
        setUser(null)
        setPermissions([])
      } else setAuthError('Unable to check your session. Your current page has been kept. Please retry.')
      return null
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    loadUser()
    const expire = () => { setUser(null); setPermissions([]) }
    window.addEventListener('costoptima:expired', expire)
    return () => window.removeEventListener('costoptima:expired', expire)
  }, [loadUser])

  async function login(email, password) {
    const me = await authApi.login(email, password)
    applyMe(me)
  }

  async function logout() {
    await authApi.logout()
    authApi.setCsrf(null)
    setUser(null)
    setPermissions([])
  }


  /** Mirrors the backend's require_permission(resource, action) check exactly —
      resource is a page/model (masters, commodity_config, boms, converters,
      settings, users, roles, permissions, workbook_config, merge); action is
      create/view/update/delete. */
  function can(resource, action) {
    return permissions.some((p) => p.resource === resource && p.action === action)
  }

  return (
    <AuthContext.Provider value={{ user, permissions, loading, authError, login, logout, can, appearance, reloadAppearance, reload: loadUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
