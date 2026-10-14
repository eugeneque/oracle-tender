import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  ExternalLink,
  Globe,
  Link2,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Upload,
  Wand2,
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
import { CatalogRepairDialog } from "../components/catalog/CatalogRepairDialog";
import { ProductDrawer } from "../components/catalog/ProductDrawer";
import { ProductMatrix } from "../components/catalog/ProductMatrix";
import { RegistryLearningSection } from "../components/catalog/RegistryLearningSection";
import { SiteSyncProgress } from "../components/catalog/SiteSyncProgress";
import { SiTypeGroups } from "../components/catalog/SiTypeGroups";
import { siTypeGroup } from "../components/catalog/siTypeGroup";
import { UpperSoftwareSection } from "../components/catalog/UpperSoftwareSection";
import { DropdownMenu } from "../components/DropdownMenu";
import { PageHeader } from "../components/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { AI_COST_NOTE, Tooltip } from "../components/ui/Tooltip";
import { useAuth } from "../context/useAuth";
import { formatDate } from "../utils/format";

// Каталог продукции (переделан 28.09.2026 по замечанию «слишком много блоков друг над
// другом, непонятно, как всё связано»). Раньше коды СИ, модели, обучение, документы, ПО
// верхнего уровня и характеристики модели шли одной лентой. Теперь у производителя пять
// шагов в порядке, в котором наполняется каталог, — у каждого подпись, что в нём и что
// делать дальше, — а модель открывается отдельным окном.
//
// Редизайн 14.10.2026 (замечание: «слишком много всего, куча текста, куча кнопок»). Шаги
// стали обычными вкладками без номеров и подписей, «Модели» — первой: за ними приходят на
// страницу. Служебные действия (опрос, обход сайта, импорт, единый формат значений) ушли в
// меню «⋯» у производителя и над списком. Значок-«звёздочка» у кнопок с моделью ИИ убран:
// что действие тратит токены, сказано в подсказке. Зелёные галочки «опрошен» у каждого
// производителя убраны — в списке отмечается только то, что требует внимания.

type StepKey = "si" | "models" | "learning" | "documents" | "software";

const STEPS: { key: StepKey; title: string }[] = [
  { key: "models", title: "Модели" },
  { key: "si", title: "Коды СИ" },
  { key: "documents", title: "Документы" },
  { key: "software", title: "ПО верхнего уровня" },
  { key: "learning", title: "Реестр и руководства" },
];

// Что делает опрос производителя — в подсказке пункта меню. Модель ИИ вызывается только во
// втором проходе («Описания типа», руководства), и только в ручном запуске и раз в неделю:
// плановый опрос дважды в день идёт без неё (14.10.2026). Уже прочитанные документы
// повторно в модель не отправляются.
const AUTOFILL_HINT =
  "Коды СИ в ФГИС, каталог сайта, модели из реестра — без ИИ.\n" +
  "Затем характеристики из ещё не прочитанных «Описаний типа» и руководств — моделью ИИ.\n" +
  "Плановый опрос (06:00 и 20:00) идёт без ИИ, документы дочитываются раз в неделю.\n" +
  AI_COST_NOTE;

