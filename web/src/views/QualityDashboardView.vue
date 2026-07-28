<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { getQualityDashboard, triggerDuplicateCheck } from '../api'
import type { QualityDashboardData, QualityAlert } from '../types'

const router = useRouter()
const loading = ref(true)
const detecting = ref(false)
const errorMsg = ref('')
const dashboardData = ref<QualityDashboardData | null>(null)

const metrics = computed(() => dashboardData.value?.metrics)
const alerts = computed(() => dashboardData.value?.alerts || [])

async function loadData() {
  loading.value = true
  errorMsg.value = ''
  try {
    dashboardData.value = await getQualityDashboard()
  } catch (err: any) {
    errorMsg.value = err.message || '加载质量仪表盘失败'
  } finally {
    loading.value = false
  }
}

async function handleRunDuplicateCheck() {
  detecting.value = true
  try {
    await triggerDuplicateCheck()
    await loadData()
  } catch (err: any) {
    errorMsg.value = err.message || '触发重复块检测失败'
  } finally {
    detecting.value = false
  }
}

function navigateToReview(alert: QualityAlert) {
  router.push({
    path: '/admin/chunks',
    query: {
      review_status: 'pending',
      filename: alert.source_file !== '未知文件' ? alert.source_file : undefined,
    },
  })
}

function getAlertTypeName(type: string): string {
  if (type === 'negative_feedback') return '差评预警'
  if (type === 'duplicate') return '重复块预警'
  return '质量预警'
}

function formatPercent(val: number | undefined): string {
  if (val === undefined || val === null) return '0.0%'
  return (val * 100).toFixed(1) + '%'
}

onMounted(() => {
  loadData()
})
</script>

