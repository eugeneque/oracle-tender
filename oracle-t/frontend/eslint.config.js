// Конфигурация ESLint (flat config, ESLint 9). В package.json скрипт `lint` был с самого
// начала, но ни линтера, ни конфига в проекте не было — `npm run lint` просто падал.
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "*.config.js"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Vite Fast Refresh требует, чтобы модуль экспортировал только компоненты. В проекте
      // есть файлы, где рядом с компонентом лежит его же хелпер — предупреждение, не ошибка.
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Неиспользуемый аргумент с префиксом `_` — осознанная заглушка (`_admin: User = Depends(...)`
      // на бэкенде, обработчики событий на фронте), а не забытый код.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
);
