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
  if (!confirm(`确定要永久删除此任务吗？\n${job.command || job.name}`)) return
  try {
    await fetch('http://localhost:8000/api/jobs/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: job.id })
    })
    emit('refresh')
  } catch (e) { console.error(e) }
}
</script>

<template>
  <div class="view-container">
    <div class="view-header">
      <h2>📝 任务管理</h2>
      <p class="view-subtitle">查看、暂停、启动或删除所有定时任务</p>
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
              <span :class="['status-badge', job.status === 'RUNNING' ? 'badge-running' : 'badge-paused']">
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
      <h3 class="table-title">沙盒 Crontab 任务</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 25%">任务名称</th>
            <th style="width: 35%">详细描述</th>
            <th style="width: 15%">状态</th>
            <th style="width: 25%">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="ubuntuLoading">
            <td colspan="4" class="empty-row">⏳ 首次同步中...</td>
          </tr>
          <template v-else>
            <tr v-for="job in ubuntuJobs" :key="job.id" class="clickable-row" @click="emit('showDetail', job)">
              <td>{{ job.name || '未命名任务' }}</td>
              <td class="desc-cell">{{ job.description || job.command }}</td>
              <td>
                <span :class="['status-badge', job.status === 'RUNNING' ? 'badge-running' : 'badge-paused']">
                  {{ job.status === 'RUNNING' ? '运行中' : '已暂停' }}
                </span>
              </td>
              <td class="action-cell">
                <button @click="toggleTask($event, job)" :class="['table-btn', job.status === 'RUNNING' ? 'btn-danger' : 'btn-success']">
                  {{ job.status === 'RUNNING' ? '暂停' : '启动' }}
                </button>
                <button @click="deleteTask($event, job)" class="table-btn btn-delete">删除</button>
              </td>
            </tr>
            <tr v-if="ubuntuJobs.length === 0">
              <td colspan="4" class="empty-row">沙盒中没有配置任何 Crontab 任务</td>
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
</style>
