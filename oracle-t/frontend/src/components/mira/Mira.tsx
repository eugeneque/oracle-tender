import { motion, useMotionValue, useSpring } from "motion/react";
import { useEffect, useId, useRef, useState } from "react";
import type { ReactNode } from "react";

/**
 * Мира — маскот ИИ (редизайн 23.10.2026, вторая версия по референсам заказчика).
 *
 * Тело — светящаяся капля без ушей: форма плавно перетекает (круг, «желе», звезда,
 * скруглённый квадрат), а внутри по кругу плывут размытые цветные пятна — получается
 * текучий градиент, как у присланных анимаций. Палитра и форма зависят от стадии:
 * - ждёт — фиолетово-голубая, мягко колышется, глаза следят за курсором, моргает и иногда
 *   довольно щурится «^^»; по клику подпрыгивает;
 * - работает — переливается синим, красным, оранжевым и белым; читает документы
 *   (скруглённый квадрат, глаза бегут по строкам), сверяет с каталогом (круг ↔ ромб, взгляд
 *   влево-вправо), думает (пульсирующая звезда, взгляд вверх), слушает замечание (кивает);
 * - итог — зелёно-бирюзовая и радостная, янтарная и с прищуром, розово-красная и грустная,
 *   с ошибкой — трясётся с крестиками вместо глаз.
 *
 * Формы — замкнутые кривые с одинаковым числом точек (`blob`), поэтому `motion`
 * интерполирует между ними строку `d`. Глаза — пружины `motion`, пятна — CSS
 * (`.mira-flow*` в index.css). `prefers-reduced-motion` гасит перетекание и слежку.
 */
export type MiraMood =
  | "idle"
  | "reading"
  | "comparing"
  | "thinking"
  | "listening"
  | "happy"
  | "doubtful"
  | "sad"
  | "error";

const POINTS = 16;

