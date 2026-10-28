import { defineConfig } from 'vite';
import path from 'node:path';

export default defineConfig({
  base: './',
  build: {
    outDir: path.resolve(__dirname, '../dist'),
    emptyOutDir: true,
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 14000,
    sourcemap: false
  },
  // Standalone Indigo WASM runs in the browser, without a remote Indigo backend.
  optimizeDeps: { exclude: ['ketcher-standalone'] }
});
