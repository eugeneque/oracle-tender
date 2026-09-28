import { useCallback, useSyncExternalStore } from "react";

/**
 * Вид главного меню (28.09.2026): боковая панель или строка разделов в шапке. Шапка
 * освобождает под карточку тендера ~260 px по ширине — на ноутбуке это разница между
 * читаемой карточкой и столбиком по три слова. Выбор личный и хранится в браузере, как тема.
 */
export type NavLayout = "sidebar" | "header";

const STORAGE_KEY = "nav.layout";

function readLayout(): NavLayout {
  try {
    return localStorage.getItem(STORAGE_KEY) === "header" ? "header" : "sidebar";
  } catch {
    return "sidebar";
  }
}

let current: NavLayout = readLayout();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function applyLayout(layout: NavLayout) {
  current = layout;
  try {
    localStorage.setItem(STORAGE_KEY, layout);
  } catch {
    // без хранилища выбор действует до перезагрузки
  }
  listeners.forEach((listener) => listener());
}

export function useNavLayout() {
  const layout = useSyncExternalStore(subscribe, () => current);
  const setLayout = useCallback((next: NavLayout) => applyLayout(next), []);
  return { layout, setLayout };
}
