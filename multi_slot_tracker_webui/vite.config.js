import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// Dev project for the Multi Slot Tracker's browser UI. This directory is NOT part of the apworld
// itself (see ../multi-slot-tracker-implementation-plan.md) -- `npm run build` outputs straight
// into worlds/multi_slot_tracker/webui/dist/, which is the only piece that ends up inside the
// packaged .apworld; this project's own source/node_modules/package.json never do.
export default defineConfig({
  plugins: [vue()],
  base: './', // served from a local http.server, not a domain root -- relative asset URLs
  build: {
    outDir: fileURLToPath(new URL('../worlds/multi_slot_tracker/webui/dist', import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    // during `npm run dev`, proxy API calls to the real Python backend (see WebServer.py) so the
    // Vue dev server's hot-reload can be used against live data instead of needing a full rebuild
    // for every change.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8422',
        changeOrigin: true,
      },
    },
  },
})
