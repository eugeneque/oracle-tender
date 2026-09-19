import { useEffect, useState } from "react";
import {
  Briefcase,
  ChevronDown,
  ChevronRight,
  FileText,
  Globe,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
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
import { formatDateTime, plural } from "../../utils/format";

/**
 * Профили компаний (раздел 5.6 «Моя компания», раздел 7 ТЗ).
 *
 * Вход главной метрики: измерения «Задача» и «Компетенции» AI-оценки (раздел 5.5.1)
 * сравнивают требования закупки не с каталогом приборов, а с самой компанией — её
 * допусками, стажем и выполненными проектами. Пока раздел пуст, оценка не считается вовсе,
 * и карточка тендера прямо об этом говорит.
 *
 * Компаний с 07.09.2026 может быть несколько, ограничения на количество нет: работа с
 * закупками идёт от разных юрлиц, и разложить участие по ним без отдельных карточек нельзя.
 * Одна компания помечена основной — с ней и считается оценка, по её ИНН синхронизируется
 * история участий. Остальные пока только хранятся: аналитика по юрлицам появится позже, но
 * данные для неё копятся с этого момента.
 *
 * Список — свёрнутые строки, правка — модальное окно. Форма профиля длинная (реквизиты,
 * стаж, допуски, проекты), и держать её раскрытой для каждой компании значило бы прятать сам
 * список за экранами полей.
 *
 * Юридические данные ищутся автопоиском, но **не сохраняются сами**: найденное подставляется
 * в поля, а записывает его человек кнопкой «Сохранить». Профиль подаётся в обоснования
 * AI-оценки, и непроверенный результат внешнего сервиса там неотличим от подтверждённого
 * факта.
 *
 * Источников автопоиска два, и выбираются они по виду запроса: ссылка на карточку
 * rusprofile.ru разбирается напрямую (единственный путь, дающий КПП, — в выдаче ЕГРЮЛ его
 * нет), всё остальное — ИНН, ОГРН, название — уходит в ЕГРЮЛ как в первоисточник.
 *
 * С 18.09.2026 есть и третий путь — «Обновить из rusprofile»: под учётной записью из
 * «Интеграций» сайт отдаёт карточку целиком, и допуски, реализованные проекты (из
 * выигранных закупок) и историю участий заполняет синхронизация, а не человек. Она
 * заполняет только пустые и свои же поля: подтверждённое человеком и ручные записи остаются.
 */

const inputClass =
  "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

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
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // null — модалка закрыта, undefined-профиль внутри — режим добавления новой компании.
  const [editing, setEditing] = useState<{ profile: CompanyProfile | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Подтверждение удаления живёт в самой строке, а не в системном `confirm`: карточка
  // компании — не мелочь, которую сносят не глядя, а спрашивать о ней окном браузера поверх
  // тёмного интерфейса значит выдёргивать человека из страницы.
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
        `Основная компания — «${companyTitle(profile)}». Уже посчитанные оценки не ` +
          "пересчитываются: каждая хранит снимок профиля и остаётся объяснимой.",
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
      setExpandedId(profile.id);
      setNotice(
        `Обновлено из rusprofile. ${result.message}` +
          (result.data_hidden
            ? " Проверьте подписку учётной записи: часть данных на сайте скрыта."
            : ""),
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

  return (
    <div>
      {error && (
        <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}
      {notice && (
        <div className="mb-4 rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] px-4 py-2.5 text-sm text-indigo-300">
          {notice}
        </div>
      )}

      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-[11px] leading-relaxed text-zinc-600">
          Компаний может быть сколько угодно. Оценку тендеров и историю участий питает
          основная — остальные карточки копят данные для аналитики по юрлицам.
        </p>
        {isAdmin && (
          <button
            onClick={() => setEditing({ profile: null })}
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
          >
            <Plus size={13} />
            Добавить компанию
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 py-6 text-xs text-zinc-500">
          <Loader2 size={14} className="animate-spin" />
          Загружаем компании…
        </div>
      ) : profiles.length === 0 ? (
        <div className="rounded-lg border border-dashed border-white/10 px-4 py-8 text-center text-xs text-zinc-600">
          Компаний пока нет. Без профиля не считаются измерения «Задача» и «Компетенции» —
          {isAdmin ? " добавьте первую компанию." : " попросите администратора завести профиль."}
        </div>
      ) : (
        <div className="space-y-4">
          {profiles.map((profile) => (
            <CompanyRow
              key={profile.id}
              profile={profile}
              isAdmin={isAdmin}
              isExpanded={expandedId === profile.id}
              isBusy={busyId === profile.id}
              isConfirmingDelete={confirmingId === profile.id}
              onToggle={() =>
                setExpandedId((prev) => (prev === profile.id ? null : profile.id))
              }
              onEdit={() => setEditing({ profile })}
              onSyncRusprofile={() => void syncRusprofile(profile)}
              onMakePrimary={() => void makePrimary(profile)}
              onAskDelete={() => setConfirmingId(profile.id)}
              onCancelDelete={() => setConfirmingId(null)}
              onConfirmDelete={() => void remove(profile)}
            />
          ))}
        </div>
      )}

      {!isAdmin && profiles.length > 0 && (
        <p className="mt-3 text-[11px] text-zinc-600">
          Компании правит администратор: они влияют на оценку всех тендеров сразу.
        </p>
      )}

      {editing && (
        <CompanyProfileModal
          profile={editing.profile}
          isAdmin={isAdmin}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null);
            setExpandedId(saved.id);
            setNotice(
              saved.is_filled
                ? `Компания «${companyTitle(saved)}» сохранена.`
                : `Компания «${companyTitle(saved)}» сохранена, но профиль пуст: без стажа, ` +
                    "допусков и проектов оценка по ней не считается.",
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

/**
 * Одна компания — отдельная карточка. Свёрнутая показывает шапку и ряд ключевых цифр (стаж,
 * допуски, проекты, история, выручка), раскрытая — четыре самостоятельных блока, каждый в
 * своём оттенке: реквизиты, допуски, проекты и досье с сайта. Так видно, что реально уходит
 * в AI-оценку, без открытия формы; блоки различимы с первого взгляда, а не сливаются в один
 * список «ключ — значение».
 */
function CompanyRow({
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
  onToggle: () => void;
  onEdit: () => void;
  onSyncRusprofile: () => void;
  onMakePrimary: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}) {
  const dossier = profile.rusprofile_data;
  const purchases = dossier?.purchases;
  const finance = dossier?.finance ?? {};
  const canSync = Boolean(profile.inn || profile.rusprofile_card_id);

  return (
    <div
      className={`relative overflow-hidden rounded-2xl border ${
        profile.is_primary
          ? "border-indigo-500/30 bg-gradient-to-br from-indigo-500/[0.08] via-white/[0.03] to-transparent shadow-[0_0_60px_-20px_rgba(99,102,241,0.45)]"
          : "border-white/[0.08] bg-gradient-to-br from-white/[0.04] to-transparent"
      }`}
    >
      {profile.is_primary && (
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-24 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl"
        />
      )}

      {/* Шапка */}
      <div className="relative flex flex-wrap items-center gap-3 px-5 py-4">
        <button
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-center gap-3.5 text-left"
          title={isExpanded ? "Свернуть" : "Развернуть"}
        >
          <span
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-base font-semibold ${
              profile.is_primary
                ? "bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-lg shadow-indigo-500/30"
                : "bg-white/[0.06] text-zinc-300"
            }`}
          >
            {companyInitial(profile)}
          </span>
          <span className="min-w-0">
            <span className="flex items-center gap-2">
              <span className="truncate text-[15px] font-semibold text-zinc-100">
                {companyShortTitle(profile)}
              </span>
              <span className="text-zinc-600">
                {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </span>
            </span>
            <span className="block truncate text-[11px] text-zinc-500">
              {[
                profile.inn ? `ИНН ${profile.inn}` : "ИНН не указан",
                profile.kpp ? `КПП ${profile.kpp}` : null,
                dossier?.status ?? null,
                profile.rusprofile_synced_at
                  ? `rusprofile · ${formatDateTime(profile.rusprofile_synced_at)}`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </span>
        </button>

        {isConfirmingDelete ? (
          <div className="flex shrink-0 items-center gap-2 text-[11px] text-zinc-400">
            Удалить компанию?
            <button
              onClick={onConfirmDelete}
              className="rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-1 text-red-300 hover:bg-red-500/20"
            >
              Удалить
            </button>
            <button
              onClick={onCancelDelete}
              className="rounded-lg border border-white/10 px-2.5 py-1 text-zinc-300 hover:bg-white/5"
            >
              Отмена
            </button>
          </div>
        ) : (
          <div className="flex shrink-0 flex-wrap items-center gap-1.5">
            {profile.is_primary && (
              <span className="rounded-full border border-indigo-400/30 bg-indigo-500/15 px-2.5 py-0.5 text-[11px] font-medium text-indigo-200">
                основная
              </span>
            )}
            {!profile.is_filled && (
              <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-0.5 text-[11px] text-amber-300">
                не заполнен
              </span>
            )}
            {isBusy && <Loader2 size={13} className="animate-spin text-zinc-500" />}
            {isAdmin && (
              <button
                onClick={onSyncRusprofile}
                disabled={isBusy || !canSync}
                className="flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-[11px] text-zinc-300 hover:border-indigo-400/40 hover:text-indigo-200 disabled:opacity-50"
                title={
                  canSync
                    ? "Заполнить реквизиты, допуски, проекты и историю участий с rusprofile.ru"
                    : "Укажите ИНН — по нему компания ищется на rusprofile.ru"
                }
              >
                <RefreshCw size={12} className={isBusy ? "animate-spin" : ""} />
                Обновить из rusprofile
              </button>
            )}
            {isAdmin && !profile.is_primary && (
              <button
                onClick={onMakePrimary}
                disabled={isBusy}
                className="rounded-full border border-white/10 p-1.5 text-zinc-500 hover:bg-white/5 hover:text-indigo-300 disabled:opacity-50"
                title="Сделать основной — с неё будет считаться AI-оценка"
              >
                <Star size={13} />
              </button>
            )}
            {isAdmin && (
              <button
                onClick={onEdit}
                className="rounded-full border border-white/10 p-1.5 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
                title="Изменить"
              >
                <Pencil size={13} />
              </button>
            )}
            {isAdmin && !profile.is_primary && (
              <button
                onClick={onAskDelete}
                disabled={isBusy}
                className="rounded-full border border-white/10 p-1.5 text-zinc-500 hover:bg-white/5 hover:text-red-400 disabled:opacity-50"
                title="Удалить"
              >
                <Trash2 size={13} />
              </button>
            )}
          </div>
        )}
      </div>

      {/* Ключевые цифры — видны и в свёрнутом состоянии */}
      <div className="relative grid grid-cols-2 gap-2 px-5 pb-4 sm:grid-cols-3 lg:grid-cols-5">
        <Kpi
          label="Стаж"
          value={profile.years_of_experience ? String(profile.years_of_experience) : "—"}
          unit={profile.years_of_experience ? plural(profile.years_of_experience, "год", "года", "лет") : undefined}
          hint={
            profile.registration_date
              ? `с ${new Date(profile.registration_date).toLocaleDateString("ru-RU")}`
              : "дата регистрации не указана"
          }
        />
        <Kpi
          label="Допуски"
          value={String(profile.licenses.length)}
          hint={profile.licenses.length ? "лицензии и сертификаты" : "не указаны"}
          tone={profile.licenses.length ? "emerald" : undefined}
        />
        <Kpi
          label="Проекты"
          value={String(profile.past_projects.length)}
          hint={profile.past_projects.length ? "реализованных" : "не указаны"}
          tone={profile.past_projects.length ? "amber" : undefined}
        />
        <Kpi
          label="Госзакупки"
          value={purchases ? `${purchases.wins}` : "—"}
          unit={purchases ? `побед · ${purchases.losses} проигр.` : undefined}
          hint={
            purchases
              ? `${purchases.fetched} ${plural(purchases.fetched, "закупка", "закупки", "закупок")} на rusprofile`
              : "обновите из rusprofile"
          }
          tone={purchases ? "sky" : undefined}
        />
        <Kpi
          label={`Выручка${finance.year ? ` ${finance.year}` : ""}`}
          value={finance.revenue ? finance.revenue.replace(/\s*руб\.?$/, "") : "—"}
          unit={finance.revenue ? "руб." : undefined}
          hint={finance.revenue_change ? `${finance.revenue_change} к прошлому году` : "нет данных"}
          tone={finance.revenue ? "violet" : undefined}
        />
      </div>

      {isExpanded && (
        <div className="relative grid gap-3 border-t border-white/[0.06] bg-black/20 px-5 py-4 lg:grid-cols-2">
          <SectionCard tone="indigo" icon={<FileText size={13} />} title="Реквизиты">
            <dl className="grid gap-x-6 gap-y-2.5 text-xs sm:grid-cols-2">
              <div className="sm:col-span-2">
                <Detail label="Юридическое наименование" value={profile.legal_name} />
              </div>
              <Detail label="ИНН" value={profile.inn} mono />
              <Detail label="КПП" value={profile.kpp} mono />
              <Detail label="ОГРН" value={profile.ogrn} mono />
              <Detail
                label="Дата регистрации"
                value={
                  profile.registration_date
                    ? new Date(profile.registration_date).toLocaleDateString("ru-RU")
                    : null
                }
              />
              <div className="sm:col-span-2">
                <Detail label="Юридический адрес" value={profile.legal_address} />
              </div>
              {dossier?.codes && Object.keys(dossier.codes).length > 0 && (
                <div className="sm:col-span-2">
                  <dt className="text-[11px] text-zinc-500">Коды статистики</dt>
                  <dd className="mt-1 flex flex-wrap gap-1.5">
                    {Object.entries(dossier.codes).map(([key, value]) => (
                      <span
                        key={key}
                        className="rounded-md border border-white/[0.08] bg-black/30 px-2 py-0.5 font-mono text-[10px] text-zinc-400"
                      >
                        {key.toUpperCase()} {value}
                      </span>
                    ))}
                  </dd>
                </div>
              )}
            </dl>
          </SectionCard>

          <SectionCard
            tone="emerald"
            icon={<ShieldCheck size={13} />}
            title="Допуски и лицензии"
            count={profile.licenses.length}
          >
            {profile.licenses.length === 0 ? (
              <EmptyNote>
                Не указаны — «Компетенции» посчитают, что формальных подтверждений нет.
              </EmptyNote>
            ) : (
              <ul className="space-y-1.5">
                {profile.licenses.map((license, index) => (
                  <li
                    key={index}
                    className="rounded-lg border border-white/[0.06] bg-black/25 px-3 py-2 text-xs"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="line-clamp-2 text-zinc-200">{license.name}</span>
                      {license.source === "rusprofile" && <SourceMark />}
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px] text-zinc-500">
                      {license.number && <span className="font-mono">№ {license.number}</span>}
                      {license.valid_until && <span>до {license.valid_until}</span>}
                      {license.issuer && (
                        <span className="line-clamp-1 max-w-[60%]">{license.issuer}</span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          <SectionCard
            tone="amber"
            icon={<Briefcase size={13} />}
            title="Реализованные проекты"
            count={profile.past_projects.length}
          >
            {profile.past_projects.length === 0 ? (
              <EmptyNote>Не указаны — «Задаче» не с чем сопоставлять предмет закупки.</EmptyNote>
            ) : (
              <ul className="space-y-1.5">
                {profile.past_projects.map((project, index) => (
                  <li
                    key={index}
                    className="rounded-lg border border-white/[0.06] bg-black/25 px-3 py-2 text-xs"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="line-clamp-2 text-zinc-200">{project.work_type}</span>
                      {project.year && (
                        <span className="shrink-0 rounded-md bg-amber-500/10 px-1.5 py-0.5 font-mono text-[10px] text-amber-300">
                          {project.year}
                        </span>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] text-zinc-500">
                      {project.customer && <span className="truncate">{project.customer}</span>}
                      {project.volume && <span className="text-zinc-300">{project.volume}</span>}
                      {project.source === "rusprofile" && <SourceMark />}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          <SectionCard
            tone="sky"
            icon={<Globe size={13} />}
            title="Досье rusprofile"
            extra={
              dossier ? (
                <span className="text-[10px] text-zinc-500">
                  {profile.rusprofile_synced_at
                    ? `обновлено ${formatDateTime(profile.rusprofile_synced_at)} · `
                    : ""}
                  <a
                    href={dossier.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-sky-300 hover:underline"
                  >
                    карточка на сайте
                  </a>
                </span>
              ) : undefined
            }
          >
            {dossier ? (
              <RusprofileDossierBlock dossier={dossier} />
            ) : (
              <EmptyNote>
                Досье ещё не загружалось. Нажмите «Обновить из rusprofile» — сайт отдаст
                руководителя, численность, финансы, учредителей и историю госзакупок.
              </EmptyNote>
            )}
          </SectionCard>
        </div>
      )}
    </div>
  );
}

type Tone = "indigo" | "emerald" | "amber" | "sky" | "violet";

const TONE_CARD: Record<Tone, string> = {
  indigo: "border-indigo-500/20 bg-indigo-500/[0.05]",
  emerald: "border-emerald-500/20 bg-emerald-500/[0.05]",
  amber: "border-amber-500/20 bg-amber-500/[0.05]",
  sky: "border-sky-500/20 bg-sky-500/[0.05]",
  violet: "border-violet-500/20 bg-violet-500/[0.05]",
};

const TONE_ICON: Record<Tone, string> = {
  indigo: "bg-indigo-500/15 text-indigo-300",
  emerald: "bg-emerald-500/15 text-emerald-300",
  amber: "bg-amber-500/15 text-amber-300",
  sky: "bg-sky-500/15 text-sky-300",
  violet: "bg-violet-500/15 text-violet-300",
};

const TONE_VALUE: Record<Tone, string> = {
  indigo: "text-indigo-200",
  emerald: "text-emerald-200",
  amber: "text-amber-200",
  sky: "text-sky-200",
  violet: "text-violet-200",
};

/** Плитка с одной цифрой — как в сводках дашбордов: подпись, крупное число, пояснение. */
function Kpi({
  label,
  value,
  unit,
  hint,
  tone,
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  tone?: Tone;
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-black/30 px-3.5 py-2.5">
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className="mt-0.5 flex items-baseline gap-1.5">
        <span className={`text-xl font-semibold leading-tight ${tone ? TONE_VALUE[tone] : "text-zinc-100"}`}>
          {value}
        </span>
        {unit && <span className="text-[11px] text-zinc-500">{unit}</span>}
      </div>
      {hint && <div className="mt-0.5 truncate text-[10px] text-zinc-600">{hint}</div>}
    </div>
  );
}

/** Информационный блок компании в своём оттенке — чтобы блоки читались как разные сущности. */
function SectionCard({
  tone,
  icon,
  title,
  count,
  extra,
  children,
}: {
  tone: Tone;
  icon: React.ReactNode;
  title: string;
  count?: number;
  extra?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className={`rounded-xl border p-4 ${TONE_CARD[tone]}`}>
      <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h4 className="flex items-center gap-2 text-xs font-semibold text-zinc-100">
          <span className={`flex h-6 w-6 items-center justify-center rounded-md ${TONE_ICON[tone]}`}>
            {icon}
          </span>
          {title}
          {typeof count === "number" && count > 0 && (
            <span className="rounded-full bg-white/[0.06] px-1.5 py-px text-[10px] font-normal text-zinc-400">
              {count}
            </span>
          )}
        </h4>
        {extra}
      </header>
      {children}
    </section>
  );
}

function EmptyNote({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] leading-relaxed text-zinc-500">{children}</p>;
}

/** Пометка «с rusprofile» у допуска или проекта: такие записи ведёт синхронизация, а не человек. */
function SourceMark() {
  return (
    <span
      className="shrink-0 rounded border border-sky-500/20 bg-sky-500/[0.08] px-1 py-px text-[10px] text-sky-300/90"
      title="Запись получена с rusprofile.ru и обновляется синхронизацией"
    >
      rusprofile
    </span>
  );
}

/**
 * Содержимое блока «Досье rusprofile»: цифры плитками (численность, прибыль, капитал,
 * заказчики), ниже — факты списком. Те же факты уходят в промпт AI-оценки: «Компетенции»
 * сверяют с ними требования к участнику.
 */
function RusprofileDossierBlock({ dossier }: { dossier: RusprofileDossier }) {
  const finance = dossier.finance ?? {};
  const summary = dossier.purchases_summary ?? {};
  const tiles: Array<{ label: string; value: string; hint?: string }> = [];
  if (dossier.headcount) {
    tiles.push({
      label: "Численность",
      value: `${dossier.headcount}`,
      hint: dossier.headcount_year ? `чел. в ${dossier.headcount_year}` : "чел.",
    });
  }
  if (finance.profit) {
    tiles.push({ label: "Прибыль", value: finance.profit.replace(/\s*руб\.?$/, ""), hint: finance.profit_change ? `${finance.profit_change} к прошлому году` : "руб." });
  }
  if (dossier.authorized_capital) {
    tiles.push({ label: "Уставный капитал", value: dossier.authorized_capital.replace(/\s*руб\.?$/, ""), hint: "руб." });
  }
  if (summary.contracts_count) {
    tiles.push({
      label: "Контрактов",
      value: String(summary.contracts_count),
      hint: summary.contracts_sum ? `на ${summary.contracts_sum}` : undefined,
    });
  }

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
        ? `${dossier.main_okved_code} ${dossier.main_okved_name ?? ""}${dossier.okved_count ? ` (всего ${dossier.okved_count})` : ""}`
        : null,
    ],
    ["Реестр МСП", dossier.msp_status],
    ["Налоговый режим", dossier.tax_regime],
    ["Контакты", [...(dossier.phones ?? []), ...(dossier.emails ?? []), dossier.website].filter(Boolean).join(" · ") || null],
    ["Исполнительные производства", dossier.enforcement],
    ["Проверки", dossier.inspections],
    ["Лицензии на сайте", dossier.licenses_note],
  ];
  const visible = facts.filter(([, value]) => value);
  const ratings = Object.entries(finance.ratings ?? {}).filter(([, v]) => v);

  return (
    <div className="space-y-3">
      {dossier.data_hidden && (
        <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.07] px-3 py-2 text-[11px] text-amber-200/90">
          Часть данных на сайте скрыта подпиской — проверьте, действует ли учётная запись.
        </div>
      )}

      {tiles.length > 0 && (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {tiles.map((tile) => (
            <div key={tile.label} className="rounded-lg border border-white/[0.06] bg-black/30 px-3 py-2">
              <div className="text-[10px] uppercase tracking-wider text-zinc-500">{tile.label}</div>
              <div className="mt-0.5 text-base font-semibold text-sky-100">{tile.value}</div>
              {tile.hint && <div className="text-[10px] text-zinc-600">{tile.hint}</div>}
            </div>
          ))}
        </div>
      )}

      {ratings.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {ratings.map(([label, value]) => (
            <span
              key={label}
              className="rounded-full border border-white/[0.08] bg-black/30 px-2.5 py-0.5 text-[10px] text-zinc-400"
            >
              {label}: <span className="text-zinc-200">{value}</span>
            </span>
          ))}
        </div>
      )}

      {summary.top_customers && summary.top_customers.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">Крупные заказчики</div>
          <div className="flex flex-wrap gap-1.5">
            {summary.top_customers.map((customer) => (
              <span
                key={customer.name}
                className="rounded-lg border border-sky-500/20 bg-sky-500/[0.08] px-2.5 py-1 text-[11px] text-sky-100"
                title={customer.sum ?? undefined}
              >
                {customer.name}
                {customer.purchases ? (
                  <span className="ml-1.5 text-[10px] text-sky-300/70">{customer.purchases}</span>
                ) : null}
              </span>
            ))}
          </div>
        </div>
      )}

      {visible.length > 0 && (
        <dl className="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
          {visible.map(([label, value]) => (
            <div key={label}>
              <dt className="text-[11px] text-zinc-500">{label}</dt>
              <dd className="line-clamp-3 text-zinc-300">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      {dossier.summary_text && (
        <p
          className="line-clamp-4 border-t border-white/[0.06] pt-2 text-[11px] leading-relaxed text-zinc-500"
          title={dossier.summary_text}
        >
          {dossier.summary_text}
        </p>
      )}
    </div>
  );
}

function Detail({ label, value, mono }: { label: string; value: string | null; mono?: boolean }) {
  return (
    <div>
      <dt className="text-[11px] text-zinc-500">{label}</dt>
      <dd className={`text-zinc-200 ${mono ? "font-mono" : ""}`}>{value?.trim() ? value : "—"}</dd>
    </div>
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
 * тендерам сразу и делается отдельной кнопкой в списке.
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
      setError(
        "Укажите ИНН, наименование или вставьте ссылку на карточку rusprofile — по пустому " +
          "запросу реестр ничего не найдёт.",
      );
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
      `Данные подставлены из источника «${candidate.source === "rusprofile" ? "rusprofile.ru" : "ЕГРЮЛ"}». ` +
        "Проверьте их и нажмите «Сохранить» — до этого они никуда не записаны.",
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-8"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-white/[0.08] px-5 py-3">
          <h2 className="text-sm text-zinc-200">
            {profile ? "Компания" : "Новая компания"}
            {profile?.is_primary && (
              <span className="ml-2 rounded-md border border-indigo-500/30 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-300">
                основная
              </span>
            )}
          </h2>
          <button
            onClick={onClose}
            className="rounded-lg p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
            title="Закрыть"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {error && (
            <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
              {error}
            </div>
          )}
          {notice && (
            <div className="mb-4 rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] px-4 py-2.5 text-sm text-indigo-300">
              {notice}
            </div>
          )}

          <div className="mb-5">
            <h3 className="mb-2 text-xs font-medium text-zinc-300">Юридические данные</h3>

            {isAdmin && (
              <div className="mb-3 rounded-lg border border-white/10 bg-black/20 p-3">
                <div className="flex flex-wrap items-end gap-2">
                  <label className="min-w-[240px] flex-1 text-xs text-zinc-400">
                    Автопоиск
                    <input
                      value={lookupQuery}
                      onChange={(e) => setLookupQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void lookup();
                      }}
                      placeholder="ИНН, название или ссылка https://www.rusprofile.ru/id/…"
                      className={inputClass}
                    />
                  </label>
                  <button
                    onClick={() => void lookup()}
                    disabled={isLookingUp}
                    className="flex items-center gap-1 rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
                  >
                    {isLookingUp ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <Search size={12} />
                    )}
                    Найти
                  </button>
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-zinc-600">
                  ИНН, ОГРН или название ищутся в ЕГРЮЛ. Ссылка на карточку rusprofile
                  разбирается напрямую — только этот путь даёт КПП, в выдаче ЕГРЮЛ его нет.
                  Найденное подставляется в поля и никуда не записывается, пока вы не нажмёте
                  «Сохранить».
                </p>
              </div>
            )}

            {candidates && candidates.length > 1 && (
              <div className="mb-3 space-y-1 rounded-lg border border-white/10 bg-black/20 p-2">
                <p className="text-[11px] text-zinc-500">
                  Найдено несколько организаций — выберите свою:
                </p>
                {candidates.map((candidate, index) => (
                  <button
                    key={`${candidate.inn}-${index}`}
                    onClick={() => applyCandidate(candidate)}
                    className="block w-full rounded-md px-2 py-1 text-left text-xs text-zinc-300 hover:bg-white/5"
                  >
                    {candidate.legal_name ?? "Без наименования"}
                    <span className="text-zinc-600"> — ИНН {candidate.inn ?? "—"}</span>
                  </button>
                ))}
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block text-xs text-zinc-400">
                Юридическое наименование
                <input
                  value={legalName}
                  disabled={!isAdmin}
                  onChange={(e) => setLegalName(e.target.value)}
                  placeholder="ООО «МИРТЕК»"
                  className={inputClass}
                />
              </label>
              <label className="block text-xs text-zinc-400">
                ИНН
                <input
                  value={inn}
                  disabled={!isAdmin}
                  onChange={(e) => setInn(e.target.value)}
                  placeholder="6167102308"
                  className={inputClass}
                />
                <span className="mt-1 block text-[11px] text-zinc-600">
                  По ИНН основной компании синхронизируется история участий — без него
                  измерение «История» не считается.
                </span>
              </label>
              <label className="block text-xs text-zinc-400">
                КПП
                <input
                  value={kpp}
                  disabled={!isAdmin}
                  onChange={(e) => setKpp(e.target.value)}
                  placeholder="263501001"
                  className={inputClass}
                />
                <span className="mt-1 block text-[11px] text-zinc-600">
                  Нужен в реквизитах заявки. В ЕГРЮЛ его нет — берётся с rusprofile или
                  вводится вручную.
                </span>
              </label>
              <label className="block text-xs text-zinc-400">
                ОГРН
                <input
                  value={ogrn}
                  disabled={!isAdmin}
                  onChange={(e) => setOgrn(e.target.value)}
                  placeholder="1086167002204"
                  className={inputClass}
                />
              </label>
              <label className="block text-xs text-zinc-400">
                Дата регистрации
                <input
                  type="date"
                  value={registrationDate}
                  disabled={!isAdmin}
                  onChange={(e) => setRegistrationDate(e.target.value)}
                  className={inputClass}
                />
              </label>
              <label className="block text-xs text-zinc-400 sm:col-span-2">
                Юридический адрес
                <input
                  value={legalAddress}
                  disabled={!isAdmin}
                  onChange={(e) => setLegalAddress(e.target.value)}
                  placeholder="г. Ростов-на-Дону, ..."
                  className={inputClass}
                />
              </label>
            </div>
          </div>

          <label className="mb-5 block max-w-xs text-xs text-zinc-400">
            Стаж работы компании, лет
            <input
              type="number"
              min={0}
              max={200}
              value={years}
              disabled={!isAdmin}
              onChange={(e) => setYears(e.target.value)}
              placeholder="18"
              className={inputClass}
            />
          </label>

          <div className="mb-5">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-xs font-medium text-zinc-300">Допуски и лицензии</h3>
              {isAdmin && (
                <button
                  onClick={() => setLicenses((prev) => [...prev, { name: "" }])}
                  className="flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1 text-xs text-zinc-300 hover:bg-white/5"
                >
                  <Plus size={12} />
                  Добавить
                </button>
              )}
            </div>
            {licenses.length === 0 && (
              <p className="text-xs text-zinc-600">
                Допуски не указаны — измерение «Компетенции» будет считать, что формальных
                подтверждений у компании нет.
              </p>
            )}
            <div className="space-y-2">
              {licenses.map((license, index) => (
                <div key={index} className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr_auto]">
                  <input
                    value={license.name}
                    disabled={!isAdmin}
                    onChange={(e) => updateLicense(index, { name: e.target.value })}
                    placeholder="Наименование (СРО, лицензия ФСБ, ISO 9001)"
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
                    <button
                      onClick={() =>
                        setLicenses((prev) => prev.filter((_, i) => i !== index))
                      }
                      className="mt-1.5 rounded-lg border border-white/10 px-2 text-zinc-500 hover:bg-white/5 hover:text-red-400"
                      title="Удалить"
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="mb-5">
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-xs font-medium text-zinc-300">Реализованные проекты</h3>
              {isAdmin && (
                <button
                  onClick={() => setProjects((prev) => [...prev, { work_type: "" }])}
                  className="flex items-center gap-1 rounded-lg border border-white/10 px-2.5 py-1 text-xs text-zinc-300 hover:bg-white/5"
                >
                  <Plus size={12} />
                  Добавить
                </button>
              )}
            </div>
            {projects.length === 0 && (
              <p className="text-xs text-zinc-600">
                Проекты не указаны — измерению «Задача» не с чем сопоставлять предмет закупки.
              </p>
            )}
            <div className="space-y-2">
              {projects.map((project, index) => (
                <div key={index} className="grid gap-2 sm:grid-cols-[2fr_2fr_1fr_1fr_auto]">
                  <input
                    value={project.work_type}
                    disabled={!isAdmin}
                    onChange={(e) => updateProject(index, { work_type: e.target.value })}
                    placeholder="Тип работ (поставка ПУ, монтаж, АСКУЭ)"
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
                    <button
                      onClick={() =>
                        setProjects((prev) => prev.filter((_, i) => i !== index))
                      }
                      className="mt-1.5 rounded-lg border border-white/10 px-2 text-zinc-500 hover:bg-white/5 hover:text-red-400"
                      title="Удалить"
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          <p className="text-[11px] leading-relaxed text-zinc-600">
            Уже посчитанные оценки после правки профиля не пересчитываются автоматически —
            каждая хранит снимок профиля на момент расчёта и остаётся объяснимой. Нужные
            тендеры пересчитываются кнопкой в карточке.
          </p>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-white/[0.08] px-5 py-3">
          <button
            onClick={onClose}
            className="rounded-lg border border-white/10 px-3 py-2 text-sm text-zinc-300 hover:bg-white/5"
          >
            {isAdmin ? "Отмена" : "Закрыть"}
          </button>
          {isAdmin && (
            <button
              onClick={() => void save()}
              disabled={isSaving}
              className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {isSaving && <Loader2 size={14} className="animate-spin" />}
              Сохранить
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
