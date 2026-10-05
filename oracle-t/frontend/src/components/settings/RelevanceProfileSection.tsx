import { useCallback, useEffect, useState } from "react";
import { Loader2, Plus, RefreshCw, Sparkles, X } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  AiCheckResult,
  CollectionTerm,
  RelevanceRecalcResult,
  Source,
  UserRelevanceProfile,
} from "../../api/types";
import { CATALOG_SOURCE_TYPES } from "../../api/types";
import { ProfilesManager } from "../RelevanceProfilesModal";
import { SelectionGuide } from "../SelectionGuide";
import {
  SettingCard,
  SettingsGroup,
  SettingsNotice,
  SettingsPanel,
  primaryButtonClass,
  secondaryButtonClass,
} from "./ui";

/**
 * «Отбор тендеров» (с 05.10.2026; раньше — «Профиль отбора» с группами потребностей).
 *
 * Одна логика вместо двух: что СОБИРАТЬ с площадок (фразы сбора) и как ОТБИРАТЬ собранное
 * (профили — общие и личные, одним движком). Сверху — схема слоёв, чтобы было видно, где
 * настраивается каждый и почему закупка могла не дойти до списка.
 */

/** Фразы сбора: что система спрашивает у площадок. Меняет администратор. */
function CollectionTerms({ isAdmin }: { isAdmin: boolean }) {
  const [terms, setTerms] = useState<CollectionTerm[] | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setTerms(await api.get<CollectionTerm[]>("/relevance/terms"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить фразы сбора");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    const phrase = draft.trim();
    if (!phrase) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/relevance/terms", { phrase });
      setDraft("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось добавить фразу");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (term: CollectionTerm) => {
    try {
      await api.patch(`/relevance/terms/${term.id}`, { is_active: !term.is_active });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить фразу");
    }
  };

  const remove = async (term: CollectionTerm) => {
    if (!window.confirm(`Удалить фразу «${term.phrase}»? Закупки по ней перестанут собираться.`))
      return;
    try {
      await api.delete(`/relevance/terms/${term.id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить фразу");
    }
  };

  return (
    <div className="space-y-3 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
      {terms === null ? (
        <div className="flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 size={14} className="animate-spin" />
          Загрузка…
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {terms.map((term) => (
            <span
              key={term.id}
              className={`inline-flex items-center gap-1 rounded-md border py-0.5 pl-2 pr-1 text-[12px] ${
                term.is_active
                  ? "border-white/10 bg-white/[0.05] text-zinc-200"
                  : "border-white/[0.06] text-zinc-600 line-through"
              }`}
            >
              {isAdmin ? (
                <button
                  type="button"
                  onClick={() => void toggle(term)}
                  title={term.is_active ? "Выключить: площадки по ней не опрашиваются" : "Включить"}
                  className="hover:text-white"
                >
                  {term.phrase}
                </button>
              ) : (
                term.phrase
              )}
              {isAdmin && (
                <button
                  type="button"
                  onClick={() => void remove(term)}
                  aria-label={`Удалить «${term.phrase}»`}
                  className="rounded p-0.5 text-zinc-500 hover:bg-white/10 hover:text-zinc-100"
                >
                  <X size={11} />
                </button>
              )}
            </span>
          ))}
          {terms.length === 0 && (
            <span className="text-sm text-zinc-500">Фраз нет — сбор ничего не ищет.</span>
          )}
        </div>
      )}
      {isAdmin && (
        <div className="flex gap-2">
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void add();
              }
            }}
            placeholder="Например: поверка приборов учета электроэнергии"
            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
          />
          <button
            onClick={() => void add()}
            disabled={busy || !draft.trim()}
            className={secondaryButtonClass}
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
            Добавить
          </button>
        </div>
      )}
      <p className="text-xs text-zinc-500">
        Обычный текст, без звёздочек: так ищут сами площадки. Каждая фраза — отдельный обход
        выдачи на каждой площадке, поэтому их немного. Новая фраза действует со следующего сбора.
        {isAdmin ? " Щелчок по фразе выключает её, не удаляя." : " Меняет администратор."}
      </p>
    </div>
  );
}

export function RelevanceProfileSection({ isAdmin }: { isAdmin: boolean }) {
  const [profiles, setProfiles] = useState<UserRelevanceProfile[] | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [pending, setPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadProfiles = useCallback(async () => {
    try {
      setProfiles(await api.get<UserRelevanceProfile[]>("/relevance/profiles"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить профили");
    }
  }, []);

  useEffect(() => {
    void loadProfiles();
    void api
      .get<Source[]>("/sources")
      .then((all) => setSources(all.filter((s) => !CATALOG_SOURCE_TYPES.includes(s.type))))
      .catch(() => setSources([]));
    void api
      .get<{ pending: number }>("/relevance/ai-pending")
      .then(({ pending: left }) => setPending(left))
      .catch(() => setPending(null));
  }, [loadProfiles]);

  /** Проверка моделью пачками: каждая проверка — вызов модели, и «разобрать всё» одним
   * запросом упёрлось бы в таймаут. Пачка за нажатие, остаток показан на кнопке. */
  const runAiCheck = async () => {
    setIsChecking(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.post<AiCheckResult>("/relevance/ai-check?limit=50", {});
      setPending(result.pending);
      setNotice(
        `Проверено моделью: ${result.checked}. Наша тематика: ${result.relevant}, ` +
          `отклонено: ${result.rejected}` +
          (result.failed ? `, сбоев: ${result.failed}` : "") +
          (result.pending ? `. Осталось разобрать: ${result.pending}.` : ". Архив разобран."),
      );
      if (result.messages.length > 0) setError(result.messages.join(" "));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Проверка моделью не удалась");
    } finally {
      setIsChecking(false);
    }
  };

  const recalculate = async () => {
    setIsRecalculating(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.post<RelevanceRecalcResult>("/relevance/recalculate", {});
      setNotice(
        `Пересчитано закупок: ${result.processed}. Прошли общие профили: ${result.passed}, ` +
          `отсеяно: ${result.rejected}.`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Пересчёт не удался");
    } finally {
      setIsRecalculating(false);
    }
  };

  return (
    <SettingsPanel
      title="Отбор тендеров"
      description={
        <>
          Как закупка попадает в ваш список: что система ищет на площадках, какими профилями
          отбирает собранное и что отсекает модель. Отсеянное не удаляется — его видно, если
          снять профиль или проверку моделью.
        </>
      }
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
      {notice && <SettingsNotice tone="success">{notice}</SettingsNotice>}

      <SettingsGroup
        id="relevance-guide"
        label="Как отбираются тендеры"
        hint="Слои — в том порядке, в каком их применяет система. Числа по текущей выдаче — в строке над списком тендеров."
      >
        <SelectionGuide />
      </SettingsGroup>

      <SettingsGroup
        id="relevance-terms"
        label="Что ищем на площадках"
        hint="Фразы сбора. Закупка, которой нет ни по одной фразе, в систему не попадёт — профили её уже не увидят."
      >
        <CollectionTerms isAdmin={isAdmin} />
      </SettingsGroup>

      <SettingsGroup
        id="relevance-profiles"
        label={`Профили отбора${profiles ? ` · ${profiles.length}` : ""}`}
        hint="Общие действуют у всех по умолчанию (меняет администратор), личные заводит любой сотрудник. Выбор профилей для списка — в меню «Профиль» на странице тендеров."
      >
        <div className="overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.02]">
          {profiles === null ? (
            <div className="flex items-center gap-2 p-5 text-sm text-zinc-500">
              <Loader2 size={14} className="animate-spin" />
              Загрузка профилей…
            </div>
          ) : (
            <ProfilesManager
              profiles={profiles}
              sources={sources}
              onChanged={() => void loadProfiles()}
            />
          )}
        </div>
      </SettingsGroup>

      {isAdmin && (
        <SettingsGroup id="relevance-actions" label="Проверка моделью и пересчёт">
          <SettingCard
            id="relevance-ai-check"
            icon={<Sparkles size={18} />}
            title="Проверка моделью"
            description="Новые закупки площадок модель проверяет сама при сборе. Кнопка разбирает накопленный архив — по 50 закупок за нажатие, в том числе канал Госплана."
            control={
              <button
                onClick={() => void runAiCheck()}
                disabled={isChecking || pending === 0}
                className={primaryButtonClass}
              >
                {isChecking ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {pending === 0
                  ? "Всё проверено"
                  : `Проверить${pending === null ? "" : ` (${pending})`}`}
              </button>
            }
          />
          <SettingCard
            id="relevance-recalculate"
            icon={<RefreshCw size={18} />}
            title="Пересчёт отметок общих профилей"
            description="После правки общего профиля отметки пересчитываются сами. Кнопка — на случай, если базу правили вручную."
            control={
              <button
                onClick={() => void recalculate()}
                disabled={isRecalculating}
                className={secondaryButtonClass}
              >
                {isRecalculating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <RefreshCw size={13} />
                )}
                Пересчитать
              </button>
            }
          />
        </SettingsGroup>
      )}
    </SettingsPanel>
  );
}
