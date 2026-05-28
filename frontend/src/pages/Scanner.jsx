import React, { useEffect, useState, useCallback, useRef } from 'react'
import { currentTemp, fmtEdge, humanBlocker, humanizeMarketParts, paperActionInfo, parseApiTime, qualityScore, recommendation, stateLabel, trustLabel } from '../utils/format'

const STATE_COLOR = {
  paper_ready: 'var(--green)',
  watch:       'var(--amber)',
  caution:     'var(--sky)',
  skip:        'var(--red)',
}
const STATE_BADGE = {
  paper_ready: 'badge-green',
  watch:       'badge-amber',
  caution:     'badge-sky',
  skip:        'badge-red',
}
const TIER_BADGE = {
  tier_a:   'badge-green',
  tier_b:   'badge-sky',
  watch:    'badge-amber',
  learning: 'badge-purple',
  avoid:    'badge-red',
}

const tierLabel = tier => ({
  tier_a: 'Tier A',
  tier_b: 'Tier B',
  watch: 'Watch',
  learning: 'Learning',
  avoid: 'Avoid',
}[tier] || 'Check')

function MiniBar({ value, max, color }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div className="progress-bar" style={{ width: 50, height: 3 }}>
        <div className="progress-fill" style={{ width: `${pct}%`, background: color }}/>
      </div>
    </div>
  )
}

function opportunityEdge(a) {
  const rec = recommendation(a)
  if (rec.side_edge != null && !Number.isNaN(Number(rec.side_edge))) return Number(rec.side_edge)
  if (a.edge == null) return null
  return a.direction === 'no' ? -a.edge : a.edge
}

function sidePrice(a) {
  if (a.direction === 'no') {
    const noAsk = a.no_ask ?? a.details?.no_ask
    if (noAsk != null && !Number.isNaN(Number(noAsk))) return Number(noAsk)
  } else {
    const yesAsk = a.yes_ask ?? a.details?.yes_ask
    if (yesAsk != null && !Number.isNaN(Number(yesAsk))) return Number(yesAsk)
  }
  if (a.market_price == null) return null
  return a.direction === 'no' ? 1 - a.market_price : a.market_price
}

const fmtPct = (value, digits = 1) => value == null ? '—' : `${(value * 100).toFixed(digits)}%`
const fmtHours = value => {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  if (num < 1) return `${Math.max(1, Math.round(num * 60))}m`
  return `${num.toFixed(num < 10 ? 1 : 0)}h`
}

const sourceLabel = value => {
  const raw = Array.isArray(value) ? value.join('+') : String(value || '')
  if (!raw || raw === 'none') return 'No source'
  return raw
    .replace(/\bnws_free\b/gi, 'NOAA/NWS')
    .replace(/\baccuweather\b/gi, 'AccuWeather')
    .replace(/\bnoaa_cdo\b/gi, 'NOAA CDO')
    .replace(/\+/g, ' + ')
}

const urgencyLabel = value => ({
  high: 'High urgency',
  normal: 'Normal',
  low: 'Longer-dated',
}[value] || 'Timing unknown')

const PAGE_SIZE = 25

