<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const activeTab = ref('ask')
const authUser = ref(null)
const authLoading = ref(true)
const loginUsername = ref('')
const loginPassword = ref('')
const loginBusy = ref(false)
const loginError = ref('')
const sessionNotice = ref('')
const authMode = ref('login')
const registerPassword = ref('')
const registerConfirmPassword = ref('')
const question = ref('')
const busy = ref(false)
const job = ref(null)
const answer = ref(null)
const error = ref('')
const documents = ref([])
const codexHealth = ref(null)
const evidenceDetail = ref(null)
const syncBusy = ref(false)
const syncReport = ref(null)
const monitorOverview = ref(null)
const monitorUsers = ref([])
const monitorSessions = ref({ items: [], total: 0, page: 1, pageSize: 20 })
const monitorQuestions = ref({ items: [], total: 0, page: 1, pageSize: 20 })
const monitorBusy = ref(false)
const monitorDetail = ref(null)
const monitorToday = new Date()
const monitorWeekStart = new Date(monitorToday)
monitorWeekStart.setDate(monitorToday.getDate() - 6)
const monitorFilters = ref({
  username: '', sessionStatus: '', questionStatus: '', keyword: '',
  dateFrom: monitorWeekStart.toLocaleDateString('en-CA'),
  dateTo: monitorToday.toLocaleDateString('en-CA')
})
const markdownRoot = ref(null)
const mermaidPreview = ref(null)
const isAdmin = computed(() => authUser.value?.role === 'admin')
const statusLabel = computed(() => ({
  answered: '答复完成', partial: '部分答复', unresolved: '尚未解决', insufficient_evidence: '证据不足', system_error: '处理失败'
}[answer.value?.status] || '等待提问'))
const isReport = computed(() => answer.value?.schemaVersion === '3.0')
const isWorkProduct = computed(() => answer.value?.schemaVersion === '4.0')
const isConcise = computed(() => answer.value?.schemaVersion === '5.0')
const isDynamicTable = computed(() => answer.value?.schemaVersion === '6.0')
const isMarkdown = computed(() => answer.value?.schemaVersion === '7.0')
const renderedMarkdown = computed(() => DOMPurify.sanitize(marked.parse(answer.value?.markdown || '', {
  gfm: true,
  breaks: false
})))
const monitorDetailMarkdown = computed(() => DOMPurify.sanitize(marked.parse(
  monitorDetail.value?.answer?.markdown || '', { gfm: true, breaks: false }
)))

let mermaidRenderRun = 0
let mermaidInstance = null

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function downloadMermaidSource(source, filename) {
  saveBlob(new Blob([source], { type: 'text/plain;charset=utf-8' }), filename)
}

async function downloadMermaidPng(svgMarkup, filename) {
  const parser = new DOMParser()
  const svgDocument = parser.parseFromString(svgMarkup, 'image/svg+xml')
  const svgElement = svgDocument.documentElement
  if (svgElement.nodeName.toLowerCase() !== 'svg') throw new Error('图示内容无法转换为图片')
  svgElement.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  const viewBox = (svgElement.getAttribute('viewBox') || '').split(/\s+/).map(Number)
  const rawWidth = viewBox.length === 4 && viewBox[2] > 0
    ? viewBox[2] : Number.parseFloat(svgElement.getAttribute('width')) || 1200
  const rawHeight = viewBox.length === 4 && viewBox[3] > 0
    ? viewBox[3] : Number.parseFloat(svgElement.getAttribute('height')) || 800
  const scale = Math.min(2, 8000 / rawWidth, 8000 / rawHeight)
  const width = Math.max(1, Math.round(rawWidth * scale))
  const height = Math.max(1, Math.round(rawHeight * scale))
  const serialized = new XMLSerializer().serializeToString(svgElement)
  const svgUrl = URL.createObjectURL(new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' }))
  try {
    const image = new Image()
    image.src = svgUrl
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) throw new Error('浏览器不支持图片导出')
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, width, height)
    context.drawImage(image, 0, 0, width, height)
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'))
    if (!blob) throw new Error('图片生成失败')
    saveBlob(blob, filename)
  } finally {
    URL.revokeObjectURL(svgUrl)
  }
}

function openMermaidPreview(svgMarkup, source, baseName) {
  const document = new DOMParser().parseFromString(svgMarkup, 'image/svg+xml')
  const svg = document.documentElement
  const viewBox = (svg.getAttribute('viewBox') || '').split(/\s+/).map(Number)
  const naturalWidth = viewBox.length === 4 && viewBox[2] > 0
    ? viewBox[2] : Number.parseFloat(svg.getAttribute('width')) || 1000
  mermaidPreview.value = {
    svg: svgMarkup,
    source,
    baseName,
    previewWidth: Math.min(5000, Math.max(1200, Math.round(naturalWidth * 1.4)))
  }
}

function exportMermaidPng(svgMarkup, filename) {
  downloadMermaidPng(svgMarkup, filename).catch(err => {
    error.value = err instanceof Error ? `图示下载失败：${err.message}` : '图示下载失败'
  })
}

function handleMermaidPreviewKey(event) {
  if (event.key === 'Escape' && mermaidPreview.value) mermaidPreview.value = null
}
async function loadMermaid() {
  if (mermaidInstance) return mermaidInstance
  const module = await import('mermaid')
  mermaidInstance = module.default
  mermaidInstance.initialize({
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'neutral',
    flowchart: { htmlLabels: false },
    sequence: { useMaxWidth: true }
  })
  return mermaidInstance
}

async function renderMermaidDiagrams() {
  const run = ++mermaidRenderRun
  await nextTick()
  if (run !== mermaidRenderRun || !markdownRoot.value) return
  const blocks = markdownRoot.value.querySelectorAll('pre code.language-mermaid, pre code.lang-mermaid')
  if (!blocks.length) return
  const mermaid = await loadMermaid()
  if (run !== mermaidRenderRun) return
  for (const [index, block] of blocks.entries()) {
    const pre = block.closest('pre')
    if (!pre) continue
    try {
      const source = block.textContent || ''
      const id = `mermaid-${Date.now()}-${run}-${index}`
      const { svg } = await mermaid.render(id, source)
      if (run !== mermaidRenderRun) return
      const sanitizedSvg = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } })
      const baseName = `法规助手-时序图-${index + 1}`
      const card = document.createElement('figure')
      card.className = 'mermaid-card'
      const toolbar = document.createElement('figcaption')
      toolbar.className = 'mermaid-toolbar'
      const title = document.createElement('span')
      title.textContent = '时序图'
      toolbar.appendChild(title)
      const actions = document.createElement('div')
      const previewButton = document.createElement('button')
      previewButton.type = 'button'
      previewButton.textContent = '查看大图'
      previewButton.addEventListener('click', () => openMermaidPreview(sanitizedSvg, source, baseName))
      const imageButton = document.createElement('button')
      imageButton.type = 'button'
      imageButton.textContent = '下载图片'
      imageButton.addEventListener('click', () => exportMermaidPng(sanitizedSvg, `${baseName}.png`))
      const sourceButton = document.createElement('button')
      sourceButton.type = 'button'
      sourceButton.textContent = '下载源码'
      sourceButton.addEventListener('click', () => downloadMermaidSource(source, `${baseName}.mmd`))
      actions.append(previewButton, imageButton, sourceButton)
      toolbar.appendChild(actions)
      const diagram = document.createElement('div')
      diagram.className = 'mermaid-diagram'
      diagram.setAttribute('role', 'img')
      diagram.setAttribute('aria-label', '答复图示')
      diagram.innerHTML = sanitizedSvg
      card.append(toolbar, diagram)
      pre.replaceWith(card)
    } catch (err) {
      pre.classList.add('mermaid-error-source')
      const note = document.createElement('div')
      note.className = 'mermaid-error-note'
      note.textContent = isAdmin.value && err instanceof Error ? `图表渲染失败：${err.message}` : '图示暂无法显示'
      pre.before(note)
    }
  }
}

watch(renderedMarkdown, renderMermaidDiagrams, { flush: 'post' })

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(options.headers || {}) },
    ...options
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const detail = payload.detail
    const code = typeof detail === 'object' ? detail?.code : ''
    const message = typeof detail === 'object' ? detail?.message : detail
    if (response.status === 401 && path !== '/api/auth/login') {
      authUser.value = null
      activeTab.value = 'ask'
      answer.value = null
      job.value = null
      documents.value = []
      codexHealth.value = null
      sessionNotice.value = message || '登录已失效，请重新登录'
    }
    const requestError = new Error(message || `请求失败：${response.status}`)
    requestError.code = code
    throw requestError
  }
  if (response.status === 204) return null
  return response.json()
}

