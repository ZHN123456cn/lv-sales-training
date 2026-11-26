<script setup>
/**
 * 学生信息表单：输入姓名和学号。
 * Emits: submit({ name, id }), back
 */
import { ref } from 'vue'

const emit = defineEmits(['submit', 'back'])
const name = ref('')
const studentId = ref('')

function handleSubmit() {
  if (!name.value.trim() || !studentId.value.trim()) return
  emit('submit', { name: name.value.trim(), id: studentId.value.trim() })
}
</script>

<template>
  <div class="form-stage">
    <div class="form-card">
      <div class="form-icon">👨‍🎓</div>
      <h2 class="form-title">学员信息</h2>
      <p class="form-desc">请填写你的基本信息，用于培训记录和教练评价。</p>

      <div class="form-group">
        <label class="form-label">姓名</label>
        <input
          v-model="name"
          type="text"
          class="form-input"
          placeholder="请输入你的姓名"
          @keyup.enter="handleSubmit"
        />
      </div>

      <div class="form-group">
        <label class="form-label">学号</label>
        <input
          v-model="studentId"
          type="text"
          class="form-input"
          placeholder="请输入你的学号"
          @keyup.enter="handleSubmit"
        />
      </div>

      <div class="form-actions">
        <button class="btn-secondary" @click="emit('back')">返回</button>
        <button
          class="btn-primary"
          :disabled="!name.trim() || !studentId.trim()"
          @click="handleSubmit"
        >
          下一步：选择场景 →
        </button>
      </div>
    </div>
  </div>
</template>
