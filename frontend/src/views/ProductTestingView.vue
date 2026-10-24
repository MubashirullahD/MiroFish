<template>
  <div class="study-page">
    <nav class="study-nav" aria-label="Workspace">
      <RouterLink to="/" class="study-brand">MIROFISH <span>LOCAL</span></RouterLink>
      <div><RouterLink to="/product-testing" class="selected">Product testing</RouterLink><RouterLink to="/">Social simulation</RouterLink></div>
    </nav>
    <main class="study-main">
      <header class="study-intro">
        <p class="eyebrow">PRODUCT STUDIES</p>
        <h1>See your product through fresh eyes.</h1>
        <p>Explore first impressions, compare two screens, or observe a browser task. Start with a small group of hypothetical participants.</p>
      </header>
      <div class="study-layout">
        <aside class="study-sidebar">
          <button class="primary new-study" @click="newStudy">＋ New study</button>
          <h2>Your studies</h2>
          <p v-if="!studies.length" class="muted">Your saved studies will appear here.</p>
          <RouterLink v-for="item in studies" :key="item.id" :to="`/product-testing/${item.id}`" class="study-history" :class="{ current: study?.id === item.id }">
            <strong>{{ item.name }}</strong><span>{{ kindLabel(item.kind) }} · {{ statusLabel(item.status) }}</span>
          </RouterLink>
          <div class="study-note"><strong>A useful starting point</strong><p>AI feedback helps form hypotheses. Validate important decisions with real people.</p></div>
        </aside>

        <section class="study-content" aria-label="Study workspace">
          <p v-if="error" class="study-error" role="alert">{{ error }}</p>
          <p v-if="loading" class="muted" role="status">Loading your workspace…</p>

          <form v-if="!study && !loading" @submit.prevent="saveAndRun" class="setup-form">
            <section class="study-card">
              <div class="section-heading"><span class="section-number">01</span><div><h2>What would you like to learn?</h2><p>Each study keeps its audience, screens, and evidence together.</p></div></div>
              <label>Study name<input v-model="form.name" required maxlength="160" placeholder="Map of XP — first impressions"></label>
              <div class="study-kind-options">
                <button v-for="option in kinds" :key="option.id" type="button" :aria-pressed="form.kind === option.id" :class="{ chosen: form.kind === option.id }" @click="changeKind(option.id)"><strong>{{ option.title }}</strong><span>{{ option.description }}</span></button>
              </div>
              <label v-if="form.kind === 'comparison'">Comparison hypothesis<textarea v-model="form.hypothesis" required maxlength="2000" placeholder="A clearer headline will make it easier to understand what the pins represent."></textarea><small>Choose one question. No preferred version is revealed to the evaluator.</small></label>
              <template v-if="form.kind === 'browser_task'">
                <label>Task for each participant<textarea v-model="form.task" required maxlength="2000" placeholder="Find an experience in Karachi and open its full account."></textarea></label>
                <div class="field-pair"><label>Success: URL contains<input v-model="form.success_url_contains" placeholder="/experience/"></label><label>Success: visible text contains<input v-model="form.success_text" placeholder="A specific heading or detail"></label></div>
                <small>Supply at least one observable success condition. If both are supplied, both must be met. Public, read-only browsing only.</small>
              </template>
            </section>

            <section class="study-card">
              <div class="section-heading"><span class="section-number">02</span><div><h2>{{ form.kind === 'comparison' ? 'Choose two versions' : 'Choose the screen' }}</h2><p>Use the application for an app study. A marketing page is a separate test.</p></div></div>
              <div class="variant-grid" :class="{ two: form.kind === 'comparison' }">
                <div v-for="(variant, index) in visibleVariants" :key="index" class="variant-input">
                  <label>{{ form.kind === 'comparison' ? `Version ${index + 1} label` : 'Screen label' }}<input v-model="variant.name" required maxlength="100"></label>
                  <div v-if="form.kind !== 'browser_task'" class="source-options"><label><input type="radio" v-model="variant.source" value="url"> Public page</label><label><input type="radio" v-model="variant.source" value="image"> Screenshot</label></div>
                  <label v-if="variant.source === 'url' || form.kind === 'browser_task'">Public page URL<input v-model="variant.url" type="url" placeholder="https://mapofxp.com/" required><small>We capture and freeze the page before participants see it.</small></label>
                  <label v-else class="file-label">Upload screenshot<input type="file" accept="image/png,image/jpeg,image/webp" required @change="pickImage($event, index)"><small>{{ variant.file ? `${variant.file.name} · ${variant.width} × ${variant.height}` : 'PNG, JPEG or WebP, up to 5 MB. Use matching dimensions for both versions.' }}</small></label>
                  <img v-if="variant.preview && variant.source === 'image'" :src="variant.preview" class="input-preview" alt="Selected study screenshot">
                </div>
              </div>
              <div class="field-pair screen-size"><label>Screen width<input type="number" v-model.number="form.viewport.width" min="320" max="1920" required></label><label>Screen height<input type="number" v-model.number="form.viewport.height" min="480" max="1200" required></label></div>
              <p class="muted small">The study uses English and a fixed Karachi setting. Private apps can be tested using screenshots.</p>
            </section>

            <section class="study-card">
              <div class="section-heading"><span class="section-number">03</span><div><h2>Who should take a fresh look?</h2><p>These are hypothetical perspectives. No graph extraction or existing customer profiles are required.</p></div></div>
              <fieldset v-for="(person, index) in form.personas" :key="index" class="persona-editor"><legend>Participant {{ index + 1 }}</legend>
                <div class="persona-top"><label>Name<input v-model="person.name" required maxlength="100"></label><button v-if="form.personas.length > 1" type="button" class="text-button" @click="form.personas.splice(index, 1)">Remove</button></div>
                <label>Background<textarea v-model="person.background" required maxlength="1500" rows="2"></textarea></label>
                <label>Immediate goal<input v-model="person.goal" required maxlength="1000"></label>
              </fieldset>
              <button v-if="form.personas.length < 6" type="button" class="secondary" @click="addPerson">＋ Add participant</button>
            </section>

            <section class="study-card">
              <div class="section-heading"><span class="section-number">04</span><div><h2>Context and limits</h2><p>The product brief is available to the evaluator after independent responses are saved.</p></div></div>
              <details class="context-details"><summary>Researcher context · hidden from participants</summary><label>Product description<textarea v-model="form.researcher_context" rows="9" maxlength="30000" placeholder="Product facts, source links, and which features are available."></textarea></label></details>
              <details class="context-details"><summary>Capture settings and request limits</summary>
                <label>Additional asset origins<textarea v-model="assetOrigins" rows="2" placeholder="https://tiles.openfreemap.org"></textarea><small>One HTTPS origin per line. Use this only if page images, fonts, or maps need another public domain.</small></label>
                <div class="field-pair"><label>Maximum model requests<input type="number" min="3" max="100" v-model.number="form.max_requests" required></label><label v-if="form.kind === 'browser_task'">Maximum actions per participant<input type="number" min="1" max="10" v-model.number="form.max_steps" required></label></div>
              </details>
              <div class="run-summary"><div><strong>{{ form.personas.length }} participants · {{ visibleVariants.length }} {{ visibleVariants.length === 1 ? 'screen' : 'screens' }}</strong><p>At most {{ requestBound }} model requests, including a screenshot check and evaluation.</p></div><span class="pill">Your connected plan</span></div>
              <p v-if="requestBound > form.max_requests" class="study-error">The request cap is too small. Increase it or reduce the participants or action limit.</p>
              <p class="small muted">Screenshots and selected study context are sent to your connected model. Files and evidence are stored on this computer. Results are AI hypotheses, not measured human behavior.</p>
              <button class="primary run-button" :disabled="busy || requestBound > form.max_requests">{{ busy ? 'Saving your study…' : 'Save and run study →' }}</button>
            </section>
          </form>

          <template v-if="study">
            <section class="study-card study-overview">
              <div class="overview-top"><div><p class="eyebrow">{{ kindLabel(study.spec.kind) }}</p><h2>{{ study.spec.name }}</h2></div><span class="pill" :class="study.status">{{ statusLabel(study.status) }}</span></div>
              <p class="muted">{{ completedRuns }} of {{ study.runs.length }} sessions saved · {{ study.requests_used }} of {{ study.spec.max_requests }} requests used<span v-if="study.model"> · {{ study.model }}</span></p>
              <progress v-if="study.runs.length" :value="completedRuns" :max="study.runs.length" aria-label="Completed sessions"></progress>
              <p v-if="study.status === 'running' || study.status === 'queued'" role="status" class="run-status">{{ completedRuns === study.runs.length && study.runs.length ? 'Evaluating the saved evidence…' : 'Preparing screens and collecting independent responses…' }} You can leave this page and return.</p>
              <p v-if="study.error" class="study-error" role="alert">{{ study.error }}</p>
              <p v-if="study.quality_warning" class="study-error" role="alert">{{ study.quality_warning }}</p>
              <div class="study-actions"><button v-if="active" class="secondary" :disabled="busy" @click="stop">Stop after current request</button><button v-if="['draft','failed','cancelled'].includes(study.status)" class="primary" :disabled="busy" @click="resume">{{ busy ? 'Starting…' : study.status === 'draft' ? 'Run study' : 'Resume unfinished work' }}</button><a class="secondary" :href="exportStudyUrl(study.id)">Export evidence</a><a class="secondary" :href="exportStudyUrl(study.id, 'human-plan')">Human validation plan</a></div>
              <details v-if="['draft','failed','cancelled'].includes(study.status)" class="context-details"><summary>Change this study’s request cap</summary><div class="budget-control"><label>Maximum requests<input type="number" min="3" max="100" v-model.number="resumeBudget"></label><button class="secondary" :disabled="busy" @click="saveBudget">Save limit</button></div></details>
              <p class="small muted">{{ study.limitation }}</p>
            </section>

            <section v-if="study.report" class="study-card report-card">
              <p class="eyebrow">EVALUATION</p><h2>What the evidence suggests</h2><p class="report-summary">{{ study.report.summary }}</p>
              <div v-if="study.report.control === 'identical_screens'" class="control-note">Identical-image control: no design difference can be inferred from these two inputs.</div>
              <article v-for="(finding, index) in study.report.findings" :key="index" class="finding"><div class="finding-top"><h3>{{ finding.title }}</h3><span class="severity" :class="finding.severity">{{ finding.severity }}</span></div><p>{{ finding.description }}</p><blockquote v-for="(citation, n) in finding.evidence" :key="n"><p>“{{ citation.quote }}”</p><a :href="`#${citation.run_id}`">{{ runLabel(citation.run_id) }} ↓</a></blockquote></article>
              <div class="next-step"><strong>Check this with people</strong><p>{{ study.report.next_step }}</p></div>
            </section>

            <section class="study-card"><h2>Frozen screens and setup</h2><p class="muted">{{ study.spec.viewport.width }} × {{ study.spec.viewport.height }} · {{ study.spec.location }} · {{ study.spec.language }}</p>
              <p v-if="study.spec.hypothesis" class="study-hypothesis">{{ study.spec.hypothesis }}</p>
              <div class="frozen-grid"><figure v-for="variant in study.spec.variants" :key="variant.id"><a v-if="variant.asset" :href="studyAssetUrl(study.id, variant.asset)" target="_blank" rel="noopener"><img :src="studyAssetUrl(study.id, variant.asset)" :alt="variant.name"></a><figcaption><strong>{{ variant.name }}</strong><span v-if="variant.url">{{ variant.url }}</span><small v-if="variant.asset">Captured {{ formatDate(variant.asset.captured_at) }} · {{ variant.asset.sha256.slice(0, 12) }}</small></figcaption><label v-if="study.status === 'draft'">{{ variant.asset ? 'Replace screenshot' : 'Upload screenshot' }}<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="busy" @change="uploadDraft($event, variant.id)"><small>Use {{ study.spec.viewport.width }} × {{ study.spec.viewport.height }} pixels.</small></label><p v-if="variant.asset?.source?.blocked_requests?.length" class="capture-note">Some external requests were blocked. Check the screenshot is complete before interpreting its findings.</p></figure></div>
              <details class="context-details"><summary>Researcher context and participant definitions</summary><p class="small muted">This brief was hidden from the participants.</p><pre>{{ study.spec.researcher_context || 'No source brief supplied.' }}</pre><div v-for="p in study.spec.personas" :key="p.id" class="saved-persona"><strong>{{ p.name }} · hypothetical</strong><p>{{ p.background }}</p><p>Goal: {{ p.goal }}</p></div></details>
            </section>

            <section v-if="study.runs.length" class="study-card evidence-card"><h2>Independent sessions</h2><p class="muted">Each participant starts fresh for every version. Open a session to inspect its answers and evidence.</p>
              <details v-for="run in study.runs" :id="run.id" :key="run.id" class="session-details"><summary><span><strong>{{ personaName(run.persona_id) }}</strong><small>{{ variantName(run.variant_id) }} · hypothetical participant</small></span><span class="pill">{{ statusLabel(run.status) }}</span></summary>
                <template v-if="run.response && study.spec.kind !== 'browser_task'"><dl class="answers"><dt>Understanding</dt><dd>{{ run.response.understanding }}</dd><dt>Intended audience</dt><dd>{{ run.response.audience }}</dd><dt>First action</dt><dd>{{ run.response.first_action }}</dd><dt>Uncertainties</dt><dd><ul><li v-for="(item, i) in run.response.uncertainties" :key="i">{{ item }}</li></ul><span v-if="!run.response.uncertainties.length">None stated.</span></dd></dl><p v-for="(item, i) in run.response.screen_evidence || []" :key="i" class="screen-observation"><strong>{{ item.region }}:</strong> {{ item.observation }}</p></template>
                <template v-if="study.spec.kind === 'browser_task' && run.response"><p class="task-outcome"><strong>{{ run.response.success_observed ? 'Goal observed' : 'Goal not verified' }}</strong> · {{ run.response.outcome }}</p><p v-if="run.response.outcome_reason" class="small muted">{{ run.response.outcome_reason }}</p><p v-else-if="run.response.outcome === 'access_restriction'" class="small muted">The test runner deliberately blocked an account or authoring route. This is not evidence of a product defect.</p><p class="small muted">Agent execution, not a human completion rate.</p></template>
                <details v-for="(step, index) in run.steps" :key="index" class="trace-step"><summary>Step {{ index + 1 }} · {{ step.action.action }} — {{ step.action.reason }}</summary><p class="small muted">{{ step.observation.url }}</p><img :src="studyAssetUrl(study.id, step.screenshot)" :alt="`Evidence at step ${index + 1}`"><p v-if="step.observation.blocked_requests.length" class="small muted">Some requests were blocked by the public, read-only boundary.</p></details>
                <p v-if="run.previous_attempts?.length" class="muted small">{{ run.previous_attempts.length }} interrupted browser attempt(s) retained in the evidence export.</p>
                <details v-if="run.raw_text" class="raw-response"><summary>Original model response</summary><pre>{{ run.raw_text }}</pre></details>
              </details>
            </section>
          </template>
        </section>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { cancelStudy, createStudy, exportStudyUrl, getStudy, getStudyDefaults, listStudies, setStudyBudget, startStudy, studyAssetUrl, uploadStudyScreen } from '../api/productTesting'

