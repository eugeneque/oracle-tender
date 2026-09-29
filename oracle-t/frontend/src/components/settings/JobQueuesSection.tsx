import { ChevronDown, Layers } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { JobQueueItem, UserJobQueue } from "../../api/types";
import { formatDateTime } from "../../utils/format";
import { JOB_STATUS_LABELS } from "../../utils/jobQueue";
import { QueueRow } from "../nav/MyJobsIndicator";

const POLL_MS = 5000;

const STATUS_STYLE: Record<string, string> = {
  queued: "text-zinc-400",
  running: "text-indigo-300",
  success: "text-emerald-400",
  error: "text-red-400",
  cancelled: "text-amber-300",
};

/**
 * Очереди разборов всех пользователей (29.09.2026, только администратор). У каждого
 * пользователя одновременно разбирается одна закупка, остальные его разборы ждут; здесь
 * видно, кто что запустил, что идёт сейчас и что ждёт, а по «Что запускал» — история
 * запусков конкретного человека с итогом каждой задачи.
 */
export function JobQueuesSection() {
  const [queues, setQueues] = useState<UserJobQueue[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await api.get<UserJobQueue[]>("/jobs/queue?scope=all");
        if (!cancelled) {
          setQueues(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Не удалось загрузить очереди");
      }
    };
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <section className="mb-6 rounded-xl border border-white/[0.08] bg-white/[0.02] p-4">
      <div className="flex items-center gap-2 text-sm font-medium text-zinc-200">
        <Layers size={15} className="text-indigo-400" />
        Очереди разборов
      </div>
      <p className="mt-1 text-xs text-zinc-500">
        У каждого пользователя одновременно разбирается одна закупка, остальные ждут по порядку
        запуска. Обновляется каждые 5 секунд.
      </p>
      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>
      )}
      {queues !== null && queues.length === 0 && (
        <p className="mt-3 text-sm text-zinc-500">Сейчас ничего не разбирается и не ждёт.</p>
      )}
      <div className="mt-3 space-y-3">
        {queues?.map((queue) => (
          <UserQueueCard key={queue.user_id ?? "system"} queue={queue} />
        ))}
      </div>
      <LaunchedLookup queues={queues ?? []} />
    </section>
  );
}

function UserQueueCard({ queue }: { queue: UserJobQueue }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="rounded-lg border border-white/[0.06] bg-black/10 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-zinc-200">{queue.user_name}</span>
        <span className="text-xs text-zinc-500">
          идёт {queue.running.length} · в очереди {queue.queued.length}
        </span>
        <button
          type="button"
          onClick={() => setIsOpen((value) => !value)}
          className="ml-auto flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200"
        >
          Что запускал
          <ChevronDown size={12} className={`transition-transform ${isOpen ? "rotate-180" : ""}`} />
        </button>
      </div>
      <div className="mt-2">
        {queue.running.map((item) => (
          <QueueRow key={item.id} item={item} />
        ))}
        {queue.queued.map((item, index) => (
          <QueueRow key={item.id} item={item} position={index + 1} />
        ))}
      </div>
      {isOpen && <LaunchedList userId={queue.user_id} />}
    </div>
  );
}

/** «Что запускал» для пользователя, у которого сейчас пустая очередь, — выбором из списка. */
function LaunchedLookup({ queues }: { queues: UserJobQueue[] }) {
  const [users, setUsers] = useState<{ id: string; full_name: string }[]>([]);
  const [selected, setSelected] = useState<string>("");

  useEffect(() => {
    api
      .get<{ id: string; full_name: string }[]>("/users")
      .then(setUsers)
      .catch(() => setUsers([]));
  }, []);

  const busy = new Set(queues.map((queue) => queue.user_id));
  const idle = users.filter((user) => !busy.has(user.id));
  if (idle.length === 0) return null;

  return (
    <div className="mt-4 border-t border-white/[0.06] pt-3">
      <label className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
        Что запускал пользователь без текущих разборов:
        <select
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
          className="rounded-md border border-white/10 bg-black/20 px-2 py-1 text-xs text-zinc-200"
        >
          <option value="">— выберите —</option>
          {idle.map((user) => (
            <option key={user.id} value={user.id}>
              {user.full_name}
            </option>
          ))}
        </select>
      </label>
      {selected && <LaunchedList key={selected} userId={selected} />}
    </div>
  );
}

function LaunchedList({ userId }: { userId: string | null }) {
  const [items, setItems] = useState<JobQueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const query = userId ? `user_id=${userId}` : "system=true";
    api
      .get<JobQueueItem[]>(`/jobs/launched?${query}&limit=30`)
      .then(setItems)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить историю"));
  }, [userId]);

  if (error) return <p className="mt-2 text-xs text-red-300">{error}</p>;
  if (items === null) return <p className="mt-2 text-xs text-zinc-500">Загружаю…</p>;
  if (items.length === 0) return <p className="mt-2 text-xs text-zinc-500">Запусков нет.</p>;

  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-zinc-500">
          <tr>
            <th className="py-1 pr-3 font-normal">Запущено</th>
            <th className="py-1 pr-3 font-normal">Что</th>
            <th className="py-1 pr-3 font-normal">Закупка</th>
            <th className="py-1 pr-3 font-normal">Итог</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} className="border-t border-white/[0.04] align-top">
              <td className="whitespace-nowrap py-1.5 pr-3 text-zinc-400">{formatDateTime(item.created_at)}</td>
              <td className="whitespace-nowrap py-1.5 pr-3 text-zinc-300">{item.kind_label}</td>
              <td className="py-1.5 pr-3 text-zinc-300">
                {item.tender_title ?? "—"}
                {item.tender_external_id && <span className="text-zinc-500"> · № {item.tender_external_id}</span>}
              </td>
              <td className="py-1.5 pr-3">
                <span className={STATUS_STYLE[item.status] ?? "text-zinc-400"}>
                  {JOB_STATUS_LABELS[item.status] ?? item.status}
                </span>
                {item.message && item.status !== "running" && (
                  <span className="block max-w-md truncate text-zinc-500" title={item.message}>
                    {item.message}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
