export const fmtPct = (value, digits = 1) => (
  value == null || Number.isNaN(Number(value)) ? '—' : `${(Number(value) * 100).toFixed(digits)}%`
)

export const fmtCents = value => (
  value == null || Number.isNaN(Number(value)) ? '—' : `${Number(value) >= 0 ? '+' : ''}${(Number(value) * 100).toFixed(1)}¢`
)

// fmtEdge is the same as fmtCents — kept as alias for semantic clarity in edge-formatting contexts
export const fmtEdge = fmtCents

export const fmtDollar = value => (
  value == null || Number.isNaN(Number(value))
    ? '—'
    : `${Number(value) >= 0 ? '+$' : '-$'}${Math.abs(Number(value)).toFixed(2)}`
)

export const fmtMoney = value => {
  if (value == null || Number.isNaN(Number(value))) return '—'
  const dollars = Number(value)
  return `${dollars >= 0 ? '+$' : '-$'}${Math.abs(dollars).toFixed(2)}`
}

export const parseApiTime = value => {
  if (!value) return null
  let normalized = String(value).trim()
  if (!normalized) return null
  normalized = normalized.includes('T') ? normalized : normalized.replace(' ', 'T')
  if (!/[zZ]$/.test(normalized) && !/[+-]\d{2}:?\d{2}$/.test(normalized)) {
    normalized += 'Z'
  }
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export const cleanTitle = value => (
  String(value || '')
    .replace(/\*\*/g, '')
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
)

export const stateLabel = value => {
  const map = {
    paper_ready: 'Paper ready',
    watch: 'Wait',
    caution: 'Learning',
    skip: 'No trade',
    complete: 'Complete',
    running: 'Running',
    failed: 'Failed',
    never: 'Never run',
    paper_traded: 'Paper traded',
    tiny_payout: 'Tiny payout',
    small_payout: 'Small payout',
    extreme_quote: 'Extreme quote',
    segment_negative_clv: 'Similar trades weak',
    recent_clv_negative: 'Recent entries weak',
    low_positive_clv_rate: 'Low hit quality',
    positive_clv_rate_below_live_gate: 'Entry quality too low',
    segment_paper_pnl_negative: 'Similar trades losing',
    segment_positive_clv: 'Similar trades improving',
    recent_clv_positive: 'Recent entries improved',
    phantom_risk_low: 'Low forecast disagreement',
  }
  return map[value] || String(value || '—').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase())
}

const SEGMENT_TYPE_LABELS = {
  'weather_all': 'All Weather Markets',
  'high_bracket': 'High Temp Markets',
  'low_bracket': 'Low Temp Markets',
  'precipitation': 'Precipitation Markets',
  'rain': 'Rain Markets',
  'snow': 'Snow Markets',
  'high': 'High Temp',
  'low': 'Low Temp',
}
const SEGMENT_TIMING_LABELS = {
  'all': '',
  'same_day': '— Same-Day Entries',
  'next_day': '— Next-Day Entries',
  'week_out': '— Week-Out Entries',
}

export const segmentLabel = value => {
  if (!value) return '—'
  const [type, timing] = String(value).split(':')
  const typeLabel = SEGMENT_TYPE_LABELS[type] || stateLabel(type)
  const timingLabel = timing ? (SEGMENT_TIMING_LABELS[timing] ?? `— ${stateLabel(timing)}`) : ''
  return timingLabel ? `${typeLabel} ${timingLabel}` : typeLabel
}

export const sidePrice = item => {
  if (item?.direction === 'no') {
    const noAsk = item?.no_ask ?? item?.details?.no_ask
    if (noAsk != null && !Number.isNaN(Number(noAsk))) return Number(noAsk)
  } else {
    const yesAsk = item?.yes_ask ?? item?.details?.yes_ask
    if (yesAsk != null && !Number.isNaN(Number(yesAsk))) return Number(yesAsk)
  }
  if (item?.market_price == null) return null
  return item.direction === 'no' ? 1 - Number(item.market_price) : Number(item.market_price)
}

export const tradeEntryPrice = trade => {
  if (trade?.entry_price == null) return null
  return trade.direction === 'no' ? 1 - Number(trade.entry_price) : Number(trade.entry_price)
}

export const opportunityEdge = item => {
  const rec = recommendation(item)
  if (rec?.side_edge != null && !Number.isNaN(Number(rec.side_edge))) return Number(rec.side_edge)
  if (item?.edge == null) return null
  return item.direction === 'no' ? -Number(item.edge) : Number(item.edge)
}

export const ageMinutes = item => {
  const ts = item?.updated_at || item?.created_at
  if (!ts) return null
  const normalized = String(ts).includes('T') ? ts : `${ts.replace(' ', 'T')}Z`
  const diff = Date.now() - new Date(normalized).getTime()
  return Number.isFinite(diff) ? Math.max(0, Math.round(diff / 60000)) : null
}

export const closeHours = item => {
  if (!item?.close_time) return null
  const diff = new Date(item.close_time).getTime() - Date.now()
  return Number.isFinite(diff) ? diff / 3600000 : null
}

