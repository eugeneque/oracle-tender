import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { AiFeedback, AiFeedbackPage } from "../../api/types";
import { AiFeedbackCard } from "../tender-detail/AiFeedbackCard";

/**
 * «Ответы специалистов» (28.09.2026): что тендерные специалисты отвечали на заключения ИИ
 * по всем закупкам — кто и когда, текст замечания, заключение до и после пересмотра,
 * ссылка на закупку. Отсюда видно, в чём модель ошибается систематически, — материал для
 * правки промптов и каталога. Доступно всем, роль не нужна.
 */

const PAGE_SIZE = 20;

const FILTERS: { value: "" | "disagree" | "agree"; label: string }[] = [
  { value: "", label: "Все" },
  { value: "disagree", label: "Несогласия" },
  { value: "agree", label: "Согласия" },
];

export function SpecialistFeedbackSection() {
  const [kind, setKind] = useState<"" | "disagree" | "agree">("");
  const [items, setItems] = useState<AiFeedback[] | null>(null);
  const [total, setTotal] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (offset: number) => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (kind) params.set("kind", kind);
      return api.get<AiFeedbackPage>(`/ai-feedback?${params.toString()}`);
    },
    [kind],
  );

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setError(null);
    load(0)
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setTotal(page.total);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Не удалось загрузить ответы");
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  const loadMore = async () => {
    if (!items) return;
    setIsLoadingMore(true);
    try {
      const page = await load(items.length);
      setItems([...items, ...page.items]);
      setTotal(page.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить ответы");
    } finally {
      setIsLoadingMore(false);
    }
  };

  return (
    <section>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-zinc-100">Ответы специалистов</h2>
        <div className="flex rounded-lg border border-white/[0.08] p-0.5">
          {FILTERS.map((filter) => (
            <button
              key={filter.value}
              onClick={() => setKind(filter.value)}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                kind === filter.value ? "bg-white/10 text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </div>
      <p className="mb-4 text-sm text-zinc-500">
        Что специалисты отвечали на заключения ИИ: замечание, заключение до и после пересмотра и
        ответ модели. Название закупки открывает её карточку с той же документацией.
      </p>

      {error && (
        <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}

      {items === null ? (
        <div className="flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 size={14} className="animate-spin" />
          Загрузка…
        </div>
      ) : items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-white/10 p-6 text-center text-sm text-zinc-500">
          Ответов пока нет. Они появляются, когда специалист нажимает «Согласен» или «Не согласен»
          под заключением ИИ в карточке закупки.
        </p>
      ) : (
        <div className="space-y-2.5">
          {items.map((item) => (
            <AiFeedbackCard key={item.id} item={item} showTender />
          ))}
          {items.length < total && (
            <button
              onClick={() => void loadMore()}
              disabled={isLoadingMore}
              className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
            >
              {isLoadingMore && <Loader2 size={13} className="animate-spin" />}
              Показать ещё ({total - items.length})
            </button>
          )}
        </div>
      )}
    </section>
  );
}
