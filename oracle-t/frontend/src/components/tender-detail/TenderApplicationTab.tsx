import { AlertTriangle, Check, Circle } from "lucide-react";

import type { CompanyProfile, TenderDocument } from "../../api/types";

/**
 * Вкладка «Заявка» (раздел 5.6 ТЗ) — чек-лист обязательных документов.
 *
 * Реализуется в последнюю очередь (решение с созвона 02.09.2026), но структура заложена
 * сразу. Сейчас вкладка делает единственное, что можно сделать честно и без догадок:
 * показывает, чего не хватает в профиле компании для формирования заявки, и перечисляет
 * стандартный состав пакета. Автосборка появится, когда будут утверждены формы.
 */

const CHECKLIST = [
  "Заявка на участие по форме заказчика",
  "Согласие на поставку товара",
  "Документы, подтверждающие соответствие требованиям (лицензии, СРО)",
  "Декларация о принадлежности к СМП/СОНКО (если требуется)",
  "Техническое предложение с характеристиками прибора",
  "Обеспечение заявки (платёжное поручение или банковская гарантия)",
];

export function TenderApplicationTab({
  profile,
  documents,
}: {
  profile: CompanyProfile | null;
  documents: TenderDocument[] | null;
}) {
  const missing: string[] = [];
  if (!profile) missing.push("профиль компании не заполнен");
  else {
    if (!profile.bank_requisites) missing.push("банковские реквизиты");
    if (!profile.letterhead_file_path) missing.push("фирменный бланк");
    if (profile.licenses.length === 0) missing.push("перечень допусков и лицензий");
  }

  const hasTzDocument = (documents ?? []).some(
    (document) => document.document_class === "tz_description",
  );

  return (
    <div className="space-y-4">
      {missing.length > 0 && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-500/20 bg-amber-500/[0.06] px-3 py-2.5 text-xs text-amber-300">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>
            Для формирования заявки не хватает данных компании: {missing.join(", ")}. Заполните
            их в разделе «Настройки → Профиль компании».
          </span>
        </div>
      )}

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Состав пакета документов
        </h4>
        <ul className="space-y-1.5">
          {CHECKLIST.map((item) => (
            <li key={item} className="flex items-start gap-2 text-sm text-zinc-300">
              <Circle size={12} className="mt-1 shrink-0 text-zinc-600" />
              {item}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Что уже есть в системе
        </h4>
        <ul className="space-y-1.5 text-sm text-zinc-300">
          <li className="flex items-center gap-2">
            {hasTzDocument ? (
              <Check size={13} className="text-emerald-400" />
            ) : (
              <Circle size={12} className="text-zinc-600" />
            )}
            Техническое задание закупки {hasTzDocument ? "загружено" : "не найдено среди документов"}
          </li>
          <li className="flex items-center gap-2">
            {profile?.is_filled ? (
              <Check size={13} className="text-emerald-400" />
            ) : (
              <Circle size={12} className="text-zinc-600" />
            )}
            Профиль компании {profile?.is_filled ? "заполнен" : "не заполнен"}
          </li>
        </ul>
      </section>

      <p className="text-[11px] text-zinc-600">
        Автоматическое формирование заявки делается в последнюю очередь (решение с созвона
        02.09.2026): сначала должны быть утверждены формы документов заказчиков.
      </p>
    </div>
  );
}
