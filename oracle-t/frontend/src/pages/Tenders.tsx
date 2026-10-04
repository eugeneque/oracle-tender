import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowUp,
  Building2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Database,
  Download,
  Columns2,
  FilePlus2,
  Filter,
  KanbanSquare,
  Loader2,
  MoreHorizontal,
  RefreshCw,
  RotateCcw,
  Search,
  Star,
  Table as TableIcon,
  Tag as TagIcon,
  Timer,
  X,
} from "lucide-react";

import { ApiError, api, downloadFile } from "../api/client";
import type {
  BackgroundJob,
  ManualRequestOut,
  Region,
  Source,
  Tender,
  TenderBoard,
  TenderPage,
  TenderStats,
  TenderFeed,
  TenderTag,
} from "../api/types";
import {
  CATALOG_SOURCE_TYPES,
  MANUAL_SOURCE_TYPE,
  STAGE_LABELS,
  STAGE_ORDER,
  isExternalFeedSource,
  parseTenderFeed,
} from "../api/types";
import { motion } from "motion/react";

import { AppShell } from "../components/AppShell";
import { ConfidenceBar } from "../components/ConfidenceBar";
import { DecisionMark } from "../components/DecisionMark";
import { DropdownMenu } from "../components/DropdownMenu";
import { NewRequestModal } from "../components/NewRequestModal";
import { TenderDetailModal } from "../components/TenderDetailModal";
import { TagChip, TagRow } from "../components/tags/TagChip";
import { TenderSplitView } from "../components/tender-views/TenderSplitView";
import { TenderTableView } from "../components/tender-views/TenderTableView";
import { METER_KINDS } from "../utils/meterKinds";
import { SPRING_SNAPPY } from "../utils/motion";
import type {
  SortDirection,
  SortKey,
} from "../components/tender-views/TenderTableView";
import {
  daysLeft,
  formatPrice,
  percentValue,
  plural,
  scoreBadgeClass,
} from "../utils/format";

const SELECTED_SOURCES_STORAGE_KEY = "oraclet_selected_source_keys";
const FEED_STORAGE_KEY = "oraclet_tender_feed";

/** Канал сбора (28.09.2026): «Стандартные ресурсы» — площадки из «Настройки → Источники
 * тендеров», остальные — закупки, собранные через API внешнего сервиса (Госплан, с 30.09 —
 * Селдон и Тендерплан). Каналы не смешиваются, и выбор запоминается: кто сравнивает
 * каналы, возвращается к тому же. */
const FEED_OPTIONS: { value: TenderFeed; label: string; hint: string }[] = [
  {
    value: "standard",
    label: "Стандартные ресурсы",
    hint: "Закупки с площадок из «Настройки → Источники тендеров»",
  },
  { value: "gosplan", label: "Госплан", hint: "Закупки, собранные через API Госплана" },
  { value: "seldon", label: "Селдон", hint: "Закупки, собранные через Seldon.API" },
  { value: "tenderplan", label: "Тендерплан", hint: "Закупки, собранные через API Тендерплана" },
];

function loadFeed(): TenderFeed {
  try {
    return parseTenderFeed(localStorage.getItem(FEED_STORAGE_KEY)) ?? "standard";
  } catch {
    return "standard";
  }
}

function saveFeed(feed: TenderFeed): void {
  try {
    localStorage.setItem(FEED_STORAGE_KEY, feed);
  } catch {
    // не сохранили — канал всё равно переключился
  }
}

/** Колонки Kanban — этапы внутреннего пайплайна (раздел 5.6 ТЗ, решение 03.09.2026).
 *
 * Раньше доска группировала по `status` — состоянию закупки на площадке. Это отвечало на
 * вопрос «что происходит у заказчика», а не «что происходит у нас»: доска тендерного отдела
 * должна показывать, по каким закупкам заявка уже подана, а какие ждут проверки. `status`
 * при этом никуда не делся — он остался фильтром и бейджем карточки. */
const COLUMN_ACCENTS: Record<string, string> = {
  ai_selected: "bg-indigo-400",
  under_review: "bg-amber-400",
  application_submitted: "bg-sky-400",
  won: "bg-emerald-400",
  lost: "bg-zinc-500",
  rejected: "bg-red-400",
  unclassified: "bg-zinc-700",
};

const COLUMNS: { stage: string; label: string; accent: string }[] = STAGE_ORDER.map(
  (stage) => ({
    stage,
    label: STAGE_LABELS[stage],
    accent: COLUMN_ACCENTS[stage],
  }),
);

function loadSelectedSourceKeys(): string[] | null {
  try {
    const raw = localStorage.getItem(SELECTED_SOURCES_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as string[]) : null;
  } catch {
    return null;
  }
}

function saveSelectedSourceKeys(keys: string[]): void {
  try {
    localStorage.setItem(SELECTED_SOURCES_STORAGE_KEY, JSON.stringify(keys));
  } catch {
    // приватный режим браузера и т.п. — просто не сохраняем, не критично
  }
}

