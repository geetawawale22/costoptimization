import React from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import ProtectedRoute from './components/ProtectedRoute'
import ProtectedLayout from './layouts/ProtectedLayout'
import Login from './pages/Login'
import SsoCallback from './pages/SsoCallback'
import Users from './pages/Users'
import Roles from './pages/Roles'
import Settings from './pages/Settings'
import Profile from './pages/Profile'
import AuditLogs from './pages/AuditLogs'
import Masters from './pages/Masters'
import MasterDetail from './pages/MasterDetail'
import Calculations from './pages/Calculations'
import NewCalculation from './pages/NewCalculation'
import FormulaManagement from './pages/FormulaManagement'
import FtgConverter from './pages/FtgConverter'
import { useAuth } from './auth/AuthContext'
function Allowed({ resource, children }) { const { can } = useAuth(); return can(resource, 'view') ? children : <p role="alert">You do not have permission to view this page.</p> }
export default function App() {
 return <Routes><Route path="/login" element={<Login />} /><Route path="/sso/callback" element={<SsoCallback />} />
    <Route path="/sso" element={<SsoCallback />} />
  <Route path="/" element={<ProtectedRoute><ProtectedLayout /></ProtectedRoute>}>
   <Route index element={<Navigate to="/calculations" replace />} />
   <Route path="calculations" element={<Allowed resource="boms"><Calculations /></Allowed>} />
   <Route path="calculations/new" element={<Allowed resource="boms"><NewCalculation /></Allowed>} />
   <Route path="calculations/:runId" element={<Allowed resource="boms"><NewCalculation /></Allowed>} />
   <Route path="activity-logs" element={<Allowed resource="settings"><AuditLogs /></Allowed>} />
   <Route path="formula-management" element={<Allowed resource="settings"><FormulaManagement /></Allowed>} />
   <Route path="ftg-converter" element={<Allowed resource="converters"><FtgConverter /></Allowed>} />
   <Route path="masters" element={<Allowed resource="masters"><Masters /></Allowed>} />
   <Route path="masters/:code" element={<Allowed resource="masters"><MasterDetail /></Allowed>} />
   <Route path="profile" element={<Profile />} />
   <Route path="settings/audit-logs" element={<Navigate to="/activity-logs" replace />} />
   <Route path="settings/users" element={<Allowed resource="users"><Users /></Allowed>} />
   <Route path="settings/roles-permissions" element={<Allowed resource="roles"><Roles /></Allowed>} />
   <Route path="settings/app-configuration" element={<Allowed resource="settings"><Settings /></Allowed>} />
   <Route path="users" element={<Navigate to="/settings/users" replace />} />
   <Route path="roles" element={<Navigate to="/settings/roles-permissions" replace />} />
   <Route path="settings" element={<Navigate to="/settings/app-configuration" replace />} />
   <Route path="*" element={<p>Page not found.</p>} />
  </Route></Routes>
}