const route = useRoute(), router = useRouter()
const studies = ref([]), study = ref(null), error = ref(''), loading = ref(true), busy = ref(false), resumeBudget = ref(12)
const assetOrigins = ref('https://tiles.openfreemap.org\nhttps://media.mapofxp.com')
let defaults = null, timer, disposed = false
const kinds = [{ id: 'first_impression', title: 'First impressions', description: 'What does a fresh screen communicate?' }, { id: 'comparison', title: 'Compare two designs', description: 'Explore one hypothesis across two versions.' }, { id: 'browser_task', title: 'Browser walkthrough', description: 'Attempt a public, read-only task.' }]
const makeVariant = (name, url = '') => ({ name, url, source: 'url', file: null, preview: '', width: 0, height: 0 })
const form = reactive({ name: 'Map of XP — first impressions', kind: 'first_impression', hypothesis: '', task: '', success_url_contains: '', success_text: '', researcher_context: '', personas: [], variants: [makeVariant('Current public app', 'https://mapofxp.com/'), makeVariant('Alternative design')], viewport: { width: 1280, height: 800 }, max_steps: 5, max_requests: 12 })
const visibleVariants = computed(() => form.variants.slice(0, form.kind === 'comparison' ? 2 : 1))
const requestBound = computed(() => form.personas.length * visibleVariants.value.length * (form.kind === 'browser_task' ? form.max_steps : 1) + 2)
const completedRuns = computed(() => study.value?.runs.filter(r => r.status === 'completed').length || 0)
const active = computed(() => ['running', 'queued'].includes(study.value?.status))
const kindLabel = kind => kinds.find(k => k.id === kind)?.title || kind
const statusLabel = status => ({ draft: 'Draft', queued: 'Queued', running: 'Running', completed: 'Complete', failed: 'Needs attention', cancelled: 'Stopped' }[status] || status)
const personaName = id => study.value?.spec.personas.find(p => p.id === id)?.name || id
const variantName = id => study.value?.spec.variants.find(v => v.id === id)?.name || id
const runLabel = id => { const run = study.value?.runs.find(r => r.id === id); return run ? `${personaName(run.persona_id)} · ${variantName(run.variant_id)}` : id }
const formatDate = value => new Date(value).toLocaleString()
function resetForm() {
  form.personas = JSON.parse(JSON.stringify(defaults?.personas || [])); form.researcher_context = defaults?.researcher_context || ''
  assetOrigins.value = (defaults?.asset_origins || []).join('\n')
}
function changeKind(kind) { form.kind = kind; form.max_requests = kind === 'browser_task' ? 24 : 12; if (kind === 'browser_task') form.variants[0].source = 'url' }
function addPerson() { form.personas.push({ name: `Participant ${form.personas.length + 1}`, background: 'Has never used this application.', goal: 'Understand what this product offers.' }) }
async function pickImage(event, index) {
  const file = event.target.files[0]
  if (!file) return
  error.value = ''
  if (file.size > 5 * 1024 * 1024) { error.value = 'Choose a screenshot smaller than 5 MB.'; return }
  const variant = form.variants[index]
  if (variant.preview) URL.revokeObjectURL(variant.preview)
  const url = URL.createObjectURL(file), image = new Image()
  image.onload = () => {
    variant.file = file; variant.preview = url; variant.width = image.width; variant.height = image.height
    if (!index) form.viewport = { width: image.width, height: image.height }
    else if (image.width !== form.viewport.width || image.height !== form.viewport.height) error.value = 'Both screenshots need matching dimensions. Choose matching images before running.'
  }
  image.onerror = () => { URL.revokeObjectURL(url); error.value = 'This screenshot could not be read.' }
  image.src = url
}
async function refreshList() { studies.value = await listStudies() }
async function loadSelected() {
  clearTimeout(timer)
  if (disposed) return
  const id = route.params.studyId
  if (!id) { study.value = null; return }
  try {
    const result = await getStudy(id)
    if (disposed || route.params.studyId !== id) return
    study.value = result; resumeBudget.value = result.spec.max_requests
    if (['running', 'queued'].includes(result.status)) timer = setTimeout(loadSelected, 2000)
    else await refreshList()
  } catch (err) { error.value = err.message }
}
async function newStudy() {
  error.value = ''; await router.push('/product-testing'); study.value = null
  for (const variant of form.variants) if (variant.preview) URL.revokeObjectURL(variant.preview)
  Object.assign(form, { name: 'Map of XP — first impressions', kind: 'first_impression', hypothesis: '', task: '', success_url_contains: '', success_text: '',
    variants: [makeVariant('Current public app', defaults?.product_url || 'https://mapofxp.com/'), makeVariant('Alternative design')], viewport: {width:1280,height:800}, max_steps:5, max_requests:12 })
  resetForm()
}
async function saveAndRun() {
  error.value = ''; busy.value = true
  try {
    for (const variant of visibleVariants.value) {
      if (variant.source === 'image' && (!variant.file || variant.width !== form.viewport.width || variant.height !== form.viewport.height)) throw new Error('Select a screenshot matching the study width and height for each version.')
    }
    const result = await createStudy({ ...form, asset_origins: assetOrigins.value.split(/\s+/).filter(Boolean), variants: visibleVariants.value.map(v => ({ name: v.name, url: v.source === 'url' ? v.url : '' })) })
    await router.push(`/product-testing/${result.id}`)
    for (let index = 0; index < visibleVariants.value.length; index++) {
      const variant = visibleVariants.value[index]
      if (variant.source === 'image') await uploadStudyScreen(result.id, `v${index}`, variant.file)
    }
    await startStudy(result.id); await refreshList(); await loadSelected()
  } catch (err) { error.value = err.message; await refreshList().catch(() => {}) }
  finally { busy.value = false }
}
async function resume() { error.value = ''; busy.value = true; try { await startStudy(study.value.id); await loadSelected() } catch (err) { error.value = err.message } finally { busy.value = false } }
async function stop() { busy.value = true; try { study.value = await cancelStudy(study.value.id); clearTimeout(timer); await refreshList() } catch (err) { error.value = err.message } finally { busy.value = false } }
async function saveBudget() { busy.value = true; try { study.value = await setStudyBudget(study.value.id, resumeBudget.value); error.value = '' } catch (err) { error.value = err.message } finally { busy.value = false } }
async function uploadDraft(event, variantId) { const file = event.target.files[0]; if (!file) return; busy.value = true; try { study.value = await uploadStudyScreen(study.value.id, variantId, file); error.value = '' } catch (err) { error.value = err.message } finally { busy.value = false } }
watch(() => route.params.studyId, () => { error.value = ''; loadSelected() })
onMounted(async () => { try { defaults = await getStudyDefaults(); resetForm(); await refreshList(); await loadSelected() } catch (err) { error.value = err.message } finally { loading.value = false } })
onUnmounted(() => { disposed = true; clearTimeout(timer); for (const v of form.variants) if (v.preview) URL.revokeObjectURL(v.preview) })
</script>

