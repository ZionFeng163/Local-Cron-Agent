<script setup>
const props = defineProps({
  internalJobs: Array,
  ubuntuJobs: Array,
  ubuntuLoading: Boolean
})

const emit = defineEmits(['refresh', 'showDetail'])

const toggleTask = async (event, job) => {
  event.stopPropagation() // 防止触发行点击
  const newStatus = job.status === 'RUNNING' ? 'PAUSED' : 'RUNNING'
  job.status = newStatus  // 乐观更新
  try {
    await fetch('http://localhost:8000/api/jobs/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: job.id })
    })
    emit('refresh')
  } catch (e) { console.error(e) }
}

const deleteTask = async (event, job) => {
  event.stopPropagation() // 防止触发行点击
  if (!confirm(`确定要永久删除此任务吗？\n${job.script_path || job.name}`)) return
  try {
    await fetch('http://localhost:8000/api/jobs/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: job.id })
    })
    emit('refresh')
  } catch (e) { console.error(e) }
}

const healTask = async (event, job) => {
  event.stopPropagation()
  if (job.status === 'RUNNING') {
    alert('任务运行中，请先暂停后再修复')
    return
  }
  try {
    const res = await fetch(`http://localhost:8000/api/tasks/${job.id}/heal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'manual' })
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data?.detail || '修复请求失败')

    const result = data?.result || {}
    const lines = [result.msg || '已触发修复']
    if (result.category) lines.push(`失败类型: ${result.category}`)
    if (result.action) lines.push(`动作: ${result.action}`)
    alert(lines.join('\n'))

    emit('refresh')
  } catch (e) {
    console.error(e)
    alert(e.message || '修复失败')
  }
}

const fmtExitCode = (v) => (v === null || v === undefined ? '---' : String(v))
</script>

<template>
  <div class="view-container">
    <div class="view-header">
      <h2>任务管理</h2>
      <p class="view-subtitle">查看、暂停、启动或删除脚本型定时任务</p>
    </div>

    <div class="table-section">
      <h3 class="table-title">Agent 内置任务</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 25%">任务名称</th>
            <th style="width: 35%">规则 / 描述</th>
            <th style="width: 15%">状态</th>
            <th style="width: 25%">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="job in internalJobs" :key="job.id" class="clickable-row" @click="emit('showDetail', job)">
            <td>{{ job.name }}</td>
            <td class="mono">{{ job.cron_expr || job.description || '---' }}</td>
            <td>
              <span :class="['status-badge', job.status === 'RUNNING' ? 'badge-running status-live' : 'badge-paused']">
                <span v-if="job.status === 'RUNNING'" class="live-dot"></span>
                {{ job.status === 'RUNNING' ? '运行中' : '已暂停' }}
              </span>
            </td>
            <td>
              <button @click="toggleTask($event, job)" :class="['table-btn', job.status === 'RUNNING' ? 'btn-danger' : 'btn-success']">
                {{ job.status === 'RUNNING' ? '暂停' : '启动' }}
              </button>
            </td>
          </tr>
          <tr v-if="internalJobs.length === 0"><td colspan="4" class="empty-row">暂无内置任务</td></tr>
        </tbody>
      </table>
    </div>

    <div class="table-section">
      <h3 class="table-title">沙盒脚本定时任务</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 18%">任务名称</th>
            <th style="width: 26%">详细描述</th>
            <th style="width: 9%">状态</th>
            <th style="width: 10%">连续失败</th>
            <th style="width: 14%">最近成功</th>
            <th style="width: 8%">退出码</th>
            <th style="width: 15%">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="ubuntuLoading">
            <td colspan="7" class="empty-row">⏳ 首次同步中...</td>
          </tr>
          <template v-else>
            <tr v-for="job in ubuntuJobs" :key="job.id" class="clickable-row" @click="emit('showDetail', job)">
              <td>{{ job.name || '未命名任务' }}</td>
              <td class="desc-cell">{{ job.description || job.script_path }}</td>
              <td>
                <span :class="['status-badge', job.status === 'RUNNING' ? 'badge-running status-live' : 'badge-paused']">
                  <span v-if="job.status === 'RUNNING'" class="live-dot"></span>
                  {{ job.status === 'RUNNING' ? '运行中' : '已暂停' }}
                </span>
              </td>
              <td class="mono">{{ job.consecutive_failures ?? 0 }}</td>
              <td class="mono">{{ job.last_success_at || '---' }}</td>
              <td class="mono">{{ fmtExitCode(job.last_exit_code) }}</td>
              <td class="action-cell">
                <button @click="toggleTask($event, job)" :class="['table-btn', job.status === 'RUNNING' ? 'btn-danger' : 'btn-success']">
                  {{ job.status === 'RUNNING' ? '暂停' : '启动' }}
                </button>
                <button @click="healTask($event, job)" class="table-btn btn-success" :disabled="job.status === 'RUNNING'" :title="job.status === 'RUNNING' ? '请先暂停任务再修复' : '立即执行一次修复尝试'">立即修复</button>
                <button @click="deleteTask($event, job)" class="table-btn btn-delete">删除</button>
              </td>
            </tr>
            <tr v-if="ubuntuJobs.length === 0">
              <td colspan="7" class="empty-row">沙盒中没有配置任何 Crontab 任务</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.clickable-row {
  cursor: pointer;
  transition: background 0.2s;
}
.clickable-row:hover {
  background: rgba(255, 255, 255, 0.05) !important;
}
.desc-cell {
  color: #aaa;
  font-size: 0.9rem;
  max-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.data-table th {
  text-align: left;
  padding: 12px 16px;
  background: rgba(255,255,255,0.03);
  color: #777;
  font-weight: 600;
  font-size: 0.85rem;
  text-transform: uppercase;
}
.status-live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 rgba(16, 185, 129, 0.6);
  animation: pulseLive 1.2s ease-out infinite;
}
@keyframes pulseLive {
  0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
  70% { box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
  100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
}
</style>
