import React, { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { cleanTitle, currentTemp, fmtEdge, fmtMoney, humanizeMarketParts, isActionable, marketQuestion, opportunityEdge, optionLabel, paperActionInfo, parseApiTime, qualityScore, recommendation, segmentLabel, stateLabel } from '../utils/format'

const fmtPct = (v, d = 1) => v == null ? '—' : `${(v * 100).toFixed(d)}%`

function ChartTip({ active, payload }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#0d1623', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 6, padding: '7px 11px', fontSize: '0.73rem', fontFamily: 'var(--font-mono)' }}>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name}: <strong style={{ color: '#dde2ef' }}>{p.name === 'P&L' ? fmtMoney(p.value) : `${p.value > 0 ? '+' : ''}${p.value}¢`}</strong>
        </div>
      ))}
    </div>
  )
}

function ScanCountdown({ nextScan }) {
  const [secs, setSecs] = useState(null)
  useEffect(() => {
    if (!nextScan) return
    const tick = () => setSecs(Math.max(0, Math.round((nextScan - Date.now()) / 1000)))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [nextScan])
  if (secs == null) return null
  const m = Math.floor(secs / 60), s = secs % 60
  return <span className="scan-countdown">next scan {m}:{String(s).padStart(2, '0')}</span>
}

function BrainBar({ brain, winRate, winCount, tradeCount }) {
  if (!brain) return null
  const score   = brain.score ?? 0
  const color   = score >= 80 ? 'var(--green)' : score >= 60 ? 'var(--amber)' : 'var(--red)'
  const label   = brain.readiness_label || stateLabel(brain.state)
  const predAcc = brain.prediction_accuracy ?? 0
  const predSamples = brain.prediction_sample_count ?? 0

  return (
    <div className="brain-bar">
      <div className="brain-bar-top">
        <span className="brain-bar-label">Bot Trust</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color, fontSize: '0.9rem' }}>{score}/100</span>
          <span className={`badge ${score >= 80 ? 'badge-green' : score >= 60 ? 'badge-amber' : 'badge-red'}`}>{label}</span>
        </div>
      </div>
      <div className="progress-bar" style={{ height: 5, margin: '6px 0 8px' }}>
        <div className="progress-fill" style={{ width: `${score}%`, background: color, transition: 'width 0.8s ease' }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
        {[
          { label: 'Accuracy', val: predSamples > 0 ? `${Math.round(predAcc * 100)}%` : '—', color: predAcc >= 0.55 ? 'var(--green)' : predAcc >= 0.40 ? 'var(--amber)' : predSamples > 0 ? 'var(--red)' : 'var(--text-2)', sub: predSamples > 0 ? `${brain.prediction_correct_count ?? 0}/${predSamples}` : null },
          { label: 'Win Rate', val: winRate == null ? '—' : `${winRate}%`, color: winRate >= 55 ? 'var(--green)' : winRate >= 45 ? 'var(--amber)' : winRate != null ? 'var(--red)' : 'var(--text-2)', sub: tradeCount ? `${winCount}/${tradeCount}` : null },
          { label: 'Settled', val: brain.learning_samples ?? 0, color: (brain.learning_samples ?? 0) >= 20 ? 'var(--green)' : 'var(--amber)' },
        ].map(m => (
          <div key={m.label} style={{ background: 'var(--bg-card2)', borderRadius: 6, padding: '6px 10px' }}>
            <div style={{ fontSize: '0.63rem', color: 'var(--text-2)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 2 }}>{m.label}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, fontSize: '0.85rem', color: m.color }}>{m.val}</div>
            {m.sub && <div style={{ fontSize: '0.58rem', color: 'var(--text-muted)', marginTop: 1 }}>{m.sub}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

function TopTrade({ alert, onPaperTrade }) {
  if (!alert) {
    return (
      <div className="dash-top-trade blocked">
        <div className="dash-top-trade-kicker">Next Paper Candidate</div>
        <div className="dash-top-trade-title">No signal is cleared for entry</div>
        <div className="dash-top-trade-copy">
          <p>The scanner is still ranking markets, but current signals are wait-only until a paper slot opens or the quote improves.</p>
        </div>
      </div>
    )
  }
  const edge  = opportunityEdge(alert)
  const rec   = recommendation(alert)
  const det = alert.details || {}
  const { city, rest } = humanizeMarketParts(alert.market_ticker, alert.market_title || det.market_title)
  const title = marketQuestion(alert)
  const edgeColor = edge >= 0.1 ? 'var(--green)' : edge >= 0.04 ? 'var(--amber)' : 'var(--red)'
  const ctx = det.analysis_context || {}
  const learned = ctx.segment_learning || det.brain?.learned || {}
  const forecast = det.forecast || {}
  const modelSide = alert.direction === 'no' ? 1 - (alert.model_prob || 0) : (alert.model_prob || 0)
  const forecastValue = rest?.includes('Low')
    ? forecast.low
    : rest?.includes('Rain') || rest?.includes('Precip')
      ? forecast.precip_pct
      : forecast.high
  const threshold = alert.floor_strike ?? alert.cap_strike ?? det.floor_strike ?? det.cap_strike
  const thresholdText = threshold == null ? 'listed threshold' : (rest?.includes('Rain') || rest?.includes('Precip') ? `${threshold} in.` : `${threshold}°F`)
  const history = learned.trade_count
    ? `${learned.trade_count} similar trades · ${((learned.positive_clv_rate || 0) * 100).toFixed(0)}% good entries`
    : `Segment history: ${segmentLabel(ctx.segment_key || learned.segment_key || det.brain?.segment)}`
  const action = paperActionInfo(alert)
  const narrative = [
    `${city || 'This market'} is ranked first because the model chance is higher than the live market price by ${edge == null ? 'a positive amount' : fmtEdge(edge)}.`,
    `${forecastValue == null ? 'Forecast snapshot is incomplete' : `Forecast ${rest?.includes('Rain') || rest?.includes('Precip') ? `${Number(forecastValue).toFixed(0)}% rain chance` : `${Number(forecastValue).toFixed(0)}°F`} vs. ${thresholdText}`}; model chance on ${(alert.direction || 'yes').toUpperCase()} is ${fmtPct(modelSide, 0)}.`
  ]
  const cleanedAnalysis = cleanTitle(det.analysis || alert.analysis)
    .replace(/\bbot chance\b/gi, 'model chance')
    .replace(/\bedge\b/gi, 'value')
  const direction = (alert.direction || 'yes').toUpperCase()

  return (
    <div className="dash-top-trade">
      <div className="dash-top-trade-kicker">
        <span className="signal-dot" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: 'var(--green)', boxShadow: '0 0 8px var(--green)', marginRight: 6 }} />
        Next Paper Candidate
      </div>
      <div className="dash-top-trade-title">{title}</div>
      <div className="dash-top-trade-sub">
        <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>Option {optionLabel(alert)}</span>
        <span style={{ color: alert.direction === 'yes' ? 'var(--green)' : 'var(--red)', fontWeight: 700, fontSize: '0.72rem' }}>
          {(alert.direction || 'YES').toUpperCase()}
        </span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{currentTemp(alert)}</span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{history}</span>
      </div>
      <div className="trade-reasoning">
        <div className="trade-reasoning-title">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><circle cx="8" cy="8" r="6"/><path d="M8 5v3l2 2"/></svg>
          Why this trade
        </div>
        {narrative.map((line, i) => (
          <div key={i} className="trade-reasoning-row">
            <span className="reason-icon reason-good">▸</span>
            <span>{line}</span>
          </div>
        ))}
        {cleanedAnalysis && (
          <div className="trade-reasoning-row" style={{ marginTop: 4, paddingTop: 4, borderTop: '1px solid var(--border)' }}>
            <span className="reason-icon reason-neutral">◆</span>
            <span style={{ color: 'var(--text)' }}>{cleanedAnalysis}</span>
          </div>
        )}
        {(det.phantom_risk_level === 'high' || det.phantom_risk_level === 'medium') && (
          <div className="trade-reasoning-row">
            <span className="reason-icon reason-bad">⚠</span>
            <span>Phantom risk {det.phantom_risk_level} — edge may be from stale data vs live pricing</span>
          </div>
        )}
      </div>
      <div className="dash-top-trade-action">
        <div>
          <span>Paper action</span>
          <strong>{action.enabled ? `${direction} · ${action.sub} at ${fmtPct(rec.limit_price_side, 0)}` : cleanTitle(action.sub || rec.reason || 'No entry right now')}</strong>
        </div>
        <button
          className={`btn btn-sm ${action.enabled ? `btn-side-${alert.direction || 'yes'}` : 'btn-ghost'}`}
          disabled={!action.enabled}
          onClick={() => onPaperTrade?.(alert.id, action.contracts, action.manual)}
        >
          {action.label}
        </button>
      </div>
      <div className="dash-top-trade-metrics">
        <div>
          <span>Value vs price</span>
          <strong style={{ color: edgeColor }}>{fmtEdge(edge)}</strong>
        </div>
        <div>
          <span>Price</span>
          <strong>{rec.limit_price_side == null ? '—' : fmtPct(rec.limit_price_side)}</strong>
        </div>
        <div>
          <span>Size</span>
          <strong style={{ color: action.enabled ? 'var(--green)' : 'var(--text-2)' }}>
            {action.enabled ? action.sub : 'Wait'}
          </strong>
        </div>
        <div>
          <span>Live trust</span>
          <strong style={{ color: (alert.brain_score || 0) >= 70 ? 'var(--green)' : 'var(--amber)' }}>{alert.brain_score ?? '—'}</strong>
        </div>
        <div>
          <span>Good entries</span>
          <strong>{rec.historical_positive_clv_rate == null ? '—' : `${(rec.historical_positive_clv_rate * 100).toFixed(0)}%`}</strong>
        </div>
        <div>
          <span>Confidence</span>
          <strong>{fmtPct(alert.confidence, 0)}</strong>
        </div>
      </div>
    </div>
  )
}


export default function Dashboard() {
  const navigate = useNavigate()
  const [overview, setOverview]   = useState(null)
  const [trades, setTrades]       = useState([])
  const [scanning, setScanning]   = useState(false)
  const [topAlert, setTopAlert]   = useState(null)
  const [brain, setBrain]         = useState(null)
  const [autoStatus, setAutoStatus] = useState(null)
  const [nextScan, setNextScan]   = useState(null)

  const loadOverview = useCallback(() =>
    fetch('/api/overview').then(r => r.json()).then(d => {
      setOverview(d)
      if (d.last_scan?.completed_at && d.last_scan?.status === 'complete') {
        const completedAt = parseApiTime(d.last_scan.completed_at)
        if (completedAt) setNextScan(completedAt.getTime() + 15 * 60 * 1000)
      }
    }).catch(console.error), [])

  const loadTrades = useCallback(() =>
    fetch('/api/trades?status=closed&limit=500').then(r => r.json())
      .then(d => setTrades(d.trades || [])).catch(console.error), [])

  const loadTopAlert = useCallback(() =>
    fetch('/api/alerts?status=active&limit=30').then(r => r.json())
      .then(d => {
        const ranked = (d.alerts || []).sort((a, b) => qualityScore(b) - qualityScore(a))
        const actionable = ranked.filter(a => a.status === 'pending' && isActionable(a))
        setTopAlert(actionable.length ? actionable[0] : ranked[0] || null)
      }).catch(() => {}), [])

  const loadBrain = useCallback(() =>
    fetch('/api/brain/status').then(r => r.json()).then(setBrain).catch(() => {}), [])
  const loadAutoStatus = useCallback(() =>
    fetch('/api/auto-trade/status').then(r => r.json()).then(setAutoStatus).catch(() => {}), [])

  useEffect(() => {
    loadOverview(); loadTrades(); loadTopAlert(); loadBrain(); loadAutoStatus()
    const ids = [
      setInterval(loadOverview, 30000),
      setInterval(loadTopAlert, 15000),
      setInterval(loadBrain, 30000),
      setInterval(loadAutoStatus, 30000),
    ]
    return () => ids.forEach(clearInterval)
  }, [loadOverview, loadTrades, loadTopAlert, loadBrain, loadAutoStatus])

  const triggerScan = () => {
    setScanning(true)
    fetch('/api/scan/weather', { method: 'POST' })
      .then(() => setTimeout(() => { loadOverview(); loadTopAlert(); setScanning(false) }, 5000))
      .catch(() => setScanning(false))
  }

  const paperTrade = (id, contracts = 1, learningOverride = false) => {
    fetch(`/api/alerts/${id}/paper-trade`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contracts, learning_override: learningOverride }),
    }).then(async r => {
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        window.alert(d.detail || 'Paper trade was rejected')
        return
      }
      loadOverview()
      loadTopAlert()
      loadTrades()
    }).catch(() => {})
  }

  const pnlData = trades
    .filter(t => t.pnl != null && t.exit_time)
    .sort((a, b) => new Date(a.exit_time) - new Date(b.exit_time))
    .reduce((acc, t) => {
      const prev = acc.length ? acc[acc.length - 1].cum : 0
      acc.push({ n: acc.length + 1, cum: +(prev + Number(t.pnl || 0)).toFixed(2) })
      return acc
    }, [])

  const pnl  = overview?.total_pnl_paper ?? null
  const unrealized = overview?.unrealized_pnl_paper ?? null
  const live = overview?.kalshi_balance || {}
  const clv  = overview?.avg_clv_cents ?? null
  const scan = overview?.last_scan || {}
  const scanTs = parseApiTime(scan.completed_at)
  const pnlCurrent = pnlData.length ? pnlData[pnlData.length - 1].cum : 0
  const pnlColor = pnlCurrent >= 0 ? '#00c805' : '#f04444'

  const closedWithPnl = trades.filter(t => t.pnl != null)
  const winners  = closedWithPnl.filter(t => t.pnl > 0).length
  const winRate  = closedWithPnl.length ? Math.round(winners / closedWithPnl.length * 100) : null
  const accountMoney = value => value == null ? '—' : `$${Number(value).toFixed(2)}`
  const autoBlocker = (autoStatus?.blockers || []).find(b => String(b).startsWith('Paper auto paused'))
  const paperAutoActive = brain?.auto_paper_trade_enabled || autoStatus?.paper_auto_enabled
  const autonomy = brain?.auto_trade_enabled
    ? 'Live auto ON'
    : autoBlocker
      ? 'Paper bot paused'
    : overview?.last_scan?.status === 'running'
      ? 'Scanning now'
    : paperAutoActive
      ? 'Paper bot running'
      : brain?.automation_enabled
        ? 'Auto scan on'
        : 'Manual'

  return (
    <div>
      {/* Top Bar */}
      <div className="topbar">
        <div className="topbar-left">
          <span className="topbar-date">
            {new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}
          </span>
          {scan.status === 'complete' && scanTs && (
            <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              Last scan {scanTs.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
              <span style={{ marginLeft: 6, color: 'var(--green)', fontWeight: 700 }}>· {scan.alerts_created ?? 0} new alerts</span>
            </span>
          )}
          <ScanCountdown nextScan={nextScan} />
        </div>
        <div className="topbar-right">
          {scanning && <div className="spinner" />}
          <button className="btn btn-primary btn-sm" onClick={triggerScan} disabled={scanning}>
            {scanning ? 'Scanning…' : 'Scan Now'}
          </button>
        </div>
      </div>

      <div className="page-content">
        {/* Page header */}
        <div className="page-hd" style={{ marginBottom: 14 }}>
          <div>
            <div className="page-title">Dashboard</div>
            <div className="page-sub">
              {paperAutoActive ? 'Auto-trading active' : autonomy} · {brain?.auto_trade_enabled ? 'live orders enabled' : 'paper mode'} · weather markets
            </div>
          </div>
        </div>

        {(brain?.learning_samples ?? 0) === 0 && (
          <div className="fresh-start-banner">
            <div className="fresh-icon">↻</div>
            <div>
              <strong>Fresh start — model recalibrated</strong>
              <p>Historical data archived. The bot will now scan markets with improved probability estimates and build new learning data from scratch. Auto-trading will begin collecting paper trades on the next scan.</p>
            </div>
          </div>
        )}


        <div className="dash-account-row">
          <div className="dash-account live">
            <div>
              <div className="dash-kpi-label">Kalshi Live Account</div>
              <div className="dash-account-val" style={{ color: live.connected ? 'var(--green)' : 'var(--amber)' }}>
                {live.connected && live.balance != null ? accountMoney(live.balance) : 'Not connected'}
              </div>
              <div className="dash-kpi-sub">{live.configured ? (live.connected ? `Portfolio ${accountMoney(live.portfolio_value ?? 0)}` : 'Auth failed') : 'Add API key'}</div>
            </div>
            <span className={`badge ${live.connected ? 'badge-green' : 'badge-amber'}`}>Live</span>
          </div>
          <div className="dash-account paper">
            <div>
              <div className="dash-kpi-label">Paper Equity</div>
              <div className="dash-account-val" style={{ color: (overview?.total_equity_paper ?? 0) >= overview?.paper_starting_balance ? 'var(--green)' : 'var(--red)' }}>
                {accountMoney(overview?.total_equity_paper)}
              </div>
              <div className="dash-kpi-sub">Start {accountMoney(overview?.paper_starting_balance)} · open mark {unrealized == null ? '—' : fmtMoney(unrealized)}</div>
            </div>
            <div className="dash-paper-pnl">
              <span>Paper P&amp;L</span>
              <strong style={{ color: pnl != null ? (pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-2)' }}>{pnl != null ? fmtMoney(pnl) : '—'}</strong>
            </div>
          </div>
        </div>

        <div className="dash-kpi-row compact">
          <div className="dash-kpi dash-kpi-link" onClick={() => navigate('/paper')}>
            <div className="dash-kpi-label">Open Positions</div>
            <div className="dash-kpi-val" style={{ color: (overview?.trades_open ?? 0) > 0 ? 'var(--sky)' : 'var(--text-2)' }}>
              {overview?.trades_open ?? '—'}
            </div>
            <div className="dash-kpi-sub">View paper trades →</div>
          </div>
          <div className="dash-kpi dash-kpi-link" onClick={() => navigate('/trades')}>
            <div className="dash-kpi-label">Closed Trades</div>
            <div className="dash-kpi-val">{overview?.trades_closed ?? '—'}</div>
            <div className="dash-kpi-sub">{winRate == null ? 'Win rate pending' : `${winRate}% win rate`} →</div>
          </div>
          <div className="dash-kpi dash-kpi-link" onClick={() => navigate('/scanner')}>
            <div className="dash-kpi-label">Latest Scan</div>
            <div className="dash-kpi-val" style={{ color: scan.status === 'complete' ? 'var(--green)' : scan.status === 'running' ? 'var(--amber)' : 'var(--text-2)' }}>
              {scan.markets_processed ?? scan.markets_found ?? '—'}
            </div>
            <div className="dash-kpi-sub">{scan.status || 'Never run'} · {scan.alerts_created ?? 0} new →</div>
          </div>
          <div className="dash-kpi dash-kpi-link" onClick={() => navigate('/brain')}>
            <div className="dash-kpi-label">Bot Trust</div>
            <div className="dash-kpi-val" style={{ color: (brain?.score ?? 0) >= 80 ? 'var(--green)' : (brain?.score ?? 0) >= 60 ? 'var(--amber)' : 'var(--red)' }}>
              {brain?.score ?? '—'}/100
            </div>
            <div className="dash-kpi-sub">View brain status →</div>
          </div>
        </div>

        {/* Brain bar + top trade */}
        <div className="dash-mid-row">
          <BrainBar brain={brain} winRate={winRate} winCount={winners} tradeCount={closedWithPnl.length} />
          <TopTrade alert={topAlert} onPaperTrade={paperTrade} />
        </div>

        <div className="dash-chart-row">
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="chart-title">Closed Paper P&amp;L</div>
            {pnlData.length < 2 ? (
              <div className="chart-empty">Not enough closed trades yet. Open positions are included in paper equity above.</div>
            ) : (
              <ResponsiveContainer width="100%" height={160}>
                <AreaChart data={pnlData} margin={{ top: 4, right: 6, left: -22, bottom: 0 }}>
                  <defs>
                    <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={pnlColor} stopOpacity={0.22} />
                      <stop offset="95%" stopColor={pnlColor} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
                  <XAxis dataKey="n" tick={{ fill: '#2c3550', fontSize: 9, fontFamily: 'var(--font-mono)' }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fill: '#2c3550', fontSize: 9, fontFamily: 'var(--font-mono)' }} tickLine={false} axisLine={false} />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.08)" strokeDasharray="4 2" />
                  <Tooltip content={<ChartTip />} />
                  <Area type="monotone" dataKey="cum" name="P&L" stroke={pnlColor} strokeWidth={1.5} fill="url(#pnlGrad)" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
