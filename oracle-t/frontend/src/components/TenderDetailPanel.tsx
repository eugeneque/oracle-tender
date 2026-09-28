import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Ban,
  BadgeCheck,
  ChevronDown,
  Download,
  FileText,
  History,
  Loader2,
  Maximize2,
  MessageSquare,
  MoreHorizontal,
  Sparkles,
  Star,
  Tag as TagIcon,
  Upload,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";

import { ApiError, api, downloadFile, postForm } from "../api/client";
import type {
  AiFeedback,
  AiFeedbackCreated,
  AiProfileScore,
  BackgroundJob,
  CompanyProfile,
  ComplianceMatrix,
  NicheStatistics,
  Requirement,
  TenderCard,
  TenderExtraSections,
  Tender,
  TenderDocument,
  TenderHistoryEntry,
  TenderStage,
  TenderUpdate,
} from "../api/types";
import { STAGE_LABELS, STAGE_ORDER } from "../api/types";
import { DecisionMark } from "./DecisionMark";
import { DropdownMenu } from "./DropdownMenu";
import { TenderAiScorePanel } from "./tender-detail/TenderAiScorePanel";
import { TenderApplicationTab } from "./tender-detail/TenderApplicationTab";
import { TenderCalculationTab } from "./tender-detail/TenderCalculationTab";
import { TenderCardTab } from "./tender-detail/TenderCardTab";
import { TenderCommentsDrawer } from "./tender-detail/TenderCommentsDrawer";
import { TenderComplianceTab } from "./tender-detail/TenderComplianceTab";
import { TenderUpperSoftwareBlock } from "./tender-detail/TenderUpperSoftwareBlock";
import { TenderRegistryBlock } from "./tender-detail/TenderRegistryBlock";
import { TenderExtraTab } from "./tender-detail/TenderExtraTab";
import { TenderHistoryTab } from "./tender-detail/TenderHistoryTab";
import { TenderOverviewTab } from "./tender-detail/TenderOverviewTab";
import { TenderRequirementsTab } from "./tender-detail/TenderRequirementsTab";
import { TagChip } from "./tags/TagChip";
import { TagPicker } from "./tags/TagPicker";
import {
  percentValue,
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

// «review» — полный разбор одной задачей: анализ документов → матрица соответствия →
// AI-оценка (18.09.2026). Отдельные «analyze»/«evaluate» с кнопок сняты: второй зависел от
// первого, а третий без первого считал вслепую, и человек должен был знать порядок.
// «ai-feedback» — пересмотр заключения по замечанию специалиста (28.09.2026): запускается не
// кнопкой-эндпоинтом, а ответом «Не согласен», но опрашивается так же.
type JobKindKey = "review" | "analyze" | "evaluate" | "ai-score" | "ai-feedback";

/** Вид фоновой задачи → кнопка карточки, которая её запускает. */
const JOB_KIND_KEYS: Partial<Record<BackgroundJob["kind"], JobKindKey>> = {
  tender_full_review: "review",
  tender_analysis: "analyze",
  tender_evaluation: "evaluate",
  ai_profile_score: "ai-score",
  ai_feedback: "ai-feedback",
};

/** Что делает «Разобрать закупку» — подсказка на кнопке и в пустых состояниях вкладок. */
const REVIEW_HINT =
  "Анализ документов, матрица соответствия и AI-оценка по профилю одной задачей";

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
  expandable = true,
}: {
  tender: Tender;
  onClose?: () => void;
  onChanged?: (tender: Tender) => void;
  /** Кнопка «Развернуть» — переход на отдельную страницу закупки (`/tenders/:id`,
   * замечание 17.09.2026). На самой этой странице кнопка не нужна. */
  expandable?: boolean;
}) {
  const [tender, setTender] = useState<Tender>(initialTender);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [isTagPickerOpen, setIsTagPickerOpen] = useState(false);

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
  // Ход полного разбора («Шаг 2 из 3: расчёт соответствия…») — сервер пишет его в
  // `message` идущей задачи, карточка показывает вместо безымянного индикатора.
  const [jobProgress, setJobProgress] = useState<string | null>(null);

  const [card, setCard] = useState<TenderCard | null>(null);
  const [isCardLoading, setIsCardLoading] = useState(true);
  const [isCardRefreshing, setIsCardRefreshing] = useState(false);

  // AI-оценка по профилю (раздел 5.5.1 ТЗ) — грузится сразу вместе с карточкой: это шапка,
  // а не вкладка, и человек смотрит на неё первым делом. Если оценки ещё нет, расчёт
  // запускается сам (решение 17.09.2026): кнопку «Рассчитать» на каждой новой закупке
  // нажимали не всегда, и список показывал «не считалась» там, где ответ был нужен.
  const [score, setScore] = useState<AiProfileScore | null>(null);
  const [scoreError, setScoreError] = useState<string | null>(null);
  // Для какого тендера автозапуск уже сделан: эффект загрузки оценки в StrictMode и при
  // обновлении карточки срабатывает повторно, а второй POST только плодил бы запросы —
  // сервер и так возвращает уже идущую задачу, но ходить за ней дважды незачем.
  const autoScoreRef = useRef<string | null>(null);

  // Ответы специалистов на заключение ИИ (28.09.2026): нужны и самому заключению (кто уже
  // согласился, не упал ли пересмотр), и блоку «Комментарии» за иконкой в шапке.
  const [feedback, setFeedback] = useState<AiFeedback[] | null>(null);
  const [isCommentsOpen, setIsCommentsOpen] = useState(false);

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
    setScore(null);
    setFeedback(null);
    setIsCommentsOpen(false);
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

  const loadCard = useCallback(
    async (refresh = false) => {
      if (refresh) setIsCardRefreshing(true);
      else setIsCardLoading(true);
      try {
        const data = await api.get<TenderCard>(
          `/tenders/${tender.id}/card${refresh ? "?refresh=true" : ""}`,
        );
        setCard(data);
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

  const loadRequirements = useCallback(async () => {
    setRequirements(await api.get<Requirement[]>(`/tenders/${tender.id}/requirements`));
  }, [tender.id]);

  const loadAnalysisJob = useCallback(async () => {
    // Анализ выполняется и отдельной задачей, и первым шагом полного разбора — нужен
    // последний из них, каким бы он ни был.
    const jobs = await api.get<BackgroundJob[]>(
      `/jobs?tender_id=${tender.id}&kind=tender_analysis,tender_full_review&limit=1`,
    );
    setAnalysisJob(jobs[0] ?? null);
  }, [tender.id]);

  const loadFeedback = useCallback(async () => {
    try {
      setFeedback(await api.get<AiFeedback[]>(`/tenders/${tender.id}/ai-score/feedback`));
    } catch {
      setFeedback([]);
    }
  }, [tender.id]);

  useEffect(() => {
    void loadFeedback();
  }, [loadFeedback]);

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
        `Добавлено файлов: ${added.length} — запустите «Разобрать закупку», чтобы учесть их`,
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
  const startJob = useCallback(
    async (kind: JobKindKey) => {
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
    },
    [tender.id],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let data: AiProfileScore | null;
      let active: BackgroundJob[];
      try {
        [data, active] = await Promise.all([
          api.get<AiProfileScore | null>(`/tenders/${tender.id}/ai-score`),
          api.get<BackgroundJob[]>(`/jobs?tender_id=${tender.id}&active_only=true&limit=1`),
        ]);
      } catch {
        // Отсутствие оценки — обычное состояние; ошибку показываем только при расчёте.
        return;
      }
      if (cancelled) return;
      setScore(data);

      // Разбор идёт минуту-две, и за это время человек успевает переключиться на другую
      // закупку: карточка пересоздаётся, опрос задачи умирает вместе с прежней (жалоба
      // 25.09.2026 — оценка 63% есть, а в шапке «не считалась» и в списке нет галочки).
      // Вернувшись, продолжаем следить за той же задачей, а не запускаем вторую.
      const running = active[0];
      const runningKind = running ? JOB_KIND_KEYS[running.kind] : undefined;
      if (running && runningKind) {
        autoScoreRef.current = tender.id;
        setRunningAction(runningKind);
        setJobId(running.id);
        setJobProgress(running.message);
        return;
      }

      if (data === null) {
        if (autoScoreRef.current === tender.id) return;
        autoScoreRef.current = tender.id;
        // Без требований оценка считалась бы «только по карточке, точность ниже» — так
        // и получались 34% по закупке, ТЗ которой никто не читал. Поэтому первый заход
        // в карточку запускает полный разбор; если требования уже есть — только оценку.
        void startJob(tender.requirements_count > 0 ? "ai-score" : "review");
        return;
      }

      // Тендер пришёл из списка, загруженного до конца разбора: шапка и знак решения
      // отстали бы от панели оценки. Перечитываем — и список получит свежую строку.
      if (
        percentValue(tender.ai_score) !== percentValue(data.overall_score) ||
        tender.ai_decision !== data.decision
      ) {
        const updated = await api.get<Tender>(`/tenders/${tender.id}`);
        if (!cancelled) applyTender(updated);
      }
    })();
    void api
      .get<CompanyProfile | null>("/company-profile")
      .then((data) => {
        if (!cancelled) setProfile(data);
      })
      .catch(() => setProfile(null));
    return () => {
      cancelled = true;
    };
    // requirements_count, ai_score и ai_decision намеренно не в зависимостях: они меняются
    // после разбора, а решать про автозапуск и сверять шапку нужно один раз — при открытии.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tender.id, startJob]);

  useEffect(() => {
    if (!jobId || runningAction === null) return;
    let cancelled = false;

    const finish = async (job: BackgroundJob) => {
      setRunningAction(null);
      setJobId(null);
      setJobProgress(null);
      if (job.status === "error") {
        // Сбой расчёта оценки показывается в её блоке: он запускается сам при открытии, и
        // общая строка ошибок карточки над вкладками для него — сообщение «ни о чём».
        if (job.kind === "ai_profile_score" || job.kind === "ai_feedback") {
          setScoreError(job.message ?? "Расчёт не удался");
          if (job.kind === "ai_feedback") await loadFeedback();
        } else setError(job.message ?? "Операция завершилась ошибкой");
        return;
      }
      setActionMessage(job.message);
      applyTender(await api.get<Tender>(`/tenders/${tender.id}`));
      if (job.kind === "tender_full_review" || job.kind === "tender_analysis") {
        // Анализ сам скачивает документацию с площадки — без перечитывания вкладка
        // «Документы» до перезагрузки страницы показывала бы «Документов нет».
        setDocuments(await api.get<TenderDocument[]>(`/tenders/${tender.id}/documents`));
      }
      if (job.kind === "tender_full_review") {
        // Обновляется всё, что разбор мог изменить; вкладка не переключается — итог
        // (оценка и знак решения) виден на «Основном», где человек и находится.
        setAnalysisJob(job);
        setRequirements(null);
        setMatrix(null);
        setScore(await api.get<AiProfileScore | null>(`/tenders/${tender.id}/ai-score`));
        await loadExtra();
        if (activeTab === "requirements" || activeTab === "compliance") await loadRequirements();
        if (activeTab === "compliance") await loadMatrix();
      } else if (job.kind === "tender_analysis") {
        // Анализ сразу достраивает матрицу — прежняя в памяти карточки устарела.
        setMatrix(null);
        await loadRequirements();
        setAnalysisJob(job);
        setActiveTab("requirements");
      } else if (job.kind === "tender_evaluation") {
        await loadMatrix();
        setActiveTab("compliance");
      } else if (job.kind === "ai_feedback") {
        // Пересмотр по замечанию: новое заключение и ответ модели — в ленте «Комментарии».
        setActionMessage(null);
        await reloadPrepared();
        setScore(await api.get<AiProfileScore | null>(`/tenders/${tender.id}/ai-score`));
        await loadFeedback();
      } else {
        await reloadPrepared();
        setScore(await api.get<AiProfileScore | null>(`/tenders/${tender.id}/ai-score`));
        await loadExtra();
        await loadFeedback();
      }
    };

    // Пересчёт оценки сам достраивает анализ и матрицу, если их не было (28.09.2026), —
    // вкладки «Требования» и «Соответствие» перечитываются, а не показывают прежнее.
    const reloadPrepared = async () => {
      setRequirements(null);
      setMatrix(null);
      if (activeTab === "requirements" || activeTab === "compliance") await loadRequirements();
      if (activeTab === "compliance") await loadMatrix();
    };

    const interval = setInterval(async () => {
      try {
        const jobs = await api.get<BackgroundJob[]>(`/jobs?tender_id=${tender.id}&limit=5`);
        const job = jobs.find((item) => item.id === jobId);
        if (cancelled || !job) return;
        if (job.status === "queued" || job.status === "running") {
          setJobProgress(job.message);
          return;
        }
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
  }, [
    jobId,
    runningAction,
    tender.id,
    activeTab,
    applyTender,
    loadRequirements,
    loadMatrix,
    loadExtra,
    loadFeedback,
  ]);

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

  // Теги (замечание 17.09.2026): общие для команды метки. Отправляется полный набор —
  // сервер сам считает разницу и пишет её в историю.
  const setTags = async (tagIds: string[]) => {
    setError(null);
    try {
      applyTender(await api.put<Tender>(`/tenders/${tender.id}/tags`, { tag_ids: tagIds }));
      if (history !== null) await loadHistory();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить теги");
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

  /** Согласие с заключением ИИ — просто запись в «Комментариях». */
  const agreeWithScore = async () => {
    setScoreError(null);
    try {
      await api.post<AiFeedbackCreated>(`/tenders/${tender.id}/ai-score/feedback`, { kind: "agree" });
      await loadFeedback();
    } catch (err) {
      setScoreError(err instanceof ApiError ? err.message : "Не удалось сохранить ответ");
    }
  };

  /** Несогласие: замечание уходит модели на пересмотр, карточка следит за задачей. */
  const disagreeWithScore = async (text: string) => {
    setScoreError(null);
    try {
      const created = await api.post<AiFeedbackCreated>(`/tenders/${tender.id}/ai-score/feedback`, {
        kind: "disagree",
        text,
      });
      setFeedback((prev) => [created.feedback, ...(prev ?? [])]);
      if (created.job) {
        setRunningAction("ai-feedback");
        setJobId(created.job.id);
      }
    } catch (err) {
      setScoreError(err instanceof ApiError ? err.message : "Не удалось отправить замечание");
      throw err;
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
  // Вкладки делятся на основные и «Ещё» (правка 28.09.2026). Раньше в ряд выстраивалось до
  // четырнадцати вкладок с иконками, и половина пряталась за правым краем — человек не знал,
  // что они есть. На виду то, с чем решают об участии: сама закупка, документы, требования,
  // конкуренция, расчёт и заявка. Служебные разрезы (карточка источника, таблицы извещения,
  // история) — в меню, и если открыта одна из них, её имя показывается на кнопке меню.
  //
  // «Похожие» убрана (04.09.2026): вкладка требует посчитанных эмбеддингов по
  // представительному корпусу, до тех пор честно показывала «недостаточно данных» и только
  // занимала место в ряду. Похожие закупки при этом никуда не делись — они по-прежнему
  // перечислены в блоке «AI-оценка по профилю» как обоснование измерения History.
  const primaryTabs: { key: TabKey; label: string; badge?: number }[] = [
    { key: "overview", label: "Основное" },
    { key: "documents", label: "Документы", badge: documents?.length },
    {
      key: "requirements",
      label: "Требования",
      badge: requirements?.length ?? tender.requirements_count,
    },
    { key: "compliance", label: "Конкуренция" },
    { key: "calculation", label: "Расчёт" },
    { key: "application", label: "Заявка" },
  ];
  const tableTabs: { key: TabKey; label: string }[] = [
    { key: "lots", label: "Лоты" },
    { key: "changes", label: "Изменения" },
    { key: "protocols", label: "Протоколы" },
    { key: "contracts", label: "Договоры" },
    { key: "events", label: "Журнал событий" },
  ];
  const moreTabs: { key: TabKey; label: string; badge?: number }[] = [
    { key: "extra", label: "Дополнительно" },
    { key: "card", label: "Карточка закупки" },
    ...tableTabs
      .filter((tab) => cardTable(tab.key))
      .map((tab) => ({ ...tab, badge: cardTableCount(tab.key) })),
    { key: "history", label: "История" },
  ];
  const activeMoreTab = moreTabs.find((tab) => tab.key === activeTab);

  const overall = percentValue(tender.ai_score);
  const iconButton =
    "flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-white/5 hover:text-zinc-200";

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {isCommentsOpen && (
        <TenderCommentsDrawer items={feedback} onClose={() => setIsCommentsOpen(false)} />
      )}
      {/* Поле выбора файла — вне вкладок: «Приложить файл» есть и в меню шапки. */}
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
      {/* Шапка карточки (переделана 28.09.2026). Было три яруса: бейджи, заголовок с
          тегами и отдельный ряд кнопок (этап, релевантность, разбор). Теперь заголовок
          читается первым, а действия — иконками в одну строку справа: они нужны по разу на
          закупку, а занимали сотню пикселей высоты у каждой. */}
      <div className="shrink-0 px-6 pt-4">
        <div className="mb-2 flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2 text-xs text-zinc-500">
            <span className="font-medium text-zinc-400">{tender.source.name}</span>
            <span className="text-zinc-700">·</span>
            <span className="truncate tabular-nums">№ {tender.external_id}</span>
          </div>

          <div className="flex shrink-0 items-center gap-0.5">
            {/* Релевантность — сегментом из двух иконок: состояние видно по подсветке, и
                отдельный бейдж «Не проверен» в заголовке больше не нужен. */}
            <div className="mr-1 flex items-center rounded-lg border border-white/[0.08] p-0.5">
              <button
                onClick={() => void setRelevance("confirmed")}
                disabled={tender.relevance_status === "confirmed"}
                title={
                  tender.relevance_status === "confirmed"
                    ? "Отмечена как релевантная"
                    : "Отметить как релевантную"
                }
                aria-pressed={tender.relevance_status === "confirmed"}
                className={`flex h-7 items-center gap-1 rounded-md px-2 text-xs transition-colors ${
                  tender.relevance_status === "confirmed"
                    ? "bg-emerald-500/15 text-emerald-300"
                    : "text-zinc-500 hover:bg-white/5 hover:text-emerald-300"
                }`}
              >
                <BadgeCheck size={14} />
                {tender.relevance_status === "confirmed" && "Релевантна"}
              </button>
              <button
                onClick={() => void setRelevance("rejected")}
                disabled={tender.relevance_status === "rejected"}
                title={
                  tender.relevance_status === "rejected"
                    ? "Отмечена как неактуальная"
                    : "Отметить как неактуальную"
                }
                aria-pressed={tender.relevance_status === "rejected"}
                className={`flex h-7 items-center gap-1 rounded-md px-2 text-xs transition-colors ${
                  tender.relevance_status === "rejected"
                    ? "bg-red-500/15 text-red-300"
                    : "text-zinc-500 hover:bg-white/5 hover:text-red-300"
                }`}
              >
                <Ban size={14} />
                {tender.relevance_status === "rejected" && "Неактуальна"}
              </button>
            </div>
            {/* «Комментарии» — ответы специалистов на заключение ИИ (28.09.2026): не основной
                блок, поэтому за иконкой; число — сколько ответов уже есть. */}
            <button
              onClick={() => setIsCommentsOpen(true)}
              title="Комментарии специалистов к заключению ИИ"
              className={`relative ${iconButton}`}
            >
              <MessageSquare size={15} />
              {feedback && feedback.length > 0 && (
                <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-indigo-500 px-1 text-[10px] font-medium tabular-nums text-snow">
                  {feedback.length}
                </span>
              )}
            </button>
            <button
              onClick={toggleBookmark}
              title={
                tender.is_bookmarked
                  ? "Убрать из избранного"
                  : "В избранное — чтобы вернуться к закупке позже"
              }
              aria-pressed={tender.is_bookmarked}
              className={
                tender.is_bookmarked
                  ? "flex h-8 w-8 items-center justify-center rounded-lg text-amber-400 transition-colors hover:bg-white/5"
                  : iconButton
              }
            >
              <Star size={16} fill={tender.is_bookmarked ? "currentColor" : "none"} />
            </button>
            {expandable && (
              <Link
                to={`/tenders/${tender.id}`}
                title="Развернуть на отдельную страницу (средней кнопкой — в новой вкладке)"
                className={iconButton}
              >
                <Maximize2 size={15} />
              </Link>
            )}
            <DropdownMenu
              title="Другие действия"
              buttonClassName={iconButton}
              trigger={<MoreHorizontal size={16} />}
              items={[
                {
                  key: "review",
                  label: runningAction === "review" ? "Разбор идёт…" : "Разобрать закупку заново",
                  icon: <Sparkles size={14} />,
                  hint: REVIEW_HINT,
                  disabled: runningAction !== null,
                  onSelect: () => void startJob("review"),
                },
                {
                  key: "tag",
                  label: "Поставить тег",
                  icon: <TagIcon size={14} />,
                  onSelect: () => setIsTagPickerOpen(true),
                },
                {
                  key: "upload",
                  label: "Приложить файл",
                  icon: <Upload size={14} />,
                  hint: "Проект договора, ТЗ или спецификация, присланные заказчиком",
                  disabled: isUploading,
                  onSelect: () => uploadInputRef.current?.click(),
                },
                {
                  key: "history",
                  label: "История и комментарии",
                  icon: <History size={14} />,
                  onSelect: () => setActiveTab("history"),
                },
              ]}
            />
            {onClose && (
              <button onClick={onClose} title="Закрыть" className={iconButton}>
                <X size={17} />
              </button>
            )}
          </div>
        </div>

        <div className="flex items-start gap-2.5">
          <DecisionMark decision={tender.ai_decision} size="md" className="mt-0.5" />
          <h2 className="text-lg font-semibold leading-snug tracking-tight text-white">
            {tender.title}
          </h2>
        </div>

        {/* Строка свойств: оценка, этап и теги — то, что меняется по ходу работы. Этап —
            выпадающим списком без подписи: список сам по себе читается как «этап», а что
            значит выбранный — в подсказке. */}
        <div className="relative mt-3 flex flex-wrap items-center gap-1.5">
          <span
            className={`rounded-md border px-2 py-1 text-[11px] font-medium tabular-nums ${scoreBadgeClass(overall)}`}
            title="Итоговая AI-оценка по профилю (раздел 5.5.1 ТЗ)"
          >
            {overall === null ? "AI-оценка —" : `AI ${overall}%`}
          </span>
          <select
            value={tender.stage}
            onChange={(e) => void changeStage(e.target.value as TenderStage)}
            title={stageHint(tender.stage) ?? "Этап работы с закупкой"}
            className="cursor-pointer rounded-md border border-white/[0.08] bg-transparent py-1 pl-2 pr-6 text-[11px] text-zinc-300 outline-none hover:border-white/20 focus:border-indigo-500"
          >
            {STAGE_ORDER.map((stage) => (
              <option key={stage} value={stage} className="bg-zinc-900">
                {STAGE_LABELS[stage]}
              </option>
            ))}
          </select>
          {tender.tags.map((tag) => (
            <TagChip
              key={tag.id}
              tag={tag}
              onClick={() =>
                void setTags(tender.tags.filter((t) => t.id !== tag.id).map((t) => t.id))
              }
              title={`${tag.name} — нажмите, чтобы снять`}
            />
          ))}
          <button
            onClick={() => setIsTagPickerOpen((v) => !v)}
            className={`inline-flex h-6 w-6 items-center justify-center rounded-md transition-colors ${
              isTagPickerOpen
                ? "bg-white/10 text-indigo-300"
                : "text-zinc-600 hover:bg-white/5 hover:text-zinc-300"
            }`}
            title="Поставить тег"
          >
            <TagIcon size={12} />
          </button>
          {isTagPickerOpen && (
            <TagPicker
              selectedIds={tender.tags.map((tag) => tag.id)}
              onChange={(ids) => void setTags(ids)}
              onClose={() => setIsTagPickerOpen(false)}
              anchorClassName="left-0 top-full"
            />
          )}
        </div>

        {/* Вкладки — подчёркиванием, без иконок и плашек: десяток иконок в ряд шумел
            сильнее, чем сами названия. */}
        <div className="mt-3 flex items-end gap-1 border-b border-white/[0.08]">
          <div className="flex min-w-0 flex-1 items-end gap-5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {primaryTabs.map((tab) => {
              const isActive = tab.key === activeTab;
              return (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`-mb-px flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 pb-2.5 pt-1 text-sm transition-colors ${
                    isActive
                      ? "border-indigo-400 text-white"
                      : "border-transparent text-zinc-500 hover:text-zinc-200"
                  }`}
                >
                  {tab.label}
                  {tab.badge !== undefined && tab.badge > 0 && (
                    <span className="text-[11px] tabular-nums text-zinc-500">{tab.badge}</span>
                  )}
                </button>
              );
            })}
          </div>
          <DropdownMenu
            title="Другие разделы закупки"
            buttonClassName={`-mb-px flex shrink-0 items-center gap-1 whitespace-nowrap border-b-2 pb-2.5 pt-1 text-sm transition-colors ${
              activeMoreTab
                ? "border-indigo-400 text-white"
                : "border-transparent text-zinc-500 hover:text-zinc-200"
            }`}
            trigger={
              <>
                {activeMoreTab ? activeMoreTab.label : "Ещё"}
                <ChevronDown size={14} />
              </>
            }
            items={moreTabs.map((tab) => ({
              key: tab.key,
              label: (
                <span className="flex items-center justify-between gap-3">
                  {tab.label}
                  {tab.badge !== undefined && tab.badge > 0 && (
                    <span className="text-[11px] tabular-nums text-zinc-500">{tab.badge}</span>
                  )}
                </span>
              ),
              active: tab.key === activeTab,
              onSelect: () => setActiveTab(tab.key),
            }))}
          />
        </div>
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
            {runningAction === "review"
              ? "Идёт полный разбор закупки"
              : runningAction === "analyze"
                ? "Идёт анализ документов на сервере"
                : runningAction === "evaluate"
                  ? "Идёт расчёт соответствия на сервере"
                  : runningAction === "ai-feedback"
                    ? "ИИ пересматривает заключение по замечанию специалиста"
                    : "Идёт разбор ИИ по профилю компании"}
            {/* Ход задачи от сервера (28.09.2026): «Матрица соответствия: 6 из 17
                производителей» вместо безымянного индикатора на десять минут. */}
            {jobProgress ? ` — ${jobProgress.replace(/…$/, "")}.` : "."}
            <span className="text-indigo-300/60">Можно перейти к другой закупке — работа продолжится.</span>
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
              isReviewing={runningAction === "ai-feedback"}
              progress={runningAction === "ai-score" ? jobProgress : null}
              onRun={() => void startJob("ai-score")}
              error={scoreError}
              feedback={feedback}
              onAgree={agreeWithScore}
              onDisagree={disagreeWithScore}
            />
            <TenderOverviewTab tender={tender} onSave={saveChanges} />
          </div>
        )}

        {activeTab === "extra" && (
          <TenderExtraTab
            data={extra}
            isLoading={isExtraLoading}
            isRunning={
              isExtraRunning || runningAction === "ai-score" || runningAction === "review"
            }
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
                      message:
                        analysisJob?.kind === "tender_full_review"
                          ? ((analysisJob.payload?.analysis as string | undefined) ?? null)
                          : (analysisJob?.message ?? null),
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
                requirementsCount={
                  requirements === null
                    ? null
                    : requirements.filter((item) => item.kind === "product").length
                }
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
