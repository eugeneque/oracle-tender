// Сова Wiki (19.11.2026) — база знаний внутри приложения: как пользоваться Совой, что делать при
// сбое, описание API. Статьи — данные, а не вёрстка: правятся в `content/wiki/*.ts`, а страница
// `pages/Wiki.tsx` рисует их одинаково. Новая возможность Совы — новая статья или абзац здесь.
//
// Внутри текстов поддерживается мини-разметка: **жирный**, `код`, [[slug|подпись]] — ссылка на
// другую статью Wiki (без подписи берётся её заголовок).

export type WikiCategoryKey =
  | "start"
  | "tenders"
  | "analysis"
  | "analytics"
  | "catalog"
  | "system"
  | "api"
  | "help";

export interface WikiCategory {
  key: WikiCategoryKey;
  label: string;
  /** Одна фраза под названием раздела — для главной Wiki. */
  lead: string;
}

/** Иллюстрации — схематичные макеты интерфейса из `components/wiki/WikiFigures.tsx`. */
export type WikiFigure =
  | "pipeline"
  | "sidebar"
  | "roles"
  | "tender-list"
  | "kanban"
  | "funnel"
  | "profile"
  | "stages"
  | "card-tabs"
  | "mira"
  | "matrix"
  | "ai-score"
  | "bitrix-flow"
  | "analytics"
  | "market"
  | "catalog-card"
  | "scheduler"
  | "api-flow"
  | "account-menu"
  | "not-in-list";

export type WikiBlock =
  | { t: "p"; text: string }
  | { t: "h2"; text: string }
  | { t: "h3"; text: string }
  | { t: "ul"; items: string[] }
  | { t: "steps"; items: string[] }
  | { t: "note"; kind: "tip" | "warn" | "info"; title?: string; text: string }
  | { t: "table"; head: string[]; rows: string[][] }
  | { t: "code"; lang?: string; caption?: string; text: string }
  | { t: "fig"; name: WikiFigure; caption: string }
  | { t: "faq"; items: { q: string; a: string }[] }
  | { t: "go"; to: string; label: string };

export interface WikiArticle {
  slug: string;
  category: WikiCategoryKey;
  title: string;
  /** Две строки на карточке и в результатах поиска. */
  summary: string;
  /** Сколько минут читать. */
  minutes: number;
  /** Дата последней правки статьи, ISO. */
  updated: string;
  /** Слова, по которым статью должен находить поиск, но которых нет в тексте. */
  keywords?: string;
  /** Статья про раздел, который виден только администратору. */
  adminOnly?: boolean;
  /** Узор на обложке карточки; по умолчанию — из slug. */
  seed?: number;
  /** Куда перейти в приложении, чтобы попробовать. */
  open?: { to: string; label: string };
  blocks: WikiBlock[];
}
