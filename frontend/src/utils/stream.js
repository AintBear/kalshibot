// Shared SSE connection to /api/stream. One EventSource for the whole app;
// components subscribe per event type and re-render on push instead of polling.
import { useEffect, useState } from 'react'

const subscribers = { quote: new Set(), pulse: new Set(), narration: new Set() }
const latest = { pulse: null }
let source = null
let retryMs = 2000

function ensureSource() {
  if (source) return
  source = new EventSource('/api/stream')
  for (const type of Object.keys(subscribers)) {
    source.addEventListener(type, (e) => {
      let data
      try { data = JSON.parse(e.data) } catch { return }
      if (type === 'pulse') latest.pulse = data
      for (const fn of subscribers[type]) {
        try { fn(data) } catch { /* subscriber errors must not kill the stream */ }
      }
    })
  }
  source.onopen = () => { retryMs = 2000 }
  source.onerror = () => {
    source.close()
    source = null
    setTimeout(ensureSource, retryMs)
    retryMs = Math.min(30000, retryMs * 2)
  }
}

export function subscribe(type, fn) {
  ensureSource()
  subscribers[type].add(fn)
  return () => subscribers[type].delete(fn)
}

// React hook: latest pulse (positions P&L, scan stage, kill switch, feed health).
export function usePulse() {
  const [pulse, setPulse] = useState(latest.pulse)
  useEffect(() => subscribe('pulse', setPulse), [])
  return pulse
}

// React hook: rolling narration feed (newest first, capped).
export function useNarration(cap = 80) {
  const [lines, setLines] = useState([])
  useEffect(() => subscribe('narration', (line) =>
    setLines(prev => [line, ...prev].slice(0, cap))
  ), [cap])
  return lines
}

// React hook: live quotes keyed by ticker.
export function useQuotes() {
  const [quotes, setQuotes] = useState({})
  useEffect(() => subscribe('quote', (q) =>
    setQuotes(prev => ({ ...prev, [q.ticker]: q }))
  ), [])
  return quotes
}
