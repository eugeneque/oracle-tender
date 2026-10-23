import defaultColors from "tailwindcss/colors";
import plugin from "tailwindcss/plugin";

/*
 * Тёмная и светлая темы (28.09.2026).
 *
 * Интерфейс писался под тёмный фон: «белый» в классах означает «передний план» (текст,
 * тонкие рамки, подложки `bg-white/5`), а светлые оттенки палитр (`text-indigo-300`) —
 * акцентный текст. Поэтому светлая тема не переписывает компоненты, а зеркалит шкалы:
 * каждый цвет берётся из CSS-переменной, и в `[data-theme="light"]` оттенок 300 получает
 * значение 700, 100 — 900 и т. д., а `white` и `black` меняются местами. Всё, что должно
 * остаться белым в обеих темах (текст на цветной кнопке), пишется через `snow`, а
 * затемнение под модальным окном — через `scrim`.
 */

const SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
const HUES = [
  "slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber", "yellow", "lime",
  "green", "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia",
  "pink", "rose",
];

// Серая шкала в светлой теме подобрана вручную, а не простым зеркалом: панели (`zinc-900`)
// должны стать белыми, а фон страницы (`zinc-950`) — светло-серым, как в macOS.
const LIGHT_ZINC = {
  50: "#09090b",
  100: "#18181b",
  200: "#27272a",
  300: "#3f3f46",
  400: "#52525b",
  500: "#71717a",
  600: "#a1a1aa",
  700: "#d4d4d8",
  800: "#e4e4e7",
  900: "#ffffff",
  950: "#f3f7fe",
};

// Палитра SalesPort (24.10.2026): акцент `indigo` заменён фирменным синим #185DF1, тёмная
// тема посветлела до графита в духе Perplexity, фон светлой — Alice Blue #F3F7FE.
// Шкала `indigo` в тёмной теме: кнопки `bg-indigo-500` — ровно фирменный синий, текст
// `indigo-300` — светлый оттенок, читаемый на графите; 950 — Prussian Blue #031130.
const DARK_INDIGO = {
  50: "#f3f7fe",
  100: "#e3ecfd",
  200: "#c6d8fc",
  300: "#9dbcfa",
  400: "#4a82f5",
  500: "#185df1",
  600: "#1550d0",
  700: "#1149c4",
  800: "#0f3b9b",
  900: "#0c2c70",
  950: "#031130",
};
// В светлой теме простое зеркало сделало бы кнопки (`indigo-500` → 500) бледными, поэтому
// 500 остаётся фирменным синим, а 300/400 (акцентный текст) — тёмными оттенками.
const LIGHT_INDIGO = {
  50: "#031130",
  100: "#0c2c70",
  200: "#0f3b9b",
  300: "#1149c4",
  400: "#1452d8",
  500: "#185df1",
  600: "#4a82f5",
  700: "#9dbcfa",
  800: "#c6d8fc",
  900: "#e3ecfd",
  950: "#f3f7fe",
};
// Тёмная серая шкала: фон страницы и панели — графит с холодным оттенком вместо почти
// чёрного #09090b; светлые оттенки (текст) остаются от zinc.
const DARK_ZINC = {
  50: "#fafafa",
  100: "#f4f4f5",
  200: "#e4e4e7",
  300: "#d4d4d8",
  400: "#a1a1aa",
  500: "#787c84",
  600: "#55595f",
  700: "#3b3e44",
  800: "#2c2f34",
  900: "#202226",
  950: "#18191c",
};

function rgbChannels(hex) {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const n = parseInt(full, 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
}

const varColor = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

const colors = {
  transparent: "transparent",
  current: "currentColor",
  inherit: "inherit",
  white: varColor("white"),
  black: varColor("black"),
  snow: "#ffffff",
  scrim: "#000000",
};
for (const hue of HUES) {
  colors[hue] = Object.fromEntries(SHADES.map((s) => [s, varColor(`${hue}-${s}`)]));
}

const darkVars = { "--c-white": "255 255 255", "--c-black": "0 0 0" };
const lightVars = { "--c-white": "9 9 11", "--c-black": "255 255 255" };
for (const hue of HUES) {
  SHADES.forEach((shade, i) => {
    const dark = { zinc: DARK_ZINC, indigo: DARK_INDIGO }[hue];
    darkVars[`--c-${hue}-${shade}`] = rgbChannels(dark ? dark[shade] : defaultColors[hue][shade]);
    const mirrored = SHADES[SHADES.length - 1 - i];
    const light = { zinc: LIGHT_ZINC, indigo: LIGHT_INDIGO }[hue];
    lightVars[`--c-${hue}-${shade}`] = rgbChannels(
      light ? light[shade] : defaultColors[hue][mirrored],
    );
  });
}

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    colors,
    extend: {},
  },
  plugins: [
    plugin(({ addBase }) => {
      addBase({
        ":root": { ...darkVars, colorScheme: "dark" },
        ':root[data-theme="light"]': { ...lightVars, colorScheme: "light" },
      });
    }),
  ],
};
