<script setup>
import { onUnmounted, ref } from 'vue'

const emit = defineEmits(['start'])

const demoMessages = [
  {
    role: 'customer',
    name: '模拟客户',
    text: '您好，我想选一份礼物，送给一位重要的客户，但不想太张扬。',
  },
  {
    role: 'advisor',
    name: '销售学员',
    text: '了解。您更看重礼物的实用性，还是希望它能体现品牌心意？预算大概在哪个范围？',
  },
  {
    role: 'customer',
    name: '模拟客户',
    text: '最好日常使用得到，预算一万元左右。',
  },
  {
    role: 'advisor',
    name: '销售学员',
    text: '可以考虑经典帆布系列。设计克制，也适合日常通勤。我为您比较两款，看看哪一款更符合对方的使用习惯。',
  },
  {
    role: 'customer',
    name: '模拟客户',
    text: '这样的推荐比较贴合我的需要，可以再看看细节。',
  },
]

const visibleCount = ref(demoMessages.length)
const isPlaying = ref(false)
const hasPlayed = ref(false)
const showFeedback = ref(true)
const demoPanel = ref(null)
let playTimer = null

function playDemo(shouldScroll = false) {
  clearTimeout(playTimer)
  hasPlayed.value = true
  isPlaying.value = true
  showFeedback.value = false
  visibleCount.value = 0

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (shouldScroll) {
    demoPanel.value?.scrollIntoView({
      behavior: reducedMotion ? 'auto' : 'smooth',
      block: 'center',
    })
  }

  if (reducedMotion) {
    visibleCount.value = demoMessages.length
    showFeedback.value = true
    isPlaying.value = false
    return
  }

  const revealNext = () => {
    visibleCount.value += 1
    if (visibleCount.value >= demoMessages.length) {
      isPlaying.value = false
      showFeedback.value = true
      playTimer = null
      return
    }
    playTimer = setTimeout(revealNext, 700)
  }

  playTimer = setTimeout(revealNext, 250)
}

onUnmounted(() => clearTimeout(playTimer))
</script>

<template>
  <section class="demo-home">
    <div class="hero-grid">
      <div class="hero-copy">
        <div class="eyebrow">
          <span class="eyebrow-rule"></span>
          AI SALES TRAINING · 销售情景演练
        </div>

        <h1>
          让销售训练，<br>
          <span>接近真实接待。</span>
        </h1>

        <p class="hero-lead">
          为销售学员打造的 AI 情景演练平台。与模拟客户真实对话，
          在每一次需求挖掘、产品推荐与异议处理之后，获得可复盘的教练反馈。
        </p>

        <div class="hero-actions">
          <button class="primary-action" type="button" @click="emit('start')">
            开始培训
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 12h13M13 6l6 6-6 6" />
            </svg>
          </button>
          <button class="text-action" type="button" @click="playDemo(true)">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 7 8 5-8 5V7Z" />
            </svg>
            播放示例
          </button>
        </div>

        <div class="hero-capabilities" aria-label="核心能力">
          <span>多场景客户模拟</span>
          <span>实时对话演练</span>
          <span>专业教练评价</span>
        </div>
      </div>

      <div class="demo-column">
        <div class="demo-overline">
          <span>示例演练 · 送礼推荐</span>
          <span>DEMO / 01</span>
        </div>

        <article ref="demoPanel" class="demo-stage" aria-label="销售对话演示">
          <header class="demo-stage-header">
            <div class="demo-scenario">
              <span class="scenario-mark" aria-hidden="true">LV</span>
              <div>
                <strong>重要客户送礼</strong>
                <span>需求挖掘与产品推荐</span>
              </div>
            </div>
            <span class="live-badge"><i></i> 情景模拟</span>
          </header>

          <div class="dialogue-list" aria-live="polite">
            <div
              v-for="(message, index) in demoMessages.slice(0, visibleCount)"
              :key="index"
              class="demo-message"
              :class="message.role"
            >
              <span class="message-name">{{ message.name }}</span>
              <p>{{ message.text }}</p>
            </div>
          </div>

          <footer v-if="showFeedback" class="coach-feedback">
            <div class="coach-label">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="m5 12 4 4L19 6" />
              </svg>
              AI 教练反馈
            </div>
            <p>先确认场合与预算，再给出选择，推荐逻辑完整。</p>
            <div class="feedback-tags">
              <span>需求挖掘清晰</span>
              <span>推荐有依据</span>
            </div>
          </footer>
          <div v-else class="feedback-placeholder">
            <span></span><span></span><span></span>
            正在生成演练片段
          </div>
        </article>

        <div class="demo-controls">
          <span>示例对话，用于展示培训流程</span>
          <button type="button" :disabled="isPlaying" @click="playDemo(false)">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 11a8 8 0 1 0-2.34 5.66M20 4v7h-7" />
            </svg>
            {{ isPlaying ? '播放中' : hasPlayed ? '重新播放' : '播放示例' }}
          </button>
        </div>
      </div>
    </div>

    <div class="workflow" aria-label="培训流程">
      <div class="workflow-title">
        <span>一套完整的训练闭环</span>
        <small>TRAINING WORKFLOW</small>
      </div>
      <ol>
        <li>
          <span>01</span>
          <div><strong>选择情景</strong><small>匹配不同客户与接待任务</small></div>
        </li>
        <li>
          <span>02</span>
          <div><strong>对话演练</strong><small>与 AI 客户进行实时交流</small></div>
        </li>
        <li>
          <span>03</span>
          <div><strong>评价改进</strong><small>获得教练反馈与复盘建议</small></div>
        </li>
      </ol>
    </div>

    <div class="technology-note">
      <span>REACT AGENT</span><i></i><span>RAG 知识检索</span><i></i><span>MCP 工具调用</span>
    </div>
  </section>
