import React from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function ProtectedRoute({ children }) {
  const { user, loading, authError, reload } = useAuth()
  const location = useLocation()

  if (loading) return <div className="loading">Loading...</div>
  if (authError) return <div className="loading" role="alert"><p>{authError}</p><button className="btn" onClick={reload}>Retry</button></div>
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />
  return children
}
