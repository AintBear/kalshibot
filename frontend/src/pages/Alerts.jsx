import React, { useEffect, useState, useCallback } from 'react'
import {
  ageMinutes,
  cleanTitle,
  closeHours,
  currentTemp,
  eventKey,
  humanizeMarketParts,
  marketQuestion,
  optionLabel,
  paperActionInfo,
  qualityScore,
  recommendation,
  segmentLabel,
  stateLabel,
} from '../utils/format'

const STATE_BADGE = {
  paper_ready: 'badge-green',
  watch:       'badge-amber',
  caution:     'badge-sky',
  skip:        'badge-red',
}

const PHANTOM_BADGE = {
  none:   'badge-muted',
  low:    'badge-muted',
  medium: 'badge-amber',
  high:   'badge-red',
}

const TIER_BADGE = {
  tier_a:   'badge-green',
  tier_b:   'badge-sky',
  watch:    'badge-amber',
  learning: 'badge-purple',
  avoid:    'badge-red',
}

const fmtPct    = (v, d = 0) => v == null ? '—' : `${(v * 100).toFixed(d)}%`
const fmtPP     = v => v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)}¢`
const fmtCents  = v => v == null ? '—' : `${(v >= 0 ? '+' : '')}${(v * 100).toFixed(1)}¢`
const fmtDollar = v => v == null ? '—' : `${v >= 0 ? '+$' : '-$'}${Math.abs(v).toFixed(2)}`
const fmtCash   = v => v == null ? '—' : `$${Math.abs(Number(v)).toFixed(2)}`
const fmtEntryCents = v => v == null ? '—' : `${Math.round(Number(v) * 100)}¢`
const cleanCopy = value => cleanTitle(value)
  .replace(/\s+([?!.,])/g, '$1')
  .replace(/\bbot chance\b/gi, 'model chance')
  .replace(/\bedge\b/gi, 'value')
  .replace(/\s+/g, ' ')
  .trim()
const sourceLabel = value => {
  const raw = Array.isArray(value) ? value.join('+') : String(value || '')
  if (!raw || raw === 'none') return 'No forecast source'
  return raw
    .replace(/\bnws_free\b/gi, 'NOAA/NWS')
    .replace(/\baccuweather\b/gi, 'AccuWeather')
    .replace(/\bnoaa_cdo\b/gi, 'NOAA CDO')
    .replace(/\bNWS\b/g, 'NOAA/NWS')
    .replace(/\+/g, ' + ')
}

const activeWeatherEvents = a => {
  const det = a.details || {}
  const events = a.active_weather_events || det.active_weather_events || []
  return Array.isArray(events) ? events.filter(Boolean) : []
}

const eventHeadline = event => event?.headline || event?.event || event?.type || 'Active weather event'

const timePriorityLabel = value => ({
  high: 'High urgency',
  normal: 'Normal timing',
  low: 'Low urgency',
}[value] || 'Timing unknown')

const fmtHours = value => {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  if (num < 1) return `${Math.max(1, Math.round(num * 60))}m`
  return `${num.toFixed(num < 10 ? 1 : 0)}h`
}

/* Gradient quality dots — one dot per dimension, colored by value */
function TierDots({ alert: a }) {
  const edge      = opportunityEdge(a) || 0
  const brain     = a.details?.brain || {}
  const learned   = brain.learned || {}
  const rec       = recommendation(a)
  const phantom   = a.phantom_risk_level || 'none'
  const conf      = a.confidence || 0

  const dot = (score, label) => {
    const color = score >= 0.7 ? '#00c805' : score >= 0.4 ? '#f59e0b' : '#f04444'
    return (
      <span key={label} className="tier-dot" style={{ background: color }} title={label} />
    )
  }

  const edgeScore    = Math.min(1, Math.max(0, edge / 0.15))
  const confScore    = conf
  const brainScore   = (a.brain_score || 0) / 100
  const riskScore    = phantom === 'high' ? 0 : phantom === 'medium' ? 0.35 : phantom === 'low' ? 0.6 : 1.0
  const learnScore   = learned.positive_clv_rate != null ? learned.positive_clv_rate : 0.5
  const clvHistScore = (learned.avg_clv != null && learned.trade_count >= 3)
    ? Math.min(1, Math.max(0, 0.5 + learned.avg_clv * 10))
    : 0.5
  const sizeScore    = (rec.contracts || 0) > 0 ? 0.9 : 0.1

  return (
    <div className="tier-dots">
      {dot(edgeScore,    'Value strength')}
      {dot(confScore,    'Forecast confidence')}
      {dot(brainScore,   'Bot trust')}
      {dot(riskScore,    'Mismatch risk')}
      {dot(learnScore,   'Historical good-entry rate')}
      {dot(clvHistScore, 'Average entry move from similar trades')}
      {dot(sizeScore,    'Recommended size')}
    </div>
  )
}

function sidePrice(a) {
  if (a.direction === 'no') {
    const noAsk = a.no_ask ?? a.details?.no_ask
    if (noAsk != null && !Number.isNaN(Number(noAsk))) return Number(noAsk)
  } else {
    const yesAsk = a.yes_ask ?? a.details?.yes_ask
    if (yesAsk != null && !Number.isNaN(Number(yesAsk))) return Number(yesAsk)
  }
  const yes = Number(a.market_price || 0)
  if (!yes) return null
  return a.direction === 'no' ? 1 - yes : yes
}

function opportunityEdge(a) {
  const rec = recommendation(a)
  if (rec.side_edge != null && !Number.isNaN(Number(rec.side_edge))) return Number(rec.side_edge)
  if (a.edge == null) return null
  return a.direction === 'no' ? -a.edge : a.edge
}

function alertTone(a) {
  const rec = recommendation(a)
  const tier = tradeTier(a, rec, a.details?.brain || {})
  if (a.status === 'paper_traded') return 'paper'
  if ((rec.contracts || 0) <= 0 || tier === 'avoid') return 'neutral'
  if (tier === 'tier_a' || (a.brain_state === 'paper_ready' && (rec.contracts || 0) > 0)) return 'good'
  if (tier === 'tier_b' || tier === 'watch' || tier === 'learning') return 'warn'
  return 'neutral'
}

function tradeTier(a, rec = recommendation(a), brain = a.details?.brain || {}) {
  if (rec.tier) return rec.tier
  const edge = opportunityEdge(a) || 0
  const state = brain.state || a.brain_state
  if ((a.phantom_risk_level || 'none') === 'high' || edge <= 0) return 'avoid'
  if (state === 'paper_ready' && edge >= 0.08 && (rec.contracts || 0) > 0) return 'tier_b'
  if (state === 'watch') return 'watch'
  if (edge > 0) return 'learning'
  return 'avoid'
}

function manualPaperEligible(a, rec = recommendation(a)) {
  const phantom = a.phantom_risk_level || a.details?.phantom_risk_level || 'none'
  const blocked = a.event_has_open_trade || a.details?.event_has_open_trade
  const edge = Number(rec.side_edge ?? opportunityEdge(a) ?? 0)
  const ev = Number(rec.expected_value_per_contract ?? edge)
  return a.status === 'pending' && !blocked && phantom !== 'high' && edge > 0 && ev > 0
}

function tierLabel(tier, rec = {}) {
  if (rec.tier_label) return rec.tier_label
  const map = {
    tier_a: 'Tier A',
    tier_b: 'Tier B',
    watch: 'Watch',
    learning: 'Learning only',
    avoid: 'Avoid',
  }
  return map[tier] || 'Check'
}

function tierVerdict(tier, rec, a) {
  const blockers = rec.blockers || []
  if ((a.details || {}).event_has_open_trade || a.event_has_open_trade) {
    return 'Already tracking this event in paper. Do not add another bracket.'
  }
  if (tier === 'tier_a') return 'Best setup: positive model value and similar trades have earned trust.'
  if (tier === 'tier_b') return 'Tradable on paper, but keep size conservative until recent entry quality improves.'
  if (tier === 'watch') return 'Good enough to monitor; wait for a cleaner quote or stronger trust score.'
  if (tier === 'learning') return 'Use only as a learning sample. The model value is there, but history is not ready.'
  return humanMsg(blockers[0] || rec.reason || 'Wait for model and market agreement to improve.')
}

function dirColor(dir) {
  return dir === 'no' ? 'var(--red)' : 'var(--green)'
}

function humanMsg(msg) {
  const MAP = {
    low_confidence_model: 'Low-confidence forecast',
    moderate_confidence:  'Moderate forecast confidence',
    thin_edge:            'Thin model value after checks',
    modest_edge:          'Modest model value',
    phantom_risk_high:    'High mismatch risk',
    phantom_risk_medium:  'Medium mismatch risk',
    phantom_risk_low:     'Low mismatch risk',
    tiny_payout:          'Tiny remaining payout',
    small_payout:         'Small remaining payout',
    extreme_quote:        'Extreme live quote',
    segment_negative_clv: 'Similar paper entries moved against us',
    recent_clv_negative:  'Recent entries moved against us',
    low_positive_clv_rate:'Low good-entry rate',
    positive_clv_rate_below_live_gate: 'Good-entry rate below live gate',
    segment_paper_pnl_negative: 'Similar paper trades are losing money',
    segment_positive_clv: 'Similar trades have good entries',
    recent_clv_positive:  'Recent entries improved',
    wide_spread:          'Wide bid/ask spread',
    moderate_spread:      'Moderate bid/ask spread',
  }
  return MAP[msg] || cleanCopy(String(msg || '').replaceAll('_', ' ').replace(/\s+[—-]\s+/g, ': '))
}

function riskFlags(a, brain) {
  const raw = a.phantom_risk_flags || a.details?.phantom_risk_flags || brain?.phantom_risk?.flags || []
  if (Array.isArray(raw)) return raw
  try {
    const p = JSON.parse(raw)
    return Array.isArray(p) ? p : []
  } catch {
    return String(raw || '').split(',').filter(Boolean)
  }
}

function brainReason(status) {
  if (!status) return 'Loading readiness checks…'
  const parts = []
  parts.push(`${status.learning_samples ?? status.settled_trades ?? 0} settled samples`)
  const avgClv = Number(status.avg_clv || 0)
  const recentClv = Number(status.recent_30_avg_clv || 0)
  const posRate = Math.round((status.positive_clv_rate || 0) * 100)
  parts.push(`avg entry move ${avgClv >= 0 ? '+' : ''}${avgClv.toFixed(1)}¢${avgClv < 0 ? ' ✗' : ''}`)
  parts.push(`recent ${recentClv >= 0 ? '+' : ''}${recentClv.toFixed(1)}¢${recentClv < 0 ? ' ✗' : ''}`)
  parts.push(`${posRate}% good entries${posRate < 50 ? ' (need 50%) ✗' : ''}`)
  const pnl = Number(status.realized_pnl_paper || 0)
  parts.push(`paper P&L ${fmtDollar(pnl)}${pnl < 0 ? ' ✗' : ''}`)
  return parts.join(' · ')
}

/* ─── Brain Panel ─────────────────────────────────────────────────── */
function BrainPanel({ status }) {
  const score      = status?.score ?? '—'
  const state      = status?.state || 'unknown'
  const ready      = status?.entry_quality_ok === true
  const automation = status?.automation_enabled || status?.auto_trade_enabled
  const label      = status?.readiness_label || stateLabel(state)

  return (
    <div className="readiness-panel">
      <div>
        <div className="panel-kicker">Bot Trust</div>
        <div className="readiness-score">
          <span>{score}</span>
          <small>/100</small>
          <span className={`badge ${ready ? 'badge-green' : 'badge-amber'}`}>{label}</span>
        </div>
      </div>
      <div className="readiness-copy">
        <strong>{ready ? 'Live-readiness gate is passing.' : 'Paper learning can keep running; live orders still need 50%+ good entries, positive recent move, and positive paper P&L.'}</strong>
        <span>{brainReason(status)}</span>
        <span>{status?.open_trades || 0} open positions waiting to settle · entry quality is measured by how much prices moved in your favor after entry, not just whether trades were profitable.</span>
        {(status?.pending_settlement_trades || 0) > 0 && (
          <span>{status.pending_settlement_trades} closed trades awaiting settlement data before they count toward learning.</span>
        )}
        <span>
          Paper {status?.paper_trading === false ? 'off' : 'on'} ·
          Auto scan {status?.automation_enabled ? 'on' : 'off'} ·
          Paper bot {status?.auto_paper_trade_enabled ? 'on' : 'off'} ·
          Live auto {status?.auto_trade_enabled ? <span style={{ color: 'var(--red)' }}>ON</span> : 'off'}
        </span>
      </div>
    </div>
  )
}

/* ─── AI Analysis Block ───────────────────────────────────────────── */
function AIAnalysis({ a }) {
  const det      = a.details || {}
  const brain    = det.brain || {}
  const forecast = det.forecast || {}
  const edge     = opportunityEdge(a)
  const entry    = sidePrice(a)
  const isNo     = a.direction === 'no'
  const phantom  = a.phantom_risk_level || 'none'
  const flags    = riskFlags(a, brain)
  const rec      = recommendation(a)
  const ctx      = det.analysis_context || {}
  const learned  = ctx.segment_learning || brain.learned || {}
  const current  = a.current_conditions || det.current_conditions || {}
  const settlementStation = a.settlement_station || det.settlement_station || current.settlement_station || current.station
  const forecastSources = a.forecast_sources || det.forecast_sources || forecast.forecast_sources || forecast.sources || forecast.source || 'NWS'
  const events = activeWeatherEvents(a)
  const hoursToClose = a.hours_to_close ?? det.hours_to_close
  const timePriority = a.time_priority || det.time_priority
  const modelSideProb = isNo ? 1 - (a.model_prob || 0) : (a.model_prob || 0)
  const marketSideProb = entry
  const ev100 = rec.expected_value_per_contract != null ? rec.expected_value_per_contract * 100 : null
  const source = sourceLabel(forecastSources)
  const confidenceTone = (a.confidence || 0) >= 0.65 ? 'good' : (a.confidence || 0) >= 0.45 ? 'warn' : 'bad'
  const learnedTone = (learned.positive_clv_rate ?? rec.historical_positive_clv_rate ?? 0) >= 0.5 ? 'good' : 'bad'
  const riskTone = phantom === 'high' ? 'bad' : phantom === 'medium' ? 'warn' : 'good'
  const edgeTone = edge >= 0.08 ? 'good' : edge > 0 ? 'warn' : 'bad'
  const { city, rest } = humanizeMarketParts(a.market_ticker, a.market_title || det.market_title)
  const question = marketQuestion(a)
  const option = optionLabel(a)
  const marketType = rest || 'Weather market'
  const segmentTrades = learned.trade_count || rec.historical_trade_count || 0
  const positiveRate = learned.positive_clv_rate ?? rec.historical_positive_clv_rate
  const avgClv = learned.avg_clv
  const recentClv = learned.recent_avg_clv ?? rec.historical_recent_clv
  const snapshot = ctx.latest_snapshot || {}
  const resolved = ctx.latest_resolved_snapshot
  const bestOption = ctx.event_best_option
  const forecastValue = marketType.includes('Low')
    ? (forecast.low ?? snapshot.forecast_low)
    : marketType.includes('Rain') || marketType.includes('Precip')
      ? (forecast.precip_pct ?? snapshot.forecast_precip)
      : (forecast.high ?? snapshot.forecast_high)
  const threshold = a.floor_strike ?? a.cap_strike ?? det.floor_strike ?? det.cap_strike
  const thresholdText = threshold == null
    ? 'the listed threshold'
    : marketType.includes('Rain') || marketType.includes('Precip')
      ? `${threshold} in.`
      : `${threshold}°F`
  const forecastText = forecastValue == null
    ? 'Forecast value is not available in the latest snapshot'
    : marketType.includes('Rain') || marketType.includes('Precip')
      ? `Forecast precipitation probability is ${Number(forecastValue).toFixed(0)}% against ${thresholdText}`
      : `Forecast is ${Number(forecastValue).toFixed(0)}°F against ${thresholdText}`
  const historySentence = segmentTrades > 0
    ? `Across ${segmentTrades} similar ${segmentLabel(learned.segment_key || ctx.segment_key).toLowerCase()} trades, Sibylla's entries moved ${fmtCents(avgClv)} on average, and ${fmtPct(positiveRate, 0)} were good entries.`
    : `This exact segment is still thin, so the aggregate weather record is carrying most of the trust estimate.`
  const edgeSentence = `The current value is ${edge == null ? 'unknown' : fmtPP(edge)}: the model puts ${(a.direction || 'yes').toUpperCase()} at ${fmtPct(modelSideProb, 1)} while the market price is ${fmtEntryCents(marketSideProb)}.`
  const forecastSentence = `${city ? `${city} setup:` : 'Weather setup:'} ${forecastText}; confidence is ${fmtPct(a.confidence, 0)} from ${source}.`
  const resolutionSentence = resolved
    ? `Most recent resolved snapshot for this market logged forecast H ${resolved.forecast_high ?? '—'} / L ${resolved.forecast_low ?? '—'} / precip ${resolved.forecast_precip ?? '—'} and actual H ${resolved.actual_high ?? '—'} / L ${resolved.actual_low ?? '—'} / precip ${resolved.actual_precip ?? '—'}.`
    : `No resolved snapshot exists yet for this exact market; current judgment is based on segment history and the latest forecast snapshot.`
  const sizingSentence = rec.contracts > 0
    ? `Sizing clears for ${rec.contracts} contract${rec.contracts === 1 ? '' : 's'} because expected value is ${ev100 == null ? 'unknown' : `${ev100 >= 0 ? '+' : ''}${ev100.toFixed(1)}c`} per contract and trust checks passed.`
    : manualPaperEligible(a, rec)
      ? `Auto size is zero, but a 1-contract paper trade is allowed because model value is positive.`
      : `Sizing is blocked because ${(rec.blockers || []).slice(0, 2).join(' and ') || 'the current risk gates are not satisfied'}.`
  const bestOptionSentence = bestOption
    ? `Within this event, the best-priced option right now is ${cleanTitle(bestOption.market_title || optionLabel(bestOption))}; Sibylla prefers ${(bestOption.direction || 'yes').toUpperCase()} at ${fmtPct(bestOption.entry_price, 1)} with ${fmtPct(bestOption.model_side_probability, 1)} bot chance.`
    : 'No better bracket was found inside this event during the latest scan.'
  const aiThesis = [
    `${marketQuestion(a)} is being judged against ${optionLabel(a)}. The trade side is ${(a.direction || 'yes').toUpperCase()}, not the raw bracket label.`,
    historySentence,
    recentClv == null ? 'Recent entry quality is not available yet.' : `Recent entries moved ${fmtCents(recentClv)}, which is ${recentClv >= 0 ? 'supporting' : 'weakening'} the setup.`,
    bestOptionSentence,
    resolutionSentence,
    `The thesis is strongest if multiple sources keep the forecast near the same side of the threshold and the live quote stays tight; it weakens if the quote moves against entry before settlement or this segment keeps stopping out.`
  ]

  return (
    <details className="ai-analysis">
      <summary>
        Signal read {a.brain_score ? `· Trust ${a.brain_score}/100` : ''} {(a.confidence||0) > 0 ? `· ${Math.round((a.confidence||0)*100)}% confidence` : ''}
      </summary>

      {cleanCopy(det.analysis || a.analysis) && (
        <div className="sibylla-narrative">
          <span className="sibylla-label">Sibylla</span>
          <p>{cleanCopy(det.analysis || a.analysis)}</p>
        </div>
      )}

      <div className="analysis-summary">
        <div>
          <strong>{city ? `${city} · ${marketType}` : marketType}</strong>
          <p>{historySentence} {edgeSentence}</p>
          <p>{forecastSentence} {sizingSentence}</p>
        </div>
        <div className="analysis-summary-score">
          <span>{a.brain_score ?? '—'}</span>
          <small>trust</small>
        </div>
      </div>

      <div className="analysis-chipline">
        <span className={`analysis-chip ${edgeTone}`}>Value {fmtPP(edge)}</span>
        <span className={`analysis-chip ${ev100 == null ? 'warn' : ev100 >= 0 ? 'good' : 'bad'}`}>EV {ev100 == null ? '—' : `${ev100 >= 0 ? '+' : ''}${ev100.toFixed(1)}c`}</span>
        <span className={`analysis-chip ${confidenceTone}`}>Confidence {fmtPct(a.confidence, 0)}</span>
        <span className={`analysis-chip ${timePriority === 'high' ? 'good' : timePriority === 'low' ? 'warn' : 'warn'}`}>{timePriorityLabel(timePriority)} · {fmtHours(hoursToClose)}</span>
        {events.length > 0 && <span className="analysis-chip warn">{events.length} active weather event{events.length === 1 ? '' : 's'}</span>}
        <span className={`analysis-chip ${learnedTone}`}>History {segmentTrades} · {fmtPct(positiveRate, 0)} good entries</span>
        {['medium', 'high'].includes(phantom) && <span className={`analysis-chip ${riskTone}`}>Forecast disagreement {phantom}</span>}
      </div>

      <div className="analysis-compact-grid">
        <div className="analysis-panel">
          <h4>AI Thesis</h4>
          {aiThesis.map((line, i) => (
            <div className="analysis-note" key={`ai-${i}`}>{line}</div>
          ))}
        </div>

        <div className="analysis-panel">
          <h4>Live Setup</h4>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Direction / entry</span>
            <span className="analysis-row-val" style={{ color: dirColor(a.direction) }}>{(a.direction || 'yes').toUpperCase()} @ {fmtEntryCents(entry)}</span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Model side probability</span>
            <span className="analysis-row-val val-green">{fmtPct(modelSideProb, 1)}</span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Forecast vs threshold</span>
            <span className="analysis-row-val val-sky">{forecastValue == null ? '—' : marketType.includes('Rain') || marketType.includes('Precip') ? `${Number(forecastValue).toFixed(0)}% / ${thresholdText}` : `${Number(forecastValue).toFixed(0)}°F / ${thresholdText}`}</span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Live YES bid / ask</span>
            <span className="analysis-row-val">{fmtEntryCents(a.yes_bid)} / {fmtEntryCents(a.yes_ask)}</span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Recommended size</span>
            <span className="analysis-row-val">{rec.contracts ?? 0} @ {fmtEntryCents(rec.limit_price_side)}</span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Current / forecast</span>
            <span className="analysis-row-val val-sky">
              {current.temperature == null ? '—' : `${Number(current.temperature).toFixed(0)}°F`} / H {forecast.high ?? '—'} L {forecast.low ?? '—'}
            </span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Forecast source</span>
            <span className="analysis-row-val">{source}</span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Time priority</span>
            <span className="analysis-row-val">{timePriorityLabel(timePriority)} · closes in {fmtHours(hoursToClose)}</span>
          </div>
          <div className="analysis-row">
            <span className="analysis-row-lbl">Settlement source</span>
            <span className="analysis-row-val">
              {settlementStation ? `Settlement station: ${settlementStation}` : 'NWS daily climate report'}
            </span>
          </div>
          {events.length > 0 && (
            <div className="risk-flags">
              {events.slice(0, 3).map((event, i) => (
                <span key={`event-${i}`} className="risk-flag-chip">{eventHeadline(event)}</span>
              ))}
            </div>
          )}
          {flags.length > 0 && (
            <div className="risk-flags">
              {flags.map((f, i) => (
                <span key={i} className="risk-flag-chip">{humanMsg(f)}</span>
              ))}
            </div>
          )}
          {a.rules_primary && <div className="settlement-rule">{cleanCopy(a.rules_primary)}</div>}
        </div>

        {(rec.blockers || []).length > 0 && (
          <div className="analysis-panel">
            <h4>Why Not</h4>
            {(rec.blockers || []).slice(0, 4).map((item, i) => (
              <div className="analysis-note bad" key={`b-${i}`}>{humanMsg(item)}</div>
            ))}
          </div>
        )}
      </div>
    </details>
  )
}

