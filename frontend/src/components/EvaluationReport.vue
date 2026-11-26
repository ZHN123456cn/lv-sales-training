<script setup>
/**
 * 教练评价报告展示组件。
 * Props: evaluation (markdown 文本), studentName, studentId, scenarioTitle
 * Emits: restart, back
 */
import { computed } from 'vue'

const props = defineProps({
  evaluation: { type: String, default: '' },
  studentName: { type: String, default: '' },
  studentId: { type: String, default: '' },
  scenarioTitle: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['restart', 'back'])

/** 过滤 think 标签 */
const displayEvaluation = computed(() => {
  if (!props.evaluation) return ''
  let text = props.evaluation
  text = text.replace(/<think>[\s\S]*?<\/think>/gi, '')
  text = text.replace(/<\/?think>/gi, '')
  return text.trim()
})
</script>

<template>
  <div class="form-stage">
    <div class="form-card form-card-wide evaluation-card">
      <div class="form-icon">📋</div>
      <h2 class="form-title">演练评估报告</h2>
      <p class="form-desc">
        学员：{{ studentName }}（{{ studentId }}） | 场景：{{ scenarioTitle }}
      </p>

      <div v-if="loading" class="evaluation-loading">
        <div class="typing-indicator">
          <span></span><span></span><span></span>
        </div>
        <p>教练正在分析你的表现，请稍候...</p>
      </div>

      <div v-else class="evaluation-content">
        <!-- 简单的 markdown 渲染：保留换行 -->
        <div class="markdown-body">{{ displayEvaluation }}</div>
      </div>

      <div class="form-actions">
        <button class="btn-secondary" @click="emit('back')">查看对话</button>
        <button class="btn-primary" @click="emit('restart')">重新开始培训</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evaluation-card {
  max-width: 760px;
}
.evaluation-loading {
  text-align: center;
  padding: 40px 0;
  color: var(--text-secondary);
}
.evaluation-loading p {
  margin-top: 16px;
  font-size: 14px;
}
.markdown-body {
  white-space: pre-wrap;
  line-height: 1.8;
  font-size: 15px;
  text-align: left;
  padding: 24px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  max-height: 60vh;
  overflow-y: auto;
}
</style>