export const eventKey = ticker => {
  const parts = String(ticker || '').split('-')
  parts.pop()
  return parts.join('-')
}

export const qualityScore = item => {
  const edge = Math.max(0, opportunityEdge(item) || 0) * 100
  const brain = Number(item?.brain_score || 0)
  const risk = Number(item?.phantom_risk_score || 0)
  const phantomLevel = item?.phantom_risk_level || item?.details?.phantom_risk_level || 'none'
  const rec = item?.recommendation || item?.details?.recommendation || {}
  const contracts = Number(rec.contracts || 0)
  const ev = Math.max(0, Number(rec.expected_value_per_contract || 0) * 100)
  const agePenalty = Math.min(25, (ageMinutes(item) || 0) / 6)
  const close = closeHours(item)
  const closeBonus = close != null && close > 0 && close <= 36 ? 8 : 0
  const tooLatePenalty = close != null && close <= 0 ? 100 : 0
  const actionBonus = (rec.action === 'paper' || rec.action === 'learn') ? 20 : rec.action === 'watch' ? 8 : -25
  const phantomPenalty = phantomLevel === 'high' ? 200 : phantomLevel === 'medium' ? 60 : risk * 1.2
  const noContractsPenalty = contracts <= 0 ? 80 : 0
  return brain + edge + ev + contracts * 3 + closeBonus + actionBonus - phantomPenalty - noContractsPenalty - agePenalty - tooLatePenalty
}

export const isActionable = item => {
  const rec = recommendation(item)
  const contracts = Number(rec.contracts || 0)
  const phantomLevel = item?.phantom_risk_level || item?.details?.phantom_risk_level || 'none'
  const edge = opportunityEdge(item) || 0
  if (contracts <= 0) return false
  if (phantomLevel === 'high') return false
  if (edge <= 0) return false
  return true
}

export const recommendation = item => item?.recommendation || item?.details?.recommendation || {}

export const trustLabel = score => {
  const n = Number(score || 0)
  if (n >= 82) return 'High trust'
  if (n >= 60) return 'Paper learning'
  if (n >= 40) return 'Training'
  return 'Not ready'
}

export const humanBlocker = value => {
  const msg = String(value || '').replaceAll('_', ' ')
  if (!msg) return 'Waiting for a cleaner setup.'
  if (/brain not ready|trust score too low/i.test(msg)) return 'Trust score is too low.'
  if (/segment is not policy-ready|similar trades have not earned auto sizing/i.test(msg)) return 'Similar trades have not earned auto sizing.'
  if (/similar trades only/i.test(msg)) return msg.replace('positive CLV', 'good entries') + '.'
  if (/recent segment CLV/i.test(msg)) return msg.replace('recent segment CLV', 'Recent similar-trade entry move') + '.'
  if (/similar paper P&L is negative/i.test(msg)) return 'Similar paper trades are losing money.'
  if (/event already has an open paper trade/i.test(msg)) return 'Already paper trading this event.'
  if (/high forecast disagreement risk/i.test(msg)) return 'Weather sources disagree too much.'
  if (/wide bid\/ask spread/i.test(msg)) return 'Spread is too wide.'
  if (/tiny remaining payout/i.test(msg)) return 'Payout is too small.'
  if (/positive edge,\s*positive expected value,\s*and trust check passed/i.test(msg)) return 'Model value is positive and trust check passed.'
  if (/no positive edge/i.test(msg)) return 'No model value at this price.'
  if (/negative expected value/i.test(msg)) return 'Bad expected value.'
  return cleanTitle(msg).replace(/\bedge\b/gi, 'model value').replace(/\bexpected value\b/gi, 'model value')
}

export const paperActionInfo = (item, context = 'paper') => {
  const rec = recommendation(item)
  const direction = String(item?.direction || 'yes').toUpperCase()
  const contracts = Number(rec.contracts || 0)
  const blocked = item?.event_has_open_trade || item?.details?.event_has_open_trade
  const phantom = item?.phantom_risk_level || item?.details?.phantom_risk_level || 'none'
  const edge = Number(rec.side_edge ?? opportunityEdge(item) ?? 0)
  const ev = Number(rec.expected_value_per_contract ?? edge)
  const canPlanned = item?.status === 'pending' && contracts > 0 && !blocked
  const canManual = item?.status === 'pending' && !canPlanned && !blocked && phantom !== 'high' && edge > 0 && ev > 0
  const actionVerb = context === 'alerts' ? 'Open' : 'Paper'
  if (canPlanned) {
    return { enabled: true, manual: false, contracts, label: `${actionVerb} ${direction}`, sub: `${contracts} contract${contracts === 1 ? '' : 's'}` }
  }
  if (canManual) {
    return { enabled: true, manual: true, contracts: 1, label: `${actionVerb} ${direction}`, sub: '1 contract' }
  }
  return {
    enabled: false,
    manual: false,
    contracts: 0,
    label: 'Wait',
    sub: humanBlocker((rec.blockers || [rec.reason])[0]),
  }
}

