import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Ban,
  BadgeCheck,
  Calculator,
  Download,
  FileText,
  Building2,
  ClipboardList,
  FileSignature,
  HelpCircle,
  History,
  Info,
  ListTree,
  ScrollText,
  Loader2,
  Play,
  Scale,
  Sparkles,
  Star,
  Upload,
  X,
} from "lucide-react";

import { ApiError, api, downloadFile, postForm } from "../api/client";
import type {
  AiProfileScore,
  BackgroundJob,
  CompanyProfile,
  ComplianceMatrix,
  NicheStatistics,
  Requirement,
  TenderCard,
  TenderExtraSections,
  TenderInsights,
  Tender,
  TenderDocument,
  TenderHistoryEntry,
  TenderStage,
  TenderUpdate,
} from "../api/types";
import { STAGE_LABELS, STAGE_ORDER } from "../api/types";
import { TenderAiPanel } from "./tender-detail/TenderAiPanel";
import { TenderAiScorePanel } from "./tender-detail/TenderAiScorePanel";
import { TenderApplicationTab } from "./tender-detail/TenderApplicationTab";
import { TenderCalculationTab } from "./tender-detail/TenderCalculationTab";
import { TenderCardTab } from "./tender-detail/TenderCardTab";
import { TenderComplianceTab } from "./tender-detail/TenderComplianceTab";
import { TenderUpperSoftwareBlock } from "./tender-detail/TenderUpperSoftwareBlock";
import { TenderRegistryBlock } from "./tender-detail/TenderRegistryBlock";
import { TenderExtraTab } from "./tender-detail/TenderExtraTab";
import { TenderHistoryTab } from "./tender-detail/TenderHistoryTab";
import { TenderOverviewTab } from "./tender-detail/TenderOverviewTab";
import { TenderRequirementsTab } from "./tender-detail/TenderRequirementsTab";
import {
  percentValue,
  relevanceLabel,
  scoreBadgeClass,
  stageHint,
} from "../utils/format";

type TabKey =
  | "overview"
  | "extra"
  | "documents"
  | "calculation"
  | "compliance"
  | "application"
  | "card"
  | "requirements"
  | "lots"
  | "changes"
  | "protocols"
  | "contracts"
  | "events"
  | "history";

// Как часто карточка спрашивает сервер о состоянии запущенной фоновой задачи.
const JOB_POLL_MS = 2_000;

type JobKindKey = "analyze" | "evaluate" | "ai-score";

const RELEVANCE_CLASSES: Record<string, string> = {
  new: "border-white/10 bg-white/5 text-zinc-400",
  confirmed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  rejected: "border-red-500/30 bg-red-500/10 text-red-300",
};

function DocumentRow({
  document,
  onDownload,
  isDownloading,
  onTogglePriority,
}: {
  document: TenderDocument;
  onDownload: () => void;
  isDownloading: boolean;
  onTogglePriority: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        {/* Звёздочка — не украшение: помеченный файл уходит в разбор первым и не попадает
            под обрезку по длине контекста (раздел 5.6 ТЗ). */}
        <button
          onClick={onTogglePriority}
          title={
            document.is_priority_source
              ? "Снять отметку приоритетного источника"
              : "Считать в первую очередь по этому файлу"
          }
          className={`shrink-0 ${
            document.is_priority_source ? "text-amber-400" : "text-zinc-600 hover:text-zinc-400"
          }`}
        >
          <Star size={14} fill={document.is_priority_source ? "currentColor" : "none"} />
        </button>
        <FileText size={15} className="shrink-0 text-zinc-500" />
        <span className="truncate text-sm text-zinc-200" title={document.file_name}>
          {document.file_name}
        </span>
        {document.document_class_label && (
          <span className="shrink-0 rounded-md bg-white/5 px-2 py-0.5 text-[11px] text-zinc-400">
            {document.document_class_label}
          </span>
        )}
        {document.parse_status === "error" && (
          <span title={document.parse_error ?? "Не удалось разобрать документ"}>
            <AlertTriangle size={13} className="shrink-0 text-amber-400" />
          </span>
        )}
      </div>
      <button
        onClick={onDownload}
        disabled={isDownloading}
        className="flex shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
      >
        <Download size={13} />
        {isDownloading ? "Скачивание…" : "Скачать"}
      </button>
    </div>
  );
}

