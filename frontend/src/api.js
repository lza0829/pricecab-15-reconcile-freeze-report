async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    let detail = await res.text()
    try {
      const j = JSON.parse(detail)
      detail = j.detail || detail
    } catch (_) {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  health: () => request('/api/health'),
  dashboard: () => request('/api/dashboard'),
  skus: () => request('/api/skus'),
  tickets: () => request('/api/tickets'),
  ticket: (id) => request(`/api/tickets/${id}`),
  createTicket: (body) => request('/api/tickets', { method: 'POST', body: JSON.stringify(body) }),
  clock: () => request('/api/apply/clock'),
  setClock: (business_clock) =>
    request('/api/apply/clock', { method: 'PUT', body: JSON.stringify({ business_clock }) }),
  applyDue: (retire_policy) =>
    request('/api/apply/due', {
      method: 'POST',
      body: JSON.stringify(retire_policy ? { retire_policy } : {}),
    }),
  batches: () => request('/api/apply/batches'),
  priceBook: () => request('/api/prices/book'),
  history: (sku) => request(sku ? `/api/prices/history?sku_code=${encodeURIComponent(sku)}` : '/api/prices/history'),
  reconcile: () => request('/api/reconcile/sales'),
  reports: () => request('/api/reconcile/reports'),
  report: (reportNo) => request(`/api/reconcile/reports/${encodeURIComponent(reportNo)}`),
  createReport: (note, holdMs) =>
    request('/api/reconcile/reports', {
      method: 'POST',
      body: JSON.stringify({ note: note || null, hold_ms: Number(holdMs) || 0 }),
    }),
  reportPolicy: () => request('/api/reconcile/policy'),
  setReportPolicy: (policy) =>
    request('/api/reconcile/policy', { method: 'PUT', body: JSON.stringify({ policy }) }),
}
