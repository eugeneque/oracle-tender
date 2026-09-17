import { useCallback, useEffect, useState } from "react";
import {
  BookOpen,
  ChevronDown,
  ChevronRight,
  GraduationCap,
  Layers,
  Loader2,
  Search,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  CatalogTask,
  DescriptionIngestOutcome,
  DiscoveryOutcome,
  ModificationsOutcome,
  UnknownField,
} from "../../api/types";

// Обучение справочника по Аршину и документации (замечание заказчика 15.09.2026).
//
// Что здесь и почему это отдельный блок. Реестр узнаёт о новом исполнении прибора раньше
// сайта производителя (у НАРТИС-И100 корпус W115 появился в редакции 2 «Описания типа», а
// в каталоге на сайте его нет), а документация на него уже лежит на официальном сайте и
// находится поисковиком. Полный проход — в фоне: карточки Аршина → исполнения → «Описание
// типа» → поиск документации → руководства. Отдельные шаги — синхронно, чтобы
// администратор видел результат сразу, а не «задача поставлена».

export function RegistryLearningSection({
  manufacturerId,
  isAdmin,
  onChanged,
}: {
  manufacturerId: string | null;
  isAdmin: boolean;
  // Каталог мог измениться (заведены исполнения, добавлены характеристики) — родитель
  // перечитывает модели и коды СИ.
  onChanged: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unknownFields, setUnknownFields] = useState<UnknownField[]>([]);

  const loadUnknown = useCallback(async (id: string) => {
    setUnknownFields(await api.get<UnknownField[]>(`/manufacturers/${id}/unknown-fields?limit=30`));
  }, []);

  useEffect(() => {
    setUnknownFields([]);
    setNotice(null);
    setError(null);
    if (!manufacturerId) return;
    loadUnknown(manufacturerId).catch((err) =>
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить сводку характеристик")
    );
  }, [manufacturerId, loadUnknown]);

  const run = async (key: string, action: () => Promise<string>) => {
    if (!manufacturerId) return;
    setBusy(key);
    setError(null);
    try {
      setNotice(await action());
      await onChanged();
      await loadUnknown(manufacturerId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить действие");
    } finally {
      setBusy(null);
    }
  };

  const handleLearn = () =>
    run("learn", async () => {
      await api.post<CatalogTask>(`/manufacturers/${manufacturerId}/learn`);
      return (
        "Обучение по Аршину запущено в фоне: карточки реестра → исполнения → «Описание типа» → " +
        "поиск документации → руководства. Это минуты; итог — в очереди справочника и в журнале."
      );
    });

  const handleModifications = () =>
    run("modifications", async () => {
      const o = await api.post<ModificationsOutcome>(
        `/manufacturers/${manufacturerId}/discover-modifications`
      );
      const parts = [
        `исполнений в реестре ${o.modifications_seen}`,
        `заведено новых ${o.products_created}`,
        `уже в каталоге ${o.already_known}`,
      ];
      if (o.products_linked) parts.push(`привязано к кодам СИ ${o.products_linked}`);
      const names = o.created_names.length ? ` Новые: ${o.created_names.join(", ")}.` : "";
      return `Исполнения из Аршина: ${parts.join(", ")}.${names}`;
    });

  const handleDescriptions = () =>
    run("descriptions", async () => {
      const o = await api.post<DescriptionIngestOutcome>(
        `/manufacturers/${manufacturerId}/ingest-description-types?limit=10`
      );
      const parts = [
        `документов вычитано ${o.documents_read}`,
        `моделей обновлено ${o.products_updated}`,
        `характеристик ${o.characteristics_saved}`,
      ];
      if (o.modifications_decoded) parts.push(`исполнений расшифровано ${o.modifications_decoded}`);
      if (o.skipped_up_to_date) parts.push(`актуальны ${o.skipped_up_to_date}`);
      if (o.skipped_no_url) parts.push(`без ссылки на документ ${o.skipped_no_url}`);
      if (o.failed) parts.push(`не удалось ${o.failed}`);
      return `«Описание типа» (Аршин): ${parts.join(", ")}.`;
    });

  const handleDocuments = () =>
    run("documents", async () => {
      const o = await api.post<DiscoveryOutcome>(
        `/manufacturers/${manufacturerId}/discover-documents?limit=20`
      );
      const parts = [
        `проверено моделей ${o.products_checked}`,
        `найдено ${o.found}`,
        `не найдено ${o.not_found}`,
      ];
      if (o.skipped_have_link) parts.push(`уже со ссылкой ${o.skipped_have_link}`);
      const found = o.messages.length ? ` ${o.messages.slice(0, 3).join("; ")}` : "";
      return `Поиск документации на официальном сайте (Яндекс): ${parts.join(", ")}.${found}`;
    });

  const button = (
    key: string,
    label: string,
    title: string,
    icon: React.ReactNode,
    onClick: () => void
  ) => (
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

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <div
        className={`flex flex-wrap items-center justify-between gap-2 px-5 py-3 ${
          open ? "border-b border-white/[0.08]" : ""
        }`}
      >
        <button onClick={() => setOpen((v) => !v)} className="flex items-start gap-2 text-left" aria-expanded={open}>
          <span className="mt-0.5 text-zinc-500">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
          <span>
            <h2 className="text-sm font-semibold text-zinc-100">
              Обучение по Аршину
              {unknownFields.length > 0 && (
                <span className="ml-1.5 text-zinc-500">· вне справочника {unknownFields.length}</span>
              )}
            </h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              Исполнения и характеристики из реестра ФГИС, документация с официального сайта через поиск.
            </p>
          </span>
        </button>
        {isAdmin && manufacturerId && (
          <div className="flex flex-wrap gap-2">
            {button(
              "learn",
              "Обучить (в фоне)",
              "Полный проход: карточки Аршина → исполнения → «Описание типа» → поиск документации → руководства",
              <GraduationCap size={13} />,
              handleLearn
            )}
            {button(
              "modifications",
              "Исполнения из реестра",
              "Завести в каталог исполнения, представленные на испытания в Аршине, которых нет на сайте производителя",
              <Layers size={13} />,
              handleModifications
            )}
            {button(
              "descriptions",
              "Разобрать «Описания типа»",
              "Загрузить «Описание типа» и разнести характеристики по моделям (до 10 документов за запуск)",
              <BookOpen size={13} />,
              handleDescriptions
            )}
            {button(
              "documents",
              "Найти документацию",
              "Найти через Яндекс руководства на официальном сайте для моделей без ссылки (до 20 моделей за запуск)",
              <Search size={13} />,
              handleDocuments
            )}
          </div>
        )}
      </div>

      {(notice || error) && (
        <div className="px-5 pt-3">
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
        </div>
      )}

      <div className="px-5 py-3" hidden={!open}>
        {unknownFields.length === 0 ? (
          <p className="text-xs text-zinc-500">
            Характеристик вне Приложения C пока нет: всё, что извлечено из документов, нашло своё поле в справочнике.
          </p>
        ) : (
          <>
            <p className="mb-2 text-xs text-zinc-500">
              Характеристики, которые встречаются в документации приборов, но поля для них в
              Приложении C нет, — кандидаты на расширение справочника. Значения сохранены у моделей
              и не потеряны.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <tbody>
                  {unknownFields.map((f) => (
                    <tr key={f.field_name} className="border-t border-white/[0.06]">
                      <td className="py-1.5 pr-4 text-zinc-200">{f.field_name}</td>
                      <td className="py-1.5 pr-4 text-zinc-500">моделей: {f.products}</td>
                      <td className="max-w-md truncate py-1.5 text-zinc-400" title={f.sample}>
                        {f.sample}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