<style scoped>
.study-page {
  --ink: #222521;
  --muted: #666d66;
  --border: #dddeda;
  --accent: #a54428;
  background: #f6f5f1;
  color: var(--ink);
  min-height: 100vh;
  font-family: system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size: 15px;
  line-height: 1.55;
}

.study-nav {
  padding: 20px max(24px,calc((100vw - 1280px)/2));
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  background: #fff;
  border-bottom: 1px solid var(--border);
}

.study-brand {
  font-weight: 800;
  letter-spacing: .05em;
  text-decoration: none;
  color: var(--ink);
}

.study-brand span {
  font-size: 10px;
  color: var(--muted);
  margin-left: 6px;
}

.study-nav>div {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}

.study-nav>div a {
  color: var(--muted);
  font-size: 14px;
  text-decoration: none;
}

.study-nav>div a.selected {
  color: var(--accent);
  font-weight: 650;
}

.study-main {
  max-width: 1328px;
  margin: auto;
  padding: 40px 24px 80px;
}

.study-intro {
  max-width: 760px;
  margin: 0 0 32px;
}

.eyebrow {
  font-size: 11px;
  font-weight: 750;
  letter-spacing: .13em;
  color: var(--accent);
  margin-bottom: 8px;
}

.study-intro h1 {
  font-size: clamp(28px,3.6vw,42px);
  line-height: 1.15;
  letter-spacing: -.035em;
  margin: 8px 0 16px;
}

