import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ToastProvider } from './context/ToastContext'
import { ConfirmProvider } from './context/ConfirmContext'
import ProtectedRoute from './components/ProtectedRoute'
import Layout from './components/Layout'

import Login from './pages/Login'
import SsoCallback from './pages/SsoCallback'
import Calculations from './pages/Calculations'
import NewCalculation from './pages/NewCalculation'
import Masters from './pages/Masters'
import CommodityTypes from './pages/CommodityTypes'
import History from './pages/History'
import Profile from './pages/Profile'
import PdfConverter from './pages/PdfConverter'
import ConverterPlaceholder from './pages/ConverterPlaceholder'

import FormulaRepository from './pages/settings/FormulaRepository'
import MasterConfiguration from './pages/settings/MasterConfiguration'
import AuditLogs from './pages/settings/AuditLogs'
import Users from './pages/settings/Users'
import RolesPermissions from './pages/settings/RolesPermissions'
import WorkbookConfig from './pages/settings/WorkbookConfig'
import OutputTemplates from './pages/settings/OutputTemplates'
import AppConfig from './pages/settings/AppConfig'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
        <ConfirmProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/sso/callback" element={<SsoCallback />} />

            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Layout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Navigate to="/calculations" replace />} />
              <Route path="calculations" element={<Calculations />} />
              <Route path="new-calculation" element={<NewCalculation />} />

              <Route path="masters/:typeCode" element={<Masters />} />

              <Route path="commodity-types/:commodityCode" element={<CommodityTypes />} />
              <Route path="commodity-types/:commodityCode/:category" element={<CommodityTypes />} />
              <Route path="history" element={<History />} />
              <Route path="pdf-converter" element={<PdfConverter />} />
              <Route path="converter/ftg-fixture-input-to-rpa" element={<ConverterPlaceholder title="FTG Fixture - Input to RPA" />} />
              <Route path="converter/ftg-fixture-ppt-to-input-excel" element={<ConverterPlaceholder title="FTG Fixture - PPT to Input Excel" />} />
              <Route path="profile" element={<Profile />} />

              <Route path="settings/formula-repository" element={<FormulaRepository />} />
              <Route path="settings/master-configuration" element={<MasterConfiguration />} />
              <Route path="settings/audit-logs" element={<AuditLogs />} />
              <Route path="settings/users" element={<Users />} />
              <Route path="settings/roles-permissions" element={<RolesPermissions />} />
              <Route path="settings/workbook-configuration" element={<WorkbookConfig />} />
              <Route path="settings/output-templates" element={<OutputTemplates />} />
              <Route path="settings/app-configuration" element={<AppConfig />} />

              <Route path="*" element={<div style={{ padding: 40 }}>Page not found.</div>} />
            </Route>
          </Routes>
        </ConfirmProvider>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
