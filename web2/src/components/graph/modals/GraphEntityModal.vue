<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { GraphNode } from '../../../types'
import { DOC_CATEGORIES } from '../../../types'
import { entityTypeLabel, docCategoryLabel, FORMAL_ENTITY_TYPES } from '../../../utils/graphLabels'

const props = defineProps<{
  visible: boolean
  mode: 'create' | 'edit'
  entityToEdit: GraphNode | null
  isProductBackbonePreviewAny: boolean
  isSaving: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'save', form: {
    id?: string
    name: string
    entity_type: string
    doc_category?: string
    canonical_name?: string
    description?: string
    layer?: string
    subtype?: string
    source?: string
    status?: string
    alias_candidates?: string
  }): void
}>()

const FORM_ENTITY_TYPES = [...FORMAL_ENTITY_TYPES]

const form = reactive({
  id: '',
  name: '',
  entity_type: 'Module',
  doc_category: '',
  canonical_name: '',
  description: '',
  layer: '',
  subtype: '',
  source: '',
  status: '',
  alias_candidates: '',
})

watch(() => props.visible, (val) => {
  if (val) {
    if (props.mode === 'edit' && props.entityToEdit) {
      const node = props.entityToEdit
      let properties: Record<string, any> = {}
      if (node.properties_json) {
        try {
          properties = JSON.parse(node.properties_json)
        } catch {
          properties = {}
        }
      }
      
      form.id = node.id
      form.name = node.label || (node as any).name || ''
      form.entity_type = node.type || 'Module'
      form.doc_category = node.doc_category || ''
      form.canonical_name = node.canonical_name || ''
      form.description = node.description || ''
      form.layer = properties.layer || ''
      form.subtype = properties.subtype || ''
      form.source = properties.source || ''
      form.status = properties.status || ''
      form.alias_candidates = Array.isArray(properties.alias_candidates)
        ? properties.alias_candidates.join('\n')
        : (properties.alias_candidates || '')
    } else {
      form.id = ''
      form.name = ''
      form.entity_type = 'Module'
      form.doc_category = ''
      form.canonical_name = ''
      form.description = ''
      form.layer = ''
      form.subtype = ''
      form.source = ''
      form.status = ''
      form.alias_candidates = ''
    }
  }
})

function handleSave() {
  if (!form.name.trim()) return
  emit('save', { ...form })
}
</script>

<template>
  <div class="modal-backdrop" v-if="visible">
    <div class="modal-card">
      <header class="modal-header">
        <h3>{{ mode === 'create' ? '新建实体' : '编辑实体' }}</h3>
        <button @click="emit('close')" class="close-btn">&times;</button>
      </header>
      <div class="modal-body form-body">
        <div class="form-row-2">
          <div class="form-field">
            <label class="form-label">名称</label>
            <input v-model="form.name" class="modal-input" data-test="entity-name" placeholder="输入实体名称..." />
          </div>
          <div class="form-field">
            <label class="form-label">类型</label>
            <select v-model="form.entity_type" class="filter-select" data-test="entity-type">
              <option v-for="type in FORM_ENTITY_TYPES" :key="type" :value="type">{{ entityTypeLabel(type) }}</option>
            </select>
          </div>
        </div>

        <template v-if="isProductBackbonePreviewAny">
          <div class="form-row-2">
            <div class="form-field">
              <label class="form-label">功能层</label>
              <input v-model="form.layer" class="modal-input" data-test="entity-layer" />
            </div>
            <div class="form-field">
              <label class="form-label">实体子类型</label>
              <input v-model="form.subtype" class="modal-input" data-test="entity-subtype" />
            </div>
          </div>
          <div class="form-row-2">
            <div class="form-field">
              <label class="form-label">来源</label>
              <input v-model="form.source" class="modal-input" data-test="entity-source" />
            </div>
            <div class="form-field">
              <label class="form-label">状态</label>
              <input v-model="form.status" class="modal-input" data-test="entity-status" />
            </div>
          </div>
          <div class="form-field">
            <label class="form-label">别名候选</label>
            <textarea v-model="form.alias_candidates" class="modal-textarea" data-test="entity-alias-candidates" placeholder="每行一个别名..."></textarea>
          </div>
        </template>
        <template v-else>
          <div class="form-row-2">
            <div class="form-field">
              <label class="form-label">分类</label>
              <select v-model="form.doc_category" class="filter-select">
                <option value="">未设置</option>
                <option v-for="cat in DOC_CATEGORIES" :key="cat" :value="cat">{{ docCategoryLabel(cat) }}</option>
              </select>
            </div>
            <div class="form-field">
              <label class="form-label">规范名</label>
              <input v-model="form.canonical_name" class="modal-input" placeholder="标准规范名..." />
            </div>
          </div>
        </template>

        <div class="form-field">
          <label class="form-label">说明</label>
          <textarea v-model="form.description" class="modal-textarea" placeholder="实体功能说明..."></textarea>
        </div>
      </div>
      <footer class="modal-footer">
        <button @click="emit('close')" class="secondary-btn">取消</button>
        <button @click="handleSave" class="danger-confirm-btn primary-save-btn" :disabled="isSaving || !form.name.trim()" data-test="save-entity">
          {{ isSaving ? '保存中...' : '保存实体' }}
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
  max-width: 520px;
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
  gap: 14px;
  max-height: 70vh;
  overflow-y: auto;
}

.form-row-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
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
  height: 70px;
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
  background: #3370ff !important;
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
  background: #1a56db !important;
}

.primary-save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
