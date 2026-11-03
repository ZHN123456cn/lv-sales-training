<script setup>
/**
 * LV 销售培训系统 - 主组件。
 *
 * 培训流程：
 *   welcome → student_info → scenario_select → practicing → evaluation
 *
 * 核心机制：
 *   - practicing 阶段使用 customer Agent 模拟客户
 *   - 结束演练时切换到 coach Agent 生成评价
 */
import { ref, reactive, onMounted, onUnmounted, nextTick, computed } from 'vue'
import StudentInfoForm from './components/StudentInfoForm.vue'
import ScenarioPicker from './components/ScenarioPicker.vue'
import DemoHome from './components/DemoHome.vue'
import EvaluationReport from './components/EvaluationReport.vue'
import ChatMessage from './components/ChatMessage.vue'
import ChatInput from './components/ChatInput.vue'
import { trainingApi, healthApi } from './api'
import { streamChat } from './api/sse'

// ----- 流程阶段 -----
const stage = ref('welcome')  // welcome | student_info | scenario_select | practicing | evaluation
const connected = ref(false)

// ----- 培训数据 -----
const studentInfo = ref({ name: '', id: '' })
const scenarios = ref([])
const currentScenario = ref(null)
const sessionId = ref(null)

// ----- 对话 -----
const messages = reactive([])
const isStreaming = ref(false)
let abortController = null
const messagesContainer = ref(null)

// ----- 评价 -----
const evaluation = ref('')
const evaluationLoading = ref(false)

// ----- 错误 -----
const error = ref(null)

// ----- 初始化 -----
onMounted(async () => {
  // 先尝试加载场景（即使健康检查短暂失败也不阻塞）
  await loadScenarios()
  // 定期检查连接状态
  await checkHealth()
  // 每 5 秒重连一次（如果断连）
  healthCheckInterval = setInterval(checkHealth, 5000)
})

onUnmounted(() => {
  if (healthCheckInterval) clearInterval(healthCheckInterval)
})

let healthCheckInterval = null

async function checkHealth() {
  try {
    await healthApi.check()
    connected.value = true
    if (error.value && error.value.includes('连接')) error.value = null
  } catch {
    connected.value = false
    if (!error.value) error.value = '无法连接后端服务，请确认 FastAPI 已启动'
  }
}

async function loadScenarios() {
  try {
    const data = await trainingApi.scenarios()
    scenarios.value = data.scenarios || []
  } catch (e) {
    console.error('加载场景失败', e)
  }
}

// ----- 阶段流转 -----
function goStudentInfo() {
  error.value = null
  stage.value = 'student_info'
}

function goScenarioSelect(info) {
  studentInfo.value = info
  stage.value = 'scenario_select'
}

async function startScenario(scenarioId) {
  error.value = null
  const scenario = scenarios.value.find((s) => s.id === scenarioId)
  currentScenario.value = scenario

  try {
    const data = await trainingApi.start({
      student_name: studentInfo.value.name,
      student_id: studentInfo.value.id,
      scenario_id: scenarioId,
    })
    sessionId.value = data.session_id
    // 客户开场消息
    messages.splice(0, messages.length)
    messages.push({
      role: 'assistant',
      content: data.opening_message,
      tools: [],
    })
    stage.value = 'practicing'
    await scrollToBottom()
  } catch (e) {
    error.value = e.message
  }
}

