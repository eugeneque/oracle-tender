import { useEffect, useMemo, useRef, useState } from "react";
import {
  BadgeCheck,
  BookOpen,
  Boxes,
  ChevronDown,
  ChevronRight,
  FileDown,
  Globe,
  Link2,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Upload,
} from "lucide-react";

import { ApiError, api, uploadFile } from "../api/client";
import type {
  CatalogImportOutcome,
  Characteristic,
  ExtractionOutcome,
  CatalogSite,
  CatalogTask,
  LinkSiTypesOutcome,
  ManualIngestOutcome,
  Manufacturer,
  ManualExtractionOutcome,
  Product,
  SiType,
} from "../api/types";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../context/useAuth";

const SOURCE_LABELS: Record<string, string> = {
  fgis_description_type: "ФГИС «Описание типа»",
  manufacturer_site: "сайт производителя",
  user_manual: "руководство пользователя",
  manual_entry: "введено вручную",
};

const SI_SOURCE_LABELS: Record<string, string> = {
  auto_search: "автопоиск",
  manual: "вручную",
  import: "импорт",
};

function Badge({ tone, children }: { tone: "green" | "amber" | "zinc"; children: React.ReactNode }) {
  const tones = {
    green: "bg-emerald-500/10 text-emerald-400",
    amber: "bg-amber-500/10 text-amber-400",
    zinc: "bg-zinc-500/10 text-zinc-400",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

function summariseExtraction(outcome: ExtractionOutcome): string {
  const parts = [`сохранено ${outcome.saved}`];
  if (outcome.skipped_protected) parts.push(`не тронуто ручных ${outcome.skipped_protected}`);
  if (outcome.skipped_unknown_field) parts.push(`отброшено ${outcome.skipped_unknown_field}`);
  if (outcome.chunks_failed) parts.push(`ошибок разбора ${outcome.chunks_failed}`);
  return parts.join(", ");
}

export function CatalogPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const [manufacturers, setManufacturers] = useState<Manufacturer[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [siTypes, setSiTypes] = useState<SiType[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [selectedProductId, setSelectedProductId] = useState<string | null>(null);
  const [characteristics, setCharacteristics] = useState<Characteristic[]>([]);

  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newModelName, setNewModelName] = useState("");
  // Список кодов СИ длинный (у МИРТЕК их 28, и половина — теплосчётчики и счётчики воды,
  // не относящиеся к справочнику) и оттесняет вниз то, ради чего страницу открывают —
  // модели и характеристики. По умолчанию свёрнут; счётчик в заголовке позволяет понять,
  // есть ли там что-то, не разворачивая.
  const [siTypesOpen, setSiTypesOpen] = useState(false);
  // Сайты, каталоги которых система умеет обходить. Кнопка обхода показывается только у тех
  // производителей, для чьего сайта есть разобранный профиль: у остальных нажимать было бы
  // не на что, и кнопка вводила бы в заблуждение.
  const [catalogSites, setCatalogSites] = useState<CatalogSite[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selected = useMemo(
    () => manufacturers.find((m) => m.id === selectedId) ?? null,
    [manufacturers, selectedId]
  );
  const selectedProduct = useMemo(
    () => products.find((p) => p.id === selectedProductId) ?? null,
    [products, selectedProductId]
  );

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
      setSelectedId((current) => current ?? data[0]?.id ?? null);
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
    setSelectedProductId(null);
    setCharacteristics([]);
    void run("manufacturer", () => loadManufacturerData(selectedId));
  }, [selectedId]);

  useEffect(() => {
    if (!selectedProductId) return;
    void run("characteristics", async () => {
      setCharacteristics(await api.get<Characteristic[]>(`/products/${selectedProductId}/characteristics`));
    });
  }, [selectedProductId]);

  const selectedSite = useMemo(
    () => catalogSites.find((site) => site.manufacturer_id === selectedId) ?? null,
    [catalogSites, selectedId]
  );

  const siTypesNeedingReview = useMemo(
    () => siTypes.filter((s) => s.review_status === "needs_review").length,
    [siTypes]
  );

  const groupedCharacteristics = useMemo(() => {
    const groups: Record<string, Characteristic[]> = {};
    for (const c of characteristics) {
      (groups[c.group_name] ??= []).push(c);
    }
    return Object.entries(groups);
  }, [characteristics]);

  const handleSearchSiTypes = () =>
    run("si-search", async () => {
      const found = await api.post<SiType[]>(`/manufacturers/${selectedId}/si-types/search`);
      setSiTypes(found);
      // Раскрываем блок: человек только что нажал кнопку и должен увидеть результат, а не
      // гадать, отработала ли она.
      if (found.length) setSiTypesOpen(true);
      setNotice(
        found.length
          ? `Автопоиск ФГИС: найдено кодов СИ — ${found.length}. Подходящие подставлены к моделям; проверьте и подтвердите записи.`
          : "Автопоиск ФГИС не вернул результатов (реестр может быть недоступен — см. журнал)."
      );
      // Список моделей мог измениться: найденные коды подставляются к ним сразу.
      await loadManufacturerData(selectedId!);
    });

  // Обход каталога МИРТЕК запускается синхронно, а не через очередь: администратор,
  // нажавший кнопку, должен увидеть итог обхода целиком (сколько создано, что ушло на
  // проверку, где сломалось), а не «задача поставлена». Плановый обход — раз в неделю
  // из планировщика.
  // Обход ставится в очередь, а не выполняется в запросе: сайты производителей отвечают
  // по 2-6 секунд на страницу, и каталог из двух сотен позиций обходится минутами. Держать
  // на этом открытую вкладку нельзя — человек решит, что интерфейс завис.
  const handleSyncSite = (adapterKey: string) =>
    run("site-sync", async () => {
      await api.post<CatalogTask>(`/catalog/sites/${adapterKey}/sync`);
      setNotice(
        "Обход каталога запущен в фоне — сайты производителей отвечают медленно, полный " +
          "каталог занимает минуты. Ход работы виден в разделе «Логирование» настроек; " +
          "обновите страницу, когда обход закончится."
      );
    });

  // Привязка моделей к кодам СИ. Обход каталога делает это сам; отдельная кнопка нужна для
  // случая «модели уже есть, а автопоиск в ФГИС запустили только что» — иначе пришлось бы
  // заново обходить сайт ради одной связи.
  const handleLinkSiTypes = () =>
    run("link-si", async () => {
      const outcome = await api.post<LinkSiTypesOutcome>(
        `/manufacturers/${selectedId}/link-si-types`
      );
      const parts = [`привязано ${outcome.linked}`];
      if (outcome.already_linked) parts.push(`было привязано ранее ${outcome.already_linked}`);
      if (outcome.not_found) parts.push(`без подходящего кода СИ ${outcome.not_found}`);
      if (outcome.needs_review) parts.push(`неоднозначно ${outcome.needs_review}`);
      setNotice(`Привязка кодов СИ: ${parts.join(", ")}.`);
      await loadManufacturerData(selectedId!);
    });

  // Руководства по эксплуатации. Обход сайта сохраняет только ссылку на документ, а сам
  // документ — самый подробный источник о приборе (интерфейсы, протоколы, функции). Отдельной
  // кнопкой, а не частью обхода: PDF весит мегабайты, а разбор каждого стоит нескольких
  // обращений к модели, и делать это на каждом еженедельном обходе незачем.
  const handleIngestManuals = () =>
    run("manuals", async () => {
      const outcome = await api.post<ManualIngestOutcome>(
        `/manufacturers/${selectedId}/ingest-manuals`
      );
      const parts = [
        `разобрано руководств ${outcome.processed}`,
        `характеристик сохранено ${outcome.characteristics_saved}`,
      ];
      if (outcome.skipped_have_data) parts.push(`уже разобраны ранее ${outcome.skipped_have_data}`);
      if (outcome.skipped_no_link) parts.push(`без ссылки на руководство ${outcome.skipped_no_link}`);
      if (outcome.skipped_by_robots)
        parts.push(`закрыто robots.txt сайта ${outcome.skipped_by_robots}`);
      if (outcome.failed) parts.push(`не загрузилось ${outcome.failed}`);
      setNotice(`Руководства: ${parts.join(", ")}.`);
      await loadManufacturerData(selectedId!);
    });

  const handleVerifySiType = (siType: SiType) =>
    run(`si-${siType.id}`, async () => {
      const updated = await api.patch<SiType>(`/si-types/${siType.id}`, {
        verified_by_user: !siType.verified_by_user,
      });
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

  const handleCreateProduct = () =>
    run("create-product", async () => {
      const created = await api.post<Product>(`/manufacturers/${selectedId}/products`, {
        model_name: newModelName.trim(),
      });
      setProducts((prev) => [...prev, created]);
      setNewModelName("");
      setSelectedProductId(created.id);
    });

  const handleExtractFromSiType = () =>
    run("extract-fgis", async () => {
      const outcome = await api.post<ExtractionOutcome>(
        `/products/${selectedProductId}/extract-characteristics/from-si-type`
      );
      setCharacteristics(await api.get<Characteristic[]>(`/products/${selectedProductId}/characteristics`));
      setNotice(`Из «Описание типа»: ${summariseExtraction(outcome)}.`);
    });

  const handleExtractFromSite = () =>
    run("extract-site", async () => {
      const outcome = await api.post<ManualExtractionOutcome>(
        `/products/${selectedProductId}/extract-characteristics/from-manufacturer-site`
      );
      setCharacteristics(await api.get<Characteristic[]>(`/products/${selectedProductId}/characteristics`));
      setNotice(
        outcome.message ??
          `Из «${outcome.manual_title ?? "руководства"}»: ${summariseExtraction(outcome.extraction)}.`
      );
    });

  const handleVerifyCharacteristic = (characteristic: Characteristic) =>
    run(`ch-${characteristic.id}`, async () => {
      const updated = await api.post<Characteristic>(`/characteristics/${characteristic.id}/verify`, {
        verified_by_user: !characteristic.verified_by_user,
      });
      setCharacteristics((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    });

  const handleEditValue = (characteristic: Characteristic) => {
    const next = window.prompt(
      `${characteristic.group_name} → ${characteristic.field_name}`,
      characteristic.value ?? ""
    );
    if (next === null || next === characteristic.value) return;
    void run(`edit-${characteristic.id}`, async () => {
      const updated = await api.put<Characteristic>(`/products/${selectedProductId}/characteristics`, {
        group_name: characteristic.group_name,
        field_name: characteristic.field_name,
        value: next,
      });
      setCharacteristics((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
    });
  };

  const handleImport = (file: File) =>
    run("import", async () => {
      const outcome = await uploadFile<CatalogImportOutcome>("/catalog/import", file);
      if (selectedId) await loadManufacturerData(selectedId);
      const base = `Импорт: кодов СИ создано ${outcome.si_types_created}, обновлено ${outcome.si_types_updated}, моделей создано ${outcome.products_created}.`;
      setNotice(outcome.errors.length ? `${base} Ошибок в строках: ${outcome.errors.length} — ${outcome.errors[0]}` : base);
    });

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl px-8 py-8">
        <PageHeader
          breadcrumb={["ORACLE-T", "Каталог продукции"]}
          title="Каталог продукции"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <Boxes size={18} />
            </span>
          }
        />

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
            {error}
          </div>
        )}
        {notice && (
          <div className="mb-4 flex items-start justify-between gap-4 rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-4 py-2.5 text-sm text-indigo-300">
            <span>{notice}</span>
            <button onClick={() => setNotice(null)} className="shrink-0 text-indigo-400 hover:text-indigo-200">
              ×
            </button>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
          {/* Производители */}
          <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
            <div className="flex items-center justify-between border-b border-white/[0.08] px-4 py-3">
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
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    disabled={busy === "import"}
                    title="CSV-импорт: производитель, код СИ, модель"
                    className="flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                  >
                    {busy === "import" ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                    CSV
                  </button>
                </>
              )}
            </div>
            <div className="max-h-[70vh] overflow-y-auto py-1">
              {manufacturers.map((m) => (
                <button
                  key={m.id}
                  onClick={() => setSelectedId(m.id)}
                  className={`flex w-full items-center justify-between px-4 py-2 text-left text-sm ${
                    m.id === selectedId ? "bg-indigo-500/10 text-indigo-300" : "text-zinc-300 hover:bg-white/5"
                  }`}
                >
                  <span className="truncate">
                    {m.brand_name ?? m.legal_name}
                    {m.is_mirtek && <span className="ml-1.5 text-[10px] text-emerald-400">МИРТЕК</span>}
                  </span>
                  <ChevronRight size={14} className="shrink-0 opacity-40" />
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-4">
            {/* Коды СИ */}
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
              <div
                className={`flex flex-wrap items-center justify-between gap-2 px-5 py-3 ${
                  siTypesOpen ? "border-b border-white/[0.08]" : ""
                }`}
              >
                <button
                  onClick={() => setSiTypesOpen((open) => !open)}
                  className="flex items-start gap-2 text-left"
                  aria-expanded={siTypesOpen}
                >
                  <span className="mt-0.5 text-zinc-500">
                    {siTypesOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                  </span>
                  <span>
                    <h2 className="text-sm font-semibold text-zinc-100">
                      Коды СИ {selected && <span className="text-zinc-500">· {selected.legal_name}</span>}
                      {siTypes.length > 0 && (
                        <span className="ml-1.5 text-zinc-500">({siTypes.length})</span>
                      )}
                      {/* Сколько записей ждут человека — видно и в свёрнутом виде, иначе
                          пометка «требует проверки» перестала бы попадаться на глаза. */}
                      {siTypesNeedingReview > 0 && (
                        <span className="ml-1.5 text-amber-400">⚠ {siTypesNeedingReview}</span>
                      )}
                    </h2>
                    <p className="mt-0.5 text-xs text-zinc-500">
                      Госреестр средств измерений (ФГИС). Автопоиск требует проверки человеком — раздел 5.3 ТЗ.
                    </p>
                  </span>
                </button>
                {isAdmin && selectedId && (
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={handleSearchSiTypes}
                      disabled={busy === "si-search"}
                      className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                    >
                      {busy === "si-search" ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                      Автопоиск в ФГИС
                    </button>
                    <button
                      onClick={handleLinkSiTypes}
                      disabled={busy === "link-si"}
                      title="Сопоставить модели каталога с кодами СИ по обозначению типа (по артикулу — он различает заводские исполнения)"
                      className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                    >
                      {busy === "link-si" ? <Loader2 size={13} className="animate-spin" /> : <Link2 size={13} />}
                      Привязать к моделям
                    </button>
                    <button
                      onClick={handleIngestManuals}
                      disabled={busy === "manuals"}
                      title="Скачать руководства по эксплуатации моделей и извлечь из них характеристики. За один запуск — до 20 документов; уже разобранные пропускаются"
                      className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                    >
                      {busy === "manuals" ? <Loader2 size={13} className="animate-spin" /> : <FileText size={13} />}
                      Разобрать руководства
                    </button>
                  </div>
                )}
              </div>
              <div className="px-5 py-3" hidden={!siTypesOpen}>
                {siTypes.length === 0 ? (
                  <p className="text-xs text-zinc-500">Кодов СИ пока нет — запустите автопоиск или загрузите CSV.</p>
                ) : (
                  <div className="space-y-2">
                    {siTypes.map((s) => (
                      <div key={s.id} className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-mono text-zinc-200">{s.si_code}</span>
                        <Badge tone="zinc">{SI_SOURCE_LABELS[s.source] ?? s.source}</Badge>
                        {s.verified_by_user ? (
                          <Badge tone="green">
                            <BadgeCheck size={11} /> подтверждён
                          </Badge>
                        ) : (
                          <Badge tone="amber">требует проверки</Badge>
                        )}
                        {s.has_description_type_text && <Badge tone="green">описание типа загружено</Badge>}
                        {s.review_status === "needs_review" && (
                          // Причина показывается целиком в подсказке: без неё пометка
                          // бесполезна — человек всё равно пойдёт искать в ФГИС руками.
                          // Отдельная формулировка для типов вне области справочника: их у
                          // производителя бывает половина реестра (теплосчётчики, вода, газ),
                          // и путать их с настоящей неоднозначностью нельзя.
                          <span title={s.review_reason ?? undefined}>
                            <Badge tone="amber">
                              {s.review_reason?.includes("Справочник продукции ограничен")
                                ? "⚠ не электросчётчик"
                                : "⚠ требует ручной проверки"}
                            </Badge>
                          </span>
                        )}
                        {isAdmin && (
                          <div className="ml-auto flex gap-2">
                            {s.description_type_url && (
                              <button
                                onClick={() => handleFetchDescriptionType(s)}
                                disabled={busy === `fetch-${s.id}`}
                                className="flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[11px] text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                              >
                                {busy === `fetch-${s.id}` ? (
                                  <Loader2 size={11} className="animate-spin" />
                                ) : (
                                  <FileDown size={11} />
                                )}
                                загрузить описание типа
                              </button>
                            )}
                            <button
                              onClick={() => handleVerifySiType(s)}
                              disabled={busy === `si-${s.id}`}
                              className="rounded border border-white/10 px-2 py-0.5 text-[11px] text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                            >
                              {s.verified_by_user ? "снять подтверждение" : "подтвердить"}
                            </button>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Модели */}
            <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.08] px-5 py-3">
                <h2 className="text-sm font-semibold text-zinc-100">Модели приборов</h2>
                {isAdmin && selectedId && (
                  <div className="flex flex-wrap gap-2">
                    {selectedSite && (
                      <button
                        onClick={() => handleSyncSite(selectedSite.adapter_key)}
                        disabled={busy === "site-sync"}
                        title={`Обойти каталог на ${selectedSite.base_url} и заполнить справочник (занимает несколько минут)`}
                        className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                      >
                        {busy === "site-sync" ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <RefreshCw size={13} />
                        )}
                        Обойти сайт производителя
                      </button>
                    )}
                    <input
                      value={newModelName}
                      onChange={(e) => setNewModelName(e.target.value)}
                      placeholder="Название модели"
                      className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
                    />
                    <button
                      onClick={handleCreateProduct}
                      disabled={!newModelName.trim() || busy === "create-product"}
                      className="flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                    >
                      <Plus size={13} />
                      Добавить
                    </button>
                  </div>
                )}
              </div>
              <div className="flex flex-wrap gap-2 px-5 py-3">
                {products.length === 0 ? (
                  <p className="text-xs text-zinc-500">Моделей пока нет.</p>
                ) : (
                  products.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => setSelectedProductId(p.id)}
                      className={`max-w-full truncate rounded-lg border px-3 py-1.5 text-left text-xs ${
                        p.id === selectedProductId
                          ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                          : "border-white/10 text-zinc-300 hover:bg-white/5"
                      }`}
                    >
                      <span title={p.model_name}>{p.model_name}</span>
                      {p.execution && <span className="ml-1.5 text-[10px] text-zinc-500">{p.execution}</span>}
                      {p.status === "discontinued" && (
                        <span className="ml-1.5 text-[10px] text-amber-400">снят с производства</span>
                      )}
                      {p.review_status === "needs_review" && (
                        <span className="ml-1.5 text-[10px] text-amber-400" title={p.review_reason ?? undefined}>
                          ⚠ проверить
                        </span>
                      )}
                      {p.si_type_id ? (
                        <span className="ml-1.5 font-mono text-[10px] text-emerald-400/80">
                          {siTypes.find((s) => s.id === p.si_type_id)?.si_code ?? "код СИ"}
                        </span>
                      ) : (
                        <span className="ml-1.5 text-[10px] text-zinc-500">без кода СИ</span>
                      )}
                    </button>
                  ))
                )}
              </div>
            </div>

            {/* Характеристики */}
            {selectedProduct && (
              <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.08] px-5 py-3">
                  <div>
                    <h2 className="text-sm font-semibold text-zinc-100">
                      Характеристики · {selectedProduct.model_name}
                    </h2>
                    <p className="mt-0.5 text-xs text-zinc-500">
                      Приложение C ТЗ. Извлечённое ИИ отмечено как требующее проверки, ручной ввод имеет приоритет.
                    </p>
                  </div>
                  {isAdmin && (
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={handleExtractFromSiType}
                        disabled={busy === "extract-fgis"}
                        title="Извлечь из текста «Описание типа» привязанного кода СИ"
                        className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                      >
                        {busy === "extract-fgis" ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <BookOpen size={13} />
                        )}
                        Из «Описание типа»
                      </button>
                      <button
                        onClick={handleExtractFromSite}
                        disabled={busy === "extract-site"}
                        title="Найти руководство пользователя на сайте производителя (занимает до минуты)"
                        className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                      >
                        {busy === "extract-site" ? (
                          <Loader2 size={13} className="animate-spin" />
                        ) : (
                          <Globe size={13} />
                        )}
                        С сайта производителя
                      </button>
                    </div>
                  )}
                </div>

                <div className="px-5 py-3">
                  {characteristics.length === 0 ? (
                    <p className="text-xs text-zinc-500">
                      Характеристик пока нет — запустите извлечение или добавьте значения вручную.
                    </p>
                  ) : (
                    <div className="space-y-4">
                      {groupedCharacteristics.map(([group, items]) => (
                        <div key={group}>
                          <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
                            {group}
                          </h3>
                          <div className="overflow-x-auto">
                            <table className="w-full text-left text-sm">
                              <tbody>
                                {items.map((c) => (
                                  <tr key={c.id} className="border-t border-white/[0.06]">
                                    <td className="py-1.5 pr-4 text-zinc-400">{c.field_name}</td>
                                    <td className="py-1.5 pr-4 text-zinc-100">{c.value}</td>
                                    <td className="py-1.5 pr-4">
                                      <span className="text-[11px] text-zinc-500">
                                        {SOURCE_LABELS[c.source] ?? c.source}
                                        {c.confidence !== null && ` · ${Math.round(c.confidence * 100)}%`}
                                      </span>
                                    </td>
                                    <td className="py-1.5 pr-4">
                                      {c.verified_by_user ? (
                                        <Badge tone="green">
                                          <BadgeCheck size={11} /> проверено
                                        </Badge>
                                      ) : (
                                        <Badge tone="amber">требует проверки</Badge>
                                      )}
                                    </td>
                                    {isAdmin && (
                                      <td className="py-1.5 text-right">
                                        <button
                                          onClick={() => handleEditValue(c)}
                                          className="mr-2 text-[11px] text-indigo-400 hover:underline"
                                        >
                                          править
                                        </button>
                                        <button
                                          onClick={() => handleVerifyCharacteristic(c)}
                                          disabled={busy === `ch-${c.id}`}
                                          className="text-[11px] text-zinc-400 hover:underline disabled:opacity-50"
                                        >
                                          {c.verified_by_user ? "снять" : "подтвердить"}
                                        </button>
                                      </td>
                                    )}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {Object.keys(selectedProduct.extra_specifications ?? {}).length > 0 && (
                    // Характеристики, снятые с сайта производителя, которым не нашлось поля
                    // в Приложении C. Показываются отдельно и как есть: это данные, а не
                    // мусор, — просто справочник до них ещё не дорос, и человек, увидев их
                    // здесь, может завести значение в нужное поле руками.
                    <div className="mt-4 rounded-lg border border-white/[0.06] bg-black/20 px-4 py-3">
                      <h3 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
                        Вне справочника Приложения C
                      </h3>
                      <p className="mb-2 text-[11px] text-zinc-600">
                        Сняты с сайта производителя, но подходящего поля в справочнике нет.
                        Значение можно перенести в нужное поле вручную.
                      </p>
                      <table className="w-full text-left text-sm">
                        <tbody>
                          {Object.entries(selectedProduct.extra_specifications).map(([key, value]) => (
                            <tr key={key} className="border-t border-white/[0.06]">
                              <td className="py-1.5 pr-4 text-zinc-400">{key}</td>
                              <td className="py-1.5 text-zinc-100">{value}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
