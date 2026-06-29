import { defineConfig } from 'vite'

export default defineConfig({
  root: 'math_camp',
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://127.0.0.1:5000',
    },
  },
})
