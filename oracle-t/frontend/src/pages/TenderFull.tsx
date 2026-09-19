import { ArrowLeft, ChevronRight, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { Tender } from "../api/types";
import { AppShell } from "../components/AppShell";
import { TenderDetailPanel } from "../components/TenderDetailPanel";

/**
 * Закупка на отдельной странице (замечание 17.09.2026): та же карточка, что справа в
 * двухпанельном режиме, но во всю ширину и высоту окна — матрица соответствия на
 * тринадцать производителей и таблицы карточки ЕИС в узкой панели читались с трудом.
 *
 * У страницы свой адрес, поэтому её можно открыть в новой вкладке, переслать ссылкой и
 * вернуться к ней из избранного в меню учётной записи.
 */
export function TenderFullPage() {
  const { tenderId } = useParams<{ tenderId: string }>();
  const [tender, setTender] = useState<Tender | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!tenderId) return;
    let cancelled = false;
    setTender(null);
    setError(null);
    api
      .get<Tender>(`/tenders/${tenderId}`)
      .then((loaded) => {
        if (!cancelled) setTender(loaded);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError && err.status === 404
            ? "Закупка не найдена — возможно, ссылка устарела"
            : err instanceof ApiError
              ? err.message
              : "Не удалось загрузить закупку",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [tenderId]);

  return (
    <AppShell>
      <div className="flex h-full flex-col px-8 pb-3 pt-4">
        <div className="mb-2 flex shrink-0 items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-1.5 text-xs text-zinc-500">
            <Link to="/tenders" className="hover:text-zinc-300">
              Тендеры
            </Link>
            <ChevronRight size={14} className="text-zinc-700" />
            <span className="truncate text-zinc-300">
              {tender ? `№ ${tender.external_id}` : "Закупка"}
            </span>
          </div>
          <Link
            to="/tenders"
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5"
          >
            <ArrowLeft size={13} />К списку
          </Link>
        </div>

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02]">
          {error ? (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-red-400">
              {error}
            </div>
          ) : tender ? (
            <TenderDetailPanel key={tender.id} tender={tender} expandable={false} />
          ) : (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-zinc-600">
              <Loader2 size={16} className="animate-spin" />
              Загрузка…
            </div>
          )}
        </div>
      </div>
    </AppShell>
  );
}
