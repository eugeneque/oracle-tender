import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Circle,
  Compass,
  Database,
  ShieldCheck,
  Users as UsersIcon,
  Wifi,
} from "lucide-react";

import { api } from "../api/client";
import { CATALOG_SOURCE_TYPES, TENDER_SOURCES_TOTAL } from "../api/types";
import type { Source, TenderStats, User } from "../api/types";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../context/useAuth";

// Дорожная карта раздела 9 ТЗ. Список правится вручную по мере закрытия этапов — вывести
// его из данных нельзя: «этап сдан» — это решение человека, а не следствие того, что в базе
// появились какие-то строки.
const ROADMAP = [
  { stage: "Этап 0", title: "Инфраструктура и окружение", done: true },
  { stage: "Этап 1", title: "Аутентификация и пользователи", done: true },
  { stage: "Этап 2", title: "Сбор тендеров: адаптеры источников, планировщик", done: true },
  { stage: "Этап 3", title: "Загрузка и парсинг документов", done: true },
  { stage: "Этап 4", title: "Справочник продукции: ФГИС + сайты производителей", done: true },
  { stage: "Этап 5", title: "ИИ-модуль анализа требований тендера", done: true },
  { stage: "Этап 6", title: "Матрица соответствия и процент победителя", done: true },
  { stage: "Этап 7", title: "Веб-интерфейс: список и карточка тендера", done: true },
  { stage: "Этап 8", title: "Веб-интерфейс: аналитика и настройки", done: true },
  { stage: "Этап 9", title: "Экспорт в Excel", done: true },
  { stage: "Этап 10", title: "Уведомления по почте", done: true },
  { stage: "Этап 11", title: "Логирование и отказоустойчивость", done: true },
  { stage: "Этап 12", title: "Расширение источников тендеров", done: false },
  { stage: "Этап 13", title: "Подготовка к Bitrix24", done: true },
  { stage: "Этап 14", title: "Развёртывание на сервере и документация", done: false },
];

const STAGES_DONE = ROADMAP.filter((item) => item.done).length;

function StatCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-5">
      <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
        {icon}
      </div>
      <div className="text-2xl font-semibold text-white">{value}</div>
      <div className="mt-0.5 text-sm text-zinc-500">{label}</div>
      {hint && <div className="mt-2 text-xs text-zinc-600">{hint}</div>}
    </div>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const [activeUserCount, setActiveUserCount] = useState<number | null>(null);
  const [tenderTotal, setTenderTotal] = useState<number | null>(null);
  const [availableSources, setAvailableSources] = useState<number | null>(null);
  const [tenderSourceTotal, setTenderSourceTotal] = useState<number>(TENDER_SOURCES_TOTAL);

  useEffect(() => {
    if (user?.role !== "admin") return;
    api
      .get<User[]>("/users")
      .then((users) => setActiveUserCount(users.filter((u) => u.is_active).length))
      .catch(() => setActiveUserCount(null));
  }, [user?.role]);

  useEffect(() => {
    api
      .get<TenderStats>("/tenders/stats")
      .then((stats) => setTenderTotal(stats.total))
      .catch(() => setTenderTotal(null));
    api
      .get<Source[]>("/sources")
      .then((sources) => {
        // Считаем только площадки закупок: `/sources` отдаёт одним списком и источники
        // справочника продукции (ФГИС, сайты производителей), а плитка говорит про 12
        // площадок раздела 4.1 ТЗ. Без этого фильтра выходило «24 из 12».
        const tenderSources = sources.filter((s) => !CATALOG_SOURCE_TYPES.includes(s.type));
        setAvailableSources(
          tenderSources.filter((s) => s.availability_status === "available").length,
        );
        setTenderSourceTotal(tenderSources.length);
      })
      .catch(() => setAvailableSources(null));
  }, []);

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-8 py-8">
        <PageHeader
          breadcrumb={["ORACLE-T", "Дашборд"]}
          title={`Добро пожаловать, ${user?.full_name ?? ""}`}
        />

        <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            icon={<ShieldCheck size={18} />}
            label="Ваша роль"
            value={user?.role === "admin" ? "Администратор" : "Пользователь"}
          />
          {user?.role === "admin" && (
            <StatCard
              icon={<UsersIcon size={18} />}
              label="Активных пользователей"
              value={activeUserCount === null ? "…" : String(activeUserCount)}
            />
          )}
          <StatCard
            icon={<Database size={18} />}
            label="Тендеров собрано"
            value={tenderTotal === null ? "…" : String(tenderTotal)}
            hint="ЕИС, по расписанию 2 раза в день"
          />
          <StatCard
            icon={<Wifi size={18} />}
            label="Источников доступно сейчас"
            value={availableSources === null ? "…" : `${availableSources} из ${tenderSourceTotal}`}
            hint="Проверка — раз в минуту, см. Настройки"
          />
          <StatCard
            icon={<Compass size={18} />}
            label="Текущий этап разработки"
            value={`${STAGES_DONE} из ${ROADMAP.length}`}
            hint="Остались расширение источников (9 из 12 подключены) и развёртывание на сервере"
          />
        </div>

        <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-6">
          <h2 className="mb-1 text-base font-semibold text-white">
            Дорожная карта платформы
          </h2>
          <p className="mb-5 text-sm text-zinc-500">
            Тендеры собираются с девяти площадок вместе с документацией, работают ИИ-анализ
            требований, матрица соответствия, аналитика, выгрузки и почтовые уведомления.
            Впереди — оставшиеся три площадки (закрыты внешне) и развёртывание на сервере.
          </p>
          <ul className="space-y-3">
            {ROADMAP.map((item) => (
              <li key={item.stage} className="flex items-center gap-3 text-sm">
                {item.done ? (
                  <CheckCircle2 size={17} className="shrink-0 text-emerald-400" />
                ) : (
                  <Circle size={17} className="shrink-0 text-zinc-700" />
                )}
                <span className={item.done ? "text-zinc-300" : "text-zinc-500"}>
                  <span className="font-medium">{item.stage}.</span> {item.title}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </AppShell>
  );
}
