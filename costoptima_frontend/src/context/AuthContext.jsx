import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { useDispatch } from 'react-redux'
import * as authApi from '../api/auth'
import { setAuth, clearAuth } from '../store/authSlice'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const dispatch = useDispatch()
  const [user, setUser] = useState(null)
  const [permissions, setPermissions] = useState([])
  const [loading, setLoading] = useState(true)

  const applyMe = (me) => {
    const { permissions: perms, ...rest } = me
    setUser(rest)
    setPermissions(perms || [])
    dispatch(setAuth({ user: rest, permissions: perms || [] }))
  }

  const loadUser = useCallback(async () => {
    try {
      const me = await authApi.me()
      applyMe(me)
    } catch (_) {
      setUser(null)
      setPermissions([])
      dispatch(clearAuth())
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    loadUser()
  }, [loadUser])

  async function login(email, password) {
    const me = await authApi.login(email, password)
    applyMe(me)
  }

  function logout() {
    authApi.logout().catch(() => {})
    setUser(null)
    setPermissions([])
    dispatch(clearAuth())
  }

  /** Mirrors the backend's require_permission(resource, action) check exactly —
      resource is a page/model (masters, commodity_config, calculations, outputs,
      history, settings, users, roles, permissions, workbook_config, merge); action is
      create/view/update/delete. */
  function can(resource, action) {
    return permissions.some((p) => p.resource === resource && p.action === action)
  }

  return (
    <AuthContext.Provider value={{ user, permissions, loading, login, logout, can, reload: loadUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}