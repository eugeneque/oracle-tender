import { useEffect, useRef, useState } from "react";
import { Check, ExternalLink, Info, Loader2, Pencil } from "lucide-react";

import type { Characteristic, DataSource, MeterParameter, MeterParameterFact, MeterParameterSourceKey } from "../../api/types";
import { Tooltip } from "../ui/Tooltip";
import { SOURCE_STATE_HINT } from "./dataSourceStates";

// Карточка модели по 39 параметрам файла тендерного отдела «Параметры для ПУ» (28.09.2026):
// в порядке файла, со значениями из справочника характеристик и сведениями из реестров
// допуска, списков ПО верхнего уровня и каталога «Ready for Astra».
//
// Редизайн 14.10.2026 (замечание: «жёлтый, фиолетовый, серый, руководство пользователя,
// статусы — не разобраться»). У значения на виду только само значение. Источник, «вычитано
// моделью ИИ» и «где искать пустое» — в подсказке; единственная видимая пометка — янтарная
// точка у значения, которое вычитала модель и никто не проверил. Действия — иконки при
// наведении: карандаш правит прямо в строке (без окна браузера), галочка отмечает
// проверенным и показывается только там, где проверять есть что.

// Тон сведений из реестров — точкой, а не цветом всего текста (дизайн-система: статус —
// только точка).
const FACT_DOTS: Record<MeterParameterFact["tone"], string> = {
  ok: "bg-emerald-400",
  bad: "bg-rose-400",
  warn: "bg-amber-400",
  muted: "bg-zinc-600",
};

const WHERE_TO_LOOK: Record<MeterParameterSourceKey, string> = {
  site: "сайт производителя",
  fgis: "«Описание типа»",
  manual: "руководство по эксплуатации",
  gisp: "реестр ГИСП (ПП 719)",
  zak: "перечень ЗАК Россетей",
  upper_software: "списки оборудования ПО верхнего уровня",
  astra: "каталог Ready for Astra",
};

// Действия появляются при наведении на строку; на сенсорных экранах наведения нет — там они
// видны всегда.
const HOVER_ACTIONS =
  "opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100";

function originText(characteristic: Characteristic, sourceLabels: Record<string, string>): string {
  const label = sourceLabels[characteristic.source] ?? characteristic.source;
  if (characteristic.verified_by_user) return `Источник: ${label}\nПроверено человеком`;
  if (characteristic.ai_extracted) return `Источник: ${label}\nВычитано моделью ИИ — не проверено`;
  return `Источник: ${label}`;
}

function needsCheck(c: Characteristic | null): boolean {
  return Boolean(c?.value && c.ai_extracted && !c.verified_by_user);
}

// Одно значение характеристики: подпись поля (если у параметра их несколько), значение,
// точка «не проверено», ссылка на источник и действия администратора.
export function CharacteristicLine({
  label,
  characteristic,
  emptyHint,
  sourceLabels,
  sourceUrls,
  isAdmin,
  verifying,
  onSave,
  onVerify,
}: {
  label?: string;
  characteristic: Characteristic | null;
  emptyHint?: string | null;
  sourceLabels: Record<string, string>;
  sourceUrls: Record<string, string | null>;
  isAdmin: boolean;
  verifying: boolean;
  onSave: (value: string) => Promise<void>;
  onVerify: (characteristic: Characteristic) => void;
}) {
  const c = characteristic;
  const [draft, setDraft] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const editing = draft !== null;

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const save = async () => {
    if (draft === null || saving) return;
    if (draft.trim() === (c?.value ?? "")) {
      setDraft(null);
      return;
    }
    setSaving(true);
    try {
      await onSave(draft.trim());
      setDraft(null);
    } catch {
      // Ошибку показывает окно модели; строка остаётся в режиме правки, ввод не теряется.
    } finally {
      setSaving(false);
    }
  };

  const url = c ? sourceUrls[c.source] : null;

  return (
    <div className="group flex min-h-[26px] items-center gap-2">
      {label && <span className="shrink-0 text-zinc-500">{label}</span>}
      {editing ? (
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void save();
              if (e.key === "Escape") {
                // Esc закрывает правку, а не всё окно модели.
                e.stopPropagation();
                e.nativeEvent.stopImmediatePropagation();
                setDraft(null);
              }
            }}
            onBlur={() => void save()}
            disabled={saving}
            className="min-w-0 flex-1 rounded-md border border-indigo-500/40 bg-black/30 px-2 py-0.5 text-sm text-zinc-100 focus:outline-none"
          />
          {saving && <Loader2 size={13} className="animate-spin text-zinc-500" />}
        </span>
      ) : c?.value ? (
        <Tooltip content={originText(c, sourceLabels)} className="min-w-0 items-center gap-1.5">
          {needsCheck(c) && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" aria-label="не проверено" />}
          <span className="text-zinc-100">{c.value}</span>
        </Tooltip>
      ) : (
        <Tooltip content={emptyHint}>
          <span className="text-zinc-600">—</span>
        </Tooltip>
      )}

      {!editing && (
        <span className={`flex shrink-0 items-center gap-0.5 ${HOVER_ACTIONS}`}>
          {url && c?.value && (
            <Tooltip content="Открыть источник">
              <a
                href={url}
                target="_blank"
                rel="noreferrer"
                aria-label="Открыть источник"
                className="rounded p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
              >
                <ExternalLink size={13} />
              </a>
            </Tooltip>
          )}
          {isAdmin && (
            <Tooltip content={c?.value ? "Изменить" : "Заполнить"}>
              <button
                onClick={() => setDraft(c?.value ?? "")}
                aria-label={c?.value ? "Изменить" : "Заполнить"}
                className="rounded p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
              >
                <Pencil size={13} />
              </button>
            </Tooltip>
          )}
          {isAdmin && c?.value && (needsCheck(c) || c.verified_by_user) && (
            <Tooltip content={c.verified_by_user ? "Снять отметку «проверено»" : "Отметить как проверенное"}>
              <button
                onClick={() => onVerify(c)}
                disabled={verifying}
                aria-label={c.verified_by_user ? "Снять отметку «проверено»" : "Отметить как проверенное"}
                className={`rounded p-1 hover:bg-white/5 disabled:opacity-50 ${
                  c.verified_by_user ? "text-emerald-400" : "text-zinc-500 hover:text-zinc-200"
                }`}
              >
                <Check size={13} />
              </button>
            </Tooltip>
          )}
        </span>
      )}
    </div>
  );
}