export const currentTemp = item => {
  const c = item?.current_conditions || item?.details?.current_conditions || {}
  return c.temperature == null ? '—' : `${Number(c.temperature).toFixed(0)}°F`
}

const CITY_NAMES = {
  NYC: 'New York', CHI: 'Chicago', LAX: 'Los Angeles', MIA: 'Miami',
  DAL: 'Dallas', ATL: 'Atlanta', SEA: 'Seattle', DEN: 'Denver',
  BOS: 'Boston', PHX: 'Phoenix', SFO: 'San Francisco', HOU: 'Houston',
  PHIL: 'Philadelphia', MIN: 'Minneapolis', AUS: 'Austin',
  LV: 'Las Vegas', DC: 'Washington DC', OKC: 'Oklahoma City',
  NOLA: 'New Orleans', SATX: 'San Antonio',
}

export const humanizeMarketParts = (ticker, title) => {
  const t = String(ticker || '').toUpperCase()
  const series = t.split('-')[0]
  let type = ''
  if (series.includes('HIGH'))   type = 'High Temp'
  else if (series.includes('LOW'))    type = 'Low Temp'
  else if (series.includes('RAIN'))   type = 'Rain'
  else if (series.includes('SNOW'))   type = 'Snow'
  else if (series.includes('PRECIP')) type = 'Precipitation'
  let city = ''
  for (const [code, name] of Object.entries(CITY_NAMES)) {
    if (series.includes(code)) { city = name; break }
  }
  if (title && !title.includes('KXHIGH') && !title.includes('KXLOW') && !title.includes('KXRAIN') && !title.includes('KXSNOW')) {
    return { city: city || null, rest: cleanTitle(title) }
  }
  const dateMatch = t.match(/-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})/)
  let date = ''
  if (dateMatch) {
    const months = { JAN:'Jan',FEB:'Feb',MAR:'Mar',APR:'Apr',MAY:'May',JUN:'Jun',JUL:'Jul',AUG:'Aug',SEP:'Sep',OCT:'Oct',NOV:'Nov',DEC:'Dec' }
    date = `${months[dateMatch[2]]} ${parseInt(dateMatch[3], 10)}`
  }
  const threshMatch = t.match(/[-]T(\d+)$/)
  const betweenMatch = t.match(/[-]B(\d+)$/)
  let threshold = ''
  if (threshMatch)  threshold = `${threshMatch[1]}°F`
  else if (betweenMatch) threshold = `~${betweenMatch[1]}°F`
  const rest = [type, threshold, date].filter(Boolean).join(' · ')
  return { city: city || null, rest: rest || cleanTitle(title || 'Weather market') }
}


export const marketQuestion = (itemOrTicker, maybeTitle) => {
  const ticker = typeof itemOrTicker === 'string' ? itemOrTicker : itemOrTicker?.market_ticker
  const title = typeof itemOrTicker === 'string' ? maybeTitle : itemOrTicker?.market_title
  const { city, rest } = humanizeMarketParts(ticker, title)
  const t = String(ticker || '').toUpperCase()
  const dateMatch = t.match(/-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})/)
  const months = { JAN:'Jan',FEB:'Feb',MAR:'Mar',APR:'Apr',MAY:'May',JUN:'Jun',JUL:'Jul',AUG:'Aug',SEP:'Sep',OCT:'Oct',NOV:'Nov',DEC:'Dec' }
  const date = dateMatch ? `${months[dateMatch[2]]} ${parseInt(dateMatch[3], 10)}` : ''
  const series = t.split('-')[0]
  if (series.includes('LOW')) return `Lowest temperature in ${city || 'this city'}${date ? ` on ${date}` : ''}`
  if (series.includes('HIGH')) return `Highest temperature in ${city || 'this city'}${date ? ` on ${date}` : ''}`
  if (series.includes('RAIN')) {
    const month = dateMatch ? `${months[dateMatch[2]]} 20${dateMatch[1]}` : ''
    return `Total rain in ${city || 'this city'}${month ? ` in ${month}` : ''}`
  }
  if (series.includes('SNOW')) return `Total snow in ${city || 'this city'}${date ? ` on ${date}` : ''}`
  return [city, rest].filter(Boolean).join(' · ') || cleanTitle(title || 'Weather market')
}

export const kalshiUrl = (ticker) => {
  if (!ticker) return null
  const t = String(ticker).toUpperCase()
  const eventTicker = t.replace(/-[^-]+$/, '')
  const seriesTicker = eventTicker.replace(/-\d{2}[A-Z]{3}\d{2}.*/, '')
  return `https://kalshi.com/markets/${seriesTicker}/${eventTicker}`
}

export const optionLabel = item => {
  const label = item?.yes_sub_title || item?.details?.yes_sub_title || item?.no_sub_title || item?.details?.no_sub_title
  if (label) return cleanTitle(label)
  const { rest } = humanizeMarketParts(item?.market_ticker, item?.market_title || item?.details?.market_title)
  return rest || 'listed option'
}
