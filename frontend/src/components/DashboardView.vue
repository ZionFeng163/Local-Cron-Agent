<script setup>
import { ref, onMounted } from 'vue'

const stats = ref({ total: 0, running: 0, paused: 0, internal_count: 0, ubuntu_count: 0 })
const internalJobs = ref([])
const ubuntuJobs = ref([])
const ubuntuLoading = ref(true)

const emit = defineEmits(['navigate'])

const fetchData = async () => {
  // 1. 先秒加载内置任务（纯内存 0ms）
  try {
    const res = await fetch('http://localhost:8000/api/jobs/internal')
    const data = await res.json()
    internalJobs.value = data.internal_jobs
    // 先用内置数据刷新统计
    stats.value.internal_count = internalJobs.value.length
    stats.value.total = internalJobs.value.length
    stats.value.running = internalJobs.value.filter(j => j.status === 'RUNNING').length
    stats.value.paused = stats.value.total - stats.value.running
  } catch (e) { console.error(e) }

  // 2. 再异步加载沙盒任务（慢 2-4 秒）
  ubuntuLoading.value = true
  try {
    const res = await fetch('http://localhost:8000/api/jobs/ubuntu')
    const data = await res.json()
    ubuntuJobs.value = data.ubuntu_jobs
    // 更新统计
    const all = internalJobs.value.length + ubuntuJobs.value.length
    const running = internalJobs.value.filter(j => j.status === 'RUNNING').length
                    + ubuntuJobs.value.filter(j => j.status === 'RUNNING').length
    stats.value = {
      total: all, running, paused: all - running,
      internal_count: internalJobs.value.length,
      ubuntu_count: ubuntuJobs.value.length
    }
  } catch (e) { console.error(e) }
  ubuntuLoading.value = false
}

onMounted(fetchData)
defineExpose({ fetchData })
</script>

<template>
  <div class="view-container">
    <div class="view-header">
      <h2>系统概览</h2>
      <p class="view-subtitle">全局状态一览</p>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-number">{{ stats.total }}</div>
        <div class="stat-label">总任务数</div>
      </div>
      <div class="stat-card stat-running">
        <div class="stat-number">{{ stats.running }}</div>
        <div class="stat-label">运行中</div>
      </div>
      <div class="stat-card stat-paused">
        <div class="stat-number">{{ stats.paused }}</div>
        <div class="stat-label">已暂停</div>
      </div>
      <div class="stat-card">
        <div class="stat-number">{{ stats.internal_count }} / {{ stats.ubuntu_count }}</div>
        <div class="stat-label">内置 / 沙盒</div>
      </div>
    </div>

    <div class="dashboard-sections">
      <div class="section-card">
        <h3>✨ Agent 内置心跳</h3>
        <div v-if="internalJobs.length === 0" class="empty-small">暂无内置任务</div>
        <div v-for="job in internalJobs" :key="job.id" class="mini-job" @click="emit('showDetail', job)">
          <span class="mini-name">{{ job.name }}</span>
          <span :class="['status-badge', job.status === 'RUNNING' ? 'badge-running' : 'badge-paused']">
            {{ job.status === 'RUNNING' ? '运行中' : '已暂停' }}
          </span>
        </div>
      </div>
      <div class="section-card">
        <h3>🐧 沙盒定时任务</h3>
        <div v-if="ubuntuLoading" class="empty-small">⏳ 正在连接沙盒虚拟机...</div>
        <template v-else>
          <div v-if="ubuntuJobs.length === 0" class="empty-small">沙盒没有配置定时任务</div>
          <div v-for="job in ubuntuJobs" :key="job.id" class="mini-job" @click="emit('showDetail', job)">
            <span class="mini-name">{{ job.name || job.command.split(' ').slice(0,3).join(' ') + '...' }}</span>
            <span :class="['status-badge', job.status === 'RUNNING' ? 'badge-running' : 'badge-paused']">
              {{ job.status === 'RUNNING' ? '运行中' : '已暂停' }}
            </span>
          </div>
        </template>
        <button class="link-btn" @click="emit('navigate', 'tasks')">查看全部 →</button>
      </div>
    </div>
  </div>
</template>
