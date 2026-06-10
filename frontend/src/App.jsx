import React, { useEffect, useState } from 'react'
import { Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom'
import { usePulse } from './utils/stream'
import Dashboard from './pages/Dashboard'
import Alerts from './pages/Alerts'
import Scanner from './pages/Scanner'
import Trades from './pages/Trades'
import Paper from './pages/Paper'
import Settings from './pages/Settings'
import Brain from './pages/Brain'
import Glossary from './pages/Glossary'

const ICONS = {
  glossary: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 2h8a1 1 0 011 1v10a1 1 0 01-1 1H3"/>
      <path d="M3 2a1 1 0 00-1 1v10a1 1 0 001 1"/>
      <path d="M6 5.5h4M6 8h4M6 10.5h2.5"/>
    </svg>
  ),
  dashboard: (
    <svg viewBox="0 0 16 16" fill="currentColor">
      <rect x="1" y="1" width="6" height="6" rx="1.2"/>
      <rect x="9" y="1" width="6" height="6" rx="1.2"/>
      <rect x="1" y="9" width="6" height="6" rx="1.2"/>
      <rect x="9" y="9" width="6" height="6" rx="1.2"/>
    </svg>
  ),
  alerts: (
    <svg viewBox="0 0 16 16" fill="currentColor">
      <path d="M8 1a5 5 0 00-5 5v2.8L1.6 10.3A.5.5 0 002 11h12a.5.5 0 00.4-.7L13 8.8V6a5 5 0 00-5-5z"/>
      <path d="M6.5 12.5a1.5 1.5 0 003 0h-3z" opacity="0.8"/>
    </svg>
  ),
  scanner: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
      <circle cx="8" cy="8" r="6"/>
      <circle cx="8" cy="8" r="2.5"/>
      <line x1="8" y1="1" x2="8" y2="2.5"/>
      <line x1="8" y1="13.5" x2="8" y2="15"/>
      <line x1="1" y1="8" x2="2.5" y2="8"/>
      <line x1="13.5" y1="8" x2="15" y2="8"/>
    </svg>
  ),
  trades: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>
      <path d="M5 2.5v11M11 2.5v11"/>
    </svg>
  ),
  paper: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 3.5h10v9H3z"/>
      <path d="M5.5 6h5M5.5 8h3.5M5.5 10h2"/>
    </svg>
  ),
  brain: (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2C5.5 2 3.5 4 3.5 6.5c0 1.4.6 2.6 1.6 3.4.3.3.4.6.4 1V12a1 1 0 001 1h3a1 1 0 001-1v-1.1c0-.4.2-.7.4-1A4.5 4.5 0 008 2z"/>
      <path d="M6.5 14h3M5.5 6.5c0-1.4 1.1-2.5 2.5-2.5"/>
    </svg>
  ),
  settings: (
    <svg viewBox="0 0 16 16" fill="currentColor">
      <path fillRule="evenodd" d="M8.837 1.476a1 1 0 00-1.674 0l-.812 1.19a.5.5 0 01-.413.21l-1.44-.07a1 1 0 00-1.024.838l-.243 1.42a.5.5 0 01-.27.372L2.01 6.2a1 1 0 00-.394 1.6l.96 1.066a.5.5 0 010 .67L1.616 10.6a1 1 0 00.394 1.6l.95.764a.5.5 0 01.27.372l.244 1.421a1 1 0 001.024.837l1.44-.07a.5.5 0 01.413.211l.812 1.19a1 1 0 001.674 0l.812-1.19a.5.5 0 01.413-.21l1.44.07a1 1 0 001.024-.838l.243-1.42a.5.5 0 01.27-.372l.951-.764a1 1 0 00.394-1.6l-.96-1.066a.5.5 0 010-.67l.96-1.066a1 1 0 00-.394-1.6l-.95-.764a.5.5 0 01-.27-.372l-.244-1.421a1 1 0 00-1.024-.837l-1.44.07a.5.5 0 01-.413-.211l-.812-1.19zM8 10.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5z" clipRule="evenodd"/>
    </svg>
  ),
}

const NAV = [
  { to: '/',            label: 'Dashboard',   icon: ICONS.dashboard,   end: true },
  { to: '/alerts',      label: 'Alerts',      icon: ICONS.alerts },
  { to: '/paper',       label: 'Paper',       icon: ICONS.paper },
  { to: '/scanner',     label: 'Scanner',     icon: ICONS.scanner },
  { to: '/brain',       label: 'Brain',       icon: ICONS.brain },
  { to: '/trades',      label: 'Trade Log',   icon: ICONS.trades },
  { to: '/settings',    label: 'Settings',    icon: ICONS.settings },
  { to: '/glossary',    label: 'Glossary',    icon: ICONS.glossary },
]

