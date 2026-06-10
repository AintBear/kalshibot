import React, { useEffect, useState, useCallback } from 'react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts'
import { fmtMoney, segmentLabel, stateLabel } from '../utils/format'
import { useNarration, usePulse } from '../utils/stream'

const NARRATION_COLORS = { audit: 'var(--amber)', scan: 'var(--sky)', alert: 'var(--text-muted)', trade: 'var(--green)' }

function LiveThoughts() {
  const lines = useNarration(60)
  const pulse = usePulse()
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, letterSpacing: '0.04em', fontSize: '0.8rem', textTransform: 'uppercase' }}>
          Live Thoughts
        </div>
        <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
          <span className={`status-dot ${pulse?.feed?.connected ? 'dot-online' : 'dot-offline'}`} style={{ display: 'inline-block', marginRight: 6 }} />
          {pulse?.feed?.connected
            ? `streaming ${pulse.feed.subscribed_tickers} markets · scan ${pulse.scan?.stage || 'idle'}`
            : 'feed reconnecting…'}
        </div>
      </div>
      <div style={{ maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
        {lines.length === 0 && (
          <div style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>
            Quiet right now — thoughts appear here the moment the bot scans, scores, blocks, or trades.
          </div>
        )}
        {lines.map((l, i) => (
          <div key={`${l.at}-${i}`} style={{ display: 'flex', gap: 8, fontSize: '0.78rem', lineHeight: 1.5 }}>
            <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
              {(l.at || '').slice(11, 19) || '—'}
            </span>
            <span style={{ color: NARRATION_COLORS[l.kind] || 'var(--text)', wordBreak: 'break-word' }}>{l.text}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ChartTip({ active, payload }) {
  if (!active || !payload?.length) return null
  return (
    <div className="chart-tip-dark">
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name}: <strong style={{ color: '#dde2ef' }}>{typeof p.value === 'number' ? (p.name.includes('P&L') ? fmtMoney(p.value) : `${p.value > 0 ? '+' : ''}${p.value.toFixed(1)}¢`) : p.value}</strong>
        </div>
      ))}
    </div>
  )
}

function AnimatedGauge({ score, size = 180 }) {
  const R = (size - 24) / 2
  const circ = 2 * Math.PI * R
  const arc = (score / 100) * circ * 0.75
  const totalArc = circ * 0.75
  const color = score >= 80 ? '#00c805' : score >= 60 ? '#f59e0b' : score >= 40 ? '#f59e0b' : '#f04444'
  const glowColor = score >= 80 ? 'rgba(0,200,5,0.4)' : score >= 60 ? 'rgba(245,158,11,0.3)' : 'rgba(240,68,68,0.3)'
  const center = size / 2

  return (
    <div className="brain-gauge-wrap">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <defs>
          <filter id="glow">
            <feGaussianBlur stdDeviation="4" result="coloredBlur"/>
            <feMerge>
              <feMergeNode in="coloredBlur"/>
              <feMergeNode in="SourceGraphic"/>
            </feMerge>
          </filter>
          <linearGradient id="gaugeGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={color} stopOpacity="1"/>
            <stop offset="100%" stopColor={color} stopOpacity="0.6"/>
          </linearGradient>
        </defs>
        <circle
          cx={center} cy={center} r={R}
          fill="none"
          stroke="rgba(255,255,255,0.04)"
          strokeWidth="10"
          strokeDasharray={`${totalArc} ${circ - totalArc}`}
          strokeLinecap="round"
          transform={`rotate(135 ${center} ${center})`}
        />
        <circle
          cx={center} cy={center} r={R}
          fill="none"
          stroke="url(#gaugeGrad)"
          strokeWidth="10"
          strokeDasharray={`${arc} ${circ - arc}`}
          strokeLinecap="round"
          transform={`rotate(135 ${center} ${center})`}
          filter="url(#glow)"
          style={{ transition: 'stroke-dasharray 1.2s cubic-bezier(0.4, 0, 0.2, 1)' }}
        />
        {[0, 25, 50, 75, 100].map(tick => {
          const angle = 135 + (tick / 100) * 270
          const rad = (angle * Math.PI) / 180
          const x1 = center + (R + 6) * Math.cos(rad)
          const y1 = center + (R + 6) * Math.sin(rad)
          const x2 = center + (R + 10) * Math.cos(rad)
          const y2 = center + (R + 10) * Math.sin(rad)
          return <line key={tick} x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgba(255,255,255,0.15)" strokeWidth="1.5"/>
        })}
        <text x={center} y={center - 8} textAnchor="middle" fontSize="38" fontWeight="800" fill={color} fontFamily="var(--font-mono)" style={{ filter: `drop-shadow(0 0 12px ${glowColor})` }}>
          {score}
        </text>
        <text x={center} y={center + 14} textAnchor="middle" fontSize="11" fill="rgba(255,255,255,0.35)" fontFamily="var(--font-mono)" fontWeight="600">
          / 100
        </text>
      </svg>
      <div className="gauge-state-label" style={{ color }}>{
        score >= 80 ? 'READY' : score >= 60 ? 'LEARNING' : score >= 40 ? 'TRAINING' : 'NOT READY'
      }</div>
    </div>
  )
}

function ScoreBreakdown({ status }) {
  if (!status) return null
  const s = status

  const predAcc = s.prediction_accuracy ?? 0
  const predSamples = s.prediction_sample_count ?? 0
  const predPct = Math.round(predAcc * 100)
  const predHasData = predSamples >= 10

  const components = [
    {
      label: 'Prediction Accuracy',
      value: predAcc,
      display: predSamples > 0 ? `${predPct}% (${s.prediction_correct_count ?? 0}/${predSamples})` : 'No data',
      maxPoints: 25,
      points: predHasData ? Math.max(0, Math.min(25, Math.round((predAcc - 0.30) * 83.33))) : 0,
      target: '60%+ for max points (need 10+ samples)',
      color: predAcc >= 0.55 ? 'var(--green)' : predAcc >= 0.40 ? 'var(--amber)' : 'var(--red)',
    },
    {
      label: 'Sample Depth',
      value: s.learning_samples ?? s.settled_trades ?? 0,
      display: `${s.learning_samples ?? s.settled_trades ?? 0} trades`,
      maxPoints: 10,
      points: Math.min(10, Math.round((s.learning_samples ?? 0) * 0.20)),
      target: '50 settled trades for max points',
      color: (s.learning_samples ?? 0) >= 20 ? 'var(--green)' : 'var(--amber)',
    },
    {
      label: 'Avg Entry Move',
      value: s.avg_clv ?? 0,
      display: `${(s.avg_clv ?? 0) >= 0 ? '+' : ''}${(s.avg_clv ?? 0).toFixed(1)}¢`,
      maxPoints: 15,
      points: Math.max(0, Math.min(15, Math.round(((s.avg_clv ?? 0) + 5.0) * 1.5))),
      target: '+5.0¢ avg for max points',
      color: (s.avg_clv ?? 0) >= 0 ? 'var(--green)' : 'var(--red)',
    },
    {
      label: 'Recent Trend (30)',
      value: s.recent_30_avg_clv ?? 0,
      display: `${(s.recent_30_avg_clv ?? 0) >= 0 ? '+' : ''}${(s.recent_30_avg_clv ?? 0).toFixed(1)}¢`,
      maxPoints: 15,
      points: Math.max(0, Math.min(15, Math.round(((s.recent_30_avg_clv ?? 0) + 5.0) * 1.5))),
      target: '+5.0¢ recent for max points',
      color: (s.recent_30_avg_clv ?? 0) >= 0 ? 'var(--green)' : 'var(--red)',
    },
    {
      label: 'Good Entry Rate',
      value: s.positive_clv_rate ?? 0,
      display: `${Math.round((s.positive_clv_rate ?? 0) * 100)}%`,
      maxPoints: 15,
      points: Math.max(0, Math.min(15, Math.round(((s.positive_clv_rate ?? 0) - 0.20) * 37.5))),
      target: '60%+ for max points',
      color: (s.positive_clv_rate ?? 0) >= 0.50 ? 'var(--green)' : (s.positive_clv_rate ?? 0) >= 0.35 ? 'var(--amber)' : 'var(--red)',
    },
    {
      label: 'Paper P&L',
      value: s.realized_pnl_paper ?? 0,
      display: fmtMoney(s.realized_pnl_paper),
      maxPoints: 10,
      points: (s.realized_pnl_paper ?? 0) > 0 && (s.recent_30_pnl_paper ?? 0) >= 0 ? 10 : (s.realized_pnl_paper ?? 0) >= 0 ? 5 : 0,
      target: 'Positive P&L + positive recent',
      color: (s.realized_pnl_paper ?? 0) >= 0 ? 'var(--green)' : 'var(--red)',
    },
    {
      label: 'Auto-Eligible Segments',
      value: s.auto_eligible_segments ?? 0,
      display: `${s.auto_eligible_segments ?? 0} segments`,
      maxPoints: 10,
      points: Math.min(10, Math.round((s.auto_eligible_segments ?? 0) * 3.5)),
      target: '3+ segments for max points',
      color: (s.auto_eligible_segments ?? 0) >= 1 ? 'var(--green)' : 'var(--red)',
    },
  ]

  return (
    <div className="brain-breakdown">
      <div className="brain-breakdown-title">Score Components</div>
      {components.map(c => (
        <div key={c.label} className="brain-component">
          <div className="brain-component-header">
            <span className="brain-component-label">{c.label}</span>
            <span className="brain-component-value" style={{ color: c.color }}>{c.display}</span>
          </div>
          <div className="brain-component-bar">
            <div className="brain-component-fill" style={{
              width: `${(c.points / c.maxPoints) * 100}%`,
              background: c.color,
            }}/>
          </div>
          <div className="brain-component-meta">
            <span>{c.points}/{c.maxPoints} pts</span>
            <span>{c.target}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

function LiveGates({ status }) {
  if (!status) return null
  const s = status
  const gates = [
    {
      label: 'Prediction Accuracy',
      current: (s.prediction_sample_count ?? 0) >= 10
        ? `${Math.round((s.prediction_accuracy ?? 0) * 100)}%`
        : `${s.prediction_sample_count ?? 0} samples`,
      required: '55%+ on 10+',
      pass: (s.prediction_sample_count ?? 0) >= 10 && (s.prediction_accuracy ?? 0) >= 0.55,
    },
    {
      label: 'Settled Samples',
      current: `${s.learning_samples ?? 0}`,
      required: '20+',
      pass: (s.learning_samples ?? 0) >= 20,
    },
    {
      label: 'Avg Entry Move',
      current: `${(s.avg_clv ?? 0) >= 0 ? '+' : ''}${(s.avg_clv ?? 0).toFixed(1)}¢`,
      required: '0.0¢+',
      pass: (s.avg_clv ?? 0) >= 0,
    },
    {
      label: 'Recent Entry Move',
      current: `${(s.recent_30_avg_clv ?? 0) >= 0 ? '+' : ''}${(s.recent_30_avg_clv ?? 0).toFixed(1)}¢`,
      required: '0.0¢+',
      pass: (s.recent_30_avg_clv ?? 0) >= 0,
    },
    {
      label: 'Good Entry Rate',
      current: `${Math.round((s.positive_clv_rate ?? 0) * 100)}%`,
      required: '50%+',
      pass: (s.positive_clv_rate ?? 0) >= 0.50,
    },
    {
      label: 'Paper P&L',
      current: fmtMoney(s.realized_pnl_paper),
      required: 'Positive',
      pass: (s.realized_pnl_paper ?? 0) >= 0,
    },
    {
      label: 'Auto-Eligible Segment',
      current: `${s.auto_eligible_segments ?? 0}`,
      required: '1+',
      pass: (s.auto_eligible_segments ?? 0) >= 1,
    },
  ]

  const passCount = gates.filter(g => g.pass).length
  const totalGates = gates.length

  return (
    <div className="brain-gates">
      <div className="brain-gates-header">
        <span className="brain-gates-title">Live-Readiness Gates</span>
        <span className={`badge ${passCount === totalGates ? 'badge-green' : 'badge-amber'}`}>
          {passCount}/{totalGates} passing
        </span>
      </div>
      <div className="brain-gates-grid">
        {gates.map(g => (
          <div key={g.label} className={`brain-gate ${g.pass ? 'gate-pass' : 'gate-fail'}`}>
            <div className="gate-icon">{g.pass ? '✓' : '✗'}</div>
            <div className="gate-info">
              <span className="gate-label">{g.label}</span>
              <span className="gate-values">
                <strong style={{ color: g.pass ? 'var(--green)' : 'var(--red)' }}>{g.current}</strong>
                <span className="gate-divider">/</span>
                <span className="gate-required">{g.required}</span>
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function DeficitTracker({ deficit }) {
  if (!deficit || deficit.current_deficit >= 0) return null
  const d = deficit
  return (
    <div className="brain-deficit">
      <div className="brain-deficit-icon">↗</div>
      <div className="brain-deficit-info">
        <span className="brain-deficit-title">Recovery Tracker</span>
        <div className="brain-deficit-stats">
          <span>Deficit: <strong style={{ color: 'var(--red)' }}>{fmtMoney(d.current_deficit)}</strong></span>
          <span>Avg profit/trade: <strong style={{ color: d.avg_profit_per_recent_trade > 0 ? 'var(--green)' : 'var(--text-2)' }}>{fmtMoney(d.avg_profit_per_recent_trade)}</strong></span>
          {d.estimated_trades_to_breakeven != null && (
            <span>Est. trades to breakeven: <strong style={{ color: 'var(--amber)' }}>{d.estimated_trades_to_breakeven}</strong></span>
          )}
        </div>
      </div>
    </div>
  )
}

function NextActions({ actions }) {
  if (!actions?.length) return null
  return (
    <div className="brain-actions">
      <div className="brain-actions-title">What Needs to Happen</div>
      <div className="brain-actions-list">
        {actions.map((action, i) => (
          <div key={i} className="brain-action-item">
            <div className="brain-action-num">{i + 1}</div>
            <p>{action}</p>
          </div>
        ))}
      </div>
    </div>
  )
}

function SegmentsTable({ segments }) {
  if (!segments?.length) return (
    <div className="brain-segments">
      <div className="brain-segments-title">Market Segments</div>
      <div className="empty">No segments yet — run scans and close trades to build history</div>
    </div>
  )

  return (
    <div className="brain-segments">
      <div className="brain-segments-header">
        <span className="brain-segments-title">Market Segments</span>
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{segments.length} tracked</span>
      </div>
      <div className="brain-segments-grid">
        {segments.map(seg => {
          const clvCents = (seg.avg_clv || 0) * 100
          const pnlCents = (seg.avg_pnl || 0) * 100
          const posRate = Math.round((seg.positive_clv_rate || 0) * 100)
          const recentClv = ((seg.recent_avg_clv || 0) * 100).toFixed(1)
          return (
            <div key={seg.segment_key} className={`brain-segment ${seg.auto_eligible ? 'segment-eligible' : ''}`}>
              <div className="segment-header">
                <span className="segment-name">{segmentLabel(seg.segment_key)}</span>
                {seg.auto_eligible
                  ? <span className="badge badge-green">Auto-Ready</span>
                  : <span className="badge badge-muted">Learning</span>
                }
              </div>
              <div className="segment-stats">
                <div>
                  <span>Trades</span>
                  <strong>{seg.trade_count}</strong>
                </div>
                <div>
                  <span>Avg Move</span>
                  <strong style={{ color: clvCents >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {clvCents >= 0 ? '+' : ''}{clvCents.toFixed(1)}¢
                  </strong>
                </div>
                <div>
                  <span>Recent</span>
                  <strong style={{ color: Number(recentClv) >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {Number(recentClv) >= 0 ? '+' : ''}{recentClv}¢
                  </strong>
                </div>
                <div>
                  <span>Good Rate</span>
                  <strong style={{ color: posRate >= 50 ? 'var(--green)' : posRate >= 35 ? 'var(--amber)' : 'var(--red)' }}>
                    {posRate}%
                  </strong>
                </div>
                <div>
                  <span>Avg P&L</span>
                  <strong style={{ color: pnlCents >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {pnlCents >= 0 ? '+' : ''}{pnlCents.toFixed(1)}¢
                  </strong>
                </div>
              </div>
              {(seg.details?.lessons || []).length > 0 && (
                <div className="segment-lessons">
                  {seg.details.lessons.slice(0, 2).map((lesson, i) => (
                    <div key={i} className="segment-lesson">{lesson}</div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function Brain() {
  const [status, setStatus] = useState(null)
  const [trades, setTrades] = useState([])
  const [rebuilding, setRebuilding] = useState(false)

  const load = useCallback(() =>
    Promise.all([
      fetch('/api/brain/status').then(r => r.json()),
      fetch('/api/trades?status=closed&limit=100').then(r => r.json()),
    ]).then(([brain, tradeData]) => {
      setStatus(brain)
      setTrades(tradeData.trades || [])
    }).catch(console.error), [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  const rebuild = () => {
    setRebuilding(true)
    fetch('/api/brain/rebuild', { method: 'POST' }).then(load).finally(() => setRebuilding(false))
  }

  const clvData = trades
    .filter(t => t.clv != null)
    .sort((a, b) => new Date(a.exit_time || 0) - new Date(b.exit_time || 0))
    .slice(-50)
    .map((t, i) => ({ n: i + 1, clv: +(t.clv * 100).toFixed(1) }))

  const pnlData = trades
    .filter(t => t.pnl != null && t.exit_time)
    .sort((a, b) => new Date(a.exit_time) - new Date(b.exit_time))
    .reduce((acc, t) => {
      const prev = acc.length ? acc[acc.length - 1].cum : 0
      acc.push({ n: acc.length + 1, cum: +(prev + Number(t.pnl || 0)).toFixed(2) })
      return acc
    }, [])

  const pnlColor = pnlData.length && pnlData[pnlData.length - 1].cum >= 0 ? '#00c805' : '#f04444'

  if (!status) return (
    <div className="brain-page">
      <div className="page-hd">
        <div className="page-title">Brain Status</div>
      </div>
      <div className="brain-loading">
        <div className="spinner" />
        <span>Loading brain status...</span>
      </div>
    </div>
  )

  const s = status
  const score = s.score ?? 0

  return (
    <div className="brain-page">
      <div className="page-hd">
        <div>
          <div className="page-title">Brain Status</div>
          <div className="page-sub">
            Trust score, learning progress, and live-readiness gates
          </div>
        </div>
        <div className="page-hd-actions">
          {rebuilding && <div className="spinner" />}
          <button className="btn btn-ghost" onClick={rebuild} disabled={rebuilding}>
            {rebuilding ? 'Rebuilding...' : 'Rebuild Snapshots'}
          </button>
        </div>
      </div>

      <LiveThoughts />

      {/* Hero section: Gauge + Status + Quick stats */}
      <div className="brain-hero">
        <div className="brain-hero-gauge">
          <AnimatedGauge score={score} size={190} />
        </div>
        <div className="brain-hero-info">
          <div className="brain-hero-label">{s.readiness_label || stateLabel(s.state)}</div>
          <div className="brain-hero-stats">
            <div className="brain-stat" style={{ gridColumn: 'span 2', background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '8px 12px' }}>
              <span style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Prediction Accuracy</span>
              <strong style={{
                fontSize: '1.4rem',
                color: (s.prediction_accuracy ?? 0) >= 0.55 ? 'var(--green)' : (s.prediction_accuracy ?? 0) >= 0.40 ? 'var(--amber)' : 'var(--red)',
              }}>
                {(s.prediction_sample_count ?? 0) > 0
                  ? `${Math.round((s.prediction_accuracy ?? 0) * 100)}%`
                  : '—'}
              </strong>
              <span style={{ fontSize: '0.6rem', color: 'var(--text-muted)' }}>
                {(s.prediction_sample_count ?? 0) > 0
                  ? `${s.prediction_correct_count ?? 0} correct / ${s.prediction_sample_count} settled`
                  : 'Waiting for settlement data'}
              </span>
            </div>
            <div className="brain-stat">
              <span>Total Trades</span>
              <strong>{s.total_trades ?? 0}</strong>
            </div>
            <div className="brain-stat">
              <span>Open</span>
              <strong style={{ color: 'var(--sky)' }}>{s.open_trades ?? 0}</strong>
            </div>
            <div className="brain-stat">
              <span>Settled</span>
              <strong style={{ color: (s.learning_samples ?? 0) >= 20 ? 'var(--green)' : 'var(--amber)' }}>{s.learning_samples ?? 0}</strong>
            </div>
            <div className="brain-stat">
              <span>Pending</span>
              <strong style={{ color: 'var(--text-2)' }}>{s.pending_settlement_trades ?? 0}</strong>
            </div>
            <div className="brain-stat">
              <span>Paper P&L</span>
              <strong style={{ color: (s.realized_pnl_paper ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtMoney(s.realized_pnl_paper)}</strong>
            </div>
            <div className="brain-stat">
              <span>Recent P&L (30)</span>
              <strong style={{ color: (s.recent_30_pnl_paper ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtMoney(s.recent_30_pnl_paper)}</strong>
            </div>
          </div>
          <div className="brain-hero-modes">
            <span className={`badge ${s.paper_trading ? 'badge-green' : 'badge-muted'}`}>Paper {s.paper_trading ? 'ON' : 'OFF'}</span>
            <span className={`badge ${s.automation_enabled ? 'badge-sky' : 'badge-muted'}`}>Auto Scan {s.automation_enabled ? 'ON' : 'OFF'}</span>
            <span className={`badge ${s.auto_paper_trade_enabled ? 'badge-green' : 'badge-muted'}`}>Paper Bot {s.auto_paper_trade_enabled ? 'ON' : 'OFF'}</span>
            <span className={`badge ${s.auto_trade_enabled ? 'badge-red' : 'badge-muted'}`}>Live Auto {s.auto_trade_enabled ? 'ON' : 'OFF'}</span>
          </div>
        </div>
      </div>

      <DeficitTracker deficit={s.deficit_recovery} />

      {/* Score breakdown + Live gates side by side */}
      <div className="brain-detail-grid">
        <ScoreBreakdown status={s} />
        <LiveGates status={s} />
      </div>

      <NextActions actions={s.next_actions} />

      {/* Charts */}
      <div className="brain-charts">
        <div className="card">
          <div className="chart-title">Entry Move Per Trade (last 50, cents)</div>
          {clvData.length < 2 ? (
            <div className="chart-empty">Close more trades to see entry-move history</div>
          ) : (
            <ResponsiveContainer width="100%" height={190}>
              <BarChart data={clvData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false}/>
                <XAxis dataKey="n" tick={{ fill: '#3b5472', fontSize: 10 }} tickLine={false} axisLine={false}/>
                <YAxis tick={{ fill: '#3b5472', fontSize: 10 }} tickLine={false} axisLine={false}/>
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.08)"/>
                <Tooltip content={<ChartTip/>}/>
                <Bar dataKey="clv" name="Entry move" radius={[3,3,0,0]}>
                  {clvData.map((e, i) => <Cell key={i} fill={e.clv >= 0 ? '#00c805' : '#f04444'} fillOpacity={0.8}/>)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="card">
          <div className="chart-title">Cumulative Paper P&L</div>
          {pnlData.length < 2 ? (
            <div className="chart-empty">Not enough closed trades yet</div>
          ) : (
            <ResponsiveContainer width="100%" height={190}>
              <AreaChart data={pnlData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <defs>
                  <linearGradient id="brainPnlGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={pnlColor} stopOpacity={0.22}/>
                    <stop offset="95%" stopColor={pnlColor} stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false}/>
                <XAxis dataKey="n" tick={{ fill: '#3b5472', fontSize: 10 }} tickLine={false} axisLine={false}/>
                <YAxis tick={{ fill: '#3b5472', fontSize: 10 }} tickLine={false} axisLine={false}/>
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.08)" strokeDasharray="4 2"/>
                <Tooltip content={<ChartTip/>}/>
                <Area type="monotone" dataKey="cum" name="P&L" stroke={pnlColor} strokeWidth={2} fill="url(#brainPnlGrad)" dot={false}/>
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <SegmentsTable segments={s.segments} />
    </div>
  )
}
