import { useState } from "react";

import type { CompanyProfile } from "../api/types";
import { AppShell } from "../components/AppShell";
import { CompanyParticipationsPanel } from "../components/settings/CompanyParticipationsSection";
import { CompanyProfilesPanel } from "../components/settings/CompanyProfileSection";
import { SegmentedControl } from "../components/ui/SegmentedControl";
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
 *
 * Компаний с 07.09.2026 несколько (ограничения на количество нет), и цифры считаются по
 * основной из них — её же профиль страница держит у себя и передаёт вкладке истории.
 * Остальные карточки пока только копят данные: раскладывать участие по юрлицам аналитика
 * научится позже, но заводить их имеет смысл уже сейчас.
 *
 * Редизайн 24.10.2026: шапка как у «Приложений» — крупный заголовок и переключатель вкладок
 * справа; плашка «основной профиль не заполнен» переехала в карточку самой компании.
 */

type Tab = "profile" | "participations";

const TABS: Array<{ key: Tab; label: string }> = [
  { key: "profile", label: "Компании" },
  { key: "participations", label: "История участий" },
];

export function CompanyPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("profile");
  const [profile, setProfile] = useState<CompanyProfile | null>(null);
  // Синхронизация основной компании с rusprofile пополняет историю участий; вкладка
  // перечитывает список через смену ключа, а не через общий стор ради одного события.
  const [participationsKey, setParticipationsKey] = useState(0);

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-6 py-8 sm:px-8">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <h1 className="text-4xl font-bold tracking-tight text-white">Моя компания</h1>
          <SegmentedControl<Tab>
            value={tab}
            onChange={setTab}
            size="sm"
            ariaLabel="Раздел компании"
            segments={TABS.map((item) => ({ value: item.key, label: item.label }))}
          />
        </header>

        {/* Обе вкладки монтируются сразу и прячутся стилем: список компаний здесь — источник
            признака «ИНН основной заполнен» для соседней вкладки, и перемонтирование при
            каждом переключении гоняло бы запрос списка впустую. */}
        <div className={`pb-16 ${tab === "profile" ? "" : "hidden"}`}>
          <CompanyProfilesPanel
            isAdmin={Boolean(isAdmin)}
            onPrimaryLoaded={setProfile}
            onRusprofileSynced={() => setParticipationsKey((k) => k + 1)}
          />
        </div>
        <div className={`pb-16 ${tab === "participations" ? "" : "hidden"}`}>
          <CompanyParticipationsPanel
            key={participationsKey}
            isAdmin={Boolean(isAdmin)}
            hasInn={Boolean(profile?.has_inn)}
          />
        </div>
      </div>
    </AppShell>
  );
}
