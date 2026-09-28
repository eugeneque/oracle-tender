/**
 * Одиннадцать типов приборов учёта из файла тендерного отдела «Параметры для ПУ»
 * (25.09.2026). Коды совпадают с `METER_KINDS` в `backend/app/services/meter_kind.py` —
 * менять только вместе.
 */
export const METER_KINDS: { value: string; label: string; short: string }[] = [
  { value: "1ph_direct_din", label: "Однофазный прямого включения на DIN-рейку", short: "1ф прям. DIN" },
  { value: "1ph_direct_split", label: "Однофазный прямого включения сплит-исполнения", short: "1ф прям. сплит" },
  { value: "1ph_direct_panel", label: "Однофазный прямого включения шкафной", short: "1ф прям. шкаф" },
  { value: "3ph_direct_din", label: "Трёхфазный прямого включения на DIN-рейку", short: "3ф прям. DIN" },
  { value: "3ph_direct_split", label: "Трёхфазный прямого включения сплит-исполнения", short: "3ф прям. сплит" },
  { value: "3ph_direct_panel", label: "Трёхфазный прямого включения шкафной", short: "3ф прям. шкаф" },
  { value: "3ph_semi_din", label: "Трёхфазный полукосвенного включения на DIN-рейку", short: "3ф полукосв. DIN" },
  { value: "3ph_indirect_din", label: "Трёхфазный косвенного включения на DIN-рейку", short: "3ф косв. DIN" },
  { value: "3ph_semi_panel", label: "Трёхфазный полукосвенного включения шкафной", short: "3ф полукосв. шкаф" },
  { value: "3ph_indirect_panel", label: "Трёхфазный косвенного включения шкафной", short: "3ф косв. шкаф" },
  { value: "hv", label: "Высоковольтный прибор учёта", short: "ВПУ" },
];

const LABELS = new Map(METER_KINDS.map((kind) => [kind.value, kind.label]));

export function meterKindLabel(code: string): string {
  return LABELS.get(code) ?? code;
}

/** Подпись для карточки: все типы, если их немного; иначе — сжатая сводка. Раскрытый
 * список «все трёхфазные» (7 типов) читается хуже, чем «Трёхфазный (любой)». */
export function meterKindsSummary(codes: string[] | null | undefined): string | null {
  if (!codes || codes.length === 0) return null;
  const threePhase = METER_KINDS.filter((kind) => kind.value.startsWith("3ph")).map((k) => k.value);
  const onePhase = METER_KINDS.filter((kind) => kind.value.startsWith("1ph")).map((k) => k.value);
  const set = new Set(codes);
  const parts: string[] = [];
  const rest = new Set(codes);
  if (threePhase.every((code) => set.has(code))) {
    parts.push("Трёхфазный (любое включение и крепление)");
    threePhase.forEach((code) => rest.delete(code));
  }
  if (onePhase.every((code) => set.has(code))) {
    parts.push("Однофазный прямого включения (любое крепление)");
    onePhase.forEach((code) => rest.delete(code));
  }
  for (const code of rest) parts.push(meterKindLabel(code));
  return parts.join("; ");
}

export const VERDICT_LABELS: Record<string, string> = {
  passes: "Проходит",
  caveats: "С оговорками",
  fails: "Не проходит",
  unknown: "Не хватает данных",
};

export const VERDICT_CLASSES: Record<string, string> = {
  passes: "bg-emerald-500/15 text-emerald-300",
  caveats: "bg-amber-500/15 text-amber-300",
  fails: "bg-red-500/15 text-red-300",
  unknown: "bg-white/5 text-zinc-400",
};
