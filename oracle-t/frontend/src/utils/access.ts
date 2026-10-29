import type { User, UserRole } from "../api/types";

/**
 * Доступ ролей к разделам — в одном месте для меню, поиска по разделам и маршрутов (29.10.2026).
 * Тот же доступ сервер проверяет сам (`require_analytics`, `deny_analyst_catalog`): скрыть пункт
 * меню мало, адрес можно набрать руками.
 *
 * Аналитик — «Тендеры» (с «Минутками»), «Аналитика», «Настройки», «Логирование»; «Каталог
 * продукции», «Пользователи», «Моя компания», «Приложения» и «Модели ИИ» ему закрыты.
 * «Аналитика» — только у аналитика и администратора.
 */

export const ROLE_LABELS: Record<UserRole, string> = {
  admin: "Администратор",
  analyst: "Аналитик",
  user: "Пользователь",
};

export const ROLE_HINTS: Record<UserRole, string> = {
  admin: "Все разделы и управление системой",
  analyst: "Тендеры, Аналитика с рынком ПУ, Настройки, Логирование",
  user: "Тендеры, каталог, компания, приложения — без Аналитики",
};

/** Разделы с ограниченным доступом: адрес → роли, которым он открыт. Не перечисленное открыто всем. */
export const SECTION_ROLES: Record<string, UserRole[]> = {
  "/analytics": ["admin", "analyst"],
  "/catalog": ["admin", "user"],
  "/company": ["admin", "user"],
  "/apps": ["admin", "user"],
  "/users": ["admin"],
  "/ai": ["admin"],
};

export function canOpen(user: User | null | undefined, to: string): boolean {
  const path = to.split("?")[0];
  const section = Object.keys(SECTION_ROLES).find((prefix) => path === prefix || path.startsWith(`${prefix}/`));
  return !section || (!!user && SECTION_ROLES[section].includes(user.role));
}
