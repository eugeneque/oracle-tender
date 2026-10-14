import { useState } from "react";
import { ScrollText } from "lucide-react";

import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { JobQueuesSection } from "../components/settings/JobQueuesSection";
import { LogsSection } from "../components/settings/LogsSection";
import { useAuth } from "../context/useAuth";

/**
 * «Логирование» — журнал операций системы (раздел 5.9 ТЗ) отдельной страницей в меню
 * (28.09.2026). До этого был последним свёрнутым блоком «Настроек», и до него приходилось
 * долистывать мимо источников и профиля релевантности; теперь к нему же добавлена выгрузка
 * в файл. Открыт всем пользователям, как и раньше.
 *
 * Вкладка «Ошибки ИИ» (14.10.2026) — отдельный журнал всего, что не получилось при
 * обращениях к моделям: сбои попыток, ответы не по схеме, отказы фильтра, исчерпанные
 * лимиты, итоговые отказы (компонент `ai_models`, `ai_log_service` на бэкенде).
 */
const TABS = [
  { key: "all", label: "Все операции" },
  { key: "ai", label: "Ошибки ИИ" },
] as const;

export function LogsPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("all");
  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-8 py-8">
        <PageHeader
          breadcrumb={["Sova", "Логирование"]}
          title="Логирование"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <ScrollText size={18} />
            </span>
          }
        />
        <p className="-mt-2 mb-6 text-sm text-zinc-500">
          Журнал операций: опрос источников, разбор документов, ИИ-анализ, действия
          пользователей. Обновляется автоматически, пока страница открыта.
        </p>
        <div
          role="tablist"
          aria-label="Журнал"
          className="mb-4 inline-flex rounded-lg border border-white/10 bg-black/20 p-0.5"
        >
          {TABS.map((item) => (
            <button
              key={item.key}
              role="tab"
              aria-selected={tab === item.key}
              onClick={() => setTab(item.key)}
              className={`rounded-md px-3 py-1.5 text-xs transition-colors ${
                tab === item.key ? "bg-white/10 text-zinc-100" : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
        {tab === "all" ? (
          <>
            {/* Очереди разборов всех пользователей — только администратору (29.09.2026). */}
            {user?.role === "admin" && <JobQueuesSection />}
            <LogsSection />
          </>
        ) : (
          <>
            <p className="mb-4 text-sm text-zinc-500">
              Всё, что не получилось при обращениях к моделям ИИ: сбои сети и таймауты, ответы
              не по схеме, отказы фильтра, исчерпанные лимиты и итоговые отказы после всех
              повторов. WARNING — попытка будет повторена или запрос уйдёт в запасную модель,
              ERROR — запрос не выполнен.
            </p>
            <LogsSection key="ai" fixedComponent="ai_models" />
          </>
        )}
      </div>
    </AppShell>
  );
}