function TradeActionCard({ alert: a, onPaperTrade }) {
  const det = a.details || {}
  const rec = recommendation(a)
  const entry = rec.limit_price_side ?? sidePrice(a)
  const contracts = Number(rec.contracts || 0)
  const direction = (a.direction || 'yes').toUpperCase()
  const canTrade = a.status === 'pending' && contracts > 0 && !(a.event_has_open_trade || det.event_has_open_trade)
  const action = paperActionInfo(a, 'alerts')
  const actionContracts = action.contracts
  const side = a.direction === 'no' ? 'no' : 'yes'
  const yesStop = rec.stop_loss_price
  const yesTarget = rec.take_profit_price
  const sideStop = yesStop == null ? (entry == null ? null : entry * 0.5) : side === 'no' ? 1 - yesStop : yesStop
  const sideTarget = yesTarget == null ? (entry == null ? null : Math.min(0.99, entry * 1.5)) : side === 'no' ? 1 - yesTarget : yesTarget
  const maxLoss = actionContracts > 0 && entry != null ? actionContracts * entry : null
  const maxPayout = actionContracts > 0 && entry != null ? actionContracts * (1 - entry) : null

  if (a.status === 'paper_traded') return null

  return (
    <div className={`trade-action-card ${action.enabled ? 'ready' : 'muted'}`}>
      <div className="trade-action-side" style={{ color: action.enabled ? dirColor(a.direction) : 'var(--text-2)' }}>
        {action.enabled ? direction : 'Wait'}
      </div>
      <div className="trade-action-grid">
        <div><span>Decision</span><strong>{action.enabled ? direction : 'WAIT'}</strong></div>
        <div><span>Entry plan</span><strong>{actionContracts || 0} @ {fmtEntryCents(entry)}</strong></div>
        <div><span>Capital at risk</span><strong>{fmtCash(maxLoss)}</strong></div>
        <div><span>Potential payout</span><strong>{fmtCash(maxPayout)}</strong></div>
        <div><span>Stop loss</span><strong>Exit if price drops to {fmtEntryCents(sideStop)}</strong></div>
        <div><span>Take profit</span><strong>Target {fmtEntryCents(sideTarget)} to lock gains</strong></div>
        <div><span>Entry trust</span><strong>{a.brain_score ?? '—'}/100 · {fmtPct(a.confidence, 0)} conf</strong></div>
      </div>
      <button
        className={`btn btn-sm ${action.enabled ? `btn-side-${side}` : 'btn-ghost'}`}
        onClick={() => onPaperTrade(a.id, actionContracts, action.manual)}
        disabled={!action.enabled}
        title={action.enabled ? 'Open this paper trade' : humanMsg((rec.blockers || [rec.reason || 'Risk gates blocked this entry'])[0])}
      >
        {action.label}
      </button>
    </div>
  )
}

