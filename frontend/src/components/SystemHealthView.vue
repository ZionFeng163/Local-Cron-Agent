<script setup>
import { ref, onMounted } from 'vue'

const report = ref(null)
const loading = ref(false)
const checking = ref(false)
const errorMsg = ref('')

const loadLatest = async () => {
  loading.value = true
  errorMsg.value = ''
  try {
    const res = await fetch('http://localhost:8000/api/system/health')
    const data = await res.json()
    report.value = data
  } catch (e) {
    errorMsg.value = e.message || '加载失败'
  }
  loading.value = false
}

const triggerCheck = async () => {
  checking.value = true
  errorMsg.value = ''
  try {
    const res = await fetch('http://localhost:8000/api/system/health/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto_heal: true })
    })
    const data = await res.json()
    report.value = data.report
  } catch (e) {
    errorMsg.value = e.message || '检查失败'
  }
  checking.value = false
}

const metricList = () => {
  if (!report.value?.metrics) return []
  return Object.entries(report.value.metrics).map(([key, metric]) => ({ key, ...metric }))
}

const boolText = (v) => (v ? '正常' : '异常')

onMounted(loadLatest)
</script>

<template>
  <div class="view-container">
    <div class="view-header">
      <div>
        <h2>🩺 系统健康检查</h2>
        <p class="view-subtitle">展示最近一次检查结果，并支持一键手动体检 + 自愈</p>
      </div>
      <button class="table-btn btn-success" @click="triggerCheck" :disabled="checking">
        {{ checking ? '检查中...' : '立即检查并自愈' }}
      </button>
    </div>

    <div v-if="loading" class="empty-small">加载健康报告中...</div>
    <div v-else-if="errorMsg" class="health-error">❌ {{ errorMsg }}</div>
    <div v-else-if="!report" class="empty-small">暂无健康报告</div>
    <template v-else>
      <div class="stats-grid health-stats-grid">
        <div class="stat-card" :class="report.overall_is_normal ? 'stat-running' : 'stat-paused'">
          <div class="stat-number">{{ boolText(report.overall_is_normal) }}</div>
          <div class="stat-label">整体状态</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{{ report.checked_at || '---' }}</div>
          <div class="stat-label">最近检查时间</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{{ report.trigger || 'unknown' }}</div>
          <div class="stat-label">触发来源</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{{ report.self_heal?.summary || '未触发' }}</div>
          <div class="stat-label">自愈结果</div>
        </div>
      </div>

      <div class="table-section">
        <h3 class="table-title">指标明细（含是否正常）</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th style="width: 20%">指标</th>
              <th style="width: 20%">当前值</th>
              <th style="width: 20%">正常阈值</th>
              <th style="width: 15%">状态</th>
              <th style="width: 25%">说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="metric in metricList()" :key="metric.key">
              <td>{{ metric.label }}</td>
              <td class="mono">{{ metric.value }}</td>
              <td>{{ metric.threshold }}</td>
              <td>
                <span :class="['status-badge', metric.is_normal ? 'badge-running' : 'badge-paused']">
                  {{ metric.is_normal ? '正常' : '异常' }}
                </span>
              </td>
              <td class="desc-cell">{{ metric.detail }}</td>
            </tr>
            <tr v-if="metricList().length === 0">
              <td colspan="5" class="empty-row">暂无指标数据</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="table-section" v-if="report.top_process">
        <h3 class="table-title">当前高负载进程</h3>
        <div class="section-card">
          <div class="mono">{{ report.top_process }}</div>
        </div>
      </div>

      <div class="table-section" v-if="report.self_heal?.actions?.length">
        <h3 class="table-title">自愈动作记录</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th style="width: 35%">动作</th>
              <th style="width: 15%">结果</th>
              <th style="width: 25%">stdout</th>
              <th style="width: 25%">stderr</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(action, i) in report.self_heal.actions" :key="i">
              <td>{{ action.action }}</td>
              <td>
                <span :class="['status-badge', action.success ? 'badge-running' : 'badge-paused']">
                  {{ action.success ? '成功' : '失败' }}
                </span>
              </td>
              <td class="desc-cell">{{ action.stdout || '-' }}</td>
              <td class="desc-cell">{{ action.stderr || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="report.error" class="health-error">❌ 检查异常: {{ report.error }}</div>
    </template>
  </div>
</template>
