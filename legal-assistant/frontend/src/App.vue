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
  answered: '证据充分', partial: '部分有依据', insufficient_evidence: '证据不足', system_error: '系统错误'
}[answer.value?.status] || '等待提问'))

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

async function reviewDocument(document, status, sourceLevel = document.source_level) {
  try {
    await api(`/api/documents/${document.id}/review`, {
      method: 'POST', headers: { 'X-Role': 'admin' },
      body: JSON.stringify({ review_status: status, source_level: sourceLevel, note: `Web管理员设置为${status}/${sourceLevel}` })
    })
    await loadDocuments()
  } catch (err) {
    error.value = err.message
  }
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
    await pollJob(accepted.job_id)
  } catch (err) {
    error.value = err.message
  } finally {
    busy.value = false
  }
}

async function pollJob(id) {
  for (let count = 0; count < 400; count += 1) {
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

function sourceClass(level) {
  return `source source-${level?.toLowerCase()}`
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
          <h2>从已审核法规中寻找答案</h2>
          <p>助手先检索本地源文件，再让本机 Codex 生成原子论点，并独立核验每条引用。</p>
          <div class="rule-grid">
            <article><b>A1 / A2</b><span>正式法规与现行文本</span></article>
            <article><b>B / C</b><span>指南与标准仅作补充</span></article>
            <article><b>D</b><span>草案不支撑确定性结论</span></article>
          </div>
        </div>

        <div v-if="answer" class="answer-layout">
          <div class="answer-main">
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
                        <span :class="sourceClass(citation.sourceLevel)">{{ citation.sourceLevel }}</span>
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
          </div>

          <aside class="evidence-panel">
            <template v-if="evidenceDetail">
              <div class="evidence-head">
                <span :class="sourceClass(evidenceDetail.source_level)">{{ evidenceDetail.source_level }}</span>
                <button @click="evidenceDetail = null">×</button>
              </div>
              <h3>{{ evidenceDetail.document_title }}</h3>
              <p class="evidence-meta">{{ evidenceDetail.article || '未识别条款' }} · PDF第{{ evidenceDetail.page }}页</p>
              <p v-if="evidenceDetail.effective_date" class="evidence-meta">生效日期：{{ evidenceDetail.effective_date }}</p>
              <blockquote>{{ evidenceDetail.text }}</blockquote>
              <a :href="evidenceDetail.pdfUrl" target="_blank">打开法规原文件 ↗</a>
            </template>
            <template v-else>
              <div class="evidence-placeholder">点击结论下方的条款标签，在这里核对法规原文。</div>
            </template>
          </aside>
        </div>
      </section>

      <section v-if="activeTab === 'library'" class="library-page">
        <div class="section-toolbar">
          <div><p class="eyebrow">管理员设置</p><h2>本地法规资料库</h2><p>自动读取本地法规目录；新文件默认以B级发布，管理员可调整等级或停用。</p></div>
          <div class="toolbar-actions">
            <button class="primary" :disabled="syncBusy" @click="syncLocalDocuments">{{ syncBusy ? '正在扫描与提取…' : '同步本地目录' }}</button>
          </div>
        </div>
        <div v-if="syncReport" class="sync-report">
          发现 {{ syncReport.discovered }} 个本地文件；新增 {{ syncReport.imported }} 个；已有 {{ syncReport.unchanged }} 个；失败 {{ syncReport.errors?.length || 0 }} 个。
        </div>
        <div class="document-table">
          <div class="table-row table-head"><span>法规文件</span><span>等级</span><span>状态</span><span>条款片段</span><span>生效日期</span><span>审核</span></div>
          <div v-for="document in documents" :key="document.id" class="table-row">
            <span><b>{{ document.title }}</b><small>{{ document.filename }}</small></span>
            <span><select class="level-select" :value="document.source_level" @change="reviewDocument(document, document.review_status, $event.target.value)"><option v-for="level in ['A1','A2','B','C','D']" :key="level">{{ level }}</option></select></span>
            <span><i class="review-pill">{{ document.review_status }}</i></span>
            <span>{{ document.passage_count }}</span>
            <span>{{ document.effective_date || '—' }}</span>
            <span class="row-actions">
              <button v-if="document.review_status !== 'published'" @click="reviewDocument(document, 'published')">发布</button>
              <button v-if="document.review_status !== 'disabled'" @click="reviewDocument(document, 'disabled')">停用</button>
            </span>
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
          <article><span>独立审核</span><b>{{ codexHealth?.verifyEnabled ? '已启用' : '已关闭' }}</b><small>生成与审核分两次运行</small></article>
        </div>
        <div class="boundary-note">
          <h3>数据边界</h3>
          <p>法规源文件和索引保存在本机；提交给 Codex 的用户问题与已筛选证据片段会通过当前 ChatGPT 登录会话发送给 OpenAI。Codex不启用自主网络搜索。</p>
        </div>
      </section>
    </main>
  </div>
</template>
