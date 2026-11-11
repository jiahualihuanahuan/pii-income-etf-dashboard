import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { num } from '../format.js'

const TOP = 25
const KIND = { fund: ['fund', 'warn'], cash: ['cash', ''], derivative: ['deriv.', ''], memo: ['memo', ''], undisclosed: ['undisclosed', 'warn'] }
const wfmt = (v) => (v === null || v === undefined ? '–' : `${v >= 0 ? '' : '−'}${Math.abs(v).toFixed(Math.abs(v) < 0.1 ? 3 : 2)}%`)

export default function Holdings({ ticker }) {
  const [look, setLook] = useState(true)
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [q, setQ] = useState('')
  const [all, setAll] = useState(false)
  useEffect(() => {
    setD(null); setErr(null); setAll(false); setQ('')
    api.holdings(ticker, look).then(setD).catch((e) => setErr(String(e)))
  }, [ticker, look])
  const rows = useMemo(() => {
    if (!d?.rows) return []
    const ql = q.trim().toLowerCase()
    return ql ? d.rows.filter((r) => `${r.name} ${r.ticker || ''} ${r.sector || ''}`.toLowerCase().includes(ql)) : d.rows
  }, [d, q])
  const shown = all || q ? rows : rows.slice(0, TOP)
  if (err) return <div className="panel" style={{ background: 'var(--panel2)' }}><h3>Holdings</h3><p className="muted small">Could not load holdings: {err}</p></div>
  if (!d) return <div className="panel" style={{ background: 'var(--panel2)' }}><h3>Holdings</h3><p className="muted">Loading…</p></div>
  if (!d.available) return <div className="panel" style={{ background: 'var(--panel2)' }}><h3>Holdings</h3><p className="muted">{d.reason || 'No holdings data for this ETF.'} Load them with <code>python -m app.holdings_import</code> (see README).</p></div>
  const s = d.summary
  const seg = [['Individual securities', s.individual_pct, 'var(--good)'], ['Cash / borrowing', s.cash_pct, 'var(--muted)'], ['Derivatives', s.derivative_pct, 'var(--accent)'],
    ['Funds not looked through', s.opaque_fund_pct, 'var(--warn)'], ['Undisclosed', s.undisclosed_pct, 'var(--bad)']].filter(([, v]) => Math.abs(v) >= 0.005)
  return (
    <div className="panel" style={{ background: 'var(--panel2)' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
        <h3 style={{ margin: 0 }}>Holdings</h3>
        <span className={`chip ${s.complete ? 'good' : 'warn'}`} title="Complete = every % of the fund reaches an individual security, cash or a derivative: no undisclosed remainder, no fund without data.">{s.complete ? 'look-through complete' : 'look-through incomplete'}</span>
        {d.leverage ? <span className="chip warn" title={d.leverage_basis === 'holdings' ? 'Derived from the holdings: sum of positive weights / NAV (negative cash = borrowing)' : "Issuer 'Enhanced' naming convention (1.25x); not stated in the holdings data"}>leverage ~{num(d.leverage, 2)}x{d.leverage_basis === 'holdings' ? '' : ' (assumed)'}</span> : null}
        <span className="muted small">source: {d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.source} ↗</a> : d.source} · as of {d.as_of || '–'} · {d.n_direct} direct rows</span>
        <span style={{ marginLeft: 'auto' }} className="small">
          <label><input type="radio" checked={!look} onChange={() => setLook(false)} /> Direct</label>{' '}
          <label><input type="radio" checked={look} onChange={() => setLook(true)} /> Look-through (to individual securities)</label>
        </span>
      </div>
      <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', background: 'var(--line)', margin: '4px 0' }} title="Share of the look-through weight">
        {seg.filter(([, v]) => v > 0).map(([l, v, c]) => <i key={l} style={{ width: `${Math.min(100, (v / Math.max(100, s.gross_pct || 100)) * 100)}%`, background: c }} title={`${l}: ${v.toFixed(1)}%`} />)}
      </div>
      <div className="muted small" style={{ marginBottom: 6 }}>
        {seg.map(([l, v]) => `${l} ${v.toFixed(1)}%`).join(' · ')}{s.gross_pct > 100.5 ? ` · gross exposure ${s.gross_pct.toFixed(1)}% of NAV` : ''}
      </div>
      {s.flags.length > 0 && (
        <ul className="check small" style={{ margin: '4px 0 8px' }}>
          {s.flags.map((f, i) => <li key={i} className={f.level === 'warn' ? 'neg' : 'muted'} style={f.level === 'warn' ? { color: 'var(--warn)' } : null}>{f.msg}</li>)}
        </ul>
      )}
      {look && d.funds_used.length > 1 && (
        <details className="small" style={{ marginBottom: 8 }}>
          <summary className="muted">Funds looked through ({d.funds_used.length - 1}) with their source and as-of</summary>
          <div className="tablewrap"><table><thead><tr><th className="l">Fund</th><th>Share of NAV</th><th className="l">Source</th><th className="l">As of</th><th className="l">Coverage</th></tr></thead>
            <tbody>{d.funds_used.map((u) => <tr key={u.fund_key}><td className="l"><b>{u.ticker}</b> <span className="muted">{u.name}</span></td><td>{num(u.exposure_pct, 1)}%</td><td className="l" style={/^PROXY/i.test(u.source) ? { color: 'var(--warn)' } : null}>{u.url ? <a href={u.url} target="_blank" rel="noreferrer">{u.source}</a> : u.source}</td><td className="l">{u.as_of}</td><td className="l muted">{u.partial || /^PROXY/i.test(u.source) ? u.coverage : 'full'}{u.leverage ? ` · ~${u.leverage}x` : ''}</td></tr>)}</tbody></table></div>
        </details>
      )}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', margin: '6px 0' }}>
        <input type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder="search ticker, name or sector" />
        <span className="muted small">{q ? `${rows.length} match${rows.length === 1 ? '' : 'es'} of ${d.n_total}` : `top ${Math.min(shown.length, d.n_total)} of ${d.n_total} ${look ? 'securities after look-through' : 'direct holdings'}`}</span>
        {!q && rows.length > TOP && <button className="btn ghost" onClick={() => setAll(!all)}>{all ? `Show top ${TOP}` : `Show all ${rows.length}`}</button>}
      </div>
      <div className="tablewrap" style={{ maxHeight: all || q ? 520 : 'none', overflowY: 'auto' }}>
        <table>
          <thead><tr><th>#</th><th className="l">Ticker</th><th className="l">Name</th><th>{look ? 'Effective weight' : 'Weight'}</th><th className="l">Sector</th><th className="l">{look ? 'Via' : 'Type'}</th></tr></thead>
          <tbody>
            {shown.map((r, i) => {
              const k = KIND[r.kind]
              return (
                <tr key={i} style={r.kind === 'memo' ? { opacity: 0.55 } : null} title={r.kind === 'memo' ? "Memo: top holdings of the underlying fund listed by the issuer; not added to the weights (shown via look-through)" : undefined}>
                  <td className="muted">{i + 1}</td>
                  <td className="l"><b>{r.ticker || '–'}</b></td>
                  <td className="l name" style={{ maxWidth: 360 }}>{r.name}</td>
                  <td className={r.weight < 0 ? 'neg' : ''}>{wfmt(r.weight)}</td>
                  <td className="l small muted">{r.sector || '–'}</td>
                  <td className="l small muted">
                    {look
                      ? Object.entries(r.via || {}).slice(0, 3).map(([f, v]) => `${f} ${v.toFixed(2)}%`).join(' · ') + (Object.keys(r.via || {}).length > 3 ? ' …' : '')
                      : <>{k && k[0] !== 'fund' ? <span className={`chip ${k[1]}`}>{k[0]}</span> : null}{r.kind === 'fund' ? <span className={`chip ${r.lookthrough ? 'good' : 'warn'}`}>{r.lookthrough ? 'fund · expands in look-through' : 'fund · no holdings data'}</span> : null}</>}
                  </td>
                </tr>
              )
            })}
            {shown.length === 0 && <tr><td colSpan={6} className="muted">Nothing matches.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
