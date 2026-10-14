import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Конфиг выполняется в Node, но @types/node в зависимостях нет: локально его подтягивало
// транзитивно, а чистый `npm install` на сервере (update.sh, 14.10.2026) его не ставит —
// и `tsc -b` падал на TS2580. Нам нужен один `process.env`, тащить ради него пакет незачем.
declare const process: { env: Record<string, string | undefined> };

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
        // VITE_API_TARGET — второй стенд рядом с основным (проверка правки, пока на 8000
        // крутится рабочий API с задачами в очереди); по умолчанию — API из `run.sh`.
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        // Бэкенд авторизует по Bearer-токену, cookie ему не нужны. А чужие cookie с localhost
        // легко перерастают лимит заголовков uvicorn — тот же 431, только уже от API.
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => proxyReq.removeHeader("cookie"));
        },
      },
    },
  },
});