/* ─── Quick Reason Summary (visible without expanding) ────────────── */
function QuickReason({ alert: a }) {
  const det = a.details || {}
  const rec = recommendation(a)
  const edge = opportunityEdge(a)
  const isNo = a.direction === 'no'
  const modelSideProb = isNo ? 1 - (a.model_prob || 0) : (a.model_prob || 0)
  const entry = sidePrice(a)
  const forecast = det.forecast || {}
  const { city, rest } = humanizeMarketParts(a.market_ticker, a.market_title || det.market_title)
  const marketType = rest || ''
  const forecastValue = marketType.includes('Low')
    ? forecast.low : marketType.includes('Rain') || marketType.includes('Precip')
      ? forecast.precip_pct : forecast.high
  const threshold = a.floor_strike ?? a.cap_strike ?? det.floor_strike ?? det.cap_strike
  const phantom = a.phantom_risk_level || 'none'
  const learned = det.brain?.learned || (det.analysis_context || {}).segment_learning || {}
  const segmentTrades = learned.trade_count || rec.historical_trade_count || 0
  const positiveRate = learned.positive_clv_rate ?? rec.historical_positive_clv_rate
  const ctx = det.analysis_context || {}
  const bestOption = ctx.event_best_option

  const reasons = []

  if (edge != null && edge > 0) {
    const diff = modelSideProb - (entry || 0)
    reasons.push({
      tone: 'good',
      text: `Model says ${fmtPct(modelSideProb, 0)} chance, market prices at ${fmtEntryCents(entry)} — ${fmtPP(edge)} value gap`
    })
  } else if (edge != null) {
    reasons.push({ tone: 'bad', text: `No value edge at current price` })
  }

  if (forecastValue != null && threshold != null) {
    const isTemp = !(marketType.includes('Rain') || marketType.includes('Precip'))
    const fVal = Number(forecastValue).toFixed(0)
    const dir = (a.direction || 'yes').toUpperCase()
    if (isTemp) {
      reasons.push({ tone: 'neutral', text: `Forecast ${fVal}°F vs ${threshold}°F threshold → betting ${dir}` })
    } else {
      reasons.push({ tone: 'neutral', text: `Forecast ${fVal}% precip vs ${threshold} in. threshold → betting ${dir}` })
    }
  }

  if (segmentTrades >= 3 && positiveRate != null) {
    const pct = Math.round(positiveRate * 100)
    reasons.push({
      tone: pct >= 50 ? 'good' : 'bad',
      text: `${segmentTrades} similar trades: ${pct}% had good entries`
    })
  } else if (segmentTrades > 0) {
    reasons.push({ tone: 'neutral', text: `${segmentTrades} similar trades (still building history)` })
  }

  if (['medium', 'high'].includes(phantom)) {
    reasons.push({ tone: 'bad', text: `Weather sources disagree (${phantom} risk)` })
  }

  if (bestOption && bestOption.market_ticker !== a.market_ticker) {
    reasons.push({
      tone: 'neutral',
      text: `Best bracket in event: ${cleanTitle(optionLabel(bestOption))} at ${fmtEntryCents(bestOption.entry_price)}`
    })
  }

  if (!reasons.length) return null

  return (
    <div className="quick-reason">
      {reasons.slice(0, 4).map((r, i) => (
        <div key={i} className="quick-reason-row">
          <span className={`reason-icon reason-${r.tone}`}>{r.tone === 'good' ? '▸' : r.tone === 'bad' ? '⚠' : '◆'}</span>
          <span>{r.text}</span>
        </div>
      ))}
    </div>
  )
}

