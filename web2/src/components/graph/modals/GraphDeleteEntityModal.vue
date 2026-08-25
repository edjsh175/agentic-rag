<script setup lang="ts">
import { ref, watch } from 'vue'
import type { GraphNode } from '../../../types'

const props = defineProps<{
  visible: boolean
  node: GraphNode | null
  relationsCount: number
  isDeleting: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'confirm'): void
}>()

const deleteConfirmationInput = ref('')

watch(() => props.visible, (val) => {
  if (val) {
    deleteConfirmationInput.value = ''
  }
})

function handleConfirm() {
  if (props.node && deleteConfirmationInput.value === props.node.label && !props.isDeleting) {
    emit('confirm')
  }
}
</script>

<template>
  <div class="modal-backdrop" v-if="visible && node">
    <div class="modal-card">
      <header class="modal-header danger-header">
        <div class="modal-title-with-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
            <line x1="12" y1="9" x2="12" y2="13"></line>
            <line x1="12" y1="17" x2="12.01" y2="17"></line>
          </svg>
          <h3>高危操作：级联删除实体</h3>
        </div>
        <button @click="emit('close')" class="close-btn">&times;</button>
      </header>
      <div class="modal-body">
        <p class="warning-text">
          删除实体 <strong>“{{ node.label }}”</strong> 将会连带删除它所关联的<strong>全部关系边（{{ relationsCount }} 条）</strong>以及<strong>证据链关联记录</strong>，此操作为物理删除且<strong>不可恢复</strong>。
        </p>
        <p class="prompt-text">
          请输入实体名称 <strong>{{ node.label }}</strong> 确认此删除操作：
        </p>
        <input
          type="text"
          v-model="deleteConfirmationInput"
          class="modal-input"
          :placeholder="node.label"
          @keyup.enter="handleConfirm"
        />
      </div>
      <footer class="modal-footer">
        <button @click="emit('close')" class="secondary-btn">取消</button>
        <button
          @click="handleConfirm"
          class="danger-confirm-btn"
          :disabled="deleteConfirmationInput !== node.label || isDeleting"
        >
          {{ isDeleting ? '正在删除...' : '确认并级联删除' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10002;
  padding: 16px;
}

.modal-card {
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.15), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
  width: 100%;
  max-width: 480px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  animation: pop-in 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes pop-in {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #f1f5f9;
}

.danger-header {
  background: #fef2f2;
  border-bottom-color: #fee2e2;
}

.modal-title-with-icon {
  display: flex;
  align-items: center;
  gap: 8px;
}

.danger-header h3 {
  color: #dc2626;
  font-size: 15px;
  font-weight: 600;
  margin: 0;
}

.close-btn {
  border: none;
  background: transparent;
  color: #94a3b8;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: all 0.15s;
}

.close-btn:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.modal-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.warning-text {
  font-size: 13px;
  color: #b91c1c;
  line-height: 1.6;
  margin: 0;
}

.prompt-text {
  font-size: 13px;
  color: #475569;
  margin: 0;
}

.modal-input {
  width: 100%;
  height: 38px;
  padding: 0 12px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  transition: all 0.15s;
  box-sizing: border-box;
}

.modal-input:focus {
  border-color: #dc2626;
  box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.12);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  background: #f8fafc;
  border-top: 1px solid #f1f5f9;
}

.secondary-btn {
  padding: 8px 16px;
  border-radius: 6px;
  border: 1px solid #cbd5e1;
  background: #ffffff;
  color: #475569;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.secondary-btn:hover {
  background: #f1f5f9;
  color: #1e293b;
}

.danger-confirm-btn {
  padding: 8px 16px;
  border-radius: 6px;
  border: none;
  background: #dc2626;
  color: #ffffff;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.danger-confirm-btn:hover:not(:disabled) {
  background: #b91c1c;
}

.danger-confirm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
