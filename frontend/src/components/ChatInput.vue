<script setup>
/**
 * 聊天输入框组件。
 * Emits: send(message), stop
 * Props: disabled (流式输出中禁用), placeholder
 *
 * 流式输出时（disabled=true），发送按钮变为停止按钮。
 */
import { ref, nextTick, watch } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '输入消息，向 LV 销售培训助手提问...' },
})

const emit = defineEmits(['send', 'stop'])
const text = ref('')
const textarea = ref(null)

function autoResize() {
  const el = textarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

function handleSend() {
  const msg = text.value.trim()
  if (!msg || props.disabled) return
  emit('send', msg)
  text.value = ''
  nextTick(autoResize)
}

function handleKeydown(e) {
  // Enter 发送，Shift+Enter 换行
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

watch(text, () => nextTick(autoResize))
</script>

<template>
  <div class="input-area">
    <div class="input-wrapper">
      <textarea
        ref="textarea"
        v-model="text"
        class="chat-input"
        :placeholder="placeholder"
        :disabled="disabled"
        rows="1"
        @keydown="handleKeydown"
        @input="autoResize"
      ></textarea>
      <!-- 流式输出时显示停止按钮，否则显示发送按钮 -->
      <button
        v-if="disabled"
        class="send-btn stop-btn"
        @click="emit('stop')"
        title="停止生成"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      </button>
      <button
        v-else
        class="send-btn"
        :disabled="!text.trim()"
        @click="handleSend"
        title="发送 (Enter)"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
        </svg>
      </button>
    </div>
  </div>
</template>
