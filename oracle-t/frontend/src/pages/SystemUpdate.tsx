import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CloudDownload,
  Database,
  GitCommitHorizontal,
  Loader2,
  Package,
  RefreshCw,
  Terminal,
  XCircle,
} from "lucide-react";

import { api, ApiError } from "../api/client";
import type { SystemCommit, SystemUpdateCheck, SystemUpdateRun, SystemUpdateStatus } from "../api/types";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { CHANGE_KIND_LABELS } from "../content/changelog";

/**
 * «Обновление» — новая версия Sova с GitHub без входа на сервер (09.11.2026, раздел 91
 * ARCHITECTURE.md). Только администратору.
 *
 * Страница сама сверяется с GitHub при открытии и показывает то же, что «sudo ./update.sh»
 * в консоли (вывод update.sh --check — как есть), а сверху — по-человечески: записи журнала
 * изменений, коммиты, миграции базы, новые зависимости. Установку выполняет служба
 * oracle-t-update-web от root; backend во время неё перезапускается, поэтому опрос хода
 * переживает недоступность API и сам продолжается, когда Sova поднимется.
 */

const KIND_STYLE = {
  new: "bg-emerald-500/10 text-emerald-300 ring-emerald-400/25",
  improved: "bg-sky-500/10 text-sky-300 ring-sky-400/25",
  fixed: "bg-amber-500/10 text-amber-300 ring-amber-400/25",
} as const;

const POLL_MS = 2000;

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

function formatDay(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

function commitsWord(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "изменение";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return "изменения";
  return "изменений";
}

function Card({ title, icon, children, actions }: { title: string; icon?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="mb-5 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
      <div className="mb-4 flex items-center justify-between gap-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
          {icon}
          {title}
        </h2>
        {actions}
      </div>
      {children}
    </section>
  );
}

function Version({ commit, label }: { commit: SystemCommit | null | undefined; label: string }) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-zinc-500">{label}</div>
      {commit ? (
        <div className="mt-1 min-w-0">
          <div className="flex items-center gap-2 text-sm text-zinc-100">
            <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-xs text-zinc-300">{commit.short}</code>
            <span className="text-zinc-500">{formatDateTime(commit.date)}</span>
          </div>
          <div className="mt-1 truncate text-sm text-zinc-300" title={commit.subject}>
            {commit.subject}
          </div>
        </div>
      ) : (
        <div className="mt-1 text-sm text-zinc-500">неизвестно</div>
      )}
    </div>
  );
}

function ConsoleBlock({ text, autoScroll = false, maxHeight = "max-h-96" }: { text: string; autoScroll?: boolean; maxHeight?: string }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (autoScroll && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text, autoScroll]);
  return (
    <pre
      ref={ref}
      className={`${maxHeight} overflow-auto whitespace-pre-wrap break-words rounded-xl border border-white/[0.06] bg-black/40 p-4 font-mono text-[12px] leading-relaxed text-zinc-300`}
    >
      {text || "—"}
    </pre>
  );
}

const RUN_LABELS: Record<SystemUpdateRun["state"], string> = {
  idle: "Из интерфейса ещё не обновлялись",
  queued: "Запрос отправлен — служба обновления запускается…",
  running: "Идёт обновление…",
  success: "Обновление завершено",
  failed: "Обновление не удалось",
  interrupted: "Обновление прервано",
};

