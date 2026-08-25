<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'

const emit = defineEmits<{
  send: [text: string, image?: File]
  stop: []
}>()

defineProps<{ disabled?: boolean }>()

const text = ref('')
const previewUrl = ref<string | null>(null)
const pendingFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement>()
const textareaRef = ref<HTMLTextAreaElement>()
const isFocused = ref(false)

function adjustHeight() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = (el.scrollHeight + 2) + 'px'
}

function handleSend() {
  const val = text.value.trim()
  if (!val && !pendingFile.value) return
  emit('send', val || '请描述这张图片', pendingFile.value || undefined)
  text.value = ''
  clearImage()
  nextTick(adjustHeight)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) { setImage(file); e.preventDefault(); return }
    }
  }
}

function pickImage() { fileInput.value?.click() }
function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) setImage(file)
  input.value = ''
}
function setImage(file: File) {
  pendingFile.value = file
  const reader = new FileReader()
  reader.onload = () => { previewUrl.value = reader.result as string }
  reader.readAsDataURL(file)
}
function clearImage() {
  previewUrl.value = null
  pendingFile.value = null
}

function setText(val: string) {
  text.value = val
  nextTick(() => {
    adjustHeight()
    textareaRef.value?.focus()
  })
}

function focus() {
  textareaRef.value?.focus()
}

defineExpose({
  setText,
  focus
})

onMounted(() => {
  adjustHeight()
})
</script>

<template>
  <div class="input-area">
    <!-- 图片预览 -->
    <div v-if="previewUrl" class="preview-row">
      <div class="preview-item">
        <img :src="previewUrl" class="preview-thumb" />
        <button class="preview-remove" @click="clearImage">&times;</button>
      </div>
    </div>

    <!-- 现代化一体化胶囊输入框 -->
    <div class="input-container" :class="{ 'is-focused': isFocused, 'is-disabled': disabled }">
      <button class="tool-btn" @click="pickImage" title="上传图片" :disabled="disabled">
        <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
          <circle cx="8.5" cy="8.5" r="1.5"/>
          <polyline points="21 15 16 10 5 21"/>
        </svg>
      </button>
      <input ref="fileInput" type="file" accept="image/*" hidden @change="onFileChange" />

      <textarea
        ref="textareaRef"
        v-model="text"
        class="input-box"
        :placeholder="previewUrl ? '输入关于这张图片的问题…' : '输入问题，或粘贴图片（Enter 发送，Shift+Enter 换行）'"
        :disabled="disabled"
        rows="1"
        @input="adjustHeight"
        @keydown="onKeydown"
        @paste="onPaste"
        @focus="isFocused = true"
        @blur="isFocused = false"
      />

      <button v-if="disabled" class="stop-btn" @click="$emit('stop')" title="停止生成">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>
      </button>
      <button v-else class="send-btn" :disabled="!text.trim() && !pendingFile" @click="handleSend" title="发送">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.input-area {
  background: transparent;
  padding: 0 24px 20px;
}

.preview-row {
  max-width: 820px;
  margin: 0 auto;
  padding: 0 0 8px 12px;
}
.preview-item {
  position: relative;
  display: inline-block;
}
.preview-thumb {
  max-height: 90px;
  max-width: 150px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  display: block;
}
.preview-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: none;
  background: #ef4444;
  color: #fff;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 5px rgba(0,0,0,0.15);
}

.input-container {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  max-width: 820px;
  margin: 0 auto;
  padding: 6px 8px 6px 12px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 26px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.input-container.is-focused {
  border-color: #3370ff;
  box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.12), 0 2px 12px rgba(51, 112, 255, 0.08);
}

.input-container.is-disabled {
  background: #f8fafc;
  border-color: #e2e8f0;
}

.tool-btn {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  margin-bottom: 2px;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}
.tool-btn:hover:not(:disabled) {
  background: #f1f5f9;
  color: #3370ff;
}
.tool-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.input-box {
  flex: 1;
  border: none;
  background: transparent;
  padding: 7px 4px;
  font-size: 14px;
  line-height: 1.55;
  resize: none;
  outline: none;
  font-family: inherit;
  color: #1e2a41;
  max-height: 130px;
}
.input-box:disabled {
  cursor: not-allowed;
  color: #94a3b8;
}
.input-box::placeholder {
  color: #94a3b8;
  font-size: 14px;
}

.send-btn {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  margin-bottom: 2px;
  border: none;
  border-radius: 50%;
  background: #3370ff;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}
.send-btn:hover:not(:disabled) {
  background: #1a56db;
  transform: scale(1.05);
}
.send-btn:disabled {
  background: #e2e8f0;
  color: #94a3b8;
  cursor: not-allowed;
  opacity: 0.7;
}

.stop-btn {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  margin-bottom: 2px;
  border: none;
  border-radius: 50%;
  background: #ef4444;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: pulse 1.8s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.65; }
}

/* ===== 移动端响应式 ===== */
@media (max-width: 768px) {
  .input-area {
    padding: 0 12px 14px;
  }
  .input-container {
    padding: 4px 6px 4px 10px;
    border-radius: 22px;
    gap: 6px;
  }
  .tool-btn, .send-btn, .stop-btn {
    width: 30px;
    height: 30px;
  }
  .input-box {
    font-size: 13px;
    padding: 6px 2px;
    max-height: 100px;
  }
}
</style>
