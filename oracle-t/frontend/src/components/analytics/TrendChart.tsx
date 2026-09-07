import { useMemo, useRef, useState } from "react";

import type { MonthPoint } from "../../api/types";

/**
 * График динамики по месяцам (раздел 5.6 ТЗ, «динамика по месяцам»).
 *
 * Своя отрисовка на SVG, а не библиотека: в проекте нет ни одной charting-зависимости, а
 * нужен ровно один график с двумя рядами. Тянуть ради него recharts/chart.js — это +150 КБ
 * в бандл и чужая система тем поверх нашей.
 *
 * Две метрики на одной картинке (количество и сумма) намеренно нормируются каждая к своему
 * максимуму: у них разные единицы и разные порядки величин, общая ось сплющила бы линию
 * количества в ноль. Поэтому подписи значений даются в тултипе, а не на оси Y — по оси
 * читается только форма, а точные числа пользователь берёт из панели справа.
 */

type MetricKey = "count" | "amount" | "ai" | "win";

const METRICS: { key: MetricKey; label: string; color: string; unit: string }[] = [
  { key: "count", label: "Количество тендеров", color: "#22c55e", unit: "шт." },
  { key: "amount", label: "Сумма НМЦК", color: "#3b82f6", unit: "₽" },
  { key: "ai", label: "AI-оценка по профилю", color: "#818cf8", unit: "%" },
  { key: "win", label: "% соответствия МИРТЕК", color: "#a78bfa", unit: "%" },
];

const MONTH_LABELS = [
  "янв", "фев", "мар", "апр", "май", "июн",
  "июл", "авг", "сен", "окт", "ноя", "дек",
];

// Геометрия в единицах viewBox: SVG масштабируется по ширине контейнера, поэтому числа
// здесь — это пропорции, а не пиксели.
const VIEW_WIDTH = 900;
const VIEW_HEIGHT = 320;
const PADDING = { top: 24, right: 24, bottom: 36, left: 24 };

function monthLabel(iso: string): string {
  const [, month] = iso.split("-");
  return MONTH_LABELS[Number(month) - 1] ?? iso;
}

function metricValue(point: MonthPoint, metric: MetricKey): number {
  if (metric === "count") return point.count;
  if (metric === "amount") return Number(point.amount) || 0;
  if (metric === "ai") {
    return point.average_ai_score === null ? 0 : Number(point.average_ai_score);
  }
  return point.average_win_percentage === null ? 0 : Number(point.average_win_percentage);
}

