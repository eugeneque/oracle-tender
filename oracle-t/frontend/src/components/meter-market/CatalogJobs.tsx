import type { MarketCatalogJob } from "../../api/types";

function resultText(job: MarketCatalogJob): string {
  if (job.state === "running") return "выполняется…";
  if (job.state === "error") return job.error ?? "Ошибка";
  const result = job.result ?? {};
  const parts = [
    ["типов", result.types],
    ["скриптом", result.script],
    ["сопоставлено", result.mapped],
    ["новых", result.created],
    ["ошибок", result.failed],
  ].filter(([, value]) => typeof value === "number").map(([label, value]) => `${label} ${String(value)}`);
  return `готово${parts.length ? `: ${parts.join(", ")}` : ""}`;
}

export function CatalogJobs({ jobs }: { jobs: MarketCatalogJob[] }) {
  return (
    <>
      {jobs.slice(-3).map((job) => (
        <div key={job.key} className={`rounded-lg px-3 py-2 text-xs ${job.state === "error" ? "bg-red-500/10 text-red-300" : job.state === "done" ? "bg-emerald-500/10 text-emerald-300" : "bg-indigo-500/10 text-indigo-200"}`}>
          {job.label}: {resultText(job)}
        </div>
      ))}
    </>
  );
}
