import type { MarketCatalogJob } from "../../api/types";

function resultCounts(result: Record<string, unknown>): string {
  return [
    ["типов", result.types],
    ["скриптом", result.script],
    ["сопоставлено", result.mapped],
    ["новых", result.created],
    ["ошибок", result.failed],
  ].filter(([, value]) => typeof value === "number").map(([label, value]) => `${label} ${String(value)}`).join(", ");
}

function resultText(job: MarketCatalogJob): string {
  if (job.state === "running" && job.progress) {
    const current = job.progress.current;
    const total = job.progress.total === null ? "?" : job.progress.total;
    return `выполняется: ${current} из ${total}${job.progress.mit_number ? ` · ${job.progress.mit_number}` : ""} · ошибок ${job.progress.failed}`;
  }
  if (job.state === "running") return "выполняется…";
  const counts = resultCounts(job.result ?? {});
  if (job.state === "error") return `${job.error ?? "Ошибка"}${counts ? ` (${counts})` : ""}`;
  return `готово${counts ? `: ${counts}` : ""}`;
}

export function CatalogJobs({ jobs, onCancel }: { jobs: MarketCatalogJob[]; onCancel?: (key: string) => void }) {
  return (
    <>
      {jobs.slice(-3).map((job) => (
        <div key={job.key} className={`rounded-lg px-3 py-2 text-xs ${job.state === "error" ? "bg-red-500/10 text-red-300" : job.state === "done" ? "bg-emerald-500/10 text-emerald-300" : "bg-indigo-500/10 text-indigo-200"}`}>
          <div className="flex items-center justify-between gap-3">
            <span>{job.label}: {resultText(job)}</span>
            {job.state === "running" && onCancel && <button type="button" className="shrink-0 text-red-300 hover:text-red-200" onClick={() => onCancel(job.key)}>Остановить</button>}
          </div>
        </div>
      ))}
    </>
  );
}
