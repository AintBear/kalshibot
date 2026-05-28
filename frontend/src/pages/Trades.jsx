import React, { useEffect, useState, useCallback } from 'react'
import { fmtCents, humanizeMarketParts, stateLabel, tradeEntryPrice } from '../utils/format'

const fmtPct   = v => v == null ? '—' : `${(v * 100).toFixed(1)}%`
const fmtMoney = v => v == null ? '—' : `${v >= 0 ? '+$' : '-$'}${Math.abs(v).toFixed(2)}`

const OUTCOME_COLOR = {
  win_clv:   'var(--green)',
  flat_clv:  'var(--text-2)',
  loss_clv:  'var(--amber)',
  bad_clv:   'var(--red)',
  stopped:   'var(--amber)',
  open:      'var(--sky)',
  neutral:   'var(--text-2)',
}

const EXIT_LABEL = {
  stop_loss:     'Stop-Loss Exit',
  take_profit:   'Take-Profit Exit',
  market_closed: 'Market Closed',
  settled:       'Settled',
  manual:        'Manual Close',
  expired:       'Expired',
  auto_paper:    'Paper Bot',
  auto_live:     'Auto Live',
}

function outcomeLabel(outcome) {
  const map = {
    win_clv:  'Good entry',
    flat_clv: 'Flat Entry',
    loss_clv: 'Mild Loss',
    bad_clv:  'Bad Entry',
    stopped:  'Stop-Loss',
    open:     'Open',
    neutral:  'Neutral',
  }
  return map[outcome] || outcome
}

function toneClass(trade) {
  const lesson = trade.lesson || {}
  if (lesson.outcome === 'open') return 'tone-neutral'
  if (lesson.outcome === 'win_clv') return 'tone-good'
  if (lesson.outcome === 'bad_clv' || lesson.outcome === 'stopped') return 'tone-bad'
  if (lesson.outcome === 'loss_clv') return 'tone-warn'
  // Fallback: color by pnl
  if (trade.pnl != null) return trade.pnl >= 0 ? 'tone-good' : 'tone-bad'
  return 'tone-neutral'
}

function TradeCard({ trade: t }) {
  const [lessonOpen, setLessonOpen] = useState(false)
  const lesson   = t.lesson || {}
  const entry    = tradeEntryPrice(t)
  const isOpen   = t.status === 'open'
  const hasPnl   = t.pnl != null
  const hasClv   = t.clv != null
  const clvColor = hasClv ? (t.clv >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-2)'
  const pnlColor = hasPnl ? (t.pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-2)'
  const exitLabel = EXIT_LABEL[t.exit_reason] || (t.exit_reason ? stateLabel(t.exit_reason) : '—')
  const outcomeColor = OUTCOME_COLOR[lesson.outcome] || 'var(--text-2)'
  const { city, rest } = humanizeMarketParts(t.market_ticker, t.market_title)
  const title = [city, rest].filter(Boolean).join(' · ') || 'Weather market'

  const entryDate = t.entry_time
    ? new Date(String(t.entry_time).includes('T') ? t.entry_time : `${t.entry_time.replace(' ', 'T')}Z`)
        .toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : '—'
  const exitDate = t.exit_time
    ? new Date(String(t.exit_time).includes('T') ? t.exit_time : `${t.exit_time.replace(' ', 'T')}Z`)
        .toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    : null

  return (
    <div className={`trade-row ${toneClass(t)}`}>
      <div className="trade-main">
        <div className="trade-title-block">
          <div className="trade-title">{title}</div>
          <div className="trade-sub">
            <span style={{ color: t.direction === 'yes' ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
              {(t.direction || 'YES').toUpperCase()}
            </span>
            <span>Entered {entryDate}</span>
            {exitDate && <span>Closed {exitDate}</span>}
            {t.contracts > 1 && <span>{t.contracts} contracts</span>}
          </div>
        </div>

        {/* P&L — the main metric */}
        <div className="trade-actions" style={{ alignItems: 'flex-end', minWidth: 90 }}>
          {isOpen ? (
            <span className="badge badge-sky" style={{ fontSize: '0.72rem' }}>Open</span>
          ) : hasPnl ? (
            <>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '1.22rem', fontWeight: 800, color: pnlColor, lineHeight: 1 }}>
                {fmtMoney(t.pnl)}
              </span>
              <span style={{ color: 'var(--text-muted)', fontSize: '0.56rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', marginTop: 3 }}>
                P&amp;L
              </span>
            </>
          ) : (
            <span style={{ color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: '0.88rem' }}>—</span>
          )}
        </div>
      </div>

      {/* Metrics Row */}
      <div className="trade-metrics" style={{ marginTop: 9 }}>
        <div className="metric-pill">
          <span>Entry</span>
          <strong>{fmtPct(entry)}</strong>
        </div>
        <div className="metric-pill">
          <span>Exit</span>
          <strong>{fmtPct(t.exit_price)}</strong>
        </div>
        <div className="metric-pill">
          <span>Contracts</span>
          <strong>{t.contracts ?? 1}</strong>
        </div>
        <div className="metric-pill">
          <span>{isOpen ? 'Status' : 'Exit Reason'}</span>
          <strong style={{ color: t.exit_reason === 'stop_loss' ? 'var(--amber)' : 'var(--text-2)', fontSize: '0.7rem' }}>
            {isOpen ? 'Open' : exitLabel}
          </strong>
        </div>
      </div>
    </div>
  )
}

