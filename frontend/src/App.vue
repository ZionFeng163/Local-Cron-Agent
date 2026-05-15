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

const currentView = ref('dashboard')

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
const chatMessages = ref([
  { role: 'agent', content: '您好，长官！我是您的 Local-Cron-Agent。很高兴为您服务。通过在下面打字，您可以让我生成脚本型定时任务、暂停/拉起任务，或检查沙盒状态。' }
])

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
  currentView.value = view
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
      receivedAgentContent = false
      startThinkingHints()
    } else if (msg.type === 'stream_end') {
      stopThinkingHints()
      finalizeThinkingMessage()
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
  if (!text) return
  chatMessages.value.push({ role: 'user', content: text })
  receivedAgentContent = false
  chatMessages.value.push({ role: 'agent', isThinking: true, content: thinkingHints[0] })
  startThinkingHints()
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(text)
  } else {
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

const showTaskDetail = (task) => {
  selectedTask.value = task
  isModalOpen.value = true
}

const closeModal = () => {
  isModalOpen.value = false
  selectedTask.value = null
}

// ========== 生命周期 ==========
let refreshTimer = null

onMounted(() => {
  refreshAll()
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
      <ChatView       v-if="currentView === 'chat'" :messages="chatMessages" @send="sendMessage" />
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
                <span class="detail-label">状态</span>
                <span :class="['status-badge', selectedTask.status === 'RUNNING' ? 'badge-running' : 'badge-paused']">
                  {{ selectedTask.status === 'RUNNING' ? '运行中' : '已暂停' }}
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
