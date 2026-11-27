import { Check, Pencil, X } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { MarketAttributeMetadata } from "../../api/types";
import { BUTTON } from "./ModificationForm";

const SOURCE = { script: "скрипт", ai: "ИИ", manual: "вручную", import: "Excel" } as const;

export function AttributeCell({
  modificationId,
  field,
  value,
  metadata,
  canEdit,
  onEdit,
  onDone,
  onError,
}: {
  modificationId: number;
  field: string;
  value: string;
  metadata?: MarketAttributeMetadata;
  canEdit: boolean;
  onEdit: () => void;
  onDone: (message: string) => void;
  onError: (err: unknown, fallback: string) => void;
}) {
  const pending = metadata?.status === "pending";
  const unresolved = metadata?.status === "unresolved";
  const act = async (action: "confirm" | "reject") => {
    try {
      await api.post(`/meter-market/modifications/${modificationId}/attributes/${field}/${action}`, {});
      onDone(action === "confirm" ? `«${value}» подтверждено аналитиком` : `Предложение ИИ для поля «${field}» отклонено`);
    } catch (err) {
      onError(err instanceof ApiError ? err : new Error("Не удалось сохранить решение"), "Не удалось сохранить решение");
    }
  };
  return (
    <div className={`min-w-[7rem] rounded-md px-1.5 py-1 ${pending ? "bg-amber-400/10 text-amber-200" : unresolved ? "bg-white/[0.03] text-zinc-500" : ""}`}>
      <div className="flex items-center gap-1.5">
        <span>{value}</span>
        {pending && <span className="text-[10px] text-amber-300">ИИ · проверить</span>}
        {unresolved && <span className="text-[10px]">не определено</span>}
        {!pending && !unresolved && metadata && <span className="text-[10px] text-zinc-600">{SOURCE[metadata.source]}</span>}
        {metadata && canEdit && (
          <button type="button" className="text-zinc-600 hover:text-zinc-300" title="Изменить характеристику" onClick={onEdit}>
            <Pencil size={11} />
          </button>
        )}
      </div>
      {metadata && (pending || unresolved) && (
        <details className="mt-1 text-[11px] font-normal text-zinc-500">
          <summary className="cursor-pointer">Основание</summary>
          {metadata.reason && <p className="mt-1 max-w-md whitespace-pre-wrap">{metadata.reason}</p>}
          {metadata.evidence.map((item, index) => (
            <blockquote key={index} className="mt-1 max-w-md border-l border-white/10 pl-2">
              {item.code && <span className="font-mono text-zinc-400">{item.code} · </span>}
              {item.page && <span>лист {item.page} · </span>}
              {item.quote}
            </blockquote>
          ))}
        </details>
      )}
      {pending && canEdit && (
        <div className="mt-1 flex gap-1">
          <button type="button" className={BUTTON} onClick={() => void act("confirm")}><Check size={11} /> Подтвердить</button>
          <button type="button" className={BUTTON} onClick={() => void act("reject")}><X size={11} /> Отклонить</button>
        </div>
      )}
    </div>
  );
}
