import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  ChevronDown,
  ExternalLink,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Star,
  Trash2,
  X,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  CompanyLicense,
  CompanyPastProject,
  CompanyProfile,
  CompanyProfileUpdate,
  EgrulCandidate,
  RusprofileDossier,
  RusprofileSyncResult,
} from "../../api/types";
import { DropdownMenu } from "../DropdownMenu";
import { SegmentedControl } from "../ui/SegmentedControl";
import { Tooltip } from "../ui/Tooltip";
import { formatDateTime, plural } from "../../utils/format";
import { SettingsNotice } from "./ui";

/**
 * Профили компаний (раздел 5.6 «Моя компания», раздел 7 ТЗ).
 *
 * Вход главной метрики: измерения «Задача» и «Компетенции» AI-оценки (раздел 5.5.1)
 * сравнивают требования закупки не с каталогом приборов, а с самой компанией — её
 * допусками, стажем и выполненными проектами. Пока раздел пуст, оценка не считается вовсе,
 * и карточка тендера прямо об этом говорит.
 *
 * Компаний с 07.09.2026 может быть несколько, ограничения на количество нет. Одна помечена
 * основной — с ней и считается оценка, по её ИНН синхронизируется история участий. Остальные
 * пока только копят данные для будущей аналитики по юрлицам.
 *
 * Редизайн 24.10.2026 (замечание заказчика: «слишком цветасто, много текста, всё в куче»).
 * Было: у каждой компании пять цветных плиток цифр и четыре блока в своих оттенках, открытых
 * разом, плюс абзацы пояснений над и под каждым. Стало — как в остальном приложении:
 * монохромные карточки с hairline-рамкой, один акцент. Основная компания раскрыта сразу —
 * ряд ключевых цифр и вкладки «Реквизиты / Допуски / Проекты / Досье», в каждый момент виден
 * один блок. Прочие компании — свёрнутые строки, редкие действия (сделать основной, удалить)
 * убраны в меню «⋯». Пояснения, нужные не всем и не всегда, ушли в подсказки по наведению.
 *
 * Юридические данные ищутся автопоиском, но **не сохраняются сами**: найденное подставляется
 * в поля, а записывает его человек кнопкой «Сохранить». Профиль подаётся в обоснования
 * AI-оценки, и непроверенный результат внешнего сервиса там неотличим от подтверждённого
 * факта. Ссылка на карточку rusprofile.ru разбирается напрямую (единственный путь, дающий
 * КПП), всё остальное — ИНН, ОГРН, название — уходит в ЕГРЮЛ.
 *
 * «Обновить из rusprofile» (с 18.09.2026) под учётной записью из «Приложений» заполняет
 * допуски, реализованные проекты и историю участий; трогает только пустые и свои же поля.
 */

const inputClass =
  "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none disabled:opacity-60";

const primaryButton =
  "flex items-center gap-2 rounded-lg bg-indigo-500 px-3.5 py-2 text-sm font-medium text-snow hover:bg-indigo-400 disabled:opacity-50";

const secondaryButton =
  "flex items-center gap-2 rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition-colors hover:bg-white/5 hover:text-zinc-100 disabled:opacity-50";

