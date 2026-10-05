import { Columns2, KanbanSquare, Table as TableIcon } from "lucide-react";

/** Вид списка на странице тендеров. Выбирается в «Настройки → Интерфейс» (05.10.2026): вид
 * меняют раз и надолго, а переключатель в шапке занимал место при каждом заходе. */
export const TENDER_VIEWS = [
  {
    key: "split",
    label: "Две панели",
    hint: "Список слева, карточка выбранной закупки справа.",
    icon: Columns2,
  },
  {
    key: "kanban",
    label: "Kanban",
    hint: "Доска по этапам работы: перетаскивайте закупки между колонками.",
    icon: KanbanSquare,
  },
  {
    key: "table",
    label: "Таблица",
    hint: "Плотная таблица с сортировкой по любому столбцу.",
    icon: TableIcon,
  },
] as const;

export type TenderViewKey = (typeof TENDER_VIEWS)[number]["key"];

export const TENDERS_VIEW_STORAGE_KEY = "oraclet_tenders_view";

/** Проверка по списку видов не формальность: у тех, кто раньше выбрал удалённые «Список» или
 * «Таймплан», в хранилище остался ключ несуществующего вида. */
export function loadTendersView(): TenderViewKey {
  try {
    const raw = localStorage.getItem(TENDERS_VIEW_STORAGE_KEY);
    if (raw && TENDER_VIEWS.some((view) => view.key === raw)) return raw as TenderViewKey;
  } catch {
    // приватный режим браузера — значение по умолчанию
  }
  return "split";
}

export function saveTendersView(key: TenderViewKey): void {
  try {
    localStorage.setItem(TENDERS_VIEW_STORAGE_KEY, key);
  } catch {
    // не сохранили — вид применится только до перезагрузки
  }
}
