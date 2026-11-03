<script setup>
/**
 * 场景选择器：卡片式展示所有模拟场景。
 * Props: scenarios
 * Emits: select(scenarioId), back
 */
defineProps({
  scenarios: { type: Array, default: () => [] },
})

const emit = defineEmits(['select', 'back'])
</script>

<template>
  <div class="form-stage">
    <div class="form-card form-card-wide">
      <div class="form-icon">🎭</div>
      <h2 class="form-title">选择模拟场景</h2>
      <p class="form-desc">选择一个客户场景，系统将模拟该类型的顾客与你进行销售对话演练。</p>

      <div class="scenario-grid">
        <div
          v-for="s in scenarios"
          :key="s.id"
          class="scenario-card"
          @click="emit('select', s.id)"
        >
          <div class="scenario-icon">{{ s.icon }}</div>
          <div class="scenario-title">{{ s.title }}</div>
          <div class="scenario-desc">{{ s.description }}</div>
          <div class="scenario-cta">开始演练 →</div>
        </div>
      </div>

      <div class="form-actions">
        <button class="btn-secondary" @click="emit('back')">← 返回</button>
      </div>
    </div>
  </div>
</template>
