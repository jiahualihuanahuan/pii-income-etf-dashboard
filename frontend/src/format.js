export const pct = (v, d = 1) => (v === null || v === undefined ? '–' : `${(v * 100).toFixed(d)}%`)
export const spct = (v, d = 1) => (v === null || v === undefined ? '–' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}%`)
export const num = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d))
export const cls = (v) => (v === null || v === undefined ? '' : v >= 0 ? 'pos' : 'neg')
export const ytLink = (id, t) => `https://youtu.be/${id}${t ? `?t=${t}` : ''}`
export const STANCE_LABELS = {
  core: ['Core holding', 'good'], hold: ['Holds', 'good'], love: ['Loves', 'good'], buy: ['Buying / adding', 'good'],
  recommend: ['Recommends', 'good'], watch: ['Watching', 'warn'], neutral: ['Neutral', 'warn'], cautious: ['Cautious', 'warn'],
  avoid: ['Avoids', 'bad'], sell: ['Sold / avoid', 'bad'], obsolete: ['Obsolete', 'bad'],
}
export const stanceChip = (label) => STANCE_LABELS[label] || [label, 'warn']
