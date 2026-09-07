import {
  AlertTriangle,
  Boxes,
  CheckCircle2,
  FileText,
  Radio,
  ScanSearch,
  Table2,
} from "lucide-react";
import type { ReactNode } from "react";

import type { AnalyticsWidget, WidgetTone } from "../../api/types";

/**
 * Плитки состояния системы в духе виджетов iOS: крупное значение, короткая подпись,
 * кольцевая шкала заполнения.
 *
 * Тон (ok/warn/danger) приходит с сервера вместе со значением — пороги «сколько считать
 * нормой» живут в одном месте (`analytics_service`), иначе они бы разъехались между
 * бэкендом, который их знает, и фронтом, который их дублирует.
 */

const TONE_STYLES: Record<WidgetTone, { ring: string; text: string; glow: string }> = {
  ok: { ring: "#34d399", text: "text-emerald-400", glow: "bg-emerald-500/10" },
  warn: { ring: "#fbbf24", text: "text-amber-400", glow: "bg-amber-500/10" },
  danger: { ring: "#f87171", text: "text-red-400", glow: "bg-red-500/10" },
  neutral: { ring: "#a1a1aa", text: "text-zinc-300", glow: "bg-white/5" },
};

const ICONS: Record<string, ReactNode> = {
  sources: <Radio size={16} />,
  documents: <FileText size={16} />,
  catalog: <Boxes size={16} />,
  analysis: <ScanSearch size={16} />,
  compliance: <Table2 size={16} />,
  errors: <AlertTriangle size={16} />,
};

/** Кольцевая шкала: окружность с обрезанным штрихом. Радиус и длина окружности заданы
 * константами — единственный способ анимировать заполнение одним CSS-свойством. */
function ProgressRing({ progress, color }: { progress: number; color: string }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(1, progress));

  return (
    <svg viewBox="0 0 64 64" className="h-16 w-16 -rotate-90">
      <circle
        cx="32"
        cy="32"
        r={radius}
        fill="none"
        stroke="rgba(255,255,255,0.07)"
        strokeWidth="6"
      />
      <circle
        cx="32"
        cy="32"
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth="6"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - clamped)}
        style={{ transition: "stroke-dashoffset 600ms ease" }}
      />
    </svg>
  );
}

export function SystemWidgets({ widgets }: { widgets: AnalyticsWidget[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      {widgets.map((widget) => {
        const tone = TONE_STYLES[widget.tone] ?? TONE_STYLES.neutral;
        return (
          <div
            key={widget.key}
            className="group relative overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5 transition-colors hover:border-white/[0.14]"
          >
            {/* Мягкое цветное свечение в углу — визуальный сигнал состояния, читаемый
                боковым зрением при беглом взгляде на сетку плиток. */}
            <div
              className={`pointer-events-none absolute -right-8 -top-8 h-24 w-24 rounded-full blur-2xl ${tone.glow}`}
            />
            <div className="relative flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="mb-3 flex items-center gap-2 text-sm text-zinc-400">
                  <span
                    className={`flex h-7 w-7 items-center justify-center rounded-lg bg-white/[0.06] ${tone.text}`}
                  >
                    {ICONS[widget.key] ?? <CheckCircle2 size={16} />}
                  </span>
                  {widget.title}
                </div>
                <div className={`text-2xl font-semibold ${tone.text}`}>{widget.value}</div>
                <div className="mt-1.5 text-xs leading-snug text-zinc-500">
                  {widget.caption}
                </div>
              </div>
              {widget.progress !== null && (
                <div className="relative shrink-0">
                  <ProgressRing progress={widget.progress} color={tone.ring} />
                  <span className="absolute inset-0 flex items-center justify-center text-[11px] font-medium text-zinc-400">
                    {Math.round(widget.progress * 100)}%
                  </span>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