/* ─── Model vs Market Visual Bar ──────────────────────────────────── */
function EdgeBar({ alert: a }) {
  const isNo = a.direction === 'no'
  const modelProb = isNo ? 1 - (a.model_prob || 0) : (a.model_prob || 0)
  const entry = sidePrice(a)
  if (modelProb == null || entry == null) return null
  const modelPct = Math.round(modelProb * 100)
  const marketPct = Math.round(entry * 100)
  const edgeSign = modelPct > marketPct ? 'positive' : modelPct < marketPct ? 'negative' : 'flat'

  return (
    <div className="edge-bar-wrap">
      <div className="edge-bar-labels">
        <span>Model {modelPct}%</span>
        <span style={{ color: edgeSign === 'positive' ? 'var(--green)' : edgeSign === 'negative' ? 'var(--red)' : 'var(--text-muted)' }}>
          {edgeSign === 'positive' ? `+${modelPct - marketPct}¢ value` : edgeSign === 'negative' ? `${modelPct - marketPct}¢` : 'No edge'}
        </span>
        <span>Market {marketPct}¢</span>
      </div>
      <div className="edge-bar-track">
        <div className="edge-bar-model" style={{ width: `${modelPct}%` }} />
        <div className="edge-bar-market" style={{ left: `${marketPct}%` }} />
      </div>
    </div>
  )
}

