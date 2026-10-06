import { Ban, CheckCircle2 } from "lucide-react";

import type { Tender } from "../api/types";
import { formatDate } from "../utils/format";

/** Метка «уже посмотрели» (06.10.2026): специалист отметил закупку «Релевантна» или
 * «Неактуально», и коллеги видят это в любом списке — с автором и датой, чтобы закупку не
 * проверяли второй раз. У неотмеченных закупок метки нет. */
export function RelevanceMark({
  tender,
  compact = false,
}: {
  tender: Pick<Tender, "relevance_status" | "relevance_marked_by_name" | "relevance_marked_at">;
  /** Только значок с подсказкой — для узких мест (таблица). */
  compact?: boolean;
}) {
  const relevant = tender.relevance_status === "confirmed";
  const rejected = tender.relevance_status === "rejected";
  if (!relevant && !rejected) return null;

  const label = relevant ? "Релевантный" : "Неактуальный";
  const who = tender.relevance_marked_by_name;
  const when = formatDate(tender.relevance_marked_at);
  const title = [label, who && `отметил(а) ${who}`, when].filter(Boolean).join(" · ");
  const Icon = relevant ? CheckCircle2 : Ban;
  const tone = relevant
    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
    : "border-zinc-500/30 bg-zinc-500/10 text-zinc-400";

  if (compact) {
    return (
      <span className={`inline-flex shrink-0 ${relevant ? "text-emerald-400" : "text-zinc-500"}`} title={title}>
        <Icon size={13} aria-label={label} />
      </span>
    );
  }
  return (
    <span
      className={`inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] leading-none ${tone}`}
      title={title}
    >
      <Icon size={11} className="shrink-0" />
      <span className="shrink-0">{label}</span>
      {who && <span className="truncate opacity-75">· {who}</span>}
    </span>
  );
}