function whereToLook(parameter: MeterParameter, dataSources: DataSource[] | null): string {
  if (!parameter.sources.length) return "Нет данных";
  const byKey = new Map((dataSources ?? []).map((source) => [source.key, source]));
  const places = parameter.sources.map((key) => {
    const state = byKey.get(key)?.state;
    return state ? `• ${WHERE_TO_LOOK[key]} — ${SOURCE_STATE_HINT[state]}` : `• ${WHERE_TO_LOOK[key]}`;
  });
  return `Нет данных. Где искать:\n${places.join("\n")}`;
}

type Props = {
  parameters: MeterParameter[];
  sourceLabels: Record<string, string>;
  // Ссылка на документ, из которого взято значение, — по источнику характеристики.
  sourceUrls: Record<string, string | null>;
  dataSources: DataSource[] | null;
  isAdmin: boolean;
  busyKey: string | null;
  onSave: (groupName: string, fieldName: string, value: string) => Promise<void>;
  onVerify: (characteristic: Characteristic) => void;
};

export function MeterParametersTable({
  parameters,
  sourceLabels,
  sourceUrls,
  dataSources,
  isAdmin,
  busyKey,
  onSave,
  onVerify,
}: Props) {
  if (parameters.length === 0) {
    return <p className="text-xs text-zinc-500">Загружаю параметры…</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <tbody>
          {parameters.map((parameter) => {
            // Читателю пустые подполя ничего не говорят — показываются только заполненные;
            // администратору нужны все, чтобы заполнить.
            const fields = isAdmin ? parameter.fields : parameter.fields.filter((item) => item.characteristic?.value);
            const hint = !parameter.filled ? whereToLook(parameter, dataSources) : null;
            return (
              <tr key={parameter.no} className="border-t border-white/[0.06] align-top first:border-t-0">
                <td className="w-10 py-2.5 pr-2 font-mono text-[11px] leading-[26px] text-zinc-600">{parameter.no}</td>
                <td className="w-[38%] py-2.5 pr-6">
                  <span className="inline-flex items-start gap-1.5 leading-[26px]">
                    <span className={parameter.filled ? "text-zinc-300" : "text-zinc-500"}>{parameter.name}</span>
                    {parameter.note && (
                      <Tooltip content={parameter.note} className="mt-[7px] shrink-0">
                        <Info size={12} className="text-zinc-600 hover:text-zinc-300" aria-label="Пояснение" />
                      </Tooltip>
                    )}
                  </span>
                </td>
                <td className="py-2.5">
                  {fields.map((item) => (
                    <CharacteristicLine
                      key={`${item.group_name}/${item.field_name}`}
                      label={parameter.fields.length > 1 ? item.label || item.field_name : undefined}
                      characteristic={item.characteristic}
                      emptyHint={hint}
                      sourceLabels={sourceLabels}
                      sourceUrls={sourceUrls}
                      isAdmin={isAdmin}
                      verifying={busyKey === `ch-${item.characteristic?.id}`}
                      onSave={(value) => onSave(item.group_name, item.field_name, value)}
                      onVerify={onVerify}
                    />
                  ))}
                  {parameter.facts.map((fact, index) => (
                    <div key={index} className="flex min-h-[26px] items-center gap-2 text-[13px] text-zinc-300">
                      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${FACT_DOTS[fact.tone]}`} aria-hidden="true" />
                      {fact.text}
                      {fact.url && (
                        <a href={fact.url} target="_blank" rel="noreferrer" className="text-zinc-500 hover:text-zinc-200" aria-label="Открыть">
                          <ExternalLink size={12} />
                        </a>
                      )}
                    </div>
                  ))}
                  {fields.length === 0 && parameter.facts.length === 0 && (
                    <Tooltip content={hint}>
                      <span className="leading-[26px] text-zinc-600">—</span>
                    </Tooltip>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