export function CompanyProfilesPanel({
  isAdmin,
  onPrimaryLoaded,
  onRusprofileSynced,
}: {
  isAdmin: boolean;
  /** Соседняя вкладка «История участий» синхронизируется по ИНН основной компании — и
   * показывает кнопку только когда он заполнен. */
  onPrimaryLoaded?: (profile: CompanyProfile | null) => void;
  /** Синхронизация основной компании с rusprofile пополняет и историю участий — соседняя
   * вкладка должна перечитать список. */
  onRusprofileSynced?: () => void;
}) {
  const [profiles, setProfiles] = useState<CompanyProfile[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  // Раскрытые неосновные компании; основная раскрыта всегда.
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // null — модалка закрыта, null-профиль внутри — режим добавления новой компании.
  const [editing, setEditing] = useState<{ profile: CompanyProfile | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Подтверждение удаления живёт в самой карточке, а не в системном `confirm`: спрашивать
  // окном браузера поверх тёмного интерфейса значит выдёргивать человека из страницы.
  const [confirmingId, setConfirmingId] = useState<string | null>(null);

  const publish = (list: CompanyProfile[]) => {
    setProfiles(list);
    onPrimaryLoaded?.(list.find((item) => item.is_primary) ?? null);
  };

  const load = async () => {
    try {
      publish(await api.get<CompanyProfile[]>("/company-profiles"));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить список компаний");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const makePrimary = async (profile: CompanyProfile) => {
    setBusyId(profile.id);
    setError(null);
    setNotice(null);
    try {
      await api.post<CompanyProfile>(`/company-profiles/${profile.id}/primary`);
      await load();
      setNotice(
        `Основная компания — «${companyShortTitle(profile)}». Уже посчитанные оценки не пересчитываются.`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сменить основную компанию");
    } finally {
      setBusyId(null);
    }
  };

  const syncRusprofile = async (profile: CompanyProfile) => {
    setBusyId(profile.id);
    setError(null);
    setNotice(null);
    try {
      const result = await api.post<RusprofileSyncResult>(
        `/company-profiles/${profile.id}/rusprofile-sync`,
      );
      await load();
      if (!profile.is_primary) setExpandedId(profile.id);
      setNotice(
        `Обновлено из rusprofile. ${result.message}` +
          (result.data_hidden ? " Часть данных на сайте скрыта — проверьте подписку." : ""),
      );
      if (profile.is_primary) onRusprofileSynced?.();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Не удалось обновить данные из rusprofile",
      );
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (profile: CompanyProfile) => {
    setConfirmingId(null);
    setBusyId(profile.id);
    setError(null);
    setNotice(null);
    try {
      await api.delete(`/company-profiles/${profile.id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить компанию");
    } finally {
      setBusyId(null);
    }
  };

  const primary = profiles.find((item) => item.is_primary) ?? null;
  const others = profiles.filter((item) => !item.is_primary);

  const cardProps = (profile: CompanyProfile) => ({
    profile,
    isAdmin,
    isBusy: busyId === profile.id,
    isConfirmingDelete: confirmingId === profile.id,
    onEdit: () => setEditing({ profile }),
    onSyncRusprofile: () => void syncRusprofile(profile),
    onMakePrimary: () => void makePrimary(profile),
    onAskDelete: () => setConfirmingId(profile.id),
    onCancelDelete: () => setConfirmingId(null),
    onConfirmDelete: () => void remove(profile),
  });

  return (
    <div className="space-y-8">
      {(error || notice) && (
        <div className="space-y-2">
          {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
          {notice && <SettingsNotice tone="success">{notice}</SettingsNotice>}
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center gap-2 py-10 text-sm text-zinc-500">
          <Loader2 size={15} className="animate-spin" />
          Загружаем компании…
        </div>
      ) : profiles.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-white/[0.1] px-6 py-14 text-center">
          <p className="text-sm text-zinc-300">Компаний пока нет</p>
          <p className="mt-1 text-sm text-zinc-500">
            Без профиля AI-оценка не считает «Задачу» и «Компетенции».
          </p>
          {isAdmin && (
            <button
              onClick={() => setEditing({ profile: null })}
              className={`${primaryButton} mx-auto mt-5`}
            >
              <Plus size={15} />
              Добавить компанию
            </button>
          )}
        </div>
      ) : (
        <>
          {primary && <CompanyCard {...cardProps(primary)} isExpanded />}

          {(others.length > 0 || isAdmin) && (
            <section>
              <div className="mb-3 flex items-end justify-between gap-3 px-1">
                <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
                  Другие компании
                  {others.length > 0 && (
                    <span className="font-normal tabular-nums text-zinc-600">{others.length}</span>
                  )}
                  <Tooltip content="Оценку тендеров и историю участий считает основная компания. Остальные копят данные для аналитики по юрлицам.">
                    <span className="cursor-help font-normal normal-case tracking-normal text-zinc-600">
                      ?
                    </span>
                  </Tooltip>
                </div>
                {isAdmin && (
                  <button
                    onClick={() => setEditing({ profile: null })}
                    className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-zinc-400 transition-colors hover:bg-white/5 hover:text-zinc-100"
                  >
                    <Plus size={15} />
                    Добавить
                  </button>
                )}
              </div>
              {others.length === 0 ? (
                <p className="rounded-2xl border border-dashed border-white/[0.08] px-5 py-6 text-center text-sm text-zinc-600">
                  Других компаний нет
                </p>
              ) : (
                <div className="space-y-3">
                  {others.map((profile) => (
                    <CompanyCard
                      key={profile.id}
                      {...cardProps(profile)}
                      isExpanded={expandedId === profile.id}
                      onToggle={() =>
                        setExpandedId((prev) => (prev === profile.id ? null : profile.id))
                      }
                    />
                  ))}
                </div>
              )}
            </section>
          )}

          {!isAdmin && (
            <p className="px-1 text-xs text-zinc-600">
              Компании правит администратор — они влияют на оценку всех тендеров.
            </p>
          )}
        </>
      )}

      {editing && (
        <CompanyProfileModal
          profile={editing.profile}
          isAdmin={isAdmin}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            if (!saved.is_primary) setExpandedId(saved.id);
            setNotice(
              saved.is_filled
                ? `Компания «${companyShortTitle(saved)}» сохранена.`
                : `Компания «${companyShortTitle(saved)}» сохранена. Без стажа, допусков и проектов оценка по ней не считается.`,
            );
            void load();
          }}
        />
      )}
    </div>
  );
}

function companyTitle(profile: CompanyProfile): string {
  return profile.legal_name?.trim() || (profile.inn ? `ИНН ${profile.inn}` : "Без наименования");
}

/** Короткое имя для аватара и подписей: «ООО ТД "Миртек"» вместо полной формы, если сайт его дал. */
function companyShortTitle(profile: CompanyProfile): string {
  return profile.rusprofile_data?.short_name?.trim() || companyTitle(profile);
}

const LEGAL_FORM_WORDS =
  /^(ООО|АО|ПАО|ЗАО|ОАО|ТД|ГУП|МУП|ИП|ОБЩЕСТВО|С|ОГРАНИЧЕННОЙ|ОТВЕТСТВЕННОСТЬЮ|АКЦИОНЕРНОЕ|ПУБЛИЧНОЕ|НЕПУБЛИЧНОЕ|ТОРГОВЫЙ|ДОМ|КОМПАНИЯ)$/i;

/** Буква для аватара — первая буква собственно названия, а не организационно-правовой формы. */
function companyInitial(profile: CompanyProfile): string {
  const short = companyShortTitle(profile).replace(/[«»"']/g, "");
  const word = short.split(/\s+/).find((part) => part.length > 1 && !LEGAL_FORM_WORDS.test(part));
  return (word ?? short).charAt(0).toUpperCase() || "К";
}

function stripRub(value: string): string {
  return value.replace(/\s*руб\.?$/, "");
}

function formatRuDate(value: string | null): string | null {
  return value ? new Date(value).toLocaleDateString("ru-RU") : null;
}

type DetailTab = "requisites" | "licenses" | "projects" | "dossier";

/**
 * Карточка компании: шапка (имя, ИНН, состояние, действия), под ней в раскрытом виде — ряд
 * ключевых цифр и вкладки с данными, которые уходят в AI-оценку.
 */
function CompanyCard({
  profile,
  isAdmin,
  isExpanded,
  isBusy,
  isConfirmingDelete,
  onToggle,
  onEdit,
  onSyncRusprofile,
  onMakePrimary,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  profile: CompanyProfile;
  isAdmin: boolean;
  isExpanded: boolean;
  isBusy: boolean;
  isConfirmingDelete: boolean;
  /** Нет у основной — она раскрыта всегда. */
  onToggle?: () => void;
  onEdit: () => void;
  onSyncRusprofile: () => void;
  onMakePrimary: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  const [tab, setTab] = useState<DetailTab>("requisites");
  const dossier = profile.rusprofile_data;
  const canSync = Boolean(profile.inn || profile.rusprofile_card_id);
  const isPrimary = profile.is_primary;

  const subtitle = [
    profile.inn ? `ИНН ${profile.inn}` : "ИНН не указан",
    profile.kpp ? `КПП ${profile.kpp}` : null,
    dossier?.status ?? null,
  ]
    .filter(Boolean)
    .join(" · ");

  const syncHint = canSync
    ? profile.rusprofile_synced_at
      ? `Последнее обновление: ${formatDateTime(profile.rusprofile_synced_at)}`
      : "Заполнить допуски, проекты и историю участий с rusprofile.ru"
    : "Укажите ИНН — по нему компания ищется на rusprofile.ru";

  // У основной компании обновление и правка — кнопки на виду; у прочих всё в меню «⋯»,
  // иначе список одинаковых строк превращается в столбец одинаковых кнопок.
  const menuItems = [
    ...(!isPrimary
      ? [
          {
            key: "sync",
            label: "Обновить из rusprofile",
            icon: <RefreshCw size={15} />,
            hint: syncHint,
            disabled: isBusy || !canSync,
            onSelect: onSyncRusprofile,
          },
          { key: "edit", label: "Изменить", icon: <Pencil size={15} />, onSelect: onEdit },
          {
            key: "primary",
            label: "Сделать основной",
            icon: <Star size={15} />,
            hint: "С неё будет считаться AI-оценка",
            disabled: isBusy,
            onSelect: onMakePrimary,
          },
          {
            key: "delete",
            label: "Удалить",
            icon: <Trash2 size={15} />,
            disabled: isBusy,
            onSelect: onAskDelete,
          },
        ]
      : []),
  ];

  const heading = (
    <span className="flex min-w-0 flex-1 items-center gap-4 text-left">
      <span
        className={`flex shrink-0 items-center justify-center rounded-xl font-semibold ${
          isPrimary
            ? "h-12 w-12 bg-white/[0.08] text-lg text-zinc-100"
            : "h-10 w-10 bg-white/[0.05] text-base text-zinc-400"
        }`}
      >
        {companyInitial(profile)}
      </span>
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-2">
          <span
            className={`truncate font-semibold text-zinc-100 ${isPrimary ? "text-lg" : "text-[15px]"}`}
          >
            {companyShortTitle(profile)}
          </span>
          {isPrimary && (
            <span className="rounded-full bg-white/[0.06] px-2.5 py-0.5 text-xs text-zinc-300">
              Основная
            </span>
          )}
          {!profile.is_filled && (
            <span className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
              Не заполнен
            </span>
          )}
        </span>
        <span className="mt-0.5 block truncate text-sm text-zinc-500">{subtitle}</span>
      </span>
    </span>
  );

  return (
    <section className="overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03]">
      <header className={`flex flex-wrap items-center gap-3 px-5 ${isPrimary ? "py-5" : "py-4"}`}>
        {onToggle ? (
          <button
            onClick={onToggle}
            className="flex min-w-0 flex-1 items-center gap-3"
            aria-expanded={isExpanded}
          >
            {heading}
            <ChevronDown
              size={16}
              className={`shrink-0 text-zinc-600 transition-transform ${isExpanded ? "rotate-180" : ""}`}
            />
          </button>
        ) : (
          heading
        )}

        {isConfirmingDelete ? (
          <div className="flex shrink-0 items-center gap-2 text-sm text-zinc-400">
            Удалить компанию?
            <button
              onClick={onConfirmDelete}
              className="rounded-lg bg-red-500/15 px-3 py-1.5 text-red-300 hover:bg-red-500/25"
            >
              Удалить
            </button>
            <button onClick={onCancelDelete} className={`${secondaryButton} !py-1.5`}>
              Отмена
            </button>
          </div>
        ) : (
          isAdmin && (
            <div className="flex shrink-0 items-center gap-1.5">
              {isBusy && !isPrimary && <Loader2 size={15} className="animate-spin text-zinc-500" />}
              {isPrimary && (
                <>
                  <Tooltip content={syncHint}>
                    <button
                      onClick={onSyncRusprofile}
                      disabled={isBusy || !canSync}
                      className={secondaryButton}
                    >
                      <RefreshCw size={14} className={isBusy ? "animate-spin" : ""} />
                      <span className="hidden sm:inline">Обновить из rusprofile</span>
                    </button>
                  </Tooltip>
                  <button onClick={onEdit} className={secondaryButton}>
                    <Pencil size={14} />
                    <span className="hidden sm:inline">Изменить</span>
                  </button>
                </>
              )}
              {menuItems.length > 0 && (
                <DropdownMenu
                  trigger={<MoreHorizontal size={16} />}
                  title="Ещё"
                  items={menuItems}
                />
              )}
            </div>
          )
        )}
      </header>

      {isExpanded && (
        <>
          <KeyFigures profile={profile} />
          <div className="border-t border-white/[0.06] px-5 pb-5 pt-4">
            <div className="mb-4 overflow-x-auto">
              <div className="inline-flex">
                <SegmentedControl<DetailTab>
                  value={tab}
                  onChange={setTab}
                  size="sm"
                  ariaLabel="Данные компании"
                  segments={[
                    { value: "requisites", label: "Реквизиты" },
                    { value: "licenses", label: <TabLabel text="Допуски" count={profile.licenses.length} /> },
                    { value: "projects", label: <TabLabel text="Проекты" count={profile.past_projects.length} /> },
                    { value: "dossier", label: "Досье" },
                  ]}
                />
              </div>
            </div>
            {tab === "requisites" && <Requisites profile={profile} />}
            {tab === "licenses" && <Licenses licenses={profile.licenses} />}
            {tab === "projects" && <Projects projects={profile.past_projects} />}
            {tab === "dossier" && (
              <Dossier dossier={dossier} syncedAt={profile.rusprofile_synced_at} />
            )}
          </div>
        </>
      )}
    </section>
  );
}

function TabLabel({ text, count }: { text: string; count: number }) {
  return (
    <>
      {text}
      {count > 0 && <span className="ml-1.5 tabular-nums text-zinc-500">{count}</span>}
    </>
  );
}

/** Ряд ключевых цифр: одна строка, монохром, разделители — без цветных плиток. */
function KeyFigures({ profile }: { profile: CompanyProfile }) {
  const purchases = profile.rusprofile_data?.purchases;
  const finance = profile.rusprofile_data?.finance ?? {};
  const years = profile.years_of_experience;

  const figures: Array<{ label: string; value: string; unit?: string; hint?: string }> = [
    {
      label: "Стаж",
      value: years ? String(years) : "—",
      unit: years ? plural(years, "год", "года", "лет") : undefined,
      hint: profile.registration_date ? `с ${formatRuDate(profile.registration_date)}` : undefined,
    },
    { label: "Допуски", value: String(profile.licenses.length) },
    { label: "Проекты", value: String(profile.past_projects.length) },
    {
      label: "Победы",
      value: purchases ? String(purchases.wins) : "—",
      hint: purchases
        ? `из ${purchases.fetched} ${plural(purchases.fetched, "закупки", "закупок", "закупок")}`
        : undefined,
    },
    {
      label: `Выручка${finance.year ? ` ${finance.year}` : ""}`,
      value: finance.revenue ? stripRub(finance.revenue) : "—",
      unit: finance.revenue ? "₽" : undefined,
      hint: finance.revenue_change ? `${finance.revenue_change} к прошлому году` : undefined,
    },
  ];

  return (
    <div className="grid grid-cols-2 border-t border-white/[0.06] sm:grid-cols-3 lg:grid-cols-5">
      {figures.map((figure, index) => (
        <div
          key={figure.label}
          className={`px-5 py-4 ${index > 0 ? "lg:border-l lg:border-white/[0.06]" : ""}`}
        >
          <div className="text-xs text-zinc-500">{figure.label}</div>
          <div className="mt-1 flex items-baseline gap-1.5">
            <span className="text-2xl font-semibold tabular-nums tracking-tight text-zinc-100">
              {figure.value}
            </span>
            {figure.unit && <span className="text-sm text-zinc-500">{figure.unit}</span>}
          </div>
          {figure.hint && <div className="mt-0.5 truncate text-xs text-zinc-600">{figure.hint}</div>}
        </div>
      ))}
    </div>
  );
}

/** Список «подпись — значение» строками с тонкими разделителями. */
function FactList({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="divide-y divide-white/[0.06]">
      {rows.map(([label, value]) => (
        <div key={label} className="grid gap-1 py-2.5 sm:grid-cols-[200px_1fr] sm:gap-4">
          <dt className="text-sm text-zinc-500">{label}</dt>
          <dd className="min-w-0 text-sm text-zinc-200">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-zinc-500">{children}</p>;
}

function Requisites({ profile }: { profile: CompanyProfile }) {
  const codes = profile.rusprofile_data?.codes ?? {};
  const dash = <span className="text-zinc-600">—</span>;
  const rows: Array<[string, ReactNode]> = [
    ["Наименование", profile.legal_name?.trim() || dash],
    ["ИНН", profile.inn ? <span className="tabular-nums">{profile.inn}</span> : dash],
    ["КПП", profile.kpp ? <span className="tabular-nums">{profile.kpp}</span> : dash],
    ["ОГРН", profile.ogrn ? <span className="tabular-nums">{profile.ogrn}</span> : dash],
    ["Дата регистрации", formatRuDate(profile.registration_date) ?? dash],
    ["Юридический адрес", profile.legal_address?.trim() || dash],
  ];
  if (Object.keys(codes).length > 0) {
    rows.push([
      "Коды статистики",
      <span className="text-zinc-400 tabular-nums">
        {Object.entries(codes)
          .map(([key, value]) => `${key.toUpperCase()} ${value}`)
          .join(" · ")}
      </span>,
    ]);
  }
  return <FactList rows={rows} />;
}

/** Пометка у записи, которую ведёт синхронизация с rusprofile, а не человек. */
function SourceMark() {
  return (
    <Tooltip content="Запись с rusprofile.ru — обновляется синхронизацией">
      <span className="shrink-0 text-xs text-zinc-600">rusprofile</span>
    </Tooltip>
  );
}

function Licenses({ licenses }: { licenses: CompanyLicense[] }) {
  if (licenses.length === 0) {
    return <Empty>Допуски не указаны — «Компетенции» сочтут, что подтверждений нет.</Empty>;
  }
  return (
    <ul className="divide-y divide-white/[0.06]">
      {licenses.map((license, index) => {
        const meta = [
          license.number ? `№ ${license.number}` : null,
          license.valid_until ? `до ${license.valid_until}` : null,
          license.issuer,
        ].filter(Boolean);
        return (
          <li key={index} className="flex items-start justify-between gap-4 py-3">
            <div className="min-w-0">
              <div className="text-sm text-zinc-200">{license.name}</div>
              {meta.length > 0 && (
                <div className="mt-0.5 truncate text-xs text-zinc-500">{meta.join(" · ")}</div>
              )}
            </div>
            {license.source === "rusprofile" && <SourceMark />}
          </li>
        );
      })}
    </ul>
  );
}

function Projects({ projects }: { projects: CompanyPastProject[] }) {
  if (projects.length === 0) {
    return <Empty>Проекты не указаны — «Задаче» не с чем сопоставить предмет закупки.</Empty>;
  }
  return (
    <ul className="divide-y divide-white/[0.06]">
      {projects.map((project, index) => {
        const meta = [project.customer, project.volume].filter(Boolean);
        return (
          <li key={index} className="flex items-start gap-4 py-3">
            <span className="w-12 shrink-0 pt-px text-sm tabular-nums text-zinc-500">
              {project.year ?? "—"}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm text-zinc-200">{project.work_type}</div>
              {meta.length > 0 && (
                <div className="mt-0.5 truncate text-xs text-zinc-500">{meta.join(" · ")}</div>
              )}
            </div>
            {project.source === "rusprofile" && <SourceMark />}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Досье rusprofile: несколько цифр строкой и факты списком. Те же факты уходят в промпт
 * AI-оценки — «Компетенции» сверяют с ними требования к участнику.
 */
function Dossier({
  dossier,
  syncedAt,
}: {
  dossier: RusprofileDossier | null;
  syncedAt: string | null;
}) {
  if (!dossier) {
    return (
      <Empty>
        Досье ещё не загружалось — нажмите «Обновить из rusprofile».
      </Empty>
    );
  }
  const finance = dossier.finance ?? {};
  const summary = dossier.purchases_summary ?? {};

  const figures: Array<{ label: string; value: string; hint?: string }> = [];
  if (dossier.headcount) {
    figures.push({
      label: "Численность",
      value: String(dossier.headcount),
      hint: dossier.headcount_year ? `чел., ${dossier.headcount_year}` : "чел.",
    });
  }
  if (finance.profit) {
    figures.push({ label: "Прибыль", value: stripRub(finance.profit), hint: finance.profit_change ?? "₽" });
  }
  if (dossier.authorized_capital) {
    figures.push({ label: "Уставный капитал", value: stripRub(dossier.authorized_capital), hint: "₽" });
  }
  if (summary.contracts_count) {
    figures.push({
      label: "Контрактов",
      value: String(summary.contracts_count),
      hint: summary.contracts_sum ? `на ${summary.contracts_sum}` : undefined,
    });
  }

  const ratings = Object.entries(finance.ratings ?? {}).filter(([, v]) => v);
  const facts: Array<[string, string | null | undefined]> = [
    [
      "Руководитель",
      dossier.ceo_name
        ? [dossier.ceo_position, dossier.ceo_name, dossier.ceo_since].filter(Boolean).join(" ")
        : null,
    ],
    [
      "Учредители",
      dossier.founders?.length
        ? dossier.founders
            .map((f) => [f.name, f.share ? `доля ${f.share}` : null].filter(Boolean).join(", "))
            .join("; ")
        : null,
    ],
    [
      "Основной ОКВЭД",
      dossier.main_okved_code
        ? `${dossier.main_okved_code} ${dossier.main_okved_name ?? ""}`.trim()
        : null,
    ],
    ["Реестр МСП", dossier.msp_status],
    ["Налоговый режим", dossier.tax_regime],
    [
      "Крупные заказчики",
      summary.top_customers?.length ? summary.top_customers.map((c) => c.name).join(", ") : null,
    ],
    ["Рейтинги", ratings.length ? ratings.map(([k, v]) => `${k}: ${v}`).join(" · ") : null],
    [
      "Контакты",
      [...(dossier.phones ?? []), ...(dossier.emails ?? []), dossier.website].filter(Boolean).join(" · ") ||
        null,
    ],
    ["Исполнительные производства", dossier.enforcement],
    ["Проверки", dossier.inspections],
  ];
  const visible = facts.filter((row): row is [string, string] => Boolean(row[1]));

  return (
    <div className="space-y-4">
      {dossier.data_hidden && (
        <p className="flex items-center gap-2 text-sm text-zinc-400">
          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
          Часть данных скрыта подпиской — проверьте учётную запись rusprofile.
        </p>
      )}

      {figures.length > 0 && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
          {figures.map((figure) => (
            <div key={figure.label}>
              <div className="text-xs text-zinc-500">{figure.label}</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-zinc-100">
                {figure.value}
              </div>
              {figure.hint && <div className="truncate text-xs text-zinc-600">{figure.hint}</div>}
            </div>
          ))}
        </div>
      )}

      {visible.length > 0 && (
        <FactList
          rows={visible.map(([label, value]) => [label, <span className="line-clamp-3">{value}</span>])}
        />
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-500">
        {syncedAt && <span>Обновлено {formatDateTime(syncedAt)}</span>}
        <a
          href={dossier.source_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-indigo-300 hover:underline"
        >
          Карточка на rusprofile
          <ExternalLink size={12} />
        </a>
      </div>
    </div>
  );
}

/** Заголовок раздела формы — как группы в «Настройках». */
function FormSection({
  title,
  hint,
  action,
  children,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
          {title}
          {hint && (
            <Tooltip content={hint}>
              <span className="cursor-help font-normal normal-case tracking-normal text-zinc-600">?</span>
            </Tooltip>
          )}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function AddRowButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-zinc-400 hover:bg-white/5 hover:text-zinc-100"
    >
      <Plus size={14} />
      Добавить
    </button>
  );
}

function RemoveRowButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="mt-1.5 flex h-[38px] w-9 items-center justify-center rounded-lg text-zinc-600 hover:bg-white/5 hover:text-red-400"
      title="Удалить"
    >
      <Trash2 size={14} />
    </button>
  );
}

/**
 * Форма компании в модальном окне.
 *
 * Форма правится целиком и сохраняется одной кнопкой: лицензий и проектов немного, а
 * построчное сохранение здесь означало бы CRUD ради двух десятков строк.
 *
 * Новая компания создаётся POST-ом, существующая правится PUT-ом по своему id — основной
 * от этого никто не становится: смена основной компании меняет входы оценки по всем
 * тендерам сразу и делается отдельным действием в списке.
 */
export function CompanyProfileModal({
  profile,
  isAdmin,
  onClose,
  onSaved,
}: {
  /** null — создаём новую компанию. */
  profile: CompanyProfile | null;
  isAdmin: boolean;
  onClose: () => void;
  onSaved: (profile: CompanyProfile) => void;
}) {
  const [legalName, setLegalName] = useState(profile?.legal_name ?? "");
  const [inn, setInn] = useState(profile?.inn ?? "");
  const [kpp, setKpp] = useState(profile?.kpp ?? "");
  const [ogrn, setOgrn] = useState(profile?.ogrn ?? "");
  const [registrationDate, setRegistrationDate] = useState(profile?.registration_date ?? "");
  const [legalAddress, setLegalAddress] = useState(profile?.legal_address ?? "");
  const [lookupQuery, setLookupQuery] = useState("");
  const [candidates, setCandidates] = useState<EgrulCandidate[] | null>(null);
  const [isLookingUp, setIsLookingUp] = useState(false);
  const [years, setYears] = useState<string>(
    profile?.years_of_experience ? String(profile.years_of_experience) : "",
  );
  const [licenses, setLicenses] = useState<CompanyLicense[]>(profile?.licenses ?? []);
  const [projects, setProjects] = useState<CompanyPastProject[]>(profile?.past_projects ?? []);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const lookup = async () => {
    const query = lookupQuery.trim() || inn.trim() || legalName.trim();
    if (!query) {
      setError("Укажите ИНН, название или ссылку на карточку rusprofile.");
      return;
    }
    setIsLookingUp(true);
    setError(null);
    setNotice(null);
    setCandidates(null);
    try {
      const found = await api.get<EgrulCandidate[]>(
        `/company-profile/egrul-lookup?query=${encodeURIComponent(query)}`,
      );
      setCandidates(found);
      if (found.length === 1) applyCandidate(found[0]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Автопоиск не удался");
    } finally {
      setIsLookingUp(false);
    }
  };

  /** Подставляет найденное в поля, но НЕ сохраняет: подтверждение — отдельное действие. */
  const applyCandidate = (candidate: EgrulCandidate) => {
    setLegalName(candidate.legal_name ?? "");
    setInn(candidate.inn ?? "");
    // КПП приходит только от rusprofile — у кандидата из ЕГРЮЛ его нет, и затирать уже
    // введённое значение пустотой нельзя.
    if (candidate.kpp) setKpp(candidate.kpp);
    setOgrn(candidate.ogrn ?? "");
    setRegistrationDate(candidate.registration_date ?? "");
    setLegalAddress(candidate.legal_address ?? "");
    setNotice(
      `Данные из ${candidate.source === "rusprofile" ? "rusprofile.ru" : "ЕГРЮЛ"} подставлены — проверьте и сохраните.`,
    );
  };

  const save = async () => {
    setIsSaving(true);
    setError(null);
    setNotice(null);
    try {
      const payload: CompanyProfileUpdate = {
        legal_name: legalName.trim() || null,
        inn: inn.trim() || null,
        kpp: kpp.trim() || null,
        ogrn: ogrn.trim() || null,
        registration_date: registrationDate.trim() || null,
        legal_address: legalAddress.trim() || null,
        years_of_experience: years.trim() === "" ? null : Number(years),
        // Пустые строки в конце списка — след от «добавить» без заполнения; отправлять их
        // значит подсовывать модели пустые допуски как факт.
        licenses: licenses.filter((item) => item.name.trim() !== ""),
        past_projects: projects.filter((item) => item.work_type.trim() !== ""),
      };
      const saved = profile
        ? await api.put<CompanyProfile>(`/company-profiles/${profile.id}`, payload)
        : await api.post<CompanyProfile>("/company-profiles", payload);
      onSaved(saved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить компанию");
    } finally {
      setIsSaving(false);
    }
  };

  const updateLicense = (index: number, patch: Partial<CompanyLicense>) =>
    setLicenses((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  const updateProject = (index: number, patch: Partial<CompanyPastProject>) =>
    setProjects((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  const field = (
    label: string,
    value: string,
    onChange: (value: string) => void,
    options: { placeholder?: string; type?: string; className?: string } = {},
  ) => (
    <label className={`block text-sm text-zinc-400 ${options.className ?? ""}`}>
      {label}
      <input
        type={options.type ?? "text"}
        value={value}
        disabled={!isAdmin}
        onChange={(e) => onChange(e.target.value)}
        placeholder={options.placeholder}
        className={inputClass}
      />
    </label>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 px-4 py-8"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-zinc-900 shadow-[0_20px_60px_rgba(0,0,0,0.35)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pb-2 pt-5">
          <h2 className="text-lg font-semibold tracking-tight text-zinc-100">
            {profile ? companyShortTitle(profile) : "Новая компания"}
          </h2>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
            title="Закрыть"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 space-y-8 overflow-y-auto px-6 py-4">
          {(error || notice) && (
            <div className="space-y-2">
              {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
              {notice && <SettingsNotice tone="info">{notice}</SettingsNotice>}
            </div>
          )}

          {isAdmin && (
            <div>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Search
                    size={15}
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
                  />
                  <input
                    value={lookupQuery}
                    onChange={(e) => setLookupQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void lookup();
                    }}
                    placeholder="Найти по ИНН, названию или ссылке rusprofile"
                    className={`${inputClass} !mt-0 pl-9`}
                  />
                </div>
                <button onClick={() => void lookup()} disabled={isLookingUp} className={secondaryButton}>
                  {isLookingUp && <Loader2 size={14} className="animate-spin" />}
                  Найти
                </button>
              </div>
              <p className="mt-1.5 text-xs text-zinc-600">
                Найденное подставится в поля и запишется только после «Сохранить». КПП даёт
                только ссылка rusprofile.
              </p>
              {candidates && candidates.length > 1 && (
                <div className="mt-3 divide-y divide-white/[0.06] rounded-xl border border-white/[0.08]">
                  {candidates.map((candidate, index) => (
                    <button
                      key={`${candidate.inn}-${index}`}
                      onClick={() => applyCandidate(candidate)}
                      className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-zinc-300 hover:bg-white/5"
                    >
                      <span className="truncate">{candidate.legal_name ?? "Без наименования"}</span>
                      <span className="shrink-0 tabular-nums text-zinc-600">
                        {candidate.inn ?? "—"}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <FormSection
            title="Реквизиты"
            hint="По ИНН основной компании синхронизируется история участий. КПП нужен в заявке — в ЕГРЮЛ его нет."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              {field("Юридическое наименование", legalName, setLegalName, {
                placeholder: "ООО «МИРТЕК»",
                className: "sm:col-span-2",
              })}
              {field("ИНН", inn, setInn, { placeholder: "6167102308" })}
              {field("КПП", kpp, setKpp, { placeholder: "263501001" })}
              {field("ОГРН", ogrn, setOgrn, { placeholder: "1086167002204" })}
              {field("Дата регистрации", registrationDate, setRegistrationDate, { type: "date" })}
              {field("Юридический адрес", legalAddress, setLegalAddress, {
                placeholder: "г. Ростов-на-Дону, …",
                className: "sm:col-span-2",
              })}
              {field("Стаж, лет", years, setYears, { type: "number", placeholder: "18" })}
            </div>
          </FormSection>

          <FormSection
            title="Допуски и лицензии"
            hint="Без допусков «Компетенции» считают, что формальных подтверждений нет."
            action={isAdmin && <AddRowButton onClick={() => setLicenses((prev) => [...prev, { name: "" }])} />}
          >
            {licenses.length === 0 ? (
              <p className="text-sm text-zinc-600">Не указаны</p>
            ) : (
              <div className="space-y-2">
                {licenses.map((license, index) => (
                  <div key={index} className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr_auto]">
                    <input
                      value={license.name}
                      disabled={!isAdmin}
                      onChange={(e) => updateLicense(index, { name: e.target.value })}
                      placeholder="СРО, лицензия ФСБ, ISO 9001"
                      className={inputClass}
                    />
                    <input
                      value={license.number ?? ""}
                      disabled={!isAdmin}
                      onChange={(e) => updateLicense(index, { number: e.target.value })}
                      placeholder="Номер"
                      className={inputClass}
                    />
                    <input
                      value={license.valid_until ?? ""}
                      disabled={!isAdmin}
                      onChange={(e) => updateLicense(index, { valid_until: e.target.value })}
                      placeholder="Действует до"
                      className={inputClass}
                    />
                    {isAdmin && (
                      <RemoveRowButton
                        onClick={() => setLicenses((prev) => prev.filter((_, i) => i !== index))}
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </FormSection>

          <FormSection
            title="Реализованные проекты"
            hint="С проектами «Задача» сопоставляет предмет закупки."
            action={isAdmin && <AddRowButton onClick={() => setProjects((prev) => [...prev, { work_type: "" }])} />}
          >
            {projects.length === 0 ? (
              <p className="text-sm text-zinc-600">Не указаны</p>
            ) : (
              <div className="space-y-2">
                {projects.map((project, index) => (
                  <div key={index} className="grid gap-2 sm:grid-cols-[2fr_2fr_1fr_80px_auto]">
                    <input
                      value={project.work_type}
                      disabled={!isAdmin}
                      onChange={(e) => updateProject(index, { work_type: e.target.value })}
                      placeholder="Поставка ПУ, монтаж, АСКУЭ"
                      className={inputClass}
                    />
                    <input
                      value={project.customer ?? ""}
                      disabled={!isAdmin}
                      onChange={(e) => updateProject(index, { customer: e.target.value })}
                      placeholder="Заказчик"
                      className={inputClass}
                    />
                    <input
                      value={project.volume ?? ""}
                      disabled={!isAdmin}
                      onChange={(e) => updateProject(index, { volume: e.target.value })}
                      placeholder="Объём"
                      className={inputClass}
                    />
                    <input
                      type="number"
                      value={project.year ?? ""}
                      disabled={!isAdmin}
                      onChange={(e) =>
                        updateProject(index, {
                          year: e.target.value === "" ? null : Number(e.target.value),
                        })
                      }
                      placeholder="Год"
                      className={inputClass}
                    />
                    {isAdmin && (
                      <RemoveRowButton
                        onClick={() => setProjects((prev) => prev.filter((_, i) => i !== index))}
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </FormSection>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-white/[0.06] px-6 py-4">
          <p className="text-xs text-zinc-600">
            {isAdmin ? "Посчитанные оценки не пересчитываются автоматически." : ""}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <button onClick={onClose} className={secondaryButton}>
              {isAdmin ? "Отмена" : "Закрыть"}
            </button>
            {isAdmin && (
              <button onClick={() => void save()} disabled={isSaving} className={primaryButton}>
                {isSaving && <Loader2 size={14} className="animate-spin" />}
                Сохранить
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