/* ─── Alert Row Card ──────────────────────────────────────────────── */
function AlertRow({ alert: a, isTop, onExpire, onPaperTrade, onSkip }) {
  const det       = a.details || {}
  const brain     = det.brain || {}
  const edge      = opportunityEdge(a)
  const entry     = sidePrice(a)
  const tone      = alertTone(a)
  const phantom   = a.phantom_risk_level || 'none'
  const { city, rest } = humanizeMarketParts(a.market_ticker, a.market_title || det.market_title)
  const question = marketQuestion(a)
  const option = optionLabel(a)
  const direction = (a.direction || 'yes').toUpperCase()
  const isNo      = a.direction === 'no'
  const rec         = recommendation(a)
  const contracts   = Number(rec.contracts || 0)
  const limitPrice  = rec.limit_price_side ?? entry
  const ev100       = rec.expected_value_per_contract == null ? null : rec.expected_value_per_contract * 100
  const historyRate = rec.historical_positive_clv_rate
  const tier        = tradeTier(a, rec, brain)
  const eventBlocked = a.event_has_open_trade || det.event_has_open_trade
  const canManualPaper = manualPaperEligible(a, rec)
  const action = paperActionInfo(a, 'alerts')
  const closeH = closeHours(a)
  const alreadyPapered = a.status === 'paper_traded'
  const paperTrade = a.paper_trade || det.paper_trade || null
  const forecast = det.forecast || {}
  const marketType = rest || ''
  const forecastValue = marketType.includes('Low') ? forecast.low : marketType.includes('Rain') || marketType.includes('Precip') ? forecast.precip_pct : forecast.high
  const threshold = a.floor_strike ?? a.cap_strike ?? det.floor_strike ?? det.cap_strike
  const modelSideProb = isNo ? 1 - (a.model_prob || 0) : (a.model_prob || 0)

  return (
    <div className={`trade-row tone-${tone}${isTop ? ' trade-row-top' : ''}${alreadyPapered ? ' paper-book-row' : ''}`}>
      {alreadyPapered && <div className="paper-book-stamp">In Paper Book</div>}

      {/* ── Hero: What trade + big action button ── */}
      <div className="alert-hero">
        <div className="alert-hero-left">
          <div className="alert-hero-dir" style={{ background: a.direction === 'no' ? 'var(--red-dim)' : 'var(--green-dim)', color: dirColor(a.direction) }}>
            {direction}
          </div>
          <div className="alert-hero-info">
            <div className="alert-hero-title">
              {city && <strong>{city}</strong>}
              {city && ' — '}
              {option || question}
            </div>
            <div className="alert-hero-meta">
              {forecastValue != null && threshold != null && (
                <span>Forecast {Number(forecastValue).toFixed(0)}{marketType.includes('Rain') || marketType.includes('Precip') ? '%' : '°F'} vs {threshold}{marketType.includes('Rain') || marketType.includes('Precip') ? ' in.' : '°F'}</span>
              )}
              {closeH != null && <span>{Math.max(0, closeH).toFixed(1)}h left</span>}
              <span>{currentTemp(a)}</span>
            </div>
          </div>
        </div>

        <div className="alert-hero-right">
          <div className="alert-hero-stats">
            <div className="alert-hero-stat">
              <span className="alert-stat-val" style={{ color: edge >= 0.08 ? 'var(--green)' : edge > 0 ? 'var(--amber)' : 'var(--red)' }}>{fmtPP(edge)}</span>
              <span className="alert-stat-lbl">edge</span>
            </div>
            <div className="alert-hero-stat">
              <span className="alert-stat-val">{fmtEntryCents(entry)}</span>
              <span className="alert-stat-lbl">price</span>
            </div>
            <div className="alert-hero-stat">
              <span className="alert-stat-val">{fmtPct(modelSideProb, 0)}</span>
              <span className="alert-stat-lbl">model</span>
            </div>
            {ev100 != null && (
              <div className="alert-hero-stat">
                <span className="alert-stat-val" style={{ color: ev100 >= 0 ? 'var(--green)' : 'var(--red)' }}>{ev100 >= 0 ? '+' : ''}{ev100.toFixed(1)}¢</span>
                <span className="alert-stat-lbl">EV</span>
              </div>
            )}
          </div>

          {!alreadyPapered && action.enabled && (
            <button
              className={`btn alert-execute-btn btn-side-${a.direction === 'no' ? 'no' : 'yes'}`}
              onClick={() => onPaperTrade(a.id, action.contracts, action.manual)}
            >
              {action.label} · {action.contracts || contracts || 1} @ {fmtEntryCents(limitPrice)}
            </button>
          )}
          {!alreadyPapered && !action.enabled && a.status === 'pending' && (
            <div className="alert-blocked-reason">
              {humanMsg((rec.blockers || [rec.reason || 'Risk gates blocked'])[0])}
            </div>
          )}
          {alreadyPapered && paperTrade && (
            <div className="alert-tracking-badge">
              Tracking · {fmtEntryCents(a.direction === 'no' ? 1 - Number(paperTrade.entry_price) : Number(paperTrade.entry_price))} entry
            </div>
          )}
        </div>
      </div>

      {/* ── Why: Quick reasoning visible by default ── */}
      <QuickReason alert={a} />

      {/* ── Tags row ── */}
      <div className="trade-badges">
        <span className={`badge ${TIER_BADGE[tier] || 'badge-muted'}`}>{tierLabel(tier, rec)}</span>
        {historyRate != null && <span className="badge badge-muted">{Math.round(historyRate * 100)}% good entries</span>}
        {eventBlocked && <span className="badge badge-amber">Event already open</span>}
        {['medium', 'high'].includes(phantom) && (
          <span className={`badge ${PHANTOM_BADGE[phantom] || 'badge-muted'}`}>Forecast risk: {phantom}</span>
        )}
        {a.status === 'skipped' && <span className="badge badge-muted">Dismissed</span>}
        {alreadyPapered && <span className="badge badge-blue">Paper book</span>}
      </div>

      {/* ── Expandable details ── */}
      <AIAnalysis a={a} />

      {/* ── Footer actions ── */}
      <div className="trade-footer">
        {!['skipped', 'paper_traded'].includes(a.status) && (
          <button className="btn btn-ghost btn-sm" onClick={() => onSkip(a.id)} style={{ color: 'var(--text-2)' }}>Dismiss</button>
        )}
        <a
          className="btn btn-ghost btn-sm"
          href={a.kalshi_url || det.kalshi_url || '#'}
          target="_blank"
          rel="noreferrer"
          style={!(a.kalshi_url || det.kalshi_url) ? { pointerEvents: 'none', opacity: 0.4 } : {}}
        >
          View on Kalshi
        </a>
      </div>
    </div>
  )
}

