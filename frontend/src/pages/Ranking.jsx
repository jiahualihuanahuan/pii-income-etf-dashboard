import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api.js'
import { pct, spct, cls, stanceChip, num } from '../format.js'
import FundDetail from '../components/FundDetail.jsx'

const WINDOWS = ['1m', '3m', '6m', '1y', '3y', 'inception']
const WLABEL = { '1m': '1M', '3m': '3M', '6m': '6M', '1y': '1Y', '3y': '3Y', inception: 'Since inc.' }
const trOf = (r, w) => (w === 'inception' ? r.metrics.tr_inception : r.metrics.tr?.[w])
const prOf = (r, w) => (w === 'inception' ? r.metrics.pr_inception : r.metrics.pr?.[w])

export default function Ranking({ status }) {
  const [cfg, setCfg] = useState(null) // editable controls
  const [saved, setSaved] = useState(null)
  const [data, setData] = useState(null)
  const [cats, setCats] = useState([])
  const [groups, setGroups] = useState([])
  const [filters, setFilters] = useState({ listing: 'ALL', group: '', subsector: '', q: '', stanceOnly: false })
  const [show, setShow] = useState('tr')
  const [sort, setSort] = useState({ key: 'rank', dir: 1 })
  const [sel, setSel] = useState(null)
  const [error, setError] = useState(null)
  const seq = useRef(0)

  useEffect(() => {
    api.settings().then((s) => { setSaved(s.settings); setCfg(s.settings) }).catch((e) => setError(String(e)))
    api.categories().then(setCats).catch(() => {})
    api.groups().then(setGroups).catch(() => {})
  }, [])

  useEffect(() => {
    if (!cfg) return
    const id = ++seq.current
    const t = setTimeout(() => {
      api.funds({ window: cfg.window, scope: cfg.scope, half_life_days: cfg.half_life_days,
        w_total_return: cfg.weights.total_return, w_stability: cfg.weights.stability, w_yield: cfg.weights.yield })
        .then((d) => { if (id === seq.current) { setData(d); setError(null) } })
        .catch((e) => setError(String(e)))
    }, 250)
    return () => clearTimeout(t)
  }, [cfg, status?.jobs?.[0]?.id])

  const setW = (k, v) => setCfg({ ...cfg, weights: { ...cfg.weights, [k]: v } })
  const sumW = cfg ? Object.values(cfg.weights).reduce((a, b) => a + b, 0) || 1 : 1
  const dirty = saved && cfg && JSON.stringify(saved) !== JSON.stringify(cfg)

  const rows = useMemo(() => {
    if (!data) return []
    const w = cfg.window
    const val = {
      rank: (r) => r.rank ?? 1e9, ticker: (r) => r.ticker, score: (r) => r.score ?? -1, yield: (r) => r.metrics.ttm_yield ?? -1,
      rel: (r) => r.vs_benchmark?.[w === 'inception' ? '3y' : w] ?? -9, stance: (r) => r.stance?.date ?? '',
      ...Object.fromEntries(WINDOWS.map((x) => [x, (r) => (show === 'tr' ? trOf(r, x) : prOf(r, x)) ?? -9])),
    }[sort.key] || ((r) => r.rank ?? 1e9)
    let out = data.rows.filter((r) =>
      (filters.listing === 'ALL' || r.listing === filters.listing) && (!filters.group || r.group === filters.group) && (!filters.subsector || r.subsector === filters.subsector) &&
      (!filters.stanceOnly || r.stance) && (!filters.q || (r.ticker + ' ' + (r.name || '')).toLowerCase().includes(filters.q.toLowerCase())))
    out = [...out].sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : 0) * sort.dir })
    return out
  }, [data, filters, sort, show, cfg])

  const head = (key, label, left) => (
    <th className={left ? 'l' : ''} onClick={() => setSort({ key, dir: sort.key === key ? -sort.dir : (key === 'ticker' || key === 'rank' ? 1 : -1) })}>
      {label}{sort.key === key ? (sort.dir === 1 ? ' ▲' : ' ▼') : ''}
    </th>)

  if (!cfg) return <div className="panel">{error ? `Error: ${error}` : 'Loading…'}</div>
  const catLabel = Object.fromEntries(cats.map((c) => [c.key, c.label]))
  const groupLabel = Object.fromEntries(groups.map((g) => [g.key, g.label]))
  const curGroup = groups.find((g) => g.key === filters.group)
  const relWindow = cfg.window === 'inception' ? '3y' : cfg.window

  return (
    <>
      {status && status.funds_with_prices === 0 && <div className="banner">No price data yet. The first download (full history for every fund) starts automatically on first launch and can take several minutes; this page fills in when it is done. Use “refresh prices” to retry.</div>}
      {error && <div className="banner">{error}</div>}
      <div className="panel">
        <h2>Score = weighted percentile of total return, stability and yield</h2>
        <div className="controls">
          {[['total_return', 'Total return'], ['stability', 'Stability'], ['yield', 'Yield (TTM)']].map(([k, l]) => (
            <div key={k}><label>{l}: weight {pct(cfg.weights[k] / sumW, 0)}</label>
              <input type="range" min="0" max="100" value={Math.round(cfg.weights[k] * 100)} onChange={(e) => setW(k, +e.target.value / 100)} /></div>))}
          <div><label>Total-return window</label>
            <select value={cfg.window} onChange={(e) => setCfg({ ...cfg, window: e.target.value })}>{WINDOWS.map((w) => <option key={w} value={w}>{WLABEL[w]}</option>)}</select></div>
          <div><label>Rank against</label>
            <select value={cfg.scope} onChange={(e) => setCfg({ ...cfg, scope: e.target.value })}><option value="all">All funds</option><option value="category">Own group</option></select></div>
          <div><label>Recency half-life (days)</label>
            <input type="number" min="7" max="3650" value={cfg.half_life_days} onChange={(e) => setCfg({ ...cfg, half_life_days: +e.target.value || 180 })} /></div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" disabled={!dirty} onClick={() => api.saveSettings({ weights: cfg.weights, window: cfg.window, scope: cfg.scope, half_life_days: cfg.half_life_days }).then((r) => { setSaved(r.settings); setCfg(r.settings) })}>Save as default</button>
            <button className="btn ghost" onClick={() => api.resetSettings().then((r) => { setSaved(r.settings); setCfg(r.settings) })}>Reset</button>
          </div>
        </div>
        <p className="muted small" style={{ marginBottom: 0 }}>
          Stability = cuts, payment variability (CV), volatility and max drawdown (1Y), each percentile-ranked, lower is better. Sliders apply instantly; “Save as default” writes them to the server override file. Defaults can also be set in <code>config/settings.yaml</code>.
        </p>
      </div>

      <div className="panel">
        <div className="controls" style={{ marginBottom: 10 }}>
          <div><label>Listing</label><select value={filters.listing} onChange={(e) => setFilters({ ...filters, listing: e.target.value })}><option value="ALL">CAD + USD</option><option value="CAD">CAD</option><option value="USD">USD</option></select></div>
          <div><label>Group</label><select value={filters.group} onChange={(e) => setFilters({ ...filters, group: e.target.value, subsector: '' })}><option value="">All groups</option>{groups.filter((g) => g.count).map((g) => <option key={g.key} value={g.key}>{g.label} ({g.count})</option>)}</select></div>
          {curGroup && Object.keys(curGroup.subsectors).length > 0 && <div><label>Subsector</label><select value={filters.subsector} onChange={(e) => setFilters({ ...filters, subsector: e.target.value })}><option value="">All {curGroup.label.toLowerCase()}</option>{Object.entries(curGroup.subsectors).sort().map(([k, n]) => <option key={k} value={k}>{k} ({n})</option>)}</select></div>}
          <div><label>Returns shown</label><select value={show} onChange={(e) => setShow(e.target.value)}><option value="tr">Total return (distributions reinvested)</option><option value="pr">Price return only</option></select></div>
          <div><label>Search</label><input type="text" value={filters.q} onChange={(e) => setFilters({ ...filters, q: e.target.value })} placeholder="ticker or name" /></div>
          <div><label><input type="checkbox" checked={filters.stanceOnly} onChange={(e) => setFilters({ ...filters, stanceOnly: e.target.checked })} /> Only funds with a stated opinion</label></div>
        </div>
        <div className="muted small" style={{ marginBottom: 6 }}>
          {rows.length} funds shown · data as of {data?.as_of || '–'} · {data?.n_with_data}/{data?.n_total} funds have price data · “vs bench” = total return minus benchmark over {relWindow}
        </div>
        <div className="tablewrap">
          <table>
            <thead><tr>
              {head('rank', '#')}{head('ticker', 'Fund', true)}<th className="l">Group</th>{head('score', 'Score')}
              {WINDOWS.map((w) => head(w, WLABEL[w]))}{head('yield', 'TTM yield')}<th>Distributions</th>{head('rel', 'vs bench')}{head('stance', 'His stance', true)}
            </tr></thead>
            <tbody>
              {rows.map((r) => {
                const m = r.metrics, st = r.stance, chip = st && stanceChip(st.label)
                return (
                  <tr key={r.ticker} className={`row ${sel === r.ticker ? 'sel' : ''}`} onClick={() => setSel(sel === r.ticker ? null : r.ticker)}>
                    <td>{r.rank ?? '–'}</td>
                    <td className="l name"><b>{r.ticker}</b> <span className="cur">{r.listing}</span> <span className="muted small">{r.name}</span></td>
                    <td className="l small" title={`Detailed category: ${catLabel[r.category] || r.category}`}>{groupLabel[r.group] || r.group}{r.subsector ? <span className="muted"> · {r.subsector}</span> : null}<div className="muted" style={{ fontSize: 11 }}>{catLabel[r.category] || r.category}</div></td>
                    <td title={r.components ? `Return ${r.components.total_return ?? '–'} · Stability ${r.components.stability ?? '–'} · Yield ${r.components.yield ?? '–'}${r.partial_score ? ' (partial: a component is missing, weights re-normalised)' : ''}` : ''}>
                      {r.stale ? <span className="muted small" title="Last price is much older than the rest: halted or delisted, left unranked">stale</span> : r.no_data ? <span className="muted small">no data</span> : r.score === null || r.score === undefined ? <span className="muted small" title="Too little history for this window (policy: exclude)">n/a</span> : <>{num(r.score, 0)}{r.partial_score ? '*' : ''}<span className="scorebar"><i style={{ width: `${r.score}%` }} /></span></>}
                    </td>
                    {WINDOWS.map((w) => { const v = show === 'tr' ? trOf(r, w) : prOf(r, w); return <td key={w} className={cls(v)}>{spct(v)}</td> })}
                    <td>{pct(m.ttm_yield)}{m.ttm_yield_partial ? '*' : ''}</td>
                    <td title={`${m.n_payments_12m} payments in 12M · ${m.cuts_12m} cut(s) · CV ${m.cv_12m === null ? '–' : (m.cv_12m * 100).toFixed(0) + '%'}`}>
                      {m.trend === 'up' ? <span className="pos">▲</span> : m.trend === 'down' ? <span className="neg">▼</span> : m.trend === 'flat' ? '■' : '–'}
                      {m.cuts_12m > 0 && <span className="neg"> {m.cuts_12m} cut{m.cuts_12m > 1 ? 's' : ''}</span>}
                    </td>
                    <td className={cls(r.vs_benchmark?.[relWindow])}>{spct(r.vs_benchmark?.[relWindow])}</td>
                    <td className="l">{st ? <><span className={`chip ${chip[1]}`}>{chip[0]}</span> <span className="muted small">{st.date}</span></> : <span className="muted small">–</span>}</td>
                  </tr>)
              })}
            </tbody>
          </table>
        </div>
        <p className="muted small">* = partial: yield annualised from &lt;1 year of history, or a score component missing because the fund is too new for the chosen window.</p>
      </div>
      {sel && <FundDetail ticker={sel} onClose={() => setSel(null)} catLabel={catLabel} groupLabel={groupLabel} />}
    </>
  )
}
