<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'

const props = withDefaults(defineProps<{
  disabled?: boolean
  mode?: 'agent' | 'linear'
}>(), {
  mode: 'agent'
})

const emit = defineEmits<{
  send: [text: string, image?: File]
  stop: []
  'update:mode': [mode: 'agent' | 'linear']
}>()

const text = ref('')
const previewUrl = ref<string | null>(null)
const pendingFile = ref<File | null>(null)
const fileInput = ref<HTMLInputElement>()
const textareaRef = ref<HTMLTextAreaElement>()
const showModeMenu = ref(false)
const modeBtnRef = ref<HTMLElement | null>(null)

function toggleModeMenu() {
  if (props.disabled) return
  showModeMenu.value = !showModeMenu.value
}

function selectMode(m: 'agent' | 'linear') {
  emit('update:mode', m)
  showModeMenu.value = false
}

function handleClickOutside(e: MouseEvent) {
  if (showModeMenu.value && modeBtnRef.value && !modeBtnRef.value.contains(e.target as Node)) {
    showModeMenu.value = false
  }
}

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
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
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
      <!-- 工作模式切换器 -->
      <div ref="modeBtnRef" class="mode-selector-wrap">
        <button
          type="button"
          class="mode-trigger-btn"
          :class="{ active: showModeMenu, 'is-agent': mode === 'agent', 'is-linear': mode === 'linear' }"
          :disabled="disabled"
          @click.stop="toggleModeMenu"
          :title="mode === 'agent' ? '当前：Agent 模式（多步编排与工具调用）' : '当前：线性模式（直接检索与快速生成）'"
        >
          <!-- Agent 图标 -->
          <svg v-if="mode === 'agent'" class="mode-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="11" width="18" height="10" rx="2"/>
            <circle cx="12" cy="5" r="2"/>
            <path d="M12 7v4"/>
            <line x1="8" y1="16" x2="8" y2="16"/>
            <line x1="16" y1="16" x2="16" y2="16"/>
          </svg>
          <!-- 线性图标 -->
          <svg v-else class="mode-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
          </svg>
          <span class="mode-label">{{ mode === 'agent' ? 'Agent 模式' : '线性模式' }}</span>
          <svg class="mode-arrow" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </button>

        <!-- 模式切换下拉菜单 -->
        <Transition name="fade-slide">
          <div v-if="showModeMenu" class="mode-menu-popover">
            <div
              class="mode-menu-item"
              :class="{ selected: mode === 'agent' }"
              @click="selectMode('agent')"
            >
              <div class="mode-item-head">
                <svg class="mode-item-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="3" y="11" width="18" height="10" rx="2"/>
                  <circle cx="12" cy="5" r="2"/>
                  <path d="M12 7v4"/>
                  <line x1="8" y1="16" x2="8" y2="16"/>
                  <line x1="16" y1="16" x2="16" y2="16"/>
                </svg>
                <span class="mode-item-title">Agent 模式</span>
                <span v-if="mode === 'agent'" class="mode-check-badge">当前</span>
              </div>
              <div class="mode-item-desc">支持多步规划、工具调用、动态检索与反思校验，适合复杂与深度问答</div>
            </div>

            <div
              class="mode-menu-item"
              :class="{ selected: mode === 'linear' }"
              @click="selectMode('linear')"
            >
              <div class="mode-item-head">
                <svg class="mode-item-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                </svg>
                <span class="mode-item-title">线性模式</span>
                <span v-if="mode === 'linear'" class="mode-check-badge">当前</span>
              </div>
              <div class="mode-item-desc">单次直接检索与快速流式生成，响应速度快，适合精准知识直答</div>
            </div>
          </div>
        </Transition>
      </div>

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
  position: relative;
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

/* ===== 模式选择器 ===== */
.mode-selector-wrap {
  position: relative;
  flex-shrink: 0;
}

.mode-trigger-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  color: #475569;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}

.mode-trigger-btn:hover:not(:disabled) {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #1e293b;
}

.mode-trigger-btn.is-agent {
  color: #2563eb;
  background: #eff6ff;
  border-color: #bfdbfe;
}

.mode-trigger-btn.is-agent:hover:not(:disabled) {
  background: #dbeafe;
  border-color: #93c5fd;
}

.mode-trigger-btn.is-linear {
  color: #0d9488;
  background: #f0fdfa;
  border-color: #99f6e4;
}

.mode-trigger-btn.is-linear:hover:not(:disabled) {
  background: #ccfbf1;
  border-color: #5eead4;
}

.mode-trigger-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.mode-icon {
  flex-shrink: 0;
}

.mode-arrow {
  flex-shrink: 0;
  color: inherit;
  opacity: 0.7;
}

/* 弹出菜单 */
.mode-menu-popover {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: 260px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.06);
  padding: 6px;
  z-index: 100;
}

.mode-menu-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.12s ease;
}

.mode-menu-item:hover {
  background: #f8fafc;
}

.mode-menu-item.selected {
  background: #eff6ff;
}

.mode-item-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.mode-item-icon {
  color: #64748b;
}

.mode-menu-item.selected .mode-item-icon {
  color: #2563eb;
}

.mode-item-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  flex: 1;
}

.mode-menu-item.selected .mode-item-title {
  color: #2563eb;
}

.mode-check-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #dbeafe;
  color: #1d4ed8;
  font-weight: 500;
}

.mode-item-desc {
  font-size: 11.5px;
  color: #64748b;
  line-height: 1.4;
}

/* 动画 */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: all 0.15s ease-out;
}
.fade-slide-enter-from,
.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(4px);
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
  .mode-trigger-btn {
    height: 34px;
    padding: 0 8px;
    font-size: 12px;
  }
  .mode-label {
    display: none;
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
  .mode-trigger-btn, .tool-btn, .send-btn, .stop-btn {
    width: 32px;
    height: 32px;
    padding: 0;
    justify-content: center;
  }
  .mode-arrow {
    display: none;
  }
}
</style>
