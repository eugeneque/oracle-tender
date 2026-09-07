import { useMemo, useRef, useState } from "react";
import { X } from "lucide-react";

import type { KnownRecipient } from "../../api/types";

function formatLastSent(value: string | null): string {
  if (!value) return "ещё не писали";
  return `последнее письмо ${new Date(value).toLocaleDateString("ru-RU")}`;
}

/** Как адрес выглядит в поле и уезжает на сервер: «Иванов Иван <ivanov@example.ru>». */
function toEntry(item: KnownRecipient): string {
  return item.name ? `${item.name} <${item.address}>` : item.address;
}

function addressOf(entry: string): string {
  const match = entry.match(/<([^>]+)>/);
  return (match ? match[1] : entry).trim().toLowerCase();
}

/**
 * Поле получателей: выбранные адреса — чипами, ввод — с подсказками.
 *
 * Подсказки приходят из журнала уведомлений (`GET /notifications/recipients`): адрес, на
 * который система однажды отправила письмо, больше не нужно вспоминать и набирать заново.
 * Отдельного справочника контактов для этого нет намеренно — он разошёлся бы с тем, куда
 * письма уходят на самом деле.
 *
 * Произвольный адрес ввести можно всегда: список подсказок — это память, а не ограничение.
 */
export function RecipientPicker({
  value,
  onChange,
  suggestions,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  suggestions: KnownRecipient[];
}) {
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const chosen = useMemo(() => new Set(value.map(addressOf)), [value]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return suggestions
      .filter((item) => !chosen.has(item.address.toLowerCase()))
      .filter(
        (item) =>
          !needle ||
          item.address.toLowerCase().includes(needle) ||
          (item.name ?? "").toLowerCase().includes(needle),
      )
      .slice(0, 8);
  }, [suggestions, chosen, query]);

  const add = (entry: string) => {
    const trimmed = entry.trim().replace(/[;,]+$/, "").trim();
    if (!trimmed) return;
    // Один и тот же человек не должен получить письмо дважды из-за того, что в одном месте
    // адрес записан с именем, а в другом — без.
    if (chosen.has(addressOf(trimmed))) {
      setQuery("");
      return;
    }
    onChange([...value, trimmed]);
    setQuery("");
  };

  const remove = (entry: string) => onChange(value.filter((item) => item !== entry));

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === "," || event.key === ";" || event.key === "Tab") {
      if (!query.trim()) return;
      event.preventDefault();
      // Enter на открытом списке подтверждает первую подсказку — иначе полностью набранное
      // имя «Иванов Иван» превратилось бы в получателя без адреса.
      add(visible.length > 0 && event.key === "Enter" ? toEntry(visible[0]) : query);
    } else if (event.key === "Backspace" && !query && value.length > 0) {
      remove(value[value.length - 1]);
    } else if (event.key === "Escape") {
      setIsOpen(false);
    }
  };

  return (
    <div className="relative">
      <div
        onClick={() => inputRef.current?.focus()}
        className="mt-1.5 flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-lg border border-white/10 bg-black/20 px-2 py-1.5 focus-within:border-indigo-500/50"
      >
        {value.map((entry) => (
          <span
            key={entry}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-500/15 py-1 pl-2 pr-1 text-xs text-indigo-200"
          >
            {entry}
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                remove(entry);
              }}
              className="rounded p-0.5 text-indigo-300/70 hover:bg-white/10 hover:text-indigo-100"
              aria-label={`Убрать ${entry}`}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setIsOpen(true);
          }}
          onFocus={() => setIsOpen(true)}
          // Закрытие с задержкой: клик по подсказке — это тоже потеря фокуса, и без паузы
          // список исчезал бы раньше, чем срабатывал выбор.
          onBlur={() => {
            window.setTimeout(() => setIsOpen(false), 150);
            if (query.trim()) add(query);
          }}
          onKeyDown={handleKeyDown}
          placeholder={value.length === 0 ? "адрес или имя получателя" : ""}
          className="min-w-[200px] flex-1 bg-transparent px-1 py-1 text-sm text-zinc-100 placeholder:text-zinc-600 focus:outline-none"
        />
      </div>

      {isOpen && visible.length > 0 && (
        <ul className="absolute z-20 mt-1 max-h-64 w-full overflow-y-auto rounded-lg border border-white/10 bg-zinc-900 py-1 shadow-xl">
          {visible.map((item) => (
            <li key={item.address}>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => add(toEntry(item))}
                className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left hover:bg-white/5"
              >
                <span className="text-sm text-zinc-200">
                  {item.name ? `${item.name} · ` : ""}
                  <span className="text-zinc-400">{item.address}</span>
                </span>
                <span className="shrink-0 text-xs text-zinc-600">
                  {item.sent_count > 0 ? `писем: ${item.sent_count} · ` : ""}
                  {formatLastSent(item.last_sent_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