function readStep(): StepKey {
  try {
    const stored = localStorage.getItem("catalog.step");
    return STEPS.some((s) => s.key === stored) ? (stored as StepKey) : "models";
  } catch {
    return "models";
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

// В списке производителей отмечается только то, что требует внимания или идёт сейчас;
// успешно опрошенный производитель — без значка.
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
  if (status.failed_steps.length)
    return (
      <Tooltip content={title}>
        <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-label="опрошен не полностью" />
      </Tooltip>
    );
  return null;
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
  const [hideDiscontinued, setHideDiscontinued] = useState(readHideDiscontinued);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  // Обход сайта: `siteSyncKick` будит опрос очереди после нажатия, `siteSyncActive` — идёт ли.
  const [siteSyncKick, setSiteSyncKick] = useState(0);
  const [siteSyncActive, setSiteSyncActive] = useState(false);
  const [newModelName, setNewModelName] = useState("");
  const [addingModel, setAddingModel] = useState(false);
  const [repairOpen, setRepairOpen] = useState(false);
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
  // Форма производителя (администратор): добавить нового или поправить названия и сайт.
  // `editingId === "new"` — создание. Доля рынка убрана 13.10.2026 (замечание тендерного
  // отдела, п.11): была известна у пятерых и неточна; список — по алфавиту, как отдаёт сервер.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [manufacturerDraft, setManufacturerDraft] = useState({
    legal_name: "",
    brand_name: "",
    website: "",
  });
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
      setNotice(`Обновление каталога запущено: в очереди производителей — ${queued}. Ход виден над списком.`);
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
      setSiteSyncKick((k) => k + 1);
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
    });
  };

  const handleSaveManufacturer = () =>
    run("save-manufacturer", async () => {
      const payload = {
        legal_name: manufacturerDraft.legal_name.trim(),
        brand_name: manufacturerDraft.brand_name.trim() || null,
        website: manufacturerDraft.website.trim() || null,
      };
      const saved =
        editingId === "new"
          ? await api.post<Manufacturer>("/manufacturers", payload)
          : await api.patch<Manufacturer>(`/manufacturers/${editingId}`, payload);
      // Порядок списка зависит от названия — перечитываем целиком, а не подменяем строку.
      setManufacturers(await api.get<Manufacturer[]>("/manufacturers"));
      setEditingId(null);
      if (editingId === "new") setSelectedId(saved.id);
    });

  const handleCreateProduct = () =>
    run("create-product", async () => {
      const created = await api.post<Product>(`/manufacturers/${selectedId}/products`, { model_name: newModelName.trim() });
      setProducts((prev) => [...prev, created]);
      setNewModelName("");
      setAddingModel(false);
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
  const stepCount = (key: StepKey): number | null => {
    if (key === "si" && siTypes.length) return siTypes.length;
    if (key === "models" && products.length) return products.length;
    return null;
  };

  const selectedAutofill = selected ? autofill[selected.id] : undefined;
  const selectedAutofillActive = isAutofillActive(selectedAutofill);

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

        <div className="grid gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
          {/* Производители */}
          <aside className="self-start rounded-xl border border-white/[0.08] lg:sticky lg:top-6">
            <div className="flex items-center justify-between px-4 py-3">
              <h2 className="text-sm font-semibold text-zinc-100">Производители</h2>
              {isAdmin && (
                <>
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
                  <DropdownMenu
                    title="Действия с каталогом"
                    buttonClassName="flex h-7 w-7 items-center justify-center rounded-lg text-zinc-400 hover:bg-white/5 hover:text-zinc-100"
                    trigger={busy === "import" || busy === "autofill-all" ? <Loader2 size={15} className="animate-spin" /> : <MoreHorizontal size={16} />}
                    items={[
                      { key: "add", label: "Добавить производителя", icon: <Plus size={14} />, onSelect: () => openManufacturerForm(null) },
                      {
                        key: "import",
                        label: "Импорт из CSV",
                        icon: <Upload size={14} />,
                        hint: "Столбцы: производитель, код СИ, модель",
                        onSelect: () => fileInputRef.current?.click(),
                      },
                      {
                        key: "autofill-all",
                        label: "Обновить весь каталог",
                        icon: <RefreshCw size={14} />,
                        hint: `Все производители по очереди. Раз в сутки это происходит само.\n${AUTOFILL_HINT}`,
                        disabled: autofillActive,
                        onSelect: () => void handleAutofillAll(),
                      },
                      {
                        key: "repair",
                        label: "Привести значения к единому формату",
                        icon: <Wand2 size={14} />,
                        hint: "Класс точности, токи, фазы, обозначения — по правилам, без ИИ. Сначала покажет, что изменится.",
                        onSelect: () => setRepairOpen(true),
                      },
                    ]}
                  />
                </>
              )}
            </div>
            {editingId && (
              <div className="space-y-1.5 border-t border-white/[0.08] px-4 py-3">
                <div className="text-xs font-medium text-zinc-300">
                  {editingId === "new" ? "Новый производитель" : "Производитель"}
                </div>
                {(
                  [
                    ["legal_name", "Юридическое название"],
                    ["brand_name", "Бренд"],
                    ["website", "Сайт (каталог)"],
                  ] as const
                ).map(([field, label]) => (
                  <input
                    key={field}
                    value={manufacturerDraft[field]}
                    onChange={(e) => setManufacturerDraft((d) => ({ ...d, [field]: e.target.value }))}
                    placeholder={label}
                    aria-label={label}
                    className="w-full rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
                  />
                ))}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={handleSaveManufacturer}
                    disabled={!manufacturerDraft.legal_name.trim() || busy === "save-manufacturer"}
                    className="rounded-lg bg-indigo-500 px-2.5 py-1.5 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                  >
                    Сохранить
                  </button>
                  <button
                    onClick={() => setEditingId(null)}
                    className="rounded-lg px-2.5 py-1.5 text-xs text-zinc-400 hover:bg-white/5"
                  >
                    Отмена
                  </button>
                </div>
              </div>
            )}
            {autofillActive && (
              <div className="border-t border-white/[0.08] px-4 py-2.5" aria-live="polite">
                <div className="flex items-center justify-between text-xs text-zinc-400">
                  <span>Обновление каталога</span>
                  <span className="tabular-nums">
                    {autofillProgress.done} / {autofillProgress.total}
                  </span>
                </div>
                <div className="mt-1.5 h-0.5 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-indigo-400 transition-[width] duration-500"
                    style={{ width: `${autofillProgress.total ? (autofillProgress.done / autofillProgress.total) * 100 : 0}%` }}
                  />
                </div>
                {autofillProgress.current && (
                  <div className="mt-1.5 truncate text-[11px] text-zinc-500">
                    {autofillProgress.current}
                    {autofillProgress.step && <> · {autofillProgress.step}</>}
                  </div>
                )}
              </div>
            )}
            {!autofillActive && autofillProgress.enriching > 0 && (
              <div className="flex items-center gap-1.5 border-t border-white/[0.08] px-4 py-2 text-[11px] text-zinc-500">
                <Loader2 size={11} className="shrink-0 animate-spin" />
                <Tooltip content={`Приборы и коды СИ уже заведены; характеристики дочитываются из «Описаний типа» и руководств.\n${AI_COST_NOTE}`}>
                  <span>Характеристики дочитываются: {autofillProgress.enriching}</span>
                </Tooltip>
              </div>
            )}
            <div className="max-h-[70vh] overflow-y-auto border-t border-white/[0.08] py-1">
              {/* По алфавиту — порядок сервера. */}
              {manufacturers.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setSelectedId(m.id)}
                  className={`flex w-full items-center justify-between py-2 pl-4 pr-3 text-left text-sm transition-colors ${
                    m.id === selectedId ? "bg-white/[0.06] text-zinc-50" : "text-zinc-400 hover:bg-white/[0.03] hover:text-zinc-200"
                  }`}
                >
                  <span className="truncate">
                    {manufacturerTitle(m)}
                  </span>
                  <span className="ml-2 flex shrink-0 items-center">
                    <AutofillMark status={autofill[m.id]} />
                  </span>
                </button>
              ))}
            </div>
          </aside>

          <div className="min-w-0 space-y-5">
            {selected && (
              <>
                {/* Производитель: кто это и главные цифры */}
                <div className="flex flex-wrap items-start justify-between gap-6">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="truncate text-3xl font-semibold tracking-tight text-zinc-50">
                        {selected.brand_name ?? selected.legal_name}
                      </h2>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-zinc-500">
                      {selected.brand_name && <span>{selected.legal_name}</span>}
                      {selected.website && (
                        <a
                          href={selected.website}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 hover:text-zinc-200"
                        >
                          {selected.website.replace(/^https?:\/\//, "").replace(/\/$/, "")} <ExternalLink size={12} />
                        </a>
                      )}
                      {selectedAutofillActive && (
                        <span className="inline-flex items-center gap-1.5 text-zinc-400">
                          <Loader2 size={12} className="animate-spin" />
                          {selectedAutofill?.status === "running" ? "обновляется" : "в очереди на обновление"}
                        </span>
                      )}
                    </div>
                    {/* Действия с производителем — кнопками на виду, а не в «…» (14.10.2026): в меню
                        их не находили, а «Обновить данные» / «Обойти только сайт» не объясняли разницу. */}
                    {isAdmin && (
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <Tooltip
                          content={selectedAutofill ? `${autofillTitle(selectedAutofill)}\n\n${AUTOFILL_HINT}` : AUTOFILL_HINT}
                        >
                          <button
                            onClick={() => void handleAutofillOne()}
                            disabled={busy === "autofill-one" || selectedAutofillActive}
                            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                          >
                            {selectedAutofillActive ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                            Полное обновление
                            <span className="text-zinc-500">ФГИС, сайт, документы</span>
                          </button>
                        </Tooltip>
                        {selectedSite && (
                          <Tooltip
                            content={`Только список моделей с ${selectedSite.base_url} — без ФГИС и без ИИ. Быстрее и бесплатно, несколько минут.`}
                          >
                            <button
                              onClick={() => void handleSyncSite(selectedSite.adapter_key)}
                              disabled={busy === "site-sync" || siteSyncActive}
                              className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                            >
                              {busy === "site-sync" || siteSyncActive ? <Loader2 size={13} className="animate-spin" /> : <Globe size={13} />}
                              Модели с сайта
                            </button>
                          </Tooltip>
                        )}
                        <button
                          onClick={() => openManufacturerForm(selected)}
                          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
                        >
                          <Pencil size={13} />
                          Название и сайт
                        </button>
                      </div>
                    )}
                    {selectedSite && (
                      <SiteSyncProgress
                        adapterKey={selectedSite.adapter_key}
                        kick={siteSyncKick}
                        onActiveChange={setSiteSyncActive}
                        onFinished={(task) => {
                          setNotice(
                            task.status === "error"
                              ? `Обход сайта завершился ошибкой: ${task.message ?? "без подробностей"}`
                              : `Обход сайта завершён. ${task.message ?? ""}`.trim(),
                          );
                          if (selectedId) void loadManufacturerData(selectedId);
                        }}
                      />
                    )}
                  </div>
                  <div className="flex gap-10">
                    {(
                      [
                        [activeProducts, "в выпуске"],
                        [siTypes.length, "кодов СИ"],
                        [siTypesNeedingReview + productsNeedingReview, "ждут проверки"],
                      ] as const
                    ).map(([value, label]) => (
                      <div key={label} className="text-right">
                        <div className="text-3xl font-semibold tabular-nums tracking-tight text-zinc-50">
                          {busy === "manufacturer" ? "…" : value}
                        </div>
                        <div className="text-xs text-zinc-500">{label}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Разделы производителя */}
                <nav className="flex gap-6 overflow-x-auto border-b border-white/[0.08]" aria-label="Разделы каталога">
                  {STEPS.map((s) => {
                    const active = s.key === step;
                    const count = stepCount(s.key);
                    return (
                      <button
                        key={s.key}
                        onClick={() => switchStep(s.key)}
                        aria-current={active ? "page" : undefined}
                        className={`-mb-px flex shrink-0 items-center gap-1.5 border-b-2 pb-2.5 text-sm transition-colors ${
                          active ? "border-indigo-400 text-zinc-50" : "border-transparent text-zinc-500 hover:text-zinc-200"
                        }`}
                      >
                        {s.title}
                        {count !== null && <span className="tabular-nums text-zinc-500">{count}</span>}
                        {s.key === "si" && siTypesNeedingReview > 0 && (
                          <Tooltip content={`Ждут проверки: ${siTypesNeedingReview}`}>
                            <span className="h-1.5 w-1.5 rounded-full bg-amber-400" aria-label="есть коды на проверку" />
                          </Tooltip>
                        )}
                      </button>
                    );
                  })}
                </nav>

                {/* Коды СИ */}
                {step === "si" && (
                  <div className="space-y-3">
                    {isAdmin && (
                      <div className="flex flex-wrap gap-2">
                        <ToolbarButton onClick={handleSearchSiTypes} disabled={busy === "si-search"} busy={busy === "si-search"} icon={<Search size={13} />}>
                          Найти в ФГИС
                        </ToolbarButton>
                        <Tooltip content="Сопоставить модели с кодами СИ по обозначению типа и артикулу">
                          <ToolbarButton onClick={handleLinkSiTypes} disabled={busy === "link-si"} busy={busy === "link-si"} icon={<Link2 size={13} />}>
                            Привязать к моделям
                          </ToolbarButton>
                        </Tooltip>
                      </div>
                    )}
                    {siTypes.length === 0 ? (
                      <p className="py-6 text-sm text-zinc-500">Кодов СИ пока нет.</p>
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
                )}

                {/* Модели */}
                {step === "models" && (
                  <div className="space-y-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      {products.length > 0 ? (
                        <SegmentedControl
                          size="sm"
                          ariaLabel="Какие модели показать"
                          value={hideDiscontinued ? "active" : "all"}
                          onChange={(v) => toggleHideDiscontinued(v === "active")}
                          segments={[
                            { value: "active", label: `В выпуске ${activeProducts}` },
                            { value: "all", label: `Все ${products.length}` },
                          ]}
                        />
                      ) : (
                        <span />
                      )}
                      {isAdmin &&
                        (addingModel ? (
                          <div className="flex gap-2">
                            <input
                              autoFocus
                              value={newModelName}
                              onChange={(e) => setNewModelName(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" && newModelName.trim()) void handleCreateProduct();
                                if (e.key === "Escape") setAddingModel(false);
                              }}
                              placeholder="Название модели"
                              className="w-56 rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
                            />
                            <ToolbarButton
                              onClick={() => void handleCreateProduct()}
                              disabled={!newModelName.trim() || busy === "create-product"}
                              busy={busy === "create-product"}
                              icon={<Plus size={13} />}
                            >
                              Добавить
                            </ToolbarButton>
                          </div>
                        ) : (
                          <ToolbarButton onClick={() => setAddingModel(true)} icon={<Plus size={13} />}>
                            Модель
                          </ToolbarButton>
                        ))}
                    </div>
                    {products.length === 0 ? (
                      <p className="py-6 text-sm text-zinc-500">Моделей пока нет.</p>
                    ) : visibleProducts.length === 0 ? (
                      <p className="py-6 text-sm text-zinc-500">Все модели производителя сняты с производства.</p>
                    ) : (
                      <div className="rounded-xl border border-white/[0.08]">
                        <ProductMatrix products={visibleProducts} siTypes={siTypes} selectedProductId={openProductId} onSelect={setOpenProductId} />
                      </div>
                    )}
                  </div>
                )}

                {/* Реестр и руководства */}
                {step === "learning" && (
                  <RegistryLearningSection
                    manufacturerId={selectedId}
                    isAdmin={isAdmin}
                    onChanged={() => (selectedId ? loadManufacturerData(selectedId) : Promise.resolve())}
                  />
                )}

                {/* Документы */}
                {step === "documents" && <CatalogDocumentsSection manufacturerId={selectedId} isAdmin={isAdmin} />}

                {/* ПО верхнего уровня */}
                {step === "software" && <UpperSoftwareSection manufacturerId={selectedId} isAdmin={isAdmin} />}
              </>
            )}
          </div>
        </div>
      </div>

      {repairOpen && <CatalogRepairDialog onClose={() => setRepairOpen(false)} />}

      {openProduct && (
        <ProductDrawer product={openProduct} siTypes={siTypes} isAdmin={isAdmin} onClose={closeProduct} />
      )}
    </AppShell>
  );
}
