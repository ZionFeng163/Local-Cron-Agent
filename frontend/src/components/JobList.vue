<script setup>
const props = defineProps(['title', 'jobs', 'apiPath'])
const emit = defineEmits(['refresh'])

const toggleJob = async (job) => {
  if (job.isProcessing) return
  
  const isInternal = props.apiPath.includes('internal')
  const action = job.status === 'RUNNING' ? 'pause' : 'resume'
  const payload = isInternal
        ? { job_id: job.id, action } 
        : { raw_line: job.raw, action }
  
  // 内置的心跳任务是在主机的 RAM 内存中跑的，不需要去和沙盒"通信"以及等待耗时 I/O。
  // 所以只有沙盒任务才赋予 "通信中..." 的锁定降临视觉效果！
  if (!isInternal) {
    job.isProcessing = true
  }
  
  try {
      await fetch(`http://localhost:8000${props.apiPath}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      
      // 乐观更新视觉效果，防止用户觉得卡顿
      job.status = action === 'pause' ? 'PAUSED' : 'RUNNING'
      
      emit('refresh')
  } catch(e) {
      console.error(e)
  } finally {
      if (!isInternal) job.isProcessing = false
  }
}

// 格式化输出长命令，去除不必要的头部
const getDisplayCmd = (job) => {
  if (job.cmd && job.cmd.length > 50) return job.cmd.substring(0, 48) + '...'
  return job.cmd || '---'
}
</script>

<template>
  <div class="job-group">
    <h3>{{ title }}</h3>
    <div class="jobs-list">
      <div v-for="job in jobs" :key="job.id" class="job-item card">
         <div class="job-info">
           <h4>{{ job.name || getDisplayCmd(job) }}</h4>
           <div class="meta">{{ job.next_run || job.expr || 'No Config' }}</div>
         </div>
         <div class="job-action">
           <button 
             @click="toggleJob(job)" 
             :disabled="job.isProcessing"
             :class="['toggle-btn', job.status === 'RUNNING' ? 'btn-danger' : 'btn-success']"
             :style="{ opacity: job.isProcessing ? 0.5 : 1, cursor: job.isProcessing ? 'wait' : 'pointer' }"
           >
             {{ job.isProcessing ? '通信中...' : (job.status === 'RUNNING' ? '暂停' : '启动') }}
           </button>
         </div>
      </div>
      <div v-if="!jobs || jobs.length === 0" class="empty">暂时没有任何悬停的任务</div>
    </div>
  </div>
</template>
