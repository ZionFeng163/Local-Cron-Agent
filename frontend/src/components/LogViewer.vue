<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const logs = ref([])
const autoScroll = ref(true)
const logContainer = ref(null)
let timer = null

const fetchLogs = async () => {
  try {
    const res = await fetch('http://localhost:8000/api/logs?lines=200')
    const data = await res.json()
    logs.value = data.lines || []
    if (autoScroll.value && logContainer.value) {
      setTimeout(() => {
        logContainer.value.scrollTop = logContainer.value.scrollHeight
      }, 50)
    }
  } catch (e) {
    console.error(e)
  }
}

onMounted(() => {
  fetchLogs()
  timer = setInterval(fetchLogs, 3000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

const getLogClass = (line) => {
  if (line.includes('[ERROR]') || line.includes('❌')) return 'log-error'
  if (line.includes('[WARNING]')) return 'log-warn'
  if (line.includes('✨') || line.includes('✔')) return 'log-success'
  return ''
}
</script>

<template>
  <div class="view-container">
    <div class="view-header">
      <h2>📋 运行日志</h2>
      <div class="log-controls">
        <label class="auto-scroll-label">
          <input type="checkbox" v-model="autoScroll" /> 自动滚动
        </label>
        <button class="table-btn btn-success" @click="fetchLogs">刷新</button>
      </div>
    </div>
    <div class="log-viewer" ref="logContainer">
      <div v-for="(line, i) in logs" :key="i" :class="['log-line', getLogClass(line)]">{{ line }}</div>
      <div v-if="logs.length === 0" class="empty-small">暂无日志记录</div>
    </div>
  </div>
</template>