.study-intro>p:last-child {
  color: var(--muted);
  font-size: 17px;
  max-width: 650px;
}

.study-layout {
  display: grid;
  grid-template-columns: 230px minmax(0,1fr);
  gap: 32px;
}

.study-sidebar h2 {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--muted);
  margin: 24px 0 12px;
}

.study-history {
  display: block;
  padding: 14px 12px;
  border-radius: 7px;
  color: var(--ink);
  text-decoration: none;
  margin: 4px 0;
}

.study-history.current {
  background: #eae7df;
  box-shadow: inset 3px 0 var(--accent);
}

.study-history strong {
  display: block;
  font-size: 14px;
  line-height: 1.4;
}

.study-history span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-top: 6px;
}

.study-note {
  border-top: 1px solid var(--border);
  margin-top: 24px;
  padding: 20px 8px;
  font-size: 13px;
}

.study-note p {
  color: var(--muted);
  margin-top: 8px;
}

.study-content {
  min-width: 0;
}

.study-card {
  padding: 28px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 20px;
  box-shadow: 0 2px 5px #20202003;
}

.study-card h2 {
  font-size: 20px;
  line-height: 1.3;
  letter-spacing: -.02em;
}

.section-heading {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 24px;
}

.section-heading p,.muted {
  color: var(--muted);
}