async function initializeAuthenticatedView() {
  if (!isAdmin.value) return
  await Promise.all([loadDocuments(), loadHealth()])
}

async function login() {
  if (!loginUsername.value.trim() || !loginPassword.value || loginBusy.value) return
  loginBusy.value = true
  loginError.value = ''
  try {
    const result = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username: loginUsername.value.trim(), password: loginPassword.value })
    })
    authUser.value = result.user
    loginPassword.value = ''
    sessionNotice.value = ''
    await initializeAuthenticatedView()
  } catch (err) {
    loginError.value = err.message
  } finally {
    loginBusy.value = false
  }
}

function switchAuthMode(mode) {
  authMode.value = mode
  loginError.value = ''
  sessionNotice.value = ''
  loginPassword.value = ''
  registerPassword.value = ''
  registerConfirmPassword.value = ''
}

async function registerAccount() {
  const username = loginUsername.value.trim()
  if (loginBusy.value) return
  if (username.length < 2 || username.length > 32) {
    loginError.value = '用户名长度必须为2至32位'
    return
  }
  const usernamePattern = /^[\p{Script=Han}A-Za-z0-9](?:[\p{Script=Han}A-Za-z0-9_.-]*[\p{Script=Han}A-Za-z0-9])$/u
  if (!usernamePattern.test(username)) {
    loginError.value = '用户名只能包含中文、英文字母、数字、下划线、短横线和点，且首尾不能是符号'
    return
  }
  if (registerPassword.value.length < 8 || registerPassword.value.length > 128) {
    loginError.value = '密码长度必须为8至128位'
    return
  }
  if (registerPassword.value !== registerConfirmPassword.value) {
    loginError.value = '两次输入的密码不一致'
    return
  }
  loginBusy.value = true
  loginError.value = ''
  try {
    const result = await api('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        username,
        password: registerPassword.value,
        confirm_password: registerConfirmPassword.value
      })
    })
    authMode.value = 'login'
    loginUsername.value = result.username
    registerPassword.value = ''
    registerConfirmPassword.value = ''
    sessionNotice.value = result.message || '注册成功，请登录'
  } catch (err) {
    loginError.value = err.message
  } finally {
    loginBusy.value = false
  }
}

async function logout() {
  try {
    await api('/api/auth/logout', { method: 'POST' })
  } catch (_) { /* local state is still cleared */ }
  authUser.value = null
  activeTab.value = 'ask'
  answer.value = null
  job.value = null
  documents.value = []
  codexHealth.value = null
  loginPassword.value = ''
  sessionNotice.value = '已安全退出'
}

async function ask() {
  if (!question.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  answer.value = null
  job.value = null
  try {
    const accepted = await api('/api/questions', {
      method: 'POST',
      body: JSON.stringify({
        question: question.value.trim()
      })
    })
    await followJob(accepted.job_id)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function pollJob(id) {
  for (let count = 0; count < 900; count += 1) {
    job.value = await api(`/api/questions/${id}`)
    if (job.value.status === 'completed') {
      answer.value = job.value.answer
      return
    }
    if (job.value.status === 'failed') throw new Error(job.value.error || '任务执行失败')
    await new Promise(resolve => setTimeout(resolve, 700))
  }
  throw new Error('等待答案超时')
}

async function followJob(id) {
  job.value = { id, status: 'queued', events: [] }
  if (typeof EventSource !== 'undefined') {
    await new Promise(resolve => {
      const stream = new EventSource(`/api/questions/${id}/events`)
      const timeout = setTimeout(() => { stream.close(); resolve() }, 490000)
      stream.onmessage = event => {
        try {
          const item = JSON.parse(event.data)
          job.value = { ...(job.value || {}), events: [...(job.value?.events || []), item], status: item.stage || job.value?.status }
        } catch (_) { /* polling below remains authoritative */ }
      }
      stream.addEventListener('done', () => { clearTimeout(timeout); stream.close(); resolve() })
      stream.addEventListener('auth', event => {
        try {
          const detail = JSON.parse(event.data)
          sessionNotice.value = detail.message || '登录已失效，请重新登录'
        } catch (_) { sessionNotice.value = '登录已失效，请重新登录' }
        authUser.value = null
        clearTimeout(timeout)
        stream.close()
        resolve()
      })
      stream.onerror = () => { clearTimeout(timeout); stream.close(); resolve() }
    })
  }
  await pollJob(id)
}

async function showEvidence(citation) {
  evidenceDetail.value = await api(`/api/evidence/${citation.evidenceId}`)
}

async function showReportSource(source) {
  if (source.origin === 'local') {
    evidenceDetail.value = await api(`/api/evidence/${source.sourceId}`)
    evidenceDetail.value.origin = 'local'
    evidenceDetail.value.displaySource = source
  } else {
    evidenceDetail.value = { ...source, origin: 'web', text: source.quote }
  }
}

async function loadDocuments() {
  documents.value = await api('/api/documents')
}

async function loadHealth() {
  codexHealth.value = await api('/api/system/codex-health')
}

async function syncLocalDocuments() {
  syncBusy.value = true
  error.value = ''
  try {
    syncReport.value = await api('/api/admin/sync-local', { method: 'POST' })
    await loadDocuments()
  } catch (err) {
    error.value = err.message
  } finally {
    syncBusy.value = false
  }
}

function queryString(values) {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== '' && value != null) params.set(key, value)
  })
  return params.toString()
}

async function loadMonitor(options = {}) {
  if (!isAdmin.value || monitorBusy.value) return
  monitorBusy.value = true
  try {
    const sessionPage = options.sessionPage || monitorSessions.value.page || 1
    const questionPage = options.questionPage || monitorQuestions.value.page || 1
    const common = {
      username: monitorFilters.value.username,
      date_from: monitorFilters.value.dateFrom,
      date_to: monitorFilters.value.dateTo
    }
    const [overview, users, sessions, questions] = await Promise.all([
      api('/api/admin/monitor/overview'),
      api('/api/admin/monitor/users'),
      api(`/api/admin/monitor/sessions?${queryString({
        ...common, status: monitorFilters.value.sessionStatus, page: sessionPage, page_size: 20
      })}`),
      api(`/api/admin/monitor/questions?${queryString({
        ...common, status: monitorFilters.value.questionStatus, keyword: monitorFilters.value.keyword,
        page: questionPage, page_size: 20
      })}`)
    ])
    monitorOverview.value = overview
    monitorUsers.value = users
    monitorSessions.value = sessions
    monitorQuestions.value = questions
  } catch (err) {
    error.value = err.message
  } finally {
    monitorBusy.value = false
  }
}

async function openMonitorDetail(jobId) {
  try {
    monitorDetail.value = await api(`/api/admin/monitor/questions/${jobId}`)
  } catch (err) {
    error.value = err.message
  }
}

async function revokeMonitoredSession(session) {
  if (session.id === authUser.value?.session_id) return
  if (!window.confirm(`确认下线 ${session.username} 的这个登录会话吗？`)) return
  try {
    await api(`/api/admin/monitor/sessions/${session.id}/revoke`, { method: 'POST' })
    await loadMonitor()
  } catch (err) {
    error.value = err.message
  }
}

