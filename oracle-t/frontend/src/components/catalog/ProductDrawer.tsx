import { useEffect, useMemo, useState } from "react";
import { BadgeCheck, BookOpen, ExternalLink, Globe, Loader2, Search, X } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  Characteristic,
  ExtractionOutcome,
  ManualExtractionOutcome,
  MeterParameter,
  Product,
  ProductDocumentation,
  ProductSupport,
  SiType,
} from "../../api/types";
import { meterKindLabel } from "../../utils/meterKinds";
import { MeterParametersTable } from "./MeterParametersTable";
import { ProductRegistrySection } from "./ProductRegistrySection";

// Карточка модели прибора — отдельным окном поверх каталога (замечание 28.09.2026): раньше
// характеристики открывались ещё одним блоком внизу длинной страницы, под матрицей моделей,
// обучением, документами и ПО верхнего уровня, и до них приходилось докручивать. Здесь всё
// о модели в одном месте: паспорт, где она поддержана, параметры для ПУ или полное
// Приложение C, реестры допуска и то, что не легло в справочник.

export const SOURCE_LABELS: Record<string, string> = {
  fgis_description_type: "ФГИС «Описание типа»",
  manufacturer_site: "сайт производителя",
  user_manual: "руководство пользователя",
  manual_entry: "введено вручную",
  web_search: "поиск в интернете (официальный сайт)",
};

const STATUS_LABELS: Record<string, string> = {
  active: "выпускается",
  discontinued: "снят с производства",
};

type View = "parameters" | "appendix";

function summariseExtraction(outcome: ExtractionOutcome): string {
  const parts = [`сохранено ${outcome.saved}`];
  if (outcome.skipped_protected) parts.push(`не тронуто ручных ${outcome.skipped_protected}`);
  if (outcome.skipped_unknown_field) parts.push(`отброшено ${outcome.skipped_unknown_field}`);
  if (outcome.chunks_failed) parts.push(`ошибок разбора ${outcome.chunks_failed}`);
  return parts.join(", ");
}

function readView(): View {
  try {
    return localStorage.getItem("catalog.characteristicsView") === "appendix" ? "appendix" : "parameters";
  } catch {
    return "parameters";
  }
}