export default function Trades() {
  const [trades, setTrades] = useState([])
  const [aggregate, setAggregate] = useState(null)
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    fetch(`/api/trades?${filter ? `status=${filter}&` : ''}limit=200`)
      .then(r => r.json())
      .then(d => { setTrades(d.trades || []); setAggregate(d.aggregate || null) })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [filter])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  const closed  = trades.filter(t => t.status !== 'open')
  const open    = trades.filter(t => t.status === 'open')
  const winners = closed.filter(t => t.pnl != null && t.pnl > 0).length
  const losers  = closed.filter(t => t.pnl != null && t.pnl < 0).length
  const totalPnl = aggregate?.total_pnl ?? closed.reduce((s, t) => s + (t.pnl || 0), 0)
  const posClv   = closed.filter(t => t.clv != null && t.clv > 0).length
  const withClv  = closed.filter(t => t.clv != null).length
  const avgClv   = aggregate?.avg_clv_cents != null
    ? aggregate.avg_clv_cents / 100
    : withClv > 0
      ? closed.filter(t => t.clv != null).reduce((s, t) => s + t.clv, 0) / withClv
      : null

  return (
    <div className="trades-page">
      <div className="page-hd">
        <div>
          <div className="page-title">Trade Log</div>
          <div className="page-sub">
            {open.length} open · {aggregate?.total_trades ?? closed.length} settled · each card shows what the bot learned
          </div>
        </div>
        <div className="tabs">
          {[['', 'All'], ['open', 'Open'], ['closed', 'Closed']].map(([s, lbl]) => (
            <button key={s} className={`tab${filter === s ? ' active' : ''}`} onClick={() => setFilter(s)}>
              {lbl}
            </button>
          ))}
        </div>
      </div>

      {/* Summary strip */}
      {(closed.length > 0 || aggregate) && (
        <div className="trades-summary">
          <div>
            <span>Total P&amp;L</span>
            <strong style={{ color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtMoney(totalPnl)}</strong>
          </div>
          <div>
            <span>Avg Entry Move</span>
            <strong style={{ color: avgClv != null ? (avgClv >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text-2)' }}>
              {avgClv != null ? `${avgClv >= 0 ? '+' : ''}${(avgClv * 100).toFixed(1)}¢` : '—'}
            </strong>
          </div>
          <div>
            <span>Prediction Accuracy</span>
            <strong style={{ color: (aggregate?.prediction_accuracy ?? 0) >= 55 ? 'var(--green)' : 'var(--amber)' }}>
              {aggregate?.prediction_accuracy != null ? `${aggregate.prediction_accuracy}%` : '—'}
            </strong>
          </div>
          <div>
            <span>Settled Trades</span>
            <strong>{aggregate?.total_trades ?? closed.length}</strong>
          </div>
        </div>
      )}

      {loading && trades.length === 0 ? (
        <div className="empty">Loading trades…</div>
      ) : trades.length === 0 ? (
        <div className="empty">No trades found.</div>
      ) : (
        <div className="alert-list">
          {trades.map(t => <TradeCard key={t.id} trade={t} />)}
        </div>
      )}
    </div>
  )
}
