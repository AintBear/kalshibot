import React, { useEffect, useState, useCallback } from 'react'

function Toggle({ checked, onChange, danger }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={!!checked} onChange={e => onChange(e.target.checked)} />
      <span className={`toggle-slider${danger ? ' danger' : ''}`} />
    </label>
  )
}

function Section({ title, children }) {
  return (
    <div className="settings-section">
      <div className="section-hd">{title}</div>
      <div className="card">{children}</div>
    </div>
  )
}

function Row({ label, hint, children }) {
  return (
    <div className="settings-row">
      <div className="settings-label">
        <span>{label}</span>
        {hint && <span>{hint}</span>}
      </div>
      <div className="settings-control">{children}</div>
    </div>
  )
}

function NumInput({ value, onChange, min, max, step = 1 }) {
  return (
    <input
      type="number"
      className="num-input"
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={e => onChange(parseFloat(e.target.value) || min)}
    />
  )
}

function Accordion({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="accordion-item">
      <button className="accordion-btn" onClick={() => setOpen(o => !o)}>
        {title}
        <svg className={`accordion-chevron${open ? ' open' : ''}`} width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M2 5l5 5 5-5" />
        </svg>
      </button>
      {open && <div className="accordion-body">{children}</div>}
    </div>
  )
}

function SmartRiskPanel({ brain, form, set }) {
  const [effective, setEffective] = useState(null)
  useEffect(() => {
    fetch('/api/settings/effective-risk').then(r => r.json()).then(setEffective).catch(() => {})
  }, [brain?.score])

  const score = brain?.score ?? 0
  const scl = effective?.risk_scalar ?? 0.35
  const pct = Math.round(scl * 100)
  const barColor = scl >= 0.8 ? 'var(--green)' : scl >= 0.5 ? 'var(--amber)' : 'var(--red)'

  return (
    <>
      <div className="risk-recommendation" style={{ gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
          <strong style={{ fontSize: '1.1rem' }}>Risk Level: {pct}%</strong>
          <span className="badge" style={{ background: barColor + '22', color: barColor, border: '1px solid ' + barColor + '44' }}>
            {scl >= 0.8 ? 'Full' : scl >= 0.5 ? 'Moderate' : scl >= 0.2 ? 'Conservative' : 'Minimal'}
          </span>
        </div>
        <div className="readiness-bar" style={{ height: 8, marginBottom: 6 }}>
          <div className="readiness-fill" style={{ width: `${pct}%`, background: barColor }} />
        </div>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-2)' }}>{effective?.reason || 'Computing...'}</span>
        {effective?.adaptive_factors && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
            {effective.adaptive_factors.map((f, i) => (
              <span key={i} className="badge badge-muted" style={{ fontSize: '0.65rem' }}>{f}</span>
            ))}
          </div>
        )}
      </div>
      {effective && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginTop: 8 }}>
          <div className="metric-pill">
            <span>Kelly fraction</span>
            <strong>{effective.effective.kelly_fraction}</strong>
            <small style={{ color: 'var(--text-muted)', fontSize: '0.6rem' }}>base {effective.base.kelly_fraction}</small>
          </div>
          <div className="metric-pill">
            <span>Risk / trade</span>
            <strong>{(effective.effective.max_trade_risk_fraction * 100).toFixed(1)}%</strong>
            <small style={{ color: 'var(--text-muted)', fontSize: '0.6rem' }}>base {(effective.base.max_trade_risk_fraction * 100).toFixed(1)}%</small>
          </div>
          <div className="metric-pill">
            <span>Max contracts</span>
            <strong>{effective.effective.max_contracts_per_trade}</strong>
            <small style={{ color: 'var(--text-muted)', fontSize: '0.6rem' }}>base {effective.base.max_contracts_per_trade}</small>
          </div>
        </div>
      )}
      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: 'pointer', color: 'var(--text-2)', fontSize: '0.72rem' }}>Override base values</summary>
        <div style={{ marginTop: 8 }}>
          <Row label="Base Max Contracts" hint="Upper limit before adaptive scaling.">
            <NumInput value={form.max_contracts_per_trade || 5} onChange={v => set('max_contracts_per_trade', parseInt(v) || 1)} min={1} max={10} />
          </Row>
          <Row label="Base Kelly Fraction" hint="Kelly multiplier before adaptive scaling.">
            <NumInput value={form.kelly_fraction} onChange={v => set('kelly_fraction', v)} min={0.05} max={1} step={0.05} />
          </Row>
          <Row label="Base Max Risk %" hint="Maximum fraction of balance risked per trade before scaling.">
            <NumInput value={form.max_trade_risk_fraction} onChange={v => set('max_trade_risk_fraction', v)} min={0.005} max={0.05} step={0.005} />
          </Row>
        </div>
      </details>
    </>
  )
}

