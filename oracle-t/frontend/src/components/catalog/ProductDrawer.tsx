import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, X } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { Characteristic, DataSource, MeterParameter, Product, ProductSupport, SiType } from "../../api/types";
import { productDisplayName } from "../../utils/format";
import { meterKindLabel } from "../../utils/meterKinds";
import { DataSourcesPanel } from "./DataSourcesPanel";
import { CharacteristicLine, MeterParametersTable } from "./MeterParametersTable";
import { ProductRegistrySection } from "./ProductRegistrySection";

// Карточка модели прибора — отдельным окном поверх каталога (замечание 28.09.2026): раньше
// характеристики открывались ещё одним блоком внизу длинной страницы, под матрицей моделей,
// обучением, документами и ПО верхнего уровня, и до них приходилось докручивать. Здесь всё
// о модели в одном месте: паспорт, где она поддержана, параметры для ПУ или полное
// Приложение C, реестры допуска и то, что не легло в справочник.
//
// 13.10.2026 (замечания тендерного отдела): кнопки извлечения переехали в панель «Источники
// данных» — рядом с объяснением, почему источник не прочитан.
//
// 14.10.2026 (редизайн каталога): значение правится прямо в строке, а не окном браузера;
// цветные подписи (ПО верхнего уровня, обозначение по реестру) приглушены до нейтральных.

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

