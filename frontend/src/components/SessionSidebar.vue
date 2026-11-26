<script setup>
/**
 * 会话侧边栏：Session 列表、新建对话、Agent 切换。
 * Props:
 *   sessions: SessionMeta[]
 *   currentSessionId: string | null
 *   agents: AgentInfo[]
 *   currentAgentId: string
 * Emits:
 *   new-chat, select-session(id), delete-session(id), switch-agent(id)
 */
defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: null },
  agents: { type: Array, default: () => [] },
  currentAgentId: { type: String, default: 'main' },
})

const emit = defineEmits(['new-chat', 'select-session', 'delete-session', 'switch-agent'])

function formatDate(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const now = new Date()
    const diff = now - d
    if (diff < 60000) return '刚刚'
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
    return d.toLocaleDateString('zh-CN')
  } catch {
    return iso
  }
}
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <div class="sidebar-title">
        <span>👜</span> LV 销售培训
      </div>
      <div class="sidebar-subtitle">ReAct Agent · RAG 知识库</div>
      <button class="new-chat-btn" @click="emit('new-chat')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M12 5v14M5 12h14" />
        </svg>
        新建对话
      </button>
    </div>

    <div class="sidebar-section">历史对话</div>
    <div class="session-list">
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === currentSessionId }"
        @click="emit('select-session', s.id)"
      >
        <div class="session-title">{{ s.title || '新对话' }}</div>
        <div class="session-meta">{{ formatDate(s.updated_at) }}</div>
        <button
          class="session-delete"
          @click.stop="emit('delete-session', s.id)"
          title="删除对话"
        >✕</button>
      </div>
      <div v-if="!sessions.length" style="padding: 16px; text-align: center; color: var(--text-tertiary); font-size: 13px;">
        暂无历史对话
      </div>
    </div>

    <div class="agent-selector">
      <div class="agent-label">当前 Agent</div>
      <select
        class="agent-select"
        :value="currentAgentId"
        @change="emit('switch-agent', $event.target.value)"
      >
        <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.id }}</option>
      </select>
    </div>
  </aside>
</template>