function CleanupPanel() {
  const [result, setResult] = useState(null)
  const [running, setRunning] = useState(false)
  const [backfillResult, setBackfillResult] = useState(null)
  const [backfilling, setBackfilling] = useState(false)

  const run = () => {
    setRunning(true)
    setResult(null)
    fetch('/api/alerts/cleanup', { method: 'POST' })
      .then(r => r.json())
      .then(d => setResult(d))
      .catch(e => setResult({ error: e.message }))
      .finally(() => setRunning(false))
  }

  const backfill = () => {
    setBackfilling(true)
    setBackfillResult(null)
    fetch('/api/trades/backfill-settlements', { method: 'POST' })
      .then(r => r.json())
      .then(d => setBackfillResult(d))
      .catch(e => setBackfillResult({ error: e.message }))
      .finally(() => setBackfilling(false))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <p style={{ fontSize: '0.78rem', color: 'var(--text-2)', lineHeight: 1.6 }}>
        Removes expired alerts older than 3 days, expires alerts for markets that have already
        closed, and closes stuck open paper trades on settled markets.
      </p>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button className="btn btn-primary btn-sm" onClick={run} disabled={running} style={{ width: 'fit-content' }}>
          {running ? 'Cleaning...' : 'Run Cleanup'}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={backfill} disabled={backfilling} style={{ width: 'fit-content' }}>
          {backfilling ? 'Backfilling...' : 'Backfill Settlements'}
        </button>
      </div>
      {result && !result.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-2)', fontFamily: 'var(--font-mono)' }}>
          Deleted {result.deleted_expired_alerts} old alerts ·
          Expired {result.expired_closed_market_alerts} market-closed alerts ·
          Closed {result.closed_stuck_trades} stuck trades
        </div>
      )}
      {result?.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--red)' }}>{result.error}</div>
      )}
      {backfillResult && !backfillResult.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-2)', fontFamily: 'var(--font-mono)' }}>
          Backfilled {backfillResult.backfilled} trades · Skipped {backfillResult.skipped} (awaiting settlement)
          {backfillResult.errors?.length > 0 && ` · ${backfillResult.errors.length} errors`}
        </div>
      )}
      {backfillResult?.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--red)' }}>{backfillResult.error}</div>
      )}
    </div>
  )
}

const DEFAULT = {
  paper_trading: true,
  auto_paper_trade_enabled: false,
  automation_enabled: false,
  auto_trade_enabled: false,
  stop_loss_pct: 0.5,
  take_profit_pct: 0.5,
  kelly_fraction: 0.25,
  max_contracts_per_trade: 5,
  paper_starting_balance: 500,
  daily_loss_limit_paper: 0,
  kalshi_key_id: '',
  kalshi_private_key_path: '',
  noaa_token: '',
  nws_user_agent: 'sibylla-weather-bot/1.0 contact@sibylla.local',
  accuweather_api_key: '',
  scan_interval_minutes: 15,
  stale_alert_expiry_minutes: 60,
}

