import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import process from 'node:process'

// https://vite.dev/config/
const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:7860'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    include: [
      'src/pageIntent.test.js',
      'src/smartPaperRunClient.test.js',
      'src/videoTaskEvidence.test.js',
      'src/urlRouting.test.js',
      'src/**/*.test.jsx',
    ],
    css: true,
    // 默认 5s 在 2 核机器上跑满 90 个文件时会随机超时（实测 TextbookChapterLearning、
    // PersonalizationPage、StudyNotesPanel 都有过），而这些用例本身只耗时几百毫秒，
    // 属于并行争抢而非真实卡死。放宽到 20s，保留“真卡死仍会失败”的能力。
    testTimeout: 20000,
    hookTimeout: 20000,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'remark-gfm', 'remark-math', 'rehype-katex', 'katex'],
          syntax: ['react-syntax-highlighter'],
          icons: ['lucide-react'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api/v1': {
        target: process.env.VITE_MAIN_API_TARGET || apiTarget,
        changeOrigin: true,
      },
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/health': {
        target: process.env.VITE_MAIN_API_TARGET || apiTarget,
        changeOrigin: true,
      },
      '/platform-assets': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:7860',
        changeOrigin: true,
      },
      '/knowledge-graph': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
