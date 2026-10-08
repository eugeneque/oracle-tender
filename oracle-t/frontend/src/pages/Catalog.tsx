import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  Boxes,
  ChevronRight,
  ExternalLink,
  Link2,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Upload,
} from "lucide-react";

import { ApiError, api, uploadFile } from "../api/client";
import type {
  CatalogAutofillStatus,
  CatalogImportOutcome,
  CatalogSite,
  CatalogTask,
  LinkSiTypesOutcome,
  Manufacturer,
  Product,
  SiType,
} from "../api/types";
import { AppShell } from "../components/AppShell";
import { CatalogDocumentsSection } from "../components/catalog/CatalogDocumentsSection";
import { ProductDrawer } from "../components/catalog/ProductDrawer";
import { ProductMatrix } from "../components/catalog/ProductMatrix";
import { RegistryLearningSection } from "../components/catalog/RegistryLearningSection";
import { SiTypeGroups } from "../components/catalog/SiTypeGroups";
import { siTypeGroup } from "../components/catalog/siTypeGroup";
import { UpperSoftwareSection } from "../components/catalog/UpperSoftwareSection";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../context/useAuth";
import { formatDate } from "../utils/format";

// Каталог продукции (переделан 28.09.2026 по замечанию «слишком много блоков друг над
// другом, непонятно, как всё связано»). Раньше коды СИ, модели, обучение, документы, ПО
// верхнего уровня и характеристики модели шли одной лентой. Теперь у производителя пять
// шагов в порядке, в котором наполняется каталог, — у каждого подпись, что в нём и что
// делать дальше, — а модель открывается отдельным окном.

type StepKey = "si" | "models" | "learning" | "documents" | "software";

const STEPS: { key: StepKey; title: string; caption: string }[] = [
  {
    key: "si",
    title: "Коды СИ",
    caption: "Госреестр ФГИС",
  },
  {
    key: "models",
    title: "Модели",
    caption: "Приборы и характеристики",
  },
  {
    key: "learning",
    title: "Обучение",
    caption: "Аршин и руководства",
  },
  {
    key: "documents",
    title: "Документы",
    caption: "Актуальность",
  },
  {
    key: "software",
    title: "ПО верхнего уровня",
    caption: "Поддержка в АСКУЭ",
  },
];

function readStep(): StepKey {
  try {
    const stored = localStorage.getItem("catalog.step");
    return STEPS.some((s) => s.key === stored) ? (stored as StepKey) : "models";
  } catch {
    return "models";
  }
}

// Порядок списка производителей: по доле рынка (как отдаёт сервер, без доли — в конце) или по
// алфавиту. Выбор запоминается в браузере.
type ManufacturerSort = "share" | "alpha";

function readManufacturerSort(): ManufacturerSort {
  try {
    return localStorage.getItem("catalog.manufacturerSort") === "alpha" ? "alpha" : "share";
  } catch {
    return "share";
  }
}

// Скрывать ли снятые с производства в таблице моделей (29.09.2026). Настройка одна на все
// производителей и запоминается в браузере: кто её включил, смотрит только действующий ряд.
function readHideDiscontinued(): boolean {
  try {
    return localStorage.getItem("catalog.hideDiscontinued") === "1";
  } catch {
    return false;
  }
}

const manufacturerTitle = (m: Manufacturer) => m.brand_name ?? m.legal_name;

// Автозаполнение каталога (28.09.2026): сервер раз в сутки опрашивает все источники по
// каждому производителю по очереди — ФГИС, сайт, Аршин, руководства. Здесь — значок хода
// опроса в списке и ручной запуск.
const AUTOFILL_STEP_LABELS: Record<string, string> = {
  si_search: "коды СИ в ФГИС",
  site: "сайт производителя",
  link: "привязка кодов СИ",
  registry: "модели из реестра",
  relink: "привязка исполнений",
};

const isAutofillActive = (s?: CatalogAutofillStatus) => s?.status === "queued" || s?.status === "running";

