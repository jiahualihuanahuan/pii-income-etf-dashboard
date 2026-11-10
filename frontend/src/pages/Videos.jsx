import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { num } from '../format.js'

export default function Videos() {
  const [rows, setRows] = useState([])
  const [hl, setHl] = useState(180)
  const [mode, setMode] = useState('log')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(100)
  useEffect(() => { api.videos(hl, mode).then(setRows).catch(() => setRows([])) }, [hl, mode])
  const shown = rows.filter((v) => !q || (v.title + Object.keys(v.tickers).join(' ')).toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="panel">
      <h2>Video library, sorted by recency-weighted score</h2>
      <p className="muted small">score = transform(views) × 0.5^(age / half-life). Default transform is log10(1 + views), so one viral old video cannot bury recent ones; switch to linear to see raw popularity decayed by age. Undated (likely members-only) videos get a neutral weight of 0.25.</p>
      <div className="controls" style={{ marginBottom: 10 }}>
        <div><label>Half-life (days)</label><input type="number" min="7" value={hl} onChange={(e) => setHl(+e.target.value || 180)} /></div>
        <div><label>Views transform</label><select value={mode} onChange={(e) => setMode(e.target.value)}><option value="log">log10</option><option value="linear">linear</option><option value="none">ignore views (recency only)</option></select></div>
        <div><label>Search title / ticker</label><input type="text" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      <div className="tablewrap"><table>
        <thead><tr><th>#</th><th className="l">Title</th><th>Date</th><th>Views</th><th>Weight</th><th>Score</th><th className="l">Funds mentioned</th></tr></thead>
        <tbody>{shown.slice(0, limit).map((v, i) => (
          <tr key={v.id}><td>{i + 1}</td>
            <td className="l" style={{ whiteSpace: 'normal', maxWidth: 560 }}><a href={v.url} target="_blank" rel="noreferrer">{v.title}</a></td>
            <td>{v.date || (v.est_date ? `~${v.est_date}` : '–')}</td><td>{v.views?.toLocaleString() ?? '–'}</td><td>{num(v.recency_weight, 2)}</td><td>{num(v.score, 2)}</td>
            <td className="l small muted" style={{ whiteSpace: 'normal' }}>{Object.entries(v.tickers).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([t]) => t.split('.')[0]).join(', ')}</td></tr>))}</tbody></table></div>
      {shown.length > limit && <button className="btn ghost" onClick={() => setLimit(limit + 200)}>Show more ({shown.length - limit} left)</button>}
    </div>
  )
}
