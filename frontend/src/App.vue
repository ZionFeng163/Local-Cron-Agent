<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import Sidebar from './components/Sidebar.vue'
import DashboardView from './components/DashboardView.vue'
import TaskManager from './components/TaskManager.vue'
import ScriptEditor from './components/ScriptEditor.vue'
import LogViewer from './components/LogViewer.vue'
import ChatView from './components/ChatView.vue'
import SettingsView from './components/SettingsView.vue'
import SystemHealthView from './components/SystemHealthView.vue'
import HealCenterView from './components/HealCenterView.vue'

const VIEW_STORAGE_KEY = 'local-cron-agent.current-view'
const validViews = new Set(['dashboard', 'tasks', 'health', 'heals', 'scripts', 'logs', 'chat', 'settings'])
const getInitialView = () => {
  try {
    const savedView = localStorage.getItem(VIEW_STORAGE_KEY)
    return validViews.has(savedView) ? savedView : 'dashboard'
  } catch (e) {
    return 'dashboard'
  }
}

const currentView = ref(getInitialView())
const welcomeMessage = { role: 'agent', content: '您好，长官！我是您的 Local-Cron-Agent。很高兴为您服务。通过在下面打字，您可以让我生成脚本型定时任务、暂停/拉起任务，或检查沙盒状态。' }

// ========== 全局任务状态（唯一数据源） ==========
const internalJobs = ref([])
const ubuntuJobs = ref([])
const ubuntuLoading = ref(true)

const fetchInternalJobs = async () => {
  try {
    const res = await fetch('http://localhost:8000/api/jobs/internal')
    const data = await res.json()
    internalJobs.value = data.internal_jobs
  } catch (e) { console.error(e) }
}

const fetchUbuntuJobs = async () => {
  ubuntuLoading.value = true
  try {
    const res = await fetch('http://localhost:8000/api/jobs/ubuntu')
    const data = await res.json()
    ubuntuJobs.value = data.ubuntu_jobs
  } catch (e) { console.error(e) }
  ubuntuLoading.value = false
}

const refreshAll = async () => {
  await fetchInternalJobs()   // 秒回
  fetchUbuntuJobs()           // 异步，不阻塞
}

// ========== 聊天状态 ==========
const chatMessages = ref([{ ...welcomeMessage }])
const chatSessions = ref([])
const activeChatSessionId = ref('')
const chatBusy = ref(false)
const chatLoading = ref(false)

const thinkingHints = [
  '正在理解你的需求...',
  '正在规划执行步骤...',
  '正在调用工具检查环境...',
  '正在整理执行结果...',
  '正在生成最终回复...'
]

let thinkingTimer = null
let thinkingHintIdx = 0
let receivedAgentContent = false
let lastProgressAt = 0

const getLastMessage = () => chatMessages.value[chatMessages.value.length - 1]

const ensureThinkingMessage = () => {
  const last = getLastMessage()
  if (last && last.role === 'agent' && last.isThinking) return last
  const thinkingMsg = { role: 'agent', isThinking: true, content: thinkingHints[0] }
  chatMessages.value.push(thinkingMsg)
  return thinkingMsg
}

const startThinkingHints = () => {
  stopThinkingHints()
  thinkingHintIdx = 0
  lastProgressAt = Date.now()
  const msg = ensureThinkingMessage()
  msg.content = thinkingHints[thinkingHintIdx]
  thinkingTimer = setInterval(() => {
    const last = getLastMessage()
    if (!last || !last.isThinking) return
    if (Date.now() - lastProgressAt < 2400) return
    thinkingHintIdx = (thinkingHintIdx + 1) % thinkingHints.length
    last.content = thinkingHints[thinkingHintIdx]
  }, 2200)
}

const stopThinkingHints = () => {
  if (thinkingTimer) {
    clearInterval(thinkingTimer)
    thinkingTimer = null
  }
}

const appendAgentContent = (content) => {
  if (!content) return
  let last = getLastMessage()
  if (!last || last.role !== 'agent') {
    chatMessages.value.push({ role: 'agent', content: '' })
    last = getLastMessage()
  }
  if (last.isThinking) {
    last.isThinking = false
    last.content = ''
  }
  last.content += content
  receivedAgentContent = true
}

