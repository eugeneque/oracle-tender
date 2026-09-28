import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Boxes,
  Building2,
  KanbanSquare,
  Plug,
  ScrollText,
  Settings,
  Star,
  Users as UsersIcon,
} from "lucide-react";

import type { User } from "../../api/types";

// Разделы приложения — один список на оба вида меню. Боковая панель показывает пункты
// подряд, шапка — группами с выпадающими подпунктами (`group`). «Что нового?» сюда не
// входит: это не раздел, а отдельный блок (WhatsNewCard / WhatsNewPill).

export interface NavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Подпись под пунктом в выпадающем меню шапки. */
  hint: string;
  group: NavGroupKey;
  adminOnly?: boolean;
  /** Пункт только выпадающего меню шапки (в боковой панели его заменяет основной раздел). */
  headerOnly?: boolean;
}

export type NavGroupKey = "tenders" | "market" | "company" | "system";

export const NAV_GROUPS: { key: NavGroupKey; label: string }[] = [
  { key: "tenders", label: "Тендеры" },
  { key: "market", label: "Рынок" },
  { key: "company", label: "Компания" },
  { key: "system", label: "Система" },
];

export const NAV_ITEMS: NavItem[] = [
  { label: "Тендеры", to: "/tenders", icon: KanbanSquare, hint: "Все закупки, отбор и оценка ИИ", group: "tenders" },
  {
    label: "Избранное",
    to: "/tenders?favourites=1",
    icon: Star,
    hint: "Закупки, к которым обещали вернуться",
    group: "tenders",
    headerOnly: true,
  },
  { label: "Аналитика", to: "/analytics", icon: BarChart3, hint: "Динамика, победители, доли", group: "market" },
  { label: "Каталог продукции", to: "/catalog", icon: Boxes, hint: "Модели производителей и Госреестр", group: "market" },
  { label: "Моя компания", to: "/company", icon: Building2, hint: "Профиль, участия и реквизиты", group: "company" },
  { label: "Пользователи", to: "/users", icon: UsersIcon, hint: "Учётные записи и роли", group: "company", adminOnly: true },
  { label: "Интеграции", to: "/integrations", icon: Plug, hint: "Модели ИИ, Bitrix24, почта", group: "system" },
  { label: "Логирование", to: "/logs", icon: ScrollText, hint: "Журнал операций системы", group: "system" },
  { label: "Настройки", to: "/settings", icon: Settings, hint: "Источники, профиль отбора, вид меню", group: "system" },
];

export function visibleNavItems(user: User | null | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => !item.adminOnly || user?.role === "admin");
}

/** Вложенные адреса (`/tenders/:id`) подсвечивают свой раздел; query в `to` не учитывается. */
export function isNavItemActive(item: NavItem, pathname: string, search: string): boolean {
  const [path, query] = item.to.split("?");
  if (query) return pathname === path && search.includes(query);
  return pathname === path || pathname.startsWith(`${path}/`);
}
