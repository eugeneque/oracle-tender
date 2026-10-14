import { BadgeCheck, ExternalLink } from "lucide-react";

import type { Characteristic, DataSource, MeterParameter, MeterParameterFact, MeterParameterSourceKey } from "../../api/types";
import { AiBadge } from "./AiBadge";
import { SOURCE_STATE_HINT } from "./dataSourceStates";

// Карточка модели по 39 параметрам файла тендерного отдела «Параметры для ПУ» (28.09.2026):
// в порядке файла, со значениями из справочника характеристик и сведениями из реестров
// допуска, списков ПО верхнего уровня и каталога «Ready for Astra». Пустое поле видно
// сразу — значение вписывается прямо отсюда и ложится в то же поле Приложения C.
//
// 13.10.2026 (замечания тендерного отдела): «требует проверки» — только у значений, которые
// вычитала модель ИИ; у взятых из таблицы сайта или карточки реестра вместо этого источник
// со ссылкой. У пустого параметра — где ответ обычно есть и опрошен ли этот источник.

const FACT_TONES: Record<MeterParameterFact["tone"], string> = {
  ok: "text-emerald-400",
  bad: "text-rose-400",
  warn: "text-amber-400",
  muted: "text-zinc-500",
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

type Props = {
  parameters: MeterParameter[];
  sourceLabels: Record<string, string>;
  // Ссылка на документ, из которого взято значение, — по источнику характеристики.
  sourceUrls: Record<string, string | null>;
  dataSources: DataSource[] | null;
  isAdmin: boolean;
  busyKey: string | null;
  onEdit: (groupName: string, fieldName: string, current: Characteristic | null) => void;
  onVerify: (characteristic: Characteristic) => void;
};

export function CharacteristicOrigin({
  characteristic,
  sourceLabels,
  sourceUrls,
}: {
  characteristic: Characteristic;
  sourceLabels: Record<string, string>;
  sourceUrls: Record<string, string | null>;
}) {
  const label = sourceLabels[characteristic.source] ?? characteristic.source;
  const url = sourceUrls[characteristic.source];
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5 text-[11px] text-zinc-500">
      {url ? (
        <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 hover:text-zinc-300">
          {label} <ExternalLink size={10} />
        </a>
      ) : (
        <span>{label}</span>
      )}
      {characteristic.verified_by_user ? (
        <span className="inline-flex items-center gap-0.5 text-emerald-400">
          <BadgeCheck size={11} /> проверено
        </span>
      ) : (
        characteristic.ai_extracted && (
          <span className="inline-flex items-center gap-1 text-amber-400">
            <AiBadge compact title="Значение вычитала модель ИИ" /> требует проверки
          </span>
        )
      )}
    </span>
  );
}

function whereToLook(parameter: MeterParameter, dataSources: DataSource[] | null): string | null {
  if (!parameter.sources.length) return null;
  const byKey = new Map((dataSources ?? []).map((source) => [source.key, source]));
  const places = parameter.sources.map((key) => {
    const state = byKey.get(key)?.state;
    return state ? `${WHERE_TO_LOOK[key]} — ${SOURCE_STATE_HINT[state]}` : WHERE_TO_LOOK[key];
  });
  return `Нет данных. Где искать: ${places.join("; ")}.`;
}

export function MeterParametersTable({
  parameters,
  sourceLabels,
  sourceUrls,
  dataSources,
  isAdmin,
  busyKey,
  onEdit,
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
              <tr key={parameter.no} className="border-t border-white/[0.06] align-top">
                <td className="w-10 py-2 pr-2 font-mono text-[11px] text-zinc-500">П{parameter.no}</td>
                <td className="w-[34%] py-2 pr-4">
                  <span className={parameter.filled ? "text-zinc-300" : "text-zinc-500"}>{parameter.name}</span>
                  {parameter.note && (
                    <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-zinc-600" title={parameter.note}>
                      {parameter.note}
                    </p>
                  )}
                </td>
                <td className="py-2">
                  <div className="space-y-1">
                    {fields.map((item) => {
                      const c = item.characteristic;
                      const name = item.label || item.field_name;
                      const label = parameter.fields.length > 1 ? `${name}: ` : "";
                      return (
                        <div key={`${item.group_name}/${item.field_name}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                          <span className={c?.value ? "text-zinc-100" : "text-zinc-600"}>
                            {label && <span className="text-zinc-500">{label}</span>}
                            {c?.value || "—"}
                          </span>
                          {c?.value && <CharacteristicOrigin characteristic={c} sourceLabels={sourceLabels} sourceUrls={sourceUrls} />}
                          {isAdmin && (
                            <span className="text-[11px]">
                              <button
                                onClick={() => onEdit(item.group_name, item.field_name, c)}
                                className="text-indigo-400 hover:underline"
                                title={`${item.group_name} → ${item.field_name}`}
                              >
                                {c?.value ? "править" : "заполнить"}
                              </button>
                              {c?.value && (
                                <button
                                  onClick={() => onVerify(c)}
                                  disabled={busyKey === `ch-${c.id}`}
                                  className="ml-2 text-zinc-400 hover:underline disabled:opacity-50"
                                >
                                  {c.verified_by_user ? "снять" : "подтвердить"}
                                </button>
                              )}
                            </span>
                          )}
                        </div>
                      );
                    })}
                    {parameter.facts.map((fact, index) => (
                      <div key={index} className={`text-[12px] ${FACT_TONES[fact.tone]}`}>
                        {fact.text}
                        {fact.url && (
                          <a href={fact.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex align-middle text-zinc-500 hover:text-zinc-300">
                            <ExternalLink size={11} />
                          </a>
                        )}
                      </div>
                    ))}
                    {hint && <p className="text-[11px] leading-snug text-zinc-500">{hint}</p>}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