const updateThinkingStatus = (text) => {
  if (!text || receivedAgentContent) return
  const msg = ensureThinkingMessage()
  msg.content = text
  lastProgressAt = Date.now()
}

const finalizeThinkingMessage = () => {
  const last = getLastMessage()
  if (last && last.role === 'agent' && last.isThinking) {
    last.isThinking = false
    if (!receivedAgentContent) {
      last.content = '✅ 指令已执行完成。'
    }
  }
}

const navigateTo = (view) => {
  if (!validViews.has(view)) return
  currentView.value = view
  try {
    localStorage.setItem(VIEW_STORAGE_KEY, view)
  } catch (e) {
    console.error(e)
  }
}

const normalizeChatMessages = (items) => {
  const normalized = (items || []).map((m) => ({
    role: m.role,
    content: m.content || '',
    created_at: m.created_at,
    run_id: m.run_id
  }))
  return normalized.length ? normalized : [{ ...welcomeMessage }]
}

const fetchChatSessions = async () => {
  try {
    const res = await fetch('http://localhost:8000/api/chat/sessions?limit=50')
    const data = await res.json()
    chatSessions.value = data.sessions || []
    if (!activeChatSessionId.value && chatSessions.value.length) {
      await switchChatSession(chatSessions.value[0].session_id)
    }
  } catch (e) {
    console.error(e)
  }
}

const loadChatMessages = async (sessionId) => {
  if (!sessionId) {
    chatMessages.value = [{ ...welcomeMessage }]
    return
  }
  chatLoading.value = true
  try {
    const res = await fetch(`http://localhost:8000/api/chat/sessions/${sessionId}/messages`)
    const data = await res.json()
    chatMessages.value = normalizeChatMessages(data.messages || [])
  } catch (e) {
    console.error(e)
    chatMessages.value = [{ role: 'agent', content: '❌ 加载历史会话失败。' }]
  }
  chatLoading.value = false
}

const createChatSession = async () => {
  if (chatBusy.value) return
  activeChatSessionId.value = ''
  chatMessages.value = [{ ...welcomeMessage }]
}

const switchChatSession = async (sessionId) => {
  if (chatBusy.value || !sessionId || sessionId === activeChatSessionId.value) return
  activeChatSessionId.value = sessionId
  await loadChatMessages(sessionId)
}

// ========== WebSocket ==========
let ws = null

const initWebSocket = () => {
  ws = new WebSocket('ws://localhost:8000/ws/chat')
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    if (msg.type === 'refresh_jobs') {
      refreshAll()
    } else if (msg.type === 'stream_start') {
      chatBusy.value = true
      if (msg.session_id) activeChatSessionId.value = msg.session_id
      receivedAgentContent = false
      startThinkingHints()
    } else if (msg.type === 'stream_end') {
      stopThinkingHints()
      finalizeThinkingMessage()
      chatBusy.value = false
      fetchChatSessions()
    } else if (msg.type === 'tool_start') {
      updateThinkingStatus(msg.content || '正在调用工具执行任务...')
    } else if (msg.type === 'status' || msg.type === 'heartbeat' || msg.type === 'tool_result') {
      updateThinkingStatus(msg.content || '处理中...')
    } else if (msg.type === 'content_chunk' || msg.type === 'message') {
      const content = msg.content || ''
      if (content.trim()) {
        appendAgentContent(content)
      } else if (!receivedAgentContent) {
        ensureThinkingMessage()
      }
    }
  }
  ws.onclose = () => {
    chatBusy.value = false
    stopThinkingHints()
    const last = getLastMessage()
    if (last && last.isThinking) {
      last.isThinking = false
      last.content = '❌ 与后端连接断开，请稍后重试。'
    }
    setTimeout(initWebSocket, 3000)
  }
}