<template>
  <div class="quality-dashboard-container">
    <!-- 页头标题与工具栏 -->
    <header class="dashboard-header">
      <div class="title-group">
        <h1 class="page-title">质量控制仪表盘</h1>
        <p class="page-subtitle">知识库健康状况宏观监控与差评自动重审闭环</p>
      </div>
      <div class="action-toolbar">
        <button
          type="button"
          class="btn btn-secondary"
          :disabled="detecting"
          @click="handleRunDuplicateCheck"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/>
          </svg>
          <span>{{ detecting ? '检测中...' : '触发重复检测' }}</span>
        </button>
        <button
          type="button"
          class="btn btn-primary"
          :disabled="loading"
          @click="loadData"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
          </svg>
          <span>刷新数据</span>
        </button>
      </div>
    </header>

    <!-- 错误信息 -->
    <div v-if="errorMsg" class="error-banner">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <span>{{ errorMsg }}</span>
    </div>

    <!-- 加载遮罩 -->
    <div v-if="loading && !dashboardData" class="loading-state">
      <div class="spinner"></div>
      <p>正在计算质量指标...</p>
    </div>

    <template v-else-if="dashboardData">
      <!-- 顶部 4 大核心指标卡片 -->
      <div class="metrics-grid">
        <!-- 卡片 1: 已审核占比 -->
        <div class="metric-card" :class="{ 'is-warning': (metrics?.approved_ratio || 0) < 0.7 }">
          <div class="card-header">
            <span class="card-title">已审核占比</span>
            <span class="status-badge" :class="(metrics?.approved_ratio || 0) < 0.7 ? 'badge-warning' : 'badge-success'">
              {{ (metrics?.approved_ratio || 0) < 0.7 ? '低于70%警告' : '正常' }}
            </span>
          </div>
          <div class="card-body">
            <div class="metric-value">{{ formatPercent(metrics?.approved_ratio) }}</div>
            <div class="progress-bar-bg">
              <div
                class="progress-bar-fill"
                :style="{ width: formatPercent(metrics?.approved_ratio) }"
                :class="{ 'fill-warning': (metrics?.approved_ratio || 0) < 0.7 }"
              ></div>
            </div>
          </div>
          <div class="card-footer">
            知识块总数：{{ metrics?.total_chunks }} 个
          </div>
        </div>

        <!-- 卡片 2: 7日满意率 -->
        <div class="metric-card" :class="{ 'is-alarm': (metrics?.satisfaction_ratio_7d || 0) < 0.8 }">
          <div class="card-header">
            <span class="card-title">7日用户满意率</span>
            <span class="status-badge" :class="(metrics?.satisfaction_ratio_7d || 0) < 0.8 ? 'badge-alarm' : 'badge-success'">
              {{ (metrics?.satisfaction_ratio_7d || 0) < 0.8 ? '低于80%报警' : '良好' }}
            </span>
          </div>
          <div class="card-body">
            <div class="metric-value">{{ formatPercent(metrics?.satisfaction_ratio_7d) }}</div>
            <div class="trend-indicator">
              <span>7日无结果率：{{ formatPercent(metrics?.no_result_ratio_7d) }}</span>
            </div>
          </div>
          <div class="card-footer">
            基于近7日用户问答反馈统计
          </div>
        </div>

        <!-- 卡片 3: 孤立实体数 -->
        <div class="metric-card" :class="{ 'is-warning': (metrics?.isolated_entities || 0) > 20 }">
          <div class="card-header">
            <span class="card-title">孤立实体数</span>
            <span class="status-badge" :class="(metrics?.isolated_entities || 0) > 20 ? 'badge-warning' : 'badge-neutral'">
              {{ (metrics?.isolated_entities || 0) > 20 ? '超出阈值' : '正常' }}
            </span>
          </div>
          <div class="card-body">
            <div class="metric-value">{{ metrics?.isolated_entities || 0 }}</div>
            <div class="sub-info">未关联 Chunk 的图谱实体</div>
          </div>
          <div class="card-footer">
            孤立 Chunk 数：{{ metrics?.isolated_chunks || 0 }} 个
          </div>
        </div>

        <!-- 卡片 4: 待审核块数 -->
        <div class="metric-card" :class="{ 'is-notice': (metrics?.pending_chunks || 0) > 50 }">
          <div class="card-header">
            <span class="card-title">待审核块数</span>
            <span class="status-badge" :class="(metrics?.pending_chunks || 0) > 50 ? 'badge-notice' : 'badge-neutral'">
              {{ (metrics?.pending_chunks || 0) > 50 ? '待审核较多' : '受控' }}
            </span>
          </div>
          <div class="card-body">
            <div class="metric-value">{{ metrics?.pending_chunks || 0 }}</div>
            <div class="sub-info">包含差评重置待审块</div>
          </div>
          <div class="card-footer">
            块重复率：{{ formatPercent(metrics?.duplicate_ratio) }}
          </div>
        </div>
      </div>

      <!-- 中部图表与评估区域 -->
      <div class="charts-row">
        <!-- 左侧：8大指标完整健康评测 -->
        <div class="chart-card">
          <div class="chart-card-header">
            <h3>知识库 8 大质量指标详情</h3>
          </div>
          <div class="metrics-table-wrapper">
            <table class="metrics-table">
              <thead>
                <tr>
                  <th>指标编号</th>
                  <th>指标名称</th>
                  <th>当前数值</th>
                  <th>报警阈值</th>
                  <th>健康状态</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>M1</td>
                  <td>知识块总数</td>
                  <td>{{ metrics?.total_chunks }}</td>
                  <td>-</td>
                  <td><span class="badge badge-success">正常</span></td>
                </tr>
                <tr>
                  <td>M2</td>
                  <td>已审核占比</td>
                  <td>{{ formatPercent(metrics?.approved_ratio) }}</td>
                  <td>&lt; 70% 警告</td>
                  <td>
                    <span class="badge" :class="(metrics?.approved_ratio || 0) < 0.7 ? 'badge-warning' : 'badge-success'">
                      {{ (metrics?.approved_ratio || 0) < 0.7 ? '警告' : '达标' }}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>M3</td>
                  <td>待审核数量</td>
                  <td>{{ metrics?.pending_chunks }} 个</td>
                  <td>&gt; 50 个 提示</td>
                  <td>
                    <span class="badge" :class="(metrics?.pending_chunks || 0) > 50 ? 'badge-notice' : 'badge-success'">
                      {{ (metrics?.pending_chunks || 0) > 50 ? '提示' : '正常' }}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>M4</td>
                  <td>孤立实体数</td>
                  <td>{{ metrics?.isolated_entities }} 个</td>
                  <td>&gt; 20 个 警告</td>
                  <td>
                    <span class="badge" :class="(metrics?.isolated_entities || 0) > 20 ? 'badge-warning' : 'badge-success'">
                      {{ (metrics?.isolated_entities || 0) > 20 ? '警告' : '正常' }}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>M5</td>
                  <td>孤立块数</td>
                  <td>{{ metrics?.isolated_chunks }} 个</td>
                  <td>&gt; 100 个 警告</td>
                  <td>
                    <span class="badge" :class="(metrics?.isolated_chunks || 0) > 100 ? 'badge-warning' : 'badge-success'">
                      {{ (metrics?.isolated_chunks || 0) > 100 ? '警告' : '正常' }}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>M6</td>
                  <td>块重复率 (SimHash)</td>
                  <td>{{ formatPercent(metrics?.duplicate_ratio) }}</td>
                  <td>&gt; 5% 警告</td>
                  <td>
                    <span class="badge" :class="(metrics?.duplicate_ratio || 0) > 0.05 ? 'badge-warning' : 'badge-success'">
                      {{ (metrics?.duplicate_ratio || 0) > 0.05 ? '警告' : '正常' }}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>M7</td>
                  <td>7日无结果率</td>
                  <td>{{ formatPercent(metrics?.no_result_ratio_7d) }}</td>
                  <td>&gt; 10% 报警</td>
                  <td>
                    <span class="badge" :class="(metrics?.no_result_ratio_7d || 0) > 0.1 ? 'badge-alarm' : 'badge-success'">
                      {{ (metrics?.no_result_ratio_7d || 0) > 0.1 ? '报警' : '正常' }}
                    </span>
                  </td>
                </tr>
                <tr>
                  <td>M8</td>
                  <td>7日用户满意率</td>
                  <td>{{ formatPercent(metrics?.satisfaction_ratio_7d) }}</td>
                  <td>&lt; 80% 报警</td>
                  <td>
                    <span class="badge" :class="(metrics?.satisfaction_ratio_7d || 0) < 0.8 ? 'badge-alarm' : 'badge-success'">
                      {{ (metrics?.satisfaction_ratio_7d || 0) < 0.8 ? '报警' : '达标' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- 右侧：反馈闭环说明卡片 -->
        <div class="chart-card hint-card">
          <div class="chart-card-header">
            <h3>自动化反馈闭环机制 (Feedback Loop)</h3>
          </div>
          <div class="loop-explanation">
            <div class="step-item">
              <div class="step-num">1</div>
              <div class="step-content">
                <strong>用户提问与评价</strong>
                <p>用户在问答对话框中对回答点击 [无用] 按钮。</p>
              </div>
            </div>
            <div class="step-arrow">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>
              </svg>
            </div>
            <div class="step-item">
              <div class="step-num">2</div>
              <div class="step-content">
                <strong>差评自动计次</strong>
                <p>系统提取回答调用的所有 Chunk，计次累计增加。</p>
              </div>
            </div>
            <div class="step-arrow">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/>
              </svg>
            </div>
            <div class="step-item highlight-step">
              <div class="step-num">3</div>
              <div class="step-content">
                <strong>状态重置与预警</strong>
                <p>当差评累计达到 2 次时，Chunk 状态自动重置为 pending，并推送到待复核预警列表。</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 底部预警列表 (待复核与高重复 Chunk) -->
      <div class="alerts-section">
        <div class="section-header">
          <h3>待复核预警列表 ({{ alerts.length }})</h3>
          <span class="section-tip">包含触发差评重置待审和高重复率预警的 Chunk</span>
        </div>

        <div v-if="alerts.length === 0" class="empty-alerts">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="2">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
            <polyline points="22 4 12 14.01 9 11.01"/>
          </svg>
          <p>当前知识库无任何质量预警，健康状况良好。</p>
        </div>

        <div v-else class="table-wrapper">
          <table class="alerts-table">
            <thead>
              <tr>
                <th>预警类型</th>
                <th>Chunk ID</th>
                <th>来源文件</th>
                <th>差评次数</th>
                <th>预警原因</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(item, idx) in alerts" :key="idx">
                <td>
                  <span
                    class="type-tag"
                    :class="item.type === 'negative_feedback' ? 'tag-negative' : 'tag-duplicate'"
                  >
                    {{ getAlertTypeName(item.type) }}
                  </span>
                </td>
                <td class="chunk-id-cell">{{ item.chunk_id }}</td>
                <td>{{ item.source_file }}</td>
                <td>
                  <span v-if="item.down_count > 0" class="down-count-badge">
                    {{ item.down_count }} 次
                  </span>
                  <span v-else class="text-muted">-</span>
                </td>
                <td class="reason-cell">{{ item.reason }}</td>
                <td>
                  <button
                    type="button"
                    class="btn-action"
                    @click="navigateToReview(item)"
                  >
                    一键跳转到审核
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.quality-dashboard-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px;
  height: 100%;
  overflow-y: auto;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 4px;
}

.page-subtitle {
  font-size: 13px;
  color: #64748b;
}

.action-toolbar {
  display: flex;
  gap: 12px;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  border: 1px solid transparent;
}

.btn-primary {
  background: #3b82f6;
  color: #ffffff;
}

.btn-primary:hover:not(:disabled) {
  background: #2563eb;
}

.btn-secondary {
  background: #ffffff;
  border-color: #cbd5e1;
  color: #334155;
}

.btn-secondary:hover:not(:disabled) {
  background: #f8fafc;
  border-color: #94a3b8;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.error-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  color: #dc2626;
  font-size: 13px;
  margin-bottom: 20px;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
  color: #64748b;
  gap: 16px;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e2e8f0;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 4 大核心指标卡片 */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 24px;
}

.metric-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 18px 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.metric-card.is-warning {
  border-color: #fde68a;
  background: #fffbeb;
}

.metric-card.is-alarm {
  border-color: #fecaca;
  background: #fef2f2;
}

.metric-card.is-notice {
  border-color: #bfdbfe;
  background: #eff6ff;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.card-title {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
}

.status-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  font-weight: 500;
}

