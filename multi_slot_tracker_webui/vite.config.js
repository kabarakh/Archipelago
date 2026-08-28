import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// Dev project for the Multi Slot Tracker's browser UI. This directory is NOT part of the apworld
// itself (see ../multi-slot-tracker-implementation-plan.md) -- `npm run build` outputs straight
// into worlds/multi_slot_tracker/webui/dist/, which is the only piece that ends up inside the
// packaged .apworld; this project's own source/node_modules/package.json never do.

// archipelago.json's world_version is the single source of truth for the version shown in the
// UI -- read at build time and baked into the bundle as a constant, rather than duplicating the
// number here or fetching it at runtime, so it can never drift from what a release actually ships.
const manifestPath = fileURLToPath(new URL('../worlds/multi_slot_tracker/archipelago.json', import.meta.url))
const { world_version: worldVersion } = JSON.parse(readFileSync(manifestPath, 'utf-8'))

export default defineConfig({
  plugins: [vue()],
  base: './', // served from a local http.server, not a domain root -- relative asset URLs
  define: {
    __MST_VERSION__: JSON.stringify(worldVersion),
  },
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
