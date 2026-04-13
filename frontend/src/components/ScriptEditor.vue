<script setup>
import { ref, onMounted } from 'vue'

const currentPath = ref('/home/ubuntu')
const items = ref([])
const selectedFile = ref(null)
const fileContent = ref('')
const isSaving = ref(false)
const isLoading = ref(false)
const saveMsg = ref('')

const fetchDir = async (path) => {
  isLoading.value = true
  try {
    const res = await fetch(`http://localhost:8000/api/sandbox/ls?path=${encodeURIComponent(path)}`)
    const data = await res.json()
    currentPath.value = data.path
    items.value = data.items || []
  } catch (e) {
    console.error(e)
  }
  isLoading.value = false
}

const openItem = async (item) => {
  if (item.is_dir) {
    await fetchDir(item.path)
    selectedFile.value = null
    fileContent.value = ''
    saveMsg.value = ''
  } else {
    selectedFile.value = item.path
    saveMsg.value = ''
    try {
      const res = await fetch(`http://localhost:8000/api/sandbox/read?path=${encodeURIComponent(item.path)}`)
      const data = await res.json()
      fileContent.value = data.content || ''
      if (data.error) saveMsg.value = '⚠️ ' + data.error
    } catch (e) {
      fileContent.value = '无法读取文件: ' + e.message
    }
  }
}

const goUp = () => {
  const parent = currentPath.value.replace(/\/[^/]+$/, '') || '/'
  fetchDir(parent)
  selectedFile.value = null
  fileContent.value = ''
  saveMsg.value = ''
}

const saveFile = async () => {
  if (!selectedFile.value) return
  isSaving.value = true
  saveMsg.value = ''
  try {
    const res = await fetch('http://localhost:8000/api/sandbox/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: selectedFile.value, content: fileContent.value })
    })
    const data = await res.json()
    saveMsg.value = data.status === 'success' ? '✅ 保存成功' : '❌ ' + data.msg
  } catch (e) {
    saveMsg.value = '❌ 保存失败: ' + e.message
  }
  isSaving.value = false
}

onMounted(() => fetchDir(currentPath.value))
</script>

<template>
  <div class="view-container script-editor-layout">
    <div class="view-header">
      <h2>📄 沙盒脚本编辑器</h2>
      <p class="view-subtitle">浏览、查看并编辑沙盒内的文件</p>
    </div>

    <div class="editor-body">
      <div class="file-browser">
        <div class="browser-header">
          <button class="nav-up-btn" @click="goUp" :disabled="currentPath === '/'">⬆ 上级</button>
          <span class="current-path">{{ currentPath }}</span>
        </div>
        <div class="file-list">
          <div v-if="isLoading" class="empty-small">加载中...</div>
          <div
            v-for="item in items"
            :key="item.path"
            :class="['file-item', { 'is-dir': item.is_dir, 'selected': selectedFile === item.path }]"
            @click="openItem(item)"
          >
            <span class="file-icon">{{ item.is_dir ? '📁' : '📄' }}</span>
            <span class="file-name">{{ item.name }}</span>
            <span class="file-size" v-if="!item.is_dir">{{ item.size }}B</span>
          </div>
          <div v-if="!isLoading && items.length === 0" class="empty-small">此目录为空</div>
        </div>
      </div>

      <div class="code-editor">
        <div v-if="!selectedFile" class="editor-placeholder">
          <p>👈 在左侧选择一个文件以开始编辑</p>
        </div>
        <template v-else>
          <div class="editor-toolbar">
            <span class="editor-filename">{{ selectedFile }}</span>
            <div class="editor-actions">
              <span v-if="saveMsg" class="save-msg">{{ saveMsg }}</span>
              <button class="save-btn" @click="saveFile" :disabled="isSaving">
                {{ isSaving ? '保存中...' : '💾 保存' }}
              </button>
            </div>
          </div>
          <textarea
            v-model="fileContent"
            class="code-textarea"
            spellcheck="false"
            wrap="off"
          ></textarea>
        </template>
      </div>
    </div>
  </div>
</template>
