import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    css: { include: /src\/style\.css/ },
    include: ['src/**/*.test.ts'],
    setupFiles: ['src/testSetup.ts'],
  },
})