function BestTradePanel({ alert }) {
  if (!alert) return null
  const rec = recommendation(alert)
  const edge = opportunityEdge(alert)
  const tier = tradeTier(alert, rec, alert.details?.brain || {})
  const { city } = humanizeMarketParts(alert.market_ticker, alert.market_title || alert.details?.market_title)
  const title = marketQuestion(alert)
  const isNo = alert.direction === 'no'
  const modelSideProb = isNo ? 1 - (alert.model_prob || 0) : (alert.model_prob || 0)
  const entry = sidePrice(alert)
  const ev100 = rec.expected_value_per_contract != null ? rec.expected_value_per_contract * 100 : null
  const eventBlocked = alert.event_has_open_trade || alert.details?.event_has_open_trade
  const action = paperActionInfo(alert, 'alerts')
  const sizeText = action.enabled ? action.sub : 'wait'
  return (
    <div className={`best-pick tier-${tier}`}>
      <div className="best-pick-tag">{eventBlocked ? 'Best tracked paper signal' : 'Top paper candidate'}</div>
      <div className="best-pick-main">
        <span className={`badge ${TIER_BADGE[tier] || 'badge-muted'}`}>{tierLabel(tier, rec)}</span>
        <span className="best-pick-ticker" style={{ fontSize: '0.95rem', fontWeight: 700, color: 'var(--text)' }}>{title}</span>
        <span className="best-pick-edge" style={{ color: edge >= 0.1 ? 'var(--green)' : edge >= 0.04 ? 'var(--amber)' : 'var(--red)' }}>
          {fmtPP(edge)} value
        </span>
      </div>
      <div className="best-pick-body">
        <strong style={{ color: dirColor(alert.direction) }}>{(alert.direction || 'yes').toUpperCase()}</strong> at {fmtEntryCents(rec.limit_price_side ?? entry)} · model {fmtPct(modelSideProb, 0)} vs market {fmtEntryCents(entry)} · {sizeText}
      </div>
      <div className="best-pick-why">
        {tierVerdict(tier, rec, alert)}
        {ev100 != null && <span style={{ color: ev100 >= 0 ? 'var(--green)' : 'var(--red)', fontFamily: 'var(--font-mono)', fontSize: '0.72rem' }}> · EV {ev100 >= 0 ? '+' : ''}{ev100.toFixed(1)}¢</span>}
      </div>
      <div className="best-pick-sources">
        {(rec.drivers || [])
          .filter(d => !/^expected\b/i.test(String(d || '')))
          .slice(0, 4)
          .map((d, i) => <span className="source-tag" key={i}>{humanMsg(d)}</span>)}
      </div>
    </div>
  )
}

