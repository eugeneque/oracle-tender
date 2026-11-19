import { ANALYSIS_ARTICLES } from "./analysis";
import { API_ARTICLES } from "./api";
import { HELP_ARTICLES } from "./help";
import { MARKET_ARTICLES } from "./market";
import { START_ARTICLES } from "./start";
import { SYSTEM_ARTICLES } from "./system";
import { TENDERS_ARTICLES } from "./tenders";
import type { WikiArticle, WikiCategory } from "./types";

export type { WikiArticle, WikiBlock, WikiCategory, WikiCategoryKey, WikiFigure } from "./types";

export const WIKI_CATEGORIES: WikiCategory[] = [
  { key: "start", label: "Начало работы", lead: "Что такое Сова, интерфейс, роли и первый рабочий день." },
  { key: "tenders", label: "Тендеры", lead: "Список, отбор, профили, этапы и выгрузка." },
  { key: "analysis", label: "Разбор и оценка", lead: "Карточка закупки, разбор ИИ, матрица, AI-оценка, Bitrix24." },
  { key: "analytics", label: "Аналитика", lead: "Дашборд по тендерам и рынок ПУ по поверкам." },
  { key: "catalog", label: "Каталог и компания", lead: "Продукция, ПО верхнего уровня, профиль и история участий." },
  { key: "system", label: "Настройки", lead: "Площадки, модели ИИ, приложения, пользователи, обновление." },
  { key: "api", label: "API", lead: "Внешний API по ключу, внутренний по токену, пакет для Bitrix24." },
  { key: "help", label: "Если что-то не так", lead: "Диагностика типичных проблем и частые вопросы." },
];

// Порядок статей внутри раздела — порядок чтения; он же определяет «дальше» внизу статьи.
export const WIKI_ARTICLES: WikiArticle[] = [
  ...START_ARTICLES,
  ...TENDERS_ARTICLES,
  ...ANALYSIS_ARTICLES,
  ...MARKET_ARTICLES,
  ...SYSTEM_ARTICLES,
  ...API_ARTICLES,
  ...HELP_ARTICLES,
];

export const WIKI_BY_SLUG: Record<string, WikiArticle> = Object.fromEntries(
  WIKI_ARTICLES.map((article) => [article.slug, article]),
);

export function categoryOf(article: WikiArticle): WikiCategory {
  return WIKI_CATEGORIES.find((c) => c.key === article.category) ?? WIKI_CATEGORIES[0];
}
