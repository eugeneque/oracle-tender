import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  Clock,
  Copy,
  ExternalLink,
  Info,
  Lightbulb,
  Moon,
  Search,
  ShieldCheck,
  Sun,
  X,
} from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { DotCover } from "../components/wiki/DotCover";
import { WikiFigureView } from "../components/wiki/WikiFigures";
import { Logo } from "../components/Logo";
import {
  WIKI_ARTICLES,
  WIKI_BY_SLUG,
  WIKI_CATEGORIES,
  categoryOf,
} from "../content/wiki";
import type { WikiArticle, WikiBlock, WikiCategoryKey } from "../content/wiki";
import { useAuth } from "../context/useAuth";
import { useTheme } from "../hooks/useTheme";

/**
 * «Сова Wiki» (19.11.2026) — база знаний внутри приложения: гайды, разбор проблем, описание API.
 * Ссылка на неё — в меню аккаунта над «Выйти». Живёт вне каркаса с боковой панелью: это
 * самостоятельный «сайт» в духе документации, со своей шапкой и поиском, откуда одна кнопка
 * возвращает в Сову. Содержимое — данные в `content/wiki/*.ts`, а не вёрстка.
 */

/* ------------------------------------------------------------------ Поиск */

const normalize = (s: string) => s.toLowerCase().replace(/ё/g, "е");

function blockText(block: WikiBlock): string {
  switch (block.t) {
    case "p":
    case "h2":
    case "h3":
      return block.text;
    case "ul":
    case "steps":
      return block.items.join(" ");
    case "note":
      return `${block.title ?? ""} ${block.text}`;
    case "table":
      return `${block.head.join(" ")} ${block.rows.map((r) => r.join(" ")).join(" ")}`;
    case "faq":
      return block.items.map((i) => `${i.q} ${i.a}`).join(" ");
    case "code":
      return block.text;
    default:
      return "";
  }
}

const SEARCH_INDEX = WIKI_ARTICLES.map((article) => ({
  article,
  title: normalize(article.title),
  head: normalize(`${article.summary} ${article.keywords ?? ""}`),
  body: normalize(article.blocks.map(blockText).join(" ")),
}));

function searchArticles(query: string): WikiArticle[] {
  const words = normalize(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  return SEARCH_INDEX.map((entry) => {
    let score = 0;
    for (const w of words) {
      if (entry.title.includes(w)) score += 10;
      else if (entry.head.includes(w)) score += 5;
      else if (entry.body.includes(w)) score += 1;
      else return null; // каждое слово запроса должно где-то встретиться
    }
    return { article: entry.article, score };
  })
    .filter((x): x is { article: WikiArticle; score: number } => x !== null)
    .sort((a, b) => b.score - a.score)
    .map((x) => x.article);
}

/* --------------------------------------------------------- Инлайн-разметка */

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[\[[^\]]+\]\])/g;

function Inline({ text }: { text: string }) {
  const parts = text.split(INLINE).filter((p) => p !== "");
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**")) {
          return (
            <strong key={i} className="font-semibold text-zinc-100">
              {part.slice(2, -2)}
            </strong>
          );
        }
        if (part.startsWith("`")) {
          return (
            <code key={i} className="rounded-md bg-white/[0.07] px-1.5 py-0.5 font-mono text-[0.86em] text-indigo-200">
              {part.slice(1, -1)}
            </code>
          );
        }
        if (part.startsWith("[[")) {
          const [slug, label] = part.slice(2, -2).split("|");
          const target = WIKI_BY_SLUG[slug];
          if (!target) return <Fragment key={i}>{label ?? slug}</Fragment>;
          return (
            <Link
              key={i}
              to={`/wiki/${slug}`}
              className="text-indigo-400 underline decoration-indigo-400/30 underline-offset-2 transition-colors hover:text-indigo-300 hover:decoration-indigo-300"
            >
              {label ?? target.title}
            </Link>
          );
        }
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

/* ------------------------------------------------------------------ Блоки */