</template>

<style scoped>
.demo-home {
  --paper: #f4f0e8;
  --paper-deep: #eae3d7;
  --ink: #211d18;
  --muted: #756d62;
  --line: #d5ccbd;
  --gold: #a27c48;
  --wine: #6f2526;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  background:
    linear-gradient(90deg, transparent 49.95%, rgba(108, 88, 61, 0.06) 50%, transparent 50.05%),
    var(--paper);
  color: var(--ink);
}

.hero-grid {
  width: min(1440px, 100%);
  min-height: calc(100vh - 154px);
  margin: 0 auto;
  padding: clamp(36px, 5vw, 76px) clamp(24px, 5vw, 80px) 42px;
  display: grid;
  grid-template-columns: minmax(0, 0.94fr) minmax(400px, 0.86fr);
  gap: clamp(40px, 7vw, 108px);
  align-items: center;
}

.hero-copy {
  max-width: 650px;
  animation: rise-in 600ms cubic-bezier(0.16, 1, 0.3, 1) both;
}

.eyebrow {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 28px;
  color: var(--wine);
  font-size: 0.72rem;
  font-weight: 650;
  letter-spacing: 0.16em;
}

.eyebrow-rule {
  width: 32px;
  height: 1px;
  background: var(--wine);
}

h1 {
  margin: 0;
  font-family: Georgia, 'Songti SC', 'STSong', 'SimSun', serif;
  font-size: clamp(3rem, 5.2vw, 5.5rem);
  font-weight: 400;
  line-height: 1.12;
  letter-spacing: -0.055em;
}

h1 span {
  color: var(--wine);
}

.hero-lead {
  max-width: 37rem;
  margin: 28px 0 0;
  color: var(--muted);
  font-size: 1rem;
  line-height: 1.9;
}

.hero-actions {
  display: flex;
  align-items: center;
  gap: 24px;
  margin-top: 34px;
}

.primary-action,
.text-action,
.demo-controls button {
  font: inherit;
}

.primary-action {
  min-height: 52px;
  padding: 0 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 30px;
  border: 1px solid var(--ink);
  background: var(--ink);
  color: #fffdf8;
  font-size: 0.9rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  transition: background 180ms ease, color 180ms ease, transform 180ms ease;
}

