import { useSearchParams } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { Bell, BrainCircuit, Globe, Plug2 } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { AppShell } from "../components/AppShell";
import { AiIntegrationSection } from "../components/settings/AiIntegrationSection";
import { BitrixSection } from "../components/settings/BitrixSection";
import { NotificationsSection } from "../components/settings/NotificationsSection";
import { RusprofileSection } from "../components/settings/RusprofileSection";
import { useAuth } from "../context/useAuth";
import { SPRING_SNAPPY } from "../utils/motion";

/**
 * «Интеграции» — внешние подключения системы в одном месте (выделены из «Настроек»
 * 18.09.2026, когда провайдеров ИИ стало два и раздел перестал помещаться среди источников).
 *
 * Редизайн 08.10.2026: вкладки вместо ленты сворачиваемых блоков, как в «Настройках». Блоков
 * четыре, у ИИ — уже четыре модели, и страница уходила на несколько экранов вниз, хотя в
 * каждый момент нужен один блок. Выбранная вкладка живёт в адресе `?tab=`. Страница шире
 * «Настроек» — раздел ИИ разложен в две колонки (список моделей и карточка ключей).
 *
 * Разграничение по ролям прежнее: модели ИИ и Rusprofile — только администратору, почта
 * (журнал — всем, ящик и рассылка — администратору) и Bitrix24 (выгрузка — всем, ключи API
 * — администратору) открыты всем, закрытые части скрыты внутри блоков.
 *
 * 08.10.2026: пояснения под заголовком и вкладками убраны — интерфейс перегружали.
 */

type TabKey = "ai" | "mail" | "bitrix" | "rusprofile";

interface TabDef {
  key: TabKey;
  label: string;
  icon: LucideIcon;
  adminOnly?: boolean;
}

const TABS: TabDef[] = [
  {
    key: "ai",
    label: "Модели ИИ",
    icon: BrainCircuit,
    adminOnly: true,
  },
  {
    key: "mail",
    label: "Почта и уведомления",
    icon: Bell,
  },
  {
    key: "bitrix",
    label: "Bitrix24",
    icon: Plug2,
  },
  {
    key: "rusprofile",
    label: "Rusprofile",
    icon: Globe,
    adminOnly: true,
  },
];

export function IntegrationsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [searchParams, setSearchParams] = useSearchParams();

  const tabs = TABS.filter((tab) => isAdmin || !tab.adminOnly);
  const fallback: TabKey = tabs[0]?.key ?? "mail";
  const requested = searchParams.get("tab");
  const current = tabs.find((tab) => tab.key === requested) ?? tabs[0];
  const activeTab: TabKey = current?.key ?? fallback;

  const selectTab = (key: TabKey) => {
    setSearchParams(key === fallback ? {} : { tab: key }, { replace: true });
  };

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl px-6 py-8 sm:px-8">
        <header className="mb-8">
          <h1 className="text-4xl font-bold tracking-tight text-white">Интеграции</h1>
        </header>

        <nav
          role="tablist"
          aria-label="Разделы интеграций"
          className="mb-8 flex gap-1 overflow-x-auto border-b border-white/[0.08]"
        >
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = tab.key === activeTab;
            return (
              <button
                key={tab.key}
                role="tab"
                aria-selected={isActive}
                onClick={() => selectTab(tab.key)}
                className={`relative flex shrink-0 items-center gap-2 px-3.5 py-3.5 text-sm font-medium transition-colors ${
                  isActive ? "text-white" : "text-zinc-500 hover:text-zinc-200"
                }`}
              >
                <Icon size={15} />
                {tab.label}
                {isActive && (
                  <motion.span
                    layoutId="integrations-tab-underline"
                    transition={SPRING_SNAPPY}
                    className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-indigo-400"
                  />
                )}
              </button>
            );
          })}
        </nav>

        <AnimatePresence mode="wait">
          <motion.section
            key={activeTab}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18 }}
            className="pb-16"
          >
            {activeTab === "ai" && isAdmin && <AiIntegrationSection />}
            {activeTab === "mail" && <NotificationsSection isAdmin={isAdmin} embedded />}
            {activeTab === "bitrix" && <BitrixSection isAdmin={isAdmin} embedded />}
            {activeTab === "rusprofile" && isAdmin && <RusprofileSection embedded />}
          </motion.section>
        </AnimatePresence>
      </div>
    </AppShell>
  );
}
