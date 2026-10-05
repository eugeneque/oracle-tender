import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Loader2, Search, X } from "lucide-react";
import { motion } from "motion/react";

import { ApiError, api } from "../api/client";
import type { OkpdNode } from "../api/types";
import { dialogVariants, scrimVariants } from "../utils/motion";

/**
 * Выбор кодов ОКПД2 деревом классификатора (05.10.2026).
 *
 * Ветки раскрываются по одному уровню — справочник на 21 тысячу записей целиком в браузер
 * не грузится. Выбор — префиксами: отметили «26.51», и в фильтр уходит всё вложенное. Раздел-
 * буква отдельным кодом не хранится (у закупки код числовой), поэтому его выбор записывает
 * классы раздела (`select_codes`).
 *
 * Состояние галочки выводится из набора выбранных кодов, а не хранится на узле:
 * отмечен — если сам код или его предок в выборе; «часть» — если выбраны только потомки.
 * Снять галочку с узла внутри целиком выбранной ветки можно: ветка разбирается на соседей по
 * пути, и остаётся выбранным всё, кроме снятого.
 */

const ROOT_KEY = "";

type Children = Record<string, string[]>;

function coveredBy(selected: string[], code: string): boolean {
  return selected.some((value) => code === value || code.startsWith(value));
}

function hasSelectedInside(selected: string[], code: string): boolean {
  return selected.some((value) => value.startsWith(code) && value !== code);
}

/** Обычный узел: выбирается своим кодом. Раздел-буква выбирается кодами своих классов. */
function isPlain(node: OkpdNode): boolean {
  return node.select_codes.length === 1 && node.select_codes[0] === node.code;
}

