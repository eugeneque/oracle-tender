import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import {
  ArrowUpRight,
  CornerDownLeft,
  Globe,
  KeyRound,
  Landmark,
  MapPin,
  MessageSquareText,
  Palette,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { api } from "../api/client";
import type { Source } from "../api/types";
import { MANUAL_SOURCE_TYPE, isExternalFeedSource, type ExternalFeed } from "../api/types";
import { AppShell } from "../components/AppShell";
import { CredentialsSection } from "../components/settings/CredentialsSection";
import { FeedChannelSection } from "../components/settings/FeedChannelSection";
import { InterfaceSection } from "../components/settings/InterfaceSection";
import { RegionResponsiblesSection } from "../components/settings/RegionResponsiblesSection";
import { RelevanceProfileSection } from "../components/settings/RelevanceProfileSection";
import { SourcesSection } from "../components/settings/SourcesSection";
import { SpecialistFeedbackSection } from "../components/settings/SpecialistFeedbackSection";
import { useAuth } from "../context/useAuth";
import { SPRING_SNAPPY, SPRING_SOFT } from "../utils/motion";

/**
 * «Настройки» (редизайн 28.09.2026): вкладки сверху вместо ленты сворачиваемых блоков и
 * поиск по настройкам.
 *
 * Раньше блоки стояли друг под другом, и чтобы добраться до ответственных по регионам,
 * приходилось листать мимо таблицы источников и профиля релевантности. Теперь каждый раздел —
 * своя вкладка (выбранная живёт в адресе `?tab=`, ссылку можно переслать), а поиск находит
 * настройку по словам, открывает её вкладку и подсвечивает саму карточку. В поиск попадают и
 * настройки соседних страниц («Интеграции», «Логирование», «Моя компания») — человек ищет
 * «ключ DeepSeek» в настройках, не помня, что модели ИИ вынесены отдельно.
 */

type TabKey =
  | "sources"
  | "gosplan"
  | "seldon"
  | "tenderplan"
  | "credentials"
  | "relevance"
  | "regions"
  | "feedback"
  | "interface";

interface TabDef {
  key: TabKey;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

const TABS: TabDef[] = [
  { key: "sources", label: "Источники", icon: Globe },
  { key: "gosplan", label: "Госплан", icon: Landmark },
  { key: "seldon", label: "Селдон", icon: Landmark },
  { key: "tenderplan", label: "Тендерплан", icon: Landmark },
  { key: "relevance", label: "Профиль отбора", icon: SlidersHorizontal },
  { key: "credentials", label: "Доступы", icon: KeyRound, adminOnly: true },
  { key: "regions", label: "Регионы", icon: MapPin, adminOnly: true },
  { key: "feedback", label: "Ответы специалистов", icon: MessageSquareText },
  { key: "interface", label: "Интерфейс", icon: Palette },
];

interface SearchEntry {
  title: string;
  hint: string;
  keywords?: string;
  adminOnly?: boolean;
  /** Настройка на этой странице: вкладка и якорь карточки (без якоря — вкладка целиком). */
  tab?: TabKey;
  anchor?: string;
  /** Настройка на другой странице приложения. */
  to?: string;
  page?: string;
}

const SEARCH_INDEX: SearchEntry[] = [
  { tab: "sources", anchor: "sources-connected", title: "Подключённые площадки", hint: "Источники тендеров, опрос и доступность", keywords: "источники площадки опрос опросить еис синхронизация доступность стандартные ресурсы" },
  { tab: "sources", anchor: "sources-queued", title: "Площадки в очереди", hint: "Ещё не подключённые источники", keywords: "очередь не подключены" },
  { tab: "sources", anchor: "sources-catalog", title: "Справочники продукции", hint: "Госреестр СИ, сайты производителей, реестры допуска", keywords: "фгис аршин госреестр производители каталог по верхнего уровня пирамида гисп зак россети" },
  { tab: "gosplan", anchor: "gosplan-tariff", title: "Тариф Госплана", hint: "Бесплатный или платный доступ к API", keywords: "госплан тариф платный бесплатный лимит api" },
  { tab: "gosplan", anchor: "gosplan-availability", title: "Сбор из Госплана", hint: "Состояние канала и запуск сбора", keywords: "госплан собрать опрос состояние доступность" },
  { tab: "gosplan", anchor: "gosplan-key", title: "Ключ API Госплана", hint: "Ключ платного тарифа", keywords: "госплан ключ токен apikey api", adminOnly: true },
  { tab: "gosplan", anchor: "gosplan-search", title: "Как Госплан ищет закупки", hint: "ОКПД2, слова, документы из ЕИС", keywords: "госплан окпд2 поиск документы модель" },
  { tab: "seldon", anchor: "seldon-tariff", title: "Доступ к Seldon.API", hint: "Подключение канала Селдон", keywords: "селдон seldon api доступ договор" },
  { tab: "seldon", anchor: "seldon-availability", title: "Сбор из Селдона", hint: "Состояние канала и запуск сбора", keywords: "селдон seldon собрать опрос состояние" },
  { tab: "seldon", anchor: "seldon-key", title: "Ключ Seldon.API", hint: "Ключ, выданный по договору", keywords: "селдон seldon ключ токен api", adminOnly: true },
  { tab: "tenderplan", anchor: "tenderplan-tariff", title: "Доступ к API Тендерплана", hint: "Подключение канала Тендерплан", keywords: "тендерплан tenderplan api доступ" },
  { tab: "tenderplan", anchor: "tenderplan-availability", title: "Сбор из Тендерплана", hint: "Состояние канала и запуск сбора", keywords: "тендерплан tenderplan собрать опрос состояние" },
  { tab: "tenderplan", anchor: "tenderplan-key", title: "Токен Тендерплана", hint: "Персональный токен из личного кабинета", keywords: "тендерплан tenderplan токен ключ pat api", adminOnly: true },
  { tab: "tenderplan", anchor: "tenderplan-search", title: "Как Тендерплан ищет закупки", hint: "ОКПД2, слова, документы", keywords: "тендерплан окпд2 поиск документы" },
  { tab: "relevance", anchor: "relevance-ai-check", title: "Проверка закупок моделью", hint: "ИИ-отбор накопленного архива", keywords: "ии ai модель нейросеть проверить архив отбор", adminOnly: true },
  { tab: "relevance", anchor: "relevance-recalculate", title: "Пересчёт профиля", hint: "Применить профиль к собранным тендерам", keywords: "пересчитать профиль релевантность", adminOnly: true },
  { tab: "relevance", anchor: "relevance-queries", title: "Поисковые фразы", hint: "Чем система ищет на площадках", keywords: "фразы запросы охват поиск" },
  { tab: "relevance", anchor: "relevance-groups", title: "Группы потребностей", hint: "Ключевые слова и исключения профиля", keywords: "профиль релевантности ключевые слова исключения группы окпд2" },
  { tab: "credentials", anchor: "credentials-list", title: "Доступы к площадкам", hint: "Логины и пароли личных кабинетов", keywords: "логин пароль учётка учетные данные личный кабинет доступ", adminOnly: true },
  { tab: "regions", anchor: "regions-assign", title: "Ответственный за регион", hint: "Назначение ответственного и руководителя", keywords: "регион ответственный руководитель выгрузка excel", adminOnly: true },
  { tab: "regions", anchor: "regions-list", title: "Назначенные регионы", hint: "Список назначений", keywords: "регионы список", adminOnly: true },
  { tab: "feedback", title: "Ответы специалистов", hint: "Замечания специалистов к заключениям ИИ", keywords: "заключение ии несогласия согласия замечания обратная связь" },
  { tab: "interface", anchor: "interface-dark-theme", title: "Тёмная тема", hint: "Оформление приложения", keywords: "тема светлая тёмная темная оформление цвет" },
  { tab: "interface", anchor: "interface-nav", title: "Вид меню", hint: "Боковое меню или меню в шапке", keywords: "меню сайдбар шапка навигация" },
  { to: "/integrations", page: "Интеграции", title: "Модели ИИ", hint: "Claude, DeepSeek, YandexGPT — ключи и выбор модели", keywords: "ии ai модель нейросеть ключ routerai deepseek claude yandex провайдер", adminOnly: true },
  { to: "/integrations", page: "Интеграции", title: "Rusprofile", hint: "Учётная запись для заполнения компании", keywords: "rusprofile русрофиль компания", adminOnly: true },
  { to: "/integrations", page: "Интеграции", title: "Почта и уведомления", hint: "Ящик, рассылка, журнал отправок", keywords: "почта email уведомления рассылка письма" },
  { to: "/integrations", page: "Интеграции", title: "Bitrix24", hint: "Выгрузка лидов и ключи API", keywords: "битрикс bitrix crm лиды" },
  { to: "/logs", page: "Логирование", title: "Журнал операций", hint: "Опросы, анализ, действия пользователей", keywords: "лог журнал ошибки логирование" },
  { to: "/company", page: "Моя компания", title: "Профиль компании", hint: "Реквизиты, допуски, история участий", keywords: "компания инн реквизиты миртек участия" },
  { to: "/users", page: "Пользователи", title: "Пользователи и роли", hint: "Учётные записи сотрудников", keywords: "пользователи роли администратор сотрудники", adminOnly: true },
  { to: "/account", page: "Мой аккаунт", title: "Мой аккаунт", hint: "Имя, пароль, аватар", keywords: "аккаунт пароль профиль аватар" },
];

function normalize(text: string): string {
  return text.toLowerCase().replace(/ё/g, "е");
}

function words(text: string): string[] {
  return normalize(text).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
}

/** Насколько запись подходит под запрос: 0 — не подходит. Каждое слово запроса должно быть
 * началом какого-то слова записи — иначе «ключ» находил бы «подключённые площадки».
 * Совпадения в названии весят больше, чем в пояснении и ключевых словах. */
function score(entry: SearchEntry, query: string, tabLabel: string): number {
  const tokens = words(query);
  if (tokens.length === 0) return 0;
  const title = words(entry.title);
  const rest = words([entry.hint, entry.keywords ?? "", entry.page ?? "", tabLabel].join(" "));
  let total = 0;
  for (const token of tokens) {
    if (title.some((word) => word.startsWith(token))) total += 3;
    else if (rest.some((word) => word.startsWith(token))) total += 1;
    else return 0;
  }
  return total;
}

/** Прокрутка к найденной карточке и короткая подсветка — чтобы глаз сразу нашёл её на вкладке. */
function focusAnchor(anchor: string) {
  const element = document.getElementById(anchor);
  if (!element) return false;
  element.scrollIntoView({ behavior: "smooth", block: "center" });
  element.animate(
    [
      { boxShadow: "0 0 0 3px rgb(var(--c-indigo-500) / 0.55)" },
      { boxShadow: "0 0 0 3px rgb(var(--c-indigo-500) / 0.55)", offset: 0.6 },
      { boxShadow: "0 0 0 0 rgb(var(--c-indigo-500) / 0)" },
    ],
    { duration: 1800, easing: "ease-out" },
  );
  return true;
}

function SettingsSearch({
  isAdmin,
  sources,
  onPick,
}: {
  isAdmin: boolean;
  sources: Source[];
  onPick: (entry: SearchEntry) => void;
}) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // «/» или ⌘K из любого места страницы — к поиску, как в большинстве панелей настроек.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable);
      if ((event.key === "/" && !typing) || (event.key === "k" && (event.metaKey || event.ctrlKey))) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const index = useMemo(() => {
    // Площадки ищутся по имени: «Росэлторг» должен находить свою карточку, а не общий раздел.
    const sourceEntries: SearchEntry[] = sources
      .filter((s) => s.type !== MANUAL_SOURCE_TYPE)
      .map((s) =>
        isExternalFeedSource(s.type)
          ? { tab: s.type as ExternalFeed, anchor: `${s.type}-availability`, title: s.name, hint: "Канал сбора через API" }
          : {
              tab: "sources",
              anchor: `source-${s.key}`,
              title: s.name,
              hint: s.url.replace(/^https?:\/\//, ""),
              keywords: "площадка источник",
            },
      );
    return [...SEARCH_INDEX, ...sourceEntries].filter((entry) => isAdmin || !entry.adminOnly);
  }, [sources, isAdmin]);

  const results = useMemo(() => {
    if (!query.trim()) return [];
    return index
      .map((entry) => {
        const tabLabel = TABS.find((tab) => tab.key === entry.tab)?.label ?? "";
        return { entry, rank: score(entry, query, tabLabel) };
      })
      .filter((item) => item.rank > 0)
      // Стабильная сортировка: при равном весе остаётся порядок вкладок.
      .sort((a, b) => b.rank - a.rank)
      .slice(0, 8)
      .map((item) => item.entry);
  }, [index, query]);

  useEffect(() => setCursor(0), [query]);

  const pick = (entry: SearchEntry) => {
    onPick(entry);
    setQuery("");
    setIsOpen(false);
    inputRef.current?.blur();
  };

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (event.key === "Enter" && results[cursor]) {
      event.preventDefault();
      pick(results[cursor]);
    } else if (event.key === "Escape") {
      setQuery("");
      inputRef.current?.blur();
    }
  };

  const showPanel = isOpen && query.trim() !== "";

  return (
    <div className="relative w-full sm:w-80">
      <Search
        size={16}
        className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-zinc-500"
      />
      <input
        ref={inputRef}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setIsOpen(true);
        }}
        onFocus={() => setIsOpen(true)}
        onBlur={() => window.setTimeout(() => setIsOpen(false), 120)}
        onKeyDown={onKeyDown}
        placeholder="Поиск по настройкам"
        aria-label="Поиск по настройкам"
        className="h-11 w-full rounded-full border border-white/[0.08] bg-white/[0.05] pl-11 pr-12 text-sm text-white placeholder-zinc-500 outline-none transition-colors focus:border-indigo-500/60 focus:bg-white/[0.07]"
      />
      {query ? (
        <button
          onClick={() => setQuery("")}
          title="Очистить"
          className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full p-1 text-zinc-500 hover:text-zinc-200"
        >
          <X size={14} />
        </button>
      ) : (
        <kbd className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 rounded-md border border-white/10 px-1.5 text-[11px] text-zinc-500">
          /
        </kbd>
      )}

      <AnimatePresence>
        {showPanel && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={SPRING_SOFT}
            className="absolute right-0 top-full z-30 mt-2 w-full min-w-[300px] overflow-hidden rounded-2xl border border-white/[0.1] bg-zinc-900 shadow-2xl sm:w-[420px]"
          >
            {results.length === 0 ? (
              <div className="px-4 py-6 text-center text-sm text-zinc-500">
                Ничего не нашлось по «{query}»
              </div>
            ) : (
              <ul className="max-h-[360px] overflow-y-auto p-1.5">
                {results.map((entry, i) => {
                  const tab = TABS.find((t) => t.key === entry.tab);
                  const Icon = tab?.icon ?? ArrowUpRight;
                  return (
                    <li key={`${entry.title}-${entry.anchor ?? entry.to ?? entry.tab}`}>
                      <button
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => pick(entry)}
                        onMouseEnter={() => setCursor(i)}
                        className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors ${
                          i === cursor ? "bg-white/[0.07]" : ""
                        }`}
                      >
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.05] text-zinc-400">
                          <Icon size={15} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm text-zinc-100">{entry.title}</span>
                          <span className="block truncate text-xs text-zinc-500">
                            {tab ? tab.label : entry.page} · {entry.hint}
                          </span>
                        </span>
                        {entry.to ? (
                          <ArrowUpRight size={14} className="shrink-0 text-zinc-500" />
                        ) : (
                          i === cursor && <CornerDownLeft size={13} className="shrink-0 text-zinc-500" />
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function SettingsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sources, setSources] = useState<Source[]>([]);
  const [pendingAnchor, setPendingAnchor] = useState<string | null>(null);

  const tabs = TABS.filter((tab) => isAdmin || !tab.adminOnly);
  const requested = searchParams.get("tab");
  const activeTab: TabKey = tabs.find((tab) => tab.key === requested)?.key ?? "sources";

  useEffect(() => {
    void api
      .get<Source[]>("/sources")
      .then(setSources)
      .catch(() => setSources([]));
  }, []);

  const selectTab = (key: TabKey) => {
    setSearchParams(key === "sources" ? {} : { tab: key }, { replace: true });
  };

  // Карточка найденной настройки появляется после смены вкладки и загрузки её данных, поэтому
  // якорь ищется несколько раз, пока не найдётся (или пока не выйдет время).
  useEffect(() => {
    if (!pendingAnchor) return;
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (focusAnchor(pendingAnchor) || attempts > 20) {
        window.clearInterval(timer);
        setPendingAnchor(null);
      }
    }, 150);
    return () => window.clearInterval(timer);
  }, [pendingAnchor, activeTab]);

  const handlePick = (entry: SearchEntry) => {
    if (entry.to) {
      navigate(entry.to);
      return;
    }
    if (entry.tab) selectTab(entry.tab);
    if (entry.anchor) setPendingAnchor(entry.anchor);
  };

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-8 sm:px-8">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-5">
          <div>
            <h1 className="text-4xl font-bold tracking-tight text-white">Настройки</h1>
            <p className="mt-2 text-sm text-zinc-500">
              Источники закупок, отбор, доступы и вид приложения.
            </p>
          </div>
          <SettingsSearch isAdmin={isAdmin} sources={sources} onPick={handlePick} />
        </header>

        <nav
          role="tablist"
          aria-label="Разделы настроек"
          className="mb-10 flex gap-1 overflow-x-auto border-b border-white/[0.08]"
        >
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = tab.key === activeTab;
            return (
              <button
                key={tab.key}
                role="tab"
                aria-selected={isActive}
                onClick={() => selectTab(tab.key)}
                className={`relative flex shrink-0 items-center gap-2 px-3.5 py-3.5 text-sm font-medium transition-colors ${
                  isActive ? "text-white" : "text-zinc-500 hover:text-zinc-200"
                }`}
              >
                <Icon size={15} />
                {tab.label}
                {isActive && (
                  <motion.span
                    layoutId="settings-tab-underline"
                    transition={SPRING_SNAPPY}
                    className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-indigo-400"
                  />
                )}
              </button>
            );
          })}
        </nav>

        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18 }}
            className="max-w-4xl pb-16"
          >
            {activeTab === "sources" && <SourcesSection isAdmin={isAdmin} active />}
            {(activeTab === "gosplan" || activeTab === "seldon" || activeTab === "tenderplan") && (
              <FeedChannelSection feed={activeTab} isAdmin={isAdmin} active />
            )}
            {activeTab === "relevance" && <RelevanceProfileSection isAdmin={isAdmin} />}
            {activeTab === "credentials" && isAdmin && <CredentialsSection />}
            {activeTab === "regions" && isAdmin && <RegionResponsiblesSection />}
            {activeTab === "feedback" && <SpecialistFeedbackSection />}
            {activeTab === "interface" && <InterfaceSection />}
          </motion.div>
        </AnimatePresence>
      </div>
    </AppShell>
  );
}
