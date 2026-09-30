import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

const ConfirmContext = createContext(null)

export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null)
  const resolver = useRef(null)

  const confirm = useCallback((options) => {
    const opts = typeof options === 'string' ? { message: options } : options
    setState({
      title: opts.title || 'Are you sure?',
      message: opts.message,
      confirmLabel: opts.confirmLabel || 'Confirm',
      danger: opts.danger !== false,
    })
    return new Promise((resolve) => { resolver.current = resolve })
  }, [])

  function close(result) {
    setState(null)
    resolver.current?.(result)
    resolver.current = null
  }

  useEffect(() => {
    if (!state) return
    const onKeyDown = (event) => event.key === 'Escape' && close(false)
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state])

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {state && (
        <div className="config-modal-overlay" onClick={(event) => event.target === event.currentTarget && close(false)}>
          <div className="config-modal confirm-modal" role="alertdialog" aria-modal="true" aria-label={state.title}>
            <header className="config-modal-head">
              <div><h2>{state.title}</h2></div>
              <button type="button" className="modal-close" onClick={() => close(false)} aria-label="Close">×</button>
            </header>
            <div className="config-modal-body"><p className="confirm-message">{state.message}</p></div>
            <footer className="config-modal-footer">
              <button type="button" className="btn" onClick={() => close(false)}>Cancel</button>
              <button type="button" className={`btn ${state.danger ? 'danger' : 'primary'}`} onClick={() => close(true)}>{state.confirmLabel}</button>
            </footer>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  )
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext)
  if (!ctx) throw new Error('useConfirm must be used within a ConfirmProvider')
  return ctx
}
