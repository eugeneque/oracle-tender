import { useEffect, useState } from "react";
import { RefreshCw, Trash2 } from "lucide-react";
import { ApiError, api } from "../../api/client";
import { formatDateTime } from "../../utils/format";

/**
 * Банковские гарантии и реестры ЕИС по закупке (19.11.2026, раздел 115 ARCHITECTURE.md) — во
 * вкладке «Журнал событий».
 *
 * Гарантии вносит тендерный отдел: срок действия выданной гарантии нигде не публикуется, а без
 * него не ответить, нужно ли продлевать её при переносе сроков (п. 32 перечня Bitrix24).
 * Реестры жалоб и договоров ЕИС мониторинг перечитывает сам раз в 6 часов; кнопка — сейчас.
 */

interface Guarantee {
  id: string;
  kind: string;
  kind_label: string;
  number: string | null;
  bank: string | null;
  amount: string | null;
  valid_until: string;
  covered: boolean | null;
  needed_until: string | null;
  basis: string;
}

interface Registry {
  complaints: Array<Record<string, unknown>>;
  contracts: Array<Record<string, unknown>>;
  customer_history: Array<Record<string, unknown>>;
  registry_checked_at: string | null;
}

const KINDS = [
  { value: "APPLICATION", label: "Обеспечение заявки" },
  { value: "CONTRACT", label: "Обеспечение исполнения договора" },
  { value: "WARRANTY", label: "Гарантийные обязательства" },
];

const input =
  "rounded-lg border border-white/10 bg-white/[0.03] px-2.5 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-white/25 focus:outline-none";

function ruDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length === 10 ? new Date(value).toLocaleDateString("ru-RU") : formatDateTime(value) ?? value;
}

export function TenderGuaranteesRegistry({ tenderId }: { tenderId: string }) {
  const [guarantees, setGuarantees] = useState<Guarantee[]>([]);
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [form, setForm] = useState({ kind: "APPLICATION", number: "", bank: "", amount: "", valid_until: "" });
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    void api.get<Guarantee[]>(`/tenders/${tenderId}/guarantees`).then(setGuarantees).catch(() => setGuarantees([]));
    void api.get<Registry>(`/tenders/${tenderId}/eis-registry`).then(setRegistry).catch(() => setRegistry(null));
  }, [tenderId]);

  async function add() {
    setError(null);
    try {
      const list = await api.post<Guarantee[]>(`/tenders/${tenderId}/guarantees`, {
        kind: form.kind,
        number: form.number || null,
        bank: form.bank || null,
        amount: form.amount ? Number(form.amount.replace(/\s/g, "").replace(",", ".")) : null,
        valid_until: form.valid_until,
      });
      setGuarantees(list);
      setForm({ ...form, number: "", bank: "", amount: "", valid_until: "" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить гарантию");
    }
  }

  async function remove(id: string) {
    try {
      setGuarantees(await api.delete<Guarantee[]>(`/tenders/${tenderId}/guarantees/${id}`));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось удалить гарантию");
    }
  }

  async function refresh() {
    setRefreshing(true);
    setError(null);
    try {
      setRegistry(await api.post<Registry>(`/tenders/${tenderId}/eis-registry/refresh`));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "ЕИС не ответила");
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="mb-6 space-y-6">
      <section>
        <h4 className="mb-2 text-sm font-semibold text-white">Банковские гарантии</h4>
        {guarantees.length > 0 && (
          <ul className="mb-3 divide-y divide-white/[0.06]">
            {guarantees.map((g) => (
              <li key={g.id} className="flex items-start justify-between gap-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="text-zinc-200">
                    {g.kind_label}
                    {g.number ? ` · № ${g.number}` : ""} · до {ruDate(g.valid_until)}
                  </div>
                  <div className="text-xs text-zinc-500">
                    {[g.bank, g.amount ? `${Number(g.amount).toLocaleString("ru-RU")} ₽` : null].filter(Boolean).join(" · ")}
                  </div>
                  <div className={`text-xs ${g.covered === false ? "text-amber-400" : "text-zinc-500"}`}>
                    {g.covered === false ? "Требуется продление. " : g.covered ? "Срок покрывает. " : ""}
                    {g.basis}
                  </div>
                </div>
                <button type="button" aria-label="Удалить" onClick={() => void remove(g.id)} className="p-1 text-zinc-500 hover:text-red-400">
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <select className={input} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>
                {k.label}
              </option>
            ))}
          </select>
          <input className={`${input} w-28`} placeholder="Номер" value={form.number} onChange={(e) => setForm({ ...form, number: e.target.value })} />
          <input className={`${input} w-40`} placeholder="Банк" value={form.bank} onChange={(e) => setForm({ ...form, bank: e.target.value })} />
          <input className={`${input} w-28`} placeholder="Сумма, ₽" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
          <input
            className={input}
            type="date"
            aria-label="Действует до"
            value={form.valid_until}
            onChange={(e) => setForm({ ...form, valid_until: e.target.value })}
          />
          <button
            type="button"
            disabled={!form.valid_until}
            onClick={() => void add()}
            className="rounded-lg bg-white px-3 py-1.5 text-sm font-medium text-black disabled:opacity-40"
          >
            Добавить
          </button>
        </div>
      </section>

      <section>
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
          <h4 className="text-sm font-semibold text-white">Реестры ЕИС: жалобы и договоры</h4>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={refreshing}
            className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-white disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {registry?.registry_checked_at ? `Проверено ${formatDateTime(registry.registry_checked_at)}` : "Проверить"}
          </button>
        </div>
        {!registry?.registry_checked_at ? (
          <div className="text-xs text-zinc-600">Реестры ещё не проверялись</div>
        ) : (
          <ul className="space-y-1 text-sm text-zinc-300">
            {registry.complaints.length === 0 && <li className="text-xs text-zinc-500">Жалоб в реестре ЕИС нет</li>}
            {registry.complaints.map((c) => (
              <li key={String(c.number)}>
                Жалоба № {String(c.number)} · {String(c.status ?? "—")}
                {c.outcome ? ` · ${String(c.outcome)}` : ""}
                {c.hearing_at ? ` · рассмотрение ${ruDate(String(c.hearing_at))}` : ""}
              </li>
            ))}
            {registry.contracts.map((c) => (
              <li key={String(c.number)}>
                Договор № {String(c.number)} · {String(c.status ?? "—")}
                {c.price ? ` · ${Number(c.price).toLocaleString("ru-RU")} ₽` : ""}
                {Array.isArray(c.versions) && c.versions.length > 1 ? ` · редакций ${c.versions.length}` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>
      {error && <div className="text-xs text-red-400">{error}</div>}
    </div>
  );
}