const sendMessage = (text) => {
  if (!text || chatBusy.value) return
  chatBusy.value = true
  chatMessages.value.push({ role: 'user', content: text })
  receivedAgentContent = false
  chatMessages.value.push({ role: 'agent', isThinking: true, content: thinkingHints[0] })
  startThinkingHints()
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ session_id: activeChatSessionId.value, content: text }))
  } else {
    chatBusy.value = false
    stopThinkingHints()
    const last = getLastMessage()
    if (last && last.isThinking) {
      last.isThinking = false
      last.content = '❌ 无法连接到后端，请确保 Uvicorn 正在运行。'
    }
  }
}

const selectedTask = ref(null)
const isModalOpen = ref(false)
const selectedTaskRuns = ref([])
const selectedTaskRunsLoading = ref(false)

const showTaskDetail = async (task) => {
  selectedTask.value = task
  isModalOpen.value = true
  selectedTaskRuns.value = []
  if (task?.source !== 'sandbox' || !task?.id) return
  selectedTaskRunsLoading.value = true
  try {
    const res = await fetch(`http://localhost:8000/api/tasks/${task.id}/runs?limit=8`)
    const data = await res.json()
    selectedTaskRuns.value = data.runs || []
  } catch (e) {
    console.error(e)
  }
  selectedTaskRunsLoading.value = false
}

const closeModal = () => {
  isModalOpen.value = false
  selectedTask.value = null
  selectedTaskRuns.value = []
  selectedTaskRunsLoading.value = false
}

const scheduleLabel = (task) => task?.schedule_label || (task?.status === 'RUNNING' ? '调度中' : '已暂停')
const healthLabel = (task) => task?.health_label || '未检测'
const healthBadgeClass = (task) => {
  if (task?.health_status === 'HEALTHY') return 'badge-running'
  if (task?.health_status === 'FAILING') return 'badge-failing'
  return 'badge-unknown'
}
const scheduleBadgeClass = (task) => {
  if (task?.status !== 'RUNNING') return 'badge-paused'
  if (task?.health_status === 'FAILING') return 'badge-failing'
  return 'badge-running'
}
const scheduleDisplayLabel = (task) => {
  if (task?.status === 'RUNNING' && task?.health_status === 'FAILING') return '异常调度中'
  return scheduleLabel(task)
}
const fmtExitCode = (v) => (v === null || v === undefined ? '---' : String(v))
const runStatusText = (v) => v === 'success' ? '成功' : (v === 'failed' ? '失败' : (v || '---'))

// ========== 生命周期 ==========
let refreshTimer = null

onMounted(() => {
  refreshAll()
  fetchChatSessions()
  initWebSocket()
  // 每 30 秒静默刷新全局数据
  refreshTimer = setInterval(refreshAll, 30000)
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  stopThinkingHints()
})
</script>

