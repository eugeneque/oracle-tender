import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import { ArrowLeft, ChevronRight } from "lucide-react";
import { motion } from "motion/react";

import { AppShell } from "../components/AppShell";
import type {
  AppDef,
  AppStatus,
  AppStatusInfo,
} from "../components/apps/appsCatalog";
import { APP_STATUS_EVENT } from "../components/apps/AppKit";
import { APP_STATUS_LABELS, visibleApps } from "../components/apps/appsCatalog";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { useAuth } from "../context/useAuth";

/**
 * «Приложения» (23.10.2026, бывшие «Интеграции») — сетка карточек внешних сервисов по образцу
 * каталогов интеграций: логотип, назначение, состояние подключения. Нажатие открывает страницу
 * настройки сервиса `/apps/<key>`. Вкладки прежней страницы не масштабировались: каждая новая
 * интеграция удлиняла ряд, а состояние подключения не было видно, пока вкладку не откроешь.
 *
 * Модели ИИ и детализация расхода отсюда уехали в отдельный раздел «Модели ИИ» (`/ai`).
 */

type StatusMap = Record<string, AppStatusInfo | undefined>;

/** Состояние подключения всех приложений — только администратору (эндпоинты закрыты ролью).
 * Ошибка загрузки одного приложения не мешает остальным: его карточка просто без отметки. */
function useAppStatuses(apps: AppDef[], enabled: boolean): StatusMap {
  const [statuses, setStatuses] = useState<StatusMap>({});
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    for (const app of apps) {
      if (!app.loadStatus) continue;
      app
        .loadStatus()
        .then(
          (info) =>
            alive && setStatuses((prev) => ({ ...prev, [app.key]: info })),
        )
        .catch(() => undefined);
    }
    return () => {
      alive = false;
    };
    // Список приложений статичен — пересчёт нужен только при смене роли.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);
  return statuses;
}

const STATUS_PILL: Record<AppStatus, string> = {
  connected: "border-indigo-400/40 bg-indigo-500/10 text-indigo-300",
  paused: "border-white/[0.12] text-zinc-300",
  error: "border-rose-400/40 bg-rose-500/10 text-rose-300",
  off: "border-white/[0.12] text-zinc-400",
};

function StatusPill({ info }: { info: AppStatusInfo | undefined }) {
  if (!info) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium ${STATUS_PILL[info.status]}`}
    >
      {info.status !== "off" && (
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            info.status === "connected"
              ? "bg-indigo-400"
              : info.status === "paused"
                ? "bg-amber-400"
                : "bg-rose-400"
          }`}
        />
      )}
      {info.status === "off" ? "Подключить" : APP_STATUS_LABELS[info.status]}
    </span>
  );
}

function AppLogo({ app, size }: { app: AppDef; size: "md" | "lg" }) {
  return (
    <span
      className={`block shrink-0 overflow-hidden shadow-sm ring-1 ring-black/5 ${
        size === "lg"
          ? "h-14 w-14 rounded-2xl text-2xl"
          : "h-11 w-11 rounded-xl text-lg"
      }`}
    >
      {app.logo}
    </span>
  );
}

type Filter = "all" | "connected" | "off";

