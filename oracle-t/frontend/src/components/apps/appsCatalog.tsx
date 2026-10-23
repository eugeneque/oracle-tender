import type { ReactNode } from "react";
import { Mail } from "lucide-react";

import { api } from "../../api/client";
import type { Bitrix24Settings, NotificationSettings, RusprofileSettings } from "../../api/types";
import { BitrixSection } from "../settings/BitrixSection";
import { NotificationsSection } from "../settings/NotificationsSection";
import { RusprofileSection } from "../settings/RusprofileSection";
import { RusprofileMark } from "./RusprofileMark";

/**
 * Каталог «Приложений» (23.10.2026) — внешние сервисы, с которыми работает Сова. Одна запись —
 * одна карточка в сетке и одна страница настройки `/apps/<key>`. Новая интеграция добавляется
 * сюда же: логотип, описание, загрузчик состояния и блок настроек — страницы сами подхватят.
 *
 * Состояние подключения читается теми же эндпоинтами, что и блоки настроек, а они закрыты
 * для всех, кроме администратора, — поэтому `loadStatus` вызывается только у администратора;
 * остальные видят карточку без отметки.
 */

export type AppStatus = "connected" | "error" | "off";

export interface AppStatusInfo {
  status: AppStatus;
  /** Короткая подпись под описанием: портал, ящик, логин, итог последней проверки. */
  detail?: string | null;
}

export interface AppDef {
  key: string;
  name: string;
  category: string;
  description: string;
  logo: ReactNode;
  adminOnly?: boolean;
  loadStatus?: () => Promise<AppStatusInfo>;
  render: (ctx: { isAdmin: boolean }) => ReactNode;
}

/** Плитка логотипа: фирменный цвет сервиса и монограмма или знак, если есть его SVG. */
function LogoTile({ className, children }: { className: string; children: ReactNode }) {
  return (
    <span
      className={`flex h-full w-full items-center justify-center rounded-[inherit] font-bold tracking-tight text-white ${className}`}
    >
      {children}
    </span>
  );
}

export const APPS: AppDef[] = [
  {
    key: "bitrix24",
    name: "Bitrix24",
    category: "CRM",
    description: "Сделки по тендерам в воронку портала, разбор ИИ в карточку сделки, лиды и ключи API",
    logo: <LogoTile className="bg-[#2FC6F6] text-[0.95em]">24</LogoTile>,
    loadStatus: async () => {
      const s = await api.get<Bitrix24Settings>("/integrations/bitrix24");
      if (!s.is_configured) return { status: "off" };
      if (s.last_check_status === "error") return { status: "error", detail: s.last_check_message };
      return { status: "connected", detail: s.portal };
    },
    render: ({ isAdmin }) => <BitrixSection isAdmin={isAdmin} embedded />,
  },
  {
    key: "mail",
    name: "Почта и уведомления",
    category: "Рассылка",
    description: "Письма о новых релевантных закупках, сроках подачи и ошибках; журнал отправок",
    logo: (
      <LogoTile className="bg-indigo-500">
        <Mail size="1em" strokeWidth={2.25} />
      </LogoTile>
    ),
    loadStatus: async () => {
      const s = await api.get<NotificationSettings>("/notifications/settings");
      if (!s.is_configured) return { status: "off" };
      if (!s.is_enabled) return { status: "off", detail: "Рассылка выключена" };
      return { status: "connected", detail: s.from_address ?? s.smtp_username };
    },
    render: ({ isAdmin }) => <NotificationsSection isAdmin={isAdmin} embedded />,
  },
  {
    key: "rusprofile",
    name: "Rusprofile",
    category: "Данные о компаниях",
    description: "Реквизиты, лицензии, проекты и история участий для раздела «Моя компания»",
    logo: (
      <LogoTile className="bg-white">
        <RusprofileMark className="h-[70%] w-[70%]" />
      </LogoTile>
    ),
    adminOnly: true,
    loadStatus: async () => {
      const s = await api.get<RusprofileSettings>("/integrations/rusprofile");
      if (!s.is_configured) return { status: "off" };
      if (s.last_sync_status === "error") return { status: "error", detail: s.last_sync_message };
      return { status: "connected", detail: s.login };
    },
    render: () => <RusprofileSection embedded />,
  },
];

export function visibleApps(isAdmin: boolean): AppDef[] {
  return APPS.filter((app) => isAdmin || !app.adminOnly);
}

export const APP_STATUS_LABELS: Record<AppStatus, string> = {
  connected: "Подключено",
  error: "Ошибка",
  off: "Не настроено",
};
