import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import {
  ageMinutes,
  closeHours,
  eventKey,
  fmtEdge,
  fmtDollar,
  fmtMoney,
  fmtPct,
  humanBlocker,
  currentTemp,
  humanizeMarketParts,
  kalshiUrl,
  marketQuestion,
  opportunityEdge,
  optionLabel,
  paperActionInfo,
  qualityScore,
  recommendation,
  sidePrice,
  stateLabel,
  tradeEntryPrice,
} from '../utils/format'

function ChartTip({ active, payload }) {
  if (!active || !payload?.length) return null
  const point = payload[0]?.payload || {}
  return (
    <div className="chart-tip">
      <div>{point.label || `Trade ${point.n}`}</div>
      <strong>{point.equity == null ? '—' : `$${Number(point.equity).toFixed(2)}`}</strong>
    </div>
  )
}

function PaperEquityChart({ overview, closedTrades }) {
  const start = Number(overview?.paper_starting_balance ?? 500)
  const sorted = [...closedTrades]
    .filter(t => t.pnl != null && t.exit_time)
    .sort((a, b) => new Date(a.exit_time) - new Date(b.exit_time))
  const data = [{ n: 0, label: 'Start', equity: start }]
  let equity = start
  for (const trade of sorted) {
    equity = +(equity + Number(trade.pnl || 0)).toFixed(2)
    data.push({ n: data.length, label: trade.market_ticker, equity })
  }
  if (overview?.total_equity_paper != null) {
    data.push({ n: data.length, label: 'Live mark', equity: Number(overview.total_equity_paper) })
  }
  const last = data[data.length - 1]?.equity ?? start
  const color = last >= start ? '#00c805' : '#f04444'

  return (
    <div className="paper-equity-panel">
      <div className="paper-equity-top">
        <div>
          <span>Paper Equity</span>
          <strong style={{ color }}>{`$${last.toFixed(2)}`}</strong>
        </div>
        <div>
          <span>Total P&amp;L</span>
          <strong style={{ color: Number(overview?.total_pnl_paper || 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtMoney(overview?.total_pnl_paper)}</strong>
        </div>
      </div>
      {data.length < 2 ? (
        <div className="chart-empty">Equity curve starts after the first closed or live-marked paper position.</div>
      ) : (
        <ResponsiveContainer width="100%" height={210}>
          <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <defs>
              <linearGradient id="paperEquityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={color} stopOpacity={0.28} />
                <stop offset="95%" stopColor={color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
            <XAxis dataKey="n" tick={{ fill: '#39435e', fontSize: 9, fontFamily: 'var(--font-mono)' }} tickLine={false} axisLine={false} />
            <YAxis domain={['auto', 'auto']} tick={{ fill: '#39435e', fontSize: 9, fontFamily: 'var(--font-mono)' }} tickLine={false} axisLine={false} />
            <ReferenceLine y={start} stroke="rgba(255,255,255,0.12)" strokeDasharray="4 2" />
            <Tooltip content={<ChartTip />} />
            <Area type="monotone" dataKey="equity" stroke={color} strokeWidth={1.8} fill="url(#paperEquityGrad)" dot={false} activeDot={{ r: 3 }} />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

function AlertCandidate({ alert, onAction }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const edge = opportunityEdge(alert)
  const entry = sidePrice(alert)
  const close = closeHours(alert)
  const age = ageMinutes(alert)
  const isNo = alert.direction === 'no'
  const modelSide = isNo ? 1 - alert.model_prob : alert.model_prob
  const maxLoss = entry
  const maxGain = entry == null ? null : 1 - entry
  const rec = recommendation(alert)
  const title = marketQuestion(alert)
  const action = paperActionInfo(alert)

  const paperTrade = async () => {
    setBusy(true)
    setError('')
    const r = await fetch(`/api/alerts/${alert.id}/paper-trade`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contracts: action.contracts, learning_override: action.manual }),
    })
    if (!r.ok) {
      const d = await r.json().catch(() => ({}))
      setError(d.detail || 'Paper trade was rejected.')
      setBusy(false)
      return
    }
    setBusy(false)
    onAction()
  }

  const dismiss = () => fetch(`/api/alerts/${alert.id}/skip`, { method: 'POST' }).then(onAction).catch(onAction)

  return (
    <div className={`paper-row signal ${alert.direction === 'no' ? 'signal-no' : 'signal-yes'} ${action.enabled ? 'actionable' : 'waiting'}`}>
      <div className="paper-row-main">
        <div>
          <div className="trade-title">{title}</div>
          <div className="trade-sub">
            <span>Option {optionLabel(alert)}</span>
            <span>{close == null ? 'close unknown' : `closes in ${Math.max(0, close).toFixed(1)}h`}</span>
            <span>{age == null ? 'freshness unknown' : `updated ${age}m ago`}</span>
            <span>current {currentTemp(alert)}</span>
          </div>
        </div>
        <div className="paper-actions">
          <button className={`btn btn-sm ${action.enabled ? `btn-side-${String(alert.direction || 'yes')}` : 'btn-wait'}`} onClick={paperTrade} disabled={!action.enabled || busy}>
            {busy ? 'Opening…' : action.label}
          </button>
          <button className="btn btn-dismiss btn-sm" onClick={dismiss}>Dismiss</button>
        </div>
      </div>

      <div className="paper-metrics-grid">
        <div><span>Value vs price</span><strong style={{ color: edge >= 0.08 ? 'var(--green)' : edge > 0 ? 'var(--amber)' : 'var(--red)' }}>{fmtEdge(edge)}</strong></div>
        <div><span>Model chance</span><strong style={{ color: 'var(--blue)' }}>{fmtPct(modelSide)}</strong></div>
        <div><span>Market price</span><strong>{fmtPct(entry)}</strong></div>
        <div><span>Size</span><strong style={{ color: action.enabled ? 'var(--green)' : 'var(--text-muted)' }}>{action.enabled ? action.sub : 'Wait'}</strong></div>
        <div><span>Good entries</span><strong>{fmtPct(rec.historical_positive_clv_rate, 0)}</strong></div>
        <div><span>Live trust</span><strong>{alert.brain_score ?? '—'} · {stateLabel(alert.brain_state)}</strong></div>
      </div>
      <div className="paper-reason">
        {action.enabled
          ? `Model sees ${fmtPct(modelSide)} chance but market prices at ${fmtPct(entry)} → ${fmtEdge(edge)} value gap. ${action.sub} paper trade.`
          : humanBlocker((rec.blockers || [rec.reason])[0]) || `${fmtDollar(maxGain)} max gain / ${fmtDollar(maxLoss == null ? null : -maxLoss)} max loss`}
      </div>
      {error && <div className="paper-error">{error}</div>}
    </div>
  )
}

function OpenTrade({ trade }) {
  const entry = tradeEntryPrice(trade)
  const side = String(trade.direction || 'yes').toUpperCase()
  const { city, rest } = humanizeMarketParts(trade.market_ticker, trade.market_title)
  const title = [city, rest].filter(Boolean).join(' · ') || 'Weather market'
  const pnl = Number(trade.unrealized_pnl || 0)
  const sideStop = trade.stop_loss_price == null ? null : trade.direction === 'no' ? 1 - Number(trade.stop_loss_price) : Number(trade.stop_loss_price)
  const sideTarget = trade.take_profit_price == null ? null : trade.direction === 'no' ? 1 - Number(trade.take_profit_price) : Number(trade.take_profit_price)
  const current = trade.current_price == null ? null : Number(trade.current_price)
  const nearStop = current != null && sideStop != null && current <= sideStop + 0.03
  const nearTarget = current != null && sideTarget != null && current >= sideTarget - 0.03
  const status = nearStop ? 'Near stop' : nearTarget ? 'Near target' : pnl >= 0 ? 'In profit' : 'In drawdown'
  const badge = nearStop ? 'badge-red' : nearTarget ? 'badge-green' : pnl >= 0 ? 'badge-green' : 'badge-amber'

  const prevPrice = useRef(current)
  const [flash, setFlash] = useState(null)
  useEffect(() => {
    if (prevPrice.current != null && current != null && current !== prevPrice.current) {
      setFlash(current > prevPrice.current ? 'flash-up' : 'flash-down')
      const timer = setTimeout(() => setFlash(null), 1200)
      prevPrice.current = current
      return () => clearTimeout(timer)
    }
    prevPrice.current = current
  }, [current])

  const link = kalshiUrl(trade.market_ticker)

  return (
    <div className={`paper-row compact${flash ? ` ${flash}` : ''}`}>
      <div className="paper-row-main">
        <div>
          <div className="trade-title">{title}</div>
          <div className="trade-sub">
            <span>{side}</span>
            <span style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} unrealized</span>
            <span>{trade.entry_time ? new Date(`${trade.entry_time.replace(' ', 'T')}Z`).toLocaleString() : '—'}</span>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`badge ${badge}`}>{status}</span>
          {link && (
            <a className="btn btn-primary btn-sm" href={link} target="_blank" rel="noreferrer">
              View on Kalshi ↗
            </a>
          )}
        </div>
      </div>
      <div className="paper-metrics-grid compact">
        <div><span>Entry</span><strong>{fmtPct(entry)}</strong></div>
        <div><span>Current</span><strong className={flash || ''}>{fmtPct(trade.current_price)}</strong></div>
        <div><span>Stop</span><strong>{fmtPct(sideStop)}</strong></div>
        <div><span>Target</span><strong>{fmtPct(sideTarget)}</strong></div>
        <div><span>Contracts</span><strong>{trade.contracts}</strong></div>
        <div><span>Entry Trust</span><strong>{trade.brain_score ?? '—'}</strong></div>
      </div>
    </div>
  )
}

export default function Paper() {
  const [alerts, setAlerts] = useState([])
  const [trades, setTrades] = useState([])
  const [closedTrades, setClosedTrades] = useState([])
  const [overview, setOverview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshingMarks, setRefreshingMarks] = useState(false)

  const load = useCallback((refreshMarks = false) => {
    if (refreshMarks) setRefreshingMarks(true)
    else setLoading(true)
    Promise.all([
      fetch('/api/alerts?status=pending&limit=60').then(r => r.json()),
      fetch(`/api/trades?status=open&limit=120${refreshMarks ? '&refresh=1' : ''}`).then(r => r.json()),
      fetch('/api/trades?status=closed&limit=500').then(r => r.json()),
      fetch('/api/overview').then(r => r.json()),
    ]).then(([a, t, c, o]) => {
      const openTrades = t.trades || []
      const openEvents = new Set(openTrades.map(trade => eventKey(trade.market_ticker)))
      const openMarkets = new Set(openTrades.map(trade => trade.market_ticker))
      const candidates = (a.alerts || [])
        .filter(alert => !openMarkets.has(alert.market_ticker))
        .filter(alert => !openEvents.has(eventKey(alert.market_ticker)))
        .sort((x, y) => qualityScore(y) - qualityScore(x))
      const bestByEvent = []
      const seenEvents = new Set()
      for (const alert of candidates) {
        const key = eventKey(alert.market_ticker)
        if (seenEvents.has(key)) continue
        seenEvents.add(key)
        bestByEvent.push(alert)
      }
      setAlerts(bestByEvent.slice(0, 12))
      setTrades(openTrades.sort((x, y) => Number(y.unrealized_pnl || 0) - Number(x.unrealized_pnl || 0)))
      setClosedTrades(c.trades || [])
      setOverview(o)
    }).finally(() => {
      setLoading(false)
      setRefreshingMarks(false)
    })
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [load])

  const [sweeping, setSweeping] = useState(false)
  const [sweepResult, setSweepResult] = useState(null)
  const [resetting, setResetting] = useState(false)
  const [resetResult, setResetResult] = useState(null)

  const sweepSettlements = () => {
    setSweeping(true)
    setSweepResult(null)
    fetch('/api/trades/sweep-settlements', { method: 'POST' })
      .then(r => r.json())
      .then(d => { setSweepResult(d); load() })
      .catch(e => setSweepResult({ error: e.message }))
      .finally(() => setSweeping(false))
  }

  const resetPaperTrades = () => {
    if (!window.confirm('Close all open paper trades at current Kalshi prices and start fresh?')) return
    setResetting(true)
    setResetResult(null)
    fetch('/api/trades/reset-paper-trades', { method: 'POST' })
      .then(r => r.json())
      .then(d => { setResetResult(d); load() })
      .catch(e => setResetResult({ error: e.message }))
      .finally(() => setResetting(false))
  }

  const balance = overview?.paper_balance
  const pnl = overview?.total_pnl_paper

  return (
    <div className="paper-page">
      <div className="page-hd">
        <div>
          <div className="page-title">Paper Trading</div>
          <div className="page-sub">Open paper trades, live Kalshi marks, and next paper candidates</div>
        </div>
        <div className="paper-balance">
          <span>Paper balance</span>
          <strong>{balance == null ? '—' : `$${Number(balance).toFixed(2)}`}</strong>
          <small>{fmtMoney(pnl)} realized · {fmtMoney(overview?.unrealized_pnl_paper)} live</small>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-ghost btn-sm" onClick={() => load(true)} disabled={refreshingMarks}>
            {refreshingMarks ? 'Refreshing marks…' : 'Refresh marks'}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={sweepSettlements} disabled={sweeping}>
            {sweeping ? 'Sweeping…' : 'Sweep Settlements'}
          </button>
          <button className="btn btn-danger btn-sm" onClick={resetPaperTrades} disabled={resetting}>
            {resetting ? 'Resetting…' : 'Reset Paper Book'}
          </button>
        </div>
      </div>
      {sweepResult && !sweepResult.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-2)', padding: '8px 12px', background: 'var(--card)', borderRadius: 'var(--radius-sm)', marginBottom: 8 }}>
          Settled {sweepResult.settle?.settled ?? 0} trades · Backfilled {sweepResult.backfill?.updated ?? 0} · Cross-ref {sweepResult.cross_reference?.updated ?? 0} · {sweepResult.current_stats?.prediction_accuracy ?? 0}% prediction accuracy ({sweepResult.current_stats?.prediction_sample_count ?? 0} samples)
        </div>
      )}
      {resetResult && !resetResult.error && (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-2)', padding: '8px 12px', background: 'var(--card)', borderRadius: 'var(--radius-sm)', marginBottom: 8 }}>
          Reset {resetResult.reset?.closed ?? 0} trades · {resetResult.current_stats?.prediction_accuracy ?? 0}% prediction accuracy ({resetResult.current_stats?.prediction_sample_count ?? 0} samples)
        </div>
      )}
      {(sweepResult?.error || resetResult?.error) && (
        <div style={{ fontSize: '0.78rem', color: 'var(--red)', padding: '8px 12px' }}>{sweepResult?.error || resetResult?.error}</div>
      )}

      <PaperEquityChart overview={overview} closedTrades={closedTrades} />

      <div className="paper-grid">
        <section>
          <div className="section-hd">Next Paper Candidates</div>
          {loading && alerts.length === 0 ? (
            <div className="empty">Loading paper candidates…</div>
          ) : alerts.length === 0 ? (
            <div className="empty">No pending paper candidates.</div>
          ) : (
            <div className="paper-list">
              {alerts.map(a => <AlertCandidate key={a.id} alert={a} onAction={load} />)}
            </div>
          )}
        </section>

        <section>
          <div className="section-hd">Open Paper Trades ({trades.length})</div>
          {trades.length === 0 ? (
            <div className="empty">No open paper positions.</div>
          ) : (
            <div className="paper-list">
              {trades.map(t => <OpenTrade key={t.id} trade={t} />)}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
