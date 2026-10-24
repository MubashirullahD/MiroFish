<template>
  <section class="chatgpt-connection" aria-labelledby="chatgpt-title">
    <div class="connection-heading">
      <div class="connection-copy">
        <p class="connection-eyebrow">LOCAL WORKSPACE</p>
        <h2 id="chatgpt-title">{{ connected ? (planEnabled ? 'Using your ChatGPT plan' : 'ChatGPT account connected') : 'Connect your ChatGPT plan' }}</h2>
        <p class="connection-description">
          <template v-if="checking && !status">Checking your connection…</template>
          <template v-else-if="connected && planEnabled">{{ accountLabel }} · Requests count toward your existing plan.</template>
          <template v-else-if="connected">Your account is connected. Subscription access is not enabled for this account yet.</template>
          <template v-else>Sign in to use your existing subscription for simulations.</template>
        </p>
      </div>
      <div class="connection-actions">
        <a v-if="!connected" :href="loginUrl" target="_blank" rel="noopener noreferrer"
          class="connection-button connection-button-primary" @click="startSignIn">
          Continue with ChatGPT <span aria-hidden="true">↗</span>
        </a>
        <span v-else class="connection-badge" :class="{ 'connection-badge-pending': !planEnabled }">
          <span aria-hidden="true">{{ planEnabled ? '●' : '○' }}</span>
          {{ planEnabled ? 'Connected' : 'Access pending' }}
        </span>
        <button type="button" class="connection-button" :disabled="checking || !!busy" @click="refresh">
          {{ checking ? 'Refreshing…' : 'Refresh' }}
        </button>
      </div>
    </div>

    <div v-if="connected" class="connection-controls">
      <label class="connection-model">
        <span>Simulation model</span>
        <select v-model="selectedModel" :disabled="!models.length || !!busy || checking" @change="saveModel">
          <option v-if="!models.length" value="">{{ planEnabled ? 'No models available' : 'Waiting for account access' }}</option>
          <option v-else value="" disabled>Select a model</option>
          <option v-for="model in models" :key="model.id" :value="model.id">{{ model.display_name || model.id }}</option>
        </select>
      </label>
      <button type="button" class="connection-button" :disabled="!planEnabled || !selectedModel || !!busy || checking" @click="testConnection">
        {{ busy === 'test' ? 'Testing…' : 'Test connection · 1 request' }}
      </button>
      <a class="connection-text-link" href="https://chatgpt.com/#settings/Usage" target="_blank" rel="noopener noreferrer">Manage plan usage ↗</a>
      <button type="button" class="connection-text-button" :disabled="!!busy || checking" @click="signOut">Sign out</button>
    </div>

    <div class="connection-footer">
      <span>{{ storageLabel }}</span>
      <span v-if="usageLabel">{{ usageLabel }}</span>
      <button v-if="connected && usageLabel" type="button" class="connection-text-button" :disabled="!!busy || checking" @click="resetUsage">Reset local counter</button>
      <a :href="`${loginUrl}?new=1`" target="_blank" rel="noopener noreferrer" class="connection-text-link" @click="startSignIn($event, true)">{{ connected ? 'Switch account' : 'Use another account' }} ↗</a>
    </div>
    <p v-if="waitingForSignIn" class="connection-notice" role="status">Complete sign-in in the new tab. This page will update automatically.</p>
    <p v-if="status?.adapter_configured === false" class="connection-notice connection-error" role="status">This backend uses another model provider. Enable the local subscription settings in .env as described in LOCAL_SETUP.md before running a simulation with your plan.</p>
    <p v-if="status?.usage?.paused" class="connection-notice connection-error" role="status">Simulation requests are paused. Check your plan usage and local request limit before continuing.</p>
    <p v-if="feedback" class="connection-notice" :class="{ 'connection-error': feedbackIsError }" :role="feedbackIsError ? 'alert' : 'status'">{{ feedback }}</p>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

const status = ref(null)
const models = ref([])
const selectedModel = ref('')
const checking = ref(false)
const busy = ref('')
const feedback = ref('')
const feedbackIsError = ref(false)
const waitingForSignIn = ref(false)
let pollTimer
let pollDeadline
let previousClientId = null
let disposed = false