function formatValue(value: number, metric: MetricKey): string {
  if (metric === "amount") {
    if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)} млрд ₽`;
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} млн ₽`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(0)} тыс ₽`;
    return `${new Intl.NumberFormat("ru-RU").format(Math.round(value))} ₽`;
  }
  if (metric === "win" || metric === "ai") return `${value.toFixed(1)} %`;
  return new Intl.NumberFormat("ru-RU").format(value);
}

/**
 * Сглаженная ломаная через все точки (кубические Безье с горизонтальными касательными).
 *
 * Касательные именно горизонтальные, а не по соседям: на рядах с резкими скачками (месяц
 * с одним крупным тендером) наклонные касательные дают выброс за пределы данных — линия
 * уходит ниже нуля между двумя положительными точками, и график начинает врать.
 */
function smoothPath(points: { x: number; y: number }[]): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;

  let path = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i += 1) {
    const previous = points[i - 1];
    const current = points[i];
    const midX = (previous.x + current.x) / 2;
    path += ` C ${midX} ${previous.y}, ${midX} ${current.y}, ${current.x} ${current.y}`;
  }
  return path;
}

export function TrendChart({ points }: { points: MonthPoint[] }) {
  const [metric, setMetric] = useState<MetricKey>("count");
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const active = METRICS.find((m) => m.key === metric) ?? METRICS[0];

  const geometry = useMemo(() => {
    const width = VIEW_WIDTH - PADDING.left - PADDING.right;
    const height = VIEW_HEIGHT - PADDING.top - PADDING.bottom;

    const values = points.map((point) => metricValue(point, metric));
    // Верхняя граница берётся с запасом 15%, чтобы пик не упирался в край области, и
    // никогда не равна нулю — иначе на пустом ряде делили бы на ноль.
    const max = Math.max(...values, 0) * 1.15 || 1;

    const step = points.length > 1 ? width / (points.length - 1) : 0;
    const coordinates = points.map((point, index) => ({
      x: PADDING.left + step * index,
      y: PADDING.top + height - (metricValue(point, metric) / max) * height,
    }));

    return { coordinates, height, width, max };
  }, [points, metric]);

  const linePath = smoothPath(geometry.coordinates);
  const areaPath = geometry.coordinates.length
    ? `${linePath} L ${geometry.coordinates[geometry.coordinates.length - 1].x} ${
        PADDING.top + geometry.height
      } L ${geometry.coordinates[0].x} ${PADDING.top + geometry.height} Z`
    : "";

  /** Ближайшая к курсору точка: попадать мышью в саму линию неудобно, поэтому активной
   * становится ближайшая по горизонтали — так же, как ведут себя биржевые графики. */
  function handleMove(event: React.MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg || geometry.coordinates.length === 0) return;
    const rect = svg.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    const x = ratio * VIEW_WIDTH;
    let nearest = 0;
    let bestDistance = Infinity;
    geometry.coordinates.forEach((point, index) => {
      const distance = Math.abs(point.x - x);
      if (distance < bestDistance) {
        bestDistance = distance;
        nearest = index;
      }
    });
    setHoverIndex(nearest);
  }

  const shownIndex = hoverIndex ?? points.length - 1;
  const shownPoint = points[shownIndex];
  const shownCoordinate = geometry.coordinates[shownIndex];
  const gradientId = `trend-gradient-${metric}`;

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="inline-flex items-center gap-1 rounded-xl border border-white/[0.08] bg-white/[0.03] p-1">
          {METRICS.map((option) => (
            <button
              key={option.key}
              onClick={() => setMetric(option.key)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                option.key === metric
                  ? "bg-white/10 text-white"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              <span
                className="mr-2 inline-block h-1.5 w-1.5 rounded-full align-middle"
                style={{ backgroundColor: option.color }}
              />
              {option.label}
            </button>
          ))}
        </div>
        <div className="text-xs text-zinc-600">
          Наведите курсор на график — значения показываются справа
        </div>
      </div>

      <div className="flex flex-col gap-4 lg:flex-row">
        <div className="min-w-0 flex-1">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
            className="h-[280px] w-full cursor-crosshair"
            onMouseMove={handleMove}
            onMouseLeave={() => setHoverIndex(null)}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={active.color} stopOpacity="0.28" />
                <stop offset="100%" stopColor={active.color} stopOpacity="0" />
              </linearGradient>
            </defs>

            {/* Горизонтальная сетка: четыре линии — достаточно, чтобы глаз считывал
                уровни, и не настолько плотно, чтобы спорить с самой кривой. */}
            {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
              const y = PADDING.top + geometry.height * fraction;
              return (
                <line
                  key={fraction}
                  x1={PADDING.left}
                  y1={y}
                  x2={VIEW_WIDTH - PADDING.right}
                  y2={y}
                  stroke="rgba(255,255,255,0.05)"
                  strokeWidth="1"
                />
              );
            })}

            {areaPath && <path d={areaPath} fill={`url(#${gradientId})`} />}
            {linePath && (
              <path
                d={linePath}
                fill="none"
                stroke={active.color}
                strokeWidth="2.5"
                strokeLinecap="round"
              />
            )}

            {shownCoordinate && (
              <>
                <line
                  x1={shownCoordinate.x}
                  y1={PADDING.top}
                  x2={shownCoordinate.x}
                  y2={PADDING.top + geometry.height}
                  stroke="rgba(255,255,255,0.25)"
                  strokeWidth="1"
                />
                <circle
                  cx={shownCoordinate.x}
                  cy={shownCoordinate.y}
                  r="6"
                  fill={active.color}
                  stroke="#09090b"
                  strokeWidth="3"
                />
              </>
            )}

            {points.map((point, index) => {
              const coordinate = geometry.coordinates[index];
              if (!coordinate) return null;
              // Подписи месяцев прореживаются на длинных рядах: 24 подписи в ряд
              // сливаются в полосу и перестают читаться.
              const stride = points.length > 14 ? 2 : 1;
              if (index % stride !== 0 && index !== points.length - 1) return null;
              return (
                <text
                  key={point.month}
                  x={coordinate.x}
                  y={VIEW_HEIGHT - 12}
                  textAnchor="middle"
                  className="fill-zinc-600"
                  style={{ fontSize: 12 }}
                >
                  {monthLabel(point.month)}
                </text>
              );
            })}
          </svg>
        </div>

        {/* Панель значений справа — как на референсе: активная метрика крупно, остальные
            за тот же месяц мелко, чтобы месяц можно было сравнить по всем трём разрезам
            не переключая вкладку. */}
        <div className="w-full shrink-0 lg:w-64">
          {shownPoint ? (
            <div className="space-y-3">
              <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-4">
                <div className="mb-1 text-xs text-zinc-500">
                  {active.label} · {monthLabel(shownPoint.month)}{" "}
                  {shownPoint.month.slice(0, 4)}
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{ backgroundColor: active.color }}
                  />
                  <span className="text-xl font-semibold text-white">
                    {formatValue(metricValue(shownPoint, metric), metric)}
                  </span>
                </div>
              </div>

              {METRICS.filter((option) => option.key !== metric).map((option) => (
                <div
                  key={option.key}
                  className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3"
                >
                  <div className="mb-0.5 text-[11px] text-zinc-600">{option.label}</div>
                  <div className="flex items-center gap-2">
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full"
                      style={{ backgroundColor: option.color }}
                    />
                    <span className="text-sm text-zinc-300">
                      {(option.key === "win" && shownPoint.average_win_percentage === null) ||
                      (option.key === "ai" && shownPoint.average_ai_score === null)
                        ? "не рассчитан"
                        : formatValue(metricValue(shownPoint, option.key), option.key)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 text-sm text-zinc-500">
              Нет данных за выбранный период
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
