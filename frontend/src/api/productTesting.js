const base = '/api/product-testing'

async function call(path, options = {}) {
  const response = await fetch(base + path, options)
  const result = await response.json()
  if (!response.ok || !result.success) throw new Error(result.error || 'The study request failed.')
  return result.data
}

const json = (value, method = 'POST') => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
export const getStudyDefaults = () => call('/defaults')
export const listStudies = () => call('/studies')
export const getStudy = id => call(`/studies/${id}`)
export const createStudy = spec => call('/studies', json(spec))
export const startStudy = id => call(`/studies/${id}/start`, json({}))
export const cancelStudy = id => call(`/studies/${id}/cancel`, json({}))
export const setStudyBudget = (id, maximum) => call(`/studies/${id}/budget`, json({ max_requests: maximum }, 'PATCH'))
export const studyAssetUrl = (id, asset) => `${base}/studies/${id}/assets/${asset.id}`
export const exportStudyUrl = (id, format = 'json') => `${base}/studies/${id}/export?format=${format}`
export function uploadStudyScreen(id, variantId, file) {
  const body = new FormData()
  body.append('image', file)
  return call(`/studies/${id}/variants/${variantId}/image`, { method: 'POST', body })
}
