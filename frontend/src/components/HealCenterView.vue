<script setup>
import { ref, onMounted } from 'vue'

const loading = ref(false)
const loadingCatalog = ref(false)
const errorMsg = ref('')

const catalog = ref({ categories: [], actions: [] })
const records = ref([])
const total = ref(0)
const taskNameMap = ref({})

const filters = ref({
  ok: '',
  category: '',
  action: ''
})

const pagination = ref({
  limit: 50,
  offset: 0
})

const boolText = (v) => (Number(v) === 1 ? '成功' : '失败')
const triggerText = (v) => {
  if (v === 'manual') return '手动'
  if (v === 'auto_threshold') return '自动阈值'
  return v || 'unknown'
}

const getTaskName = (taskId) => {
  if (!taskId) return '---'
  return taskNameMap.value[taskId] || taskId
}

const fetchTaskMap = async () => {
  try {
    const res = await fetch('http://localhost:8000/api/jobs')
    const data = await res.json()
    const all = [...(data.internal_jobs || []), ...(data.ubuntu_jobs || [])]
    const m = {}
    all.forEach((j) => {
      if (j?.id) m[j.id] = j.name || j.id
    })
    taskNameMap.value = m
  } catch (e) {
    console.error(e)
  }
}

const fetchCatalog = async () => {
  loadingCatalog.value = true
  try {
    const res = await fetch('http://localhost:8000/api/heals/catalog')
    const data = await res.json()
    catalog.value = {
      categories: data.categories || [],
      actions: data.actions || []
    }
  } catch (e) {
    errorMsg.value = e.message || '加载返回值说明失败'
  }
  loadingCatalog.value = false
}

const fetchHistory = async () => {
  loading.value = true
  errorMsg.value = ''

  const params = new URLSearchParams()
  params.set('limit', String(pagination.value.limit))
  params.set('offset', String(pagination.value.offset))
  if (filters.value.ok !== '') params.set('ok', filters.value.ok)
  if (filters.value.category) params.set('category', filters.value.category)
  if (filters.value.action) params.set('action', filters.value.action)

  try {
    const res = await fetch(`http://localhost:8000/api/heals/history?${params.toString()}`)
    const data = await res.json()
    if (!res.ok) throw new Error(data?.detail || '加载自愈历史失败')
    records.value = data.items || []
    total.value = Number(data.total || 0)
  } catch (e) {
    errorMsg.value = e.message || '加载自愈历史失败'
  }

  loading.value = false
}

const applyFilters = () => {
  pagination.value.offset = 0
  fetchHistory()
}

const resetFilters = () => {
  filters.value = { ok: '', category: '', action: '' }
  pagination.value.offset = 0
  fetchHistory()
}

const nextPage = () => {
  const nextOffset = pagination.value.offset + pagination.value.limit
  if (nextOffset >= total.value) return
  pagination.value.offset = nextOffset
  fetchHistory()
}

const prevPage = () => {
  const nextOffset = pagination.value.offset - pagination.value.limit
  pagination.value.offset = nextOffset < 0 ? 0 : nextOffset
  fetchHistory()
}

const refreshAll = async () => {
  await fetchTaskMap()
  await fetchCatalog()
  await fetchHistory()
}

onMounted(refreshAll)
</script>

