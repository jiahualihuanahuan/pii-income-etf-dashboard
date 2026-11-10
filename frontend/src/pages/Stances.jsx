import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { stanceChip, num } from '../format.js'

export default function Stances() {
  const [rows, setRows] = useState([])
  const [hl, setHl] = useState(180)
  const [q, setQ] = useState('')
  useEffect(() => { api.stances(hl).then(setRows).catch(() => setRows([])) }, [hl])
  const shown = rows.filter((r) => !q || (r.ticker + r.name + (r.stance.quote || '')).toLowerCase().includes(q.toLowerCase()))
  return (
    <div className="panel">
      <h2>His latest stance per fund (newest dated opinion wins)</h2>
      <div className="controls" style={{ marginBottom: 10 }}>
        <div><label>Recency half-life (days)</label><input type="number" min="7" value={hl} onChange={(e) => setHl(+e.target.value || 180)} /></div>
        <div><label>Search</label><input type="text" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      </div>
      <p className="muted small">Only opinions I could read in his videos are listed (see README for coverage). Quotes come from auto-generated captions and can contain spelling errors. Older opinions stay visible under “earlier opinions” in each fund's detail.</p>
      <div className="tablewrap"><table>
        <thead><tr><th className="l">Fund</th><th className="l">Stance</th><th>Date</th><th>Weight</th><th className="l">What he said</th><th className="l">Source</th></tr></thead>
        <tbody>{shown.map((r) => { const s = r.stance, c = stanceChip(s.label); return (
          <tr key={r.ticker}>
            <td className="l"><b>{r.ticker}</b> <span className="muted small">{r.name}</span></td>
            <td className="l"><span className={`chip ${c[1]}`}>{c[0]}</span></td>
            <td>{s.date}</td><td>{num(s.recency_weight, 2)}</td>
            <td className="l" style={{ whiteSpace: 'normal', minWidth: 380, maxWidth: 560 }}>{s.quote ? `“${s.quote}”` : s.summary}</td>
            <td className="l">{s.url && <a href={s.url} target="_blank" rel="noreferrer">{s.video_title || 'video'}{s.t ? ` @ ${s.t}` : ''} ↗</a>}</td>
          </tr>) })}</tbody></table></div>
    </div>
  )
}
