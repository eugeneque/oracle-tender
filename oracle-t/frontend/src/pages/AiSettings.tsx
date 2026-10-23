import { useSearchParams } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import { BrainCircuit, Receipt } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { AppShell } from "../components/AppShell";
import { AiIntegrationSection } from "../components/settings/AiIntegrationSection";
import { AiUsageSection } from "../components/settings/AiUsageSection";
import { SPRING_SNAPPY } from "../utils/motion";

/**
 * «Модели ИИ» — отдельный раздел меню (23.10.2026, выделен из «Интеграций», которые стали
 * «Приложениями»). ИИ — не внешнее приложение среди прочих, а двигатель всей системы: ключи
 * моделей, окна контекста и расход токенов смотрят чаще и другими глазами, чем Bitrix24 или
 * почту, и в сетке карточек приложений им не место.
 *
 * Только администратору (маршрут закрыт в App.tsx). Вкладка живёт в адресе `?tab=`, как
 * раньше на «Интеграциях»: старые ссылки `/integrations?tab=usage` переводятся сюда.
 */

type TabKey = "models" | "usage";

const TABS: { key: TabKey; label: string; icon: LucideIcon }[] = [
  { key: "models", label: "Модели и ключи", icon: BrainCircuit },
  // Расход токенов ИИ (14.10.2026): сколько тратит Сова и на что — для прогноза бюджета.
  { key: "usage", label: "Детализация расхода", icon: Receipt },
];

export function AiSettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab: TabKey = searchParams.get("tab") === "usage" ? "usage" : "models";

  const selectTab = (key: TabKey) => {
    setSearchParams(key === "models" ? {} : { tab: key }, { replace: true });
  };

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl px-6 py-8 sm:px-8">
        <header className="mb-8">
          <h1 className="text-4xl font-bold tracking-tight text-white">Модели ИИ</h1>
        </header>

        <nav
          role="tablist"
          aria-label="Разделы ИИ"
          className="mb-8 flex gap-1 overflow-x-auto border-b border-white/[0.08]"
        >
          {TABS.map((tab) => {
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
                    layoutId="ai-settings-tab-underline"
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
            {activeTab === "models" ? <AiIntegrationSection /> : <AiUsageSection />}
          </motion.section>
        </AnimatePresence>
      </div>
    </AppShell>
  );
}