function AutoTradePanel() {
  const [status, setStatus] = useState(null)
  const [running, setRunning] = useState(false)

  useEffect(() => {
    fetch('/api/auto-trade/status').then(r => r.json()).then(setStatus).catch(() => {})
    const id = setInterval(() => {
      fetch('/api/auto-trade/status').then(r => r.json()).then(setStatus).catch(() => {})
    }, 30000)
    return () => clearInterval(id)
  }, [])

  const runNow = () => {
    setRunning(true)
    fetch('/api/auto-trade/run', { method: 'POST' })
      .then(r => r.json())
      .then(d => { alert(d.skipped ? `Skipped: ${d.reason}` : `Entered ${d.total_entered || 0} trade(s)`) })
      .finally(() => { setRunning(false); fetch('/api/auto-trade/status').then(r => r.json()).then(setStatus) })
  }

  const resetCB = () => {
    fetch('/api/auto-trade/reset-circuit-breaker', { method: 'POST' }).then(() =>
      fetch('/api/auto-trade/status').then(r => r.json()).then(setStatus)
    )
  }

  if (!status) return null
  const paperActive = status.paper_auto_enabled && status.paper_ready
  const liveReady = status.live_auto_enabled && status.live_ready
  const ready = paperActive || liveReady
  const statusLabel = paperActive ? 'Paper bot running' : liveReady ? 'Real money ready' : status.live_auto_enabled ? 'Live requested, gated' : 'Off'

  return (
    <div className="auto-trade-panel">
      <div className="auto-trade-panel-hd">
        <span>Paper Bot Status</span>
        <span className={`badge ${ready ? 'badge-green' : status.live_auto_enabled ? 'badge-red' : 'badge-amber'}`}>{statusLabel}</span>
      </div>
      <div className="auto-trade-grid">
        <div><span>Paper entries</span><strong style={{ color: status.paper_auto_enabled ? 'var(--green)' : 'var(--text-2)' }}>{status.paper_auto_enabled ? 'Running' : 'Off'}</strong></div>
        <div><span>Bot Trust</span><strong style={{ color: status.brain_score >= 80 ? 'var(--green)' : status.brain_score >= 60 ? 'var(--amber)' : 'var(--red)' }}>{status.brain_score}/100</strong></div>
        <div><span>Avg Entry Move</span><strong style={{ color: status.avg_clv >= 0 ? 'var(--green)' : 'var(--red)' }}>{Number(status.avg_clv || 0).toFixed(1)}¢</strong></div>
        <div><span>Paper P&amp;L</span><strong style={{ color: status.realized_pnl_paper >= 0 ? 'var(--green)' : 'var(--red)' }}>{status.realized_pnl_paper >= 0 ? '+' : ''}${Math.abs(Number(status.realized_pnl_paper || 0)).toFixed(2)}</strong></div>
        <div><span>Real-money gate</span><strong style={{ color: status.entry_quality_ok ? 'var(--green)' : 'var(--amber)' }}>{status.entry_quality_ok ? 'Passing' : 'Off until quality improves'}</strong></div>
        <div><span>Circuit</span><strong style={{ color: status.circuit_tripped ? 'var(--red)' : 'var(--green)' }}>{status.circuit_tripped ? `Tripped (${status.consecutive_losses})` : 'OK'}</strong></div>
      </div>
      {status.blockers?.length > 0 && (
        <ul style={{ fontSize: '0.75rem', color: 'var(--text-2)', marginTop: 8, paddingLeft: 16, lineHeight: 1.7 }}>
          {status.blockers.map((b, i) => <li key={i}>{b}</li>)}
        </ul>
      )}
      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
        <button className="btn btn-ghost btn-sm" onClick={runNow} disabled={running}>
          {running ? 'Running…' : 'Run Auto-Entry Now'}
        </button>
        {status.circuit_tripped && (
          <button className="btn btn-danger btn-sm" onClick={resetCB}>Reset Circuit Breaker</button>
        )}
      </div>
    </div>
  )
}

