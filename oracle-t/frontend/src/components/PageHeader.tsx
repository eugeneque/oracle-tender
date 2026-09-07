import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

export function PageHeader({
  breadcrumb,
  title,
  icon,
  actions,
  /** Плотная шапка для страниц, где содержимое живёт в панелях фиксированной высоты:
   * каждый лишний десяток пикселей наверху забирается у карточки, которую читают. */
  compact = false,
}: {
  breadcrumb: string[];
  title: string;
  icon?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={compact ? "mb-2 shrink-0" : "mb-8"}>
      <div
        className={`flex items-center gap-1.5 text-zinc-500 ${compact ? "mb-0.5 text-xs" : "mb-4 text-sm"}`}
      >
        {breadcrumb.map((crumb, i) => (
          <span key={crumb} className="flex items-center gap-1.5">
            {i > 0 && <ChevronRight size={14} className="text-zinc-700" />}
            <span className={i === breadcrumb.length - 1 ? "text-zinc-300" : ""}>
              {crumb}
            </span>
          </span>
        ))}
      </div>
      <div className="flex items-center justify-between gap-4">
        <div className={`flex items-center ${compact ? "gap-2" : "gap-3"}`}>
          {icon}
          <h1
            className={`font-bold tracking-tight text-white ${compact ? "text-xl" : "text-3xl"}`}
          >
            {title}
          </h1>
        </div>
        {actions}
      </div>
    </div>
  );
}