/* ─── Active Trades Mini-Panel ────────────────────────────────────── */
function ActiveTradesBar() {
  const [trades, setTrades] = useState([])
  useEffect(() => {
    const load = () => fetch('/api/trades?status=open&limit=80').then(r => r.json())
      .then(d => setTrades(d.trades || [])).catch(() => {})
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [])
  if (!trades.length) return null
  const totalPnl = trades.reduce((sum, t) => {
    if (t.unrealized_pnl != null) return sum + Number(t.unrealized_pnl || 0)
    const cur = t.current_price ?? t.entry_price ?? 0
    return sum + (cur - (t.entry_price || 0)) * (t.contracts || 1)
  }, 0)
  return (
    <div className="active-trades-bar">
      <div className="active-trades-bar-left">
        <span className="signal-dot" style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--amber)', boxShadow: '0 0 8px var(--amber)', display: 'inline-block', marginRight: 6 }} />
        <strong>{trades.length} paper position{trades.length !== 1 ? 's' : ''} open</strong>
      </div>
      <div className="active-trades-bar-right">
        <div className="active-trade-marquee">
          <div className="active-trade-track">
        {trades.map(t => {
          const { city, rest } = humanizeMarketParts(t.market_ticker, t.market_title)
          const label = city || rest || 'Weather market'
          const pnl = Number(t.unrealized_pnl || 0)
          return (
            <span key={t.id} className="active-trade-chip">
              <span style={{ color: t.direction === 'yes' ? 'var(--green)' : 'var(--red)' }}>{(t.direction||'yes').toUpperCase()}</span>
              {' '}{label}
              <span style={{ color: 'var(--text-muted)' }}> @ {fmtPct(t.current_price ?? (t.direction === 'no' ? 1 - Number(t.entry_price || 0) : Number(t.entry_price || 0)), 0)}</span>
              <span style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}</span>
            </span>
          )
        })}
          </div>
        </div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.79rem', color: totalPnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
          {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)} unrealized
        </span>
      </div>
    </div>
  )
}