function TenderCard({
  tender,
  onOpen,
}: {
  tender: Tender;
  onOpen: (tender: Tender) => void;
}) {
  const left = daysLeft(tender.application_end);
  const price = formatPrice(tender.price, tender.currency);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpen(tender)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onOpen(tender);
      }}
      className="cursor-pointer rounded-xl border border-white/[0.08] bg-white/[0.04] p-4 transition-colors hover:border-white/[0.15]"
      title="Открыть карточку тендера"
    >
      <div className="mb-2.5 flex items-center gap-1.5">
        <DecisionMark decision={tender.ai_decision} size="sm" />
        <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs text-zinc-400">
          {tender.source.name}
        </span>
        <span
          className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${scoreBadgeClass(
            percentValue(tender.ai_score),
          )}`}
          title="AI-оценка по профилю (раздел 5.5.1 ТЗ)"
        >
          {percentValue(tender.ai_score) === null ? "AI —" : `AI ${percentValue(tender.ai_score)}%`}
        </span>
        {tender.okpd2_code && (
          <span className="rounded-md bg-indigo-500/10 px-2 py-0.5 text-xs text-indigo-400">
            {tender.okpd2_code}
          </span>
        )}
      </div>

      <h3 className="mb-2.5 text-sm font-medium leading-snug text-zinc-100">
        {tender.is_bookmarked && (
          <Star size={12} fill="currentColor" className="mr-1 inline -translate-y-px text-amber-400" aria-label="В избранном" />
        )}
        {tender.title}
      </h3>

      {tender.tags.length > 0 && (
        <div className="mb-2.5">
          <TagRow tags={tender.tags} max={3} />
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-zinc-500">
        {tender.customer_name && (
          <span className="flex min-w-0 items-center gap-1">
            <Building2 size={12} className="shrink-0" />
            <span className="truncate">{tender.customer_name}</span>
          </span>
        )}
        {left !== null && (
          <span
            className={`flex shrink-0 items-center gap-1 ${left <= 5 ? "text-red-400" : ""}`}
          >
            <Clock size={12} />
            {left >= 0 ? `${left} дн.` : "срок истёк"}
          </span>
        )}
      </div>

      <div className="mb-3">
        {/* Процент соответствия характеристик — деталь под главной метрикой: он отвечает на
            вопрос «подходит ли прибор», а решение об участии принимается по AI-оценке
            (раздел 5.5 ТЗ). Пока расчёт не выполнялся, ConfidenceBar покажет «нет данных». */}
        <ConfidenceBar percent={percentValue(tender.win_percentage)} />
      </div>

      {price && (
        <div className="text-sm font-semibold text-zinc-200">{price}</div>
      )}
    </div>
  );
}

function ResourcesModal({
  sources,
  initialSelected,
  onCancel,
  onSave,
}: {
  sources: Source[];
  initialSelected: Set<string>;
  onCancel: () => void;
  onSave: (keys: string[]) => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(
    new Set(initialSelected),
  );

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 px-4">
      <div className="w-full max-w-lg rounded-xl border border-white/10 bg-zinc-900 shadow-xl">
        <div className="flex items-center justify-between border-b border-white/[0.08] px-5 py-4">
          <h2 className="text-sm font-semibold text-white">
            Источники для синхронизации
          </h2>
          <button
            onClick={onCancel}
            className="text-zinc-500 hover:text-zinc-200"
          >
            <X size={18} />
          </button>
        </div>

        <div className="max-h-[60vh] overflow-y-auto px-5 py-3">
          {sources.map((source) => {
            const disabled = source.adapter_status !== "implemented";
            return (
              <label
                key={source.id}
                className={`flex items-center gap-3 border-b border-white/[0.05] py-2.5 last:border-b-0 ${
                  disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer"
                }`}
                title={
                  disabled
                    ? "Адаптер для этого источника ещё не реализован"
                    : undefined
                }
              >
                <input
                  type="checkbox"
                  checked={selected.has(source.key)}
                  disabled={disabled}
                  onChange={() => toggle(source.key)}
                  className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
                />
                <span className="text-sm text-zinc-200">{source.name}</span>
              </label>
            );
          })}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-white/[0.08] px-5 py-4">
          <button
            onClick={onCancel}
            className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 hover:bg-white/5"
          >
            Отмена
          </button>
          <button
            onClick={() => onSave(Array.from(selected))}
            className="rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3.5 py-2 text-sm font-medium text-snow hover:opacity-90"
          >
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}

interface TendersFilters {
  /** Канал сбора — переключатель «Стандартные ресурсы | Госплан» в шапке страницы. */
  feed: TenderFeed;
  search: string;
  sourceKeys: string[];
  publishFrom: string;
  publishTo: string;
  deadlineFrom: string;
  deadlineTo: string;
  priceMin: string;
  priceMax: string;
  hideExpired: boolean;
  onlyRelevant: boolean;
  onlyAiSelected: boolean;
  // Фильтры по результатам ИИ-анализа (Этапы 5-6, раздел 5.6 ТЗ). Пока анализ по тендеру не
  // выполнен, эти поля у него пустые — и он честно не попадает в такую выборку.
  regionCodes: string[];
  tenderTypes: string[];
  meterKinds: string[];
  statuses: string[];
  relevanceStatuses: string[];
  stages: string[];
  okpd2: string;
  winPercentMin: string;
  winPercentMax: string;
  aiScoreMin: string;
  aiScoreMax: string;
  /** Раздел «Избранное» (замечание тестировщика 16.09.2026): только отложенные текущим
   * пользователем закупки — и вне фильтров по умолчанию (срок подачи, профиль), иначе
   * отложенная закупка исчезала бы из раздела, как только у неё истекал срок. */
  favouritesOnly: boolean;
  /** Раздел «Минутки» (04.10.2026): срок подачи заявок истекает в день размещения. В отличие
   * от «Избранного» остальные фильтры действуют: минутка интересна, пока на неё ещё можно
   * успеть подать заявку. */
  minutesOnly: boolean;
  /** Теги (замечание 17.09.2026): закупка проходит, если у неё есть хотя бы один из них. */
  tagIds: string[];
}

// По умолчанию скрываем тендеры с истёкшим сроком подачи: собранные тендеры — это broad-поиск
// по ключевым словам, не только активные закупки, поэтому без этого фильтра список на 90%+
// состоит из уже завершённых/исторических записей и создаёт впечатление, что "все просрочены".
const DEFAULT_FILTERS: TendersFilters = {
  feed: "standard",
  search: "",
  sourceKeys: [],
  publishFrom: "",
  publishTo: "",
  deadlineFrom: "",
  deadlineTo: "",
  priceMin: "",
  priceMax: "",
  hideExpired: true,
  onlyRelevant: true,
  onlyAiSelected: false,
  favouritesOnly: false,
  minutesOnly: false,
  tagIds: [],
  regionCodes: [],
  tenderTypes: [],
  meterKinds: [],
  statuses: [],
  relevanceStatuses: [],
  stages: [],
  okpd2: "",
  winPercentMin: "",
  winPercentMax: "",
  aiScoreMin: "",
  aiScoreMax: "",
};

const TENDER_TYPE_OPTIONS = [
  { value: "supply_only", label: "Поставка ИПУ" },
  { value: "complex", label: "Комплекс" },
  { value: "works_only", label: "Работы" },
  { value: "reverification", label: "Переповерка" },
  { value: "other", label: "Прочее" },
];

// Типы приборов (файл «Параметры для ПУ»). Определяются по наименованию при сборе и по
// требованиям после «Разобрать закупку»; закупка без определённого типа в выборку не
// попадает, как и при других фильтрах по незаполненным полям.
const METER_KIND_OPTIONS = METER_KINDS.map((kind) => ({
  value: kind.value,
  label: kind.short,
  title: kind.label,
}));

const STATUS_OPTIONS = [
  { value: "collecting_bids", label: "Сбор заявок" },
  { value: "evaluation", label: "Оценка" },
  { value: "completed", label: "Завершено" },
  { value: "cancelled", label: "Отменено" },
];

const STAGE_OPTIONS = STAGE_ORDER.map((stage) => ({
  value: stage,
  label: STAGE_LABELS[stage],
}));

const RELEVANCE_OPTIONS = [
  { value: "new", label: "Не проверен" },
  { value: "confirmed", label: "Релевантный" },
  { value: "rejected", label: "Неактуальный" },
];

const PAGE_SIZE = 50;

// Сколько карточек грузим в одну колонку Kanban. Доска набирается не страницей списка, а
// по этому числу на КАЖДУЮ колонку — см. комментарий у `loadBoard`.
const BOARD_COLUMN_SIZE = 20;

/**
 * Фильтры раздела 5.6 ТЗ в query-параметры — единственное место, где они перечислены.
 *
 * Список, доска и выгрузка в Excel обязаны отбирать один и тот же набор тендеров. Раньше
 * каждый собирал параметры сам, тремя почти одинаковыми копиями: добавленный фильтр легко
 * попадал в одну и не попадал в остальные, а расхождение вылезало уже у пользователя —
 * «в таблице вижу, в отчёте нет».
 */
function buildFilterParams(filters: TendersFilters): URLSearchParams {
  const params = new URLSearchParams();
  params.set("feed", filters.feed);
  if (filters.search.trim()) params.set("search", filters.search.trim());
  for (const key of filters.sourceKeys) params.append("source", key);
  if (filters.publishFrom) params.set("publish_date_from", filters.publishFrom);
  if (filters.publishTo) params.set("publish_date_to", filters.publishTo);
  if (filters.deadlineFrom) params.set("deadline_from", filters.deadlineFrom);
  if (filters.deadlineTo) params.set("deadline_to", filters.deadlineTo);
  if (filters.priceMin) params.set("price_min", filters.priceMin);
  if (filters.priceMax) params.set("price_max", filters.priceMax);
  if (filters.favouritesOnly) params.set("bookmarked", "true");
  if (filters.minutesOnly) params.set("same_day", "true");
  for (const id of filters.tagIds) params.append("tag", id);
  if (filters.hideExpired && !filters.favouritesOnly) params.set("hide_expired", "true");
  if (filters.onlyRelevant && !filters.favouritesOnly) params.set("only_profile_relevant", "true");
  if (filters.onlyAiSelected) params.set("only_ai_selected", "true");
  for (const code of filters.regionCodes) params.append("region", code);
  for (const value of filters.tenderTypes) params.append("tender_type", value);
  for (const value of filters.meterKinds) params.append("meter_kind", value);
  for (const value of filters.statuses) params.append("tender_status", value);
  for (const value of filters.relevanceStatuses)
    params.append("relevance_status", value);
  for (const value of filters.stages) params.append("stage", value);
  if (filters.okpd2.trim()) params.set("okpd2", filters.okpd2.trim());
  if (filters.winPercentMin)
    params.set("win_percentage_min", filters.winPercentMin);
  if (filters.winPercentMax)
    params.set("win_percentage_max", filters.winPercentMax);
  if (filters.aiScoreMin) params.set("ai_score_min", filters.aiScoreMin);
  if (filters.aiScoreMax) params.set("ai_score_max", filters.aiScoreMax);
  return params;
}

function buildTendersQuery(
  filters: TendersFilters,
  sort: { key: SortKey; direction: SortDirection },
  offset: number,
): string {
  const params = buildFilterParams(filters);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(offset));
  params.set("sort_by", sort.key);
  params.set("sort_dir", sort.direction);
  return params.toString();
}

/**
 * Параметры доски Kanban: те же фильтры и та же сортировка, что в списке, но вместо
 * пагинации — размер одной колонки. Общий с выгрузкой сборщик фильтров: разойдись они,
 * пользователь увидел бы на доске не тот набор тендеров, что в таблице.
 */
function buildBoardQuery(
  filters: TendersFilters,
  sort: { key: SortKey; direction: SortDirection },
): string {
  const params = new URLSearchParams(buildFilterParams(filters));
  params.set("per_column", String(BOARD_COLUMN_SIZE));
  params.set("sort_by", sort.key);
  params.set("sort_dir", sort.direction);
  return params.toString();
}

/**
 * Параметры выгрузки в Excel: те же фильтры, что и в списке, но без пагинации и сортировки.
 *
 * Отдельная функция, а не `buildTendersQuery` с вырезанными полями: выгрузка обязана
 * отдавать ВСЕ отобранные строки, а не текущую страницу из пятидесяти, — и случайно
 * унаследованный `offset` превратил бы отчёт в обрезок выдачи.
 */
function buildExportQuery(filters: TendersFilters): string {
  return buildFilterParams(filters).toString();
}

/** Группа переключателей «выбрано / не выбрано» — компактнее мультиселекта и не прячет
 * выбранное за схлопнутым списком. */
function ChipGroup({
  label,
  options,
  selected,
  onToggle,
}: {
  label: string;
  options: { value: string; label: string; title?: string }[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="mr-1 text-xs text-zinc-500">{label}:</span>
      {options.map((option) => {
        const active = selected.includes(option.value);
        return (
          <button
            key={option.value}
            onClick={() => onToggle(option.value)}
            title={option.title}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
              active
                ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                : "border-white/10 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

function dateInputClass(): string {
  return "w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-indigo-500";
}

/**
 * Панель фильтров (раздел 5.6 ТЗ).
 *
 * Разделена на две части сознательно. Сверху — то, чем пользуются каждый день: поиск, вилка
 * суммы и срок подачи; они видны всегда, без лишнего клика. Всё остальное — ОКПД2, регион,
 * AI-оценка, площадки, этапы — раскрывается кнопкой «Фильтры»: раньше эти четыре ряда чипов
 * занимали пол-экрана и отодвигали сам список тендеров за сгиб, хотя трогают их редко.
 */
function FiltersPanel({
  filters,
  sources,
  regions,
  tags,
  onChange,
  onReset,
}: {
  filters: TendersFilters;
  sources: Source[];
  regions: Region[];
  tags: TenderTag[];
  onChange: (patch: Partial<TendersFilters>) => void;
  onReset: () => void;
}) {
  const toggleSource = (key: string) => {
    const has = filters.sourceKeys.includes(key);
    onChange({
      sourceKeys: has
        ? filters.sourceKeys.filter((k) => k !== key)
        : [...filters.sourceKeys, key],
    });
  };

  const toggleIn = (list: string[], value: string) =>
    list.includes(value)
      ? list.filter((item) => item !== value)
      : [...list, value];

  return (
    <div className="mb-3 rounded-xl border border-white/[0.08] bg-white/[0.03] p-4">
      {/* Раздел и теги — первым рядом: это не сужение выборки, а выбор, на что смотреть.
          «Избранное» живёт здесь, а не отдельной кнопкой в шапке (замечание 17.09.2026):
          у шапки и так не хватало места, а раздел — такой же фильтр, как и остальные. */}
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-white/[0.06] pb-4">
        <div className="flex items-center gap-1.5">
          <span className="mr-1 text-xs text-zinc-500">Раздел:</span>
          <button
            onClick={() => onChange({ favouritesOnly: false, minutesOnly: false })}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
              !filters.favouritesOnly && !filters.minutesOnly
                ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                : "border-white/10 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            Все закупки
          </button>
          <button
            onClick={() => onChange({ favouritesOnly: true, minutesOnly: false })}
            aria-pressed={filters.favouritesOnly}
            title="Отложенные закупки: то, что отмечено звёздочкой в карточке. Показываются независимо от срока подачи и профиля."
            className={`flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors ${
              filters.favouritesOnly
                ? "border-amber-400/40 bg-amber-500/10 text-amber-300"
                : "border-white/10 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            <Star size={11} fill={filters.favouritesOnly ? "currentColor" : "none"} />
            Избранное
          </button>
          <button
            onClick={() => onChange({ minutesOnly: true, favouritesOnly: false })}
            aria-pressed={filters.minutesOnly}
            title="Тендеры-минутки: срок подачи заявок истекает в день размещения."
            className={`flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors ${
              filters.minutesOnly
                ? "border-rose-400/40 bg-rose-500/10 text-rose-300"
                : "border-white/10 text-zinc-500 hover:text-zinc-300"
            }`}
          >
            <Timer size={11} />
            Минутки
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="mr-1 flex items-center gap-1 text-xs text-zinc-500">
            <TagIcon size={12} />
            Теги:
          </span>
          {tags.length === 0 ? (
            <span className="text-xs text-zinc-600">
              пока нет — заведите в карточке закупки кнопкой «Добавить тег»
            </span>
          ) : (
            tags.map((tag) => (
              <TagChip
                key={tag.id}
                tag={tag}
                active={filters.tagIds.includes(tag.id)}
                onClick={() => onChange({ tagIds: toggleIn(filters.tagIds, tag.id) })}
                title={
                  filters.tagIds.includes(tag.id)
                    ? `${tag.name} — снять фильтр`
                    : `Показать закупки с тегом «${tag.name}»`
                }
              />
            ))
          )}
        </div>
      </div>

      {/* Поиск живёт в строке над панелью и здесь не дублируется: два поля с одним и тем же
          значением на одном экране читаются как два разных фильтра. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Сумма: от</label>
          <input
            type="number"
            min={0}
            value={filters.priceMin}
            onChange={(e) => onChange({ priceMin: e.target.value })}
            placeholder="0"
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Сумма: до</label>
          <input
            type="number"
            min={0}
            value={filters.priceMax}
            onChange={(e) => onChange({ priceMax: e.target.value })}
            placeholder="без ограничения"
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            Срок подачи: от
          </label>
          <input
            type="date"
            value={filters.deadlineFrom}
            onChange={(e) => onChange({ deadlineFrom: e.target.value })}
            className={dateInputClass()}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            Срок подачи: до
          </label>
          <input
            type="date"
            value={filters.deadlineTo}
            onChange={(e) => onChange({ deadlineTo: e.target.value })}
            className={dateInputClass()}
          />
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            Размещён: от
          </label>
          <input
            type="date"
            value={filters.publishFrom}
            onChange={(e) => onChange({ publishFrom: e.target.value })}
            className={dateInputClass()}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            Размещён: до
          </label>
          <input
            type="date"
            value={filters.publishTo}
            onChange={(e) => onChange({ publishTo: e.target.value })}
            className={dateInputClass()}
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">Код ОКПД2</label>
          <input
            value={filters.okpd2}
            onChange={(e) => onChange({ okpd2: e.target.value })}
            placeholder="26.51 — включая вложенные"
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            AI-оценка: от
          </label>
          <input
            type="number"
            min={0}
            max={100}
            value={filters.aiScoreMin}
            onChange={(e) => onChange({ aiScoreMin: e.target.value })}
            placeholder="0"
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            AI-оценка: до
          </label>
          <input
            type="number"
            min={0}
            max={100}
            value={filters.aiScoreMax}
            onChange={(e) => onChange({ aiScoreMax: e.target.value })}
            placeholder="100"
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            % соответствия: от
          </label>
          <input
            type="number"
            min={0}
            max={100}
            value={filters.winPercentMin}
            onChange={(e) => onChange({ winPercentMin: e.target.value })}
            placeholder="0"
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            % соответствия: до
          </label>
          <input
            type="number"
            min={0}
            max={100}
            value={filters.winPercentMax}
            onChange={(e) => onChange({ winPercentMax: e.target.value })}
            placeholder="100"
            className="w-full rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-zinc-500">
            Регион (заказчик или поставка)
          </label>
          <select
            value={filters.regionCodes[0] ?? ""}
            onChange={(e) =>
              onChange({ regionCodes: e.target.value ? [e.target.value] : [] })
            }
            className="w-full rounded-lg border border-white/10 bg-zinc-900 px-3 py-2 text-sm text-white outline-none focus:border-indigo-500"
          >
            <option value="">Любой регион</option>
            {regions.map((region) => (
              <option key={region.code} value={region.code}>
                {region.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="mt-4 space-y-2.5">
        <ChipGroup
          label="Тип конкурса"
          options={TENDER_TYPE_OPTIONS}
          selected={filters.tenderTypes}
          onToggle={(value) =>
            onChange({ tenderTypes: toggleIn(filters.tenderTypes, value) })
          }
        />
        <ChipGroup
          label="Тип прибора учёта"
          options={METER_KIND_OPTIONS}
          selected={filters.meterKinds}
          onToggle={(value) =>
            onChange({ meterKinds: toggleIn(filters.meterKinds, value) })
          }
        />
        <ChipGroup
          label="Статус"
          options={STATUS_OPTIONS}
          selected={filters.statuses}
          onToggle={(value) =>
            onChange({ statuses: toggleIn(filters.statuses, value) })
          }
        />
        <ChipGroup
          label="Этап"
          options={STAGE_OPTIONS}
          selected={filters.stages}
          onToggle={(value) => onChange({ stages: toggleIn(filters.stages, value) })}
        />
        <ChipGroup
          label="Релевантность"
          options={RELEVANCE_OPTIONS}
          selected={filters.relevanceStatuses}
          onToggle={(value) =>
            onChange({
              relevanceStatuses: toggleIn(filters.relevanceStatuses, value),
            })
          }
        />
      </div>

      {sources.length > 0 && (
      <div className="mt-4 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-xs text-zinc-500">Площадки:</span>
        {sources.map((source) => {
          const active = filters.sourceKeys.includes(source.key);
          return (
            <button
              key={source.key}
              onClick={() => toggleSource(source.key)}
              className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                active
                  ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                  : "border-white/10 text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {source.name}
            </button>
          );
        })}
      </div>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <label className="flex items-center gap-2 text-sm text-zinc-300">
            <input
              type="checkbox"
              checked={filters.hideExpired}
              onChange={(e) => onChange({ hideExpired: e.target.checked })}
              className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
            />
            Скрывать закрытые: истёкший срок подачи, завершённые и отменённые
          </label>
          <label
            className="flex items-center gap-2 text-sm text-zinc-300"
            title="Модель проверяет каждую отобранную по словам закупку и отвечает, действительно ли она профильная. Непроверенные при включённой галочке не показываются."
          >
            <input
              type="checkbox"
              checked={filters.onlyAiSelected}
              onChange={(e) => onChange({ onlyAiSelected: e.target.checked })}
              className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
            />
            Только подобранные ИИ
          </label>
          <label
            className="flex items-center gap-2 text-sm text-zinc-300"
            title="Профиль релевантности из настроек: девять групп потребностей с исключениями, поверх него — ответ модели «наша ли это тематика». Скрыто то, что отсеял профиль или отклонила модель; непроверенное моделью остаётся. Снимите галочку, чтобы увидеть отсеянное — профиль мог оказаться слишком узким."
          >
            <input
              type="checkbox"
              checked={filters.onlyRelevant}
              onChange={(e) => onChange({ onlyRelevant: e.target.checked })}
              className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
            />
            Только прошедшие профиль релевантности
          </label>
        </div>
        <button
          onClick={onReset}
          className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-300"
        >
          <RotateCcw size={12} />
          Сбросить фильтры
        </button>
      </div>
    </div>
  );
}

/**
 * Пагинация (раздел 5.6 ТЗ). Показывает не только номера страниц, но и «N–M из K»:
 * пользователю важно знать, сколько всего нашлось по фильтрам, — это единственное место,
 * где видно, что выборка большая, а не «вот эти пятьдесят».
 */
function Pagination({
  total,
  offset,
  shown,
  onChange,
}: {
  total: number;
  offset: number;
  shown: number;
  onChange: (offset: number) => void;
}) {
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : offset + 1;
  const to = offset + shown;
  // Одна страница — листать нечего, строка только отнимала бы высоту.
  if (pages <= 1) return null;

  const arrow =
    "flex h-7 w-7 items-center justify-center rounded-md text-zinc-400 transition-colors hover:bg-white/5 hover:text-zinc-200 disabled:pointer-events-none disabled:opacity-30";
  return (
    <div className="flex shrink-0 items-center justify-between gap-3 px-2 pt-2 text-xs text-zinc-500">
      <span className="tabular-nums">
        {from}–{to} из {total.toLocaleString("ru-RU")}
      </span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}
          disabled={offset === 0}
          title="Предыдущая страница"
          className={arrow}
        >
          <ChevronLeft size={15} />
        </button>
        <span className="tabular-nums">
          {page} / {pages}
        </span>
        <button
          onClick={() => onChange(offset + PAGE_SIZE)}
          disabled={page >= pages}
          title="Следующая страница"
          className={arrow}
        >
          <ChevronRight size={15} />
        </button>
      </div>
    </div>
  );
}

// Три вида, а не пять (решение 04.09.2026: «Список» и «Таймплан» убраны как лишние).
// «Список» дублировал левую колонку двухпанельного режима, только без карточки справа, а
// «Таймплан» показывал те же сроки, что видно в таблице. Лишний выбор в переключателе
// стоит внимания на каждом заходе и ничего не даёт взамен.
/** Сколько фильтров отличаются от значений по умолчанию (без учёта строки поиска). */
function countActiveFilters(filters: TendersFilters): number {
  let count = 0;
  const simpleKeys: Array<keyof TendersFilters> = [
    "priceMin",
    "priceMax",
    "deadlineFrom",
    "deadlineTo",
    "publishFrom",
    "publishTo",
    "okpd2",
    "aiScoreMin",
    "aiScoreMax",
    "winPercentMin",
    "winPercentMax",
  ];
  for (const key of simpleKeys) {
    const value = filters[key];
    if (typeof value === "string" && value.trim() !== "") count += 1;
  }
  const listKeys: Array<keyof TendersFilters> = [
    "sourceKeys",
    "tenderTypes",
    "meterKinds",
    "statuses",
    "stages",
    "relevanceStatuses",
    "regionCodes",
  ];
  for (const key of listKeys) {
    const value = filters[key];
    if (Array.isArray(value) && value.length > 0) count += 1;
  }
  // Галочки считаем, только когда они отличаются от умолчания: включённые по умолчанию
  // «скрывать закрытые» и «только релевантные» не должны выглядеть как ручная настройка.
  if (!filters.hideExpired) count += 1;
  if (!filters.onlyRelevant) count += 1;
  if (filters.onlyAiSelected) count += 1;
  if (filters.favouritesOnly) count += 1;
  if (filters.minutesOnly) count += 1;
  if (filters.tagIds.length > 0) count += 1;
  return count;
}

const VIEW_TABS = [
  { key: "split", label: "Две панели", icon: Columns2 },
  { key: "kanban", label: "Kanban", icon: KanbanSquare },
  { key: "table", label: "Таблица", icon: TableIcon },
] as const;

type ViewKey = (typeof VIEW_TABS)[number]["key"];

const ACTIVE_VIEW_STORAGE_KEY = "oraclet_tenders_view";

/** Выбранный вид переживает перезагрузку страницы: у каждого пользователя свой рабочий
 * режим (кто-то живёт в таблице, кто-то на доске), и сбрасывать его при каждом заходе —
 * лишнее раздражение.
 *
 * Умолчание — двухпанельный режим (раздел 5.6 ТЗ). Проверка `VIEW_TABS.some(...)` здесь не
 * формальность: у тех, кто раньше выбрал «Список» или «Таймплан», в хранилище остался ключ
 * удалённого вида, и без неё страница показала бы пустое место вместо таблицы. */
function loadActiveView(): ViewKey {
  try {
    const raw = localStorage.getItem(ACTIVE_VIEW_STORAGE_KEY);
    if (raw && VIEW_TABS.some((tab) => tab.key === raw)) return raw as ViewKey;
  } catch {
    // приватный режим браузера — используем значение по умолчанию
  }
  return "split";
}

export function TendersPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeView, setActiveView] = useState<ViewKey>(loadActiveView);
  // Справочник тегов — для чипов в фильтрах. Перечитывается при открытии панели фильтров:
  // тег, заведённый в карточке минуту назад, должен быть виден без перезагрузки.
  const [tags, setTags] = useState<TenderTag[]>([]);
  const [tenders, setTenders] = useState<Tender[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  // Синхронизация — фоновая задача сервера (баг 17.09.2026: «бесконечная синхронизация»).
  // Раньше запрос держался открытым на весь опрос — 20–25 минут на девять площадок — и
  // любой перезапуск сервера или таймаут прокси оставлял оверлей крутиться вечно. Теперь
  // здесь лежит состояние задачи, страница опрашивает его раз в две секунды, а при входе
  // подхватывает опрос, запущенный до перезагрузки вкладки.
  const [syncJob, setSyncJob] = useState<BackgroundJob | null>(null);
  const isSyncing = syncJob?.status === "queued" || syncJob?.status === "running";
  const [isExporting, setIsExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [isResourcesOpen, setIsResourcesOpen] = useState(false);
  const [isNewRequestOpen, setIsNewRequestOpen] = useState(false);
  // Свёрнуто по умолчанию: базовые поля видны и так, а редкие фильтры отодвигали список
  // тендеров за сгиб экрана.
  const [isFiltersOpen, setIsFiltersOpen] = useState(false);
  const [selectedSourceKeys, setSelectedSourceKeys] = useState<string[] | null>(
    null,
  );
  const [filters, setFilters] = useState<TendersFilters>(() => ({
    ...DEFAULT_FILTERS,
    feed: parseTenderFeed(searchParams.get("feed")) ?? loadFeed(),
  }));
  const feed = filters.feed;
  // Ссылка «Все →» из меню учётной записи ведёт сразу в раздел «Избранное». Эффектом, а не
  // начальным состоянием: со страницы тендеров та же ссылка не перемонтирует компонент, и
  // параметр должен сработать и тогда. Он тут же снимается с адреса, чтобы не залипал в
  // закладках и при обновлении страницы не возвращал раздел насильно.
  useEffect(() => {
    if (!searchParams.has("favourites")) return;
    setOffset(0);
    setFilters((prev) => ({ ...prev, favouritesOnly: true, minutesOnly: false }));
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);
  // Раздел «Минутки» живёт в адресе (`?minutes=1`), в отличие от «Избранного»: у него свой
  // пункт меню, и тот должен подсвечиваться, пока раздел открыт. Поэтому адрес — источник
  // правды: переход по пункту меню включает раздел, переход на «Тендеры» без параметра —
  // выключает, а кнопки на странице меняют адрес через `updateFilters`.
  const minutesInUrl = searchParams.has("minutes");
  useEffect(() => {
    if (filters.minutesOnly === minutesInUrl) return;
    setOffset(0);
    setFilters((prev) => ({
      ...prev,
      minutesOnly: minutesInUrl,
      favouritesOnly: minutesInUrl ? false : prev.favouritesOnly,
    }));
  }, [minutesInUrl, filters.minutesOnly]);
  // Ссылка «Тендеры Госплана» (Селдона, Тендерплана) из настроек (`?feed=gosplan`): канал
  // уже выбран начальным состоянием, параметр только запоминается и снимается с адреса.
  useEffect(() => {
    const requested = parseTenderFeed(searchParams.get("feed"));
    if (requested === null) return;
    setOffset(0);
    setFilters((prev) => ({ ...prev, feed: requested, sourceKeys: [] }));
    saveFeed(requested);
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);
  /** Сколько фильтров сейчас сужают выдачу — цифра на кнопке «Фильтры».
   *
   * Нужна именно потому, что панель свёрнута: спрятанный фильтр иначе становится невидимой
   * причиной пустого списка, и человек ищет ошибку в данных, а не в своих настройках. Поиск
   * не считается — он на виду в той же строке. */
  const activeFilterCount = countActiveFilters(filters);
  const [openTender, setOpenTender] = useState<Tender | null>(null);
  // Выбранная закупка двухпанельного режима — отдельно от `openTender`: там карточка
  // открывается модалкой поверх списка, здесь живёт в правой панели постоянно.
  const [selectedTender, setSelectedTender] = useState<Tender | null>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  // Сколько закупок собрано всего — знаменатель для строки «показано N из M»: список по
  // умолчанию отфильтрован (открытые, прошедшие профиль), и без знаменателя это не видно.
  const [collectedTotal, setCollectedTotal] = useState<number | null>(null);
  const [total, setTotal] = useState(0);
  const [board, setBoard] = useState<TenderBoard | null>(null);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<{ key: SortKey; direction: SortDirection }>({
    key: "application_end",
    direction: "asc",
  });

  const loadTenders = useCallback(
    async (
      activeFilters: TendersFilters,
      activeSort: { key: SortKey; direction: SortDirection },
      activeOffset: number,
    ) => {
      try {
        const query = buildTendersQuery(
          activeFilters,
          activeSort,
          activeOffset,
        );
        const page = await api.get<TenderPage>(`/tenders?${query}`);
        setTenders(page.items);
        setTotal(page.total);
        setError(null);
      } catch (err) {
        setError(
          err instanceof ApiError
            ? err.message
            : "Не удалось загрузить тендеры",
        );
      }
    },
    [],
  );

  /**
   * Загрузка доски Kanban — отдельным запросом, а не из страницы списка.
   *
   * Страница отдаёт сквозной срез в 50 записей по одной сортировке, и колонки доставались
   * ей как придётся: при сортировке по сроку подачи по возрастанию первые полсотни — это
   * закупки 2010-2015 годов со статусом «Завершено», и доска показывала одну заполненную
   * колонку из пяти. Снятие галочки «скрывать закрытые» при этом ОПУСТОШАЛО «Сбор заявок»
   * вместо того, чтобы что-то добавить. Сервер набирает каждую колонку своей выборкой.
   */
  const loadBoard = useCallback(
    async (
      activeFilters: TendersFilters,
      activeSort: { key: SortKey; direction: SortDirection },
    ) => {
      try {
        const query = buildBoardQuery(activeFilters, activeSort);
        setBoard(await api.get<TenderBoard>(`/tenders/board?${query}`));
        setError(null);
      } catch (err) {
        setError(
          err instanceof ApiError ? err.message : "Не удалось загрузить доску",
        );
      }
    },
    [],
  );

  useEffect(() => {
    // Только площадки закупок: `/sources` отдаёт вместе с ними источники справочника
    // продукции (ФГИС, сайты производителей), а фильтр «Площадки» и модалка «Ресурсы»
    // описывают, откуда берутся тендеры. Опрос их и так пропускает (`poll_sources`), но в
    // списке они выглядели как площадки, по которым можно фильтровать закупки.
    void api
      .get<Source[]>("/sources")
      .then((all) => setSources(all.filter((s) => !CATALOG_SOURCE_TYPES.includes(s.type))));
    void api.get<Region[]>("/dictionaries/regions").then(setRegions);
    void api.get<TenderTag[]>("/tags").then(setTags).catch(() => setTags([]));
  }, []);

  // «Из N собранных» — по текущему каналу: у Госплана и других сервисов своё число, и общий знаменатель
  // выдавал бы строку «12 из 17 800» там, где в канале всего три сотни закупок.
  const loadCollectedTotal = useCallback(
    (activeFeed: TenderFeed) =>
      api
        .get<TenderStats>(`/tenders/stats?feed=${activeFeed}`)
        .then((stats) => setCollectedTotal(stats.total))
        .catch(() => setCollectedTotal(null)),
    [],
  );
  useEffect(() => {
    setCollectedTotal(null);
    void loadCollectedTotal(feed);
  }, [feed, loadCollectedTotal]);

  // Площадки стандартного канала: Госплан, Селдон и Тендерплан — отдельные каналы со своей
  // кнопкой сбора, в фильтре «Площадки» и в «Площадках для синхронизации» им не место.
  const standardSources = sources.filter((s) => !isExternalFeedSource(s.type));
  const feedLabel = FEED_OPTIONS.find((option) => option.value === feed)?.label ?? "";

  const changeFeed = (next: TenderFeed) => {
    if (next === feed) return;
    saveFeed(next);
    setOffset(0);
    setSelectedTender(null);
    setFilters((prev) => ({ ...prev, feed: next, sourceKeys: [] }));
  };

  // Перезагрузка при любом изменении фильтров, сортировки или страницы, с небольшим
  // дебаунсом — чтобы ввод в поле поиска не бил по API на каждое нажатие клавиши.
  // Kanban ходит за доской, остальные виды — за страницей списка: у доски своя выборка на
  // каждую колонку, и грузить ради неё страницу списка незачем.
  useEffect(() => {
    setIsLoading(true);
    const timer = setTimeout(() => {
      const request =
        activeView === "kanban"
          ? loadBoard(filters, sort)
          : loadTenders(filters, sort, offset);
      void request.finally(() => setIsLoading(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [filters, sort, offset, activeView, loadTenders, loadBoard]);

  // Смена фильтров или сортировки возвращает на первую страницу: иначе после сужения
  // выборки пользователь оказывается на пустой пятой странице и решает, что ничего не нашлось.
  const updateFilters = (patch: Partial<TendersFilters>) => {
    setOffset(0);
    if (patch.minutesOnly !== undefined && patch.minutesOnly !== minutesInUrl) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (patch.minutesOnly) next.set("minutes", "1");
          else next.delete("minutes");
          return next;
        },
        { replace: true },
      );
    }
    setFilters((prev) => ({ ...prev, ...patch }));
  };

  const changeSort = (key: SortKey, direction: SortDirection) => {
    setOffset(0);
    setSort({ key, direction });
  };

  /** Правка в карточке (релевантность, классификация) должна быть видна в списке сразу —
   * без перезапроса всей страницы. */
  const replaceTender = useCallback((updated: Tender) => {
    setTenders((prev) =>
      prev.map((item) => (item.id === updated.id ? updated : item)),
    );
    // В карточке могли завести новый тег — справочник для фильтров должен его знать.
    setTags((prev) => {
      const known = new Set(prev.map((tag) => tag.id));
      const fresh = updated.tags.filter((tag) => !known.has(tag.id));
      return fresh.length === 0 ? prev : [...prev, ...fresh].sort((a, b) => a.name.localeCompare(b.name, "ru"));
    });
    setOpenTender((current) =>
      current && current.id === updated.id ? updated : current,
    );
    setSelectedTender((current) =>
      current && current.id === updated.id ? updated : current,
    );
  }, []);

  // В двухпанельном режиме правая панель не должна встречать пустотой: как только выдача
  // загрузилась, показываем первую закупку. Выбор пользователя при этом не сбрасывается —
  // только если выбранная закупка выпала из текущей выборки.
  useEffect(() => {
    if (activeView !== "split") return;
    if (tenders.length === 0) {
      setSelectedTender(null);
      return;
    }
    setSelectedTender((current) =>
      current && tenders.some((item) => item.id === current.id) ? current : tenders[0],
    );
  }, [activeView, tenders]);

  const changeView = (key: ViewKey) => {
    setActiveView(key);
    try {
      localStorage.setItem(ACTIVE_VIEW_STORAGE_KEY, key);
    } catch {
      // не сохранили — не критично, вид всё равно переключился
    }
  };

  useEffect(() => {
    if (sources.length === 0) return;
    const saved = loadSelectedSourceKeys();
    if (saved) {
      setSelectedSourceKeys(saved.filter((key) => !isExternalFeedSource(key)));
      return;
    }
    const defaultKeys = sources
      .filter((s) => s.adapter_status === "implemented" && !isExternalFeedSource(s.type))
      .map((s) => s.key);
    setSelectedSourceKeys(defaultKeys);
    saveSelectedSourceKeys(defaultKeys);
  }, [sources]);

  // Колонки приходят с сервера уже разложенными по этапам — группировать нечего.
  const boardColumns = board?.columns ?? {};
  const boardCounts = board?.counts ?? {};
  // Число закупок в строке состояния: у доски нет страницы списка и `total` не приходит —
  // считаем по счётчикам колонок, иначе на Kanban стояло бы «0 закупок».
  const shownTotal =
    activeView === "kanban"
      ? Object.values(boardCounts).reduce((sum, count) => sum + count, 0)
      : total;

  const syncSources = async (keys: string[]) => {
    if (keys.length === 0) return;
    setError(null);
    setNotice(null);
    try {
      setSyncJob(await api.post<BackgroundJob>("/sources/poll", { source_keys: keys }));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось запустить синхронизацию",
      );
    }
  };

  // При входе на страницу — подхватить идущий опрос (запущен до перезагрузки вкладки или
  // другим пользователем). Завершённый в прошлом не показываем: его итог уже видели.
  useEffect(() => {
    void api
      .get<BackgroundJob | null>("/sources/poll/current")
      .then((job) => {
        if (job && (job.status === "queued" || job.status === "running")) setSyncJob(job);
      })
      .catch(() => undefined);
  }, []);

  // Опрос состояния задачи, пока она идёт. Площадки сохраняются порциями по 100 записей
  // (правка 17.09.2026), и после каждой порции список перечитывается тихо — без «Загрузка…»
  // и без сброса выбранной закупки, — но не чаще раза в десять секунд: на большой выдаче
  // порции идут каждые пару секунд. По завершении — итог и последнее перечитывание.
  useEffect(() => {
    if (!isSyncing) return;
    let lastMessage = syncJob?.message ?? null;
    let lastRefreshAt = 0;
    const timer = setInterval(() => {
      void api
        .get<BackgroundJob | null>("/sources/poll/current")
        .then((job) => {
          if (!job) return;
          setSyncJob(job);
          const progressed = job.message !== lastMessage;
          lastMessage = job.message;
          if (
            progressed &&
            job.status === "running" &&
            Date.now() - lastRefreshAt > 10_000
          ) {
            lastRefreshAt = Date.now();
            void (activeView === "kanban"
              ? loadBoard(filters, sort)
              : loadTenders(filters, sort, offset));
          }
          if (job.status === "success") {
            setNotice(job.message ?? "Синхронизация завершена");
            void (activeView === "kanban"
              ? loadBoard(filters, sort)
              : loadTenders(filters, sort, offset));
            void loadCollectedTotal(feed);
          } else if (job.status === "error") {
            setError(`Синхронизация прервана: ${job.message ?? "неизвестная ошибка"}`);
          }
        })
        .catch(() => undefined);
    }, 2_000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `syncJob.message` нужен только как стартовое значение
  }, [isSyncing, activeView, filters, sort, offset, loadBoard, loadTenders, loadCollectedTotal, feed]);

  /** Выгрузка в Excel (раздел 5.7 ТЗ) — по текущим фильтрам списка, а не по всей базе:
   * пользователь только что отобрал нужное, и отчёт должен повторять именно этот срез. */
  const exportXlsx = async () => {
    setIsExporting(true);
    setError(null);
    setNotice(null);
    try {
      const query = buildExportQuery(filters);
      const { fileName, rows } = await downloadFile(
        `/export/tenders.xlsx${query ? `?${query}` : ""}`,
        "sast-tenders.xlsx",
      );
      setNotice(
        rows === null
          ? `Файл ${fileName} выгружен`
          : `Выгружено ${new Intl.NumberFormat("ru-RU").format(rows)} строк — файл ${fileName}`,
      );
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось сформировать выгрузку",
      );
    } finally {
      setIsExporting(false);
    }
  };

  /** Заявка создана: закрываем форму, перечитываем список (она должна появиться в нём и на
   * доске) и сразу открываем карточку — там виден ход анализа и извлечённые требования. */
  const handleRequestCreated = (result: ManualRequestOut) => {
    setIsNewRequestOpen(false);
    setNotice(
      result.job
        ? `Заявка ${result.tender.external_id} создана, ИИ-анализ документов запущен`
        : `Заявка ${result.tender.external_id} создана`,
    );
    void (activeView === "kanban"
      ? loadBoard(filters, sort)
      : loadTenders(filters, sort, offset));
    if (activeView === "split") setSelectedTender(result.tender);
    else setOpenTender(result.tender);
  };

  const handleSaveResources = (keys: string[]) => {
    setSelectedSourceKeys(keys);
    saveSelectedSourceKeys(keys);
    setIsResourcesOpen(false);
    void syncSources(keys);
  };

  return (
    <AppShell>
      {/* Колонка на всю высоту: шапка, фильтры и переключатель видов зафиксированы, а
          прокручивается только содержимое. Раньше скроллилась вся страница, и в
          двухпанельном режиме приходилось сначала пролистать её, и только потом — карточку
          закупки. `min-h-0` обязателен: без него flex-потомок не сжимается и внутренний
          скролл не включается. */}
      <div className="flex h-full flex-col px-6 pb-4 pt-4">
        {/* Шапка страницы (переделана 28.09.2026): заголовок, поиск, виды, фильтры и
            синхронизация — одной строкой. Раньше над списком стояли три яруса — хлебные
            крошки с заголовком и четырьмя кнопками, строка поиска, строка состояния в рамке —
            и забирали у карточки закупки почти двести пикселей. Редкие действия (ручная
            заявка, выбор площадок, выгрузка) переехали в меню «⋯». */}
        <div className="mb-2 flex shrink-0 flex-wrap items-center gap-2">
          <h1 className="mr-1 text-xl font-semibold tracking-tight text-white">Тендеры</h1>

          <div
            role="radiogroup"
            aria-label="Источник закупок"
            className="inline-flex h-9 items-center gap-0.5 rounded-lg border border-white/[0.08] bg-white/[0.03] p-0.5"
          >
            {FEED_OPTIONS.map((option) => {
              const isActive = option.value === feed;
              return (
                <button
                  key={option.value}
                  role="radio"
                  aria-checked={isActive}
                  onClick={() => changeFeed(option.value)}
                  title={option.hint}
                  className="relative flex h-7 items-center rounded-md px-3 text-xs font-medium"
                >
                  {isActive && (
                    <motion.span
                      layoutId="tender-feed-selected"
                      transition={SPRING_SNAPPY}
                      className="absolute inset-0 rounded-md bg-white/10"
                    />
                  )}
                  <span
                    className={`relative transition-colors ${
                      isActive ? "text-white" : "text-zinc-500 hover:text-zinc-200"
                    }`}
                  >
                    {option.label}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="relative min-w-[220px] flex-1">
            <Search
              size={14}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-600"
            />
            <input
              value={filters.search}
              onChange={(e) => updateFilters({ search: e.target.value })}
              placeholder="Поиск по названию или заказчику"
              className="h-9 w-full rounded-lg border border-white/[0.08] bg-white/[0.03] pl-9 pr-8 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
            />
            {filters.search && (
              <button
                onClick={() => updateFilters({ search: "" })}
                title="Очистить поиск"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-zinc-500 hover:text-zinc-200"
              >
                <X size={14} />
              </button>
            )}
          </div>

          {/* Виды — иконками с подсказкой: подписи повторялись на каждом заходе, а выбирают
              вид раз и надолго (он запоминается). */}
          <div className="inline-flex h-9 items-center gap-0.5 rounded-lg border border-white/[0.08] bg-white/[0.03] p-0.5">
            {VIEW_TABS.map((tab) => {
              const Icon = tab.icon;
              const isActive = tab.key === activeView;
              return (
                <button
                  key={tab.key}
                  onClick={() => changeView(tab.key)}
                  title={tab.label}
                  aria-label={tab.label}
                  aria-pressed={isActive}
                  className={`flex h-7 w-8 items-center justify-center rounded-md transition-colors ${
                    isActive ? "bg-white/10 text-white" : "text-zinc-500 hover:text-zinc-200"
                  }`}
                >
                  <Icon size={15} />
                </button>
              );
            })}
          </div>

          <DateSortControl sort={sort} onChange={changeSort} />

          <button
            onClick={() => {
              setIsFiltersOpen((v) => !v);
              if (!isFiltersOpen) void api.get<TenderTag[]>("/tags").then(setTags).catch(() => undefined);
            }}
            className={`flex h-9 items-center gap-1.5 rounded-lg border px-3 text-sm transition-colors ${
              isFiltersOpen || activeFilterCount > 0
                ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                : "border-white/[0.08] bg-white/[0.03] text-zinc-300 hover:bg-white/5"
            }`}
          >
            <Filter size={14} />
            Фильтры
            {activeFilterCount > 0 && (
              <span className="rounded-full bg-indigo-500/25 px-1.5 text-[11px] leading-4 text-indigo-200">
                {activeFilterCount}
              </span>
            )}
          </button>

          <DropdownMenu
            title="Ещё действия"
            trigger={<MoreHorizontal size={17} />}
            items={[
              {
                key: "new-request",
                label: "Новая заявка",
                icon: <FilePlus2 size={14} />,
                hint: "Завести закупку, которую заказчик прислал напрямую, и приложить документы",
                onSelect: () => setIsNewRequestOpen(true),
              },
              ...(feed === "standard"
                ? [
                    {
                      key: "resources",
                      label: "Площадки для синхронизации",
                      icon: <Database size={14} />,
                      onSelect: () => setIsResourcesOpen(true),
                    },
                  ]
                : []),
              {
                key: "export",
                label: isExporting ? "Формирую выгрузку…" : "Выгрузить в Excel",
                icon: <Download size={14} />,
                hint: "Все закупки по текущим фильтрам",
                disabled: isExporting,
                onSelect: () => void exportXlsx(),
              },
            ]}
          />

          <button
            onClick={() =>
              // Имя внешнего канала — это и ключ его источника.
              feed !== "standard"
                ? void syncSources([feed])
                : selectedSourceKeys && void syncSources(selectedSourceKeys)
            }
            disabled={
              isSyncing ||
              (feed === "standard" && (!selectedSourceKeys || selectedSourceKeys.length === 0))
            }
            title={
              feed !== "standard"
                ? `Собрать новые закупки через API: ${feedLabel}`
                : !selectedSourceKeys || selectedSourceKeys.length === 0
                  ? "Сначала выберите площадки в меню «⋯»"
                  : "Собрать новые закупки с выбранных площадок"
            }
            className="flex h-9 items-center gap-1.5 rounded-lg bg-indigo-500 px-3 text-sm font-medium text-snow transition-colors hover:bg-indigo-400 disabled:opacity-50"
          >
            <RefreshCw size={14} className={isSyncing ? "animate-spin" : ""} />
            {isSyncing ? "Синхронизация…" : "Синхронизировать"}
          </button>
        </div>

        {error && (
          <div className="mb-2 flex shrink-0 items-start gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-400">
            <span className="min-w-0 flex-1">{error}</span>
            <button onClick={() => setError(null)} title="Скрыть" className="shrink-0 opacity-70 hover:opacity-100">
              <X size={14} />
            </button>
          </div>
        )}
        {notice && (
          <div className="mb-2 flex shrink-0 items-start gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-400">
            <span className="min-w-0 flex-1">{notice}</span>
            <button onClick={() => setNotice(null)} title="Скрыть" className="shrink-0 opacity-70 hover:opacity-100">
              <X size={14} />
            </button>
          </div>
        )}

        {/* Строка состояния: что показано и почему. Список по умолчанию — не «все собранные
            закупки», а открытые и прошедшие профиль релевантности; без этой строки два
            включённых переключателя в свёрнутой панели выглядели как «система собрала
            только 52 тендера» (вопрос тестировщика 16.09.2026). С 28.09.2026 — просто
            строкой текста, без рамки: это пояснение, а не отдельный блок. */}
        <div className="mb-2 flex min-h-[24px] shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500">
            {isLoading ? (
              <span className="flex items-center gap-1.5">
                <Loader2 size={12} className="animate-spin text-zinc-600" />
                Считаем…
              </span>
            ) : filters.favouritesOnly ? (
              <>
                <span className="flex items-center gap-1.5">
                  <Star size={12} className="text-amber-400" fill="currentColor" />
                  <span className="font-medium tabular-nums text-zinc-200">
                    {shownTotal.toLocaleString("ru-RU")}
                  </span>
                  {plural(shownTotal, "закупка", "закупки", "закупок")} в избранном
                </span>
                <span className="text-zinc-700">·</span>
                <span>срок подачи и профиль не учитываются</span>
                <button
                  onClick={() => updateFilters({ favouritesOnly: false })}
                  className="text-indigo-400 hover:text-indigo-300"
                >
                  Ко всем закупкам
                </button>
              </>
            ) : (
              <>
                <span>
                  <span className="font-medium tabular-nums text-zinc-200">
                    {shownTotal.toLocaleString("ru-RU")}
                  </span>{" "}
                  {plural(shownTotal, "закупка", "закупки", "закупок")}
                  {collectedTotal !== null && (
                    <> из {collectedTotal.toLocaleString("ru-RU")} собранных</>
                  )}
                </span>
                {(filters.hideExpired || filters.onlyRelevant) && (
                  <>
                    <span className="text-zinc-700">·</span>
                    <span
                      title={[
                        filters.hideExpired &&
                          "Скрыты закупки с истёкшим сроком подачи, завершённые и отменённые",
                        filters.onlyRelevant &&
                          "Только прошедшие профиль релевантности из настроек и не отклонённые моделью",
                      ]
                        .filter(Boolean)
                        .join(". ")}
                    >
                      {[filters.hideExpired && "только открытые", filters.onlyRelevant && "по профилю"]
                        .filter(Boolean)
                        .join(", ")}
                    </span>
                    <button
                      onClick={() => updateFilters({ hideExpired: false, onlyRelevant: false })}
                      className="text-indigo-400 hover:text-indigo-300"
                    >
                      показать все
                    </button>
                  </>
                )}
              </>
            )}
          </div>

          {(filters.favouritesOnly || filters.minutesOnly || filters.tagIds.length > 0) && (
            <div className="flex flex-wrap items-center gap-1.5">
              {filters.minutesOnly && (
                <button
                  onClick={() => updateFilters({ minutesOnly: false })}
                  className="inline-flex items-center gap-1 rounded-full border border-rose-400/40 bg-rose-500/10 px-2 py-1 text-[11px] leading-none text-rose-300 hover:bg-rose-500/20"
                  title="Выйти из раздела «Минутки»: срок подачи в день размещения"
                >
                  <Timer size={11} />
                  Минутки
                  <X size={11} />
                </button>
              )}
              {filters.favouritesOnly && (
                <button
                  onClick={() => updateFilters({ favouritesOnly: false })}
                  className="inline-flex items-center gap-1 rounded-full border border-amber-400/40 bg-amber-500/10 px-2 py-1 text-[11px] leading-none text-amber-300 hover:bg-amber-500/20"
                  title="Выйти из раздела «Избранное»"
                >
                  <Star size={11} fill="currentColor" />
                  Избранное
                  <X size={11} />
                </button>
              )}
              {filters.tagIds.map((id) => {
                const tag = tags.find((item) => item.id === id);
                if (!tag) return null;
                return (
                  <TagChip
                    key={id}
                    tag={tag}
                    onClick={() => updateFilters({ tagIds: filters.tagIds.filter((item) => item !== id) })}
                    title={`${tag.name} — снять фильтр`}
                  />
                );
              })}
            </div>
          )}
        </div>

        {isFiltersOpen && (
        <FiltersPanel
          filters={filters}
          sources={feed === "standard" ? standardSources : []}
          regions={regions}
          tags={tags}
          onChange={updateFilters}
          onReset={() => updateFilters({ ...DEFAULT_FILTERS, feed })}
        />
        )}

        {/* Ход синхронизации — полосой над списком, а не оверлеем поверх него: опрос идёт
            десятки минут, и всё это время со списком можно работать. Текст — прогресс
            задачи с сервера («Опрос 3 из 9: Сбербанк-АСТ…»). */}
        {isSyncing && (
          <div className="mb-3 flex shrink-0 items-center gap-2.5 rounded-lg border border-indigo-500/25 bg-indigo-500/10 px-4 py-2 text-sm text-indigo-200">
            <Loader2 size={15} className="shrink-0 animate-spin" />
            <span className="min-w-0 truncate">
              {syncJob?.status === "queued"
                ? "Синхронизация поставлена в очередь…"
                : syncJob?.message || "Синхронизация с источниками…"}
            </span>
            <span className="ml-auto shrink-0 text-xs text-indigo-300/70">
              список обновится по завершении
            </span>
          </div>
        )}

        <div className="relative flex min-h-0 flex-1 flex-col">
          <div className="flex min-h-0 flex-1 flex-col">
            {isLoading ? (
              <div className="rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-600">
                Загрузка…
              </div>
            ) : activeView === "kanban" ? (
              <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-auto sm:grid-cols-2 xl:grid-cols-5">
                {COLUMNS.map((column) => {
                  const items = boardColumns[column.stage] ?? [];
                  const count = boardCounts[column.stage] ?? 0;
                  return (
                    <div
                      key={column.stage}
                      className="min-w-[260px] rounded-2xl border border-white/[0.06] bg-white/[0.02] p-3"
                    >
                      <div className="mb-3 flex items-center gap-2 px-1">
                        <span
                          className={`h-3.5 w-1 rounded-full ${column.accent}`}
                        />
                        <h2 className="text-sm font-semibold text-zinc-200">
                          {column.label}
                        </h2>
                        <span className="text-xs text-zinc-600">{count}</span>
                      </div>
                      <div className="flex flex-col gap-3">
                        {items.map((tender) => (
                          <TenderCard
                            key={tender.id}
                            tender={tender}
                            onOpen={setOpenTender}
                          />
                        ))}
                        {items.length === 0 && (
                          <div className="rounded-xl border border-dashed border-white/10 p-4 text-center text-xs text-zinc-600">
                            Нет тендеров
                          </div>
                        )}
                        {/* Колонка показывает начало выборки. Прятать остаток молча нельзя:
                          именно немая обрезка и создавала впечатление пропавших тендеров. */}
                        {count > items.length && (
                          <div className="rounded-xl border border-dashed border-white/10 p-3 text-center text-xs text-zinc-500">
                            Ещё {count - items.length} — сузьте фильтры или
                            откройте вид «Таблица»
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : tenders.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-zinc-600">
                Нет тендеров по текущим фильтрам
              </div>
            ) : activeView === "split" ? (
              <TenderSplitView
                tenders={tenders}
                selected={selectedTender}
                onSelect={setSelectedTender}
                onChanged={replaceTender}
                footer={
                  <Pagination
                    total={total}
                    offset={offset}
                    shown={tenders.length}
                    onChange={setOffset}
                  />
                }
              />
            ) : (
              <div className="min-h-0 flex-1 overflow-auto">
                <TenderTableView
                  tenders={tenders}
                  onOpen={setOpenTender}
                  sortKey={sort.key}
                  direction={sort.direction}
                  onSortChange={changeSort}
                />
              </div>
            )}

            {/* У доски нет страниц: каждая колонка приходит своей выборкой. */}
            {/* Пагинация не участвует в растяжении: `shrink-0`, иначе при коротком списке
                она уезжала бы к нижнему краю окна, оторвавшись от содержимого. */}
            {!isLoading && activeView === "table" && total > 0 && (
              <Pagination
                total={total}
                offset={offset}
                shown={tenders.length}
                onChange={setOffset}
              />
            )}
          </div>
        </div>
      </div>

      {isNewRequestOpen && (
        <NewRequestModal
          onCancel={() => setIsNewRequestOpen(false)}
          onCreated={handleRequestCreated}
        />
      )}

      {isResourcesOpen && (
        <ResourcesModal
          // Источник ручных заявок — не площадка: синхронизировать по нему нечего, а в
          // фильтре «Площадки» он остаётся, чтобы можно было показать одни заявки.
          sources={standardSources.filter((s) => s.type !== MANUAL_SOURCE_TYPE)}
          initialSelected={new Set(selectedSourceKeys ?? [])}
          onCancel={() => setIsResourcesOpen(false)}
          onSave={handleSaveResources}
        />
      )}

      {openTender && (
        <TenderDetailModal
          tender={openTender}
          onClose={() => setOpenTender(null)}
          onChanged={replaceTender}
        />
      )}
    </AppShell>
  );
}

/** Сортировка списка по датам (29.09.2026) — прямо в панели над списком, а не в
 *  «Фильтрах»: это порядок строк, а не сужение выдачи, и нужна она во всех видах, а не
 *  только в таблице, где сортируют щелчком по заголовку. Повторный щелчок по выбранной
 *  дате меняет направление. Первый щелчок — самое полезное направление: свежие
 *  размещения сверху, ближайшие окончания подачи сверху. */
const DATE_SORTS: { key: "publish_date" | "application_end"; label: string; first: SortDirection }[] = [
  { key: "publish_date", label: "Размещение", first: "desc" },
  { key: "application_end", label: "Окончание", first: "asc" },
];

function DateSortControl({
  sort,
  onChange,
}: {
  sort: { key: SortKey; direction: SortDirection };
  onChange: (key: SortKey, direction: SortDirection) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Сортировка по дате"
      className="flex h-9 items-center gap-0.5 rounded-lg border border-white/[0.08] bg-white/[0.03] p-1"
    >
      <span className="px-1.5 text-xs text-zinc-500">Дата:</span>
      {DATE_SORTS.map((item) => {
        const isActive = sort.key === item.key;
        const Arrow = isActive && sort.direction === "asc" ? ArrowUp : ArrowDown;
        const next = isActive ? (sort.direction === "asc" ? "desc" : "asc") : item.first;
        return (
          <button
            key={item.key}
            type="button"
            aria-pressed={isActive}
            onClick={() => onChange(item.key, next)}
            title={
              isActive
                ? `Сейчас: ${sort.direction === "asc" ? "сначала ранние" : "сначала поздние"} — нажмите, чтобы развернуть`
                : `Сортировать по дате ${item.key === "publish_date" ? "размещения" : "окончания подачи"}`
            }
            className={`flex h-7 items-center gap-1 rounded-md px-2 text-xs transition-colors ${
              isActive ? "bg-white/10 text-white" : "text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {item.label}
            {isActive && <Arrow size={12} />}
          </button>
        );
      })}
    </div>
  );
}
