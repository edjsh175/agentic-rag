import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import AdminChunksView from '../views/AdminChunksView.vue'
import BlogView from '../views/BlogView.vue'
import ChatView from '../views/ChatView.vue'
import KnowledgeGraphView from '../views/KnowledgeGraphView.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/', name: 'chat', component: ChatView },
  { path: '/blog', name: 'blog', component: BlogView },
  { path: '/admin/chunks', name: 'admin-chunks', component: AdminChunksView },
  { path: '/admin/graph', name: 'admin-graph', component: KnowledgeGraphView },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
