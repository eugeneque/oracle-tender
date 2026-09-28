import { Moon, Sun } from "lucide-react";

import { useTheme } from "../hooks/useTheme";
import { SegmentedControl } from "./ui/SegmentedControl";

/** Переключатель «Тёмная / Светлая» в шапке: два сегмента, подложка переезжает к активному. */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  return (
    <SegmentedControl
      size="sm"
      ariaLabel="Тема оформления"
      value={theme}
      onChange={setTheme}
      segments={[
        { value: "dark", label: "Тёмная", icon: <Moon size={13} />, title: "Тёмная тема" },
        { value: "light", label: "Светлая", icon: <Sun size={13} />, title: "Светлая тема" },
      ]}
    />
  );
}