/**
 * Карточка тендера (раздел 5.6 ТЗ) — содержимое без обёртки.
 *
 * Отдельно от модального окна, потому что с 03.09.2026 та же карточка показывается в двух
 * местах: в модалке (из Kanban/таблицы/таймплана) и в правой панели двухпанельного режима.
 * Дублировать её логику в двух компонентах значило бы получить две расходящиеся карточки —
 * с разным набором вкладок и разным поведением кнопок.
 *
 * Данные вкладок грузятся лениво, по первому открытию: матрица на полсотни требований ×
 * тринадцать производителей — сотни строк, тянуть их ради взгляда на сроки незачем.
 */
export function TenderDetailPanel({
  tender: initialTender,
  onClose,
  onChanged,
}: {
  tender: Tender;
  onClose?: () => void;
  onChanged?: (tender: Tender) => void;
}) {
  const [tender, setTender] = useState<Tender>(initialTender);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");

  const [documents, setDocuments] = useState<TenderDocument[] | null>(null);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);

  const [requirements, setRequirements] = useState<Requirement[] | null>(null);
  // Последний запуск анализа документов. Нужен пустой вкладке «Требования»: без него она
  // советует нажать «Анализ документов» и тогда, когда он только что отработал и честно не
  // нашёл ни одного требования. `undefined` — ещё не спрашивали, `null` — не запускался.
  const [analysisJob, setAnalysisJob] = useState<BackgroundJob | null | undefined>(undefined);
  const [matrix, setMatrix] = useState<ComplianceMatrix | null>(null);
  const [history, setHistory] = useState<TenderHistoryEntry[] | null>(null);

  const [runningAction, setRunningAction] = useState<JobKindKey | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const [card, setCard] = useState<TenderCard | null>(null);
  const [isCardLoading, setIsCardLoading] = useState(true);
  const [isCardRefreshing, setIsCardRefreshing] = useState(false);
  const [insights, setInsights] = useState<TenderInsights | null>(null);
  const [isInsightsRunning, setIsInsightsRunning] = useState(false);
  const [insightsError, setInsightsError] = useState<string | null>(null);

  // AI-оценка по профилю (раздел 5.5.1 ТЗ) — грузится сразу вместе с карточкой: это шапка,
  // а не вкладка, и человек смотрит на неё первым делом.
  const [score, setScore] = useState<AiProfileScore | null>(null);
  const [scoreError, setScoreError] = useState<string | null>(null);

  const [extra, setExtra] = useState<TenderExtraSections | null>(null);
  const [isExtraLoading, setIsExtraLoading] = useState(false);
  const [isExtraRunning, setIsExtraRunning] = useState(false);
  const [extraError, setExtraError] = useState<string | null>(null);

  const [niche, setNiche] = useState<NicheStatistics | null>(null);
  const [isNicheLoading, setIsNicheLoading] = useState(false);
  const [profile, setProfile] = useState<CompanyProfile | null>(null);

  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const applyTender = useCallback(
    (updated: Tender) => {
      setTender(updated);
      onChanged?.(updated);
    },
    [onChanged],
  );

  // Смена тендера в двухпанельном режиме — это тот же компонент с другими данными:
  // без сброса состояния правая панель показывала бы документы и оценку предыдущей закупки.
  useEffect(() => {
    setTender(initialTender);
    setActiveTab("overview");
    setDocuments(null);
    setRequirements(null);
    setAnalysisJob(undefined);
    setMatrix(null);
    setHistory(null);
    setCard(null);
    setInsights(null);
    setScore(null);
    setExtra(null);
    setNiche(null);
    setError(null);
    setActionMessage(null);
  }, [initialTender]);

  useEffect(() => {
    let cancelled = false;
    setDocsError(null);
    api
      .get<TenderDocument[]>(`/tenders/${tender.id}/documents`)
      .then((docs) => {
        if (!cancelled) setDocuments(docs);
      })
      .catch((err) => {
        if (!cancelled) {
          setDocsError(err instanceof ApiError ? err.message : "Не удалось загрузить документы");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [tender.id]);

  useEffect(() => {
    let cancelled = false;
    api
      .get<AiProfileScore | null>(`/tenders/${tender.id}/ai-score`)
      .then((data) => {
        if (!cancelled) setScore(data);
      })
      .catch(() => {
        // Отсутствие оценки — обычное состояние; ошибку показываем только при расчёте.
      });
    void api
      .get<CompanyProfile | null>("/company-profile")
      .then((data) => {
        if (!cancelled) setProfile(data);
      })
      .catch(() => setProfile(null));
    return () => {
      cancelled = true;
    };
  }, [tender.id]);

  const loadCard = useCallback(
    async (refresh = false) => {
      if (refresh) setIsCardRefreshing(true);
      else setIsCardLoading(true);
      try {
        const data = await api.get<TenderCard>(
          `/tenders/${tender.id}/card${refresh ? "?refresh=true" : ""}`,
        );
        setCard(data);
        if (data.insights) setInsights(data.insights);
        if (refresh || data.sections.length > 0) {
          const updated = await api.get<Tender>(`/tenders/${tender.id}`);
          applyTender(updated);
        }
      } catch {
        setCard(null);
      } finally {
        setIsCardLoading(false);
        setIsCardRefreshing(false);
      }
    },
    [tender.id, applyTender],
  );

  useEffect(() => {
    void loadCard();
  }, [loadCard]);

  const runInsights = async () => {
    setIsInsightsRunning(true);
    setInsightsError(null);
    try {
      const result = await api.post<TenderInsights>(`/tenders/${tender.id}/insights`);
      setInsights(result);
      if (result.applied_fields && result.applied_fields.length > 0) {
        applyTender(await api.get<Tender>(`/tenders/${tender.id}`));
      }
    } catch (err) {
      setInsightsError(
        err instanceof ApiError ? err.message : "Не удалось выполнить разбор карточки",
      );
    } finally {
      setIsInsightsRunning(false);
    }
  };

  const loadRequirements = useCallback(async () => {
    setRequirements(await api.get<Requirement[]>(`/tenders/${tender.id}/requirements`));
  }, [tender.id]);

  const loadAnalysisJob = useCallback(async () => {
    const jobs = await api.get<BackgroundJob[]>(
      `/jobs?tender_id=${tender.id}&kind=tender_analysis&limit=1`,
    );
    setAnalysisJob(jobs[0] ?? null);
  }, [tender.id]);

  const loadMatrix = useCallback(async () => {
    setMatrix(await api.get<ComplianceMatrix>(`/tenders/${tender.id}/compliance`));
  }, [tender.id]);

  const loadHistory = useCallback(async () => {
    setHistory(await api.get<TenderHistoryEntry[]>(`/tenders/${tender.id}/history`));
  }, [tender.id]);

  const loadExtra = useCallback(async () => {
    setIsExtraLoading(true);
    try {
      setExtra(await api.get<TenderExtraSections>(`/tenders/${tender.id}/extra-sections`));
    } finally {
      setIsExtraLoading(false);
    }
  }, [tender.id]);

  const loadNiche = useCallback(async () => {
    setIsNicheLoading(true);
    try {
      setNiche(await api.get<NicheStatistics | null>(`/tenders/${tender.id}/niche-statistics`));
    } finally {
      setIsNicheLoading(false);
    }
  }, [tender.id]);

  useEffect(() => {
    if (activeTab === "requirements" && requirements === null) void loadRequirements();
    if (activeTab === "requirements" && analysisJob === undefined) void loadAnalysisJob();
    if (activeTab === "compliance" && matrix === null) void loadMatrix();
    // Требования нужны и на вкладке «Конкуренция»: по их количеству она объясняет, почему
    // матрица пуста — «нечего сопоставлять» или «расчёт ещё не запускали».
    if (activeTab === "compliance" && requirements === null) void loadRequirements();
    if (activeTab === "history" && history === null) void loadHistory();
    if (activeTab === "extra" && extra === null) void loadExtra();
    if (activeTab === "calculation" && niche === null) void loadNiche();
  }, [
    activeTab,
    requirements,
    analysisJob,
    loadAnalysisJob,
    matrix,
    history,
    extra,
    niche,
    loadRequirements,
    loadMatrix,
    loadHistory,
    loadExtra,
    loadNiche,
  ]);

  const runExtraSections = async () => {
    setIsExtraRunning(true);
    setExtraError(null);
    try {
      setExtra(await api.post<TenderExtraSections>(`/tenders/${tender.id}/extra-sections`));
    } catch (err) {
      setExtraError(
        err instanceof ApiError ? err.message : "Не удалось извлечь условия закупки",
      );
    } finally {
      setIsExtraRunning(false);
    }
  };

  const handleDownload = async (document: TenderDocument) => {
    setDownloadingId(document.id);
    setError(null);
    try {
      await downloadFile(
        `/tenders/${tender.id}/documents/${document.id}/download`,
        document.file_name,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось скачать файл");
    } finally {
      setDownloadingId(null);
    }
  };

  /** Файл, присланный заказчиком письмом, — к любой закупке, не только к заявке: уточнённое
   * ТЗ по собранному тендеру тоже должно попасть в анализ. */
  const uploadDocuments = async (files: FileList) => {
    if (files.length === 0) return;
    setIsUploading(true);
    setError(null);
    try {
      const form = new FormData();
      for (const file of Array.from(files)) form.append("files", file, file.name);
      const added = await postForm<TenderDocument[]>(
        `/tenders/${tender.id}/documents/upload`,
        form,
      );
      setDocuments((prev) => [...(prev ?? []), ...added]);
      setActionMessage(
        `Добавлено файлов: ${added.length} — запустите «Анализ документов», чтобы учесть их`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить файлы");
    } finally {
      setIsUploading(false);
    }
  };

  const togglePriority = async (document: TenderDocument) => {
    setError(null);
    try {
      const updated = await api.patch<TenderDocument>(
        `/tenders/${tender.id}/documents/${document.id}`,
        { is_priority_source: !document.is_priority_source },
      );
      setDocuments((prev) =>
        prev ? prev.map((item) => (item.id === updated.id ? updated : item)) : prev,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить отметку документа");
    }
  };

  /**
   * Запуск фоновой операции. Ответ приходит сразу, а карточка опрашивает состояние задачи:
   * синхронно эти операции держали бы HTTP-запрос открытым десятки секунд, и закрытая
   * вкладка выглядела бы как «ничего не произошло».
   */
  const startJob = async (kind: JobKindKey) => {
    setRunningAction(kind);
    setError(null);
    setScoreError(null);
    setActionMessage(null);
    try {
      const job = await api.post<BackgroundJob>(`/tenders/${tender.id}/${kind}`);
      setJobId(job.id);
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Не удалось запустить операцию";
      // Незаполненный профиль компании — причина отказа именно расчёта оценки, и текст
      // должен стоять рядом с ней, а не в общей строке ошибок карточки.
      if (kind === "ai-score") setScoreError(message);
      else setError(message);
      setRunningAction(null);
    }
  };

  useEffect(() => {
    if (!jobId || runningAction === null) return;
    let cancelled = false;

    const finish = async (job: BackgroundJob) => {
      setRunningAction(null);
      setJobId(null);
      if (job.status === "error") {
        setError(job.message ?? "Операция завершилась ошибкой");
        return;
      }
      setActionMessage(job.message);
      applyTender(await api.get<Tender>(`/tenders/${tender.id}`));
      if (job.kind === "tender_analysis") {
        await loadRequirements();
        setAnalysisJob(job);
        setActiveTab("requirements");
      } else if (job.kind === "tender_evaluation") {
        await loadMatrix();
        setActiveTab("compliance");
      } else {
        setScore(await api.get<AiProfileScore | null>(`/tenders/${tender.id}/ai-score`));
        await loadExtra();
      }
    };

    const interval = setInterval(async () => {
      try {
        const jobs = await api.get<BackgroundJob[]>(`/jobs?tender_id=${tender.id}&limit=5`);
        const job = jobs.find((item) => item.id === jobId);
        if (cancelled || !job || job.status === "queued" || job.status === "running") return;
        clearInterval(interval);
        await finish(job);
      } catch {
        // Разрыв связи не должен гасить индикатор: следующая попытка через две секунды.
      }
    }, JOB_POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [jobId, runningAction, tender.id, applyTender, loadRequirements, loadMatrix, loadExtra]);

  const saveChanges = async (changes: TenderUpdate) => {
    setError(null);
    try {
      applyTender(await api.patch<Tender>(`/tenders/${tender.id}`, changes));
      if (history !== null) await loadHistory();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить изменения");
    }
  };

  const setRelevance = async (status: "confirmed" | "rejected") => {
    await saveChanges({ relevance_status: status });
  };

  // Избранное (замечание тестировщика 16.09.2026): отложить закупку, чтобы вернуться к ней
  // позже. Личное — у каждого пользователя своё; раздел «Избранное» на странице тендеров
  // показывает отложенное вне фильтров по умолчанию.
  const toggleBookmark = async () => {
    setError(null);
    try {
      applyTender(
        tender.is_bookmarked
          ? await api.delete<Tender>(`/tenders/${tender.id}/bookmark`)
          : await api.put<Tender>(`/tenders/${tender.id}/bookmark`, {}),
      );
      if (history !== null) await loadHistory();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить избранное");
    }
  };

  const changeStage = async (stage: TenderStage) => {
    setError(null);
    try {
      applyTender(await api.patch<Tender>(`/tenders/${tender.id}/stage`, { stage }));
      if (history !== null) await loadHistory();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сменить этап");
    }
  };

  const addComment = async (text: string) => {
    setError(null);
    try {
      await api.post(`/tenders/${tender.id}/comments`, { text });
      await loadHistory();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить комментарий");
    }
  };

  const cardTable = (key: string) => card?.tables?.[key];
  const cardTableCount = (key: string) => cardTable(key)?.rows.length;

  // Порядок вкладок — раздела 5.6 ТЗ: Основное, Дополнительно, Документы, Расчёт,
  // Конкуренция, Заявка. Остальное (карточка источника, требования, таблицы извещения,
  // история) идёт следом — это служебные разрезы тех же данных.
  //
  // «Похожие» убрана (04.09.2026): вкладка требует посчитанных эмбеддингов по
  // представительному корпусу, до тех пор честно показывала «недостаточно данных» и только
  // занимала место в ряду. Похожие закупки при этом никуда не делись — они по-прежнему
  // перечислены в блоке «AI-оценка по профилю» как обоснование измерения History.
  const tabs: { key: TabKey; label: string; icon: typeof Info; badge?: number }[] = [
    { key: "overview", label: "Основное", icon: Info },
    { key: "extra", label: "Дополнительно", icon: ListTree },
    { key: "documents", label: "Документы", icon: FileText, badge: documents?.length },
    { key: "calculation", label: "Расчёт", icon: Calculator },
    { key: "compliance", label: "Конкуренция", icon: Scale },
    { key: "application", label: "Заявка", icon: FileSignature },
    { key: "card", label: "Карточка закупки", icon: Building2 },
    {
      key: "requirements",
      label: "Требования",
      icon: Sparkles,
      badge: requirements?.length ?? tender.requirements_count,
    },
    ...(cardTable("lots")
      ? [{ key: "lots" as TabKey, label: "Лоты", icon: ListTree, badge: cardTableCount("lots") }]
      : []),
    ...(cardTable("changes")
      ? [
          {
            key: "changes" as TabKey,
            label: "Изменения",
            icon: ClipboardList,
            badge: cardTableCount("changes"),
          },
        ]
      : []),
    ...(cardTable("protocols")
      ? [
          {
            key: "protocols" as TabKey,
            label: "Протоколы",
            icon: ScrollText,
            badge: cardTableCount("protocols"),
          },
        ]
      : []),
    ...(cardTable("contracts")
      ? [
          {
            key: "contracts" as TabKey,
            label: "Договоры",
            icon: FileSignature,
            badge: cardTableCount("contracts"),
          },
        ]
      : []),
    ...(cardTable("events")
      ? [
          {
            key: "events" as TabKey,
            label: "Журнал событий",
            icon: ScrollText,
            badge: cardTableCount("events"),
          },
        ]
      : []),
    { key: "history", label: "История", icon: History },
  ];

  const overall = percentValue(tender.ai_score);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] px-6 py-4">
        <div className="min-w-0">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className="rounded-md bg-white/5 px-2 py-0.5 text-xs text-zinc-400">
              {tender.source.name}
            </span>
            <span className="text-xs text-zinc-500">№ {tender.external_id}</span>
            <span
              className={`rounded-md border px-2 py-0.5 text-[11px] ${scoreBadgeClass(overall)}`}
              title="Итоговая AI-оценка по профилю (раздел 5.5.1 ТЗ)"
            >
              {overall === null ? "AI-оценка не считалась" : `AI-оценка ${overall}%`}
            </span>
            <span
              className={`rounded-md border px-2 py-0.5 text-[11px] ${
                RELEVANCE_CLASSES[tender.relevance_status] ?? RELEVANCE_CLASSES.new
              }`}
            >
              {relevanceLabel(tender.relevance_status)}
            </span>
          </div>
          <h2 className="text-base font-semibold leading-snug text-white">{tender.title}</h2>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={toggleBookmark}
            title={
              tender.is_bookmarked
                ? "Убрать из избранного"
                : "В избранное — чтобы вернуться к закупке позже (раздел «Избранное» на странице тендеров)"
            }
            aria-pressed={tender.is_bookmarked}
            className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs transition-colors ${
              tender.is_bookmarked
                ? "border-amber-400/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20"
                : "border-white/10 text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
            }`}
          >
            <Star size={14} fill={tender.is_bookmarked ? "currentColor" : "none"} />
            {tender.is_bookmarked ? "В избранном" : "В избранное"}
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="rounded-md p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
            >
              <X size={18} />
            </button>
          )}
        </div>
      </div>

      {/* Тоже одна строка с прокруткой, а не перенос: вторая строка кнопок съедала у
          карточки полсотни пикселей, а действий здесь ровно столько же на любой закупке. */}
      <div className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-white/[0.08] px-6 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {/* Этап пайплайна — выпадающим списком, а не двумя кнопками: этапов шесть, и
            «релевантно/неактуально» их не покрывает (раздел 5.6 ТЗ). */}
        <label className="flex shrink-0 items-center gap-1.5 whitespace-nowrap text-xs text-zinc-500">
          Этап:
          <select
            value={tender.stage}
            onChange={(e) => void changeStage(e.target.value as TenderStage)}
            className="rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-indigo-500"
          >
            {STAGE_ORDER.map((stage) => (
              <option key={stage} value={stage} className="bg-zinc-900">
                {STAGE_LABELS[stage]}
              </option>
            ))}
          </select>
          {/* Что означает выбранный этап — прямо здесь: список из шести значений без
              пояснений читается как набор синонимов, а «Заявка подана» вдобавок влияет на
              учёт проигрышей, и об этом надо знать в момент выбора. */}
          {stageHint(tender.stage) && (
            <span
              className="cursor-help text-zinc-600"
              title={stageHint(tender.stage) ?? undefined}
            >
              <HelpCircle size={13} />
            </span>
          )}
        </label>
        <div className="mx-1 h-5 w-px bg-white/10" />
        <button
          onClick={() => void setRelevance("confirmed")}
          disabled={tender.relevance_status === "confirmed"}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-xs text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-40"
        >
          <BadgeCheck size={14} />
          Релевантный
        </button>
        <button
          onClick={() => void setRelevance("rejected")}
          disabled={tender.relevance_status === "rejected"}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs text-red-300 hover:bg-red-500/20 disabled:opacity-40"
        >
          <Ban size={14} />
          Неактуально
        </button>
        <div className="mx-1 h-5 w-px bg-white/10" />
        <button
          onClick={() => void startJob("analyze")}
          disabled={runningAction !== null}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs text-indigo-300 hover:bg-indigo-500/20 disabled:opacity-50"
        >
          {runningAction === "analyze" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Sparkles size={14} />
          )}
          Анализ документов
        </button>
        <button
          onClick={() => void startJob("evaluate")}
          disabled={runningAction !== null}
          className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-1.5 text-xs text-indigo-300 hover:bg-indigo-500/20 disabled:opacity-50"
        >
          {runningAction === "evaluate" ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Play size={14} />
          )}
          Расчёт соответствия
        </button>
      </div>

      {/* Одна строка с горизонтальной прокруткой вместо переноса на второй ряд: вкладок
          бывает до десятка (у закупки с лотами, протоколами и договорами), и перенос
          съедал высоту у самой карточки, а ряд «прыгал» при переключении закупок.
          `scrollbar-none` — полоса не нужна, ряд листается колесом и свайпом. */}
      <div className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-white/[0.08] px-6 py-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-3 py-1.5 text-sm transition-colors ${
                isActive ? "bg-white/10 text-white" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              <Icon size={14} />
              {tab.label}
              {tab.badge !== undefined && tab.badge > 0 && (
                <span className="rounded-full bg-white/10 px-1.5 text-[10px] text-zinc-400">
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {error && (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
            {error}
          </div>
        )}
        {runningAction !== null && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] px-3 py-2 text-xs text-indigo-300">
            <Loader2 size={13} className="animate-spin" />
            {runningAction === "analyze"
              ? "Идёт анализ документов на сервере."
              : runningAction === "evaluate"
                ? "Идёт расчёт соответствия на сервере."
                : "Идёт расчёт AI-оценки по профилю."}{" "}
            Карточку можно закрыть — работа продолжится, а ход виден в «Настройках →
            Логирование».
          </div>
        )}

        {actionMessage && (
          <div className="mb-4 rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] px-3 py-2 text-xs text-indigo-300">
            {actionMessage}
          </div>
        )}

        {activeTab === "overview" && (
          <div className="space-y-5">
            {/* Главная метрика — выше данных источника: с неё начинается решение об участии. */}
            <TenderAiScorePanel
              score={score}
              isRunning={runningAction === "ai-score"}
              onRun={() => void startJob("ai-score")}
              error={scoreError}
            />
            <TenderOverviewTab tender={tender} onSave={saveChanges} />
            <TenderAiPanel
              insights={insights}
              isRunning={isInsightsRunning}
              onRun={() => void runInsights()}
              error={insightsError}
            />
          </div>
        )}

        {activeTab === "extra" && (
          <TenderExtraTab
            data={extra}
            isLoading={isExtraLoading}
            isRunning={isExtraRunning || runningAction === "ai-score"}
            onRun={() => void runExtraSections()}
            error={extraError}
          />
        )}

        {activeTab === "calculation" && (
          <TenderCalculationTab
            statistics={niche}
            isLoading={isNicheLoading}
            okpd2={tender.okpd2_code}
          />
        )}

        {activeTab === "application" && (
          <TenderApplicationTab profile={profile} documents={documents} />
        )}

        {activeTab === "card" && (
          <TenderCardTab
            card={card}
            isLoading={isCardLoading}
            onRefresh={() => void loadCard(true)}
            isRefreshing={isCardRefreshing}
          />
        )}

        {(activeTab === "lots" ||
          activeTab === "changes" ||
          activeTab === "protocols" ||
          activeTab === "contracts" ||
          activeTab === "events") && (
          <TenderCardTab
            card={card}
            isLoading={isCardLoading}
            onRefresh={() => void loadCard(true)}
            isRefreshing={isCardRefreshing}
            tableKeys={[activeTab]}
          />
        )}

        {activeTab === "documents" && (
          <div>
            <div className="mb-3 flex items-center justify-between gap-3">
              <span className="text-xs text-zinc-500">
                Документы закупки — с площадки и приложенные вручную
              </span>
              <button
                onClick={() => uploadInputRef.current?.click()}
                disabled={isUploading}
                title="Приложить файл, присланный заказчиком: проект договора, ТЗ, спецификацию"
                className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
              >
                {isUploading ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Upload size={13} />
                )}
                {isUploading ? "Загружаю…" : "Добавить файл"}
              </button>
              <input
                ref={uploadInputRef}
                type="file"
                multiple
                accept=".pdf,.doc,.docx,.rtf,.xls,.xlsx,.xlsm,.txt,.zip,.7z,.rar"
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) void uploadDocuments(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>
            {documents === null && !docsError && (
              <div className="flex items-center gap-2 text-sm text-zinc-500">
                <Loader2 size={14} className="animate-spin" />
                Загрузка документов…
              </div>
            )}
            {docsError && <div className="text-sm text-red-400">{docsError}</div>}
            {documents && documents.length === 0 && (
              <div className="text-sm text-zinc-500">
                Документов нет — приложите файл кнопкой «Добавить файл»
              </div>
            )}
            {documents && documents.length > 0 && (
              <div className="space-y-2">
                {documents.map((document) => (
                  <DocumentRow
                    key={document.id}
                    document={document}
                    onDownload={() => void handleDownload(document)}
                    isDownloading={downloadingId === document.id}
                    onTogglePriority={() => void togglePriority(document)}
                  />
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "requirements" &&
          (requirements === null ? (
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <Loader2 size={14} className="animate-spin" />
              Загрузка требований…
            </div>
          ) : (
            <TenderRequirementsTab
              requirements={requirements}
              analysis={
                analysisJob === undefined
                  ? null
                  : {
                      ran: analysisJob !== null && analysisJob.status === "success",
                      message: analysisJob?.message ?? null,
                      documentsWithText:
                        documents === null
                          ? null
                          : documents.filter((document) => document.has_text).length,
                    }
              }
            />
          ))}

        {activeTab === "compliance" &&
          (matrix === null ? (
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <Loader2 size={14} className="animate-spin" />
              Загрузка матрицы соответствия…
            </div>
          ) : (
            <>
              {/* Интеграция в ПО верхнего уровня — отдельно и над матрицей: ответ на самое
                  частое требование ТЗ должен быть виден сразу (замечание тестировщика 16.09.2026). */}
              <TenderUpperSoftwareBlock tenderId={tender.id} />
              {/* Допуски (ПП 719, ЗАК Россетей, реестр ПО) — по той же причине над матрицей. */}
              <TenderRegistryBlock tenderId={tender.id} />
              <TenderComplianceTab
                matrix={matrix}
                requirementsCount={requirements === null ? null : requirements.length}
              />
            </>
          ))}

        {activeTab === "history" && (
          <TenderHistoryTab entries={history} onComment={addComment} />
        )}
      </div>
    </div>
  );
}
