import { Check, Plus } from "lucide-react";
import { useState } from "react";

import { ApiError, api } from "../../api/client";
import type { MarketModification } from "../../api/types";
import { fmt } from "./format";
import { BUTTON, DESIGNS, INPUT, ModificationForm, NO_RIGHT, PRIMARY } from "./ModificationForm";

/**
 * Привязка написания к модификации справочника (очередь «Разметить» и «Переразметить» в детализации).
 *
 * 22.11.2026, замечание аналитика: при выборе привязки не было видно всей модификации (только имя,
 * фаза и интерфейс) и нельзя было выбрать «соответствует / не соответствует ПП 890». Теперь под
 * списком — карточка выбранной модификации со всеми признаками и выбором ПП 890. Признак хранится на
 * модификации, поэтому смена затрагивает все её написания — карточка об этом предупреждает.
 */

type Mod = MarketModification & { type_notation?: string };

const DESIGN_LABEL = Object.fromEntries(DESIGNS.map((d) => [d.value, d.label]));

/** Название для «Новая»: не всё написание из ФГИС, а «МИРТЕК-12-РУ SP1 RF433» — как в справочнике. */
export function suggestName(raw: string): string {
  const text = raw.replace(/\s+/g, " ").replace(/^(исполнение|модификация|мод\.)\s*/i, "").trim();
  const tokens = text.split("-");
  const cut = tokens.findIndex((t, i) => i > 1 && (/^[AА][\dOО.,]+[RК]?\d*$/i.test(t) || /^(AIRI|А1R1)$/i.test(t) || /^\d{3}$/.test(t)));
  if (cut < 0) return text;
  const base = tokens.slice(0, cut).join("-").replace(/-(РУ)-/i, "-$1 ");
  const radio = Array.from(new Set(text.toUpperCase().match(/RF\d+/g) ?? []));
  return [base, ...radio].join(" ");
}

function smartText(m: Mod): string {
  if (m.smart_890 === null) return "не определено";
  const verdict = m.smart_890 ? "соответствует" : "не соответствует";
  return `${verdict} (${m.smart_890_source === "manual" ? "решение аналитика" : "вывод ИИ"})`;
}

