<script setup>
import { ref, watch, nextTick } from 'vue'
import { marked } from 'marked'

const props = defineProps(['messages'])
const emit = defineEmits(['send'])
const input = ref('')
const msgContainer = ref(null)

const submit = () => {
  if (!input.value.trim()) return
  emit('send', input.value)
  input.value = ''
}

const handleEnter = (e) => {
  if (e.isComposing || e.keyCode === 229) return
  submit()
}

const renderMarkdown = (text) => {
  if (!text) return ''
  try {
    return marked(text)
  } catch (e) {
    return text.replace(/\n/g, '<br>')
  }
}

// 永远保持滚动条在最下面
watch(() => props.messages, () => {
  nextTick(() => {
    if (msgContainer.value) {
      msgContainer.value.scrollTop = msgContainer.value.scrollHeight
    }
  })
}, { deep: true })
</script>

<template>
  <div class="chat-wrapper">
    <div class="chat-header">
       <h3>Terminal: Local-Cron-Agent</h3>
       <div class="status-indicator">
         <div class="status-dot"></div>
         <span>Web 已连接</span>
       </div>
    </div>
    <div class="messages" ref="msgContainer">
      <div v-for="(msg, i) in messages" :key="i" :class="['message', msg.role]">
         <div class="bubble" v-html="renderMarkdown(msg.content)"></div>
      </div>
    </div>
    <div class="input-area">
      <input 
        v-model="input" 
        @keydown.enter.prevent="handleEnter" 
        type="text" 
        placeholder="在这里指挥您的管家，或者询问当前状况..." />
      <button @click="submit" class="send-btn">Send</button>
    </div>
  </div>
</template>