class RouteErrorBoundary extends React.Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="empty route-error">
          <strong>This page failed to render.</strong>
          <span>{this.state.error?.message || 'Unexpected frontend error'}</span>
          <button className="btn btn-primary btn-sm" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

function KillSwitch({ pulse }) {
  const [busy, setBusy] = useState(false)
  const [armed, setArmed] = useState(false)
  const killed = pulse?.kill_switch === true

  const flip = async () => {
    if (!killed && !armed) { setArmed(true); setTimeout(() => setArmed(false), 4000); return }
    setBusy(true)
    try {
      await fetch(killed ? '/api/risk/resume' : '/api/risk/kill', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: killed ? undefined : JSON.stringify({ reason: 'one-click UI kill' }),
      })
    } finally {
      setBusy(false)
      setArmed(false)
    }
  }

  return (
    <button
      className={`btn btn-sm kill-switch${killed ? ' killed' : armed ? ' armed' : ''}`}
      onClick={flip}
      disabled={busy}
      title={killed ? 'Kill switch is ON — click to re-arm trading' : 'Stops all new entries and cancels working live orders'}
      style={{
        width: '100%',
        background: killed ? 'var(--red)' : armed ? 'var(--amber)' : 'transparent',
        border: '1px solid var(--red)',
        color: killed || armed ? '#fff' : 'var(--red)',
        fontWeight: 700,
        letterSpacing: '0.04em',
      }}
    >
      {busy ? '…' : killed ? 'KILLED — RESUME?' : armed ? 'CLICK AGAIN TO KILL' : 'KILL SWITCH'}
    </button>
  )
}

function NotFound() {
  return (
    <div className="empty route-error">
      <strong>Page not found.</strong>
      <span>This route is not part of Sibylla.</span>
      <NavLink className="btn btn-primary btn-sm" to="/">
        Back to Dashboard
      </NavLink>
    </div>
  )
}