function ModificationCard({
  mod,
  canEdit,
  onSmart,
}: {
  mod: Mod;
  canEdit: boolean;
  onSmart: (value: boolean | null) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const manual = mod.smart_890_source === "manual";
  const facts: [string, string][] = [
    ["Название", mod.name],
    ["Тип ПУ · исполнение", `${mod.phase ?? "—"} · ${DESIGN_LABEL[mod.design] ?? mod.design}`],
    ["Интерфейс", mod.interface || "н/д"],
    ["МПИ", mod.mpi_years ? `${mod.mpi_years} лет` : "—"],
    [
      "РПП",
      mod.in_rpp
        ? `${mod.score ?? "?"} баллов${mod.rpp_included_at ? ` · с ${mod.rpp_included_at.split("-").reverse().join(".")}` : ""}`
        : "не внесено",
    ],
    ["ПП 890", `${smartText(mod)}${mod.smart_890_note ? ` — ${mod.smart_890_note}` : ""}`],
    ["Уже привязано", `${fmt(mod.variants ?? 0)} написаний · ${fmt(mod.count ?? 0)} поверок`],
  ];
  if (mod.comment) facts.push(["Дополнительно", mod.comment]);
  return (
    <div className="mt-2 w-full rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 text-xs">
      <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-3 gap-y-1">
        {facts.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-zinc-500">{label}</dt>
            <dd className="text-zinc-300">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-2">
        <span className="text-zinc-500">Соответствие ПП 890:</span>
        <select
          className={INPUT}
          disabled={!canEdit || busy}
          title={canEdit ? undefined : NO_RIGHT}
          value={manual ? (mod.smart_890 ? "yes" : "no") : ""}
          onChange={async (e) => {
            setBusy(true);
            await onSmart(e.target.value === "" ? null : e.target.value === "yes");
            setBusy(false);
          }}
        >
          <option value="" className="bg-zinc-900">решает ИИ</option>
          <option value="yes" className="bg-zinc-900">да (интеллектуальный)</option>
          <option value="no" className="bg-zinc-900">нет (простейший)</option>
        </select>
        <span className="text-zinc-600">относится ко всем написаниям этой модификации</span>
      </div>
    </div>
  );
}

export function VariantMapper({
  typeId,
  variantId,
  raw,
  currentModificationId,
  canEdit,
  fail,
  onDone,
}: {
  typeId: number;
  variantId: number;
  raw: string;
  currentModificationId?: number | null;
  canEdit: boolean;
  fail: (err: unknown, fallback: string) => void;
  onDone: (message: string) => void;
}) {
  const [mods, setMods] = useState<Mod[] | null>(null);
  const [related, setRelated] = useState<Mod[]>([]);
  const [target, setTarget] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  const openMods = () => {
    if (mods) return;
    api.get<Mod[]>(`/meter-market/types/${typeId}/modifications`).then(setMods).catch((err) => fail(err, "Не удалось загрузить модификации"));
    // Поверитель мог указать номер СИ соседнего типа (в поверке МИРТЕК-12-РУ — номер МИРТЕК-232-РУ):
    // модификации других типов того же производителя — отдельной группой (20.11.2026).
    api.get<Mod[]>(`/meter-market/types/${typeId}/modifications?related=true`).then(setRelated).catch(() => setRelated([]));
  };
  const all = [...(mods ?? []), ...related];
  const selected = all.find((m) => String(m.id) === target) ?? null;
  const label = (m: Mod) => `${m.name} · ${[m.phase, m.interface].filter(Boolean).join(", ")}${m.id === currentModificationId ? " · сейчас" : ""}`;

  const map = async () => {
    setBusy(true);
    try {
      const result = await api.put<{ same_key_mapped: number }>(`/meter-market/variants/${variantId}`, { modification_id: Number(target) });
      onDone(`«${raw}» привязано к «${selected?.name ?? "модификации"}»${result.same_key_mapped ? `, ещё ${result.same_key_mapped} написаний сведено` : ""}`);
    } catch (err) {
      fail(err, "Не удалось привязать");
    } finally {
      setBusy(false);
    }
  };
  const setSmart = async (value: boolean | null) => {
    if (!selected) return;
    try {
      await api.patch(`/meter-market/modifications/${selected.id}`, { smart_890: value });
      const refresh = (m: Mod): Mod =>
        m.id !== selected.id
          ? m
          : { ...m, smart_890: value, smart_890_source: value === null ? null : "manual", smart_890_note: value === null ? null : m.smart_890_note };
      setMods((prev) => prev && prev.map(refresh));
      setRelated((prev) => prev.map(refresh));
    } catch (err) {
      fail(err, "Не удалось изменить соответствие ПП 890");
    }
  };

  return (
    <div className="flex w-full flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <select
          className={`${INPUT} max-w-[22rem]`}
          value={target}
          onFocus={openMods}
          onMouseDown={openMods}
          disabled={!canEdit}
          title={canEdit ? undefined : NO_RIGHT}
          onChange={(e) => setTarget(e.target.value)}
        >
          <option value="" className="bg-zinc-900">{mods ? "привязать к модификации…" : "выбрать модификацию…"}</option>
          {(mods ?? []).map((m) => (
            <option key={m.id} value={m.id} className="bg-zinc-900">{label(m)}</option>
          ))}
          {related.length > 0 && (
            <optgroup label="Другие типы производителя" className="bg-zinc-900">
              {related.map((m) => (
                <option key={m.id} value={m.id} className="bg-zinc-900">{label(m)}</option>
              ))}
            </optgroup>
          )}
        </select>
        <button type="button" className={PRIMARY} disabled={!canEdit || !target || busy || Number(target) === currentModificationId} onClick={() => void map()}>
          <Check size={13} />
          Привязать
        </button>
        <button type="button" className={BUTTON} disabled={!canEdit} title={canEdit ? undefined : NO_RIGHT} onClick={() => setCreating((v) => !v)}>
          <Plus size={13} />
          Новая
        </button>
      </div>
      {selected && <ModificationCard mod={selected} canEdit={canEdit} onSmart={setSmart} />}
      {creating && (
        <ModificationForm
          initial={{ name: suggestName(raw) }}
          submitLabel="Создать и привязать"
          onCancel={() => setCreating(false)}
          onSubmit={async (values) => {
            try {
              await api.post(`/meter-market/types/${typeId}/modifications`, { ...values, variant_id: variantId });
              onDone(`Модификация «${values.name}» создана, «${raw}» привязано к ней`);
            } catch (err) {
              fail(err, err instanceof ApiError ? err.message : "Не удалось создать модификацию");
            }
          }}
        />
      )}
    </div>
  );
}
