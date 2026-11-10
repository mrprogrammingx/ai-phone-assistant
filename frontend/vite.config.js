import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // shared/services.json lives beside frontend/ and is read by the backend too.
    fs: { allow: ['..'] },
    proxy: {
      '/queue': 'http://localhost:8002',
      '/admin': 'http://localhost:8002',
      '/api': 'http://localhost:8002',
    },
  },
})
