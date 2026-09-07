import { useEffect, useState } from "react";
import { Loader2, Plus, Search, Trash2 } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  CompanyLicense,
  CompanyPastProject,
  CompanyProfile,
  CompanyProfileUpdate,
  EgrulCandidate,
} from "../../api/types";

/**
 * Профиль компании (раздел 5.6 «Настройки», раздел 7 ТЗ).
 *
 * Вход главной метрики: измерения «Задача» и «Компетенции» AI-оценки (раздел 5.5.1)
 * сравнивают требования закупки не с каталогом приборов, а с самой компанией — её
 * допусками, стажем и выполненными проектами. Пока раздел пуст, оценка не считается вовсе,
 * и карточка тендера прямо об этом говорит.
 *
 * Форма правится целиком и сохраняется одной кнопкой: лицензий и проектов немного, а
 * построчное сохранение здесь означало бы CRUD ради двух десятков строк.
 *
 * Юридические данные (наименование, ИНН, КПП, ОГРН, дата регистрации, адрес) ищутся
 * автопоиском, но **не сохраняются сами**: найденное подставляется в поля, а записывает его в
 * профиль человек кнопкой «Сохранить». Профиль подаётся в обоснования AI-оценки, и
 * непроверенный результат внешнего сервиса там неотличим от подтверждённого факта.
 *
 * Источников автопоиска два, и выбираются они по виду запроса: ссылка на карточку
 * rusprofile.ru разбирается напрямую (единственный путь, дающий КПП, — в выдаче ЕГРЮЛ его
 * нет), всё остальное — ИНН, ОГРН, название — уходит в ЕГРЮЛ как в первоисточник.
 *
 * ИНН здесь — не просто реквизит: по нему синхронизируется история участий (вкладка рядом),
 * то есть без него не считается измерение «История».
 */

const inputClass =
  "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

export function CompanyProfileForm({
  isAdmin,
  onProfileLoaded,
}: {
  isAdmin: boolean;
  /** Соседняя вкладка «История участий» показывает кнопку синхронизации только когда ИНН
   * заполнен — иначе синхронизировать не по чему. */
  onProfileLoaded?: (profile: CompanyProfile | null) => void;
}) {
  // Загруженный профиль наружу отдаётся колбэком, а не хранится здесь: состояние
  // «заполнен / есть ли ИНН» нужно обёртке «Моя компания» и соседней вкладке, и вторая
  // копия того же факта в этой форме разошлась бы с первой.
  const [legalName, setLegalName] = useState("");
  const [inn, setInn] = useState("");
  const [kpp, setKpp] = useState("");
  const [ogrn, setOgrn] = useState("");
  const [registrationDate, setRegistrationDate] = useState("");
  const [legalAddress, setLegalAddress] = useState("");
  const [lookupQuery, setLookupQuery] = useState("");
  const [candidates, setCandidates] = useState<EgrulCandidate[] | null>(null);
  const [isLookingUp, setIsLookingUp] = useState(false);
  const [years, setYears] = useState<string>("");
  const [licenses, setLicenses] = useState<CompanyLicense[]>([]);
  const [projects, setProjects] = useState<CompanyPastProject[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    try {
      const data = await api.get<CompanyProfile | null>("/company-profile");
      setLegalName(data?.legal_name ?? "");
      setInn(data?.inn ?? "");
      setKpp(data?.kpp ?? "");
      setOgrn(data?.ogrn ?? "");
      setRegistrationDate(data?.registration_date ?? "");
      setLegalAddress(data?.legal_address ?? "");
      setYears(data?.years_of_experience ? String(data.years_of_experience) : "");
      setLicenses(data?.licenses ?? []);
      setProjects(data?.past_projects ?? []);
      onProfileLoaded?.(data);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить профиль компании");
    }
  };

  useEffect(() => {
    void load();
  }, []);

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
        "Проверьте их и нажмите «Сохранить профиль» — до этого они никуда не записаны.",
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
      const updated = await api.put<CompanyProfile>("/company-profile", payload);
      setLicenses(updated.licenses);
      setProjects(updated.past_projects);
      setCandidates(null);
      onProfileLoaded?.(updated);
      setNotice(
        updated.is_filled
          ? "Профиль сохранён — AI-оценку по профилю теперь можно считать."
          : "Профиль сохранён, но пуст: без стажа, допусков и проектов оценка не считается.",
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить профиль");
    } finally {
      setIsSaving(false);
    }
  };

  const updateLicense = (index: number, patch: Partial<CompanyLicense>) =>
    setLicenses((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  const updateProject = (index: number, patch: Partial<CompanyPastProject>) =>
    setProjects((prev) => prev.map((item, i) => (i === index ? { ...item, ...patch } : item)));

  return (
    <div>
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
                  «Сохранить профиль».
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
                  По нему синхронизируется история участий — без ИНН измерение «История» не
                  считается.
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

          {isAdmin ? (
            <button
              onClick={() => void save()}
              disabled={isSaving}
              className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {isSaving && <Loader2 size={14} className="animate-spin" />}
              Сохранить профиль
            </button>
          ) : (
            <p className="text-xs text-zinc-600">
              Профиль правит администратор: он влияет на оценку всех тендеров сразу.
            </p>
          )}

          <p className="mt-3 text-[11px] text-zinc-600">
            Уже посчитанные оценки после правки профиля не пересчитываются автоматически —
            каждая хранит снимок профиля на момент расчёта и остаётся объяснимой. Нужные
            тендеры пересчитываются кнопкой в карточке.
          </p>
      </div>
    </div>
  );
}