export default function Settings() {
  const [form, setForm]     = useState(null)
  const [brain, setBrain]   = useState(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved]   = useState(false)
  const [dirty, setDirty]   = useState(false)

  const load = useCallback(() =>
    fetch('/api/settings')
      .then(r => r.json())
      .then(d => { setForm({ ...DEFAULT, ...d }); setDirty(false) })
      .catch(console.error), [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    fetch('/api/brain/status').then(r => r.json()).then(setBrain).catch(() => {})
  }, [])

  const set = (key, val) => {
    setForm(f => ({ ...DEFAULT, ...(f || {}), [key]: val }))
    setDirty(true)
  }

  const save = () => {
    setSaving(true)
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
      .then(() => { setSaved(true); setDirty(false); setTimeout(() => setSaved(false), 3000) })
      .finally(() => setSaving(false))
  }

  if (!form) return (
    <div className="settings-page">
      <div className="page-hd"><div className="page-title">Settings</div></div>
      <div className="skeleton" style={{ height: 260, borderRadius: 10 }} />
    </div>
  )

  const kalshiOk = form.kalshi_key_id_configured || form.kalshi_key_id
  const brainScore = brain?.score ?? 0
  const riskRecommendation = brainScore >= 80
    ? 'Recommended: Kelly 0.25. Paper auto can learn; live orders still need positive paper P&L, good-entry rate, and recent entry quality.'
    : brainScore >= 60
      ? 'Recommended: Kelly 0.15. The brain still needs cleaner recent entries before auto sizing.'
      : 'Recommended: Kelly 0.15 while the brain is still proving entry quality.'

  return (
    <div className="settings-page">
      {/* Sticky save bar */}
      <div className="save-bar">
        <div>
          <div style={{ fontSize: '0.9rem', fontWeight: 700 }}>Settings</div>
          {dirty && <div style={{ fontSize: '0.72rem', color: 'var(--amber)' }}>Unsaved changes</div>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {saved && <span className="save-success">✓ Saved</span>}
          {saving && <div className="spinner" />}
          <button className="btn btn-primary" onClick={save} disabled={saving || !dirty}>
            {saving ? 'Saving…' : 'Save Changes'}
          </button>
        </div>
      </div>

      {/* Status snapshot */}
      <div className="settings-quick">
        <div>
          <span className="pulse-kicker">Current Status</span>
          <strong>{form.paper_trading ? 'Paper trading active' : 'Live trading mode'} · live auto {form.auto_trade_enabled ? 'ON' : 'off'}</strong>
          <p>Live orders require trust 90+, positive paper P&amp;L, positive recent entry quality, and a market type that has earned sizing.</p>
        </div>
        <div className="pulse-tape">
          <div><span>Kalshi</span><strong style={{ color: kalshiOk ? 'var(--green)' : 'var(--amber)' }}>{kalshiOk ? 'Connected' : 'Missing key'}</strong></div>
          <div><span>Mode</span><strong>{form.paper_trading ? 'Paper' : 'Live'}</strong></div>
          <div><span>Entries</span><strong>{form.auto_paper_trade_enabled ? 'Paper bot' : 'Manual'}</strong></div>
          <div><span>Sizing</span><strong>Adaptive</strong></div>
        </div>
      </div>

      {/* Trading Mode */}
      <Section title="Trading Mode">
        <Row label="Trading Mode" hint="Manual: you approve every trade. Auto scan only: scans run automatically, you still approve entries. Paper bot: scans and small paper entries can run automatically for learning.">
          <div className="mode-selector">
            {[
              { key: 'manual',    label: 'Manual',   hint: 'You approve each trade',          auto: false, sched: false },
              { key: 'semiauto',  label: 'Auto scan only', hint: 'Auto scan, manual entries',       auto: false, sched: true  },
              { key: 'autopaper', label: 'Paper bot',  hint: 'Auto scan plus automatic paper-learning entries', auto: true,  sched: true  },
            ].map(({ key, label, hint, auto, sched }) => {
              const active = form.automation_enabled === sched && form.auto_paper_trade_enabled === auto
              return (
                <button
                  key={key}
                  className={`mode-btn${active ? ' active' : ''}`}
                  onClick={() => { set('automation_enabled', sched); set('auto_paper_trade_enabled', auto) }}
                  title={hint}
                >
                  {label}
                </button>
              )
            })}
          </div>
        </Row>
        <Row label="Paper Trading" hint="Log trades without using real money. Leave on until entry quality is consistently positive over 50+ trades.">
          <span style={{ fontSize: '0.78rem', color: form.paper_trading ? 'var(--green)' : 'var(--text-muted)', marginRight: 8 }}>
            {form.paper_trading ? 'Active' : 'Off'}
          </span>
          <Toggle checked={form.paper_trading} onChange={v => set('paper_trading', v)} />
        </Row>
        <Row label="Live Kalshi Auto Trading" hint="Place real Kalshi orders automatically. The backend still blocks entries unless score is 90+, paper P&L is positive, and entry quality passes.">
          <Toggle checked={form.auto_trade_enabled} onChange={v => set('auto_trade_enabled', v)} danger />
        </Row>
        {form.auto_trade_enabled && (
          <div className="danger-zone" style={{ marginTop: 12 }}>
          <p>Auto trading is <strong>ON</strong>. Real orders can be placed on Kalshi only if the backend live gates pass.</p>
            <button className="btn btn-danger btn-sm" onClick={() => set('auto_trade_enabled', false)}>Disable Auto Trading</button>
          </div>
        )}
      </Section>

      {(form.auto_paper_trade_enabled || form.auto_trade_enabled) && <AutoTradePanel />}

      {/* Risk Controls */}
      <Section title="Adaptive Risk Controls">
        <SmartRiskPanel brain={brain} form={form} set={set} />
      </Section>

      {/* API Connections */}
      <Section title="API Connections">
        <Row label="Kalshi API Key ID" hint="Your Kalshi member key ID — required for live trading and balance checks.">
          {form.kalshi_key_id_configured ? (
            <div className="api-key-locked">
              <span className="api-key-status configured">● Configured</span>
              <span className="api-key-hint">Key is set in your local config file</span>
            </div>
          ) : (
            <input
              type="password"
              className="text-input"
              placeholder="key_xxxxxxxx"
              value={form.kalshi_key_id}
              onChange={e => set('kalshi_key_id', e.target.value)}
            />
          )}
        </Row>
        <Row label="Kalshi Private Key Path" hint="Absolute path to your RSA .pem file on this machine.">
          {form.kalshi_key_id_configured ? (
            <div className="api-key-locked">
              <span className="api-key-status configured">● Configured</span>
              <span className="api-key-hint">Edit config/settings.json directly to change</span>
            </div>
          ) : (
            <input
              type="text"
              className="text-input"
              placeholder="/path/to/kalshi_private_key.pem"
              value={form.kalshi_private_key_path}
              onChange={e => set('kalshi_private_key_path', e.target.value)}
            />
          )}
        </Row>
        <Row label="AccuWeather API Key" hint="Optional. When available, it is blended with NOAA/NWS instead of replacing it.">
          {form.accuweather_api_key_configured ? (
            <div className="api-key-locked">
              <span className="api-key-status configured">● Configured</span>
              <span className="api-key-hint">Edit config/settings.json directly to change</span>
            </div>
          ) : (
            <input
              type="password"
              className="text-input"
              placeholder="(optional)"
              value={form.accuweather_api_key}
              onChange={e => set('accuweather_api_key', e.target.value)}
            />
          )}
        </Row>
        <div style={{ marginTop: 10, fontSize: '0.73rem', color: 'var(--text-2)', padding: '8px 11px', background: 'var(--green-dim)', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-accent)' }}>
          Once configured, API keys are locked in the UI for security. To change keys, edit <code style={{ color: 'var(--sky)' }}>config/settings.json</code> directly on disk.
        </div>
      </Section>

      {/* Advanced — collapsed by default */}
      <div className="settings-section">
        <div className="section-hd">Advanced</div>
        <div className="accordion">
          <Accordion title="Scan & Timing">
            <Row label="Scan Interval (minutes)" hint="How often the scheduler fetches Kalshi markets. Default: 15.">
              <NumInput value={form.scan_interval_minutes} onChange={v => set('scan_interval_minutes', parseInt(v) || 15)} min={5} max={120} />
            </Row>
            <Row label="Stale Alert Expiry (minutes)" hint="Auto-expire pending alerts after this long. Default: 60.">
              <NumInput value={form.stale_alert_expiry_minutes} onChange={v => set('stale_alert_expiry_minutes', parseInt(v) || 60)} min={15} max={1440} />
            </Row>
          </Accordion>
          <Accordion title="Paper Learning Gates">
            <Row label="Min Expected Value" hint="Minimum EV required for auto paper entries. Higher = pickier.">
              <NumInput value={form.paper_learning_min_ev ?? 0.08} onChange={v => set('paper_learning_min_ev', v)} min={0.01} max={0.50} step={0.01} />
            </Row>
            <Row label="Min Side Edge" hint="Minimum edge on the chosen side (model prob − market price).">
              <NumInput value={form.paper_learning_min_side_edge ?? 0.08} onChange={v => set('paper_learning_min_side_edge', v)} min={0.01} max={0.50} step={0.01} />
            </Row>
            <Row label="Min Confidence" hint="Minimum weather model confidence for auto entries.">
              <NumInput value={form.paper_learning_min_confidence ?? 0.55} onChange={v => set('paper_learning_min_confidence', v)} min={0.30} max={0.95} step={0.05} />
            </Row>
            <Row label="Max Entries / Scan" hint="Cap on how many auto paper entries per scan cycle.">
              <NumInput value={form.paper_learning_max_entries_per_scan ?? 8} onChange={v => set('paper_learning_max_entries_per_scan', parseInt(v) || 1)} min={1} max={50} />
            </Row>
            <Row label="Max Open / Event" hint="Max simultaneous paper positions on the same weather event.">
              <NumInput value={form.paper_learning_max_open_per_event ?? 1} onChange={v => set('paper_learning_max_open_per_event', parseInt(v) || 1)} min={1} max={5} />
            </Row>
          </Accordion>
          <Accordion title="Sizing Model">
            <Row label="Kelly Fraction" hint="How aggressively to follow the Kelly sizing formula. 0.25 is recommended.">
              <NumInput value={form.kelly_fraction} onChange={v => set('kelly_fraction', v)} min={0.05} max={1} step={0.05} />
            </Row>
            <Row label="Paper Starting Balance ($)" hint="Simulation bankroll used for paper P&L and Kelly sizing.">
              <NumInput value={form.paper_starting_balance} onChange={v => set('paper_starting_balance', v)} min={1} max={100000} />
            </Row>
          </Accordion>
          <Accordion title="Data Sources">
            <Row label="NWS User Agent" hint="NWS requires a contact-style user agent for api.weather.gov.">
              <input
                type="text"
                className="text-input"
                value={form.nws_user_agent || ''}
                onChange={e => set('nws_user_agent', e.target.value)}
              />
            </Row>
            <Row label="NOAA CDO Token" hint="Optional token for NOAA climate records and settlement reconciliation.">
              <input
                type="password"
                className="text-input"
                placeholder={form.noaa_token_configured ? '● Configured' : '(optional)'}
                value={form.noaa_token || ''}
                onChange={e => set('noaa_token', e.target.value)}
              />
            </Row>
          </Accordion>
          <Accordion title="Data Cleanup">
            <CleanupPanel />
          </Accordion>
          <Accordion title="Settlement Stations">
            <div style={{ fontSize: '0.78rem', color: 'var(--text-2)', lineHeight: 1.7, padding: '4px 0' }}>
              <p style={{ marginBottom: 8 }}>Kalshi settles weather markets on the <strong style={{ color: 'var(--text)' }}>NWS Climatological Report (Daily)</strong> from these stations:</p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '4px 16px', fontFamily: 'var(--font-mono)', fontSize: '0.72rem' }}>
                {[
                  ['NYC', 'KNYC', 'Central Park'], ['CHI', 'KMDW', 'Midway'], ['LAX', 'KLAX', 'LAX Airport'],
                  ['MIA', 'KMIA', 'Miami Intl'], ['DAL', 'KDFW', 'DFW'], ['ATL', 'KATL', 'Hartsfield'],
                  ['SEA', 'KSEA', 'SeaTac'], ['DEN', 'KDEN', 'Denver Intl'], ['BOS', 'KBOS', 'Logan'],
                  ['PHX', 'KPHX', 'Sky Harbor'], ['SFO', 'KSFO', 'SFO Airport'], ['HOU', 'KHOU', 'Hobby'],
                  ['PHIL', 'KPHL', 'PHL Airport'], ['MIN', 'KMSP', 'MSP'], ['AUS', 'KAUS', 'Bergstrom'],
                  ['LV', 'KLAS', 'Harry Reid'], ['DC', 'KDCA', 'Reagan Natl'], ['OKC', 'KOKC', 'Will Rogers'],
                  ['NOLA', 'KMSY', 'Armstrong'], ['SATX', 'KSAT', 'San Antonio Intl'],
                ].map(([city, station, name]) => (
                  <div key={city} style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--sky)' }}>{city}</span>
                    <span>{station} <span style={{ color: 'var(--text-muted)' }}>{name}</span></span>
                  </div>
                ))}
              </div>
            </div>
          </Accordion>
          <Accordion title="How Sibylla Works">
            <div style={{ fontSize: '0.78rem', color: 'var(--text-2)', lineHeight: 1.7, padding: '4px 0' }}>
              <p><strong style={{ color: 'var(--text)' }}>Kalshi Weather Markets</strong> — binary YES/NO markets on weather outcomes (daily high/low temp, rainfall). Each contract settles at $1 if it resolves YES, $0 if NO. The price = the crowd's probability estimate.</p>
              <p style={{ marginTop: 10 }}><strong style={{ color: 'var(--text)' }}>What Sibylla does</strong> — it computes its own probability by averaging NOAA/NWS and Open-Meteo forecasts, then compares that combined forecast to the market price.</p>
              <p style={{ marginTop: 10 }}><strong style={{ color: 'var(--text)' }}>Entry move</strong> — measures whether the market moved in the bot's favor after entry, not just win/loss. If paper YES opens at 35¢ and later marks at 48¢, the entry move is +13¢.</p>
              <p style={{ marginTop: 10 }}><strong style={{ color: 'var(--text)' }}>Bot Trust</strong> — 0-100 composite of sample depth, average entry quality, recent entry quality, good-entry rate, paper P&amp;L, and earned market types.</p>
              <p style={{ marginTop: 10 }}><strong style={{ color: 'var(--text)' }}>When to go live</strong> — only after score 90+, average entry quality positive, recent entry quality positive, good-entry rate at least 50%, paper P&amp;L positive, and an earned market type.</p>
            </div>
          </Accordion>
        </div>
      </div>
    </div>
  )
}
