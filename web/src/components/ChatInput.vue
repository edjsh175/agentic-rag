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

    <!-- 工具栏 + 输入框 -->
    <div class="input-row">
      <button class="tool-btn" @click="pickImage" title="上传图片" :disabled="disabled">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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
        :placeholder="previewUrl ? '输入关于这张图片的问题…' : '输入问题，或粘贴图片'"
        :disabled="disabled"
        rows="1"
        @input="adjustHeight"
        @keydown="onKeydown"
        @paste="onPaste"
      />

      <button v-if="disabled" class="stop-btn" @click="$emit('stop')" title="停止生成">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>
      </button>
      <button v-else class="send-btn" :disabled="!text.trim() && !pendingFile" @click="handleSend">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.input-area {
  border-top: 1px solid #e8eaed;
  background: #fff;
  padding: 0 24px 20px;
}

.preview-row {
  padding: 12px 0 0;
}
.preview-item {
  position: relative;
  display: inline-block;
}
.preview-thumb {
  max-height: 100px;
  max-width: 160px;
  border-radius: 6px;
  border: 1px solid #e8eaed;
  display: block;
}
.preview-remove {
  position: absolute;
  top: -8px;
  right: -8px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: none;
  background: #f25d5d;
  color: #fff;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 6px rgba(0,0,0,0.12);
}

.input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding-top: 12px;
}

.tool-btn {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: #f7f8fa;
  color: #6b7280;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.tool-btn:hover:not(:disabled) {
  background: #eef0f4;
  color: #3370ff;
}
.tool-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.input-box {
  flex: 1;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  padding: 9px 14px;
  font-size: 14px;
  line-height: 1.5;
  resize: none;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  font-family: inherit;
  max-height: 120px;
}
.input-box:focus {
  border-color: #3370ff;
  box-shadow: 0 0 0 2px rgba(51,112,255,0.08);
}
.input-box:disabled {
  background: #f7f8fa;
  cursor: not-allowed;
}
.input-box::placeholder {
  color: #b0b5be;
  font-size: 14px;
}

.send-btn {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: #3370ff;
  color: #fff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, opacity 0.15s;
}
.send-btn:hover:not(:disabled) { background: #2860e0; }
.send-btn:disabled { opacity: 0.35; cursor: not-allowed; }

.stop-btn {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: #f25d5d;
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
    padding: 0 12px 12px;
  }
  .input-row {
    gap: 6px;
    padding-top: 8px;
  }
  .tool-btn {
    width: 34px;
    height: 34px;
  }
  .input-box {
    padding: 7px 10px;
    font-size: 14px;
    max-height: 100px;
  }
  .send-btn, .stop-btn {
    width: 34px;
    height: 34px;
  }
  .preview-thumb {
    max-height: 80px;
    max-width: 120px;
  }
}

@media (max-width: 480px) {
  .input-area {
    padding: 0 8px 10px;
  }
  .input-row {
    gap: 4px;
  }
  .input-box {
    font-size: 13px;
    padding: 6px 8px;
  }
  .tool-btn, .send-btn, .stop-btn {
    width: 32px;
    height: 32px;
  }
}
</style>
