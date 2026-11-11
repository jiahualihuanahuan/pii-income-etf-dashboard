import React, { useEffect, useState } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api.js'
import { pct, spct, ytLink, stanceChip, num } from '../format.js'
import Holdings from './Holdings.jsx'

export default function FundDetail({ ticker, onClose, catLabel, groupLabel = {} }) {
  const [d, setD] = useState(null)
  const [dist, setDist] = useState([])
  const [hist, setHist] = useState([])
  const [years, setYears] = useState(3)
  useEffect(() => {
    setD(null)
    api.fund(ticker).then(setD).catch(() => {})
    api.distributions(ticker, 5).then((r) => setDist(r.items)).catch(() => setDist([]))
  }, [ticker])
  useEffect(() => { api.history(ticker, years).then((r) => setHist(r.items)).catch(() => setHist([])) }, [ticker, years])
  if (!d) return <div className="panel">Loading {ticker}…</div>
  const m = d.metrics, st = d.stance
  const thin = (arr, n = 400) => (arr.length > n ? arr.filter((_, i) => i % Math.ceil(arr.length / n) === 0 || i === arr.length - 1) : arr)
  return (
    <div className="panel">
      <div style={{ display: 'flex', justifyContent: 'space-between' }}>
        <h2>{d.ticker} · {d.name} <span className="cur">{d.listing}</span> <span className="muted small">{groupLabel[d.group] || d.group}{d.subsector ? ` · ${d.subsector}` : ''} ({catLabel[d.category] || d.category}){d.benchmark ? ` · benchmark ${d.benchmark}` : ''}{d.leverage ? ` · leverage ~${d.leverage}x` : ''}</span></h2>
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
      {d.notes && <p className="muted small">{d.notes}</p>}
      <div className="grid2">
        <div>
          <h3>Total return vs price (indexed to 100)</h3>
          <select value={years} onChange={(e) => setYears(+e.target.value)}><option value={1}>1Y</option><option value={3}>3Y</option><option value={5}>5Y</option><option value={40}>Max</option></select>
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={thin(hist)}><CartesianGrid strokeOpacity={0.15} /><XAxis dataKey="d" minTickGap={50} tick={{ fontSize: 11 }} /><YAxis domain={['auto', 'auto']} tick={{ fontSize: 11 }} /><Tooltip formatter={(v) => num(v, 1)} /><Legend />
              <Line dataKey="total_return" name="Total return" dot={false} stroke="#3ecf8e" strokeWidth={2} /><Line dataKey="price" name="Price only" dot={false} stroke="#8a97a8" /></LineChart>
          </ResponsiveContainer>
        </div>
        <div>
          <h3>Distribution history (per payment; red = cut &gt; threshold)</h3>
          <ResponsiveContainer width="100%" height={270}>
            <BarChart data={dist}><CartesianGrid strokeOpacity={0.15} /><XAxis dataKey="d" minTickGap={40} tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} /><Tooltip formatter={(v, n, p) => [num(v, 4) + (p.payload.change_pct !== null ? ` (${spct(p.payload.change_pct)})` : ''), 'Payment']} />
              <Bar dataKey="amount">{dist.map((x, i) => <Cell key={i} fill={x.cut ? '#ff6b6b' : '#4ea1ff'} />)}</Bar></BarChart>
          </ResponsiveContainer>
          {dist.length === 0 && <p className="muted">No distributions on record.</p>}
        </div>
      </div>
      <Holdings ticker={d.ticker} />
      <div className="grid2">
        <div>
          <h3>Numbers</h3>
          <table><tbody>
            <tr><td className="l">TTM yield</td><td>{pct(m.ttm_yield)}{m.ttm_yield_partial ? ' (annualised, <1y history)' : ''}</td></tr>
            <tr><td className="l">Payments in 12M / frequency</td><td>{m.n_payments_12m} / {m.frequency || '–'}</td></tr>
            <tr><td className="l">Cuts in 12M (severity)</td><td>{m.cuts_12m} ({pct(m.cut_severity_12m, 0)})</td></tr>
            <tr><td className="l">Payment variability (CV)</td><td>{pct(m.cv_12m, 0)}</td></tr>
            <tr><td className="l">Distribution trend (last 3 vs prior 3 periods)</td><td>{spct(m.trend_pct)}</td></tr>
            <tr><td className="l">Volatility / max drawdown (1Y)</td><td>{pct(m.vol_1y)} / {pct(m.maxdd_1y)}</td></tr>
            <tr><td className="l">CAGR since inception ({m.inception_date})</td><td>{spct(m.cagr_inception)}</td></tr>
            <tr><td className="l">Score components (return / stability / yield)</td><td>{d.components ? `${d.components.total_return ?? '–'} / ${d.components.stability ?? '–'} / ${d.components.yield ?? '–'}` : '–'}</td></tr>
          </tbody></table>
        </div>
        <div>
          <h3>His stance</h3>
          {st ? (
            <>
              <span className={`chip ${stanceChip(st.label)[1]}`}>{stanceChip(st.label)[0]}</span> <span className="muted small">{st.date} · {st.age_days} days ago · recency weight {num(st.recency_weight, 2)}</span>
              {st.quote && <blockquote>“{st.quote}”</blockquote>}
              {st.summary && <p>{st.summary}</p>}
              {st.url && <a href={st.url} target="_blank" rel="noreferrer">{st.video_title || 'Video'}{st.t ? ` @ ${st.t}` : ''} ↗</a>}
              {st.history?.length > 0 && <details><summary className="muted small">Earlier opinions ({st.history.length}, superseded by the newest)</summary>
                {st.history.map((h, i) => <div key={i}><span className={`chip ${stanceChip(h.label)[1]}`}>{stanceChip(h.label)[0]}</span> <span className="muted small">{h.date}</span> {h.quote && <blockquote>“{h.quote}”</blockquote>} {h.url && <a href={h.url} target="_blank" rel="noreferrer">video ↗</a>}</div>)}</details>}
            </>) : <p className="muted">No stated opinion recorded.</p>}
          {d.mentions?.length > 0 && <>
            <h3 style={{ marginTop: 12 }}>Latest mentions in videos</h3>
            {d.mentions.slice(0, 3).map((x, i) => <div key={i} className="small"><span className="muted">{x.date}</span> <a href={x.url} target="_blank" rel="noreferrer">{x.title || x.video_id} ↗</a><div className="muted">“{x.snippet}”</div></div>)}
          </>}
        </div>
      </div>
    </div>
  )
}