export function AppsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const apps = visibleApps(isAdmin);
  const statuses = useAppStatuses(apps, isAdmin);
  const [filter, setFilter] = useState<Filter>("all");

  const shown = apps.filter((app) => {
    if (filter === "all") return true;
    const status = statuses[app.key]?.status;
    // «Выключено» — подключено, просто на паузе: в списке подключённых.
    const linked = status === "connected" || status === "paused";
    return filter === "connected" ? linked : status !== undefined && !linked;
  });

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-8 sm:px-8">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <h1 className="text-4xl font-bold tracking-tight text-white">
            Приложения
          </h1>
          {isAdmin && (
            <SegmentedControl<Filter>
              value={filter}
              onChange={setFilter}
              size="sm"
              ariaLabel="Фильтр приложений"
              segments={[
                { value: "all", label: "Все" },
                { value: "connected", label: "Подключены" },
                { value: "off", label: "Не подключены" },
              ]}
            />
          )}
        </header>

        {shown.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-white/[0.1] px-6 py-12 text-center text-sm text-zinc-500">
            {filter === "connected"
              ? "Ни одно приложение пока не подключено"
              : "Все приложения подключены"}
          </p>
        ) : (
          <div className="grid gap-4 pb-16 sm:grid-cols-2 lg:grid-cols-3">
            {shown.map((app, index) => {
              const info = statuses[app.key];
              return (
                <motion.div
                  key={app.key}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2, delay: index * 0.03 }}
                >
                  <Link
                    to={`/apps/${app.key}`}
                    className="group flex h-full flex-col rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5 transition-all hover:-translate-y-0.5 hover:border-white/[0.14] hover:bg-white/[0.05] hover:shadow-lg hover:shadow-black/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/60"
                  >
                    <div className="mb-5 flex items-start justify-between gap-3">
                      <AppLogo app={app} size="md" />
                      {isAdmin ? (
                        <StatusPill info={info} />
                      ) : (
                        <ChevronRight
                          size={18}
                          className="text-zinc-600 transition-transform group-hover:translate-x-0.5 group-hover:text-zinc-400"
                        />
                      )}
                    </div>
                    <div className="mb-1.5 flex items-baseline gap-2">
                      <h2 className="text-[15px] font-semibold text-zinc-100">
                        {app.name}
                      </h2>
                      <span className="text-xs text-zinc-500">
                        {app.category}
                      </span>
                    </div>
                    <p className="text-sm leading-relaxed text-zinc-400">
                      {app.description}
                    </p>
                    {info?.detail && (
                      <p
                        className={`mt-auto truncate pt-4 text-xs ${info.status === "error" ? "text-rose-300/80" : "text-zinc-500"}`}
                        title={info.detail}
                      >
                        {info.detail}
                      </p>
                    )}
                  </Link>
                </motion.div>
              );
            })}
          </div>
        )}
      </div>
    </AppShell>
  );
}

/** Страница настройки одного приложения: шапка с логотипом и состоянием, ниже — его блок. */
export function AppDetailPage() {
  const { appKey } = useParams();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const app = visibleApps(isAdmin).find((item) => item.key === appKey);
  const [info, setInfo] = useState<AppStatusInfo | undefined>();

  useEffect(() => {
    setInfo(undefined);
    if (!isAdmin || !app?.loadStatus) return;
    let alive = true;
    const refresh = () =>
      app
        .loadStatus?.()
        .then((next) => alive && setInfo(next))
        .catch(() => undefined);
    void refresh();
    window.addEventListener(APP_STATUS_EVENT, refresh);
    return () => {
      alive = false;
      window.removeEventListener(APP_STATUS_EVENT, refresh);
    };
  }, [app, isAdmin]);

  if (!app) return <Navigate to="/apps" replace />;

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-6 py-8 sm:px-8">
        <Link
          to="/apps"
          className="mb-6 inline-flex items-center gap-1.5 text-sm text-zinc-500 transition-colors hover:text-zinc-200"
        >
          <ArrowLeft size={15} />
          Приложения
        </Link>

        <header className="mb-8 flex flex-wrap items-center gap-4">
          <AppLogo app={app} size="lg" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-3xl font-bold tracking-tight text-white">
                {app.name}
              </h1>
              {info && (
                <span
                  className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium ${STATUS_PILL[info.status]}`}
                >
                  {APP_STATUS_LABELS[info.status]}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-zinc-400">{app.description}</p>
          </div>
        </header>

        <div className="pb-16">{app.render({ isAdmin })}</div>
      </div>
    </AppShell>
  );
}

/** Старые адреса `/integrations?tab=…` (закладки, плитки «Что нового?», пересланные ссылки)
 * ведут на новые места: модели и детализация — в «Модели ИИ», остальное — в приложение. */
const LEGACY_TABS: Record<string, string> = {
  ai: "/ai",
  usage: "/ai?tab=usage",
  mail: "/apps/mail",
  bitrix: "/apps/bitrix24",
  rusprofile: "/apps/rusprofile",
};

export function LegacyIntegrationsRedirect() {
  const { search } = useLocation();
  const { user } = useAuth();
  const tab = new URLSearchParams(search).get("tab");
  const fallback = user?.role === "admin" ? "/ai" : "/apps";
  return <Navigate to={(tab && LEGACY_TABS[tab]) || fallback} replace />;
}
