<script setup>
import { ref, watch, nextTick } from 'vue'
import { marked } from 'marked'

const props = defineProps({
  messages: Array,
  sessions: Array,
  activeSessionId: String,
  busy: Boolean,
  loading: Boolean
})
const emit = defineEmits(['send', 'new-session', 'switch-session'])

const input = ref('')
const msgContainer = ref(null)

const submit = () => {
  if (!input.value.trim() || props.busy) return
  emit('send', input.value.trim())
  input.value = ''
}

const handleEnter = (e) => {
  if (e.isComposing || e.keyCode === 229) return
  submit()
}

const renderMarkdown = (text) => {
  if (!text) return ''
  try { return marked(text) }
  catch (e) { return text.replace(/\n/g, '<br>') }
}

const sessionTitle = (session) => session?.title || '新对话'
const sessionPreview = (session) => {
  const text = (session?.last_message || '').trim()
  return text || '暂无消息'
}
const sessionTime = (session) => {
  const raw = session?.last_message_at || session?.updated_at || ''
  if (!raw) return ''
  return raw.slice(5, 16)
}

watch(() => props.messages, () => {
  nextTick(() => {
    if (msgContainer.value) {
      msgContainer.value.scrollTop = msgContainer.value.scrollHeight
    }
  })
}, { deep: true })
</script>

<template>
  <div class="view-container chat-full">
    <div class="chat-shell">
      <aside class="chat-sessions">
        <div class="chat-sessions-header">
          <h3>历史会话</h3>
          <button class="table-btn btn-success" @click="emit('new-session')" :disabled="busy">新对话</button>
        </div>
        <div class="session-list">
          <button
            v-for="session in sessions"
            :key="session.session_id"
            :class="['session-item', { active: session.session_id === activeSessionId }]"
            :disabled="busy"
            @click="emit('switch-session', session.session_id)"
          >
            <span class="session-title">{{ sessionTitle(session) }}</span>
            <span class="session-preview">{{ sessionPreview(session) }}</span>
            <span class="session-time">{{ sessionTime(session) }}</span>
          </button>
          <div v-if="!sessions?.length" class="session-empty">暂无历史会话</div>
        </div>
      </aside>

      <div class="chat-main">
        <div class="view-header">
          <h2>AI 智能管家</h2>
          <div class="status-indicator">
            <div class="status-dot"></div> {{ busy ? '处理中' : '在线' }}
          </div>
        </div>
        <div class="messages" ref="msgContainer">
          <div v-if="loading" class="empty-small">加载历史消息中...</div>
          <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
            <div :class="['bubble', { 'is-thinking': !!msg.isThinking }]" v-html="renderMarkdown(msg.content)"></div>
          </div>
        </div>
        <div class="input-area">
          <input
            v-model="input"
            @keydown.enter.prevent="handleEnter"
            type="text"
            :disabled="busy"
            :placeholder="busy ? '正在回复中...' : '指挥您的管家，或者询问当前状况...'" />
          <button @click="submit" class="send-btn" :disabled="busy">{{ busy ? '处理中' : '发送' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
