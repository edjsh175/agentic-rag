<script setup lang="ts">
import { ref } from 'vue'
import { queryAdminDebug } from '../api'
import type { QaDebugResult } from '../types'

const question = ref('')
const result = ref<QaDebugResult | null>(null)
const loading = ref(false)
const error = ref('')

async function runDebug() {
  if (!question.value.trim()) return
  loading.value = true
  error.value = ''
  try {
    result.value = await queryAdminDebug(question.value.trim())
  } catch (e: any) {
    error.value = e.message || '调试请求失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="qa-debug">
    <h1>问答证据调试</h1>
    <form @submit.prevent="runDebug">
      <textarea v-model="question" placeholder="输入要复现的问答问题" rows="3" />
      <button :disabled="loading || !question.trim()">{{ loading ? '检索中…' : '运行调试' }}</button>
    </form>
    <p v-if="error" class="error">{{ error }}</p>
    <template v-if="result">
      <h2>最终回答</h2><pre>{{ result.answer }}</pre>
      <div class="columns">
        <article><h2>已引用 ({{ result.evidence_chain.cited.length }})</h2><pre>{{ result.evidence_chain.cited }}</pre></article>
        <article><h2>未引用 ({{ result.evidence_chain.retrieved_uncited.length }})</h2><pre>{{ result.evidence_chain.retrieved_uncited }}</pre></article>
        <article><h2>证据缺口 ({{ result.evidence_chain.gaps.length }})</h2><pre>{{ result.evidence_chain.gaps }}</pre></article>
        <article><h2>冲突 ({{ result.evidence_chain.conflicts.length }})</h2><pre>{{ result.evidence_chain.conflicts }}</pre></article>
      </div>
    </template>
  </section>
</template>

<style scoped>
.qa-debug { max-width: 1200px; margin: 0 auto; padding: 24px; }
form { display: grid; gap: 10px; }
textarea, pre { width: 100%; padding: 10px; border: 1px solid #d8dde6; border-radius: 6px; white-space: pre-wrap; word-break: break-word; }
button { width: fit-content; padding: 8px 14px; }
.columns { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.error { color: #c62828; }
@media (max-width: 760px) { .columns { grid-template-columns: 1fr; } }
</style>
