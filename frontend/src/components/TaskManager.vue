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
  if (!job.needs_repair) {
    alert('当前任务最近一次执行正常，无需修复。')
    return
  }
  if (job.status === 'RUNNING' && job.needs_repair) {
    const ok = confirm(
      `将对「${job.name || job.script_path || job.id}」执行一次诊断试跑。\n\n` +
      '如果脚本仍然返回非 0，系统会暂停调度，避免它继续按 cron 反复失败。\n\n' +
      '是否继续？'
    )
    if (!ok) return
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
    const title = result.ok ? '修复结果：成功' : '修复结果：未成功'
    const lines = [
      title,
      result.summary || result.msg || '修复流程已结束'
    ]
    if (result.schedule_changed) {
      lines.push(`调度变化: 已变为「${result.schedule_label || '未知'}」`)
    } else if (result.schedule_label) {
      lines.push(`当前调度: ${result.schedule_label}`)
    }
    if (result.exit_code !== undefined && result.exit_code !== null) lines.push(`本次试跑退出码: ${result.exit_code}`)
    if (result.failures !== undefined && result.failures !== null) lines.push(`连续失败次数: ${result.failures}`)
    if (result.next_step) lines.push(`下一步: ${result.next_step}`)
    if (result.category || result.action) {
      lines.push(`诊断明细: ${result.category || 'unknown'} / ${result.action || 'unknown'}`)
    }
    alert(lines.join('\n'))

    emit('refresh')
  } catch (e) {
    console.error(e)
    alert(e.message || '修复失败')
  }
}

const fmtExitCode = (v) => (v === null || v === undefined ? '---' : String(v))
const scheduleLabel = (job) => job.schedule_label || (job.status === 'RUNNING' ? '调度中' : '已暂停')
const healthLabel = (job) => job.health_label || '未检测'
const healthBadgeClass = (job) => {
  if (job.health_status === 'HEALTHY') return 'badge-running'
  if (job.health_status === 'FAILING') return 'badge-failing'
  return 'badge-unknown'
}
const scheduleBadgeClass = (job) => {
  if (job.status !== 'RUNNING') return 'badge-paused'
  if (job.health_status === 'FAILING') return 'badge-failing'
  return 'badge-running'
}
const scheduleDisplayLabel = (job) => {
  if (job.status === 'RUNNING' && job.health_status === 'FAILING') return '异常调度中'
  return scheduleLabel(job)
}
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
            <th style="width: 23%">详细描述</th>
            <th style="width: 9%">调度</th>
            <th style="width: 9%">健康</th>
            <th style="width: 9%">连续失败</th>
            <th style="width: 14%">最近成功</th>
            <th style="width: 8%">退出码</th>
            <th style="width: 19%">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="ubuntuLoading">
            <td colspan="8" class="empty-row">⏳ 首次同步中...</td>
          </tr>
          <template v-else>
            <tr v-for="job in ubuntuJobs" :key="job.id" class="clickable-row" @click="emit('showDetail', job)">
              <td>{{ job.name || '未命名任务' }}</td>
              <td class="desc-cell">{{ job.description || job.script_path }}</td>
              <td>
                <span :class="['status-badge', scheduleBadgeClass(job), job.status === 'RUNNING' ? 'status-live' : '']" :title="job.status_explanation">
                  <span v-if="job.status === 'RUNNING'" class="live-dot"></span>
                  {{ scheduleDisplayLabel(job) }}
                </span>
              </td>
              <td>
                <span :class="['status-badge', healthBadgeClass(job)]" :title="job.status_explanation">{{ healthLabel(job) }}</span>
              </td>
              <td class="mono">{{ job.consecutive_failures ?? 0 }}</td>
              <td class="mono">{{ job.last_success_at || '---' }}</td>
              <td class="mono">{{ fmtExitCode(job.last_exit_code) }}</td>
              <td class="action-cell">
                <button @click="toggleTask($event, job)" :class="['table-btn', job.status === 'RUNNING' ? 'btn-danger' : 'btn-success']">
                  {{ job.status === 'RUNNING' ? '暂停' : '启动' }}
                </button>
                <button
                  @click="healTask($event, job)"
                  :disabled="!job.needs_repair"
                  :class="['table-btn', job.needs_repair ? 'btn-success' : 'btn-disabled']"
                  :title="job.needs_repair ? '诊断脚本、权限、Cron 条目并试跑一次；如果仍失败会暂停调度' : '最近一次执行正常，无需修复'"
                >
                  {{ job.needs_repair ? '诊断修复' : '无需修复' }}
                </button>
                <button @click="deleteTask($event, job)" class="table-btn btn-delete">删除</button>
              </td>
            </tr>
            <tr v-if="ubuntuJobs.length === 0">
              <td colspan="8" class="empty-row">沙盒中没有配置任何脚本型定时任务</td>
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
