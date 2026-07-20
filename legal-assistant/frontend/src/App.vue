<script setup>
import { computed, onMounted, ref } from 'vue'

const CONVERSATION_KEY = 'legal-assistant-conversation-id'
function createConversationId() {
  return globalThis.crypto?.randomUUID?.() || `conv-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
function loadConversationId() {
  const stored = localStorage.getItem(CONVERSATION_KEY)
  if (stored) return stored
  const created = createConversationId()
  localStorage.setItem(CONVERSATION_KEY, created)
  return created
}

const activeTab = ref('ask')
const question = ref('海关查询注册中心是根据什么入参查的，经济运营商需要提供什么入参？')
const asOfDate = ref('')
const busy = ref(false)
const job = ref(null)
const answer = ref(null)
const error = ref('')
const documents = ref([])
const codexHealth = ref(null)
const evidenceDetail = ref(null)
const syncBusy = ref(false)
const syncReport = ref(null)
const conversationId = ref(loadConversationId())
const memoryTurns = ref(Number(localStorage.getItem(`legal-assistant-turns-${conversationId.value}`) || 0))

const conversationLabel = computed(() => conversationId.value.slice(0, 8))

const statusLabel = computed(() => ({
  answered: '研究结论完整', partial: '部分结论', unresolved: '尚未解决', insufficient_evidence: '证据不足', system_error: '系统错误'
}[answer.value?.status] || '等待提问'))
const isReport = computed(() => answer.value?.schemaVersion === '3.0')
const isWorkProduct = computed(() => answer.value?.schemaVersion === '4.0')

async function api(path, options = {}) {
  const isForm = options.body instanceof FormData
  const response = await fetch(path, {
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(options.headers || {}) },
    ...options
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `请求失败：${response.status}`)
  }
  return response.json()
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
        question: question.value.trim(),
        conversation_id: conversationId.value,
        as_of_date: asOfDate.value || null
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
      memoryTurns.value = Math.min(memoryTurns.value + 1, 5)
      localStorage.setItem(`legal-assistant-turns-${conversationId.value}`, String(memoryTurns.value))
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
      stream.onerror = () => { clearTimeout(timeout); stream.close(); resolve() }
    })
  }
  await pollJob(id)
}

function startNewConversation() {
  const created = createConversationId()
  conversationId.value = created
  memoryTurns.value = 0
  localStorage.setItem(CONVERSATION_KEY, created)
  localStorage.setItem(`legal-assistant-turns-${created}`, '0')
  answer.value = null
  job.value = null
  evidenceDetail.value = null
  error.value = ''
  question.value = ''
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
    syncReport.value = await api('/api/admin/sync-local', { method: 'POST', headers: { 'X-Role': 'admin' } })
    await loadDocuments()
  } catch (err) {
    error.value = err.message
  } finally {
    syncBusy.value = false
  }
}

function sourceLabel(source) {
  if (source.origin === 'web') return source.authorityKind === 'legal_act' ? 'Web官方法规' : 'Web官方说明'
  return '本地法规文件'
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
  try {
    await Promise.all([loadDocuments(), loadHealth()])
  } catch (err) {
    error.value = err.message
  }
})
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand-mark">法</div>
      <div class="brand-copy">
        <strong>法规证据助手</strong>
        <span>Local Codex · Evidence First</span>
      </div>
      <nav>
        <button :class="{ active: activeTab === 'ask' }" @click="activeTab = 'ask'">
          <span>⌁</span>法规问答
        </button>
        <button :class="{ active: activeTab === 'library' }" @click="activeTab = 'library'; loadDocuments()">
          <span>▤</span>法规资料库
        </button>
        <button :class="{ active: activeTab === 'system' }" @click="activeTab = 'system'; loadHealth()">
          <span>◉</span>运行状态
        </button>
      </nav>
      <div class="sidebar-foot">
        <div class="health-dot" :class="{ online: codexHealth?.available && codexHealth?.authenticated }"></div>
        <div>
          <b>{{ codexHealth?.authenticated ? 'Codex 已登录' : 'Codex 待检查' }}</b>
          <small>{{ codexHealth?.version || '本机运行模式' }}</small>
        </div>
      </div>
    </aside>

    <main>
      <header class="topbar">
        <div>
          <p class="eyebrow">服务商合规工作台</p>
          <h1>{{ activeTab === 'ask' ? '每个结论，都能回到法规原文' : activeTab === 'library' ? '法规资料库' : 'Codex 运行状态' }}</h1>
        </div>
        <div class="principle"><span>✓</span> 无证据，不下确定性结论</div>
      </header>

      <div v-if="error" class="error-banner">{{ error }}</div>

      <section v-if="activeTab === 'ask'" class="workspace">
        <div class="question-panel">
          <label for="question">请输入法规问题</label>
          <textarea id="question" v-model="question" rows="5" @keydown.ctrl.enter="ask"></textarea>
          <div class="question-actions">
            <div class="question-context">
              <div class="date-field">
                <span>回答截止日期（默认同时提示已公布待生效规则）</span>
                <input v-model="asOfDate" type="date" />
              </div>
              <div class="memory-control">
                <div><b>有限记忆 {{ memoryTurns }}/5轮</b><small>会话 {{ conversationLabel }} · 仅用于理解上下文</small></div>
                <button class="secondary" :disabled="busy" @click="startNewConversation">新建会话</button>
              </div>
            </div>
            <button class="primary" :disabled="busy" @click="ask">
              {{ busy ? '正在检索与核验…' : '开始法规检索' }}
            </button>
          </div>
          <div v-if="job?.events?.length" class="progress-strip">
            <div v-for="event in job.events" :key="event.at" class="progress-item">
              <span></span>{{ event.message }}
            </div>
          </div>
        </div>

        <div v-if="!answer && !busy" class="empty-state">
          <div class="empty-icon">§</div>
          <h2>从本地法规资料中寻找答案</h2>
          <p>助手会循环检索本地源文件并评估证据覆盖；本地仍有关键缺口时，仅查询欧盟官方网站，最终生成正文优先的详实法规答复。</p>
          <div class="rule-grid">
            <article><b>全部文件同权重</b><span>不再区分等级或审核状态</span></article>
            <article><b>逐条引用原文</b><span>结论可回看对应文件、条款和页码</span></article>
            <article><b>保留时间口径</b><span>继续识别生效、待生效与失效日期</span></article>
          </div>
        </div>

        <div v-if="answer" class="answer-layout">
          <div class="answer-main">
            <template v-if="isWorkProduct">
              <div class="answer-heading work-heading">
                <div class="report-heading-row">
                  <span class="answer-status" :class="`answer-${answer.status}`">{{ statusLabel }}</span>
                  <span class="report-version">法规答复 · v4.0</span>
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

              <section class="answer-section research-audit">
                <details><summary>研究与质量审计</summary>
                  <div class="audit-columns">
                    <div><h3>检索过程</h3><div class="trace-list"><article v-for="(item, index) in answer.researchTrace" :key="index"><span>{{ item.type === 'local' ? (item.action === 'read' ? `原文阅读${item.round}` : item.action === 'search' ? `本地搜索${item.round}` : `本地第${item.round}轮`) : item.type === 'web' ? '官方Web' : '独立审核' }}</span><div><b>{{ item.decision }}</b><small>{{ item.action === 'read' ? '已读取原文' : '有效结果' }}：{{ item.resultCount }}</small><small v-if="traceTimingLabel(item)" class="trace-timing">{{ traceTimingLabel(item) }}</small></div></article></div></div>
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
                  <span class="report-version">专业研究报告 · v3.0</span>
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

              <section class="answer-section research-audit">
                <details>
                  <summary>查看检索过程（{{ answer.researchTrace?.length || 0 }}个步骤）</summary>
                  <div class="trace-list">
                    <article v-for="(item, index) in answer.researchTrace" :key="index">
                      <span>{{ item.type === 'local' ? (item.action === 'read' ? `原文阅读${item.round}` : item.action === 'search' ? `本地搜索${item.round}` : `本地第${item.round}轮`) : item.type === 'web' ? '官方Web搜索' : '独立审核' }}</span>
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
              <p v-else class="evidence-meta">{{ evidenceDetail.publisher }} · 官方Web来源</p>
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

      <section v-if="activeTab === 'library'" class="library-page">
        <div class="section-toolbar">
          <div><p class="eyebrow">本地资料</p><h2>本地法规资料库</h2><p>自动读取本地法规目录；所有已同步文件不分等级和状态，以相同权重参与检索。</p></div>
          <div class="toolbar-actions">
            <button class="primary" :disabled="syncBusy" @click="syncLocalDocuments">{{ syncBusy ? '正在扫描与提取…' : '同步本地目录' }}</button>
          </div>
        </div>
        <div v-if="syncReport" class="sync-report">
          发现 {{ syncReport.discovered }} 个本地文件；新增 {{ syncReport.imported }} 个；已有 {{ syncReport.unchanged }} 个；失败 {{ syncReport.errors?.length || 0 }} 个。
        </div>
        <div class="document-table">
          <div class="table-row table-head"><span>法规文件</span><span>条款片段</span><span>发布日期</span><span>生效日期</span><span>原文件</span></div>
          <div v-for="document in documents" :key="document.id" class="table-row">
            <span><b>{{ document.title }}</b><small>{{ document.filename }}</small></span>
            <span>{{ document.passage_count }}</span>
            <span>{{ document.publication_date || '—' }}</span>
            <span>{{ document.effective_date || '—' }}</span>
            <span><a class="document-link" :href="`/api/documents/${document.id}/file`" target="_blank">打开 ↗</a></span>
          </div>
          <div v-if="!documents.length" class="table-empty">尚未发现可读取的本地法规文件。</div>
        </div>
      </section>

      <section v-if="activeTab === 'system'" class="system-page">
        <div class="status-card hero-status">
          <div class="status-icon" :class="{ ready: codexHealth?.available && codexHealth?.authenticated }">◉</div>
          <div><p class="eyebrow">本机生成引擎</p><h2>{{ codexHealth?.login || 'Codex状态未知' }}</h2><span>{{ codexHealth?.version }}</span></div>
        </div>
        <div class="metrics">
          <article><span>调用方式</span><b>codex exec</b><small>不使用业务大模型API</small></article>
          <article><span>并发上限</span><b>{{ codexHealth?.concurrency ?? 2 }}</b><small>当前队列 {{ codexHealth?.queueSize ?? 0 }}/{{ codexHealth?.queueLimit ?? 20 }}</small></article>
          <article><span>研究策略</span><b>{{ codexHealth?.localResearchRounds ?? 3 }}＋{{ codexHealth?.webResearchRounds ?? 1 }}</b><small>本地扩检轮次＋官方Web轮次</small></article>
        </div>
        <div class="boundary-note">
          <h3>数据边界</h3>
          <p>法规源文件和索引保存在本机；用户问题与已筛选证据片段会通过当前 ChatGPT 登录会话发送给 OpenAI。本地证据不足时，Codex仅在单独阶段搜索欧盟官方来源，页面会把本地法规和Web来源分别标明。</p>
        </div>
      </section>
    </main>
  </div>
</template>