export function ProductDrawer({
  product,
  siTypes,
  isAdmin,
  onClose,
}: {
  product: Product;
  siTypes: SiType[];
  isAdmin: boolean;
  onClose: () => void;
}) {
  const [characteristics, setCharacteristics] = useState<Characteristic[]>([]);
  const [support, setSupport] = useState<ProductSupport[]>([]);
  const [parameters, setParameters] = useState<MeterParameter[]>([]);
  const [view, setView] = useState<View>(readView);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const productId = product.id;
  const siType = siTypes.find((s) => s.id === product.si_type_id) ?? null;

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
    setCharacteristics([]);
    setSupport([]);
    setNotice(null);
    setError(null);
    void run("load", async () => {
      const [rows, platforms] = await Promise.all([
        api.get<Characteristic[]>(`/products/${productId}/characteristics`),
        api.get<ProductSupport[]>(`/products/${productId}/upper-software`),
      ]);
      setCharacteristics(rows);
      setSupport(platforms);
    });
  }, [productId]);

  // Параметры собираются из характеристик — перечитываются после каждой их правки.
  useEffect(() => {
    let cancelled = false;
    api
      .get<MeterParameter[]>(`/products/${productId}/meter-parameters`)
      .then((rows) => !cancelled && setParameters(rows))
      .catch(() => !cancelled && setParameters([]));
    return () => {
      cancelled = true;
    };
  }, [productId, characteristics]);

  // Esc закрывает окно, прокрутка страницы под ним на это время выключена.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  const switchView = (next: View) => {
    setView(next);
    try {
      localStorage.setItem("catalog.characteristicsView", next);
    } catch {
      // без сохранения — вид просто не запомнится
    }
  };

  // Одна площадка может покрывать модель несколькими записями — в подписи каждая один раз.
  const platforms = useMemo(() => {
    const seen = new Map<string, ProductSupport>();
    for (const row of support) if (!seen.has(row.adapter_key)) seen.set(row.adapter_key, row);
    return [...seen.values()];
  }, [support]);

  const grouped = useMemo(() => {
    const groups: Record<string, Characteristic[]> = {};
    for (const c of characteristics) (groups[c.group_name] ??= []).push(c);
    return Object.entries(groups);
  }, [characteristics]);

  const filled = parameters.filter((p) => p.filled).length;
  const reloadCharacteristics = async () =>
    setCharacteristics(await api.get<Characteristic[]>(`/products/${productId}/characteristics`));

  const upsert = (updated: Characteristic) =>
    setCharacteristics((prev) =>
      prev.some((c) => c.id === updated.id) ? prev.map((c) => (c.id === updated.id ? updated : c)) : [...prev, updated]
    );

  const handleSetField = (groupName: string, fieldName: string, current: Characteristic | null) => {
    const next = window.prompt(`${groupName} → ${fieldName}`, current?.value ?? "");
    if (next === null || next === (current?.value ?? "")) return;
    void run(`set-${groupName}/${fieldName}`, async () => {
      upsert(
        await api.put<Characteristic>(`/products/${productId}/characteristics`, {
          group_name: groupName,
          field_name: fieldName,
          value: next,
        })
      );
    });
  };

  const handleVerify = (characteristic: Characteristic) =>
    void run(`ch-${characteristic.id}`, async () => {
      upsert(
        await api.post<Characteristic>(`/characteristics/${characteristic.id}/verify`, {
          verified_by_user: !characteristic.verified_by_user,
        })
      );
    });

  const handleExtractFromSiType = () =>
    run("extract-fgis", async () => {
      const outcome = await api.post<ExtractionOutcome>(`/products/${productId}/extract-characteristics/from-si-type`);
      await reloadCharacteristics();
      setNotice(`Из «Описание типа»: ${summariseExtraction(outcome)}.`);
    });

  const handleExtractFromSite = () =>
    run("extract-site", async () => {
      const outcome = await api.post<ManualExtractionOutcome>(
        `/products/${productId}/extract-characteristics/from-manufacturer-site`
      );
      await reloadCharacteristics();
      setNotice(
        outcome.message ?? `Из «${outcome.manual_title ?? "руководства"}»: ${summariseExtraction(outcome.extraction)}.`
      );
    });

  // Для исполнения, которого нет в каталоге на сайте производителя, это единственный
  // автоматический путь к руководству — обход каталога на него не выйдет.
  const handleFindDocumentation = () =>
    run("find-docs", async () => {
      const outcome = await api.post<ProductDocumentation>(`/products/${productId}/find-documentation`);
      await reloadCharacteristics();
      setNotice(outcome.message);
    });

  const actionButton = (key: string, label: string, title: string, icon: React.ReactNode, onClick: () => void) => (
    <button
      onClick={onClick}
      disabled={busy !== null}
      title={title}
      className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
    >
      {busy === key ? <Loader2 size={13} className="animate-spin" /> : icon}
      {label}
    </button>
  );

  const passport: [string, React.ReactNode][] = [
    ["Код СИ (ГРСИ)", siType ? <span className="font-mono">{siType.si_code}</span> : <span className="text-zinc-500">не привязан</span>],
    ["Артикул", product.article ?? "—"],
    ["Исполнение", product.execution ?? "—"],
    ["Статус", STATUS_LABELS[product.status] ?? product.status],
    [
      "Тип прибора",
      product.meter_kinds.length ? product.meter_kinds.map(meterKindLabel).join("; ") : <span className="text-zinc-500">не определён</span>,
    ],
  ];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={product.model_name}>
      <button aria-label="Закрыть" onClick={onClose} className="absolute inset-0 cursor-default bg-black/60" />
      <div className="relative flex h-full w-full max-w-5xl flex-col border-l border-white/[0.08] bg-[#0b0b0f] shadow-[0_20px_60px_rgba(0,0,0,.5)]">
        {/* Шапка */}
        <div className="border-b border-white/[0.08] px-6 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">Модель прибора</div>
              <h2 className="truncate text-xl font-semibold tracking-tight text-zinc-50">{product.model_name}</h2>
              {product.registry_modification && (
                <p className="mt-0.5 font-mono text-[11px] text-sky-400/80" title="Полное условное обозначение исполнения из реестра ФГИС">
                  {product.registry_modification}
                </p>
              )}
            </div>
            <div className="flex shrink-0 items-start gap-4">
              {/* Главная цифра карточки — насколько модель описана по таблице тендерного отдела */}
              {parameters.length > 0 && (
                <div className="text-right">
                  <div className="text-3xl font-semibold tabular-nums tracking-tight text-zinc-50">
                    {filled}
                    <span className="text-base font-normal text-zinc-500"> / {parameters.length}</span>
                  </div>
                  <div className="text-[11px] text-zinc-500">параметров для ПУ заполнено</div>
                </div>
              )}
              <button onClick={onClose} title="Закрыть (Esc)" className="rounded-lg p-1.5 text-zinc-400 hover:bg-white/5 hover:text-zinc-100">
                <X size={18} />
              </button>
            </div>
          </div>
          {product.review_status === "needs_review" && product.review_reason && (
            <p className="mt-2 text-[12px] text-amber-400/90">⚠ {product.review_reason}</p>
          )}
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {error && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>
          )}
          {notice && (
            <div className="flex items-start justify-between gap-3 rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-300">
              <span>{notice}</span>
              <button onClick={() => setNotice(null)} className="shrink-0 text-indigo-400 hover:text-indigo-200">
                ×
              </button>
            </div>
          )}

          {/* Паспорт */}
          <section className="grid gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-3">
            {passport.map(([label, value]) => (
              <div key={label}>
                <div className="text-[11px] text-zinc-500">{label}</div>
                <div className="text-sm text-zinc-200">{value}</div>
              </div>
            ))}
            {product.source_url && (
              <div>
                <div className="text-[11px] text-zinc-500">Карточка на сайте производителя</div>
                <a href={product.source_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-indigo-400 hover:text-indigo-300">
                  открыть <ExternalLink size={12} />
                </a>
              </div>
            )}
            <div className="sm:col-span-2 lg:col-span-3">
              <div className="text-[11px] text-zinc-500">ПО верхнего уровня</div>
              <div className="mt-0.5 flex flex-wrap gap-1 text-[12px]">
                {platforms.length === 0 ? (
                  <span className="text-zinc-500">ни в одном списке поддерживаемого оборудования</span>
                ) : (
                  platforms.map((row) => (
                    <span key={row.adapter_key} title={`${row.device_raw} — ${row.section}`} className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-emerald-400">
                      {row.name}
                    </span>
                  ))
                )}
              </div>
            </div>
          </section>

          {/* Характеристики */}
          <section className="rounded-xl border border-white/[0.08]">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.08] px-4 py-3">
              <div className="inline-flex rounded-lg border border-white/10 p-0.5 text-xs">
                {(
                  [
                    ["parameters", "Параметры для ПУ"],
                    ["appendix", `Приложение C · ${characteristics.length}`],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => switchView(key)}
                    className={`rounded-md px-2.5 py-1 ${view === key ? "bg-white/10 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {isAdmin && (
                <div className="flex flex-wrap gap-2">
                  {actionButton("extract-fgis", "Из «Описание типа»", "Извлечь из текста «Описание типа» привязанного кода СИ", <BookOpen size={13} />, handleExtractFromSiType)}
                  {actionButton("extract-site", "С сайта производителя", "Найти руководство пользователя на сайте производителя (до минуты)", <Globe size={13} />, handleExtractFromSite)}
                  {actionButton("find-docs", "Найти документацию", "Найти руководство на официальном сайте через поиск в интернете и разобрать его (до минуты)", <Search size={13} />, handleFindDocumentation)}
                </div>
              )}
            </div>
            <div className="px-4 py-3">
              <p className="mb-3 text-[11px] text-zinc-500">
                {view === "parameters" ? "Параметры файла тендерного отдела «Параметры для ПУ», в его порядке." : "Полный справочник Приложения C ТЗ."}{" "}
                Извлечённое ИИ требует проверки, ручной ввод имеет приоритет.
              </p>
              {busy === "load" ? (
                <p className="text-xs text-zinc-500">Загружаю…</p>
              ) : view === "parameters" ? (
                <MeterParametersTable
                  parameters={parameters}
                  sourceLabels={SOURCE_LABELS}
                  isAdmin={isAdmin}
                  busyKey={busy}
                  onEdit={handleSetField}
                  onVerify={handleVerify}
                />
              ) : characteristics.length === 0 ? (
                <p className="text-xs text-zinc-500">Характеристик пока нет — запустите извлечение или заполните параметры вручную.</p>
              ) : (
                <div className="space-y-4">
                  {grouped.map(([group, items]) => (
                    <div key={group}>
                      <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">{group}</h3>
                      <table className="w-full text-left text-sm">
                        <tbody>
                          {items.map((c) => (
                            <tr key={c.id} className="border-t border-white/[0.06]">
                              <td className="w-1/3 py-1.5 pr-4 text-zinc-400">{c.field_name}</td>
                              <td className="py-1.5 pr-4 text-zinc-100">{c.value}</td>
                              <td className="whitespace-nowrap py-1.5 pr-4 text-[11px]">
                                <span className="text-zinc-500">
                                  {SOURCE_LABELS[c.source] ?? c.source}
                                  {c.confidence !== null && ` · ${Math.round(c.confidence * 100)}%`}
                                </span>{" "}
                                {c.verified_by_user ? (
                                  <span className="inline-flex items-center gap-0.5 text-emerald-400">
                                    <BadgeCheck size={11} /> проверено
                                  </span>
                                ) : (
                                  <span className="text-amber-400">требует проверки</span>
                                )}
                              </td>
                              {isAdmin && (
                                <td className="whitespace-nowrap py-1.5 text-right text-[11px]">
                                  <button onClick={() => handleSetField(c.group_name, c.field_name, c)} className="mr-2 text-indigo-400 hover:underline">
                                    править
                                  </button>
                                  <button onClick={() => handleVerify(c)} disabled={busy === `ch-${c.id}`} className="text-zinc-400 hover:underline disabled:opacity-50">
                                    {c.verified_by_user ? "снять" : "подтвердить"}
                                  </button>
                                </td>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          {/* Реестры допуска — не свойство прибора, а допуск с датами */}
          <section>
            <ProductRegistrySection productId={product.id} isAdmin={isAdmin} />
          </section>

          {Object.keys(product.extra_specifications ?? {}).length > 0 && (
            // Характеристики с сайта производителя, которым не нашлось поля в справочнике:
            // это данные, а не мусор — человек может перенести значение в нужное поле.
            <section className="rounded-xl border border-white/[0.08] px-4 py-3">
              <h3 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Вне справочника</h3>
              <p className="mb-2 text-[11px] text-zinc-600">
                Извлечены из документации, но подходящего поля нет. Значение можно перенести в нужное поле вручную.
              </p>
              <table className="w-full text-left text-sm">
                <tbody>
                  {Object.entries(product.extra_specifications).map(([key, value]) => (
                    <tr key={key} className="border-t border-white/[0.06]">
                      <td className="w-1/3 py-1.5 pr-4 text-zinc-400">{key}</td>
                      <td className="py-1.5 text-zinc-100">{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