const NOTE_STYLE = {
  tip: { icon: Lightbulb, box: "border-emerald-400/25 bg-emerald-500/[0.06]", ic: "text-emerald-400", label: "Совет" },
  warn: { icon: AlertTriangle, box: "border-amber-400/30 bg-amber-500/[0.07]", ic: "text-amber-400", label: "Важно" },
  info: { icon: Info, box: "border-sky-400/25 bg-sky-500/[0.06]", ic: "text-sky-400", label: "К сведению" },
} as const;

function CodeBlock({ text, lang, caption }: { text: string; lang?: string; caption?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* буфер обмена может быть недоступен — текст виден и так */
    }
  };
  return (
    <div className="my-5 overflow-hidden rounded-2xl border border-white/10 bg-zinc-900">
      <div className="flex h-9 items-center justify-between border-b border-white/[0.06] px-4 text-[11px] text-zinc-500">
        <span className="truncate">{caption ?? lang ?? "код"}</span>
        <button onClick={copy} className="flex items-center gap-1.5 rounded-md px-2 py-1 transition-colors hover:bg-white/[0.06] hover:text-zinc-200">
          {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
          {copied ? "Скопировано" : "Копировать"}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-[12.5px] leading-relaxed text-zinc-300">
        <code>{text}</code>
      </pre>
    </div>
  );
}

