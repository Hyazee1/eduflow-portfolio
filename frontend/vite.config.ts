import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  define: { __EDUFLOW_PORTAL__: JSON.stringify(mode === 'public' ? 'PUBLIC' : 'ADMIN') },
  plugins: [react()],
  server: { host: '127.0.0.1', proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: {
    outDir: 'dist/' + (mode === 'public' ? 'public' : 'admin'),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: { output: { manualChunks: { ...(mode === 'public' ? {} : { charts: ['echarts'] }), ui: ['antd'], icons: ['@ant-design/icons'], framework: ['react', 'react-dom', 'react-router-dom'] } } },
  },
}));