.section-heading p {
  font-size: 14px;
  margin-top: 6px;
}

.section-number {
  display: grid;
  place-items: center;
  flex: 0 0 32px;
  height: 32px;
  background: #f2eee8;
  color: var(--accent);
  border-radius: 50%;
  font-size: 12px;
  font-weight: 750;
}

label {
  display: flex;
  flex-direction: column;
  gap: 7px;
  font-size: 13px;
  font-weight: 650;
  margin: 16px 0;
}

input,textarea {
  width: 100%;
  border: 1px solid #bfc5bd;
  border-radius: 6px;
  padding: 11px 12px;
  background: #fff;
  color: var(--ink);
  font: 400 14px/1.5 system-ui,sans-serif;
  min-width: 0;
}

input:focus,textarea:focus {
  outline: 2px solid #d9ab93;
  outline-offset: 2px;
}

textarea {
  resize: vertical;
  min-height: 78px;
}

small,.small {
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}

button,a.secondary {
  font: 650 13px/1.5 system-ui,sans-serif;
  cursor: pointer;
  border-radius: 6px;
  padding: 10px 16px;
  transition: background .15s;
  text-decoration: none;
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 6px;
}

button:disabled {
  opacity: .5;
  cursor: not-allowed;
}

.primary {
  background: var(--accent);
  border: 1px solid var(--accent);
  color: #fff;
}

