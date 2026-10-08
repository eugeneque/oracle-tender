import { useState } from "react";
import { Check } from "lucide-react";

import { TENDER_VIEWS, loadTendersView, saveTendersView } from "../../utils/tendersView";
import type { TenderViewKey } from "../../utils/tendersView";
import { SettingsGroup } from "./ui";

/** Вид списка на странице тендеров: две панели, доска или таблица. Хранится в браузере. */
export function TendersViewSection() {
  const [view, setView] = useState<TenderViewKey>(loadTendersView);

  return (
    <SettingsGroup
      id="interface-tenders-view"
      label="Вид списка тендеров"
    >
      <div role="radiogroup" aria-label="Вид списка тендеров" className="grid gap-3 sm:grid-cols-3">
        {TENDER_VIEWS.map((item) => {
          const isActive = view === item.key;
          const Icon = item.icon;
          return (
            <button
              key={item.key}
              type="button"
              role="radio"
              aria-checked={isActive}
              onClick={() => {
                setView(item.key);
                saveTendersView(item.key);
              }}
              className={`rounded-2xl border p-4 text-left transition-colors ${
                isActive
                  ? "border-indigo-500/40 bg-indigo-500/[0.08]"
                  : "border-white/[0.08] bg-white/[0.03] hover:border-white/[0.14]"
              }`}
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-medium text-zinc-100">
                  <Icon size={16} className={isActive ? "text-indigo-300" : "text-zinc-500"} />
                  {item.label}
                </span>
                {isActive && <Check size={15} className="text-indigo-300" />}
              </div>
              <p className="text-xs leading-relaxed text-zinc-500">{item.hint}</p>
            </button>
          );
        })}
      </div>
    </SettingsGroup>
  );
}
