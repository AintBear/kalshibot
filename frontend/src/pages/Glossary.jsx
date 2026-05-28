import React from 'react'

function Card({ title, children }) {
  return (
    <section className="glossary-item">
      <div className="glossary-section-title">{title}</div>
      <ul className="glossary-list">{children}</ul>
    </section>
  )
}

function Dot({ color }) {
  return <span className="glossary-dot" style={{ background: color }} />
}

export default function Glossary() {
  return (
    <div className="glossary-page">
      <div className="page-hd">
        <div>
          <div className="page-title">Glossary</div>
          <div className="page-sub">Plain-English reference for the trading screens</div>
        </div>
      </div>

      <div className="glossary-grid">
        <Card title="Trade States">
          <li><strong>Paper YES/NO</strong> logs a simulated trade on the selected side.</li>
          <li><strong>Wait</strong> means the setup is visible, but trust or weather risk is not good enough.</li>
          <li><strong>Watch</strong> means the setup is close, but price, spread, or confidence needs to improve.</li>
          <li><strong>Avoid</strong> means the bot should not enter that alert.</li>
        </Card>

        <Card title="Bot Trust">
          <li>Trust uses sample count, average entry quality, recent entry quality, good-entry rate, paper P&amp;L, and market-type history.</li>
          <li>It stays low when paper P&amp;L is negative, recent entries are weak, or good-entry rate is below 50%.</li>
          <li>Live auto trading needs score 90+, positive paper P&amp;L, and entry quality passing.</li>
        </Card>

        <Card title="Key Numbers">
          <li><strong>Price</strong> is what the paper trade costs right now.</li>
          <li><strong>Model chance</strong> is the model’s chance that the selected YES or NO side wins.</li>
          <li><strong>Value</strong> is model chance minus market price, shown in cents per contract.</li>
          <li><strong>Trust</strong> is whether similar weather trades have actually worked.</li>
        </Card>

        <Card title="Trade Actions">
          <li><strong>Paper YES/NO</strong> opens a simulated position. While trust is low, manual paper trades stay at 1 contract.</li>
          <li><strong>Wait</strong> means the alert is useful for review but blocked from entry.</li>
          <li><strong>Dismiss</strong> hides a reviewed alert without deleting its record.</li>
          <li><strong>Pause Auto</strong> turns off scheduled or automatic entries from the scanner.</li>
        </Card>

        <Card title="Modes">
          <li><strong>Manual</strong> runs only the actions you click.</li>
          <li><strong>Auto scan only</strong> refreshes markets on a schedule, but you still approve every paper trade.</li>
          <li><strong>Paper bot</strong> can enter small qualifying paper trades after scans so the bot keeps collecting learning data.</li>
          <li><strong>Live auto</strong> uses the Kalshi account and is blocked unless the live gates pass.</li>
        </Card>

        <Card title="Sources">
          <li><strong>NOAA/NWS</strong> is the primary forecast and settlement reference for Kalshi weather markets.</li>
          <li><strong>Open-Meteo</strong> is blended with NOAA/NWS as a second forecast source; AccuWeather is optional when an API key is configured.</li>
          <li><strong>Forecast disagreement</strong> means sources are far enough apart to lower conviction.</li>
        </Card>
      </div>
    </div>
  )
}
