import { useState } from "react";
import { Building2 } from "lucide-react";

import type { CompanyProfile } from "../api/types";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { CompanyParticipationsPanel } from "../components/settings/CompanyParticipationsSection";
import { CompanyProfileForm } from "../components/settings/CompanyProfileSection";
import { useAuth } from "../context/useAuth";

/**
 * «Моя компания» — отдельный раздел приложения (раздел 5.6 ТЗ).
 *
 * Вынесен из настроек сознательно: настройки — это то, что задают один раз и почти не
 * открывают, а профиль и история участий питают главную метрику каждого тендера и
 * пополняются регулярно. Прятать их за «Настройками» значило бы прятать источник цифр,
 * которые люди видят в списке закупок каждый день.
 *
 * Две вкладки — про один и тот же субъект: профиль отвечает «что мы за компания» (допуски,
 * стаж, реквизиты) и питает измерения «Задача» и «Компетенции», история участий отвечает
 * «как мы выступали раньше» и питает «Историю». Профиль грузится на уровне страницы, чтобы
 * вкладка истории знала, заполнен ли ИНН, и не показывала кнопку выгрузки, которой не с чем
 * работать.
 */

type Tab = "profile" | "participations";

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "profile", label: "Профиль компании" },
  { key: "participations", label: "История участий" },
];

export function CompanyPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("profile");
  const [profile, setProfile] = useState<CompanyProfile | null>(null);

  return (
    <AppShell>
      <div className="mx-auto max-w-7xl px-8 py-8">
        <PageHeader
          breadcrumb={["ORACLE-T", "Моя компания"]}
          title="Моя компания"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <Building2 size={18} />
            </span>
          }
          actions={
            profile && !profile.is_filled ? (
              <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] text-amber-300">
                профиль не заполнен
              </span>
            ) : undefined
          }
        />

        <p className="-mt-4 mb-6 max-w-3xl text-xs leading-relaxed text-zinc-500">
          Юридические данные, допуски и реальная история участия в закупках. На этих данных
          держатся все три измерения AI-оценки (раздел 5.5.1 ТЗ): без профиля не считаются
          «Задача» и «Компетенции», без истории участий — «История».
        </p>

        <div className="mb-6 flex w-full max-w-md gap-1 rounded-lg border border-white/[0.08] bg-black/20 p-1">
          {TABS.map((item) => (
            <button
              key={item.key}
              onClick={() => setTab(item.key)}
              className={`flex-1 rounded-md px-3 py-1.5 text-xs transition-colors ${
                tab === item.key
                  ? "bg-white/[0.07] text-zinc-100"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        {/* Обе вкладки монтируются сразу и прячутся стилем: профиль здесь — источник
            признака «ИНН заполнен» для соседней вкладки, и перемонтирование при каждом
            переключении гоняло бы запрос профиля впустую. */}
        <div className={tab === "profile" ? "" : "hidden"}>
          <CompanyProfileForm isAdmin={Boolean(isAdmin)} onProfileLoaded={setProfile} />
        </div>
        <div className={tab === "participations" ? "" : "hidden"}>
          <CompanyParticipationsPanel
            isAdmin={Boolean(isAdmin)}
            hasInn={Boolean(profile?.has_inn)}
          />
        </div>
      </div>
    </AppShell>
  );
}