export default function Scanner() {
  const [scanStatus, setScanStatus] = useState(null)
  const [alerts, setAlerts]         = useState([])
  const [scanning, setScanning]     = useState(false)
  const [page, setPage]             = useState(1)
  const [papering, setPapering]     = useState({})
  const [settings, setSettings]     = useState(null)
  const [brain, setBrain]           = useState(null)
  const [autoStatus, setAutoStatus] = useState(null)
  const pollRef = useRef(null)

  const loadStatus = useCallback(() =>
    fetch('/api/scan/status').then(r => r.json()).then(setScanStatus).catch(console.error), [])

  const loadAlerts = useCallback(() =>
    fetch('/api/alerts?status=active&limit=60').then(r => r.json())
      .then(d => setAlerts(d.alerts || [])).catch(console.error), [])

  const loadMode = useCallback(() =>
    Promise.all([
      fetch('/api/settings').then(r => r.json()).catch(() => null),
      fetch('/api/brain/status').then(r => r.json()).catch(() => null),
      fetch('/api/auto-trade/status').then(r => r.json()).catch(() => null),
    ]).then(([s, b, a]) => {
      if (s) setSettings(s)
      if (b) setBrain(b)
      if (a) setAutoStatus(a)
    }), [])

  // Refresh: 30s idle, 5s while scan running
  useEffect(() => {
    loadStatus(); loadAlerts(); loadMode()
    let interval = 30000
    const id = setInterval(() => {
      loadStatus()
      loadAlerts()
      loadMode()
    }, interval)
    return () => clearInterval(id)
  }, [loadStatus, loadAlerts])

  // Fast poll only while scanning
  useEffect(() => {
    if (!scanning) return
    const id = setInterval(() => { loadStatus(); loadAlerts() }, 3000)
    return () => clearInterval(id)
  }, [scanning, loadStatus, loadAlerts])

  const triggerScan = () => {
    setScanning(true)
    fetch('/api/scan/weather', { method: 'POST' }).catch(err => {
      console.error(err)
      setScanStatus(s => ({ ...(s || {}), status: 'failed', stage: 'failed', error: err.message || 'Unable to start scan' }))
      setScanning(false)
    })

    let attempts = 0
    pollRef.current = setInterval(() => {
      attempts++
      fetch('/api/scan/status').then(r => r.json()).then(s => {
        setScanStatus(s)
        if (s.status === 'complete' || s.status === 'failed' || attempts >= 30) {
          clearInterval(pollRef.current)
          setScanning(false)
          loadAlerts()
        }
      }).catch(() => { clearInterval(pollRef.current); setScanning(false) })
    }, 3000)
  }

  useEffect(() => () => clearInterval(pollRef.current), [])

  const paperTrade = async (alertId, contracts, learningOverride = false) => {
    setPapering(p => ({ ...p, [alertId]: true }))
    try {
      const r = await fetch(`/api/alerts/${alertId}/paper-trade`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ contracts, learning_override: learningOverride }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        window.alert(d.detail || 'Paper trade was rejected')
      } else {
        await loadAlerts()
      }
    } finally {
      setPapering(p => ({ ...p, [alertId]: false }))
    }
  }

  const pauseAuto = async () => {
    await fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ automation_enabled: false, auto_paper_trade_enabled: false }),
    })
    loadMode()
  }

  const ranked = [...alerts]
    .sort((a, b) => qualityScore(b) - qualityScore(a))
    .map((a, i) => ({ ...a, rank: i + 1 }))

  const scan = scanStatus || {}

  const visibleRows = ranked.slice(0, page * PAGE_SIZE)
  const hasMore = ranked.length > page * PAGE_SIZE
  const modeLabel = settings?.auto_paper_trade_enabled
    ? 'Paper Bot'
    : settings?.automation_enabled
      ? 'Auto Scan'
      : 'Manual'
  const autoBlocker = (autoStatus?.blockers || []).find(b => String(b).startsWith('Paper auto paused'))
  const scanCurrent = scan.current_ticker
    ? (() => {
        const parts = humanizeMarketParts(scan.current_ticker, '')
        return [parts.city, parts.rest].filter(Boolean).join(' · ')
      })()
    : null

  return (
    <div className="scanner-page">
      <div className="page-hd">
        <div>
          <div className="page-title">Market Scanner</div>
          <div className="page-sub">Fresh weather markets ranked for paper entries from model chance vs market price</div>
        </div>
        <div className="page-hd-actions">
          {scanning && <div className="spinner" />}
          <button className="btn btn-primary" onClick={triggerScan} disabled={scanning}>
            {scanning ? 'Scanning…' : 'Run Scan'}
          </button>
        </div>
      </div>

      <div className="scan-hero">
        <div className="scan-hero-text">
          <h2>Weather Market Overview</h2>
          <p>
            {scan.status === 'running'
              ? 'Scan in progress - fetching Kalshi weather markets and scoring with NOAA/NWS plus Open-Meteo.'
              : scan.status === 'complete'
              ? `Last scan completed ${scan.completed_at ? parseApiTime(scan.completed_at)?.toLocaleString() || '' : ''}`
              : scan.status === 'failed'
              ? `Last scan failed${scan.error ? `: ${scan.error}` : ''}`
              : 'No scan data yet. Run a scan to fetch live Kalshi weather markets.'}
          </p>
        </div>
        <div className="scan-status-row">
          <div className="scan-stat">
            <div className={`scan-stat-val${scanning ? ' scan-running' : ''}`}>{scan.markets_found ?? '—'}</div>
            <div className="scan-stat-lbl">Markets Scanned</div>
          </div>
          <div className="scan-stat">
            <div className="scan-stat-val" style={{ color: 'var(--green)' }}>{scan.alerts_created ?? '—'}</div>
            <div className="scan-stat-lbl">New Candidates</div>
          </div>
          <div className="scan-stat">
            <div className="scan-stat-val" style={{ color: 'var(--blue)' }}>{ranked.length}</div>
            <div className="scan-stat-lbl">Paper Candidates</div>
          </div>
          {(scan.series_errors ?? 0) > 0 && (
            <div className="scan-stat">
              <div className="scan-stat-val" style={{ color: 'var(--amber)' }}>{scan.series_errors}</div>
              <div className="scan-stat-lbl">Series Fetch Errors</div>
            </div>
          )}
        </div>
        <div className="scan-progress-wrap">
          <div className="scan-progress-meta">
            <span>{stateLabel(scan.stage || scan.status)}</span>
            <span>{scan.progress ?? 0}%</span>
          </div>
          <div className="progress-bar scan-progress">
            <div className="progress-fill" style={{ width: `${scan.progress ?? 0}%`, background: scan.status === 'failed' ? 'var(--red)' : 'var(--green)' }}/>
          </div>
          {scanCurrent && <div className="scan-current">{scanCurrent}</div>}
          {scan.status === 'failed' && (
            <div className="scan-current" style={{ color: 'var(--red)' }}>
              {scan.error || 'Scanner failed before returning an error message.'}
            </div>
          )}
        </div>
      </div>

      <div className="scanner-mode-bar">
        <div>
          <strong>{modeLabel}</strong>
          <span>{autoBlocker || `Paper entries ${settings?.auto_paper_trade_enabled ? 'on' : 'off'} · real money off · trust ${brain?.score ?? '—'}/100 (${trustLabel(brain?.score)})`}</span>
        </div>
        <button
          className="btn btn-danger btn-sm"
          onClick={pauseAuto}
          disabled={!settings?.automation_enabled && !settings?.auto_paper_trade_enabled}
        >
          Pause Auto
        </button>
      </div>

      {ranked.length === 0 ? (
        <div className="card">
          <div className="empty">
            <svg width="44" height="44" viewBox="0 0 44 44" fill="none" stroke="currentColor" strokeWidth="1.3">
              <circle cx="22" cy="22" r="18"/>
              <circle cx="22" cy="22" r="7"/>
              <line x1="22" y1="2" x2="22" y2="7"/>
              <line x1="22" y1="37" x2="22" y2="42"/>
              <line x1="2" y1="22" x2="7" y2="22"/>
              <line x1="37" y1="22" x2="42" y2="22"/>
            </svg>
            No paper candidates found. Run a scan to populate results.
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th style={{ paddingLeft: 18 }}>#</th>
                  <th>Market</th>
                  <th>Direction</th>
                  <th>Tier</th>
                  <th>Value</th>
                  <th>Model Chance</th>
                  <th>Market Price</th>
                  <th>Temp</th>
                  <th>Size</th>
                  <th>EV</th>
                  <th>Trust</th>
                  <th>State</th>
                  <th>Why</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map(a => {
                  const edgeVal = opportunityEdge(a)
                  const edge    = fmtEdge(edgeVal)
                  const isNo    = a.direction === 'no'
                  const modPct  = fmtPct(isNo ? 1 - a.model_prob : a.model_prob)
                  const entry   = fmtPct(sidePrice(a))
                  const rec     = recommendation(a)
                  const action = paperActionInfo(a)
                  const tier    = rec.tier || (a.brain_state === 'paper_ready' ? 'tier_b' : a.brain_state === 'watch' ? 'watch' : edgeVal > 0 ? 'learning' : 'avoid')
                  const dirColor = a.direction === 'yes' ? 'var(--green)' : 'var(--red)'
                  const ec       = edgeVal >= 0.15 ? 'var(--green)' : edgeVal >= 0.06 ? 'var(--amber)' : 'var(--text-2)'
                  const { city, rest } = humanizeMarketParts(a.market_ticker, a.market_title || a.details?.market_title)
                  const isPapering = papering[a.id]
                  const alreadyPapered = a.status === 'paper_traded'
                  const reason = humanBlocker((rec.blockers || [])[0] || rec.reason || (rec.drivers || [])[0] || tierLabel(tier))
                  const details = a.details || {}
                  const station = a.settlement_station || details.settlement_station
                  const sources = a.forecast_sources || details.forecast_sources || details.forecast?.forecast_sources
                  const timePriority = a.time_priority || details.time_priority
                  const hoursToClose = a.hours_to_close ?? details.hours_to_close
                  const eventCount = (a.active_weather_events || details.active_weather_events || []).length || 0
                  const subline = [
                    station ? `Station ${station}` : 'Live weather market',
                    timePriority ? `${urgencyLabel(timePriority)} · ${fmtHours(hoursToClose)}` : null,
                    sources ? sourceLabel(sources) : null,
                    eventCount > 0 ? `${eventCount} weather event${eventCount === 1 ? '' : 's'}` : null,
                  ].filter(Boolean).join(' · ')
                  const ev100 = rec.expected_value_per_contract == null ? null : rec.expected_value_per_contract * 100
                  return (
                    <tr key={a.id} style={{ borderLeft: `3px solid ${STATE_COLOR[a.brain_state] || 'var(--border)'}` }}>
                      <td style={{ paddingLeft: 18, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: '0.75rem' }}>
                        {a.rank}
                      </td>
                      <td>
                        <div style={{ fontWeight: 700, color: 'var(--text)', fontSize: '0.83rem' }}>
                          {city && <span style={{ color: 'var(--sky)', fontWeight: 800 }}>{city}</span>}
                          {city && rest && <span style={{ color: 'var(--text-2)', margin: '0 3px' }}>·</span>}
                          {rest}
                        </div>
                        <div style={{ color: 'var(--text-muted)', fontSize: '0.65rem', marginTop: 2 }}>{subline}</div>
                      </td>
                      <td>
                        <span className="badge" style={{ background: dirColor + '1a', color: dirColor }}>
                          {(a.direction || '').toUpperCase()}
                        </span>
                      </td>
                      <td><span className={`badge ${TIER_BADGE[tier] || 'badge-muted'}`}>{tierLabel(tier)}</span></td>
                      <td style={{ color: ec, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>
                        {edge}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--blue)' }}>{modPct}</td>
                      <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-2)' }}>{entry}</td>
                      <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--sky)' }}>{currentTemp(a)}</td>
                      <td style={{ fontFamily: 'var(--font-mono)', color: action.enabled ? 'var(--green)' : 'var(--text-2)', fontWeight: 700 }}>
                        {action.enabled ? action.sub : 'Wait'}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)', color: ev100 == null ? 'var(--text-muted)' : ev100 >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                        {ev100 == null ? '—' : `${ev100 >= 0 ? '+' : ''}${ev100.toFixed(1)}¢`}
                      </td>
                      <td style={{ fontFamily: 'var(--font-mono)' }}>{a.brain_score ?? '—'}/100</td>
                      <td>
                        {a.brain_state && <span className={`badge ${STATE_BADGE[a.brain_state] || 'badge-muted'}`}>{stateLabel(a.brain_state)}</span>}
                      </td>
                      <td style={{ color: 'var(--text-2)', fontSize: '0.68rem', maxWidth: 210 }}>
                        {reason}
                      </td>
                      <td>
                        {alreadyPapered ? (
                          <span className="badge badge-amber">Tracking</span>
                        ) : action.enabled ? (
                          <button
                            className={`btn btn-sm btn-side-${a.direction || 'yes'}`}
                            disabled={isPapering}
                            onClick={() => paperTrade(a.id, action.contracts, action.manual)}
                            style={{ whiteSpace: 'nowrap', fontSize: '0.72rem' }}
                          >
                            {isPapering ? '...' : action.label}
                          </button>
                        ) : (
                          <button
                            className="btn btn-ghost btn-sm"
                            disabled
                            title={reason}
                            style={{ fontSize: '0.72rem', color: 'var(--text-2)' }}
                          >
                            Wait
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {hasMore && (
            <div style={{ padding: '12px 18px', borderTop: '1px solid var(--border)' }}>
              <button className="btn btn-ghost btn-sm" onClick={() => setPage(p => p + 1)}>
                Show more ({ranked.length - page * PAGE_SIZE} remaining)
              </button>
            </div>
          )}
        </div>
      )}

      <div style={{ marginTop: 14, fontSize: '0.72rem', color: 'var(--text-muted)', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <span>Resolution source: <strong style={{ color: 'var(--sky)' }}>Kalshi rules via NWS daily climate reports</strong></span>
        <span>Forecast model: <strong style={{ color: 'var(--sky)' }}>NOAA/NWS api.weather.gov</strong> · <strong style={{ color: 'var(--sky)' }}>Open-Meteo</strong></span>
        <span>Min visible value: <strong style={{ color: 'var(--sky)', fontFamily: 'var(--font-mono)' }}>+3¢</strong></span>
        <span style={{ marginLeft: 'auto' }}>Refreshes every 30s · top {ranked.length} candidates loaded</span>
      </div>
    </div>
  )
}
