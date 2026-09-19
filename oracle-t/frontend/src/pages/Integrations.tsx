import { Plug } from "lucide-react";

import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { AiIntegrationSection } from "../components/settings/AiIntegrationSection";
import { BitrixSection } from "../components/settings/BitrixSection";
import { NotificationsSection } from "../components/settings/NotificationsSection";
import { RusprofileSection } from "../components/settings/RusprofileSection";
import { useAuth } from "../context/useAuth";

/**
 * «Интеграции» — внешние подключения системы в одном месте (выделены из «Настроек»
 * 18.09.2026, когда провайдеров ИИ стало два и раздел перестал помещаться среди источников).
 *
 * Четыре блока: модели ИИ (ключи и переключатель — только администратор), Rusprofile
 * (учётная запись для заполнения «Моей компании» с сайта — только администратор), почта
 * (журнал отправок — всем, ящик и рассылка — администратору) и Bitrix24 (выгрузка лидов —
 * всем, ключи API — администратору). Разграничение по ролям осталось тем же, что было в
 * «Настройках»: страница открыта всем, а закрытые части скрыты внутри блоков.
 */
export function IntegrationsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-8 py-8">
        <PageHeader
          breadcrumb={["Sova Scanner", "Интеграции"]}
          title="Интеграции"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <Plug size={18} />
            </span>
          }
        />

        <div className="space-y-6">
          {isAdmin && <AiIntegrationSection />}
          {isAdmin && <RusprofileSection />}
          <NotificationsSection isAdmin={isAdmin} />
          <BitrixSection isAdmin={isAdmin} />
        </div>
      </div>
    </AppShell>
  );
}
