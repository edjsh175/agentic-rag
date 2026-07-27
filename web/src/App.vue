<script setup lang="ts">
/**
 * 根组件 —— 提供主要页面导航。
 */
import 'github-markdown-css/github-markdown.css'
</script>

<template>
  <div class="app-root">
    <!-- 导航栏 -->
    <nav class="nav-bar">
      <RouterLink class="nav-btn" to="/">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        知识库问答
      </RouterLink>
      <RouterLink class="nav-btn" to="/blog">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
        </svg>
        博客管理
      </RouterLink>
      <RouterLink class="nav-btn" to="/admin/chunks">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
        </svg>
        审核工作台
      </RouterLink>
      <RouterLink class="nav-btn" to="/admin/graph-candidates">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M9 12l2 2 4-4"/>
          <path d="M21 12c0 4.97-4.03 9-9 9S3 16.97 3 12 7.03 3 12 3c2.12 0 4.07.73 5.62 1.96"/>
          <path d="M16 5h5v5"/>
        </svg>
        图谱候选审批
      </RouterLink>
      <RouterLink class="nav-btn" to="/admin/graph">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="3"/>
          <circle cx="12" cy="4" r="3"/>
          <circle cx="4" cy="12" r="3"/>
          <circle cx="20" cy="12" r="3"/>
          <circle cx="12" cy="20" r="3"/>
          <line x1="12" y1="7" x2="12" y2="9"/>
          <line x1="12" y1="15" x2="12" y2="17"/>
          <line x1="7" y1="12" x2="9" y2="12"/>
          <line x1="15" y1="12" x2="17" y2="12"/>
        </svg>
        图谱管理
      </RouterLink>
      <RouterLink class="nav-btn" to="/admin/qa-debug">
        问答证据调试
      </RouterLink>
    </nav>

    <!-- 视图容器 -->
    <div class="view-container">
      <RouterView v-slot="{ Component }">
        <KeepAlive>
          <component :is="Component" />
        </KeepAlive>
      </RouterView>
    </div>
  </div>
</template>

<style>
/* 全局重置 */
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}
html, body {
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans SC', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background: #fff;
  color: #1e293b;
}
#app {
  height: 100%;
}

.app-root {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.nav-bar {
  display: flex;
  gap: 4px;
  padding: 8px 24px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.nav-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  border: 1px solid transparent;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  background: transparent;
  color: #64748b;
  transition: all 0.15s;
  text-decoration: none;
}
.nav-btn:hover {
  background: #f1f5f9;
  color: #334155;
}
.nav-btn.router-link-exact-active {
  background: #fff;
  border-color: #e2e8f0;
  color: #1e293b;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

.view-container {
  flex: 1;
  overflow: hidden;
}

@media (max-width: 768px) {
  .nav-bar {
    padding: 6px 12px;
    gap: 4px;
  }
  .nav-btn {
    flex: 1;
    justify-content: center;
    padding: 8px 8px;
    font-size: 13px;
    border-radius: 6px;
  }
}

</style>