function Faq({ items }: { items: { q: string; a: string }[] }) {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <div className="my-5 divide-y divide-white/[0.07] overflow-hidden rounded-2xl border border-white/10">
      {items.map((item, i) => (
        <div key={item.q}>
          <button
            onClick={() => setOpen(open === i ? null : i)}
            aria-expanded={open === i}
            className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left text-[15px] font-medium text-zinc-100 transition-colors hover:bg-white/[0.03]"
          >
            {item.q}
            <ChevronDown size={16} className={`shrink-0 text-zinc-500 transition-transform ${open === i ? "rotate-180" : ""}`} />
          </button>
          {open === i && (
            <div className="px-5 pb-4 text-[15px] leading-7 text-zinc-400">
              <Inline text={item.a} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Blocks({ blocks, headingIds }: { blocks: WikiBlock[]; headingIds: Map<number, string> }) {
  return (
    <>
      {blocks.map((block, index) => {
        switch (block.t) {
          case "p":
            return (
              <p key={index} className="my-4 text-[15.5px] leading-7 text-zinc-300">
                <Inline text={block.text} />
              </p>
            );
          case "h2":
            return (
              <h2 key={index} id={headingIds.get(index)} className="mb-3 mt-11 scroll-mt-24 text-2xl font-semibold tracking-tight text-zinc-50">
                {block.text}
              </h2>
            );
          case "h3":
            return (
              <h3 key={index} className="mb-2 mt-7 text-lg font-semibold text-zinc-100">
                {block.text}
              </h3>
            );
          case "ul":
            return (
              <ul key={index} className="my-4 space-y-2.5">
                {block.items.map((item, i) => (
                  <li key={i} className="flex gap-3 text-[15.5px] leading-7 text-zinc-300">
                    <span className="mt-[0.7em] h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />
                    <span className="min-w-0">
                      <Inline text={item} />
                    </span>
                  </li>
                ))}
              </ul>
            );
          case "steps":
            return (
              <ol key={index} className="my-5 space-y-0">
                {block.items.map((item, i) => (
                  <li key={i} className="relative flex gap-4 pb-5 last:pb-0">
                    {i < block.items.length - 1 && <span className="absolute left-[13px] top-8 h-[calc(100%-1.5rem)] w-px bg-white/10" />}
                    <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-500 text-xs font-semibold text-snow">
                      {i + 1}
                    </span>
                    <span className="min-w-0 pt-0.5 text-[15.5px] leading-7 text-zinc-300">
                      <Inline text={item} />
                    </span>
                  </li>
                ))}
              </ol>
            );
          case "note": {
            const style = NOTE_STYLE[block.kind];
            const Icon = style.icon;
            return (
              <aside key={index} className={`my-6 flex gap-3.5 rounded-2xl border p-4 ${style.box}`}>
                <Icon size={18} className={`mt-0.5 shrink-0 ${style.ic}`} />
                <div className="min-w-0 text-[14.5px] leading-6 text-zinc-300">
                  <div className="mb-0.5 font-semibold text-zinc-100">{block.title ?? style.label}</div>
                  <Inline text={block.text} />
                </div>
              </aside>
            );
          }
          case "table":
            return (
              <div key={index} className="my-6 overflow-x-auto rounded-2xl border border-white/10">
                <table className="w-full min-w-[480px] border-collapse text-left text-[13.5px]">
                  <thead>
                    <tr className="bg-white/[0.04] text-zinc-400">
                      {block.head.map((h, i) => (
                        <th key={i} className="px-4 py-2.5 font-medium">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r} className="border-t border-white/[0.07] align-top">
                        {row.map((cell, c) => (
                          <td key={c} className={`px-4 py-2.5 leading-6 ${c === 0 ? "font-medium text-zinc-100" : "text-zinc-400"}`}>
                            <Inline text={cell} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "code":
            return <CodeBlock key={index} text={block.text} lang={block.lang} caption={block.caption} />;
          case "fig":
            return <WikiFigureView key={index} name={block.name} caption={block.caption} />;
          case "faq":
            return <Faq key={index} items={block.items} />;
          case "go":
            return (
              <Link
                key={index}
                to={block.to}
                className="group my-6 flex items-center justify-between gap-3 rounded-2xl border border-white/10 px-5 py-4 text-[15px] font-medium text-zinc-100 transition-colors hover:border-indigo-400/40 hover:bg-indigo-500/[0.06]"
              >
                {block.label}
                <ArrowRight size={16} className="text-zinc-500 transition-transform group-hover:translate-x-1 group-hover:text-indigo-300" />
              </Link>
            );
          default:
            return null;
        }
      })}
    </>
  );
}

/* ------------------------------------------------------------ Общий каркас */

function WikiHeader({ query, onQuery }: { query?: string; onQuery?: (v: string) => void }) {
  const { theme, setTheme } = useTheme();
  return (
    <header className="sticky top-0 z-30 border-b border-white/[0.07] bg-zinc-950/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-[1240px] items-center gap-4 px-5 sm:px-8">
        <Link to="/wiki" className="flex items-center gap-3" title="Сова Wiki — главная">
          <Logo size={22} withWordmark={false} />
          <span className="text-[15px] font-semibold tracking-tight text-zinc-100">
            Sova <span className="font-normal text-zinc-500">Wiki</span>
          </span>
        </Link>
        {onQuery !== undefined && (
          <label className="ml-2 hidden h-8 w-64 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-2.5 text-sm text-zinc-400 focus-within:border-indigo-400/50 md:flex">
            <Search size={14} className="shrink-0" />
            <input
              value={query ?? ""}
              onChange={(e) => onQuery(e.target.value)}
              placeholder="Поиск по Wiki"
              className="w-full bg-transparent text-zinc-100 outline-none placeholder:text-zinc-600"
            />
          </label>
        )}
        <nav className="ml-auto flex items-center gap-1 text-[13px] text-zinc-400">
          <Link to="/wiki/api-overview" className="hidden rounded-lg px-3 py-1.5 transition-colors hover:text-zinc-100 sm:block">
            API
          </Link>
          <Link to="/wiki/faq" className="hidden rounded-lg px-3 py-1.5 transition-colors hover:text-zinc-100 sm:block">
            FAQ
          </Link>
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
            className="rounded-lg p-2 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
          >
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <Link
            to="/tenders"
            className="ml-1 inline-flex h-8 items-center gap-1.5 rounded-full bg-white px-4 text-[13px] font-semibold text-black transition-transform hover:scale-[1.03] active:scale-95"
          >
            <ArrowLeft size={13} />В Сову
          </Link>
        </nav>
      </div>
    </header>
  );
}

function WikiFooter() {
  const { user } = useAuth();
  return (
    <footer className="mx-auto mt-16 max-w-[1240px] border-t border-white/[0.07] px-5 py-10 sm:px-8">
      <div className="flex flex-col justify-between gap-6 sm:flex-row sm:items-center">
        <div>
          <div className="text-base font-semibold text-zinc-100">Не нашли ответ?</div>
          <p className="mt-1 max-w-md text-sm text-zinc-500">
            Загляните в «Частые вопросы» или в «Если что-то не так». Если не помогло — напишите администратору системы и приложите номер закупки, время и скриншот.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/wiki/faq" className="rounded-full border border-white/10 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-white/25 hover:text-white">
            Частые вопросы
          </Link>
          <Link to="/wiki/not-in-list" className="rounded-full border border-white/10 px-4 py-2 text-sm text-zinc-300 transition-colors hover:border-white/25 hover:text-white">
            Закупки нет в списке
          </Link>
        </div>
      </div>
      <div className="mt-8 text-xs text-zinc-600">
        Sova Wiki · {WIKI_ARTICLES.length} статей · {user ? `вы вошли как @${user.username}` : ""}
      </div>
    </footer>
  );
}

function Frame({ children, header }: { children: ReactNode; header: ReactNode }) {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-300">
      {header}
      <div className="mx-auto max-w-[1240px] border-x border-white/[0.06]">{children}</div>
    </div>
  );
}

/* --------------------------------------------------------------- Карточка */

function ArticleCard({ article }: { article: WikiArticle }) {
  const cat = categoryOf(article);
  const seed = article.seed ?? article.slug.split("").reduce((a, c) => a + c.charCodeAt(0), 0);
  return (
    <Link
      to={`/wiki/${article.slug}`}
      className="group flex flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 transition-[border-color,transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:border-indigo-400/40 hover:shadow-[0_10px_30px_-12px_rgba(24,93,241,0.35)]"
    >
      <div className="relative h-44 overflow-hidden border-b border-white/[0.06] bg-white/[0.02]">
        <DotCover seed={seed} cols={44} rows={18} className="h-full w-full transition-transform duration-500 group-hover:scale-105" />
        <div className="absolute left-3 top-3 flex flex-wrap gap-1.5">
          <span className="rounded-full border border-white/10 bg-zinc-900/90 px-2.5 py-0.5 text-[11px] font-medium text-zinc-200 backdrop-blur">
            {cat.label}
          </span>
          {article.adminOnly && (
            <span className="flex items-center gap-1 rounded-full border border-amber-400/30 bg-zinc-900/90 px-2.5 py-0.5 text-[11px] font-medium text-amber-300 backdrop-blur">
              <ShieldCheck size={11} /> Админ
            </span>
          )}
        </div>
      </div>
      <div className="flex flex-1 flex-col p-5">
        <h3 className="text-[17px] font-semibold leading-snug tracking-tight text-zinc-50">{article.title}</h3>
        <p className="mt-2 line-clamp-3 text-[13.5px] leading-relaxed text-zinc-500">{article.summary}</p>
        <div className="mt-auto flex items-center gap-4 pt-5 text-[11px] text-zinc-500">
          <span className="flex items-center gap-1">
            <Clock size={11} /> {article.minutes} мин
          </span>
          <span>{formatDate(article.updated)}</span>
          <ArrowRight size={13} className="ml-auto text-zinc-600 transition-transform group-hover:translate-x-1 group-hover:text-indigo-300" />
        </div>
      </div>
    </Link>
  );
}

function formatDate(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
}

/* ---------------------------------------------------------------- Главная */

export function WikiPage() {
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<WikiCategoryKey | "all">("all");
  const results = useMemo(() => searchArticles(query), [query]);
  const searching = query.trim().length > 0;

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const a of WIKI_ARTICLES) c[a.category] = (c[a.category] ?? 0) + 1;
    return c;
  }, []);

  const quick = ["Закупки нет в списке", "Разобрать закупку", "Ключ API", "Профиль отбора", "Рынок ПУ", "Bitrix24"];

  return (
    <Frame header={<WikiHeader />}>
      {/* Герой: фоновая сетка точек, синий «остров» из полутона и крупный заголовок. */}
      <section className="relative overflow-hidden border-b border-white/[0.07]">
        <div
          className="absolute inset-0 text-zinc-500/40"
          style={{
            backgroundImage: "radial-gradient(currentColor 1px, transparent 1.3px)",
            backgroundSize: "10px 10px",
            WebkitMaskImage: "linear-gradient(to bottom, black 20%, transparent 85%)",
            maskImage: "linear-gradient(to bottom, black 20%, transparent 85%)",
          }}
        />
        <DotCover seed={11} cols={78} rows={28} fade className="pointer-events-none absolute inset-x-0 top-4 mx-auto h-[310px] w-full max-w-[860px]" />
        <div className="relative flex min-h-[500px] flex-col justify-end gap-8 px-5 pb-10 pt-64 sm:px-10 lg:flex-row lg:items-end lg:justify-between">
          <h1 className="text-5xl font-medium leading-[1.05] tracking-tight sm:text-6xl">
            <span className="block text-zinc-500">Всё о Сове</span>
            <span className="block text-zinc-50">Понятно и по шагам</span>
          </h1>
          <div className="w-full max-w-md">
            <p className="text-sm leading-relaxed text-zinc-400">
              Гайды по тендерам, разбору ИИ, аналитике и настройкам; что делать, если что-то пошло не так; и описание API для интеграций.
            </p>
            <label className="mt-4 flex h-11 items-center gap-2.5 rounded-xl border border-white/15 bg-zinc-900/90 px-3.5 text-zinc-400 backdrop-blur focus-within:border-indigo-400/60">
              <Search size={16} className="shrink-0" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Что вы ищете? Например, «профиль» или «ключ API»"
                className="w-full bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
                autoFocus
              />
              {searching && (
                <button onClick={() => setQuery("")} aria-label="Очистить" className="rounded p-0.5 hover:text-zinc-100">
                  <X size={14} />
                </button>
              )}
            </label>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {quick.map((q) => (
                <button
                  key={q}
                  onClick={() => setQuery(q)}
                  className="rounded-full border border-white/10 px-2.5 py-1 text-[11.5px] text-zinc-400 transition-colors hover:border-indigo-400/40 hover:text-zinc-100"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Вкладки разделов — как «What's Hot / Software / AI» на референсе. */}
      {!searching && (
        <div className="overflow-x-auto border-b border-white/[0.07]">
          <div className="flex min-w-max gap-7 px-5 text-sm sm:px-10" role="tablist">
            {[{ key: "all" as const, label: "Все статьи" }, ...WIKI_CATEGORIES].map((c) => (
              <button
                key={c.key}
                role="tab"
                aria-selected={tab === c.key}
                onClick={() => setTab(c.key)}
                className={`relative py-4 transition-colors ${tab === c.key ? "text-zinc-50" : "text-zinc-500 hover:text-zinc-200"}`}
              >
                {c.label}
                <span className="ml-1.5 text-[11px] tabular-nums text-zinc-600">
                  {c.key === "all" ? WIKI_ARTICLES.length : counts[c.key]}
                </span>
                {tab === c.key && <span className="absolute inset-x-0 bottom-0 h-px bg-zinc-50" />}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="px-5 py-10 sm:px-10">
        {searching ? (
          <>
            <div className="mb-6 text-sm text-zinc-500">
              {results.length > 0 ? `Найдено статей: ${results.length}` : "Ничего не найдено"}
            </div>
            {results.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-white/15 p-10 text-center text-sm text-zinc-500">
                <BookOpen size={22} className="mx-auto mb-3 text-zinc-600" />
                Попробуйте другое слово или откройте раздел <Link to="/wiki/faq" className="text-indigo-400 hover:text-indigo-300">«Частые вопросы»</Link>.
              </div>
            ) : (
              <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                {results.map((a) => (
                  <ArticleCard key={a.slug} article={a} />
                ))}
              </div>
            )}
          </>
        ) : (
          WIKI_CATEGORIES.filter((c) => tab === "all" || tab === c.key).map((cat) => (
            <section key={cat.key} className="mb-14 last:mb-0">
              <div className="mb-5 flex items-baseline gap-3">
                <h2 className="text-xl font-semibold tracking-tight text-zinc-50">{cat.label}</h2>
                <span className="text-sm text-zinc-500">{cat.lead}</span>
              </div>
              <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                {WIKI_ARTICLES.filter((a) => a.category === cat.key).map((a) => (
                  <ArticleCard key={a.slug} article={a} />
                ))}
              </div>
            </section>
          ))
        )}
      </div>
      <WikiFooter />
    </Frame>
  );
}

/* ----------------------------------------------------------------- Статья */

export function WikiArticlePage() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const article = WIKI_BY_SLUG[slug];
  const [query, setQuery] = useState("");

  // Заголовки второго уровня → оглавление справа и якоря.
  const headings = useMemo(() => {
    const ids = new Map<number, string>();
    const list: { id: string; text: string }[] = [];
    article?.blocks.forEach((b, i) => {
      if (b.t === "h2") {
        const id = `s${list.length + 1}`;
        ids.set(i, id);
        list.push({ id, text: b.text });
      }
    });
    return { ids, list };
  }, [article]);

  const [activeId, setActiveId] = useState<string | null>(null);
  const observer = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    window.scrollTo({ top: 0 });
    setQuery("");
  }, [slug]);

  useEffect(() => {
    if (headings.list.length === 0) return;
    observer.current?.disconnect();
    observer.current = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActiveId(visible[0].target.id);
      },
      { rootMargin: "-80px 0px -65% 0px" },
    );
    headings.list.forEach((h) => {
      const el = document.getElementById(h.id);
      if (el) observer.current?.observe(el);
    });
    return () => observer.current?.disconnect();
  }, [headings, slug]);

  const results = useMemo(() => searchArticles(query).slice(0, 6), [query]);

  if (!article) return <Navigate to="/wiki" replace />;

  const cat = categoryOf(article);
  const index = WIKI_ARTICLES.findIndex((a) => a.slug === article.slug);
  const prev = WIKI_ARTICLES[index - 1];
  const next = WIKI_ARTICLES[index + 1];
  const related = WIKI_ARTICLES.filter((a) => a.category === article.category && a.slug !== article.slug).slice(0, 3);
  const seed = article.seed ?? article.slug.split("").reduce((a, c) => a + c.charCodeAt(0), 0);

  return (
    <Frame header={<WikiHeader query={query} onQuery={setQuery} />}>
      {query.trim() && (
        <div className="border-b border-white/[0.07] bg-zinc-900/60 px-5 py-4 sm:px-10">
          {results.length === 0 ? (
            <div className="text-sm text-zinc-500">Ничего не найдено</div>
          ) : (
            <ul className="grid gap-1 sm:grid-cols-2">
              {results.map((r) => (
                <li key={r.slug}>
                  <button
                    onClick={() => navigate(`/wiki/${r.slug}`)}
                    className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm text-zinc-300 transition-colors hover:bg-white/[0.05] hover:text-white"
                  >
                    <span className="truncate">{r.title}</span>
                    <span className="shrink-0 text-[11px] text-zinc-600">{categoryOf(r).label}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="grid gap-10 px-5 pb-6 pt-8 sm:px-10 lg:grid-cols-[minmax(0,1fr)_230px]">
        <article className="min-w-0 max-w-[760px]">
          <nav aria-label="Навигация" className="mb-5 flex flex-wrap items-center gap-1.5 text-[13px] text-zinc-500">
            <Link to="/wiki" className="hover:text-zinc-200">Wiki</Link>
            <span className="text-zinc-700">/</span>
            <Link to={`/wiki#${cat.key}`} className="hover:text-zinc-200">{cat.label}</Link>
          </nav>

          <div className="relative mb-7 h-28 overflow-hidden rounded-2xl border border-white/10 bg-white/[0.02]">
            <DotCover seed={seed} cols={90} rows={11} className="h-full w-full" />
          </div>

          <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-zinc-500">
            <span className="rounded-full border border-white/10 px-2.5 py-0.5 text-zinc-300">{cat.label}</span>
            {article.adminOnly && (
              <span className="flex items-center gap-1 rounded-full border border-amber-400/30 px-2.5 py-0.5 text-amber-300">
                <ShieldCheck size={11} /> Для администратора
              </span>
            )}
            <span className="flex items-center gap-1"><Clock size={11} /> {article.minutes} мин чтения</span>
            <span>Обновлено {formatDate(article.updated)}</span>
          </div>

          <h1 className="mt-3 text-4xl font-semibold leading-tight tracking-tight text-zinc-50">{article.title}</h1>
          <p className="mt-3 text-lg leading-relaxed text-zinc-400">{article.summary}</p>

          {article.open && (
            <Link
              to={article.open.to}
              className="mt-5 inline-flex items-center gap-2 rounded-full bg-indigo-500 px-4 py-2 text-sm font-medium text-snow transition-colors hover:bg-indigo-400"
            >
              {article.open.label}
              <ExternalLink size={13} />
            </Link>
          )}

          <div className="mt-6 border-t border-white/[0.07] pt-2">
            <Blocks blocks={article.blocks} headingIds={headings.ids} />
          </div>

          <div className="mt-12 grid gap-3 sm:grid-cols-2">
            {prev ? (
              <Link to={`/wiki/${prev.slug}`} className="group rounded-2xl border border-white/10 p-4 transition-colors hover:border-indigo-400/40">
                <div className="flex items-center gap-1.5 text-[11px] text-zinc-500"><ArrowLeft size={12} /> Назад</div>
                <div className="mt-1 text-sm font-medium text-zinc-100">{prev.title}</div>
              </Link>
            ) : <span />}
            {next && (
              <Link to={`/wiki/${next.slug}`} className="group rounded-2xl border border-white/10 p-4 text-right transition-colors hover:border-indigo-400/40">
                <div className="flex items-center justify-end gap-1.5 text-[11px] text-zinc-500">Дальше <ArrowRight size={12} /></div>
                <div className="mt-1 text-sm font-medium text-zinc-100">{next.title}</div>
              </Link>
            )}
          </div>
        </article>

        <aside className="hidden lg:block">
          <div className="sticky top-24 space-y-8">
            {headings.list.length > 1 && (
              <div>
                <div className="mb-3 text-[11px] font-medium uppercase tracking-wide text-zinc-500">На этой странице</div>
                <ul className="space-y-0.5 border-l border-white/10">
                  {headings.list.map((h) => (
                    <li key={h.id}>
                      <a
                        href={`#${h.id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
                          setActiveId(h.id);
                        }}
                        className={`-ml-px block border-l py-1.5 pl-4 text-[13px] leading-snug transition-colors ${
                          activeId === h.id ? "border-indigo-400 text-zinc-50" : "border-transparent text-zinc-500 hover:text-zinc-200"
                        }`}
                      >
                        {h.text}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {related.length > 0 && (
              <div>
                <div className="mb-3 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Читайте также</div>
                <ul className="space-y-2">
                  {related.map((r) => (
                    <li key={r.slug}>
                      <Link to={`/wiki/${r.slug}`} className="text-[13px] leading-snug text-zinc-400 transition-colors hover:text-indigo-300">
                        {r.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </aside>
      </div>
      <WikiFooter />
    </Frame>
  );
}