.primary-action svg,
.text-action svg,
.demo-controls svg,
.coach-label svg {
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.primary-action svg {
  width: 20px;
  height: 20px;
}

.primary-action:hover {
  background: var(--wine);
  border-color: var(--wine);
  transform: translateY(-2px);
}

.text-action {
  min-height: 48px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-size: 0.9rem;
  border-bottom: 1px solid var(--line);
}

.text-action svg {
  width: 20px;
  height: 20px;
}

.text-action:hover {
  color: var(--wine);
  border-color: var(--wine);
}

.hero-capabilities {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 22px;
  margin-top: 36px;
  color: var(--muted);
  font-size: 0.76rem;
}

.hero-capabilities span {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.hero-capabilities span::before {
  content: '';
  width: 4px;
  height: 4px;
  background: var(--gold);
  transform: rotate(45deg);
}

.demo-column {
  min-width: 0;
  animation: rise-in 650ms 100ms cubic-bezier(0.16, 1, 0.3, 1) both;
}

.demo-overline,
.demo-controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: var(--muted);
  font-size: 0.68rem;
  letter-spacing: 0.1em;
}

.demo-overline {
  margin-bottom: 12px;
}

.demo-stage {
  position: relative;
  min-height: 510px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  background: #24211d;
  color: #f6f1e7;
  box-shadow: 18px 22px 0 var(--paper-deep);
}

.demo-stage::before {
  content: '';
  position: absolute;
  inset: 8px;
  border: 1px solid rgba(222, 202, 167, 0.13);
  pointer-events: none;
}

.demo-stage-header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
}

.demo-scenario {
  display: flex;
  align-items: center;
  gap: 12px;
}

.scenario-mark {
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  border: 1px solid #ab8b5a;
  color: #d7bc8f;
  font-family: Georgia, serif;
  font-size: 0.85rem;
  letter-spacing: -0.08em;
}

.demo-scenario strong,
.demo-scenario span {
  display: block;
}

.demo-scenario strong {
  margin-bottom: 4px;
  font-size: 0.85rem;
  font-weight: 550;
}

.demo-scenario div > span {
  color: #a9a196;
  font-size: 0.68rem;
}

.live-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #c8bcae;
  font-size: 0.66rem;
  white-space: nowrap;
}

.live-badge i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #bba170;
  box-shadow: 0 0 0 4px rgba(187, 161, 112, 0.1);
}

.dialogue-list {
  position: relative;
  flex: 1;
  padding: 18px 0 12px;
}

.demo-message {
  max-width: 88%;
  margin-bottom: 12px;
  animation: message-in 280ms cubic-bezier(0.16, 1, 0.3, 1) both;
}

.demo-message.advisor {
  margin-left: auto;
}

.message-name {
  display: block;
  margin-bottom: 4px;
  color: #a9a196;
  font-size: 0.62rem;
}

.demo-message.advisor .message-name {
  text-align: right;
}

.demo-message p {
  margin: 0;
  padding: 9px 12px;
  background: #34302a;
  color: #e6dfd4;
  font-size: 0.76rem;
  line-height: 1.65;
}

.demo-message.advisor p {
  background: #eee6d8;
  color: #332b22;
}

.coach-feedback {
  position: relative;
  padding: 14px 16px;
  border: 1px solid rgba(190, 158, 105, 0.35);
  background: rgba(190, 158, 105, 0.07);
  animation: message-in 300ms ease-out both;
}

.coach-label {
  display: flex;
  align-items: center;
  gap: 7px;
  color: #d2b782;
  font-size: 0.68rem;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.coach-label svg {
  width: 15px;
  height: 15px;
}

.coach-feedback p {
  margin: 8px 0 10px;
  color: #d2c9bc;
  font-size: 0.7rem;
  line-height: 1.6;
}

.feedback-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.feedback-tags span {
  padding: 3px 8px;
  border: 1px solid rgba(210, 183, 130, 0.25);
  color: #bbae9b;
  font-size: 0.6rem;
}

.feedback-placeholder {
  min-height: 99px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: 1px solid rgba(190, 158, 105, 0.15);
  color: #9c9285;
  font-size: 0.68rem;
}

.feedback-placeholder span {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #b49b6f;
  animation: pulse 900ms infinite alternate;
}

.feedback-placeholder span:nth-child(2) { animation-delay: 150ms; }
.feedback-placeholder span:nth-child(3) { animation-delay: 300ms; margin-right: 5px; }

.demo-controls {
  margin-top: 26px;
  letter-spacing: 0;
}

.demo-controls button {
  min-height: 40px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--ink);
  font-size: 0.72rem;
  white-space: nowrap;
}

