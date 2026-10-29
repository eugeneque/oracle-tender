import { Database } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { MarketStatus } from "../../api/types";
import { formatDateTime } from "../../utils/format";
import { MarketDictionary } from "./MarketDictionary";
import { MarketReport } from "./MarketReport";

/**
 * Вкладки «Рынок ПУ» и «Справочник рынка ПУ» раздела «Аналитика» (раздел 79 ARCHITECTURE.md).
 * Подзаголовок с источником и датой последнего пакета обязателен на каждом экране (раздел 8
 * ТЗ): отчёт уходит руководству, и без даты непонятно, на какой день он верен.
 */
export function MarketTab({ view }: { view: "report" | "dictionary" }) {
  const [status, setStatus] = useState<MarketStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api
      .get<MarketStatus>("/meter-market/status")
      .then(setStatus)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить состояние"));
  }, []);
  useEffect(load, [load]);

  if (error) return <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">{error}</div>;
  if (!status) return <div className="py-24 text-center text-sm text-zinc-600">Загружаю…</div>;

  const last = status.last_package;
  return (
    <div className="space-y-5">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500">
        <Database size={13} className="text-indigo-400" />
        по данным ФГИС «Аршин»
        {last && (
          <span>
            · пакет {last.package_id.replace(/(\d{4})(\d{2})(\d{2})/, "$3.$2.$1")} загружен {formatDateTime(last.finished_at)}
          </span>
        )}
        {status.edge_label && <span>· отчёт строится по {status.edge_label} включительно</span>}
        {status.pending_package && (
          <span className="text-amber-400">
            · идёт загрузка {status.pending_package.package_id}
            {status.pending_package.checkpoint ? ` (${status.pending_package.checkpoint})` : ""}
          </span>
        )}
      </p>
      {view === "report" ? (
        last ? (
          <MarketReport status={status} />
        ) : (
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] px-6 py-16 text-center text-sm text-zinc-500">
            Поверки ФГИС «Аршин» ещё не загружены. Первая загрузка — слепок выгрузки (≈12 ГБ) и изменения после него;
            дальше Сова догружает изменения каждую ночь.
          </div>
        )
      ) : (
        <MarketDictionary status={status} onChanged={load} />
      )}
    </div>
  );
}
