import React, { useEffect, useState } from 'react'
import Ranking from './pages/Ranking.jsx'
import Stances from './pages/Stances.jsx'
import Videos from './pages/Videos.jsx'
import Mindset from './pages/Mindset.jsx'
import { api } from './api.js'

const TABS = [['ranking', 'Ranking'], ['stances', 'His stance'], ['videos', 'Video library'], ['mindset', 'Mindset & checklist']]

export default function App() {
  const [tab, setTab] = useState(location.hash.slice(1) || 'ranking')
  const [status, setStatus] = useState(null)
  useEffect(() => { location.hash = tab }, [tab])
  useEffect(() => {
    const load = () => api.status().then(setStatus).catch(() => setStatus(null))
    load(); const id = setInterval(load, 30000); return () => clearInterval(id)
  }, [])
  const running = status?.jobs?.find((j) => j.status === 'running')
  return (
    <>
      <header className="top">
        <h1>Income ETF Dashboard</h1>
        <nav>{TABS.map(([k, l]) => <button key={k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>{l}</button>)}</nav>
        <span className="muted small" style={{ marginLeft: 'auto' }}>
          {status ? `${status.funds_with_prices}/${status.funds} funds with prices · ${status.videos} videos` : 'backend unreachable'}
          {running ? ` · refreshing (${running.name})…` : ''}
          {' '}<a href="#" onClick={(e) => { e.preventDefault(); api.runJob('prices') }}>refresh prices</a>
        </span>
      </header>
      <main>
        {tab === 'ranking' && <Ranking status={status} />}
        {tab === 'stances' && <Stances />}
        {tab === 'videos' && <Videos />}
        {tab === 'mindset' && <Mindset />}
        <p className="muted small">Educational use only, not financial advice. Prices from Yahoo Finance via yfinance (unofficial, may be delayed or wrong); opinions are paraphrased or quoted from the creator's public videos.</p>
      </main>
    </>
  )
}
