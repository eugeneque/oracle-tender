import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { BentoRecap } from "../components/home/BentoRecap";
import { MiraSpotlight } from "../components/home/MiraSpotlight";
import { ModelUpdates } from "../components/home/ModelUpdates";
import { markWhatsNewSeen } from "../components/nav/WhatsNew";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { ONBOARDING, WHATS_NEW } from "../content/homeSlides";
import { useAuth } from "../context/useAuth";

// Страница «Что нового?» (28.09.2026 заменила дашборд): нововведения мозаикой в духе итогов
// Apple-кейноута и гайд для нового пользователя на том же месте. С того же дня первой
// открываются тендеры, а сюда ведёт блок «Что нового?» в меню (`?tab=news`). Без параметра
// страница открывается на последней выбранной вкладке, впервые — на гайде. Вкладка «Модель ИИ»
// (14.10.2026) — интерактивное «было / стало» ИИ-разбора, см. components/home/ModelUpdates.tsx.

type Tab = "news" | "model" | "guide";
const TABS: Tab[] = ["news", "model", "guide"];
const isTab = (value: string | null): value is Tab => TABS.includes(value as Tab);

const TAB_KEY = "home.tab";

// Раскладки мозаики на широком экране: четыре колонки, `hero` — центральная плитка.
// Каждый `id` из homeSlides должен встретиться здесь ровно одной прямоугольной областью.
// С 24.10.2026 у нововведений центральной плитки нет: главное — Мира, большим блоком над
// мозаикой (`MiraSpotlight`), поэтому её `id` в раскладке не встречается.
const NEWS_AREAS = [
  "tender-list tender-list sidebar palette",
  "bitrix-deal bitrix-deal apps ai-models",
  "positions verdicts matrix matrix",
  "pp1875 qwen activity activity",
  "catalog-sources catalog-sources deadline search",
  "selection selection roseltorg sberbank",
];
const SPOTLIGHT_ID = "mira";

const GUIDE_AREAS = [
  "tenders hero hero tender-card",
  "tenders hero hero tender-card",
  "manual catalog catalog company",
  "analytics settings settings done",
];

function readTab(param: string | null): Tab {
  if (isTab(param)) return param;
  try {
    const stored = localStorage.getItem(TAB_KEY);
    return isTab(stored) ? stored : "guide";
  } catch {
    return "guide";
  }
}

export function HomePage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const [tab, setTab] = useState<Tab>(() => readTab(tabParam));

  // Повторный клик по «Что нового?», когда страница уже открыта на гайде.
  useEffect(() => {
    if (isTab(tabParam)) setTab(tabParam);
  }, [tabParam]);

  useEffect(() => {
    if (tab !== "guide") markWhatsNewSeen();
  }, [tab]);

  const switchTab = (next: Tab) => {
    setTab(next);
    // Параметр снимается, чтобы следующий клик по «Что нового?» снова сработал.
    if (tabParam) setSearchParams({}, { replace: true });
    try {
      localStorage.setItem(TAB_KEY, next);
    } catch {
      // без сохранения — вкладка просто не запомнится
    }
  };

  const news = useMemo(() => WHATS_NEW.filter((slide) => !slide.adminOnly || isAdmin), [isAdmin]);
  const spotlight = news.find((slide) => slide.id === SPOTLIGHT_ID);
  const tiles = news.filter((slide) => slide.id !== SPOTLIGHT_ID);
  const [welcome, ...guide] = ONBOARDING;

  const firstName = user?.full_name?.split(" ")[0];

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-8 py-10">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="text-sm text-zinc-500">
              {tab === "news"
                ? `Октябрь 2026 · ${news.length} нововведений`
                : firstName
                  ? `${firstName}, добро пожаловать в Sova`
                  : "Sova"}
            </div>
            <h1 className="mt-1 text-5xl font-semibold tracking-tight text-zinc-50">
              {tab === "news" ? "Что нового?" : tab === "model" ? "Обновления модели" : "С чего начать"}
            </h1>
          </div>
          <SegmentedControl
            shape="pill"
            ariaLabel="Раздел страницы"
            value={tab}
            onChange={switchTab}
            segments={[
              { value: "news", label: "Что нового" },
              { value: "model", label: "Модель ИИ" },
              { value: "guide", label: "С чего начать" },
            ]}
          />
        </div>

        {tab === "model" ? (
          <ModelUpdates />
        ) : tab === "news" ? (
          <>
            {spotlight && <MiraSpotlight slide={spotlight} />}
            <BentoRecap key="news" slides={tiles} areas={NEWS_AREAS} />
          </>
        ) : (
          <BentoRecap
            key="guide"
            hero={{ title: "Добро пожаловать", subtitle: `${guide.length} шагов по разделам · нажмите, чтобы начать` }}
            heroSlide={welcome}
            slides={guide}
            areas={GUIDE_AREAS}
          />
        )}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-zinc-600">
          <p>{tab === "model" ? "Примеры закупок и проценты производителей — иллюстрация; сводные цифры — из сверок на реальных данных." : "Нажмите на плитку, чтобы прочитать подробности."}</p>
          {tab !== "guide" && (
            <Link to="/changelog" className="text-indigo-400 hover:text-indigo-300">
              Вся история обновлений →
            </Link>
          )}
        </div>
      </div>
    </AppShell>
  );
}
