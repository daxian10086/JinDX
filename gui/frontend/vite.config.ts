import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { viteSingleFile } from 'vite-plugin-singlefile'

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  base: './',
  server: {
    port: 5173,
  },
  build: {
    target: 'es2015',
    outDir: 'dist',
    rollupOptions: {
      external: ['/wails/runtime.js'],
      output: {
        format: 'iife',
        globals: {
          '/wails/runtime.js': 'window.wails',
        },
      },
    },
  },
})
