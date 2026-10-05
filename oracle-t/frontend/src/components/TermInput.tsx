import { useState } from "react";
import type { ClipboardEvent, KeyboardEvent } from "react";
import { X } from "lucide-react";

/**
 * Ввод терминов «чипами» (05.10.2026): напечатал — Enter — термин добавлен отдельной плашкой.
 *
 * Раньше слова вводились в textarea по одному на строку, и было непонятно, где кончается один
 * термин и начинается другой, а пустая строка или пробел в конце давали «невидимый» ключ.
 * Вставка списка из буфера (по строкам или через запятую) раскладывается на отдельные термины.
 */
export function TermInput({
  value,
  onChange,
  placeholder,
  tone = "positive",
  disabled = false,
  splitOnComma = true,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  tone?: "positive" | "negative" | "muted";
  disabled?: boolean;
  /** Запятая разделяет термины. Для фраз сбора — нет: в них запятая может быть частью текста. */
  splitOnComma?: boolean;
}) {
  const [draft, setDraft] = useState("");

  const add = (raw: string[]) => {
    const fresh = raw.map((item) => item.trim()).filter(Boolean);
    if (fresh.length === 0) return;
    const next = [...value];
    for (const item of fresh) if (!next.includes(item)) next.push(item);
    onChange(next);
  };

  const commit = () => {
    add(splitOnComma ? draft.split(",") : [draft]);
    setDraft("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || (splitOnComma && event.key === ",")) {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  const onPaste = (event: ClipboardEvent<HTMLInputElement>) => {
    const text = event.clipboardData.getData("text");
    if (!/[\n,]/.test(text)) return;
    event.preventDefault();
    add(text.split(splitOnComma ? /[\n,]/ : /\n/));
  };

  const chip =
    tone === "positive"
      ? "border-indigo-500/30 bg-indigo-500/10 text-indigo-200"
      : tone === "negative"
        ? "border-red-500/30 bg-red-500/10 text-red-300"
        : "border-white/10 bg-white/[0.05] text-zinc-300";

  return (
    <div
      className={`flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 focus-within:border-indigo-500 ${
        disabled ? "opacity-60" : ""
      }`}
    >
      {value.map((item) => (
        <span
          key={item}
          className={`inline-flex items-center gap-1 rounded-md border py-0.5 pl-2 pr-1 font-mono text-[12px] ${chip}`}
        >
          {item}
          {!disabled && (
            <button
              type="button"
              onClick={() => onChange(value.filter((other) => other !== item))}
              aria-label={`Убрать «${item}»`}
              className="rounded p-0.5 opacity-70 hover:bg-white/10 hover:opacity-100"
            >
              <X size={11} />
            </button>
          )}
        </span>
      ))}
      {!disabled && (
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={commit}
          placeholder={value.length === 0 ? placeholder : "ещё… (Enter)"}
          className="min-w-[10rem] flex-1 bg-transparent px-1 py-1 text-sm text-white placeholder-zinc-600 outline-none"
        />
      )}
    </div>
  );
}
