// Thin wrapper around the 4 endpoints WebServer.py exposes (see its module docstring for the
// full JSON shape of GET /api/state). Deliberately no client-side caching/state here -- App.vue
// owns all of that; this file only knows how to talk to the backend.

async function postJson(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `request to ${path} failed (${res.status})`)
  }
  return data
}

export async function fetchState() {
  const res = await fetch('/api/state')
  if (!res.ok) throw new Error(`failed to fetch state (${res.status})`)
  return res.json()
}

export function submitRoom(text) {
  return postJson('/api/room', { text })
}

export function submitSelection(slotIds) {
  return postJson('/api/selection', { slot_ids: slotIds })
}

export function requestRefresh() {
  return postJson('/api/refresh', {})
}
