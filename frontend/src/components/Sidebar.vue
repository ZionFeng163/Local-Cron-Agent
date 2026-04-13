<script setup>
import { defineProps, defineEmits } from 'vue'

const props = defineProps(['currentView'])
const emit = defineEmits(['navigate'])

const navItems = [
  { id: 'dashboard', icon: '📊', label: '概览' },
  { id: 'tasks',     icon: '📝', label: '任务' },
  { id: 'scripts',   icon: '📄', label: '脚本' },
  { id: 'logs',      icon: '📋', label: '日志' },
  { id: 'chat',      icon: '💬', label: 'AI' },
  { id: 'settings',  icon: '⚙️', label: '设置' },
]
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-logo">
      <span class="logo-icon">🤖</span>
      <span class="logo-text">Cron Agent</span>
    </div>
    <nav class="sidebar-nav">
      <button
        v-for="item in navItems"
        :key="item.id"
        :class="['nav-item', { active: currentView === item.id }]"
        @click="emit('navigate', item.id)"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </nav>
    <div class="sidebar-footer">
      <div class="version-tag">v1.0.0</div>
    </div>
  </aside>
</template>