export function SystemUpdatePage() {
  const [status, setStatus] = useState<SystemUpdateStatus | null>(null);
  const [check, setCheck] = useState<SystemUpdateCheck | null>(null);
  const [run, setRun] = useState<SystemUpdateRun | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmForce, setConfirmForce] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  const [showConsole, setShowConsole] = useState(false);
  const [showCommits, setShowCommits] = useState(false);
  // Обновление, запущенное с этой страницы, — после него нужно перезагрузить интерфейс.
  const [launchedHere, setLaunchedHere] = useState(false);

  const runActive = run?.state === "queued" || run?.state === "running";

  const loadStatus = useCallback(async () => {
    const next = await api.get<SystemUpdateStatus>("/system/update/status");
    setStatus(next);
    setRun(next.run);
    return next;
  }, []);

  const runCheck = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      setCheck(await api.post<SystemUpdateCheck>("/system/update/check"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось проверить обновления");
    } finally {
      setChecking(false);
    }
  }, []);

  // При открытии: состояние, затем сразу проверка GitHub — если обновление не идёт.
  useEffect(() => {
    loadStatus()
      .then((next) => {
        const active = next.run.state === "queued" || next.run.state === "running";
        if (next.can_check && !active) void runCheck();
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить состояние"));
  }, [loadStatus, runCheck]);

  // Опрос хода. Во время установки backend перезапускается — ошибки сети здесь ожидаемы.
  useEffect(() => {
    if (!runActive) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api.get<SystemUpdateRun>("/system/update/run");
        setBackendDown(false);
        setRun(next);
        if (next.state !== "queued" && next.state !== "running") {
          void loadStatus();
        }
      } catch {
        setBackendDown(true);
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [runActive, loadStatus]);

  const startUpdate = async (force: boolean) => {
    setStarting(true);
    setError(null);
    try {
      const next = await api.post<SystemUpdateRun>("/system/update/start", { force });
      setRun(next);
      setLaunchedHere(true);
      setConfirmForce(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось запустить обновление");
    } finally {
      setStarting(false);
    }
  };

  const cancelRequest = async () => {
    try {
      setRun(await api.post<SystemUpdateRun>("/system/update/cancel"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось отменить запрос");
    }
  };

  const canInstall = !!status?.can_install && !runActive;
  const available = check?.state === "available";
  const releases = check?.releases ?? [];
  const commits = check?.commits ?? [];

  return (
    <AppShell>
      <div className="mx-auto max-w-4xl px-8 py-8">
        <PageHeader
          breadcrumb={["Sova", "Система", "Обновление"]}
          title="Обновление"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <CloudDownload size={18} />
            </span>
          }
          actions={
            status?.can_check && (
              <button
                onClick={() => void runCheck()}
                disabled={checking || runActive}
                className="inline-flex items-center gap-2 rounded-lg border border-white/10 px-3 py-1.5 text-sm text-zinc-300 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
              >
                <RefreshCw size={14} className={checking ? "animate-spin" : ""} />
                Проверить снова
              </button>
            )
          }
        />
        <p className="-mt-2 mb-6 text-sm text-zinc-500">
          Новая версия с GitHub без входа на сервер — то же, что <code className="text-zinc-400">sudo ./update.sh</code>:
          резервная копия базы, новый код, зависимости, миграции, сборка интерфейса и перезапуск. Данные,
          настройки, пароли площадок и документы закупок не трогаются.
        </p>

        {error && (
          <div className="mb-5 flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
            <XCircle size={16} className="mt-0.5 shrink-0" />
            {error}
          </div>
        )}

        {status && (
          <Card title="Установленная версия" icon={<GitCommitHorizontal size={15} className="text-zinc-500" />}>
            <div className="grid gap-5 sm:grid-cols-2">
              <Version commit={status.current} label="Сейчас работает" />
              <div className="text-sm text-zinc-400">
                <div className="text-xs text-zinc-500">Источник</div>
                <div className="mt-1 break-all">{status.remote_url ?? "—"}</div>
                <div className="mt-1 text-zinc-500">
                  ветка <code className="text-zinc-400">{status.branch}</code>
                  {status.mode === "server" && (
                    <> · автообновление ночью: {status.auto_update ? "включено" : "выключено"}</>
                  )}
                </div>
              </div>
            </div>
            {status.unavailable_reason && (
              <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                {status.unavailable_reason}
              </div>
            )}
          </Card>
        )}

        {/* Ход обновления — сверху, пока идёт, и после окончания. */}
        {run && run.state !== "idle" && (
          <Card
            title={RUN_LABELS[run.state]}
            icon={
              runActive ? (
                <Loader2 size={15} className="animate-spin text-indigo-400" />
              ) : run.state === "success" ? (
                <CheckCircle2 size={15} className="text-emerald-400" />
              ) : (
                <XCircle size={15} className="text-rose-400" />
              )
            }
            actions={
              run.state === "queued" && (
                <button onClick={() => void cancelRequest()} className="text-xs text-zinc-500 hover:text-zinc-300">
                  Отменить запрос
                </button>
              )
            }
          >
            <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-zinc-500">
              {run.started_at && <span>начато {formatDateTime(run.started_at)}</span>}
              {run.finished_at && <span>завершено {formatDateTime(run.finished_at)}</span>}
              {run.requested_by && <span>запустил {run.requested_by}</span>}
              {run.from_version && <span>было {run.from_version.short}</span>}
              {run.to_version && run.state !== "running" && <span>стало {run.to_version.short}</span>}
            </div>
            {backendDown && runActive && (
              <div className="mb-3 flex items-center gap-2 rounded-xl border border-indigo-500/25 bg-indigo-500/10 px-4 py-2.5 text-sm text-indigo-200">
                <Loader2 size={14} className="animate-spin" />
                Sova перезапускается на новой версии — страница продолжит сама, ничего нажимать не нужно.
              </div>
            )}
            {run.state === "queued" && run.queued_stale && (
              <div className="mb-3 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-200">
                Служба обновления не забрала запрос больше минуты. Проверьте на сервере:{" "}
                <code>systemctl status oracle-t-update-web.path</code>
              </div>
            )}
            {run.state === "success" && run.result === "uptodate" && (
              <p className="mb-3 text-sm text-zinc-400">Ставить было нечего — уже установлена последняя версия.</p>
            )}
            {run.state === "failed" && (
              <p className="mb-3 text-sm text-rose-200">
                Развёртывание остановлено (код {run.code ?? "?"}). Резервная копия базы снята до изменений — команды
                отката напечатаны в конце журнала ниже. Подробности сборки — install.log в каталоге проекта.
              </p>
            )}
            {run.state === "interrupted" && (
              <p className="mb-3 text-sm text-rose-200">
                Служба обновления остановилась, не записав итог (перезагрузка сервера или принудительная остановка).
                Проверьте журнал ниже и при необходимости запустите обновление снова.
              </p>
            )}
            <ConsoleBlock text={run.log || (runActive ? "Ожидание вывода…" : "")} autoScroll={runActive} />
            {run.state === "success" && launchedHere && run.result === "updated" && (
              <button
                onClick={() => window.location.reload()}
                className="mt-4 inline-flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400"
              >
                <RefreshCw size={14} />
                Перезагрузить страницу — открыть новую версию
              </button>
            )}
          </Card>
        )}

        {/* Результат проверки GitHub */}
        {status?.can_check && (
          <Card title="Проверка GitHub" icon={<CloudDownload size={15} className="text-zinc-500" />}>
            {checking && !check && (
              <div className="flex items-center gap-2 text-sm text-zinc-400">
                <Loader2 size={14} className="animate-spin" /> Сверяюсь с GitHub…
              </div>
            )}
            {runActive && !check && <div className="text-sm text-zinc-500">Проверка — после окончания обновления.</div>}
            {check && (
              <>
                <div className="mb-4 text-xs text-zinc-500">проверено {formatDateTime(check.checked_at)}</div>

                {check.state === "up_to_date" && (
                  <div className="flex items-center gap-2 text-sm text-emerald-300">
                    <CheckCircle2 size={16} /> Установлена последняя версия.
                  </div>
                )}
                {check.state === "ahead" && (
                  <div className="text-sm text-zinc-300">
                    Установленная версия новее GitHub: здесь есть коммиты, ещё не отправленные в{" "}
                    <code>origin/{check.branch}</code>. Обновлять нечего.
                  </div>
                )}
                {check.state === "diverged" && (
                  <div className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
                    <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                    Версия на сервере разошлась с GitHub (на сервере есть свои коммиты). Обновление остановится —
                    разберитесь вручную на сервере.
                  </div>
                )}
                {check.state === "error" && (
                  <div className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                    <XCircle size={16} className="mt-0.5 shrink-0" />
                    {check.message}
                  </div>
                )}

                {(check.state === "available" || check.state === "diverged") && (
                  <>
                    <div className="grid gap-5 sm:grid-cols-2">
                      <Version commit={check.current} label="Установлена" />
                      <Version commit={check.remote} label="На GitHub" />
                    </div>

                    <div className="mt-5 flex flex-wrap gap-2 text-xs">
                      {check.commit_count != null && (
                        <span className="rounded-full bg-indigo-500/10 px-2.5 py-1 text-indigo-300 ring-1 ring-indigo-400/25">
                          {check.commit_count} {commitsWord(check.commit_count)}
                        </span>
                      )}
                      {check.shortstat && (
                        <span className="rounded-full bg-white/[0.04] px-2.5 py-1 text-zinc-400 ring-1 ring-white/10">
                          {check.shortstat}
                        </span>
                      )}
                      {!!check.migrations?.length && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2.5 py-1 text-amber-300 ring-1 ring-amber-400/25">
                          <Database size={12} /> миграции базы: {check.migrations.join(", ")}
                        </span>
                      )}
                      {check.dependencies?.python && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-white/[0.04] px-2.5 py-1 text-zinc-300 ring-1 ring-white/10">
                          <Package size={12} /> новые Python-зависимости
                        </span>
                      )}
                      {check.dependencies?.npm && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-white/[0.04] px-2.5 py-1 text-zinc-300 ring-1 ring-white/10">
                          <Package size={12} /> новые npm-зависимости
                        </span>
                      )}
                      {check.dependencies?.scripts && (
                        <span className="rounded-full bg-white/[0.04] px-2.5 py-1 text-zinc-300 ring-1 ring-white/10">
                          обновлены install.sh / update.sh
                        </span>
                      )}
                    </div>

                    {releases.length > 0 && (
                      <div className="mt-6">
                        <h3 className="mb-3 text-sm font-semibold text-zinc-200">Что нового</h3>
                        <ol className="space-y-5">
                          {releases.map((release) => (
                            <li key={`${release.date}-${release.title}`}>
                              <div className="text-xs text-zinc-500">{formatDay(release.date)}</div>
                              <div className="mt-0.5 text-sm font-medium text-zinc-100">{release.title}</div>
                              <ul className="mt-2 space-y-2">
                                {release.items.map((item, index) => (
                                  <li key={index} className="flex gap-3 text-sm leading-relaxed text-zinc-300">
                                    <span
                                      className={`mt-0.5 h-fit shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px] font-medium ring-1 ${KIND_STYLE[item.kind]}`}
                                    >
                                      {CHANGE_KIND_LABELS[item.kind]}
                                    </span>
                                    <span>{item.text}</span>
                                  </li>
                                ))}
                              </ul>
                            </li>
                          ))}
                        </ol>
                      </div>
                    )}

                    {commits.length > 0 && (
                      <div className="mt-6">
                        <button
                          onClick={() => setShowCommits((v) => !v)}
                          className="flex items-center gap-1.5 text-sm font-semibold text-zinc-200"
                        >
                          <ChevronDown size={15} className={`transition-transform ${showCommits ? "" : "-rotate-90"}`} />
                          Коммиты ({commits.length})
                        </button>
                        {showCommits && (
                          <ul className="mt-3 space-y-1.5">
                            {commits.map((commit) => (
                              <li key={commit.sha} className="flex gap-3 text-sm">
                                <code className="shrink-0 font-mono text-xs leading-5 text-zinc-500">{commit.short}</code>
                                <span className="min-w-0 flex-1 text-zinc-300">{commit.subject}</span>
                                <span className="shrink-0 text-xs leading-5 text-zinc-600">{formatDateTime(commit.date)}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </>
                )}

                <div className="mt-6">
                  <button
                    onClick={() => setShowConsole((v) => !v)}
                    className="flex items-center gap-1.5 text-sm font-semibold text-zinc-200"
                  >
                    <ChevronDown size={15} className={`transition-transform ${showConsole ? "" : "-rotate-90"}`} />
                    <Terminal size={14} className="text-zinc-500" />
                    Вывод update.sh --check
                  </button>
                  {showConsole && (
                    <div className="mt-3">
                      <ConsoleBlock text={check.console} maxHeight="max-h-72" />
                    </div>
                  )}
                </div>

                {/* Установка */}
                {status.can_install && (
                  <div className="mt-6 border-t border-white/[0.06] pt-5">
                    {confirmForce === null ? (
                      <div className="flex flex-wrap items-center gap-4">
                        {available && (
                          <button
                            onClick={() => setConfirmForce(false)}
                            disabled={!canInstall}
                            className="inline-flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                          >
                            <CloudDownload size={15} />
                            Установить {check.remote?.short}
                          </button>
                        )}
                        {(check.state === "up_to_date" || check.state === "ahead") && (
                          <button
                            onClick={() => setConfirmForce(true)}
                            disabled={!canInstall}
                            className="text-sm text-zinc-500 hover:text-zinc-300 disabled:opacity-50"
                          >
                            Развернуть текущую версию заново
                          </button>
                        )}
                      </div>
                    ) : (
                      <div className="rounded-xl border border-indigo-500/25 bg-indigo-500/[0.06] p-4">
                        <div className="text-sm font-medium text-zinc-100">
                          {confirmForce ? "Развернуть текущую версию заново?" : `Установить версию ${check.remote?.short}?`}
                        </div>
                        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zinc-400">
                          <li>снимется резервная копия базы (хранятся последние 10);</li>
                          <li>
                            {confirmForce ? "переустановятся" : "обновятся"} зависимости, применятся миграции, соберётся
                            интерфейс;
                          </li>
                          <li>
                            Sova перезапустится — у всех пользователей перерыв на десятки секунд, начатые разборы
                            продолжатся после старта;
                          </li>
                          <li>обычно это занимает 2–5 минут, ход виден на этой странице.</li>
                        </ul>
                        <div className="mt-4 flex gap-3">
                          <button
                            onClick={() => void startUpdate(confirmForce)}
                            disabled={starting}
                            className="inline-flex items-center gap-2 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
                          >
                            {starting && <Loader2 size={14} className="animate-spin" />}
                            {confirmForce ? "Да, развернуть" : "Да, обновить"}
                          </button>
                          <button
                            onClick={() => setConfirmForce(null)}
                            className="rounded-lg px-4 py-2 text-sm text-zinc-400 hover:text-zinc-200"
                          >
                            Отмена
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}
          </Card>
        )}

        {status && status.history.length > 0 && (
          <Card title="Журнал обновлений" icon={<Terminal size={15} className="text-zinc-500" />}>
            <ConsoleBlock text={status.history.join("\n")} maxHeight="max-h-60" />
          </Card>
        )}
      </div>
    </AppShell>
  );
}