function formatDateTime(value) {
  if (!value) return '未记录'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function monitorStatusLabel(status) {
  return ({
    online: '在线', inactive: '离开', active: '有效', revoked: '已下线', expired: '已过期',
    queued: '排队中', retrieving: '检索中', assessing: '分析中', web_searching: '网络搜索',
    generating: '生成中', verifying: '核验中', completed: '已完成', failed: '失败'
  })[status] || status || '未知'
}

function answerSources(detail) {
  const answer = detail?.answer || {}
  return [...(answer.sources || []), ...(answer.webSources || [])]
}

let monitorTimer = null
function startMonitorRefresh() {
  stopMonitorRefresh()
  monitorTimer = window.setInterval(() => {
    if (activeTab.value === 'monitor') loadMonitor()
  }, 30000)
}

function stopMonitorRefresh() {
  if (monitorTimer) window.clearInterval(monitorTimer)
  monitorTimer = null
}

watch(activeTab, value => {
  if (value === 'monitor' && isAdmin.value) {
    loadMonitor()
    startMonitorRefresh()
  } else {
    stopMonitorRefresh()
    monitorDetail.value = null
  }
})

function sourceLabel(source) {
  if (source.origin === 'web') return 'Web来源'
  return '本地文件'
}

function paragraphNatureLabel(nature) {
  return ({ legal_analysis: '法规分析', process_explanation: '流程解释', implementation_advice: '实施建议', uncertainty: '不确定事项' })[nature] || nature
}

function basisLabel(basis) {
  return ({
    regulatory_requirement: '法规直接要求',
    official_interpretation: '官方解释',
    standard_guidance: '本地标准指导',
    engineering_recommendation: '工程建议 · 非强制',
    uncertainty: '尚待确认'
  })[basis] || basis
}

function blockTypeLabel(type) {
  return ({
    deep_analysis: '深度解释', comparison_table: '对比表', field_matrix: '字段矩阵',
    responsibility_matrix: '责任矩阵', process: '实施流程', checklist: '验收清单',
    decision_rules: '决策规则', example: '示意例子', common_mistakes: '常见错误'
  })[type] || type
}

function priorityLabel(priority) {
  return ({ high: '高优先级', medium: '中优先级', low: '低优先级' })[priority] || ''
}

function formatDuration(milliseconds) {
  if (milliseconds == null || Number.isNaN(Number(milliseconds))) return ''
  const value = Number(milliseconds)
  return value >= 1000 ? `${(value / 1000).toFixed(1)}秒` : `${value.toFixed(1)}毫秒`
}

function traceTimingLabel(item) {
  const parts = []
  if (item.modelThinkingMs != null) parts.push(`调用前模型思考 ${formatDuration(item.modelThinkingMs)}`)
  if (item.durationMs != null) {
    parts.push(`${['web', 'verification'].includes(item.type) ? '阶段耗时' : '工具执行'} ${formatDuration(item.durationMs)}`)
  }
  if (item.finalAnswerMs != null) parts.push(`最后一次工具后生成 ${formatDuration(item.finalAnswerMs)}`)
  return parts.join(' · ')
}

function sourcesFor(refs = []) {
  const wanted = new Set(refs)
  return (answer.value?.sources || []).filter(source => wanted.has(source.sourceId))
}

function applicabilityLabel(status) {
  return ({ current: '当前有效', future: '已公布 · 待生效', unknown: '效力日期未明确' })[status] || '效力日期未明确'
}

function applicabilityClass(status) {
  return `applicability applicability-${status || 'unknown'}`
}

onMounted(async () => {
  window.addEventListener('keydown', handleMermaidPreviewKey)
  try {
    authUser.value = await api('/api/auth/me')
    await initializeAuthenticatedView()
  } catch (err) {
    if (authUser.value) error.value = err.message
  } finally {
    authLoading.value = false
  }
})
onUnmounted(() => {
  stopMonitorRefresh()
  window.removeEventListener('keydown', handleMermaidPreviewKey)
})
</script>

<template>
  <div v-if="authLoading" class="auth-screen">
    <div class="auth-loading"><span class="brand-mark">法</span><p>正在验证登录状态…</p></div>
  </div>

  <div v-else-if="!authUser" class="auth-screen">
    <form class="login-card" @submit.prevent="authMode === 'login' ? login() : registerAccount()">
      <div class="login-brand"><span class="brand-mark">法</span><div><strong>法规助手</strong><small>{{ authMode === 'login' ? '内网账号登录' : '创建问答账号' }}</small></div></div>
      <h1>{{ authMode === 'login' ? '登录系统' : '注册账号' }}</h1>
      <p>{{ authMode === 'login' ? '请输入账号和密码。' : '注册成功后，即可使用法规问答。' }}</p>
      <div v-if="sessionNotice" class="login-notice">{{ sessionNotice }}</div>
      <div v-if="loginError" class="login-error">{{ loginError }}</div>
      <label for="login-username">账号</label>
      <input id="login-username" v-model="loginUsername" autocomplete="username" autofocus />
      <template v-if="authMode === 'login'">
        <label for="login-password">密码</label>
        <input id="login-password" v-model="loginPassword" type="password" autocomplete="current-password" />
      </template>
      <template v-else>
        <p class="register-hint">2～32位，可使用中英文、数字、下划线、短横线和点。</p>
        <label for="register-password">密码</label>
        <input id="register-password" v-model="registerPassword" type="password" autocomplete="new-password" />
        <label for="register-confirm-password">确认密码</label>
        <input id="register-confirm-password" v-model="registerConfirmPassword" type="password" autocomplete="new-password" />
        <p class="register-hint">密码长度为8～128位。</p>
      </template>
      <button class="primary" type="submit" :disabled="loginBusy || !loginUsername.trim() || (authMode === 'login' ? !loginPassword : !registerPassword || !registerConfirmPassword)">
        {{ loginBusy ? (authMode === 'login' ? '正在登录…' : '正在注册…') : (authMode === 'login' ? '登录' : '注册') }}
      </button>
      <button class="auth-mode-switch" type="button" @click="switchAuthMode(authMode === 'login' ? 'register' : 'login')">
        {{ authMode === 'login' ? '没有账号？立即注册' : '已有账号？返回登录' }}
      </button>
    </form>
  </div>

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand-mark">法</div>
      <div class="brand-copy">
        <strong>法规助手</strong>
        <span>Regulatory Knowledge Service</span>
      </div>
      <nav>
        <button :class="{ active: activeTab === 'ask' }" @click="activeTab = 'ask'">
          <span>⌁</span>法规问答
        </button>
        <button v-if="isAdmin" :class="{ active: activeTab === 'library' }" @click="activeTab = 'library'; loadDocuments()">
          <span>▤</span>本地资料库
        </button>
        <button v-if="isAdmin" :class="{ active: activeTab === 'monitor' }" @click="activeTab = 'monitor'">
          <span>◎</span>使用监控
        </button>
        <button v-if="isAdmin" :class="{ active: activeTab === 'system' }" @click="activeTab = 'system'; loadHealth()">
          <span>◉</span>运行状态
        </button>
      </nav>
      <div class="sidebar-foot">
        <div class="account-avatar">{{ authUser.username.slice(0, 1).toUpperCase() }}</div>
        <div>
          <b>{{ authUser.username }}</b>
          <small>{{ isAdmin ? '管理员' : '法规问答用户' }}</small>
        </div>
        <button class="logout-button" @click="logout">退出</button>
      </div>
    </aside>

    <main>
      <header class="topbar">
        <div>
          <p class="eyebrow">服务商合规工作台</p>
          <h1>{{ activeTab === 'ask' ? '法规问题咨询' : activeTab === 'library' ? '本地资料库' : activeTab === 'monitor' ? '使用监控' : 'Codex 运行状态' }}</h1>
        </div>
        <div class="principle"><span>✓</span> 无证据，不下确定性结论</div>
      </header>

      <div v-if="error" class="error-banner">{{ error }}</div>

      <section v-if="activeTab === 'ask'" class="workspace">
        <div class="question-panel">
          <label for="question">请输入问题</label>
          <textarea id="question" v-model="question" rows="5" @keydown.ctrl.enter="ask"></textarea>
          <div class="question-actions">
            <button class="primary" :disabled="busy" @click="ask">
              {{ busy ? '正在处理…' : '提交问题' }}
            </button>
          </div>
          <div v-if="isAdmin && job?.events?.length" class="progress-strip">
            <div v-for="event in job.events" :key="event.at" class="progress-item">
              <span></span>{{ event.message }}
            </div>
          </div>
          <div v-if="!isAdmin && busy" class="generic-progress" role="progressbar" aria-label="正在处理问题">
            <div class="generic-progress-track"><span></span></div>
            <p>正在处理，请稍候</p>
          </div>
        </div>

        <div v-if="!answer && !busy" class="empty-state">
          <div class="empty-icon">§</div>
          <h2>提出你的法规问题</h2>
          <p>系统将结合现有资料整理答复，并提供可核对的参考依据。</p>
          <div class="rule-grid">
            <article><b>内容充分</b><span>围绕问题整理相关要求与说明</span></article>
            <article><b>依据清晰</b><span>保留引用资料和原文核对入口</span></article>
            <article><b>直接易读</b><span>根据问题选择合适的答复结构</span></article>
          </div>
        </div>

        <div v-if="answer" class="answer-layout">
          <div class="answer-main">
            <template v-if="isMarkdown">
              <div class="answer-heading markdown-heading">
                <div class="report-heading-row">
                  <span class="answer-status" :class="`answer-${answer.status}`">{{ statusLabel }}</span>
                  <span v-if="isAdmin" class="report-version">原生 Markdown · v7.0</span>
                </div>
                <div v-if="answer.timeContext" class="time-basis concise-time">
                  <b>法规时点：{{ answer.timeContext.asOfDate }}</b>
                  <span>{{ answer.timeContext.includesFutureRules ? '同时识别已公布待生效规则' : '仅使用当日有效规则' }}</span>
                </div>
              </div>

              <article ref="markdownRoot" class="answer-section markdown-body" v-html="renderedMarkdown"></article>

              <section v-if="answer.timeContext?.notices?.length" class="future-notice concise-notices">
                <h2>法规时点提示</h2>
                <article v-for="notice in answer.timeContext.notices" :key="notice.documentId">
                  <span :class="applicabilityClass(notice.applicabilityStatus)">{{ applicabilityLabel(notice.applicabilityStatus) }}</span>
                  <div><b>{{ notice.documentTitle }}</b><p>{{ notice.note }}</p></div>
                </article>
              </section>

              <section class="answer-section concise-basis">
                <details>
                  <summary>{{ isAdmin ? `工具调用记录（读取${answer.sources?.length || 0}个本地来源）` : `参考资料（${answer.sources?.length || 0}项）` }}</summary>
                  <div class="concise-source-list">
                    <button v-for="source in answer.sources" :key="source.sourceId" class="directory-source" @click="showReportSource(source)">
                      <span class="source-origin" :class="`origin-${source.origin}`">{{ sourceLabel(source) }}</span>
                      <span><b>{{ source.title }}</b><small>{{ source.article || '条款未识别' }}{{ source.page ? ` · 第${source.page}页` : '' }}</small></span>
                    </button>
                    <p v-if="!answer.sources?.length" class="muted">当前答复没有可展示的参考资料。</p>
                  </div>
                  <div v-if="isAdmin && answer.researchTrace?.length" class="trace-list concise-trace">
                    <article v-for="(item, index) in answer.researchTrace" :key="index">
                      <span>{{ item.type === 'local' ? (item.action === 'read' ? `原文阅读${item.round}` : `本地搜索${item.round}`) : 'Web搜索' }}</span>
                      <div><b>{{ item.decision }}</b><small v-if="traceTimingLabel(item)" class="trace-timing">{{ traceTimingLabel(item) }}</small></div>
                    </article>
                  </div>
                </details>
              </section>
            </template>

            <template v-else-if="isDynamicTable">
              <div class="answer-heading concise-heading dynamic-heading">
                <div class="report-heading-row">
                  <span class="answer-status" :class="`answer-${answer.status}`">{{ statusLabel }}</span>
                  <span v-if="isAdmin" class="report-version">动态表格答复 · v6.0</span>
                </div>
                <h2>{{ answer.title }}</h2>
                <article class="concise-conclusion">
                  <p>{{ answer.conclusion.text }}</p>
                  <div class="paragraph-meta">
                    <span class="basis-tag" :class="`basis-${answer.conclusion.basis}`">{{ basisLabel(answer.conclusion.basis) }}</span>
                    <button v-for="source in sourcesFor(answer.conclusion.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ sourceLabel(source) }} · {{ source.article || source.sourceId }}</button>
                  </div>
                </article>
                <div v-if="answer.timeContext" class="time-basis concise-time">
                  <b>法规时点：{{ answer.timeContext.asOfDate }}</b>
                  <span>{{ answer.timeContext.includesFutureRules ? '同时识别已公布待生效规则' : '仅使用当日有效规则' }}</span>
                </div>
              </div>

              <section class="answer-section dynamic-table-section">
                <div class="dynamic-table-wrap">
                  <table class="dynamic-answer-table">
                    <thead>
                      <tr><th v-for="column in answer.table.columns" :key="column">{{ column }}</th><th class="basis-column">依据</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="row in answer.table.rows" :key="row.rowId">
                        <td v-for="(cell, index) in row.cells" :key="index">{{ cell || '—' }}</td>
                        <td class="basis-cell">
                          <span class="basis-tag" :class="`basis-${row.basis}`">{{ basisLabel(row.basis) }}</span>
                          <div class="dynamic-source-list">
                            <button v-for="source in sourcesFor(row.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ source.article || source.sourceId }}</button>
                          </div>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>

              <section v-if="answer.timeContext?.notices?.length" class="future-notice concise-notices">
                <h2>法规时点提示</h2>
                <article v-for="notice in answer.timeContext.notices" :key="notice.documentId">
                  <span :class="applicabilityClass(notice.applicabilityStatus)">{{ applicabilityLabel(notice.applicabilityStatus) }}</span>
                  <div><b>{{ notice.documentTitle }}</b><p>{{ notice.note }}</p></div>
                </article>
              </section>

              <section class="answer-section concise-basis">
                <details>
                  <summary>{{ isAdmin ? `法规依据与检索过程（${answer.sources?.length || 0}个来源）` : `参考资料（${answer.sources?.length || 0}项）` }}</summary>
                  <div class="concise-source-list">
                    <button v-for="source in answer.sources" :key="source.sourceId" class="directory-source" @click="showReportSource(source)">
                      <span class="source-origin" :class="`origin-${source.origin}`">{{ sourceLabel(source) }}</span>
                      <span><b>{{ source.title }}</b><small>{{ source.article || '条款未识别' }}{{ source.page ? ` · 第${source.page}页` : '' }}</small></span>
                    </button>
                    <p v-if="!answer.sources?.length" class="muted">当前结论未使用可核验来源。</p>
                  </div>
                  <div v-if="isAdmin && answer.researchTrace?.length" class="trace-list concise-trace">
                    <article v-for="(item, index) in answer.researchTrace" :key="index">
                      <span>{{ item.type === 'local' ? (item.action === 'read' ? `原文阅读${item.round}` : `本地搜索${item.round}`) : 'Web搜索' }}</span>
                      <div><b>{{ item.decision }}</b><small v-if="traceTimingLabel(item)" class="trace-timing">{{ traceTimingLabel(item) }}</small></div>
                    </article>
                  </div>
                </details>
              </section>
            </template>

            <template v-else-if="isConcise">
              <div class="answer-heading concise-heading">
                <div class="report-heading-row">
                  <span class="answer-status" :class="`answer-${answer.status}`">{{ statusLabel }}</span>
                  <span v-if="isAdmin" class="report-version">简明法规答复 · v5.0</span>
                </div>
                <h2>{{ answer.title }}</h2>
                <article class="concise-conclusion">
                  <p>{{ answer.conclusion.text }}</p>
                  <div class="paragraph-meta">
                    <span class="basis-tag" :class="`basis-${answer.conclusion.basis}`">{{ basisLabel(answer.conclusion.basis) }}</span>
                    <button v-for="source in sourcesFor(answer.conclusion.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ sourceLabel(source) }} · {{ source.article || source.sourceId }}</button>
                  </div>
                </article>
                <div v-if="answer.timeContext" class="time-basis concise-time">
                  <b>法规时点：{{ answer.timeContext.asOfDate }}</b>
                  <span>{{ answer.timeContext.includesFutureRules ? '同时识别已公布待生效规则' : '仅使用当日有效规则' }}</span>
                </div>
              </div>

              <section v-if="answer.explanations?.length" class="answer-section concise-explanations">
                <h2>说明</h2>
                <article v-for="(item, index) in answer.explanations" :key="item.statementId">
                  <span class="concise-index">{{ index + 1 }}</span>
                  <div>
                    <p>{{ item.text }}</p>
                    <div class="paragraph-meta">
                      <span class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</span>
                      <button v-for="source in sourcesFor(item.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ sourceLabel(source) }} · {{ source.article || source.sourceId }}</button>
                    </div>
                  </div>
                </article>
              </section>

              <section v-if="answer.providerAdvice?.length" class="answer-section concise-provider">
                <p class="section-index">服务商视角</p>
                <h2>建议与责任边界</h2>
                <article v-for="item in answer.providerAdvice" :key="item.statementId">
                  <p>{{ item.text }}</p>
                  <div class="paragraph-meta">
                    <span class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</span>
                    <button v-for="source in sourcesFor(item.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ sourceLabel(source) }} · {{ source.article || source.sourceId }}</button>
                  </div>
                </article>
              </section>

              <section v-if="answer.timeContext?.notices?.length" class="future-notice concise-notices">
                <h2>法规时点提示</h2>
                <article v-for="notice in answer.timeContext.notices" :key="notice.documentId">
                  <span :class="applicabilityClass(notice.applicabilityStatus)">{{ applicabilityLabel(notice.applicabilityStatus) }}</span>
                  <div><b>{{ notice.documentTitle }}</b><p>{{ notice.note }}</p></div>
                </article>
              </section>

              <section class="answer-section concise-basis">
                <details>
                  <summary>{{ isAdmin ? `法规依据与检索过程（${answer.sources?.length || 0}个来源）` : `参考资料（${answer.sources?.length || 0}项）` }}</summary>
                  <div class="concise-source-list">
                    <button v-for="source in answer.sources" :key="source.sourceId" class="directory-source" @click="showReportSource(source)">
                      <span class="source-origin" :class="`origin-${source.origin}`">{{ sourceLabel(source) }}</span>
                      <span><b>{{ source.title }}</b><small>{{ source.article || '条款未识别' }}{{ source.page ? ` · 第${source.page}页` : '' }}</small></span>
                    </button>
                    <p v-if="!answer.sources?.length" class="muted">当前结论未使用可核验来源。</p>
                  </div>
                  <div v-if="isAdmin && answer.researchTrace?.length" class="trace-list concise-trace">
                    <article v-for="(item, index) in answer.researchTrace" :key="index">
                      <span>{{ item.type === 'local' ? (item.action === 'read' ? `原文阅读${item.round}` : `本地搜索${item.round}`) : 'Web搜索' }}</span>
                      <div><b>{{ item.decision }}</b><small v-if="traceTimingLabel(item)" class="trace-timing">{{ traceTimingLabel(item) }}</small></div>
                    </article>
                  </div>
                </details>
              </section>
            </template>

            <template v-else-if="isWorkProduct">
              <div class="answer-heading work-heading">
                <div class="report-heading-row">
                  <span class="answer-status" :class="`answer-${answer.status}`">{{ statusLabel }}</span>
                  <span v-if="isAdmin" class="report-version">法规答复 · v4.0</span>
                </div>
                <h2>{{ answer.answerTitle }}</h2>
                <article v-for="item in answer.executiveAnswer" :key="item.statementId" class="work-executive">
                  <p>{{ item.text }}</p>
                  <div class="paragraph-meta">
                    <span class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</span>
                    <button v-for="source in sourcesFor(item.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ sourceLabel(source) }} · {{ source.article || source.sourceId }}</button>
                  </div>
                </article>
                <div v-if="answer.timeContext" class="time-basis">
                  <b>法规时点：{{ answer.timeContext.asOfDate }}</b>
                  <span>{{ answer.timeContext.includesFutureRules ? '包含正式公布、尚未生效的规则' : '仅使用当日有效规则' }}</span>
                  <span>本地来源 {{ answer.sources?.filter(item => item.origin === 'local').length || 0 }} 个 · Web官方来源 {{ answer.sources?.filter(item => item.origin === 'web').length || 0 }} 个</span>
                </div>
              </div>

              <section v-if="answer.keyTakeaways?.length" class="answer-section takeaway-section">
                <p class="section-index">先看这些</p><h2>关键决策要点</h2>
                <div class="takeaway-grid">
                  <article v-for="(item, index) in answer.keyTakeaways" :key="item.statementId">
                    <span>{{ String(index + 1).padStart(2, '0') }}</span><p>{{ item.text }}</p>
                    <div class="paragraph-meta"><i class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</i></div>
                  </article>
                </div>
              </section>

              <section v-if="answer.timeContext?.notices?.length" class="future-notice">
                <h2>法规时点提示</h2>
                <article v-for="notice in answer.timeContext.notices" :key="notice.documentId">
                  <span :class="applicabilityClass(notice.applicabilityStatus)">{{ applicabilityLabel(notice.applicabilityStatus) }}</span>
                  <div><b>{{ notice.documentTitle }}</b><p>{{ notice.note }}</p></div>
                </article>
              </section>

              <section v-for="(block, blockIndex) in answer.contentBlocks" :key="block.blockId" class="answer-section work-block" :class="`block-${block.blockType}`">
                <div class="work-block-heading"><div><p class="section-index">{{ String(blockIndex + 1).padStart(2, '0') }} / {{ blockTypeLabel(block.blockType) }}</p><h2>{{ block.title }}</h2></div><span>{{ block.items.length }}项</span></div>
                <p v-if="block.introduction" class="block-intro">{{ block.introduction }}</p>

                <div v-if="block.columns?.length" class="work-table-wrap">
                  <table class="work-table">
                    <thead><tr><th v-for="column in block.columns" :key="column">{{ column }}</th><th>依据性质</th></tr></thead>
                    <tbody>
                      <tr v-for="item in block.items" :key="item.itemId">
                        <td v-for="(cell, cellIndex) in item.cells" :key="cellIndex">{{ cell }}</td>
                        <td><span class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</span><div class="table-source-list"><button v-for="source in sourcesFor(item.sourceRefs)" :key="source.sourceId" @click="showReportSource(source)">{{ source.article || source.sourceId }}</button></div></td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                <div v-else-if="block.blockType === 'process'" class="process-list">
                  <article v-for="(item, index) in block.items" :key="item.itemId">
                    <div class="process-index">{{ index + 1 }}</div>
                    <div><h3>{{ item.title }}</h3><p>{{ item.detail }}</p><dl><template v-if="item.owner"><dt>责任角色</dt><dd>{{ item.owner }}</dd></template><template v-if="item.input"><dt>输入</dt><dd>{{ item.input }}</dd></template><template v-if="item.output"><dt>输出</dt><dd>{{ item.output }}</dd></template><template v-if="item.acceptanceCriteria"><dt>验收</dt><dd>{{ item.acceptanceCriteria }}</dd></template></dl><div class="paragraph-meta"><span class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</span></div></div>
                  </article>
                </div>

                <div v-else-if="block.blockType === 'checklist'" class="checklist-grid">
                  <article v-for="item in block.items" :key="item.itemId"><span class="check-box">✓</span><div><h3>{{ item.title }}</h3><p>{{ item.detail }}</p><small v-if="item.acceptanceCriteria"><b>验收：</b>{{ item.acceptanceCriteria }}</small><div class="paragraph-meta"><em v-if="item.priority !== 'not_applicable'" :class="`priority-${item.priority}`">{{ priorityLabel(item.priority) }}</em><span class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</span></div></div></article>
                </div>

                <div v-else class="work-card-list">
                  <article v-for="item in block.items" :key="item.itemId"><h3 v-if="item.title">{{ item.title }}</h3><p>{{ item.detail }}</p><pre v-if="block.blockType === 'example' && item.output">{{ item.output }}</pre><div class="paragraph-meta"><span class="basis-tag" :class="`basis-${item.basis}`">{{ basisLabel(item.basis) }}</span><button v-for="source in sourcesFor(item.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ sourceLabel(source) }} · {{ source.article || source.sourceId }}</button></div></article>
                </div>
              </section>

              <section v-if="answer.serviceProviderActions?.length" class="answer-section provider-section action-plan">
                <div class="provider-heading"><div><p>服务商执行视角</p><h2>行动计划与交付物</h2></div><em>按优先级、负责人和依据性质拆分</em></div>
                <article v-for="action in answer.serviceProviderActions" :key="action.actionId" class="action-row">
                  <div><span :class="`priority-pill priority-${action.priority}`">{{ priorityLabel(action.priority) }}</span><span class="impact-kind">{{ action.nature === 'direct_service_provider_duty' ? '直接义务' : action.nature === 'legal_responsibility_boundary' ? '责任边界' : '实施行动' }}</span></div>
                  <div><h3>{{ action.title }}</h3><p>{{ action.detail }}</p><dl><dt>负责人</dt><dd>{{ action.owner }}</dd><dt>交付物</dt><dd>{{ action.deliverable }}</dd></dl><div class="paragraph-meta"><span class="basis-tag" :class="`basis-${action.basis}`">{{ basisLabel(action.basis) }}</span><button v-for="source in sourcesFor(action.sourceRefs)" :key="source.sourceId" class="source-ref" @click="showReportSource(source)">{{ source.article || source.sourceId }}</button></div></div>
                </article>
              </section>

              <section v-if="answer.unresolvedQuestions?.length" class="answer-section unresolved-section">
                <details><summary>仍需补齐的业务问题（{{ answer.unresolvedQuestions.length }}项）</summary><article v-for="item in answer.unresolvedQuestions" :key="item.itemId"><h3>{{ item.question }}</h3><p><b>影响：</b>{{ item.impact }}</p><p><b>需要补充：</b>{{ item.neededEvidence }}</p></article></details>
              </section>

              <section v-if="isAdmin" class="answer-section research-audit">
                <details><summary>研究与质量审计</summary>
                  <div class="audit-columns">
                    <div><h3>检索过程</h3><div class="trace-list"><article v-for="(item, index) in answer.researchTrace" :key="index"><span>{{ item.type === 'local' ? (item.action === 'read' ? `原文阅读${item.round}` : item.action === 'search' ? `本地搜索${item.round}` : `本地第${item.round}轮`) : item.type === 'web' ? 'Web搜索' : '独立审核' }}</span><div><b>{{ item.decision }}</b><small>{{ item.action === 'read' ? '已读取原文' : '有效结果' }}：{{ item.resultCount }}</small><small v-if="traceTimingLabel(item)" class="trace-timing">{{ traceTimingLabel(item) }}</small></div></article></div></div>
                    <div v-if="answer.qualityNotes?.length"><h3>内部质量记录</h3><ul class="quality-list"><li v-for="note in answer.qualityNotes" :key="note.noteId">{{ note.message }}</li></ul></div>
                  </div>
                </details>
              </section>

              <section class="answer-section source-directory">
                <p class="section-index">来源索引</p><h2>本作业包实际使用的来源</h2>
                <button v-for="source in answer.sources" :key="source.sourceId" class="directory-source" @click="showReportSource(source)"><span class="source-origin" :class="`origin-${source.origin}`">{{ sourceLabel(source) }}</span><span><b>{{ source.title }}</b><small>{{ source.article || '条款未识别' }}{{ source.page ? ` · 第${source.page}页` : '' }}</small></span></button>
              </section>
            </template>

            <template v-else-if="isReport">
              <div class="answer-heading report-heading">
                <div class="report-heading-row">
                  <span class="answer-status" :class="`answer-${answer.status}`">{{ statusLabel }}</span>
                  <span v-if="isAdmin" class="report-version">专业研究报告 · v3.0</span>
                </div>
                <h2>直接答复</h2>
                <article v-for="paragraph in answer.executiveSummary" :key="paragraph.paragraphId" class="report-paragraph executive-paragraph">
                  <p>{{ paragraph.text }}</p>
                  <div class="paragraph-meta">
                    <span class="nature-tag">{{ paragraphNatureLabel(paragraph.nature) }}</span>
                    <button v-for="source in sourcesFor(paragraph.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">
                      {{ sourceLabel(source) }} · {{ source.article || source.sourceId }}
                    </button>
                  </div>
                </article>
                <p v-if="!answer.executiveSummary?.length" class="muted">当前研究未形成可由有效来源支撑的直接答复。</p>
                <div v-if="answer.timeContext" class="time-basis">
                  <b>法规时点：{{ answer.timeContext.asOfDate }}</b>
                  <span>{{ answer.timeContext.includesFutureRules ? '包含正式公布、尚未生效的规则' : '仅使用当日有效规则' }}</span>
                  <span>本地来源 {{ answer.sources?.filter(item => item.origin === 'local').length || 0 }} 个 · Web官方来源 {{ answer.sources?.filter(item => item.origin === 'web').length || 0 }} 个</span>
                </div>
              </div>

              <section v-if="answer.timeContext?.notices?.length" class="future-notice">
                <h2>法规时点提示</h2>
                <article v-for="notice in answer.timeContext.notices" :key="notice.documentId">
                  <span :class="applicabilityClass(notice.applicabilityStatus)">{{ applicabilityLabel(notice.applicabilityStatus) }}</span>
                  <div><b>{{ notice.documentTitle }}</b><p>{{ notice.note }}</p></div>
                </article>
              </section>

              <section v-for="(section, sectionIndex) in answer.analysisSections" :key="section.title" class="answer-section report-section">
                <p class="section-index">{{ String(sectionIndex + 1).padStart(2, '0') }} / 详细分析</p>
                <h2>{{ section.title }}</h2>
                <article v-for="paragraph in section.paragraphs" :key="paragraph.paragraphId" class="report-paragraph">
                  <p>{{ paragraph.text }}</p>
                  <div class="paragraph-meta">
                    <span class="nature-tag" :class="`nature-${paragraph.nature}`">{{ paragraphNatureLabel(paragraph.nature) }}</span>
                    <button v-for="source in sourcesFor(paragraph.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">
                      {{ sourceLabel(source) }} · {{ source.article || source.sourceId }}
                    </button>
                  </div>
                </article>
              </section>

              <section v-if="answer.serviceProviderImpact?.length" class="answer-section provider-section report-provider">
                <div class="provider-heading"><div><p>服务商重点</p><h2>服务商职责与系统影响</h2></div><em>直接法定义务与实施推导分开展示</em></div>
                <article v-for="item in answer.serviceProviderImpact" :key="item.itemId" class="provider-impact" :class="`impact-${item.nature}`">
                  <div class="impact-kind">{{ ['direct_legal_duty', 'direct_service_provider_duty'].includes(item.nature) ? '服务商直接法定义务' : item.nature === 'legal_responsibility_boundary' ? '法定责任边界' : '推导实施行动' }}</div>
                  <h3>{{ item.title }}</h3>
                  <p>{{ item.detail }}</p>
                  <div class="paragraph-meta">
                    <button v-for="source in sourcesFor(item.sourceRefs)" :key="source.sourceId" class="source-ref" :class="`origin-${source.origin}`" @click="showReportSource(source)">{{ sourceLabel(source) }} · {{ source.article || source.sourceId }}</button>
                  </div>
                </article>
              </section>

              <section v-if="answer.uncertainties?.length" class="answer-section warning-section report-warning">
                <p class="section-index">研究边界</p><h2>尚不能确认的事项</h2>
                <ul><li v-for="item in answer.uncertainties" :key="item">{{ item }}</li></ul>
              </section>

              <section v-if="isAdmin" class="answer-section research-audit">
                <details>
                  <summary>查看检索过程（{{ answer.researchTrace?.length || 0 }}个步骤）</summary>
                  <div class="trace-list">
                    <article v-for="(item, index) in answer.researchTrace" :key="index">
                      <span>{{ item.type === 'local' ? (item.action === 'read' ? `原文阅读${item.round}` : item.action === 'search' ? `本地搜索${item.round}` : `本地第${item.round}轮`) : item.type === 'web' ? 'Web搜索' : '独立审核' }}</span>
                      <div><b>{{ item.decision }}</b><p v-if="item.queries?.length">检索词：{{ item.queries.join('；') }}</p><small>新增有效结果：{{ item.resultCount }}</small><small v-if="traceTimingLabel(item)" class="trace-timing">{{ traceTimingLabel(item) }}</small></div>
                    </article>
                  </div>
                </details>
              </section>

              <section class="answer-section source-directory">
                <p class="section-index">审计索引</p><h2>本报告使用的来源</h2>
                <button v-for="source in answer.sources" :key="source.sourceId" class="directory-source" @click="showReportSource(source)">
                  <span class="source-origin" :class="`origin-${source.origin}`">{{ sourceLabel(source) }}</span>
                  <span><b>{{ source.title }}</b><small>{{ source.article || '条款未识别' }}{{ source.page ? ` · 第${source.page}页` : '' }}{{ source.accessedAt ? ` · 访问于${source.accessedAt.slice(0, 10)}` : '' }}</small></span>
                </button>
                <p v-if="!answer.sources?.length" class="muted">没有通过校验并实际用于报告的来源。</p>
              </section>
            </template>

            <template v-else>
            <div class="answer-heading">
              <span class="answer-status" :class="`answer-${answer.status}`">{{ statusLabel }}</span>
              <h2>直接结论</h2>
              <p>{{ answer.summary }}</p>
              <div v-if="answer.timeContext" class="time-basis">
                <b>法规时点：{{ answer.timeContext.asOfDate }}</b>
                <span>{{ answer.timeContext.includesFutureRules ? '已纳入正式公布、尚未生效的规则' : '仅使用当日有效规则' }}</span>
              </div>
            </div>

            <section v-if="answer.timeContext?.notices?.length" class="future-notice">
              <h2>即将生效法规</h2>
              <article v-for="notice in answer.timeContext.notices" :key="notice.documentId">
                <span :class="applicabilityClass(notice.applicabilityStatus)">{{ applicabilityLabel(notice.applicabilityStatus) }}</span>
                <div><b>{{ notice.documentTitle }}</b><p>{{ notice.note }}</p></div>
              </article>
            </section>

            <section class="answer-section">
              <h2>一、法规结论</h2>
              <article v-for="claim in answer.claims" :key="claim.claimId" class="claim-card">
                <div class="claim-number">{{ claim.claimId }}</div>
                <div class="claim-body">
                  <h3>{{ claim.text }}</h3>
                  <p v-if="claim.analysis" class="claim-analysis">{{ claim.analysis }}</p>
                  <div class="citation-stack">
                    <article v-for="citation in claim.citations" :key="citation.evidenceId" class="citation-card">
                      <button class="citation-heading" @click="showEvidence(citation)">
                        <span class="source source-local">本地法规文件</span>
                        <span :class="applicabilityClass(citation.applicabilityStatus)">{{ applicabilityLabel(citation.applicabilityStatus) }}</span>
                        <b>{{ citation.documentTitle || citation.documentId }}</b>
                        <small>{{ citation.article || '条款未识别' }} · PDF第{{ citation.page }}页</small>
                      </button>
                      <blockquote>{{ citation.quote }}</blockquote>
                      <p class="citation-interpretation"><b>法规释义：</b>{{ citation.interpretation }}</p>
                    </article>
                  </div>
                </div>
              </article>
              <p v-if="!answer.claims.length" class="muted">没有通过证据审核的确定性论点。</p>
            </section>

            <section v-if="answer.implementationAdvice?.length" class="answer-section provider-section">
              <div class="provider-heading"><div><p>服务商重点</p><h2>二、服务商职责与实施行动</h2></div><em>非直接法定义务会单独标明</em></div>
              <article v-for="(item, index) in answer.implementationAdvice" :key="index" class="advice-card">
                <span>实施建议</span><div><b>{{ item.advice }}</b><p>{{ item.basis }}</p></div>
              </article>
            </section>

            <section v-if="answer.missingEvidence?.length" class="answer-section warning-section">
              <h2>三、不确定事项</h2>
              <ul><li v-for="item in answer.missingEvidence" :key="item">{{ item }}</li></ul>
            </section>
            </template>
          </div>

          <aside class="evidence-panel">
            <template v-if="evidenceDetail">
              <div class="evidence-head">
                <span v-if="evidenceDetail.origin === 'web'" class="source-origin origin-web">{{ sourceLabel(evidenceDetail) }}</span>
                <span v-else class="source source-local">本地法规文件</span>
                <button @click="evidenceDetail = null">×</button>
              </div>
              <h3>{{ evidenceDetail.document_title || evidenceDetail.title }}</h3>
              <p v-if="evidenceDetail.origin !== 'web'" class="evidence-meta">{{ evidenceDetail.article || '未识别条款' }} · 文件第{{ evidenceDetail.page }}页</p>
              <p v-else class="evidence-meta">{{ evidenceDetail.publisher }} · Web来源</p>
              <p v-if="evidenceDetail.effective_date" class="evidence-meta">生效日期：{{ evidenceDetail.effective_date }}</p>
              <blockquote>{{ evidenceDetail.text }}</blockquote>
              <a :href="evidenceDetail.origin === 'web' ? evidenceDetail.url : evidenceDetail.pdfUrl" target="_blank">{{ evidenceDetail.origin === 'web' ? '打开官方网页' : '打开法规原文件' }} ↗</a>
            </template>
            <template v-else>
              <div class="evidence-placeholder">点击结论下方的条款标签，在这里核对法规原文。</div>
            </template>
          </aside>
        </div>
      </section>

      <section v-if="isAdmin && activeTab === 'library'" class="library-page">
        <div class="section-toolbar">
          <div><p class="eyebrow">本地资料</p><h2>本地资料库</h2><p>自动读取本地目录；所有已同步文件不分类型、等级和状态，以相同权重参与检索。</p></div>
          <div class="toolbar-actions">
            <button class="primary" :disabled="syncBusy" @click="syncLocalDocuments">{{ syncBusy ? '正在扫描与提取…' : '同步本地目录' }}</button>
          </div>
        </div>
        <div v-if="syncReport" class="sync-report">
          发现 {{ syncReport.discovered }} 个本地文件；新增 {{ syncReport.imported }} 个；已有 {{ syncReport.unchanged }} 个；失败 {{ syncReport.errors?.length || 0 }} 个。
        </div>
        <div class="document-table">
          <div class="table-row table-head"><span>本地文件</span><span>文本片段</span><span>发布日期</span><span>生效日期</span><span>原文件</span></div>
          <div v-for="document in documents" :key="document.id" class="table-row">
            <span><b>{{ document.title }}</b><small>{{ document.filename }}</small></span>
            <span>{{ document.passage_count }}</span>
            <span>{{ document.publication_date || '—' }}</span>
            <span>{{ document.effective_date || '—' }}</span>
            <span><a class="document-link" :href="`/api/documents/${document.id}/file`" target="_blank">打开 ↗</a></span>
          </div>
          <div v-if="!documents.length" class="table-empty">尚未发现可读取的本地文件。</div>
        </div>
      </section>

      <section v-if="isAdmin && activeTab === 'monitor'" class="monitor-page">
        <div class="section-toolbar">
          <div><p class="eyebrow">管理员专用</p><h2>使用监控</h2><p>查看最近30天的用户登录、在线状态和问题处理记录。</p></div>
          <button class="primary" :disabled="monitorBusy" @click="loadMonitor()">{{ monitorBusy ? '正在刷新…' : '刷新数据' }}</button>
        </div>

        <div class="monitor-metrics">
          <article><span>当前在线</span><b>{{ monitorOverview?.onlineUsers ?? '—' }}</b><small>最近5分钟有活动</small></article>
          <article><span>今日登录</span><b>{{ monitorOverview?.loginsToday ?? '—' }}</b><small>登录会话次数</small></article>
          <article><span>今日问题</span><b>{{ monitorOverview?.questionsToday ?? '—' }}</b><small>所有用户提问</small></article>
          <article><span>完成率</span><b>{{ monitorOverview ? `${monitorOverview.successRate}%` : '—' }}</b><small>平均 {{ monitorOverview ? `${monitorOverview.averageDurationSeconds}秒` : '—' }}</small></article>
        </div>

        <section class="monitor-card">
          <div class="monitor-card-head"><div><h3>用户状态</h3><p>按照最近一次登录和活动时间汇总</p></div><span>每30秒自动刷新</span></div>
          <div class="monitor-table-wrap">
            <table class="monitor-table">
              <thead><tr><th>用户</th><th>状态</th><th>最后活动</th><th>登录IP</th><th>客户端</th><th>问题数</th></tr></thead>
              <tbody>
                <tr v-for="user in monitorUsers" :key="user.id">
                  <td><b>{{ user.username }}</b><small>{{ user.role === 'admin' ? '管理员' : '普通用户' }}</small></td>
                  <td><span class="monitor-status" :class="user.online ? 'status-online' : 'status-offline'">{{ user.online ? '在线' : '离线' }}</span></td>
                  <td>{{ formatDateTime(user.last_seen_at) }}</td>
                  <td>{{ user.login_ip || '未记录' }}</td>
                  <td>{{ [user.browser, user.operating_system, user.device].filter(Boolean).join(' · ') || '未记录' }}</td>
                  <td>{{ user.question_count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <div class="monitor-filters">
          <label>用户<input v-model="monitorFilters.username" placeholder="账号名称" /></label>
          <label>开始日期<input v-model="monitorFilters.dateFrom" type="date" /></label>
          <label>结束日期<input v-model="monitorFilters.dateTo" type="date" /></label>
          <button class="secondary" @click="loadMonitor({ sessionPage: 1, questionPage: 1 })">应用筛选</button>
        </div>

        <section class="monitor-card">
          <div class="monitor-card-head">
            <div><h3>登录记录</h3><p>共 {{ monitorSessions.total }} 条</p></div>
            <select v-model="monitorFilters.sessionStatus" @change="loadMonitor({ sessionPage: 1 })">
              <option value="">全部状态</option><option value="online">在线</option><option value="active">会话有效</option><option value="revoked">已下线</option><option value="expired">已过期</option>
            </select>
          </div>
          <div class="monitor-table-wrap">
            <table class="monitor-table monitor-session-table">
              <thead><tr><th>用户</th><th>登录时间</th><th>最后活动</th><th>状态</th><th>IP</th><th>浏览器 / 设备</th><th>下线原因</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="session in monitorSessions.items" :key="session.id">
                  <td><b>{{ session.username }}</b></td>
                  <td>{{ formatDateTime(session.created_at) }}</td>
                  <td>{{ formatDateTime(session.last_seen_at) }}</td>
                  <td><span class="monitor-status" :class="session.status === 'online' ? 'status-online' : 'status-offline'">{{ monitorStatusLabel(session.status) }}</span></td>
                  <td>{{ session.login_ip || '未记录' }}</td>
                  <td><b>{{ session.browser || '未记录' }}</b><small>{{ [session.operating_system, session.device].filter(Boolean).join(' · ') }}</small><details v-if="session.user_agent"><summary>完整标识</summary><p class="user-agent-text">{{ session.user_agent }}</p></details></td>
                  <td>{{ session.revoke_reason || '—' }}</td>
                  <td><button class="danger-link" :disabled="session.id === authUser.session_id || !['online', 'inactive'].includes(session.status)" @click="revokeMonitoredSession(session)">下线</button></td>
                </tr>
                <tr v-if="!monitorSessions.items.length"><td colspan="8" class="monitor-empty">没有符合条件的登录记录</td></tr>
              </tbody>
            </table>
          </div>
          <div class="monitor-pagination"><button :disabled="monitorSessions.page <= 1" @click="loadMonitor({ sessionPage: monitorSessions.page - 1 })">上一页</button><span>第 {{ monitorSessions.page }} 页</span><button :disabled="monitorSessions.page * monitorSessions.pageSize >= monitorSessions.total" @click="loadMonitor({ sessionPage: monitorSessions.page + 1 })">下一页</button></div>
        </section>

        <section class="monitor-card">
          <div class="monitor-card-head">
            <div><h3>问题记录</h3><p>共 {{ monitorQuestions.total }} 条，点击问题查看完整详情</p></div>
            <div class="monitor-inline-filters">
              <input v-model="monitorFilters.keyword" placeholder="搜索问题内容" @keyup.enter="loadMonitor({ questionPage: 1 })" />
              <select v-model="monitorFilters.questionStatus" @change="loadMonitor({ questionPage: 1 })">
                <option value="">全部状态</option><option value="completed">已完成</option><option value="failed">失败</option><option value="queued">排队中</option><option value="retrieving">处理中</option><option value="generating">生成中</option>
              </select>
            </div>
          </div>
          <div class="monitor-table-wrap">
            <table class="monitor-table monitor-question-table">
              <thead><tr><th>用户</th><th>问题</th><th>状态</th><th>提交时间</th><th>耗时</th></tr></thead>
              <tbody>
                <tr v-for="item in monitorQuestions.items" :key="item.id" class="clickable-row" @click="openMonitorDetail(item.id)">
                  <td><b>{{ item.username || '未知账号' }}</b></td><td>{{ item.question }}</td>
                  <td><span class="monitor-status" :class="item.status === 'completed' ? 'status-online' : 'status-offline'">{{ monitorStatusLabel(item.status) }}</span></td>
                  <td>{{ formatDateTime(item.created_at) }}</td><td>{{ item.duration_seconds != null ? `${item.duration_seconds}秒` : '—' }}</td>
                </tr>
                <tr v-if="!monitorQuestions.items.length"><td colspan="5" class="monitor-empty">没有符合条件的问题记录</td></tr>
              </tbody>
            </table>
          </div>
          <div class="monitor-pagination"><button :disabled="monitorQuestions.page <= 1" @click="loadMonitor({ questionPage: monitorQuestions.page - 1 })">上一页</button><span>第 {{ monitorQuestions.page }} 页</span><button :disabled="monitorQuestions.page * monitorQuestions.pageSize >= monitorQuestions.total" @click="loadMonitor({ questionPage: monitorQuestions.page + 1 })">下一页</button></div>
        </section>

        <div v-if="monitorDetail" class="monitor-modal" @click.self="monitorDetail = null">
          <article class="monitor-detail">
            <header><div><p class="eyebrow">{{ monitorDetail.username }} · {{ formatDateTime(monitorDetail.created_at) }}</p><h2>{{ monitorDetail.question }}</h2></div><button @click="monitorDetail = null">×</button></header>
            <div class="monitor-detail-meta"><span>{{ monitorStatusLabel(monitorDetail.status) }}</span><span>耗时 {{ monitorDetail.duration_seconds ?? '—' }} 秒</span><span>ID {{ monitorDetail.id }}</span></div>
            <div v-if="monitorDetail.error" class="monitor-detail-error"><b>错误信息</b><p>{{ monitorDetail.error }}</p></div>
            <section><h3>完整回答</h3><div v-if="monitorDetail.answer?.markdown" class="markdown-body monitor-answer" v-html="monitorDetailMarkdown"></div><pre v-else>{{ JSON.stringify(monitorDetail.answer, null, 2) }}</pre></section>
            <section><h3>引用来源（{{ answerSources(monitorDetail).length }}）</h3><div class="monitor-source-list"><article v-for="(source, index) in answerSources(monitorDetail)" :key="source.sourceId || source.url || index"><b>{{ source.title || source.documentTitle || '未命名来源' }}</b><p>{{ source.origin === 'web' || source.url ? 'Web来源' : '本地文件' }} · {{ source.article || `第${source.page || '—'}页` }}</p><blockquote v-if="source.quote">{{ source.quote }}</blockquote></article><p v-if="!answerSources(monitorDetail).length" class="muted">没有记录引用来源。</p></div></section>
            <section><h3>检索与模型处理轨迹</h3><pre>{{ JSON.stringify(monitorDetail.answer?.researchTrace || [], null, 2) }}</pre></section>
            <section><h3>任务事件</h3><pre>{{ JSON.stringify(monitorDetail.events || [], null, 2) }}</pre></section>
          </article>
        </div>
      </section>

      <section v-if="isAdmin && activeTab === 'system'" class="system-page">
        <div class="status-card hero-status">
          <div class="status-icon" :class="{ ready: codexHealth?.available && codexHealth?.authenticated }">◉</div>
          <div><p class="eyebrow">本机生成引擎</p><h2>{{ codexHealth?.login || 'Codex状态未知' }}</h2><span>{{ codexHealth?.version }}</span></div>
        </div>
        <div class="metrics">
          <article><span>调用方式</span><b>codex exec</b><small>不使用业务大模型API</small></article>
          <article><span>并发上限</span><b>{{ codexHealth?.concurrency ?? 2 }}</b><small>当前队列 {{ codexHealth?.queueSize ?? 0 }}/{{ codexHealth?.queueLimit ?? 20 }}</small></article>
          <article><span>研究策略</span><b>模型自主</b><small>本地工具与Web搜索在同一次调用中开放</small></article>
          <article><span>会话记忆</span><b>已关闭</b><small>每次提问独立研究，不读取历史问题或答案</small></article>
        </div>
        <div class="boundary-note">
          <h3>数据边界</h3>
          <p>法规源文件和索引保存在本机；用户问题与模型自主读取的内容会通过当前 ChatGPT 登录会话发送给 OpenAI。Codex可在同一次任务中自行使用本地搜索、原文读取和Web搜索。</p>
        </div>
      </section>

      <div v-if="mermaidPreview" class="mermaid-preview-modal" @click.self="mermaidPreview = null">
        <section class="mermaid-preview-panel" role="dialog" aria-modal="true" aria-label="时序图大图预览">
          <header>
            <div><p class="eyebrow">图示预览</p><h2>时序图大图</h2></div>
            <div class="mermaid-preview-actions">
              <button @click="exportMermaidPng(mermaidPreview.svg, `${mermaidPreview.baseName}.png`)">下载图片</button>
              <button @click="downloadMermaidSource(mermaidPreview.source, `${mermaidPreview.baseName}.mmd`)">下载源码</button>
              <button class="mermaid-preview-close" aria-label="关闭大图" @click="mermaidPreview = null">×</button>
            </div>
          </header>
          <div class="mermaid-preview-canvas"><div class="mermaid-preview-drawing" :style="{ width: `${mermaidPreview.previewWidth}px` }" v-html="mermaidPreview.svg"></div></div>
        </section>
      </div>
    </main>
  </div>
</template>
