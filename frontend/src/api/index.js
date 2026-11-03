/**
 * API 封装模块。
 * 所有请求基于 fetch，后端 API 前缀为 /api（dev 环境由 vite proxy 代理）。
 */

const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const err = await res.json()
      detail = err.detail || err.message || detail
    } catch {
      // ignore
    }
    throw new Error(detail)
  }
  return res.json()
}

// ----- Sessions -----
export const sessionApi = {
  list: () => request('/sessions'),
  create: (workDir) =>
    request('/sessions', { method: 'POST', body: JSON.stringify({ work_dir: workDir }) }),
  get: (id) => request(`/sessions/${id}`),
  delete: (id) => request(`/sessions/${id}`, { method: 'DELETE' }),
}

// ----- Agents -----
export const agentApi = {
  list: () => request('/agents'),
  current: () => request('/agents/current'),
  create: (name) =>
    request('/agents/create', { method: 'POST', body: JSON.stringify({ name }) }),
  switch: (id) => request(`/agents/${id}/switch`, { method: 'POST' }),
}

// ----- Tools / Skills / Config -----
export const toolsApi = {
  list: () => request('/tools'),
  skills: () => request('/skills'),
  config: () => request('/config'),
}

// ----- Training -----
export const trainingApi = {
  scenarios: () => request('/training/scenarios'),
  start: (data) =>
    request('/training/start', { method: 'POST', body: JSON.stringify(data) }),
  evaluate: (data) =>
    request('/training/evaluate', { method: 'POST', body: JSON.stringify(data) }),
}

// ----- Health -----
export const healthApi = {
  check: () => request('/health'),
}
