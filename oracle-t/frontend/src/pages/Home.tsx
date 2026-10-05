import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { AppShell } from "../components/AppShell";
import { BentoRecap } from "../components/home/BentoRecap";
import { markWhatsNewSeen } from "../components/nav/WhatsNew";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { ONBOARDING, WHATS_NEW } from "../content/homeSlides";
import { useAuth } from "../context/useAuth";

// Страница «Что нового?» (28.09.2026 заменила дашборд): нововведения мозаикой в духе итогов
// Apple-кейноута и гайд для нового пользователя на том же месте. С того же дня первой
// открываются тендеры, а сюда ведёт блок «Что нового?» в меню (`?tab=news`). Без параметра
// страница открывается на последней выбранной вкладке, впервые — на гайде.

type Tab = "news" | "guide";

const TAB_KEY = "home.tab";

// Раскладки мозаики на широком экране: четыре колонки, `hero` — центральная плитка.
// Каждый `id` из homeSlides должен встретиться здесь ровно одной прямоугольной областью.
const NEWS_AREAS = [
  "selection selection profiles profiles",
  "okpd2-tree hero hero minutes",
  "tenders-header hero hero feeds",
  "brand-lock brand-lock job-queue feeds",
  "no-auto-analysis meter-vpu deepseek-fast catalog-hide-eol",
];
// Плиток только для администратора сейчас нет — раскладка общая.
const NEWS_AREAS_ADMIN = NEWS_AREAS;

const GUIDE_AREAS = [
  "tenders hero hero tender-card",
  "tenders hero hero tender-card",
  "manual catalog catalog company",
  "analytics settings settings done",
];

function readTab(param: string | null): Tab {
  if (param === "news" || param === "guide") return param;
  try {
    const stored = localStorage.getItem(TAB_KEY);
    return stored === "news" || stored === "guide" ? stored : "guide";
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
    if (tabParam === "news" || tabParam === "guide") setTab(tabParam);
  }, [tabParam]);

  useEffect(() => {
    if (tab === "news") markWhatsNewSeen();
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
  const [welcome, ...guide] = ONBOARDING;

  const firstName = user?.full_name?.split(" ")[0];

  return (
    <AppShell>
      <div className="mx-auto max-w-6xl px-8 py-10">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="text-sm text-zinc-500">
              {firstName ? `${firstName}, добро пожаловать в Sova` : "Sova"}
            </div>
            <h1 className="mt-1 text-5xl font-semibold tracking-tight text-zinc-50">
              {tab === "news" ? "Что нового?" : "С чего начать"}
            </h1>
          </div>
          <SegmentedControl
            shape="pill"
            ariaLabel="Раздел страницы"
            value={tab}
            onChange={switchTab}
            segments={[
              { value: "news", label: "Что нового" },
              { value: "guide", label: "С чего начать" },
            ]}
          />
        </div>

        {tab === "news" ? (
          <BentoRecap
            key="news"
            hero={{ title: "Sova", subtitle: `Сентябрь — октябрь 2026 · ${news.length} нововведений` }}
            slides={news}
            areas={isAdmin ? NEWS_AREAS_ADMIN : NEWS_AREAS}
          />
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
          <p>Нажмите на плитку, чтобы прочитать подробности.</p>
          {tab === "news" && (
            <Link to="/changelog" className="text-indigo-400 hover:text-indigo-300">
              Вся история обновлений →
            </Link>
          )}
        </div>
      </div>
    </AppShell>
  );
}