function useOkpdNodes() {
  const [nodes, setNodes] = useState<Record<string, OkpdNode>>({});
  const [children, setChildren] = useState<Children>({});
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  // Ссылка на актуальный кэш: `loadChildren` вызывается и из обработчиков, где замыкание
  // успело бы устареть, а повторный запрос уже загруженного уровня — лишний.
  const childrenRef = useRef<Children>({});
  childrenRef.current = children;

  const remember = useCallback((list: OkpdNode[]) => {
    setNodes((prev) => {
      const next = { ...prev };
      for (const node of list) next[node.code] = node;
      return next;
    });
  }, []);

  const loadChildren = useCallback(
    async (parent: string | null): Promise<string[]> => {
      const key = parent ?? ROOT_KEY;
      const known = childrenRef.current[key];
      if (known) return known;
      setLoading((prev) => new Set(prev).add(key));
      try {
        const query = parent ? `?parent=${encodeURIComponent(parent)}` : "";
        const list = await api.get<OkpdNode[]>(`/dictionaries/okpd2${query}`);
        remember(list);
        const codes = list.map((node) => node.code);
        childrenRef.current = { ...childrenRef.current, [key]: codes };
        setChildren(childrenRef.current);
        setError(null);
        return codes;
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить классификатор");
        return [];
      } finally {
        setLoading((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      }
    },
    [remember],
  );

  return { nodes, remember, children, loading, error, loadChildren };
}

function Checkbox({
  checked,
  indeterminate,
  disabled,
  onChange,
  title,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled?: boolean;
  onChange: () => void;
  title?: string;
}) {
  return (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      title={title}
      ref={(el) => {
        if (el) el.indeterminate = indeterminate;
      }}
      onChange={onChange}
      className="h-4 w-4 shrink-0 cursor-pointer rounded border-white/20 bg-white/5 accent-indigo-500 disabled:cursor-not-allowed"
    />
  );
}

export function OkpdTreePicker({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const { nodes, remember, children, loading, error, loadChildren } = useOkpdNodes();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<OkpdNode[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);

  useEffect(() => {
    void loadChildren(null);
  }, [loadChildren]);

  // Подписи для уже выбранных кодов: профиль открывают с готовым набором, а дерево к ним
  // ещё не раскрыто.
  useEffect(() => {
    const missing = value.filter((code) => !nodes[code]);
    if (missing.length === 0) return;
    const params = missing.map((code) => `code=${encodeURIComponent(code)}`).join("&");
    void api
      .get<OkpdNode[]>(`/dictionaries/okpd2/lookup?${params}`)
      .then(remember)
      .catch(() => undefined);
  }, [value, nodes, remember]);

  useEffect(() => {
    const text = query.trim();
    if (text.length < 2) {
      setResults(null);
      return;
    }
    setIsSearching(true);
    const timer = setTimeout(() => {
      void api
        .get<OkpdNode[]>(`/dictionaries/okpd2/search?q=${encodeURIComponent(text)}`)
        .then((list) => {
          remember(list);
          setResults(list);
        })
        .catch(() => setResults([]))
        .finally(() => setIsSearching(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [query, remember]);

  const toggleExpand = (code: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
    void loadChildren(code);
  };

  const stateOf = (node: OkpdNode) => {
    if (isPlain(node)) {
      const checked = coveredBy(value, node.code);
      return { checked, indeterminate: !checked && hasSelectedInside(value, node.code) };
    }
    // Раздел-буква: выбран, когда выбраны все его классы.
    const covered = node.select_codes.filter((code) => coveredBy(value, code)).length;
    const checked = node.select_codes.length > 0 && covered === node.select_codes.length;
    const inside = node.select_codes.some((code) => hasSelectedInside(value, code));
    return { checked, indeterminate: !checked && (covered > 0 || inside) };
  };

  /** Предок, которым узел выбран «в составе», — снять его галочку отдельно нельзя, пока
   * ветку не разберут. */
  const coveringAncestor = (code: string): string | null =>
    value.find((item) => item !== code && code.startsWith(item)) ?? null;

  const select = (node: OkpdNode) => {
    const inside = (code: string) =>
      node.select_codes.some((selectCode) => code.startsWith(selectCode));
    const kept = value.filter((code) => !inside(code));
    onChange([...kept, ...node.select_codes.filter((code) => !coveredBy(kept, code))]);
  };

  const unselect = async (node: OkpdNode) => {
    const inside = (code: string) =>
      node.select_codes.some((selectCode) => code.startsWith(selectCode));
    const ancestor = isPlain(node) ? coveringAncestor(node.code) : null;
    if (!ancestor) {
      onChange(value.filter((code) => !inside(code)));
      return;
    }
    // Узел выбран через предка: разбираем ветку — предок уходит, а его соседи по пути к
    // узлу остаются выбранными.
    const chain = [node.code];
    let current: string | null = node.code;
    while (current && current !== ancestor) {
      current = nodes[current]?.parent ?? null;
      if (current) chain.unshift(current);
    }
    if (chain[0] !== ancestor) return;
    const rest: string[] = [];
    for (let i = 0; i < chain.length - 1; i += 1) {
      const siblings = await loadChildren(chain[i]);
      rest.push(...siblings.filter((code) => code !== chain[i + 1]));
    }
    onChange([...value.filter((code) => code !== ancestor), ...rest]);
  };

  const toggle = (node: OkpdNode) => {
    if (stateOf(node).checked) void unselect(node);
    else select(node);
  };

  const renderRow = (node: OkpdNode, depth: number, flat = false) => {
    const state = stateOf(node);
    const ancestor = isPlain(node) ? coveringAncestor(node.code) : null;
    // В результатах поиска дерево не раскрыто, и разобрать выбранную ветку по пути нечем.
    const locked = flat && state.checked && ancestor !== null;
    const isOpen = expanded.has(node.code);
    return (
      <div key={`${flat ? "s" : "t"}-${node.code}`}>
        <div
          className="flex items-start gap-2 rounded-md py-1.5 pr-2 hover:bg-white/[0.04]"
          style={{ paddingLeft: flat ? 8 : 8 + depth * 18 }}
        >
          {!flat && node.has_children ? (
            <button
              type="button"
              onClick={() => toggleExpand(node.code)}
              aria-label={isOpen ? "Свернуть" : "Раскрыть"}
              aria-expanded={isOpen}
              className="mt-px flex h-4 w-4 shrink-0 items-center justify-center text-zinc-500 hover:text-zinc-200"
            >
              {loading.has(node.code) ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <ChevronRight
                  size={14}
                  className={`transition-transform ${isOpen ? "rotate-90" : ""}`}
                />
              )}
            </button>
          ) : (
            <span className="h-4 w-4 shrink-0" />
          )}
          <Checkbox
            checked={state.checked}
            indeterminate={state.indeterminate}
            disabled={locked}
            onChange={() => toggle(node)}
            title={locked ? `Выбрано целиком через ${ancestor}` : undefined}
          />
          <label
            className="min-w-0 flex-1 cursor-pointer text-sm leading-snug text-zinc-200"
            onClick={() => {
              if (!locked) toggle(node);
            }}
          >
            <span className="font-medium tabular-nums text-zinc-100">{node.code}</span>
            <span className="text-zinc-500">: </span>
            {node.name}
          </label>
        </div>
        {!flat && isOpen && (
          <div>
            {(children[node.code] ?? []).map((code) =>
              nodes[code] ? renderRow(nodes[code], depth + 1) : null,
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-3">
      {value.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {value.map((code) => (
            <span
              key={code}
              title={nodes[code]?.name}
              className="inline-flex max-w-full items-center gap-1 rounded-md border border-indigo-500/25 bg-indigo-500/[0.07] py-0.5 pl-1.5 pr-1 text-[11px] text-indigo-200/90"
            >
              <span className="tabular-nums">{code}</span>
              {nodes[code] && (
                <span className="max-w-[16rem] truncate text-zinc-400">{nodes[code].name}</span>
              )}
              <button
                type="button"
                onClick={() => onChange(value.filter((item) => item !== code))}
                aria-label={`Убрать ${code}`}
                className="rounded p-0.5 text-zinc-500 hover:bg-white/10 hover:text-zinc-100"
              >
                <X size={11} />
              </button>
            </span>
          ))}
          <button
            type="button"
            onClick={() => onChange([])}
            className="text-xs text-zinc-500 hover:text-zinc-300"
          >
            очистить
          </button>
        </div>
      )}

      <div className="relative">
        <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Найти по коду или названию: 26.51.63 или «счетчики электроэнергии»"
          className="w-full rounded-lg border border-white/10 bg-white/[0.03] py-2 pl-9 pr-9 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
        />
        {isSearching && (
          <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-zinc-500" />
        )}
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}

      <div className="max-h-[22rem] overflow-y-auto rounded-lg border border-white/[0.08] bg-white/[0.02] p-1">
        {results !== null ? (
          results.length === 0 ? (
            <div className="px-3 py-6 text-center text-sm text-zinc-500">Ничего не найдено</div>
          ) : (
            <>
              {results.map((node) => renderRow(node, 0, true))}
              {results.length >= 60 && (
                <div className="px-3 py-2 text-xs text-zinc-500">
                  Показаны первые 60 — уточните запрос.
                </div>
              )}
            </>
          )
        ) : (children[ROOT_KEY] ?? []).length === 0 && loading.has(ROOT_KEY) ? (
          <div className="flex items-center gap-2 px-3 py-6 text-sm text-zinc-500">
            <Loader2 size={14} className="animate-spin" />
            Загрузка классификатора…
          </div>
        ) : (
          (children[ROOT_KEY] ?? []).map((code) => (nodes[code] ? renderRow(nodes[code], 0) : null))
        )}
      </div>
    </div>
  );
}

/** Выбор кодов в окне — для панели фильтров страницы тендеров. */
export function OkpdPickerModal({
  value,
  onApply,
  onCancel,
}: {
  value: string[];
  onApply: (codes: string[]) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <motion.div
      variants={scrimVariants}
      initial="initial"
      animate="animate"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 px-4 py-8"
      onClick={onCancel}
    >
      <motion.div
        variants={dialogVariants}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Коды ОКПД2"
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-white">Коды ОКПД2</h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              Раскрывайте разделы и отмечайте нужные ветки: выбранный код включает всё вложенное.
            </p>
          </div>
          <button
            onClick={onCancel}
            aria-label="Закрыть"
            className="rounded-md p-1 text-zinc-500 hover:bg-white/10 hover:text-zinc-100"
          >
            <X size={16} />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">
          <OkpdTreePicker value={draft} onChange={setDraft} />
        </div>
        <div className="flex items-center justify-between gap-3 border-t border-white/[0.08] px-5 py-3">
          <span className="text-xs text-zinc-500">Выбрано: {draft.length}</span>
          <div className="flex gap-2">
            <button
              onClick={onCancel}
              className="rounded-lg border border-white/10 px-3 py-1.5 text-sm text-zinc-300 hover:bg-white/5"
            >
              Отмена
            </button>
            <button
              onClick={() => onApply(draft)}
              className="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-snow hover:bg-indigo-400"
            >
              Применить
            </button>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
