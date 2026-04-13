<script setup>
import { ref, onMounted } from 'vue'

const healthInfo = ref(null)
const isLoading = ref(false)

const fetchHealth = async () => {
  isLoading.value = true
  try {
    const res = await fetch('http://localhost:8000/api/stats')
    healthInfo.value = await res.json()
  } catch (e) {
    console.error(e)
  }
  isLoading.value = false
}

onMounted(fetchHealth)
</script>

<template>
  <div class="view-container">
    <div class="view-header">
      <h2>⚙️ 系统设置</h2>
      <p class="view-subtitle">系统配置与环境信息</p>
    </div>

    <div class="settings-grid">
      <div class="section-card">
        <h3>环境信息</h3>
        <div class="settings-row">
          <span class="settings-label">Agent 名称</span>
          <span class="settings-value">Local-Cron-Agent (自愈管家)</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">沙盒引擎</span>
          <span class="settings-value">Multipass → agent-sandbox</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">后端框架</span>
          <span class="settings-value">FastAPI + Uvicorn</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">前端框架</span>
          <span class="settings-value">Vue 3 + Vite</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">AI 引擎</span>
          <span class="settings-value">exquisite_agent (ReAct FC)</span>
        </div>
      </div>

      <div class="section-card">
        <h3>工具列表</h3>
        <div class="settings-row">
          <span class="settings-label">Sandbox_Bash_Executor</span>
          <span class="settings-value tool-active">● 活跃</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Sandbox_Crontab_Admin</span>
          <span class="settings-value tool-active">● 活跃</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Sandbox_Health_Scanner</span>
          <span class="settings-value tool-active">● 活跃</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Sandbox_Service_Manager</span>
          <span class="settings-value tool-active">● 活跃</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Agent_Heartbeat_Controller</span>
          <span class="settings-value tool-active">● 活跃</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Sandbox_File_Writer</span>
          <span class="settings-value tool-active">● 活跃</span>
        </div>
        <div class="settings-row">
          <span class="settings-label">Search (SerpApi)</span>
          <span class="settings-value tool-active">● 活跃</span>
        </div>
      </div>
    </div>
  </div>
</template>
