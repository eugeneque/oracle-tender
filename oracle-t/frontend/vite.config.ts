import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Без strictPort Vite при занятом 5173 молча уезжает на 5174, 5175 и дальше. Выглядит
    // как забота, а на деле означает, что открытая вкладка показывает вчерашний стенд, а
    // новый висит на другом адресе. Пусть лучше запуск падает: занятый порт — это всегда
    // недоубитый прошлый процесс, и `run.sh` разбирается с ним до старта.
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