// ----- 流式 think 标签过滤器 -----
// 用于在 SSE 流式接收时实时过滤 <think>...</think> 内容和 [[END_TRAINING]] 标记
function createStreamFilter() {
  let buffer = ''
  let inThink = false
  let detectedEndTraining = false
  const OPEN_TAG = '<think>'
  const CLOSE_TAG = '</think>'
  const END_MARKER = '[[END_TRAINING]]'

  function filter(content) {
    buffer += content
    let result = ''

    while (buffer.length > 0) {
      if (inThink) {
        const closeIdx = buffer.toLowerCase().indexOf(CLOSE_TAG)
        if (closeIdx >= 0) {
          inThink = false
          buffer = buffer.substring(closeIdx + CLOSE_TAG.length)
        } else {
          const keep = Math.min(buffer.length, CLOSE_TAG.length - 1)
          buffer = buffer.substring(buffer.length - keep)
          break
        }
      } else {
        const openIdx = buffer.toLowerCase().indexOf(OPEN_TAG)
        if (openIdx >= 0) {
          result += buffer.substring(0, openIdx)
          inThink = true
          buffer = buffer.substring(openIdx + OPEN_TAG.length)
        } else {
          const keep = Math.min(buffer.length, OPEN_TAG.length - 1)
          if (buffer.length > keep) {
            result += buffer.substring(0, buffer.length - keep)
            buffer = buffer.substring(buffer.length - keep)
          }
          break
        }
      }
    }

    // 检测并过滤自动结束标记
    if (result.toUpperCase().includes(END_MARKER)) {
      detectedEndTraining = true
      result = result.replace(/\[\[END_TRAINING\]\]/gi, '')
    }
    // 也检查 buffer 中是否有结束标记（可能还未输出）
    if (buffer.toUpperCase().includes(END_MARKER)) {
      detectedEndTraining = true
      buffer = buffer.replace(/\[\[END_TRAINING\]\]/gi, '')
    }

    return result
  }

  filter.hasEndTraining = () => detectedEndTraining
  return filter
}

// ----- 对话（与客户 Agent）-----
async function sendMessage(text) {
  if (isStreaming.value) return
  error.value = null

  // 销售顾问消息
  messages.push({ role: 'user', content: text, tools: [] })

  // 客户回复占位
  const customerMsg = reactive({
    role: 'assistant',
    content: '',
    tools: [],
    isStreaming: true,
  })
  messages.push(customerMsg)
  await scrollToBottom()

  isStreaming.value = true
  abortController = new AbortController()
  const filterStream = createStreamFilter()

  await streamChat({
    message: text,
    sessionId: sessionId.value,
    signal: abortController.signal,
    onToken: (content) => {
      const filtered = filterStream(content)
      if (filtered) {
        customerMsg.content += filtered
        scrollToBottom()
      }
    },
    onToolCall: (tool) => {
      customerMsg.tools.push({
        name: tool.name,
        arguments: tool.arguments,
        result: undefined,
        truncated: false,
      })
      scrollToBottom()
    },
    onToolResult: (result) => {
      const last = customerMsg.tools[customerMsg.tools.length - 1]
      if (last && last.name === result.name && last.result === undefined) {
        last.result = result.result
        last.truncated = result.truncated
      }
      scrollToBottom()
    },
    onDone: (data) => {
      customerMsg.isStreaming = false
      if (data.session_id) sessionId.value = data.session_id
      // 检测客户是否决定购买（自动结算标记）
      if (filterStream.hasEndTraining()) {
        // 延迟一点让用户看到购买意向
        setTimeout(() => {
          endPractice()
        }, 1000)
      }
    },
    onError: (msg) => {
      customerMsg.isStreaming = false
      if (!customerMsg.content) {
        customerMsg.content = '⚠️ ' + msg
      } else {
        error.value = msg
      }
    },
  })

  isStreaming.value = false
  abortController = null
}

function stopGeneration() {
  if (abortController) abortController.abort()
}

// ----- 结束演练 + 教练评价 -----
async function endPractice() {
  if (isStreaming.value) return
  if (!sessionId.value) return

  evaluationLoading.value = true
  evaluation.value = ''
  stage.value = 'evaluation'

  try {
    const data = await trainingApi.evaluate({
      session_id: sessionId.value,
      student_name: studentInfo.value.name,
      student_id: studentInfo.value.id,
      scenario_id: currentScenario.value.id,
    })
    evaluation.value = data.evaluation
  } catch (e) {
    error.value = e.message
    evaluation.value = '⚠️ ' + e.message
  } finally {
    evaluationLoading.value = false
  }
}

