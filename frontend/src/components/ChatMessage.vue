<script setup>
/**
 * 单条消息组件。
 * Props:
 *   message: { role, content, tools?, isStreaming? }
 *   isCustomerMode: boolean - 是否为培训模式（assistant 显示为"客户"）
 */
import { computed } from 'vue'

const props = defineProps({
  message: { type: Object, required: true },
  isCustomerMode: { type: Boolean, default: false },
})

const avatar = computed(() => {
  if (props.message.role === 'user') return '我'
  return props.isCustomerMode ? '客' : 'AI'
})

const roleLabel = computed(() => {
  if (props.message.role === 'user') return '销售顾问'
  return props.isCustomerMode ? '客户' : 'LV 销售培训助手'
})

/** 移除 LLM 输出中的 think 标签和结束标记 */
const displayContent = computed(() => {
  if (!props.message.content) return ''
  let text = props.message.content
  // 移除 <think>...</think> 块
  text = text.replace(/<think>[\s\S]*?<\/think>/gi, '')
  // 移除孤立的 think 标签
  text = text.replace(/<\/?think>/gi, '')
  // 移除自动结束标记
  text = text.replace(/\[\[END_TRAINING\]\]/gi, '')
  return text.trim()
})

function formatArgs(args) {
  if (!args) return ''
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}
</script>

<template>
  <div class="message" :class="message.role">
    <div class="message-avatar">{{ avatar }}</div>
    <div class="message-content">
      <div class="message-role">{{ roleLabel }}</div>

      <!-- 工具调用列表 -->
      <div v-if="message.tools && message.tools.length" class="tool-calls">
        <div v-for="(tool, idx) in message.tools" :key="idx" class="tool-call">
          <div class="tool-call-header">
            <span>🔧</span>
            <span class="tool-call-name">{{ tool.name }}</span>
          </div>
          <div v-if="tool.arguments" class="tool-call-args">{{ formatArgs(tool.arguments) }}</div>
          <div v-if="tool.result !== undefined" class="tool-result">
            <span style="color: var(--text-tertiary);">结果：</span>{{ tool.result }}
            <span v-if="tool.truncated" style="color: var(--text-tertiary);">...(已截断)</span>
          </div>
        </div>
      </div>

      <!-- 消息气泡 -->
      <div class="message-bubble">
        <template v-if="displayContent">{{ displayContent }}</template>
        <template v-else-if="message.isStreaming && !message.tools?.length">
          <div class="typing-indicator">
            <span></span><span></span><span></span>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