.badge-success { background: #dcfce7; color: #15803d; }
.badge-warning { background: #fef3c7; color: #b45309; }
.badge-alarm { background: #fee2e2; color: #b91c1c; }
.badge-notice { background: #dbeafe; color: #1d4ed8; }
.badge-neutral { background: #f1f5f9; color: #64748b; }

.metric-value {
  font-size: 28px;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 8px;
  line-height: 1.1;
}

.progress-bar-bg {
  height: 6px;
  background: #e2e8f0;
  border-radius: 3px;
  overflow: hidden;
  margin-top: 6px;
}

.progress-bar-fill {
  height: 100%;
  background: #22c55e;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.fill-warning {
  background: #f59e0b;
}

.sub-info, .trend-indicator {
  font-size: 12px;
  color: #64748b;
}

.card-footer {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid rgba(226, 232, 240, 0.6);
  font-size: 12px;
  color: #94a3b8;
}

/* 图表与指标表格行 */
.charts-row {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 20px;
  margin-bottom: 24px;
}

.chart-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.chart-card-header {
  margin-bottom: 16px;
}

.chart-card-header h3 {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}

.metrics-table-wrapper {
  overflow-x: auto;
}

.metrics-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.metrics-table th, .metrics-table td {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid #f1f5f9;
}

.metrics-table th {
  background: #f8fafc;
  color: #475569;
  font-weight: 600;
}

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

/* 反馈闭环说明 */
.hint-card {
  background: #f8fafc;
  border-color: #cbd5e1;
}

.loop-explanation {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
}

.step-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
}

.highlight-step {
  border-color: #93c5fd;
  background: #eff6ff;
}

.step-num {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #3b82f6;
  color: #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.step-content strong {
  display: block;
  font-size: 13px;
  color: #1e293b;
  margin-bottom: 2px;
}

.step-content p {
  font-size: 12px;
  color: #64748b;
  line-height: 1.4;
}

.step-arrow {
  display: flex;
  justify-content: center;
  color: #94a3b8;
  margin: -4px 0;
}

/* 预警列表区域 */
.alerts-section {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.section-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 16px;
}

.section-header h3 {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
}

.section-tip {
  font-size: 12px;
  color: #94a3b8;
}

.empty-alerts {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 0;
  gap: 12px;
  color: #059669;
  font-size: 14px;
}

.table-wrapper {
  overflow-x: auto;
}

.alerts-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.alerts-table th, .alerts-table td {
  padding: 12px 14px;
  text-align: left;
  border-bottom: 1px solid #e2e8f0;
}

.alerts-table th {
  background: #f8fafc;
  color: #475569;
  font-weight: 600;
}

.type-tag {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
}

.tag-negative {
  background: #fee2e2;
  color: #dc2626;
}

.tag-duplicate {
  background: #fef3c7;
  color: #d97706;
}

.chunk-id-cell {
  font-family: monospace;
  font-size: 12px;
  color: #334155;
}

.down-count-badge {
  display: inline-block;
  padding: 2px 8px;
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
  border-radius: 10px;
  font-weight: 600;
  font-size: 12px;
}

.reason-cell {
  color: #475569;
}

.text-muted {
  color: #94a3b8;
}

.btn-action {
  padding: 5px 12px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 4px;
  color: #2563eb;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-action:hover {
  background: #2563eb;
  color: #ffffff;
  border-color: #2563eb;
}

@media (max-width: 1024px) {
  .metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .charts-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .metrics-grid {
    grid-template-columns: 1fr;
  }
  .dashboard-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
}
</style>