.primary:hover:not(:disabled) {
  background: #87381f;
}

.secondary {
  background: #fff;
  color: var(--ink);
  border: 1px solid #c9cdc6;
}

.secondary:hover {
  background: #f2f3ee;
}

.text-button {
  border: none;
  background: none;
  color: var(--accent);
  padding: 6px 0;
}

.new-study {
  width: 100%;
}

.study-kind-options {
  display: grid;
  grid-template-columns: repeat(3,minmax(0,1fr));
  gap: 12px;
  margin: 24px 0;
}

.study-kind-options button {
  flex-direction: column;
  align-items: flex-start;
  text-align: left;
  padding: 16px 12px;
  background: #fafaf8;
  border: 1px solid var(--border);
  color: var(--ink);
}

.study-kind-options button.chosen {
  border: 2px solid var(--accent);
  padding: 15px 11px;
  background: #fbf3ed;
}

.study-kind-options strong {
  font-size: 13px;
}

.study-kind-options span {
  font-size: 12px;
  color: var(--muted);
  font-weight: 400;
}

.field-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.screen-size {
  max-width: 360px;
}

.variant-grid.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.variant-input {
  min-width: 0;
}

.source-options {
  display: flex;
  gap: 20px;
}

.source-options label {
  flex-direction: row;
  align-items: center;
  font-weight: 450;
  margin: 4px 0 12px;
}

