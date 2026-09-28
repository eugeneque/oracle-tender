import { useCallback, useSyncExternalStore } from "react";

export type Theme = "dark" | "light";

// Тот же ключ читает скрипт в index.html до загрузки приложения — чтобы страница сразу
// открывалась в выбранной теме, без вспышки тёмного фона.
const STORAGE_KEY = "theme";

function readTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.classList.add("theme-switching");
  root.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // приватный режим или запрет хранилища: тема действует до перезагрузки
  }
  listeners.forEach((listener) => listener());
  window.setTimeout(() => root.classList.remove("theme-switching"), 250);
}

/** Текущая тема интерфейса и её переключение; выбор хранится в браузере пользователя. */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, readTheme);
  const toggleTheme = useCallback(() => applyTheme(readTheme() === "dark" ? "light" : "dark"), []);
  return { theme, setTheme: applyTheme, toggleTheme };
}