// Пока идёт опрос реестра, источники перечитываются: опрос фоновый и длится минуты.
const RUNNING_REFRESH_MS = 5000;

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
  const [sources, setSources] = useState<DataSource[] | null>(null);
  // Меняется после действий с источниками — раздел реестров перечитывает свои записи.
  const [registryVersion, setRegistryVersion] = useState(0);
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
    setSources(null);
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

  const loadSources = useCallback(async () => {
    try {
      setSources(await api.get<DataSource[]>(`/products/${productId}/data-sources`));
    } catch {
      setSources([]);
    }
  }, [productId]);

  useEffect(() => {
    void loadSources();
  }, [loadSources]);

  const sourcesRunning = sources?.some((s) => s.state === "running") ?? false;
  useEffect(() => {
    if (!sourcesRunning) return;
    const timer = window.setTimeout(() => {
      void loadSources();
      setRegistryVersion((v) => v + 1);
    }, RUNNING_REFRESH_MS);
    return () => window.clearTimeout(timer);
  }, [sourcesRunning, sources, loadSources]);

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
  const reloadAfterSourceAction = async () => {
    setCharacteristics(await api.get<Characteristic[]>(`/products/${productId}/characteristics`));
    setRegistryVersion((v) => v + 1);
    await loadSources();
  };

  // Источник значения — ссылкой на сам документ: сайт, «Описание типа», руководство.
  const sourceUrls = useMemo(() => {
    const byKey = new Map((sources ?? []).map((s) => [s.key, s.url]));
    return {
      manufacturer_site: product.source_url ?? byKey.get("site") ?? null,
      fgis_description_type: byKey.get("fgis") ?? null,
      user_manual: byKey.get("manual") ?? null,
    } as Record<string, string | null>;
  }, [sources, product.source_url]);

  const upsert = (updated: Characteristic) =>
    setCharacteristics((prev) =>
      prev.some((c) => c.id === updated.id) ? prev.map((c) => (c.id === updated.id ? updated : c)) : [...prev, updated]
    );

  // Ошибка показывается в окне и пробрасывается дальше — строка остаётся в режиме правки.
  const handleSaveField = async (groupName: string, fieldName: string, value: string) => {
    setError(null);
    try {
      upsert(
        await api.put<Characteristic>(`/products/${productId}/characteristics`, {
          group_name: groupName,
          field_name: fieldName,
          value,
        })
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить значение");
      throw err;
    }
  };

  const handleVerify = (characteristic: Characteristic) =>
    void run(`ch-${characteristic.id}`, async () => {
      upsert(
        await api.post<Characteristic>(`/characteristics/${characteristic.id}/verify`, {
          verified_by_user: !characteristic.verified_by_user,
        })
      );
    });

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
      <div className="relative flex h-full w-full max-w-5xl flex-col border-l border-white/[0.08] bg-zinc-950 shadow-[0_20px_60px_rgba(0,0,0,.5)]">
        {/* Шапка */}
        <div className="border-b border-white/[0.08] px-6 py-4">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <div className="text-[11px] uppercase tracking-wide text-zinc-500">Модель прибора</div>
              <h2 className="truncate text-xl font-semibold tracking-tight text-zinc-50" title={product.model_name}>
                {productDisplayName(product.model_name)}
              </h2>
              {product.registry_modification && (
                // Полное обозначение — образец из карточки типа, на котором держатся
                // характеристики исполнения; подпись нужна, иначе его читают как имя модели.
                <p className="mt-0.5 text-[11px] text-zinc-500" title="Полное условное обозначение исполнения из реестра ФГИС">
                  исполнение по реестру ФГИС: <span className="font-mono text-zinc-400">{product.registry_modification}</span>
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
            <p className="mt-2 flex items-center gap-2 text-[12px] text-zinc-400">
              <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
              {product.review_reason}
            </p>
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
                    <span key={row.adapter_key} title={`${row.device_raw} — ${row.section}`} className="rounded-full border border-white/10 px-2 py-0.5 text-zinc-300">
                      {row.name}
                    </span>
                  ))
                )}
              </div>
            </div>
          </section>

          <DataSourcesPanel
            productId={product.id}
            manufacturerId={product.manufacturer_id}
            sources={sources}
            isAdmin={isAdmin}
            onChanged={reloadAfterSourceAction}
            onNotice={(text) => {
              setError(null);
              setNotice(text);
            }}
            onError={setError}
          />

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
            </div>
            <div className="px-4 py-3">
              {busy === "load" ? (
                <p className="text-xs text-zinc-500">Загружаю…</p>
              ) : view === "parameters" ? (
                <MeterParametersTable
                  parameters={parameters}
                  sourceLabels={SOURCE_LABELS}
                  sourceUrls={sourceUrls}
                  dataSources={sources}
                  isAdmin={isAdmin}
                  busyKey={busy}
                  onSave={handleSaveField}
                  onVerify={handleVerify}
                />
              ) : characteristics.length === 0 ? (
                <p className="text-xs text-zinc-500">Характеристик пока нет — опросите источники выше или заполните параметры вручную.</p>
              ) : (
                <div className="space-y-4">
                  {grouped.map(([group, items]) => (
                    <div key={group}>
                      <h3 className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">{group}</h3>
                      <table className="w-full text-left text-sm">
                        <tbody>
                          {items.map((c) => (
                            <tr key={c.id} className="border-t border-white/[0.06] align-top">
                              <td className="w-[38%] py-1.5 pr-6 leading-[26px] text-zinc-400">{c.field_name}</td>
                              <td className="py-1.5">
                                <CharacteristicLine
                                  characteristic={c}
                                  sourceLabels={SOURCE_LABELS}
                                  sourceUrls={sourceUrls}
                                  isAdmin={isAdmin}
                                  verifying={busy === `ch-${c.id}`}
                                  onSave={(value) => handleSaveField(c.group_name, c.field_name, value)}
                                  onVerify={handleVerify}
                                />
                              </td>
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
            <ProductRegistrySection productId={product.id} isAdmin={isAdmin} version={registryVersion} />
          </section>

          {Object.keys(product.extra_specifications ?? {}).length > 0 && (
            // Характеристики с сайта производителя, которым не нашлось поля в справочнике:
            // это данные, а не мусор — человек может перенести значение в нужное поле.
            // Перечни («Интерфейсы», «Дополнительные функции») и дубли полей справочника сюда
            // больше не попадают — их раскладывает обход сайта (замечание 13.10.2026, п.8).
            <section className="rounded-xl border border-white/[0.08] px-4 py-3">
              <h3 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Вне справочника</h3>
              <p className="mb-2 text-[11px] text-zinc-600">
                Есть на сайте производителя, но в справочнике для этого нет поля. Если значение относится к параметру выше — перенесите его вручную.
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