function autofillTitle(s: CatalogAutofillStatus): string {
  if (s.status === "queued") return "В очереди на опрос источников";
  if (s.status === "running")
    return `Идёт опрос${s.current_step ? `: ${AUTOFILL_STEP_LABELS[s.current_step] ?? s.current_step}` : ""}`;
  const when = s.finished_at ? ` ${formatDate(s.finished_at)}` : "";
  if (s.status === "error") return `Опрос${when} не удался: ${s.message ?? "подробности в журнале"}`;
  if (s.empty) {
    const why = s.failed_steps.length
      ? `не ответили: ${s.failed_steps.map((k) => AUTOFILL_STEP_LABELS[k] ?? k).join(", ")}`
      : "источники не нашли ни одного прибора";
    return `Опрошен${when}, моделей нет — ${why}`;
  }
  if (s.failed_steps.length)
    return `Опрошен${when}, но не всё: ${s.failed_steps.map((k) => AUTOFILL_STEP_LABELS[k] ?? k).join(", ")} — подробности в журнале`;
  if (s.enriching) return `Опрошен${when}: приборы и коды СИ заведены, характеристики дополняются по Аршину и руководствам`;
  return `Опрошен${when}: каталог заполнен`;
}

function AutofillMark({ status }: { status?: CatalogAutofillStatus }) {
  if (!status) return null;
  const title = autofillTitle(status);
  if (status.status === "running")
    return (
      <span title={title} className="inline-flex text-indigo-400">
        <Search size={12} className="catalog-searching" />
      </span>
    );
  if (status.status === "queued")
    return (
      <span title={title} className="inline-flex text-zinc-500">
        <Search size={12} />
      </span>
    );
  if (status.status === "error" || status.empty)
    return (
      <span title={title} className="inline-flex text-red-400">
        <AlertTriangle size={12} />
      </span>
    );
  return (
    <span title={title} className={`inline-flex ${status.failed_steps.length ? "text-amber-400" : "text-emerald-400"}`}>
      <Check size={13} strokeWidth={2.5} />
    </span>
  );
}

