import type { Tender } from "../api/types";

/** Ресурсы, где нашлась закупка, — подпись «ЕИС · Госплан». В канале «Все» (08.11.2026)
 * дубли между каналами схлопнуты в одну карточку, и бэкенд кладёт в `found_in` все
 * источники; в остальных каналах список пуст, и подпись — своя площадка записи. */
export function foundInLabel(tender: Tender): string {
  const found = tender.found_in ?? [];
  return found.length > 0 ? found.map((source) => source.name).join(" · ") : tender.source.name;
}

/** Метка «Ресурсы» на строке списка — только в канале «Все», где без неё не видно,
 * откуда пришла закупка. */
export function FoundInSources({ tender, className = "" }: { tender: Tender; className?: string }) {
  const found = tender.found_in ?? [];
  if (found.length === 0) return null;
  return (
    <span
      className={`flex min-w-0 flex-wrap items-center gap-1 ${className}`}
      title={`Нашлась: ${found.map((source) => source.name).join(", ")}`}
    >
      {found.map((source) => (
        <span
          key={source.key}
          className="truncate rounded-md bg-white/5 px-1.5 py-px text-[10.5px] font-medium text-zinc-400"
        >
          {source.name}
        </span>
      ))}
    </span>
  );
}
