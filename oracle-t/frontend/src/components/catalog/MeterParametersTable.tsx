import { BadgeCheck, ExternalLink } from "lucide-react";

import type { Characteristic, MeterParameter, MeterParameterFact } from "../../api/types";

// Карточка модели по 39 параметрам файла тендерного отдела «Параметры для ПУ» (28.09.2026):
// в порядке файла, со значениями из справочника характеристик и сведениями из реестров
// допуска, списков ПО верхнего уровня и каталога «Ready for Astra». Пустое поле видно
// сразу — значение вписывается прямо отсюда и ложится в то же поле Приложения C.

const FACT_TONES: Record<MeterParameterFact["tone"], string> = {
  ok: "text-emerald-400",
  bad: "text-rose-400",
  warn: "text-amber-400",
  muted: "text-zinc-500",
};

type Props = {
  parameters: MeterParameter[];
  sourceLabels: Record<string, string>;
  isAdmin: boolean;
  busyKey: string | null;
  onEdit: (groupName: string, fieldName: string, current: Characteristic | null) => void;
  onVerify: (characteristic: Characteristic) => void;
};

export function MeterParametersTable({ parameters, sourceLabels, isAdmin, busyKey, onEdit, onVerify }: Props) {
  if (parameters.length === 0) {
    return <p className="text-xs text-zinc-500">Загружаю параметры…</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <tbody>
          {parameters.map((parameter) => (
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
                  {parameter.fields.map((item) => {
                    const c = item.characteristic;
                    const label = parameter.fields.length > 1 ? `${item.field_name}: ` : "";
                    return (
                      <div key={`${item.group_name}/${item.field_name}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                        <span className={c?.value ? "text-zinc-100" : "text-zinc-600"}>
                          {label && <span className="text-zinc-500">{label}</span>}
                          {c?.value || "—"}
                        </span>
                        {c?.value && (
                          <span className="text-[11px] text-zinc-500">
                            {sourceLabels[c.source] ?? c.source}
                            {c.verified_by_user ? (
                              <span className="ml-1 inline-flex items-center gap-0.5 text-emerald-400">
                                <BadgeCheck size={11} /> проверено
                              </span>
                            ) : (
                              <span className="ml-1 text-amber-400">требует проверки</span>
                            )}
                          </span>
                        )}
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
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