<template>
  <div class="view-container">
    <div class="view-header">
      <div>
        <h2>🛠️ 自愈中心</h2>
        <p class="view-subtitle">统一查看自愈返回值含义与历史记录</p>
      </div>
      <button class="table-btn btn-success" @click="refreshAll">刷新</button>
    </div>

    <div class="table-section">
      <h3 class="table-title">返回值说明（分类）</h3>
      <div v-if="loadingCatalog" class="empty-small">加载说明中...</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th style="width: 20%">code</th>
            <th style="width: 20%">名称</th>
            <th style="width: 60%">含义</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in catalog.categories" :key="`cat-${item.code}`">
            <td class="mono">{{ item.code }}</td>
            <td>{{ item.label }}</td>
            <td class="desc-cell">{{ item.meaning }}</td>
          </tr>
          <tr v-if="!catalog.categories.length">
            <td colspan="3" class="empty-row">暂无分类说明</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="table-section">
      <h3 class="table-title">返回值说明（动作）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 20%">code</th>
            <th style="width: 20%">名称</th>
            <th style="width: 60%">含义</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in catalog.actions" :key="`act-${item.code}`">
            <td class="mono">{{ item.code }}</td>
            <td>{{ item.label }}</td>
            <td class="desc-cell">{{ item.meaning }}</td>
          </tr>
          <tr v-if="!catalog.actions.length">
            <td colspan="3" class="empty-row">暂无动作说明</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="table-section">
      <div class="filter-bar">
        <select v-model="filters.ok" @change="applyFilters">
          <option value="">全部结果</option>
          <option value="1">仅成功</option>
          <option value="0">仅失败</option>
        </select>

        <select v-model="filters.category" @change="applyFilters">
          <option value="">全部分类</option>
          <option v-for="item in catalog.categories" :key="`fc-${item.code}`" :value="item.code">{{ item.code }}</option>
        </select>

        <select v-model="filters.action" @change="applyFilters">
          <option value="">全部动作</option>
          <option v-for="item in catalog.actions" :key="`fa-${item.code}`" :value="item.code">{{ item.code }}</option>
        </select>

        <button class="table-btn" @click="resetFilters">重置筛选</button>
      </div>

      <h3 class="table-title">自愈历史记录</h3>

      <div v-if="loading" class="empty-small">加载记录中...</div>
      <div v-else-if="errorMsg" class="health-error">❌ {{ errorMsg }}</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th style="width: 14%">时间</th>
            <th style="width: 14%">任务</th>
            <th style="width: 10%">触发</th>
            <th style="width: 14%">分类</th>
            <th style="width: 14%">动作</th>
            <th style="width: 8%">结果</th>
            <th style="width: 8%">退出码</th>
            <th style="width: 18%">说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in records" :key="row.heal_id">
            <td class="mono">{{ row.created_at || '---' }}</td>
            <td :title="row.task_id">{{ getTaskName(row.task_id) }}</td>
            <td>{{ triggerText(row.trigger) }}</td>
            <td class="mono">{{ row.category || '---' }}</td>
            <td class="mono">{{ row.action || '---' }}</td>
            <td>
              <span :class="['status-badge', Number(row.ok) === 1 ? 'badge-running' : 'badge-paused']">
                {{ boolText(row.ok) }}
              </span>
            </td>
            <td class="mono">{{ row.exit_code === null || row.exit_code === undefined ? '---' : row.exit_code }}</td>
            <td class="desc-cell">{{ row.message || '-' }}</td>
          </tr>
          <tr v-if="records.length === 0">
            <td colspan="8" class="empty-row">暂无自愈历史记录</td>
          </tr>
        </tbody>
      </table>

      <div class="pager-row" v-if="!loading">
        <span class="pager-text">共 {{ total }} 条，当前 {{ pagination.offset + 1 }} - {{ Math.min(pagination.offset + pagination.limit, total) }}</span>
        <div class="pager-actions">
          <button class="table-btn" @click="prevPage" :disabled="pagination.offset === 0">上一页</button>
          <button class="table-btn" @click="nextPage" :disabled="pagination.offset + pagination.limit >= total">下一页</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.filter-bar select {
  height: 34px;
  border-radius: 8px;
  border: 1px solid rgba(0, 0, 0, 0.12);
  background: #fff;
  padding: 0 10px;
  color: #334155;
}

.pager-row {
  margin-top: 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.pager-text {
  color: #64748b;
  font-size: 0.85rem;
}

.pager-actions {
  display: flex;
  gap: 8px;
}
</style>
