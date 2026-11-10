async function req(path, opts) {
  const r = await fetch(path, opts)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
export const api = {
  funds: (params) => req('/api/funds?' + new URLSearchParams(params)),
  fund: (t) => req('/api/funds/' + encodeURIComponent(t)),
  distributions: (t, years = 5) => req(`/api/funds/${encodeURIComponent(t)}/distributions?years=${years}`),
  history: (t, years = 3) => req(`/api/funds/${encodeURIComponent(t)}/history?years=${years}`),
  categories: () => req('/api/categories'),
  stances: (hl) => req('/api/stances' + (hl ? `?half_life_days=${hl}` : '')),
  videos: (hl, mode) => req(`/api/videos?half_life_days=${hl}&views_transform=${mode}`),
  mindset: () => req('/api/mindset'),
  settings: () => req('/api/settings'),
  saveSettings: (body) => req('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  resetSettings: () => req('/api/settings', { method: 'DELETE' }),
  status: () => req('/api/status'),
  runJob: (name) => req(`/api/jobs/${name}/run`, { method: 'POST' }),
}
