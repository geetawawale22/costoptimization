import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { AuthProvider } from './auth/AuthContext.jsx'
import App from './App.jsx'
import { ToastProvider } from './context/ToastContext'
import { ConfirmProvider } from './context/ConfirmContext'
import './styles.css'

createRoot(document.getElementById('root')).render(
  <BrowserRouter><AuthProvider><ToastProvider><ConfirmProvider><App /></ConfirmProvider></ToastProvider></AuthProvider></BrowserRouter>,
)