.source-options input {
  width: auto;
  accent-color: var(--accent);
}

.input-preview {
  width: 100%;
  max-height: 240px;
  object-fit: contain;
  border: 1px solid var(--border);
  border-radius: 6px;
}

.persona-editor {
  border: 1px solid var(--border);
  padding: 4px 16px 12px;
  margin-bottom: 20px;
  border-radius: 8px;
  min-width: 0;
}

.persona-editor legend {
  font-size: 11px;
  padding: 0 7px;
  color: var(--muted);
  letter-spacing: .04em;
}

.persona-top {
  display: flex;
  gap: 20px;
  align-items: center;
}

.persona-top label {
  flex: 1;
}

.persona-editor textarea {
  min-height: 68px;
}

.context-details {
  border-top: 1px solid var(--border);
  padding: 16px 0;
}

.context-details summary {
  font-size: 13px;
  font-weight: 650;
  cursor: pointer;
}

.run-summary {
  background: #f5f3ee;
  border-radius: 8px;
  padding: 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin: 16px 0;
}

.run-summary p {
  font-size: 12px;
  color: var(--muted);
  margin-top: 5px;
}

.pill {
  background: #f0f1ed;
  color: #566052;
  border: 1px solid #e1e4dc;
  border-radius: 20px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
  display: inline-block;
}

.pill.completed {
  background: #edf5e8;
  color: #3f6432;
}

.pill.failed {
  background: #fff1e7;
  color: #8c4620;
}

.pill.running,.pill.queued {
  background: #f8f0df;
  color: #7b5a13;
}

.run-button {
  width: 100%;
  padding: 14px;
  margin-top: 20px;
}

.study-error {
  background: #fff0e9;
  border-left: 3px solid #b95833;
  padding: 14px 16px;
  margin-bottom: 20px;
  font-size: 14px;
  overflow-wrap: anywhere;
}

.overview-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 14px;
}

.study-overview>p {
  font-size: 13px;
}

.study-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 20px 0;
}

.study-overview progress {
  width: 100%;
  height: 7px;
  margin: 18px 0 8px;
  accent-color: #708963;
}

.run-status {
  padding: 10px 0;
}

.report-card h2 {
  font-size: 25px;
}

.report-summary {
  font-size: 16px;
  line-height: 1.7;
  margin: 18px 0 24px;
  max-width: 70ch;
}

.finding {
  border-top: 1px solid var(--border);
  padding: 24px 0;
}

.finding-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px;
}

.finding h3 {
  font-size: 17px;
  line-height: 1.4;
}

.finding>p {
  font-size: 14px;
  color: #535b50;
}

.severity {
  font-size: 11px;
  text-transform: uppercase;
  color: #6b6e61;
  letter-spacing: .05em;
  padding-top: 4px;
}

.severity.high {
  color: #a4472b;
}

.finding blockquote {
  border-left: 3px solid #d7dacd;
  background: #f8f9f5;
  padding: 12px 16px;
  margin: 14px 0;
  font-size: 13px;
}

.finding blockquote a {
  font-size: 11px;
  color: var(--accent);
  display: inline-block;
  margin-top: 6px;
}

.next-step,.control-note {
  background: #f5f3ee;
  padding: 18px;
  border-radius: 8px;
  font-size: 14px;
  margin-top: 16px;
}

.next-step p {
  margin-top: 7px;
}

