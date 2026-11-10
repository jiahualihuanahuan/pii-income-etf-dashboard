import React, { useEffect, useState } from 'react'
import { api } from '../api.js'
import { ytLink } from '../format.js'

const Src = ({ s }) => (s ? <a href={s.url || ytLink(s.video_id, s.t_s)} target="_blank" rel="noreferrer" className="small">{s.title || 'source'} ({s.date}) ↗</a> : null)

export default function Mindset() {
  const [m, setM] = useState(null)
  useEffect(() => { api.mindset().then(setM).catch(() => setM({})) }, [])
  if (!m) return <div className="panel">Loading…</div>
  return (
    <>
      <div className="panel">
        <h2>{m.commandments_title || 'The 10 Commandments of investing'}</h2>
        <p className="muted small">Summarised from his video; see the source for his exact words. {m.commandments_source && <Src s={m.commandments_source} />}</p>
        {(m.commandments || []).map((c) => <div className="cmd" key={c.n}><b className="n">{c.n}</b><div><b>{c.title}</b><div className="muted">{c.text}</div></div></div>)}
      </div>
      <div className="grid2">
        <div className="panel">
          <h2>{m.checklist_title || 'Covered-call fund checklist (in his order)'}</h2>
          {m.checklist_source && <Src s={m.checklist_source} />}
          <ol className="check">{(m.checklist || []).map((c, i) => <li key={i}><b>{c.title}</b> – {c.text}</li>)}</ol>
        </div>
        <div className="panel">
          <h2>Key ideas</h2>
          <ul className="check">{(m.principles || []).map((p, i) => <li key={i}><b>{p.title}</b> – {p.text} {p.source && <Src s={p.source} />}</li>)}</ul>
        </div>
      </div>
      {m.total_return && <div className="panel"><h2>How he defines total return</h2><p>{m.total_return.text}</p><p className="muted small">Tools he uses: {(m.total_return.tools || []).join(', ')}. This dashboard computes the same thing from Yahoo Finance prices and dividends (distributions reinvested on the ex-date). <Src s={m.total_return.source} /></p></div>}
      {m.never_invest && <div className="panel"><h2>{m.never_invest.title}</h2><ul className="check">{m.never_invest.items.map((x, i) => <li key={i}>{x}</li>)}</ul><Src s={m.never_invest.source} /></div>}
    </>
  )
}