<template>
  <div class="app-layout">
    <Sidebar :currentView="currentView" @navigate="navigateTo" />
    <main class="main-content">
      <DashboardView
        v-if="currentView === 'dashboard'"
        :internalJobs="internalJobs"
        :ubuntuJobs="ubuntuJobs"
        :ubuntuLoading="ubuntuLoading"
        @navigate="navigateTo"
        @showDetail="showTaskDetail" />
      <TaskManager
        v-if="currentView === 'tasks'"
        :internalJobs="internalJobs"
        :ubuntuJobs="ubuntuJobs"
        :ubuntuLoading="ubuntuLoading"
        @refresh="refreshAll"
        @showDetail="showTaskDetail" />
      <SystemHealthView v-if="currentView === 'health'" />
      <HealCenterView  v-if="currentView === 'heals'" />
      <ScriptEditor   v-if="currentView === 'scripts'" />
      <LogViewer      v-if="currentView === 'logs'" />
      <ChatView
        v-if="currentView === 'chat'"
        :messages="chatMessages"
        :sessions="chatSessions"
        :activeSessionId="activeChatSessionId"
        :busy="chatBusy"
        :loading="chatLoading"
        @send="sendMessage"
        @new-session="createChatSession"
        @switch-session="switchChatSession" />
      <SettingsView   v-if="currentView === 'settings'" />
    </main>

    <!-- 详情弹窗 -->
    <Transition name="fade">
      <div v-if="isModalOpen" class="modal-overlay" @click.self="closeModal">
        <div class="modal-content">
          <div class="modal-header">
            <h3>任务详情</h3>
            <button class="close-btn" @click="closeModal">×</button>
          </div>
          <div class="modal-body" v-if="selectedTask">
            <div class="detail-grid">
              <div class="detail-item">
                <span class="detail-label">任务 ID</span>
                <span class="detail-value mono">{{ selectedTask.id }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">任务名称</span>
                <span class="detail-value">{{ selectedTask.name }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">来源</span>
                <span class="detail-value badge-source">{{ selectedTask.source === 'internal' ? 'Agent 内置' : '沙盒脚本任务' }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">调度状态</span>
                <span :class="['status-badge', scheduleBadgeClass(selectedTask)]" :title="selectedTask.status_explanation">
                  {{ scheduleDisplayLabel(selectedTask) }}
                </span>
              </div>
              <div class="detail-item">
                <span class="detail-label">健康状态</span>
                <span :class="['status-badge', healthBadgeClass(selectedTask)]">
                  {{ healthLabel(selectedTask) }}
                </span>
              </div>
              <div class="detail-item full-width">
                <span class="detail-label">Cron 表达式 / 频率</span>
                <span class="detail-value mono">{{ selectedTask.cron_expr }}</span>
              </div>
              <div class="detail-item full-width">
                <span class="detail-label">脚本路径</span>
                <div class="detail-value command-box">{{ selectedTask.script_path || '---' }}</div>
              </div>
              <div class="detail-item full-width">
                <span class="detail-label">详细描述</span>
                <span class="detail-value">{{ selectedTask.description || '无描述' }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">连续失败</span>
                <span class="detail-value mono">{{ selectedTask.consecutive_failures ?? 0 }} / {{ selectedTask.auto_heal_threshold ?? 1 }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">最近退出码</span>
                <span class="detail-value mono">{{ fmtExitCode(selectedTask.last_exit_code) }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">最近运行</span>
                <span class="detail-value small-time">{{ selectedTask.last_run_at || '---' }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">最近成功</span>
                <span class="detail-value small-time">{{ selectedTask.last_success_at || '---' }}</span>
              </div>
              <div class="detail-item full-width">
                <span class="detail-label">状态说明</span>
                <span class="detail-value">{{ selectedTask.status_explanation || '---' }}</span>
              </div>
              <div class="detail-item full-width" v-if="selectedTask.source === 'sandbox'">
                <span class="detail-label">最近运行记录</span>
                <div v-if="selectedTaskRunsLoading" class="runs-empty">加载运行记录中...</div>
                <table v-else class="runs-table">
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>结果</th>
                      <th>退出码</th>
                      <th>来源</th>
                      <th>输出摘要</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="run in selectedTaskRuns" :key="run.run_id">
                      <td class="mono">{{ run.run_at || '---' }}</td>
                      <td>
                        <span :class="['status-badge', run.status === 'success' ? 'badge-running' : 'badge-failing']">
                          {{ runStatusText(run.status) }}
                        </span>
                      </td>
                      <td class="mono">{{ fmtExitCode(run.exit_code) }}</td>
                      <td class="mono">{{ run.source || '---' }}</td>
                      <td class="run-output">{{ run.output_tail || '---' }}</td>
                    </tr>
                    <tr v-if="selectedTaskRuns.length === 0">
                      <td colspan="5" class="runs-empty">暂无运行记录</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="detail-item">
                <span class="detail-label">创建时间</span>
                <span class="detail-value small-time">{{ selectedTask.created_at || '---' }}</span>
              </div>
              <div class="detail-item">
                <span class="detail-label">最后更新</span>
                <span class="detail-value small-time">{{ selectedTask.updated_at || '---' }}</span>
              </div>
            </div>
          </div>
          <div class="modal-footer">
            <button class="primary-btn" @click="closeModal">关闭</button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style>
.fade-enter-active, .fade-leave-active { transition: opacity 0.3s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
