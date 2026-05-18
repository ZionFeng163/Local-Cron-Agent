<script setup>
const props = defineProps(['currentView'])
const emit = defineEmits(['navigate'])

const navItems = [
  { id: 'dashboard', icon: 'layout-dashboard', label: '概览' },
  { id: 'tasks',     icon: 'list-todo', label: '任务' },
  { id: 'health',    icon: 'activity', label: '健康' },
  { id: 'heals',     icon: 'wrench', label: '自愈' },
  { id: 'scripts',   icon: 'file-code', label: '脚本' },
  { id: 'logs',      icon: 'scroll-text', label: '日志' },
  { id: 'chat',      icon: 'messages-square', label: 'AI' },
  { id: 'settings',  icon: 'settings', label: '设置' },
]

const iconPaths = {
  'layout-dashboard': [
    'M3 3h7v7H3z',
    'M14 3h7v4h-7z',
    'M14 10h7v11h-7z',
    'M3 14h7v7H3z',
  ],
  'list-todo': [
    'M9 6h12',
    'M9 12h12',
    'M9 18h12',
    'M4 6h.01',
    'M4 12h.01',
    'M4 18h.01',
  ],
  activity: [
    'M3 12h4l3-8 4 16 3-8h4',
  ],
  wrench: [
    'M14.5 3.5a3.5 3.5 0 0 0 4.6 4.6L10 17.2a2 2 0 1 1-2.8-2.8l9.1-9.1z',
    'M12 6l6 6',
  ],
  'file-code': [
    'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z',
    'M14 2v6h6',
    'm10 13-2 2 2 2',
    'm14 13 2 2-2 2',
  ],
  'scroll-text': [
    'M8 3h9a2 2 0 0 1 2 2v15l-4-3-4 3V5a2 2 0 0 0-2-2H6a2 2 0 0 0 0 4h3',
    'M15 8h-3',
    'M15 12h-3',
  ],
  'messages-square': [
    'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
    'M8 8h8',
    'M8 12h6',
  ],
  settings: [
    'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z',
    'M12 3v2.1',
    'M12 18.9V21',
    'M4.9 4.9 6.4 6.4',
    'M17.6 17.6 19.1 19.1',
    'M3 12h2.1',
    'M18.9 12H21',
    'M4.9 19.1 6.4 17.6',
    'M17.6 6.4 19.1 4.9',
  ],
}

const iconStrokeWidth = {
  settings: 1.35,
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-logo">
      <span class="logo-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" class="brand-icon">
          <defs>
            <linearGradient id="brandSidebarGrad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#7c3aed" />
              <stop offset="100%" stop-color="#4f46e5" />
            </linearGradient>
          </defs>
          <rect x="3" y="3" width="18" height="18" rx="5" fill="url(#brandSidebarGrad)" />
          <path d="M8 15V9h2.6l1.4 2.3L13.4 9H16v6h-2V12l-2 3-2-3v3z" fill="#fff" />
        </svg>
      </span>
      <span class="logo-text">Cron Agent</span>
    </div>
    <nav class="sidebar-nav">
      <button
        v-for="item in navItems"
        :key="item.id"
        :class="['nav-item', { active: currentView === item.id }]"
        @click="emit('navigate', item.id)"
        :title="item.label"
      >
        <span class="nav-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path
              v-for="(pathDef, idx) in iconPaths[item.icon]"
              :key="`${item.id}-${idx}`"
              :d="pathDef"
              fill="none"
              stroke="currentColor"
              :stroke-width="iconStrokeWidth[item.icon] || 1.75"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </span>
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </nav>
    <div class="sidebar-footer">
      <div class="version-tag">v1.0.0</div>
    </div>
  </aside>
</template>