function ToolbarButton({
  onClick,
  disabled,
  busy,
  icon,
  title,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
  icon: React.ReactNode;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
    >
      {busy ? <Loader2 size={13} className="animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function CatalogPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [manufacturers, setManufacturers] = useState<Manufacturer[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [siTypes, setSiTypes] = useState<SiType[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [openProductId, setOpenProductId] = useState<string | null>(null);
  const [step, setStep] = useState<StepKey>(readStep);
  const [manufacturerSort, setManufacturerSort] = useState<ManufacturerSort>(readManufacturerSort);
  const [hideDiscontinued, setHideDiscontinued] = useState(readHideDiscontinued);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newModelName, setNewModelName] = useState("");
  // Сайты, каталоги которых система умеет обходить. Кнопка обхода показывается только у тех
  // производителей, для чьего сайта есть разобранный профиль.
  const [catalogSites, setCatalogSites] = useState<CatalogSite[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [autofill, setAutofill] = useState<Record<string, CatalogAutofillStatus>>({});
  const autofillActive = useMemo(() => Object.values(autofill).some(isAutofillActive), [autofill]);
  // Ход опроса для полосы над списком: сколько производителей этого прогона уже опрошено,
  // кого опрашивают прямо сейчас и на каком шаге, скольким ещё дополняются характеристики.
  // «Этот прогон» начался, когда встала в очередь самая ранняя ещё не выполненная задача;
  // опрошенными в нём считаются закончившие после этого момента (по времени окончания, а не
  // постановки — иначе закончившие раньше соседей по очереди выпадали из счёта).
  const autofillProgress = useMemo(() => {
    const rows = Object.values(autofill);
    const active = rows.filter(isAutofillActive);
    const enriching = rows.filter((r) => !isAutofillActive(r) && r.enriching).length;
    const running = rows.find((r) => r.status === "running");
    const current = running ? manufacturers.find((m) => m.id === running.manufacturer_id) : undefined;
    if (!active.length) return { done: 0, total: 0, current: null, step: null, enriching };
    // Сравнение моментов, а не строк: время постановки приходит в поясе базы, окончания — в UTC.
    const since = Math.min(...active.map((r) => Date.parse(r.created_at)));
    const done = rows.filter((r) => !isAutofillActive(r) && r.finished_at != null && Date.parse(r.finished_at) >= since).length;
    return {
      done,
      total: done + active.length,
      current: current ? manufacturerTitle(current) : null,
      step: running?.current_step ? AUTOFILL_STEP_LABELS[running.current_step] ?? running.current_step : null,
      enriching,
    };
  }, [autofill, manufacturers]);
  // Форма производителя (администратор): добавить нового или поправить долю рынка и сайт.
  // `editingId === "new"` — создание. Доля рынка вводится вместе с источником оценки.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [manufacturerDraft, setManufacturerDraft] = useState({
    legal_name: "",
    brand_name: "",
    website: "",
    market_share_pct: "",
    market_share_source: "",
  });

  const sortedManufacturers = useMemo(
    () =>
      manufacturerSort === "alpha"
        ? [...manufacturers].sort((a, b) => manufacturerTitle(a).localeCompare(manufacturerTitle(b), "ru"))
        : manufacturers,
    [manufacturers, manufacturerSort]
  );
  const selected = useMemo(() => manufacturers.find((m) => m.id === selectedId) ?? null, [manufacturers, selectedId]);
  const openProduct = useMemo(() => products.find((p) => p.id === openProductId) ?? null, [products, openProductId]);
  const selectedSite = useMemo(
    () => catalogSites.find((site) => site.manufacturer_id === selectedId) ?? null,
    [catalogSites, selectedId]
  );
  // «Ждут проверки» — только коды, по которым от человека требуется действие; истёкшие и
  // «не электросчётчики» — факты реестра, подтверждение их не меняет.
  const siTypesNeedingReview = useMemo(() => siTypes.filter((s) => siTypeGroup(s) === "pending").length, [siTypes]);
  const activeProducts = useMemo(() => products.filter((p) => p.status !== "discontinued").length, [products]);
  const discontinuedProducts = products.length - activeProducts;
  const visibleProducts = useMemo(
    () => (hideDiscontinued ? products.filter((p) => p.status !== "discontinued") : products),
    [products, hideDiscontinued],
  );
  const productsNeedingReview = useMemo(() => products.filter((p) => p.review_status === "needs_review").length, [products]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить действие");
    } finally {
      setBusy(null);
    }
  };

  useEffect(() => {
    void run("load", async () => {
      const [data, sites] = await Promise.all([
        api.get<Manufacturer[]>("/manufacturers"),
        api.get<CatalogSite[]>("/catalog/sites"),
      ]);
      setManufacturers(data);
      setCatalogSites(sites);
      // Список отсортирован по доле рынка, и МИРТЕК в нём не первый; открывать справочник
      // всё равно нужно со своей продукции.
      setSelectedId((current) => current ?? data.find((m) => m.is_mirtek)?.id ?? data[0]?.id ?? null);
    });
  }, []);

  const loadManufacturerData = async (manufacturerId: string) => {
    const [si, prods] = await Promise.all([
      api.get<SiType[]>(`/manufacturers/${manufacturerId}/si-types`),
      api.get<Product[]>(`/manufacturers/${manufacturerId}/products`),
    ]);
    setSiTypes(si);
    setProducts(prods);
  };

  useEffect(() => {
    if (!selectedId) return;
    setOpenProductId(null);
    setNotice(null);
    void run("manufacturer", () => loadManufacturerData(selectedId));
  }, [selectedId]);

  // Ход автозаполнения: пока кто-то в очереди или опрашивается — каждые 5 секунд, иначе раз
  // в минуту (ночной проход начинается сам). Когда опрос открытого производителя закончился,
  // его данные перечитываются — пользователь видит заполненное без перезагрузки.
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const autofillRef = useRef<Record<string, CatalogAutofillStatus>>({});
  const loadAutofill = useCallback(async () => {
    try {
      const rows = await api.get<CatalogAutofillStatus[]>("/catalog/autofill/status");
      const next: Record<string, CatalogAutofillStatus> = Object.fromEntries(rows.map((r) => [r.manufacturer_id, r]));
      const current = selectedIdRef.current;
      if (current && isAutofillActive(autofillRef.current[current]) && next[current] && !isAutofillActive(next[current])) {
        void loadManufacturerData(current).catch(() => undefined);
      }
      autofillRef.current = next;
      setAutofill(next);
    } catch {
      // статус опроса — подсказка, без него каталог работает
    }
  }, []);

  useEffect(() => {
    void loadAutofill();
    const timer = window.setInterval(() => void loadAutofill(), autofillActive ? 5000 : 60000);
    return () => window.clearInterval(timer);
  }, [autofillActive, loadAutofill]);

  const handleAutofillAll = () =>
    run("autofill-all", async () => {
      const { queued } = await api.post<{ queued: number }>("/catalog/autofill");
      setNotice(
        `Опрос каталога запущен: производителей в очереди — ${queued}. Они опрашиваются по одному; ` +
          "лупа в списке — идёт опрос, галочка — готово. Раз в сутки это происходит само."
      );
      await loadAutofill();
    });

  const handleAutofillOne = () =>
    run("autofill-one", async () => {
      await api.post<CatalogTask>(`/manufacturers/${selectedId}/autofill`);
      await loadAutofill();
    });

  const closeProduct = useCallback(() => setOpenProductId(null), []);

  const switchStep = (next: StepKey) => {
    setStep(next);
    try {
      localStorage.setItem("catalog.step", next);
    } catch {
      // без сохранения — шаг просто не запомнится
    }
  };

  const switchManufacturerSort = (next: ManufacturerSort) => {
    setManufacturerSort(next);
    try {
      localStorage.setItem("catalog.manufacturerSort", next);
    } catch {
      // без сохранения — порядок просто не запомнится
    }
  };

  const toggleHideDiscontinued = (next: boolean) => {
    setHideDiscontinued(next);
    try {
      localStorage.setItem("catalog.hideDiscontinued", next ? "1" : "0");
    } catch {
      // Приватный режим — настройка просто не запомнится.
    }
  };

  const handleSearchSiTypes = () =>
    run("si-search", async () => {
      const found = await api.post<SiType[]>(`/manufacturers/${selectedId}/si-types/search`);
      setSiTypes(found);
      setNotice(
        found.length
          ? `Автопоиск ФГИС: найдено кодов СИ — ${found.length}. Подходящие подставлены к моделям; проверьте и подтвердите записи.`
          : "Автопоиск ФГИС не вернул результатов (реестр может быть недоступен — см. журнал)."
      );
      await loadManufacturerData(selectedId!);
    });

  // Обход ставится в очередь: сайты производителей отвечают по 2-6 секунд на страницу, и
  // каталог из двух сотен позиций обходится минутами.
  const handleSyncSite = (adapterKey: string) =>
    run("site-sync", async () => {
      await api.post<CatalogTask>(`/catalog/sites/${adapterKey}/sync`);
      setNotice(
        "Обход каталога запущен в фоне — полный каталог занимает минуты. Ход работы виден в разделе " +
          "«Логирование» настроек; обновите страницу, когда обход закончится."
      );
    });

  // Привязка моделей к кодам СИ — для случая «модели уже есть, а автопоиск в ФГИС запустили
  // только что»: иначе пришлось бы заново обходить сайт ради одной связи.
  const handleLinkSiTypes = () =>
    run("link-si", async () => {
      const outcome = await api.post<LinkSiTypesOutcome>(`/manufacturers/${selectedId}/link-si-types`);
      const parts = [`привязано ${outcome.linked}`];
      if (outcome.already_linked) parts.push(`было привязано ранее ${outcome.already_linked}`);
      if (outcome.not_found) parts.push(`без подходящего кода СИ ${outcome.not_found}`);
      if (outcome.needs_review) parts.push(`неоднозначно ${outcome.needs_review}`);
      setNotice(`Привязка кодов СИ: ${parts.join(", ")}.`);
      await loadManufacturerData(selectedId!);
    });

  const handleVerifySiType = (siType: SiType) =>
    run(`si-${siType.id}`, async () => {
      const updated = await api.patch<SiType>(`/si-types/${siType.id}`, { verified_by_user: !siType.verified_by_user });
      setSiTypes((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    });

  const handleFetchDescriptionType = (siType: SiType) =>
    run(`fetch-${siType.id}`, async () => {
      const updated = await api.post<SiType>(`/si-types/${siType.id}/fetch-description-type`);
      setSiTypes((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      setNotice(
        updated.has_description_type_text
          ? "Текст «Описание типа» загружен — можно извлекать характеристики."
          : "Документ не удалось загрузить (подробности в журнале)."
      );
    });

  const openManufacturerForm = (m: Manufacturer | null) => {
    setEditingId(m ? m.id : "new");
    setManufacturerDraft({
      legal_name: m?.legal_name ?? "",
      brand_name: m?.brand_name ?? "",
      website: m?.website ?? "",
      market_share_pct: m?.market_share_pct != null ? String(m.market_share_pct) : "",
      market_share_source: m?.market_share_source ?? "",
    });
  };

  const handleSaveManufacturer = () =>
    run("save-manufacturer", async () => {
      const pct = manufacturerDraft.market_share_pct.trim().replace(",", ".");
      const payload = {
        legal_name: manufacturerDraft.legal_name.trim(),
        brand_name: manufacturerDraft.brand_name.trim() || null,
        website: manufacturerDraft.website.trim() || null,
        market_share_pct: pct === "" ? null : Number(pct),
        market_share_source: manufacturerDraft.market_share_source.trim() || null,
      };
      if (payload.market_share_pct !== null && Number.isNaN(payload.market_share_pct)) {
        throw new ApiError(400, "Доля рынка — число в процентах, например 7 или 12.5");
      }
      const saved =
        editingId === "new"
          ? await api.post<Manufacturer>("/manufacturers", payload)
          : await api.patch<Manufacturer>(`/manufacturers/${editingId}`, payload);
      // Порядок списка зависит от доли — перечитываем целиком, а не подменяем строку.
      setManufacturers(await api.get<Manufacturer[]>("/manufacturers"));
      setEditingId(null);
      if (editingId === "new") setSelectedId(saved.id);
    });

  const handleCreateProduct = () =>
    run("create-product", async () => {
      const created = await api.post<Product>(`/manufacturers/${selectedId}/products`, { model_name: newModelName.trim() });
      setProducts((prev) => [...prev, created]);
      setNewModelName("");
      setOpenProductId(created.id);
    });

  const handleImport = (file: File) =>
    run("import", async () => {
      const outcome = await uploadFile<CatalogImportOutcome>("/catalog/import", file);
      if (selectedId) await loadManufacturerData(selectedId);
      const base = `Импорт: кодов СИ создано ${outcome.si_types_created}, обновлено ${outcome.si_types_updated}, моделей создано ${outcome.products_created}.`;
      setNotice(outcome.errors.length ? `${base} Ошибок в строках: ${outcome.errors.length} — ${outcome.errors[0]}` : base);
    });

  // Счётчики на вкладках — только там, где данные уже загружены страницей.
  const stepCount = (key: StepKey): React.ReactNode => {
    if (key === "si" && siTypes.length)
      return (
        <>
          {siTypes.length}
          {siTypesNeedingReview > 0 && <span className="ml-1 text-amber-400">⚠ {siTypesNeedingReview}</span>}
        </>
      );
    if (key === "models" && products.length) return products.length;
    return null;
  };

  const currentStep = STEPS.find((s) => s.key === step) ?? STEPS[1];
  const stepIndex = STEPS.indexOf(currentStep);

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl px-8 py-8">
        <PageHeader
          breadcrumb={["Sova", "Каталог продукции"]}
          title="Каталог продукции"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <Boxes size={18} />
            </span>
          }
        />

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">{error}</div>
        )}
        {notice && (
          <div className="mb-4 flex items-start justify-between gap-4 rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-4 py-2.5 text-sm text-indigo-300">
            <span>{notice}</span>
            <button onClick={() => setNotice(null)} className="shrink-0 text-indigo-400 hover:text-indigo-200">
              ×
            </button>
          </div>
        )}

        <div className="grid gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
          {/* Производители */}
          <aside className="self-start rounded-xl border border-white/[0.08] bg-white/[0.03] lg:sticky lg:top-6">
            <div className="flex items-center justify-between border-b border-white/[0.08] px-4 py-3">
              <h2 className="text-sm font-semibold text-zinc-100">Производители</h2>
              {isAdmin && (
                <div className="flex gap-1.5">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".csv,text/csv"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (file) void handleImport(file);
                      e.target.value = "";
                    }}
                  />
                  <button
                    onClick={() => openManufacturerForm(null)}
                    title="Добавить производителя"
                    className="rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-300 hover:bg-white/5"
                  >
                    <Plus size={12} />
                  </button>
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={busy === "import"}
                    title="CSV-импорт: производитель, код СИ, модель"
                    className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                  >
                    {busy === "import" ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                    CSV
                  </button>
                </div>
              )}
            </div>
            {editingId && (
              <div className="space-y-1.5 border-b border-white/[0.08] px-4 py-3">
                <div className="text-[11px] font-medium text-zinc-300">
                  {editingId === "new" ? "Новый производитель" : "Правка производителя"}
                </div>
                {(
                  [
                    ["legal_name", "Юридическое название"],
                    ["brand_name", "Бренд"],
                    ["website", "Сайт (каталог)"],
                    ["market_share_pct", "Доля рынка, %"],
                    ["market_share_source", "Источник оценки доли"],
                  ] as const
                ).map(([field, label]) => (
                  <input
                    key={field}
                    value={manufacturerDraft[field]}
                    onChange={(e) => setManufacturerDraft((d) => ({ ...d, [field]: e.target.value }))}
                    placeholder={label}
                    title={label}
                    className="w-full rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
                  />
                ))}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={handleSaveManufacturer}
                    disabled={!manufacturerDraft.legal_name.trim() || busy === "save-manufacturer"}
                    className="rounded-lg bg-indigo-500/20 px-2.5 py-1.5 text-xs text-indigo-200 hover:bg-indigo-500/30 disabled:opacity-50"
                  >
                    Сохранить
                  </button>
                  <button
                    onClick={() => setEditingId(null)}
                    className="rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-400 hover:bg-white/5"
                  >
                    Отмена
                  </button>
                </div>
              </div>
            )}
            {(isAdmin || autofillActive || autofillProgress.enriching > 0) && (
              <div className="space-y-1.5 border-b border-white/[0.08] px-3 py-2">
                {autofillActive ? (
                  <div className="rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] px-2.5 py-2" aria-live="polite">
                    <div className="flex items-center gap-1.5 text-xs text-zinc-200">
                      <Search size={12} className="catalog-searching shrink-0 text-indigo-400" />
                      Опрошено {autofillProgress.done} из {autofillProgress.total}
                    </div>
                    <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.06]">
                      <div
                        className="h-full rounded-full bg-indigo-400 transition-[width] duration-500"
                        style={{ width: `${autofillProgress.total ? (autofillProgress.done / autofillProgress.total) * 100 : 0}%` }}
                      />
                    </div>
                    {autofillProgress.current && (
                      <div className="mt-1.5 truncate text-[11px] text-zinc-500">
                        Сейчас: <span className="text-zinc-300">{autofillProgress.current}</span>
                        {autofillProgress.step && <> — {autofillProgress.step}</>}
                      </div>
                    )}
                  </div>
                ) : (
                  isAdmin && (
                    <button
                      onClick={handleAutofillAll}
                      disabled={busy === "autofill-all"}
                      title="Опросить все источники по всем производителям по очереди: ФГИС, сайты, реестр, затем характеристики по Аршину и руководствам. Раз в сутки это происходит само."
                      className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                    >
                      {busy === "autofill-all" ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                      Опросить весь каталог
                    </button>
                  )
                )}
                {autofillProgress.enriching > 0 && (
                  <div
                    className="flex items-center gap-1.5 px-1 text-[11px] text-zinc-500"
                    title="Приборы и коды СИ уже заведены; характеристики из «Описаний типа» и руководств дополняются вторым проходом — он дольше, потому что читает документы"
                  >
                    <Loader2 size={11} className="shrink-0 animate-spin" />
                    Характеристики дополняются: {autofillProgress.enriching}
                  </div>
                )}
              </div>
            )}
            <div className="flex items-center gap-1 border-b border-white/[0.08] px-3 py-2" role="radiogroup" aria-label="Порядок производителей">
              <span className="mr-auto pl-1 text-[11px] text-zinc-500">Порядок</span>
              {(
                [
                  ["share", "По доле рынка"],
                  ["alpha", "А–Я"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  role="radio"
                  aria-checked={manufacturerSort === key}
                  onClick={() => switchManufacturerSort(key)}
                  className={`rounded-md px-2 py-1 text-[11px] transition-colors ${
                    manufacturerSort === key ? "bg-white/[0.08] text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="max-h-[70vh] overflow-y-auto py-1">
              {/* По доле рынка — порядок сервера, без опубликованной доли — в конце. */}
              {sortedManufacturers.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setSelectedId(m.id)}
                  className={`flex w-full items-center justify-between py-2 pl-4 pr-3 text-left text-sm ${
                    m.id === selectedId ? "bg-indigo-500/10 text-indigo-300" : "text-zinc-300 hover:bg-white/5"
                  }`}
                >
                  <span className="truncate">
                    {manufacturerTitle(m)}
                    {m.is_mirtek && <span className="ml-1.5 text-[10px] text-emerald-400">мы</span>}
                  </span>
                  <span className="ml-2 flex shrink-0 items-center gap-1.5">
                  <AutofillMark status={autofill[m.id]} />
                  <span
                    className="font-mono text-[11px] tabular-nums text-zinc-500"
                    title={
                      m.market_share_pct != null
                        ? `Доля рынка ${m.market_share_pct} % — ${m.market_share_source ?? "источник не указан"}`
                        : "Доля рынка не опубликована"
                    }
                  >
                    {m.market_share_pct != null ? `${m.market_share_pct} %` : "—"}
                  </span>
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <div className="min-w-0 space-y-4">
            {selected && (
              <>
                {/* Производитель: кто это и главные цифры */}
                <div className="flex flex-wrap items-end justify-between gap-6 rounded-xl border border-white/[0.08] bg-white/[0.03] px-6 py-5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="truncate text-2xl font-semibold tracking-tight text-zinc-50">
                        {selected.brand_name ?? selected.legal_name}
                      </h2>
                      {isAdmin && (
                        <button
                          onClick={() => openManufacturerForm(selected)}
                          title="Изменить производителя: сайт, доля рынка"
                          className="rounded p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
                        >
                          <Pencil size={14} />
                        </button>
                      )}
                      {isAdmin && (
                        <button
                          onClick={handleAutofillOne}
                          disabled={busy === "autofill-one" || isAutofillActive(autofill[selected.id])}
                          title={
                            autofill[selected.id]
                              ? autofillTitle(autofill[selected.id])
                              : "Опросить все источники по этому производителю: ФГИС, сайт, Аршин, руководства"
                          }
                          className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-400 hover:bg-white/5 hover:text-zinc-200 disabled:opacity-60"
                        >
                          {isAutofillActive(autofill[selected.id]) ? (
                            <Search size={12} className={autofill[selected.id]?.status === "running" ? "catalog-searching" : ""} />
                          ) : busy === "autofill-one" ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <RefreshCw size={12} />
                          )}
                          {autofill[selected.id]?.status === "running"
                            ? "Идёт опрос"
                            : autofill[selected.id]?.status === "queued"
                              ? "В очереди"
                              : "Опросить"}
                        </button>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
                      {selected.brand_name && <span>{selected.legal_name}</span>}
                      {selected.website && (
                        <a
                          href={selected.website}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-indigo-400 hover:text-indigo-300"
                        >
                          {selected.website.replace(/^https?:\/\//, "").replace(/\/$/, "")} <ExternalLink size={11} />
                        </a>
                      )}
                      {selected.market_share_pct != null && (
                        <span title={selected.market_share_source ?? undefined}>доля рынка {selected.market_share_pct} %</span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-8">
                    {(
                      [
                        [activeProducts, "моделей в выпуске"],
                        [siTypes.length, "кодов СИ"],
                        [siTypesNeedingReview + productsNeedingReview, "ждут проверки"],
                      ] as const
                    ).map(([value, label]) => (
                      <div key={label}>
                        <div className="text-3xl font-semibold tabular-nums tracking-tight text-zinc-50">
                          {busy === "manufacturer" ? "…" : value}
                        </div>
                        <div className="text-[11px] text-zinc-500">{label}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Шаги: как наполняется каталог */}
                <nav className="grid grid-cols-2 gap-1 rounded-xl border border-white/[0.08] bg-white/[0.02] p-1 sm:grid-cols-5" aria-label="Шаги каталога">
                  {STEPS.map((s, index) => {
                    const active = s.key === step;
                    const count = stepCount(s.key);
                    return (
                      <button
                        key={s.key}
                        onClick={() => switchStep(s.key)}
                        aria-current={active ? "step" : undefined}
                        className={`flex items-start gap-2.5 rounded-lg px-3 py-2.5 text-left transition-colors ${
                          active ? "bg-indigo-500/15" : "hover:bg-white/[0.04]"
                        }`}
                      >
                        <span
                          className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                            active ? "bg-indigo-500 text-snow" : "border border-white/15 text-zinc-500"
                          }`}
                        >
                          {index + 1}
                        </span>
                        <span className="min-w-0">
                          <span className={`block text-sm font-medium leading-snug ${active ? "text-zinc-50" : "text-zinc-300"}`}>
                            {s.title}
                            {count !== null && <span className="ml-1.5 whitespace-nowrap font-normal tabular-nums text-zinc-500">{count}</span>}
                          </span>
                          <span className="hidden text-[11px] text-zinc-500 xl:block">{s.caption}</span>
                        </span>
                      </button>
                    );
                  })}
                </nav>

                <div className="flex items-start gap-2 px-1 text-xs leading-relaxed text-zinc-400">
                  <span className="whitespace-nowrap text-zinc-600">Шаг {stepIndex + 1} из {STEPS.length}.</span>
                  {stepIndex < STEPS.length - 1 && (
                    <button
                      onClick={() => switchStep(STEPS[stepIndex + 1].key)}
                      className="ml-auto flex shrink-0 items-center gap-0.5 whitespace-nowrap text-indigo-400 hover:text-indigo-300"
                    >
                      {STEPS[stepIndex + 1].title} <ChevronRight size={13} />
                    </button>
                  )}
                </div>

                {/* 1. Коды СИ */}
                {step === "si" && (
                  <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
                    {isAdmin && (
                      <div className="flex flex-wrap gap-2 border-b border-white/[0.08] px-5 py-3">
                        <ToolbarButton onClick={handleSearchSiTypes} disabled={busy === "si-search"} busy={busy === "si-search"} icon={<Search size={13} />}>
                          Автопоиск в ФГИС
                        </ToolbarButton>
                        <ToolbarButton
                          onClick={handleLinkSiTypes}
                          disabled={busy === "link-si"}
                          busy={busy === "link-si"}
                          icon={<Link2 size={13} />}
                          title="Сопоставить модели каталога с кодами СИ по обозначению типа (по артикулу — он различает заводские исполнения)"
                        >
                          Привязать к моделям
                        </ToolbarButton>
                      </div>
                    )}
                    <div className="px-5 py-3">
                      {siTypes.length === 0 ? (
                        <p className="text-xs text-zinc-500">Кодов СИ пока нет — запустите автопоиск или загрузите CSV.</p>
                      ) : (
                        <SiTypeGroups
                          siTypes={siTypes}
                          isAdmin={isAdmin}
                          busy={busy}
                          onVerify={handleVerifySiType}
                          onFetchDescriptionType={handleFetchDescriptionType}
                        />
                      )}
                    </div>
                  </div>
                )}

                {/* 2. Модели */}
                {step === "models" && (
                  <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
                    {isAdmin && (
                      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.08] px-5 py-3">
                        {selectedSite && (
                          <ToolbarButton
                            onClick={() => handleSyncSite(selectedSite.adapter_key)}
                            disabled={busy === "site-sync"}
                            busy={busy === "site-sync"}
                            icon={<RefreshCw size={13} />}
                            title={`Обойти каталог на ${selectedSite.base_url} и заполнить справочник (занимает несколько минут)`}
                          >
                            Обойти сайт производителя
                          </ToolbarButton>
                        )}
                        <div className="ml-auto flex gap-2">
                          <input
                            value={newModelName}
                            onChange={(e) => setNewModelName(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && newModelName.trim() && void handleCreateProduct()}
                            placeholder="Новая модель"
                            className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
                          />
                          <ToolbarButton
                            onClick={() => void handleCreateProduct()}
                            disabled={!newModelName.trim() || busy === "create-product"}
                            icon={<Plus size={13} />}
                          >
                            Добавить
                          </ToolbarButton>
                        </div>
                      </div>
                    )}
                    <div className="px-5 py-3">
                      {products.length > 0 && (
                        <label className="mb-3 flex w-fit cursor-pointer items-center gap-2 text-xs text-zinc-400 hover:text-zinc-200">
                          <input
                            type="checkbox"
                            checked={hideDiscontinued}
                            onChange={(e) => toggleHideDiscontinued(e.target.checked)}
                          />
                          Скрыть снятые с производства
                          <span className="tabular-nums text-zinc-600">{discontinuedProducts}</span>
                        </label>
                      )}
                      {products.length === 0 ? (
                        <p className="text-xs text-zinc-500">
                          Моделей пока нет — обойдите сайт производителя или заведите исполнения из реестра на шаге «Обучение».
                        </p>
                      ) : visibleProducts.length === 0 ? (
                        <p className="text-xs text-zinc-500">
                          Все модели производителя сняты с производства — снимите галочку, чтобы их увидеть.
                        </p>
                      ) : (
                        <ProductMatrix products={visibleProducts} siTypes={siTypes} selectedProductId={openProductId} onSelect={setOpenProductId} />
                      )}
                    </div>
                  </div>
                )}

                {/* 3. Обучение */}
                {step === "learning" && (
                  <RegistryLearningSection
                    manufacturerId={selectedId}
                    isAdmin={isAdmin}
                    onChanged={() => (selectedId ? loadManufacturerData(selectedId) : Promise.resolve())}
                  />
                )}

                {/* 4. Документы */}
                {step === "documents" && <CatalogDocumentsSection manufacturerId={selectedId} isAdmin={isAdmin} />}

                {/* 5. ПО верхнего уровня */}
                {step === "software" && <UpperSoftwareSection manufacturerId={selectedId} isAdmin={isAdmin} />}
              </>
            )}
          </div>
        </div>
      </div>

      {openProduct && (
        <ProductDrawer product={openProduct} siTypes={siTypes} isAdmin={isAdmin} onClose={closeProduct} />
      )}
    </AppShell>
  );
}