/* ─── Main Alerts Page ────────────────────────────────────────────── */
export default function Alerts() {
  const [alerts, setAlerts]     = useState([])
  const [dismissed, setDismissed] = useState(new Set()) // optimistic hide after paper trade
  const [brain, setBrain]       = useState(null)
  const [filter, setFilter]     = useState('active')
  const [sort, setSort]         = useState('quality')
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams({ limit: '50' })
    if (filter) params.set('status', filter)
    Promise.all([
      fetch(`/api/alerts?${params.toString()}`).then(r => r.json()),
      fetch('/api/brain/status').then(r => r.json()).catch(() => null),
    ])
      .then(([alertData, brainData]) => {
        setAlerts(alertData.alerts || [])
        if (brainData) setBrain(brainData)
      })
      .catch(err => {
        setError(err?.message || 'Unable to load alerts')
      })
      .finally(() => setLoading(false))
  }, [filter])

  useEffect(() => {
    setDismissed(new Set())
  }, [filter])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  const expire = id => fetch(`/api/alerts/${id}/deny`, { method: 'POST' }).then(load)
  const skip = id => {
    setDismissed(s => new Set([...s, id]))
    fetch(`/api/alerts/${id}/skip`, { method: 'POST' })
  }
  const paperTrade = (id, contracts = 1, learningOverride = false) => {
    // Optimistically remove the alert from view immediately
    setDismissed(s => new Set([...s, id]))
    fetch(`/api/alerts/${id}/paper-trade`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contracts, learning_override: learningOverride }),
    })
      .then(async r => {
        if (!r.ok) {
          const data = await r.json().catch(() => ({}))
          // Re-show it if trade failed
          setDismissed(s => { const n = new Set(s); n.delete(id); return n })
          window.alert(data.detail || 'Paper trade was rejected')
        } else {
          // Refresh after a brief delay so trade log updates
          setTimeout(load, 1500)
        }
      })
  }

  const sorted = [...alerts]
    .filter(a => !dismissed.has(a.id))
    .sort((a, b) => {
      const aPhantom = (a.phantom_risk_level || 'none') === 'high'
      const bPhantom = (b.phantom_risk_level || 'none') === 'high'
      const aActionable = a.status === 'pending' && (recommendation(a).contracts || 0) > 0 && !a.event_has_open_trade && !aPhantom
      const bActionable = b.status === 'pending' && (recommendation(b).contracts || 0) > 0 && !b.event_has_open_trade && !bPhantom
      if (aActionable && !bActionable) return -1
      if (!aActionable && bActionable) return 1
      if (sort === 'quality') return qualityScore(b) - qualityScore(a)
      if (sort === 'edge')    return (opportunityEdge(b) || 0) - (opportunityEdge(a) || 0)
      if (sort === 'score')   return (b.brain_score || 0) - (a.brain_score || 0)
      if (sort === 'risk')    return (a.phantom_risk_score || 0) - (b.phantom_risk_score || 0)
      if (sort === 'size')    return (recommendation(b).contracts || 0) - (recommendation(a).contracts || 0)
      if (sort === 'close')   return (closeHours(a) ?? 999) - (closeHours(b) ?? 999)
      return new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at)
    })

  // Active filter: dedupe by event, hide already-tracked unless explicitly looking for them
  const visibleAlerts = filter === 'active'
    ? sorted.filter((alert, index, arr) => {
        if (alert.event_has_open_trade && alert.status !== 'pending') return false
        return arr.findIndex(a => eventKey(a.market_ticker) === eventKey(alert.market_ticker)) === index
      })
    : filter === 'pending'
    ? sorted.filter((a, idx, arr) => arr.findIndex(x => eventKey(x.market_ticker) === eventKey(a.market_ticker)) === idx)
    : sorted

  const topPick  =
    visibleAlerts.find(a => a.status === 'pending' && (recommendation(a).contracts || 0) > 0 && !a.event_has_open_trade && alertTone(a) === 'good' && (a.phantom_risk_level || 'none') !== 'high') ||
    visibleAlerts.find(a => a.status === 'pending' && (recommendation(a).contracts || 0) > 0 && !a.event_has_open_trade && (a.phantom_risk_level || 'none') !== 'high') ||
    visibleAlerts.find(a => alertTone(a) === 'good' && (a.phantom_risk_level || 'none') !== 'high') ||
    visibleAlerts[0]
  const highRisk = alerts.filter(a => ['high', 'medium'].includes(a.phantom_risk_level)).length
  const avgEdge  = alerts.length
    ? alerts.reduce((sum, a) => sum + Math.max(0, opportunityEdge(a) || 0), 0) / alerts.length
    : 0

  return (
    <div className="alerts-page">
      <div className="page-hd">
        <div>
          <div className="page-title">Alerts</div>
          <div className="page-sub">
            {alerts.length} loaded · {highRisk} mismatch warnings · avg value {(avgEdge * 100).toFixed(1)}¢
          </div>
        </div>
        <div className="page-hd-actions">
          <div className="tabs">
            {[['active', 'Paper Candidates'], ['pending', 'New'], ['paper_traded', 'Open Paper'], ['skipped', 'Dismissed'], ['', 'All'], ['expired', 'Expired']].map(([v, lbl]) => (
              <button key={v} className={`tab${filter === v ? ' active' : ''}`} onClick={() => setFilter(v)}>
                {lbl}
              </button>
            ))}
          </div>
        </div>
      </div>

      <ActiveTradesBar />
      <BrainPanel status={brain} />
      <BestTradePanel alert={topPick} />

      <div className="alert-toolbar">
        <span className="sort-label">Sort</span>
        {[
          ['quality', 'Best Candidate'],
          ['risk',    'Lowest Risk'],
          ['close',   'Expiring Soon'],
          ['time',    'Freshest'],
        ].map(([v, lbl]) => (
          <button
            key={v}
            className="btn btn-ghost btn-sm"
            style={sort === v ? { background: 'var(--blue-dim)', color: 'var(--blue)', borderColor: 'var(--border-accent)' } : {}}
            onClick={() => setSort(v)}
          >
            {lbl}
          </button>
        ))}
      </div>

      {loading && alerts.length === 0 && (
        <div className="alert-list">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="trade-row tone-neutral">
              <div className="skeleton" style={{ height: 68 }} />
            </div>
          ))}
        </div>
      )}

      {error && !loading && (
        <div className="empty route-error">
          <strong>Alerts could not load.</strong>
          <span>{error}</span>
          <button className="btn btn-primary btn-sm" onClick={load}>Retry</button>
        </div>
      )}

      {!loading && !error && visibleAlerts.length === 0 && (
        <div className="empty">
          <svg width="40" height="40" viewBox="0 0 16 16" fill="currentColor" opacity="0.4">
            <path d="M8 1a5 5 0 00-5 5v2.8L1.6 10.3A.5.5 0 002 11h12a.5.5 0 00.4-.7L13 8.8V6a5 5 0 00-5-5z"/>
          </svg>
          No {filter || 'matching'} alerts found
        </div>
      )}

      <div className="alert-list">
        {visibleAlerts.map(a => (
          <AlertRow key={a.id} alert={a} isTop={topPick?.id === a.id} onExpire={expire} onPaperTrade={paperTrade} onSkip={skip} />
        ))}
      </div>
    </div>
  )
}