.frozen-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit,minmax(220px,1fr));
  gap: 20px;
  margin: 24px 0;
}

.frozen-grid img {
  width: 100%;
  max-height: 420px;
  object-fit: contain;
  background: #fafafa;
  border: 1px solid var(--border);
  border-radius: 5px;
}

.frozen-grid figcaption strong,.frozen-grid figcaption span,.frozen-grid figcaption small {
  display: block;
  overflow-wrap: anywhere;
}

.frozen-grid figcaption {
  font-size: 13px;
  margin-top: 10px;
}

.frozen-grid figcaption span {
  font-size: 12px;
  color: var(--muted);
}

.capture-note {
  font-size: 12px;
  color: #8c5d33;
  background: #fff6e9;
  padding: 10px;
  margin-top: 10px;
}

.study-hypothesis {
  font-size: 14px;
  padding-top: 16px;
}

.session-details {
  border-top: 1px solid var(--border);
  padding: 18px 0;
  scroll-margin-top: 25px;
}

.evidence-card>p {
  font-size: 14px;
  margin: 8px 0 24px;
}

.session-details>summary {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  cursor: pointer;
  align-items: center;
}

.session-details>summary strong {
  font-size: 14px;
}

.session-details summary small {
  display: block;
  margin-top: 4px;
}

.answers {
  margin: 24px 0;
}

.answers dt {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .07em;
  color: var(--muted);
  margin-top: 16px;
}

.answers dd {
  font-size: 14px;
  margin-top: 6px;
  line-height: 1.65;
}

.answers ul {
  padding-left: 20px;
}

.screen-observation {
  font-size: 13px;
  margin: 10px 0;
}

.raw-response {
  padding-top: 18px;
}

.raw-response summary {
  font-size: 12px;
  cursor: pointer;
  color: var(--muted);
}

pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: 12px/1.7 ui-monospace,monospace;
  background: #f7f7f3;
  padding: 16px;
  border-radius: 7px;
  margin-top: 14px;
  max-height: 450px;
  overflow: auto;
}

.saved-persona {
  font-size: 13px;
  margin-top: 20px;
}

.saved-persona p {
  margin-top: 5px;
}

.trace-step {
  font-size: 13px;
  margin-top: 18px;
}

.trace-step img {
  width: 100%;
  border: 1px solid var(--border);
  margin-top: 12px;
}

.trace-step summary {
  cursor: pointer;
}

.budget-control {
  display: flex;
  align-items: end;
  gap: 12px;
}

.budget-control button {
  margin-bottom: 16px;
}

.task-outcome {
  margin-top: 24px;
  font-size: 14px;
}

@media(max-width:960px) {
  .study-layout {
    grid-template-columns: 190px minmax(0,1fr);
    gap: 20px;
  }
  .study-card {
    padding: 24px;
  }
  .study-kind-options {
    grid-template-columns: 1fr;
  }
  .study-kind-options button {
    padding: 12px 14px;
  }
  .study-kind-options button.chosen {
    padding: 11px 13px;
  }
  .variant-grid.two {
    grid-template-columns: 1fr;
  }
  .study-intro {
    margin-bottom: 24px;
  }
}

@media(max-width:640px) {
  .study-nav {
    padding: 16px 20px;
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }
  .study-nav>div {
    gap: 20px;
  }
  .study-main {
    padding: 28px 16px 56px;
  }
  .study-intro>p:last-child {
    font-size: 15px;
  }
  .study-layout {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  .study-sidebar {
    display: flex;
    flex-direction: column;
  }
  .study-sidebar h2 {
    margin-top: 16px;
  }
  .study-history {
    padding: 10px;
  }
  .study-note {
    display: none;
  }
  .study-sidebar .new-study {
    width: auto;
    align-self: flex-start;
  }
  .study-card {
    padding: 20px 16px;
    border-radius: 9px;
  }
  .section-heading {
    gap: 10px;
  }
  .study-card h2 {
    font-size: 19px;
  }
  .field-pair {
    gap: 12px;
  }
  .run-summary {
    flex-direction: column;
    align-items: flex-start;
  }
  .frozen-grid {
    grid-template-columns: 1fr;
  }
  .overview-top {
    gap: 12px;
  }
  .study-actions>* {
    flex: 1 1 45%;
    font-size: 12px;
    text-align: center;
  }
  .finding-top {
    gap: 12px;
  }
  .finding h3 {
    font-size: 16px;
  }
  .study-overview>p {
    overflow-wrap: anywhere;
  }
  .study-brand {
    font-size: 14px;
  }
}
</style>