const connected = computed(() => status.value?.connected === true)
const planEnabled = computed(() => status.value?.plan_enabled === true)
const accountLabel = computed(() => status.value?.email || 'Connected account')
const storageLabel = computed(() => {
  if (!status.value) return 'Graph storage: checking…'
  return status.value.memory_backend === 'local' ? 'Graph storage: local to this computer' : 'Graph storage: Zep Cloud'
})
const usageLabel = computed(() => {
  const usage = status.value?.usage
  if (!usage) return ''
  const used = usage.used ?? usage.requests_used ?? usage.request_count
  const limit = usage.limit ?? usage.max_requests
  if (typeof used !== 'number') return ''
  return typeof limit === 'number' ? `${used} of ${limit} local requests used` : `${used} local requests used`
})

// Sign-in opens on the backend so its browser callback stays on loopback.
const loginUrl = (() => {
  try {
    const configured = new URL(import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:5001')
    if (configured.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(configured.hostname)) {
      configured.hostname = '127.0.0.1'
      return new URL('/api/chatgpt/login', configured).href
    }
  } catch { /* Use the local backend default when the configured URL is invalid. */ }
  return 'http://127.0.0.1:5001/api/chatgpt/login'
})()

async function request(path, options = {}) {
  const response = await fetch(`/api/chatgpt/${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
    signal: AbortSignal.timeout(path === 'test' ? 250000 : 20000),
  })
  let data
  try { data = await response.json() } catch { throw new Error('The local server returned an unreadable response. Please try Refresh.') }
  if (!response.ok || data.success === false) {
    const detail = data.error?.message || data.error || data.message
    throw new Error(typeof detail === 'string' ? detail : 'The request failed. Please try again.')
  }
  return data
}

function showFeedback(message, isError = false) {
  feedback.value = message
  feedbackIsError.value = isError
}

function stopPolling() {
  clearInterval(pollTimer)
  clearTimeout(pollDeadline)
  pollTimer = undefined
  pollDeadline = undefined
  previousClientId = null
  waitingForSignIn.value = false
}

function startSignIn(_event, switchAccount = false) {
  stopPolling()
  previousClientId = switchAccount && connected.value ? status.value.client_id : null
  showFeedback('')
  waitingForSignIn.value = true
  pollTimer = setInterval(() => loadStatus(), 3000)
  pollDeadline = setTimeout(() => {
    stopPolling()
    showFeedback('Still waiting for sign-in. Finish in the other tab, then select Refresh.')
  }, 120000)
}

async function loadStatus() {
  if (checking.value || disposed) return
  checking.value = true
  try {
    const data = await request('status')
    if (disposed) return
    status.value = data
    if (data.connected) {
      // Keep the old account active until the new registration is verified.
      if (waitingForSignIn.value && previousClientId && data.client_id === previousClientId) return
      stopPolling()
      const catalog = await request('models')
      if (disposed) return
      models.value = Array.isArray(catalog.models) ? catalog.models : []
      selectedModel.value = catalog.selected_model || data.model || ''
    } else {
      models.value = []
      selectedModel.value = ''
    }
  } catch (error) {
    if (!disposed) showFeedback(error.message || 'Cannot reach the local server. Check that MiroFish is running.', true)
  } finally {
    checking.value = false
  }
}

async function refresh() {
  showFeedback('')
  await loadStatus()
}

async function perform(action, path, successMessage, body) {
  if (busy.value) return
  busy.value = action
  showFeedback('')
  try {
    const data = await request(path, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) })
    await loadStatus()
    if (!disposed) showFeedback(data.message || successMessage)
  } catch (error) {
    if (!disposed) {
      const message = error.message
      await loadStatus()
      selectedModel.value = status.value?.model || ''
      showFeedback(message, true)
    }
  } finally {
    busy.value = ''
  }
}

function saveModel() { return perform('model', 'model', 'Simulation model updated.', { model: selectedModel.value }) }
function testConnection() { return perform('test', 'test', 'Connection test succeeded. One request was used.') }
function signOut() { stopPolling(); return perform('logout', 'logout', 'Signed out of this local workspace.') }
function resetUsage() { return perform('reset', 'usage/reset', 'Local counter reset. Your ChatGPT plan allowance is unchanged.') }

onMounted(loadStatus)
onUnmounted(() => { disposed = true; stopPolling() })
</script>

<style scoped>
.chatgpt-connection {
  padding: 24px 40px;
  background: #f7f8f6;
  border-bottom: 1px solid #dce1d9;
  color: #20261e;
  font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.connection-heading { display: flex; justify-content: space-between; align-items: center; gap: 24px; }
.connection-copy { min-width: 0; }
.connection-eyebrow { margin: 0 0 8px; font-size: 11px; font-weight: 600; letter-spacing: .12em; color: #626c5d; }
.connection-copy h2 { margin: 0; font-size: 20px; font-weight: 600; line-height: 1.3; letter-spacing: -.02em; }
.connection-description { max-width: 75ch; margin: 8px 0 0; font-size: 14px; line-height: 1.5; color: #596253; overflow-wrap: anywhere; }
.connection-actions, .connection-controls, .connection-footer { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.connection-actions { flex-shrink: 0; }
.connection-button { display: inline-flex; justify-content: center; align-items: center; gap: 12px; min-height: 40px; padding: 10px 16px; border: 1px solid #c9d1c4; border-radius: 4px; background: #fff; color: #273022; font: inherit; font-size: 13px; font-weight: 600; line-height: 1.4; text-decoration: none; cursor: pointer; }
.connection-button:hover { background: #ecefe9; }
.connection-button-primary { border-color: #273e25; background: #273e25; color: #fff; }
.connection-button-primary:hover { background: #1c301a; }
.connection-button:disabled, .connection-text-button:disabled, .connection-model select:disabled { opacity: .55; cursor: default; }
.connection-button:focus-visible, .connection-text-link:focus-visible, .connection-text-button:focus-visible, .connection-model select:focus-visible { outline: 2px solid #608b4f; outline-offset: 3px; }
.connection-badge { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; color: #386c2e; }
.connection-badge-pending { color: #876226; }
.connection-controls { margin-top: 20px; gap: 16px; }
.connection-model { display: flex; align-items: center; gap: 12px; font-size: 13px; font-weight: 600; min-width: 0; }
.connection-model select { max-width: 280px; min-width: 180px; min-height: 40px; padding: 8px 32px 8px 12px; border: 1px solid #c9d1c4; border-radius: 4px; background: #fff; color: #273022; font: inherit; font-weight: 400; text-overflow: ellipsis; }
.connection-text-link, .connection-text-button { color: #435b38; font: inherit; font-size: 13px; text-decoration: underline; text-underline-offset: 3px; }
.connection-text-button { padding: 0; border: 0; background: none; cursor: pointer; }
.connection-footer { margin-top: 16px; column-gap: 24px; row-gap: 8px; font-size: 12px; line-height: 1.5; color: #626c5d; }
.connection-footer .connection-text-button { font-size: 12px; }
.connection-footer .connection-text-link { font-size: 12px; }
.connection-notice { margin: 12px 0 0; font-size: 13px; line-height: 1.5; color: #435b38; overflow-wrap: anywhere; }
.connection-error { color: #a13029; }
@media (max-width: 760px) {
  .chatgpt-connection { padding: 24px; }
  .connection-heading { align-items: flex-start; flex-direction: column; gap: 16px; }
  .connection-actions { width: 100%; }
  .connection-controls { gap: 16px; }
  .connection-model { flex-basis: 100%; flex-wrap: wrap; gap: 8px; }
  .connection-model select { flex: 1; width: 100%; max-width: none; min-width: 0; }
}
@media (max-width: 420px) {
  .chatgpt-connection { padding: 20px 16px; }
  .connection-copy h2 { font-size: 18px; }
  .connection-actions .connection-button { flex: 1; padding: 10px 12px; }
  .connection-model { display: block; }
  .connection-model select { display: block; margin-top: 8px; }
}
</style>
