import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import AdminChunksView from '../views/AdminChunksView.vue'
import BlogView from '../views/BlogView.vue'
import ChatView from '../views/ChatView.vue'
import GraphCandidatesView from '../views/GraphCandidatesView.vue'
import KnowledgeGraphView from '../views/KnowledgeGraphView.vue'
import QaDebugView from '../views/QaDebugView.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/', name: 'chat', component: ChatView },
  { path: '/blog', name: 'blog', component: BlogView },
  { path: '/admin/chunks', name: 'admin-chunks', component: AdminChunksView },
  { path: '/admin/graph-candidates', name: 'admin-graph-candidates', component: GraphCandidatesView },
  { path: '/admin/graph', name: 'admin-graph', component: KnowledgeGraphView },
  { path: '/admin/qa-debug', name: 'admin-qa-debug', component: QaDebugView },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
