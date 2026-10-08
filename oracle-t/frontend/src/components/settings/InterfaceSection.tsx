import { Moon } from "lucide-react";

import { useTheme } from "../../hooks/useTheme";
import { NavLayoutSection } from "./NavLayoutSection";
import { TendersViewSection } from "./TendersViewSection";
import { SettingCard, SettingsGroup, SettingsPanel, Toggle } from "./ui";

/** Вкладка «Интерфейс»: личные настройки вида — тема, расположение меню и вид списка тендеров. Хранятся в этом
 * браузере и на других пользователей не влияют. */
export function InterfaceSection() {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <SettingsPanel
      title="Интерфейс"
    >
      <SettingsGroup id="interface-theme" label="Оформление">
        <SettingCard
          id="interface-dark-theme"
          active={isDark}
          icon={<Moon size={18} />}
          title="Тёмная тема"
          description="Тот же переключатель есть в шапке приложения."
          control={
            <Toggle
              label="Тёмная тема"
              checked={isDark}
              onChange={(value) => setTheme(value ? "dark" : "light")}
            />
          }
        />
      </SettingsGroup>
      <NavLayoutSection />
      <TendersViewSection />
    </SettingsPanel>
  );
}