.demo-controls button:hover:not(:disabled) {
  color: var(--wine);
}

.demo-controls button:disabled {
  cursor: wait;
  opacity: 0.55;
}

.demo-controls svg {
  width: 15px;
  height: 15px;
}

.workflow {
  width: min(1440px, 100%);
  margin: 0 auto;
  padding: 28px clamp(24px, 5vw, 80px);
  display: grid;
  grid-template-columns: minmax(180px, 0.75fr) 2fr;
  gap: 48px;
  align-items: center;
  border-top: 1px solid var(--line);
}

.workflow-title > span,
.workflow-title small {
  display: block;
}

.workflow-title > span {
  margin-bottom: 7px;
  font-family: 'Songti SC', 'STSong', serif;
  font-size: 1.05rem;
}

.workflow-title small {
  color: var(--muted);
  font-size: 0.6rem;
  letter-spacing: 0.16em;
}

.workflow ol {
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  list-style: none;
}

.workflow li {
  min-width: 0;
  display: flex;
  gap: 12px;
  padding: 0 22px;
  border-left: 1px solid var(--line);
}

.workflow li > span {
  color: var(--gold);
  font-family: Georgia, serif;
  font-size: 0.78rem;
}

.workflow strong,
.workflow small {
  display: block;
}

.workflow strong {
  margin-bottom: 5px;
  font-size: 0.8rem;
  font-weight: 600;
}

.workflow li small {
  color: var(--muted);
  font-size: 0.66rem;
  line-height: 1.5;
}

.technology-note {
  padding: 15px 24px 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  border-top: 1px solid var(--line);
  color: #8b8173;
  font-size: 0.6rem;
  letter-spacing: 0.12em;
}

.technology-note i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #a8977e;
}

button:focus-visible {
  outline: 2px solid var(--wine);
  outline-offset: 4px;
}

@keyframes rise-in {
  from { opacity: 0; transform: translateY(18px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes message-in {
  from { opacity: 0; transform: translateY(7px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes pulse {
  from { opacity: 0.3; }
  to { opacity: 1; }
}

@media (max-width: 1020px) {
  .hero-grid {
    min-height: auto;
    grid-template-columns: 1fr;
    gap: 54px;
  }

  .hero-copy {
    max-width: 720px;
  }

  h1 {
    font-size: clamp(3rem, 8vw, 5rem);
  }

  .demo-column {
    width: min(620px, 100%);
  }

  .workflow {
    grid-template-columns: 1fr;
    gap: 24px;
  }
}

@media (max-width: 620px) {
  .demo-home {
    background: var(--paper);
  }

  .hero-grid {
    padding-top: 32px;
    gap: 44px;
  }

  .eyebrow {
    margin-bottom: 22px;
    font-size: 0.64rem;
    letter-spacing: 0.1em;
  }

  h1 {
    font-size: clamp(2.6rem, 12vw, 4rem);
    letter-spacing: -0.06em;
  }

  .hero-lead {
    margin-top: 22px;
    font-size: 0.94rem;
  }

  .hero-actions {
    align-items: stretch;
    flex-direction: column;
    gap: 10px;
    margin-top: 28px;
  }

  .primary-action {
    width: 100%;
  }

  .text-action {
    align-self: center;
  }

  .hero-capabilities {
    margin-top: 24px;
  }

  .demo-stage {
    min-height: 530px;
    padding: 20px;
    box-shadow: 8px 10px 0 var(--paper-deep);
  }

  .demo-stage-header {
    align-items: flex-start;
  }

  .live-badge {
    padding-top: 4px;
  }

  .demo-message {
    max-width: 94%;
  }

  .demo-overline,
  .demo-controls {
    font-size: 0.62rem;
  }

  .workflow {
    padding-top: 30px;
    padding-bottom: 30px;
  }

  .workflow ol {
    grid-template-columns: 1fr;
    gap: 20px;
  }

  .workflow li {
    padding: 0 0 0 14px;
  }

  .technology-note {
    flex-wrap: wrap;
    gap: 10px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .hero-copy,
  .demo-column,
  .demo-message,
  .coach-feedback,
  .feedback-placeholder span {
    animation: none;
  }

  .primary-action {
    transition: none;
  }
}
</style>
