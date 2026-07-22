import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import process from 'node:process'

const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:7860'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    include: ['src/pageIntent.test.js', 'src/**/*.test.jsx'],
    css: true,
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
        target: apiTarget,
        changeOrigin: true,
      },
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/health': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
