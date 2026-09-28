import { ScrollText } from "lucide-react";

import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { LogsSection } from "../components/settings/LogsSection";

/**
 * «Логирование» — журнал операций системы (раздел 5.9 ТЗ) отдельной страницей в меню
 * (28.09.2026). До этого был последним свёрнутым блоком «Настроек», и до него приходилось
 * долистывать мимо источников и профиля релевантности; теперь к нему же добавлена выгрузка
 * в файл. Открыт всем пользователям, как и раньше.
 */
export function LogsPage() {
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
        <LogsSection />
      </div>
    </AppShell>
  );
}