/** Замкнутая гладкая кривая по радиусу от угла (Catmull-Rom → кубические Безье). */
function blob(radius: (angle: number) => number, rotate = 0): string {
  const pts = Array.from({ length: POINTS }, (_, i) => {
    const a = (i / POINTS) * Math.PI * 2;
    const r = radius(a);
    return [50 + r * Math.cos(a + rotate), 50 + r * Math.sin(a + rotate)];
  });
  const f = (n: number) => n.toFixed(2);
  let d = `M${f(pts[0][0])} ${f(pts[0][1])}`;
  for (let i = 0; i < POINTS; i++) {
    const p0 = pts[(i - 1 + POINTS) % POINTS];
    const p1 = pts[i];
    const p2 = pts[(i + 1) % POINTS];
    const p3 = pts[(i + 2) % POINTS];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${f(c1[0])} ${f(c1[1])} ${f(c2[0])} ${f(c2[1])} ${f(p2[0])} ${f(p2[1])}`;
  }
  return `${d} Z`;
}

const superellipse = (size: number, power: number) => (a: number) =>
  size / Math.pow(Math.pow(Math.abs(Math.cos(a)), power) + Math.pow(Math.abs(Math.sin(a)), power), 1 / power);

const SHAPES = {
  circle: blob(() => 33),
  wobbleA: blob((a) => 33 + 2.2 * Math.sin(3 * a)),
  wobbleB: blob((a) => 33 + 2.2 * Math.sin(3 * a + 2)),
  wobbleC: blob((a) => 33 + 2.2 * Math.cos(2 * a + 1)),
  squircle: blob(superellipse(30, 4)),
  squircleTall: blob((a) => superellipse(30, 4)(a) * (1 + 0.07 * Math.sin(a) ** 2)),
  diamond: blob(superellipse(31, 4), Math.PI / 4),
  star: blob((a) => 19 + 19 * Math.pow(Math.abs(Math.cos(2 * a)), 3)),
  starSoft: blob((a) => 25 + 11 * Math.pow(Math.abs(Math.cos(2 * a)), 2)),
  flower: blob((a) => 31 + 4 * Math.cos(4 * a)),
  flowerB: blob((a) => 31 + 4 * Math.cos(4 * a + Math.PI / 4)),
  droop: blob((a) => 32 * (1 + 0.08 * Math.sin(a))),
};

type Palette = [string, string, string, string];

/** Цвета: фон капли, два плывущих пятна и блик (он же кромка). */
const PALETTES: Record<string, Palette> = {
  idle: ["#6d5cff", "#c084fc", "#38bdf8", "#f5d0fe"],
  working: ["#3b4bff", "#ff3b30", "#ff9f0a", "#fff4d6"],
  happy: ["#14b8a6", "#5ef6ff", "#8cf27a", "#e0fff4"],
  doubtful: ["#f59e0b", "#fde047", "#fb923c", "#fff7d6"],
  sad: ["#be185d", "#fb7185", "#a855f7", "#ffe4ec"],
  error: ["#b91c1c", "#f87171", "#fb923c", "#ffe4e4"],
};

/** Форма и её «дыхание» по настроению — кадры для анимации `d`. */
function shapeFrames(mood: MiraMood, busy: boolean): { frames: string[]; duration: number } {
  if (busy) {
    if (mood === "reading") return { frames: [SHAPES.squircle, SHAPES.squircleTall, SHAPES.squircle], duration: 1.6 };
    if (mood === "comparing") return { frames: [SHAPES.circle, SHAPES.diamond, SHAPES.circle], duration: 2 };
    if (mood === "listening") return { frames: [SHAPES.wobbleA, SHAPES.circle, SHAPES.wobbleB], duration: 1.2 };
    return { frames: [SHAPES.star, SHAPES.starSoft, SHAPES.flower, SHAPES.starSoft, SHAPES.star], duration: 3.2 };
  }
  if (mood === "sad") return { frames: [SHAPES.droop, SHAPES.circle, SHAPES.droop], duration: 4 };
  if (mood === "doubtful") return { frames: [SHAPES.wobbleA, SHAPES.wobbleC, SHAPES.wobbleA], duration: 3 };
  if (mood === "error") return { frames: [SHAPES.flower, SHAPES.flowerB, SHAPES.flower], duration: 0.8 };
  return { frames: [SHAPES.wobbleA, SHAPES.wobbleB, SHAPES.wobbleC, SHAPES.wobbleA], duration: 6 };
}

// Слежка за курсором: один обработчик на страницу, Миры подписываются на него. Без
// requestAnimationFrame: в фоновой вкладке кадры не идут, и взгляд отставал на шаг, а
// пружина `motion` сама сглаживает частые события.
type Listener = (x: number, y: number) => void;
const listeners = new Set<Listener>();
function onPointer(event: PointerEvent) {
  listeners.forEach((listener) => listener(event.clientX, event.clientY));
}
function subscribePointer(listener: Listener): () => void {
  if (listeners.size === 0) window.addEventListener("pointermove", onPointer);
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) window.removeEventListener("pointermove", onPointer);
  };
}

const REDUCED =
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

type Expression = "open" | "blink" | "joy" | "squint" | "sad" | "x";

const INK = "#0b1033";

function Eyes({ expression }: { expression: Expression }): ReactNode {
  const stroke = { stroke: INK, strokeWidth: 3.6, strokeLinecap: "round" as const, fill: "none" };
  if (expression === "joy") {
    return (
      <g>
        <path d="M36 51 Q40.5 46 45 51" {...stroke} />
        <path d="M55 51 Q59.5 46 64 51" {...stroke} />
      </g>
    );
  }
  if (expression === "squint") {
    return (
      <g>
        <ellipse cx="40.5" cy="49" rx="3.6" ry="5" fill={INK} />
        <path d="M55 50 L64 48.5" {...stroke} />
      </g>
    );
  }
  if (expression === "sad") {
    return (
      <g fill={INK}>
        <ellipse cx="40.5" cy="51" rx="3.4" ry="4.6" />
        <ellipse cx="59.5" cy="51" rx="3.4" ry="4.6" />
        <path d="M35 44 L45 46" {...stroke} strokeWidth={2.6} />
        <path d="M65 44 L55 46" {...stroke} strokeWidth={2.6} />
      </g>
    );
  }
  if (expression === "x") {
    return (
      <g>
        <path d="M37 45 L44 52 M44 45 L37 52" {...stroke} strokeWidth={3} />
        <path d="M56 45 L63 52 M63 45 L56 52" {...stroke} strokeWidth={3} />
      </g>
    );
  }
  return (
    <motion.g
      animate={{ scaleY: expression === "blink" ? 0.1 : 1 }}
      transition={{ duration: 0.08 }}
      style={{ originY: 0.5 }}
    >
      <ellipse cx="40.5" cy="49" rx="3.6" ry="5.2" fill={INK} />
      <ellipse cx="59.5" cy="49" rx="3.6" ry="5.2" fill={INK} />
    </motion.g>
  );
}

function bodyMotion(mood: MiraMood, working: boolean) {
  if (!working && mood === "error") {
    return { animate: { x: [0, -3, 3, -2, 2, 0] }, transition: { duration: 0.5, repeat: Infinity, repeatDelay: 1.6 } };
  }
  if (!working && mood === "doubtful") {
    return { animate: { rotate: [0, -7, 0, 7, 0] }, transition: { duration: 3.6, repeat: Infinity, ease: "easeInOut" as const } };
  }
  if (!working && mood === "happy") {
    return {
      animate: { y: [0, -6, 0, 0], scaleX: [1, 0.95, 1.06, 1], scaleY: [1, 1.06, 0.94, 1] },
      transition: { duration: 1.2, repeat: Infinity, repeatDelay: 1.4, ease: "easeInOut" as const },
    };
  }
  if (!working && mood === "sad") {
    return { animate: { y: [0, 2, 0], scaleY: [1, 0.96, 1] }, transition: { duration: 4, repeat: Infinity, ease: "easeInOut" as const } };
  }
  if (working) {
    return { animate: { scale: [1, 1.05, 1] }, transition: { duration: 1.4, repeat: Infinity, ease: "easeInOut" as const } };
  }
  return { animate: { y: [0, -1.5, 0] }, transition: { duration: 3.2, repeat: Infinity, ease: "easeInOut" as const } };
}

export function Mira({
  mood = "idle",
  size = 44,
  busy = false,
  className = "",
}: {
  mood?: MiraMood;
  size?: number;
  /** Идёт работа — форма и узор взгляда задаются стадией `mood`. */
  busy?: boolean;
  className?: string;
}) {
  const id = useId().replace(/:/g, "");
  const rootRef = useRef<HTMLSpanElement>(null);
  const result = mood === "happy" || mood === "doubtful" || mood === "sad" || mood === "error";
  const working = busy;
  const palette = PALETTES[working ? "working" : result ? mood : "idle"];
  const { frames, duration } = shapeFrames(mood, working);
  const follows = !working && mood !== "error";

  // Взгляд за курсором — пружиной, чтобы глаза «догоняли», а не прыгали.
  const lookX = useMotionValue(0);
  const lookY = useMotionValue(0);
  const eyeX = useSpring(lookX, { stiffness: 220, damping: 18 });
  const eyeY = useSpring(lookY, { stiffness: 220, damping: 18 });
  useEffect(() => {
    lookX.set(0);
    lookY.set(0);
    if (!follows || REDUCED) return;
    return subscribePointer((x, y) => {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      const dx = x - (rect.left + rect.width / 2);
      const dy = y - (rect.top + rect.height / 2);
      const dist = Math.hypot(dx, dy) || 1;
      const reach = Math.min(1, dist / 240) * 7;
      lookX.set((dx / dist) * reach);
      lookY.set((dy / dist) * reach * 0.8);
    });
  }, [follows, lookX, lookY]);

  // Моргание и довольный прищур — в случайные моменты, как у живого.
  const [blink, setBlink] = useState<"none" | "blink" | "joy">("none");
  useEffect(() => {
    if (REDUCED || working) return;
    let timer: ReturnType<typeof setTimeout>;
    const next = () => {
      timer = setTimeout(
        () => {
          const joy = (mood === "idle" || mood === "happy") && Math.random() < 0.3;
          setBlink(joy ? "joy" : "blink");
          timer = setTimeout(
            () => {
              setBlink("none");
              next();
            },
            joy ? 1100 : 140,
          );
        },
        2400 + Math.random() * 3200,
      );
    };
    next();
    return () => clearTimeout(timer);
  }, [mood, working]);

  // Клик — подпрыгивает: ключ перезапускает анимацию прыжка.
  const [hop, setHop] = useState(0);

  let expression: Expression = "open";
  if (mood === "error" && !working) expression = "x";
  else if (mood === "sad" && !working) expression = "sad";
  else if (mood === "doubtful" && !working) expression = blink === "blink" ? "blink" : "squint";
  else if (blink === "joy") expression = "joy";
  else if (blink === "blink") expression = "blink";

  // Узор взгляда во время работы — вместо слежки.
  const busyLook =
    mood === "reading"
      ? { x: [-5, 5, 5, -5, -5], y: [-2, -2, 2, 2, -2] }
      : mood === "comparing"
        ? { x: [-6, -6, 6, 6, -6], y: [0, 0, 0, 0, 0] }
        : mood === "listening"
          ? { x: [0, 0, 0], y: [1, 4, 1] }
          : { x: [0, -5, -5, 4, 0], y: [-5, -6, -4, -6, -5] };
  const body = bodyMotion(mood, working);

  return (
    <span
      ref={rootRef}
      className={`mira relative inline-flex shrink-0 ${className}`}
      data-mood={mood}
      data-busy={working ? "true" : undefined}
      style={{ width: size, height: size, ["--mira-glow" as string]: palette[1] }}
      onClick={() => setHop((n) => n + 1)}
      aria-hidden="true"
    >
      <motion.span
        key={hop}
        className="block h-full w-full"
        initial={false}
        animate={hop ? { y: [0, 3, -14, 0], scaleX: [1, 1.12, 0.92, 1], scaleY: [1, 0.86, 1.1, 1] } : undefined}
        transition={{ duration: 0.6, ease: "easeOut" }}
        style={{ originY: 1 }}
      >
        <motion.svg
          viewBox="0 0 100 100"
          className="mira__body h-full w-full overflow-visible"
          animate={REDUCED ? undefined : body.animate}
          transition={body.transition}
          style={{ originY: 0.85 }}
        >
          <defs>
            <motion.path
              id={`mira-shape-${id}`}
              initial={false}
              animate={REDUCED ? { d: frames[0] } : { d: frames }}
              transition={REDUCED ? { duration: 0.3 } : { duration, repeat: Infinity, ease: "easeInOut" }}
            />
            <clipPath id={`mira-clip-${id}`}>
              <use href={`#mira-shape-${id}`} />
            </clipPath>
            <filter id={`mira-soft-${id}`} x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="7" />
            </filter>
          </defs>

          {/* Текучий градиент: фон капли и плывущие по кругу размытые пятна. */}
          <g clipPath={`url(#mira-clip-${id})`}>
            <rect x="0" y="0" width="100" height="100" fill={palette[0]} className="mira-tint" />
            <g filter={`url(#mira-soft-${id})`}>
              <circle cx="34" cy="36" r="22" fill={palette[1]} className="mira-flow mira-flow--a" />
              <circle cx="66" cy="64" r="24" fill={palette[2]} className="mira-flow mira-flow--b" />
              <circle cx="38" cy="28" r="12" fill={palette[3]} className="mira-flow mira-flow--c" />
            </g>
          </g>
          {/* Светлая кромка поверх заливки — край капли «светится», как у референса. */}
          <use href={`#mira-shape-${id}`} fill="none" stroke={palette[3]} strokeOpacity="0.6" strokeWidth="1.6" />

          <motion.g style={follows ? { x: eyeX, y: eyeY } : undefined}>
            <motion.g
              animate={working && !REDUCED ? busyLook : { x: 0, y: 0 }}
              transition={
                working
                  ? { duration: mood === "listening" ? 1.2 : 2.4, repeat: Infinity, ease: "easeInOut" }
                  : { duration: 0.3 }
              }
            >
              <Eyes expression={expression} />
            </motion.g>
          </motion.g>
        </motion.svg>
      </motion.span>
    </span>
  );
}

/** Стадия по тексту хода задачи с сервера («Шаг 1 из 3: анализ документов…»,
 *  «Матрица соответствия: 6 из 17 производителей»). */
export function moodFromProgress(progress: string | null | undefined): MiraMood {
  const text = (progress ?? "").toLowerCase();
  if (text.includes("шаг 1") || text.includes("документ")) return "reading";
  if (text.includes("шаг 2") || text.includes("матриц") || text.includes("соответств")) return "comparing";
  return "thinking";
}
