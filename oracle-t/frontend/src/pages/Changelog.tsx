import { useMemo, useState } from "react";
import { History } from "lucide-react";

import { AppShell } from "../components/AppShell";
import { CHANGELOG, CHANGE_KIND_LABELS } from "../content/changelog";
import type { ChangeKind } from "../content/changelog";

/**
 * «История обновлений» (05.10.2026) — журнал изменений Sova из меню аккаунта.
 *
 * «Что нового?» рассказывает о последних нововведениях подробно, плитками; здесь — всё подряд
 * и коротко, с датой, чтобы можно было ответить на «когда это поменялось» и «это уже есть?».
 */

const KIND_STYLE: Record<ChangeKind, string> = {
  new: "bg-emerald-500/10 text-emerald-300 ring-emerald-400/25",
  improved: "bg-sky-500/10 text-sky-300 ring-sky-400/25",
  fixed: "bg-amber-500/10 text-amber-300 ring-amber-400/25",
};

type Filter = "all" | ChangeKind;

function formatDate(iso: string): { day: string; month: string; year: string } {
  const date = new Date(`${iso}T12:00:00`);
  return {
    day: date.toLocaleDateString("ru-RU", { day: "numeric" }),
    month: date.toLocaleDateString("ru-RU", { month: "long", day: "numeric" }).replace(/^\d+\s/, ""),
    year: String(date.getFullYear()),
  };
}

export function ChangelogPage() {
  const [filter, setFilter] = useState<Filter>("all");

  const releases = useMemo(
    () =>
      CHANGELOG.map((release) => ({
        ...release,
        items: filter === "all" ? release.items : release.items.filter((item) => item.kind === filter),
      })).filter((release) => release.items.length > 0),
    [filter],
  );

  const counts = useMemo(() => {
    const result: Record<Filter, number> = { all: 0, new: 0, improved: 0, fixed: 0 };
    for (const release of CHANGELOG)
      for (const item of release.items) {
        result.all += 1;
        result[item.kind] += 1;
      }
    return result;
  }, []);

  const filters: { value: Filter; label: string }[] = [
    { value: "all", label: "Все" },
    { value: "new", label: CHANGE_KIND_LABELS.new },
    { value: "improved", label: CHANGE_KIND_LABELS.improved },
    { value: "fixed", label: CHANGE_KIND_LABELS.fixed },
  ];

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-6 py-10 sm:px-8">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <History size={15} />
              Sova
            </div>
            <h1 className="mt-1 text-4xl font-semibold tracking-tight text-zinc-50">
              История обновлений
            </h1>
            <p className="mt-2 max-w-xl text-sm text-zinc-500">
              Все изменения по датам выкладки — что появилось, что стало удобнее и что исправлено.
              Подробно о последних нововведениях — в «Что нового?».
            </p>
          </div>
          <div role="radiogroup" aria-label="Тип изменений" className="flex flex-wrap gap-1.5">
            {filters.map((item) => (
              <button
                key={item.value}
                role="radio"
                aria-checked={filter === item.value}
                onClick={() => setFilter(item.value)}
                className={`rounded-full border px-3 py-1 text-xs transition-colors ${
                  filter === item.value
                    ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
                    : "border-white/10 text-zinc-500 hover:text-zinc-300"
                }`}
              >
                {item.label}
                <span className="ml-1.5 tabular-nums opacity-60">{counts[item.value]}</span>
              </button>
            ))}
          </div>
        </div>

        <ol className="relative">
          {releases.map((release, index) => {
            const { day, month, year } = formatDate(release.date);
            return (
              <li
                key={release.date}
                className="relative grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-6 pb-10 sm:grid-cols-[6rem_minmax(0,1fr)]"
              >
                <div className="pt-1 text-right">
                  <div className="text-2xl font-semibold tabular-nums leading-none text-zinc-100">
                    {day}
                  </div>
                  <div className="mt-1 text-xs text-zinc-500">{month}</div>
                  <div className="text-[11px] text-zinc-600">{year}</div>
                </div>
                <div className="relative border-l border-white/[0.08] pl-6">
                  <span
                    className={`absolute -left-[5px] top-2.5 h-2.5 w-2.5 rounded-full ${
                      index === 0 ? "bg-indigo-400 ring-4 ring-indigo-500/20" : "bg-zinc-600"
                    }`}
                  />
                  <h2 className="text-lg font-semibold tracking-tight text-zinc-100">{release.title}</h2>
                  <ul className="mt-3 space-y-2.5">
                    {release.items.map((item, itemIndex) => (
                      <li key={itemIndex} className="flex gap-3 text-sm leading-relaxed text-zinc-300">
                        <span
                          className={`mt-0.5 h-fit shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium ring-1 ${KIND_STYLE[item.kind]}`}
                        >
                          {CHANGE_KIND_LABELS[item.kind]}
                        </span>
                        <span>{item.text}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </AppShell>
  );
}
