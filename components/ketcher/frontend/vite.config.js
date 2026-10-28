import { defineConfig } from 'vite';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

export default defineConfig({
  base: './',
  resolve: {
    alias: {
      'events': require.resolve('events/'),
      'node:events': require.resolve('events/'),
    }
  },
  build: {
    outDir: path.resolve(__dirname, '../dist'),
    emptyOutDir: true,
    commonjsOptions: {
      transformMixedEsModules: true,
      include: [/node_modules/]
    },
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 14000,
    sourcemap: false
  },
  // Standalone Indigo WASM runs in the browser, without a remote Indigo backend.
  optimizeDeps: { exclude: ['ketcher-standalone'] }
});
