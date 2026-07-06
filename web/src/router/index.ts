import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

import AdminChunksView from '../views/AdminChunksView.vue'
import BlogView from '../views/BlogView.vue'
import ChatView from '../views/ChatView.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/', name: 'chat', component: ChatView },
  { path: '/blog', name: 'blog', component: BlogView },
  { path: '/admin/chunks', name: 'admin-chunks', component: AdminChunksView },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
