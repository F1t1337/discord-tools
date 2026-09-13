import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Сборка идёт в ../dashboard (статику раздаёт Flask). Ограничения строгого CSP панели:
// без инлайновых скриптов (отключаем polyfill modulePreload), один бандл с фиксированными
// именами assets/app.js и assets/app.css, base '/', чтобы ассеты резолвились как /assets/...
export default defineConfig({
  plugins: [svelte()],
  // The panel and jsdom component tests both use Svelte's browser runtime.
  resolve: { conditions: ['browser'] },
  base: '/',
  server: { port: 5174 },
  build: {
    outDir: '../dashboard',
    emptyOutDir: false, // сохраняем dashboard/config.yml и dashboard/miniapp.html
    cssCodeSplit: false,
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0, // не встраивать ассеты как data: (img-src 'self')
    rollupOptions: {
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'assets/app.js',
        chunkFileNames: 'assets/app.js',
        assetFileNames: (info) => {
          const name = info.name || info.names?.[0] || '';
          if (name.endsWith('.css')) return 'assets/app.css';
          return 'assets/[name][extname]';
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.js'],
  },
});
