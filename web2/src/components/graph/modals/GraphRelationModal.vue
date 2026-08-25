<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import type { GraphNode } from '../../../types'
import { relationTypeLabel } from '../../../utils/graphLabels'

const props = defineProps<{
  visible: boolean
  nodes: GraphNode[]
  initialSourceId?: string
  initialTargetId?: string
  isProductBackbonePreviewAny: boolean
  isSaving: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'save', form: {
    source_id: string
    target_id: string
    relation_type: string
    weight: number
    evidence_text: string
  }): void
}>()

const FORM_RELATION_TYPES = [
  'BELONGS_TO',
  'CONTAINS',
  'CALLS',
  'DEPENDS_ON',
  'PRODUCES',
  'CONSUMES',
  'DOCUMENTED_IN',
  'DESCRIBES',
  'NEXT_STEP',
  'PRECEDES',
  'SOLVES',
  'CAUSES',
  'MAPS_TO',
  'STORES',
  'HAS_FIELD',
  'CONFIGURED_BY',
  'RELIES_ON',
]

const form = reactive({
  source_id: '',
  target_id: '',
  relation_type: 'belongs_to',
  weight: 1.0,
  evidence_text: '',
})

const sourceSearch = ref('')
const targetSearch = ref('')

watch(() => props.visible, (val) => {
  if (val) {
    form.source_id = props.initialSourceId || ''
    form.target_id = props.initialTargetId || ''
    form.relation_type = 'belongs_to'
    form.weight = 1.0
    form.evidence_text = ''
    sourceSearch.value = ''
    targetSearch.value = ''
  }
})

const filteredSourceNodes = computed(() => {
  const q = sourceSearch.value.trim().toLowerCase()
  if (!q) return props.nodes
  return props.nodes.filter(n => n.label.toLowerCase().includes(q))
})

const filteredTargetNodes = computed(() => {
  const q = targetSearch.value.trim().toLowerCase()
  if (!q) return props.nodes
  return props.nodes.filter(n => n.label.toLowerCase().includes(q))
})

function swapDirection() {
  const temp = form.source_id
  form.source_id = form.target_id
  form.target_id = temp
}

function handleSave() {
  if (!form.source_id || !form.target_id || form.source_id === form.target_id) return
  emit('save', { ...form })
}
</script>

<template>
  <div class="modal-backdrop" v-if="visible">
    <div class="modal-card">
      <header class="modal-header">
        <h3>新建关系</h3>
        <button @click="emit('close')" class="close-btn">&times;</button>
      </header>
      <div class="modal-body form-body">
        <!-- 源实体搜索与选择 -->
        <div class="form-field">
          <div class="flex-between">
            <label class="form-label">源实体</label>
            <input
              type="text"
              v-model="sourceSearch"
              placeholder="快速筛选源实体..."
              class="field-quick-filter"
            />
          </div>
          <select v-model="form.source_id" class="filter-select" data-test="relation-source">
            <option value="">请选择</option>
            <option v-for="node in filteredSourceNodes" :key="`source-${node.id}`" :value="node.id">
              {{ node.label }}
            </option>
          </select>
        </div>

        <!-- 快速反转方向按钮 -->
        <div class="swap-direction-row">
          <button type="button" @click="swapDirection" class="swap-btn" title="交换源实体与目标实体方向">
            <span>反转方向 ⇅</span>
          </button>
        </div>

        <!-- 目标实体搜索与选择 -->
        <div class="form-field">
          <div class="flex-between">
            <label class="form-label">目标实体</label>
            <input
              type="text"
              v-model="targetSearch"
              placeholder="快速筛选目标实体..."
              class="field-quick-filter"
            />
          </div>
          <select v-model="form.target_id" class="filter-select" data-test="relation-target">
            <option value="">请选择</option>
            <option v-for="node in filteredTargetNodes" :key="`target-${node.id}`" :value="node.id">
              {{ node.label }}
            </option>
          </select>
        </div>

        <div class="form-field">
          <label class="form-label">关系类型</label>
          <select v-model="form.relation_type" class="filter-select" data-test="relation-type">
            <option v-for="type in FORM_RELATION_TYPES" :key="type" :value="type">
              {{ relationTypeLabel(type) }} ({{ type }})
            </option>
          </select>
        </div>

        <div class="form-field" v-if="!isProductBackbonePreviewAny">
          <label class="form-label">权重</label>
          <input
            v-model.number="form.weight"
            type="number"
            step="0.1"
            min="0"
            max="1"
            class="modal-input"
            data-test="relation-weight"
          />
        </div>

        <div class="form-field" v-if="!isProductBackbonePreviewAny">
          <label class="form-label">证据文本</label>
          <textarea
            v-model="form.evidence_text"
            class="modal-textarea"
            placeholder="支持此关系连线的依据文本..."
            data-test="relation-evidence"
          ></textarea>
        </div>
      </div>
      <footer class="modal-footer">
        <button @click="emit('close')" class="secondary-btn">取消</button>
        <button
          @click="handleSave"
          class="primary-save-btn"
          :disabled="isSaving || !form.source_id || !form.target_id || form.source_id === form.target_id"
          data-test="save-relation"
        >
          {{ isSaving ? '创建中...' : '创建关系' }}
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

.modal-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
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
  max-height: 70vh;
  overflow-y: auto;
}

.flex-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.field-quick-filter {
  font-size: 11px;
  padding: 2px 6px;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  outline: none;
  color: #64748b;
  width: 130px;
}

.field-quick-filter:focus {
  border-color: #3370ff;
  color: #1e293b;
}

.modal-input,
.filter-select {
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  background: #ffffff;
  color: #1e293b;
  box-sizing: border-box;
  transition: all 0.15s;
}

.modal-input:focus,
.filter-select:focus,
.modal-textarea:focus {
  border-color: #3370ff;
  box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.12);
}

.modal-textarea {
  width: 100%;
  height: 60px;
  padding: 8px 10px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  font-size: 13px;
  outline: none;
  font-family: inherit;
  resize: vertical;
  color: #1e293b;
  box-sizing: border-box;
}

.swap-direction-row {
  display: flex;
  justify-content: center;
  margin: -4px 0;
}

.swap-btn {
  background: #f1f5f9;
  border: 1px dashed #cbd5e1;
  color: #475569;
  font-size: 11px;
  padding: 3px 12px;
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.swap-btn:hover {
  background: #e2e8f0;
  color: #1e293b;
  border-color: #94a3b8;
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

.primary-save-btn {
  background: #3370ff;
  color: #ffffff;
  padding: 8px 18px;
  border-radius: 6px;
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.primary-save-btn:hover:not(:disabled) {
  background: #1a56db;
}

.primary-save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