export default function App() {
  const location = useLocation()
  const [online, setOnline]         = useState(null)
  const [paper, setPaper]           = useState(true)
  const [brainStatus, setBrainStatus] = useState(null)
  const [pendingAlerts, setPending] = useState(0)
  const [collapsed, setCollapsed]   = useState(false)
  const pulse = usePulse()

  useEffect(() => {
    const check = () =>
      fetch('/health')
        .then(r => r.json())
        .then(() => setOnline(true))
        .catch(() => setOnline(false))
    const loadOverview = () =>
      fetch('/api/overview')
        .then(r => r.json())
        .then(d => setPending(d.alerts_pending || 0))
        .catch(() => {})
    check()
    loadOverview()
    const h = setInterval(check, 20000)
    const o = setInterval(loadOverview, 20000)
    return () => { clearInterval(h); clearInterval(o) }
  }, [])

  useEffect(() => {
    const loadBrain = () => fetch('/api/brain/status')
      .then(r => r.json())
      .then(d => {
        setPaper(d.paper_trading !== false)
        setBrainStatus(d)
      })
      .catch(() => {})
    loadBrain()
    const id = setInterval(loadBrain, 30000)
    return () => clearInterval(id)
  }, [])

  const statusDot  = online === null ? 'dot-paper'  : online ? 'dot-online'  : 'dot-offline'
  const statusText = online === null ? 'Connecting…' : online ? 'System Online' : 'Offline'

  return (
    <div className={`layout${collapsed ? ' sidebar-collapsed' : ''}`}>
      <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
        <div className="sidebar-logo" onClick={() => setCollapsed(c => !c)} title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          <div className="logo-mark" aria-hidden="true">
            <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
              <circle cx="16" cy="16" r="12" stroke="var(--green)" strokeWidth="1.5" opacity="0.4"/>
              <circle cx="16" cy="16" r="7" stroke="var(--green)" strokeWidth="1.5" opacity="0.7"/>
              <circle cx="16" cy="16" r="2.5" fill="var(--green)"/>
              <line x1="16" y1="2" x2="16" y2="8" stroke="var(--green)" strokeWidth="1.2" strokeLinecap="round" opacity="0.5"/>
              <line x1="16" y1="24" x2="16" y2="30" stroke="var(--green)" strokeWidth="1.2" strokeLinecap="round" opacity="0.5"/>
              <line x1="2" y1="16" x2="8" y2="16" stroke="var(--green)" strokeWidth="1.2" strokeLinecap="round" opacity="0.5"/>
              <line x1="24" y1="16" x2="30" y2="16" stroke="var(--green)" strokeWidth="1.2" strokeLinecap="round" opacity="0.5"/>
              <line x1="5.9" y1="5.9" x2="10.1" y2="10.1" stroke="var(--green)" strokeWidth="0.8" strokeLinecap="round" opacity="0.3"/>
              <line x1="21.9" y1="21.9" x2="26.1" y2="26.1" stroke="var(--green)" strokeWidth="0.8" strokeLinecap="round" opacity="0.3"/>
              <line x1="26.1" y1="5.9" x2="21.9" y2="10.1" stroke="var(--green)" strokeWidth="0.8" strokeLinecap="round" opacity="0.3"/>
              <line x1="10.1" y1="21.9" x2="5.9" y2="26.1" stroke="var(--green)" strokeWidth="0.8" strokeLinecap="round" opacity="0.3"/>
            </svg>
          </div>
          {!collapsed && (
            <div className="logo-text">
              <span className="logo-name">SIBYLLA</span>
              <span className="logo-sub">Weather Markets</span>
            </div>
          )}
        </div>

        <nav className="sidebar-nav">
          {NAV.map(({ to, label, icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
              title={collapsed ? label : undefined}
            >
              {icon}
              {!collapsed && <span>{label}</span>}
              {!collapsed && label === 'Alerts' && pendingAlerts > 0 && (
                <span className="nav-count">{pendingAlerts > 99 ? '99+' : pendingAlerts}</span>
              )}
              {collapsed && label === 'Alerts' && pendingAlerts > 0 && (
                <span className="nav-count-dot" />
              )}
            </NavLink>
          ))}
        </nav>

        {!collapsed && (
          <div className="sidebar-footer">
            <div className="footer-row">
              <div className={`status-dot ${statusDot}`} />
              <span>{statusText}</span>
            </div>
            <div className="footer-row">
              <div className={`status-dot ${paper ? 'dot-paper' : 'dot-online'}`} />
              <span>{paper ? 'Paper Mode' : 'Live Trading'}</span>
            </div>
            {pulse && (
              <div className="footer-row" title="Open positions marked at live exit quotes (real-time)">
                <div className={`status-dot ${pulse.feed?.connected ? 'dot-online' : 'dot-offline'}`} />
                <span>
                  {pulse.open_positions} open&nbsp;
                  <strong style={{ color: pulse.open_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {pulse.open_pnl >= 0 ? '+' : ''}{Number(pulse.open_pnl ?? 0).toFixed(2)}
                  </strong>
                </span>
              </div>
            )}
            <KillSwitch pulse={pulse} />
            <div className="footer-row footer-brain">
              <span>Brain {brainStatus?.score ?? '—'}/100</span>
              <small>{brainStatus?.readiness_label || (brainStatus?.entry_quality_ok ? 'Ready for review' : 'Needs better entries')}</small>
            </div>
            <div className="footer-readiness">
              <div className="readiness-bar">
                <div className="readiness-fill" style={{
                  width: `${brainStatus?.score ?? 0}%`,
                  background: (brainStatus?.score ?? 0) >= 80 ? 'var(--green)' : (brainStatus?.score ?? 0) >= 60 ? 'var(--amber)' : 'var(--red)',
                }} />
              </div>
              <small>{
                !brainStatus ? 'Loading trust...'
                : (brainStatus.score ?? 0) >= 90 ? 'Ready for live review'
                : brainStatus.auto_paper_trade_enabled ? `${brainStatus.learning_samples ?? 0} samples, paper bot learning`
                : (brainStatus.score ?? 0) >= 70 ? 'Almost there — entry quality must improve'
                : `${brainStatus.learning_samples ?? 0} samples, need better entry trend`
              }</small>
            </div>
          </div>
        )}
        {collapsed && (
          <div className="sidebar-footer" style={{ alignItems: 'center' }}>
            <div className={`status-dot ${statusDot}`} title={statusText} />
            <div className={`status-dot ${paper ? 'dot-paper' : 'dot-online'}`} title={paper ? 'Paper Mode' : 'Live Trading'} />
          </div>
        )}
      </aside>

      <main className="main">
        <RouteErrorBoundary resetKey={location.pathname}>
          <Routes>
            <Route path="/"            element={<Dashboard />} />
            <Route path="/dashboard"   element={<Navigate to="/" replace />} />
            <Route path="/alerts"      element={<Alerts />} />
            <Route path="/paper"       element={<Paper />} />
            <Route path="/scanner"     element={<Scanner />} />
            <Route path="/brain"       element={<Brain />} />
            <Route path="/trades"      element={<Trades />} />
            <Route path="/settings"    element={<Settings />} />
            <Route path="/glossary"    element={<Glossary />} />
            <Route path="*"            element={<NotFound />} />
          </Routes>
        </RouteErrorBoundary>
      </main>
    </div>
  )
}