// ----- 重新开始 -----
function restartTraining() {
  messages.splice(0, messages.length)
  sessionId.value = null
  currentScenario.value = null
  evaluation.value = ''
  error.value = null
  stage.value = 'welcome'
}

async function scrollToBottom() {
  await nextTick()
  const el = messagesContainer.value
  if (el) el.scrollTop = el.scrollHeight
}

// ----- 计算属性 -----
const headerTitle = computed(() => {
  if (stage.value === 'practicing' && currentScenario.value) {
    return `${currentScenario.value.icon} ${currentScenario.value.title}`
  }
  return 'LV 销售培训模拟'
})
</script>

<template>
  <div class="app-layout">
    <main class="chat-main">
      <!-- ===== 顶部栏 ===== -->
      <header class="chat-header">
        <div>
          <div class="chat-header-title">{{ headerTitle }}</div>
          <div v-if="stage === 'practicing'" class="chat-header-info">
            学员：{{ studentInfo.name }}（{{ studentInfo.id }}） · 与客户对话中
          </div>
          <div v-else class="chat-header-info">
            LV 销售培训模拟系统
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 12px;">
          <button
            v-if="stage === 'practicing'"
            class="restart-btn"
            @click="endPractice"
            :disabled="isStreaming"
            title="结束演练并获取教练评价"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
            结束演练
          </button>
          <div class="status-badge">
            <span class="status-dot" :class="{ active: connected }"></span>
            {{ connected ? '已连接' : '未连接' }}
          </div>
        </div>
      </header>

      <!-- ===== 错误提示 ===== -->
      <div v-if="error" class="error-banner" style="margin: 16px 24px 0;">
        {{ error }}
        <button style="margin-left: 8px; color: var(--error); text-decoration: underline;" @click="error = null">关闭</button>
      </div>

      <!-- ===== Welcome 阶段 ===== -->
      <DemoHome v-if="stage === 'welcome'" @start="goStudentInfo" />

      <!-- ===== 学生信息阶段 ===== -->
      <StudentInfoForm
        v-else-if="stage === 'student_info'"
        @submit="goScenarioSelect"
        @back="stage = 'welcome'"
      />

      <!-- ===== 场景选择阶段 ===== -->
      <ScenarioPicker
        v-else-if="stage === 'scenario_select'"
        :scenarios="scenarios"
        @select="startScenario"
        @back="stage = 'student_info'"
      />

      <!-- ===== 演练对话阶段 ===== -->
      <template v-else-if="stage === 'practicing'">
        <div class="messages-container" ref="messagesContainer">
          <div class="messages-inner">
            <ChatMessage
              v-for="(msg, idx) in messages"
              :key="idx"
              :message="msg"
              :is-customer-mode="true"
            />
          </div>
        </div>

        <ChatInput
          :disabled="isStreaming"
          placeholder="输入你对客户说的话..."
          @send="sendMessage"
          @stop="stopGeneration"
        />
      </template>

      <!-- ===== 评价报告阶段 ===== -->
      <EvaluationReport
        v-else-if="stage === 'evaluation'"
        :evaluation="evaluation"
        :student-name="studentInfo.name"
        :student-id="studentInfo.id"
        :scenario-title="currentScenario?.title || ''"
        :loading="evaluationLoading"
        @restart="restartTraining"
        @back="stage = 'practicing'"
      />
    </main>
  </div>
</template>

<style scoped>
.message {
  margin-bottom: 24px;
  display: flex;
  gap: 12px;
}
.message-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 600;
}
.message.user .message-avatar {
  background: var(--accent);
  color: white;
}
.message.assistant .message-avatar {
  background: #e3f2fd;
  color: #1976d2;
}
.message-content {
  flex: 1;
  min-width: 0;
}
.message-role {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--text-secondary);
}
</style>
