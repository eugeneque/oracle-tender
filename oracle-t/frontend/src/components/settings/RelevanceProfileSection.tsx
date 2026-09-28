import { useEffect, useState } from "react";
import { ChevronDown, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { ApiError, api } from "../../api/client";
import {
  SettingCard,
  SettingsGroup,
  SettingsNotice,
  SettingsPanel,
  primaryButtonClass,
  secondaryButtonClass,
} from "./ui";
import type {
  AiCheckResult,
  KeywordGroup,
  RelevanceProfile,
  RelevanceRecalcResult,
} from "../../api/types";

/**
 * Профиль релевантности (раздел 5.1.1 ТЗ) — что вообще считается «нашим» тендером.
 *
 * Раздел нужен прежде всего как ответ на вопрос «почему тендеров мало» (или «почему много
 * мусора»): охват системы перестал быть константой в коде и стал настройкой, и её должно
 * быть видно. Список поисковых фраз показан отдельно от групп — именно ими система ходит на
 * площадки, и расхождение между ожиданием и этим списком объясняет пробелы в сборе.
 *
 * Правка групп меняет только будущие сборы, поэтому рядом — кнопка пересчёта: без неё
 * отметки на уже собранных тендерах остались бы от прежних настроек, и список показывал бы
 * одно, а профиль означал другое.
 */

const KEYWORD_HINT =
  "Синтаксис: слово* — по основе слова; (слово1* слово2*)~N — слова в пределах N слов друг от друга.";

function Chips({ items, tone }: { items: string[]; tone: "positive" | "negative" | "muted" }) {
  const className =
    tone === "positive"
      ? "border-indigo-500/25 bg-indigo-500/[0.07] text-indigo-200/90"
      : tone === "negative"
        ? "border-red-500/25 bg-red-500/[0.06] text-red-300/90"
        : "border-white/10 bg-white/[0.03] text-zinc-400";
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item, index) => (
        <span
          key={`${item}-${index}`}
          className={`rounded-md border px-1.5 py-0.5 text-[11px] ${className}`}
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function GroupRow({ group }: { group: KeywordGroup }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div
      className={`rounded-2xl border transition-colors ${
        isOpen ? "border-white/[0.12] bg-white/[0.04]" : "border-white/[0.08] bg-white/[0.03] hover:border-white/[0.12]"
      }`}
    >
      <button
        onClick={() => setIsOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left"
      >
        <span className="text-sm font-medium text-zinc-100">{group.name}</span>
        <span className="flex items-center gap-2 text-xs text-zinc-500">
          {group.keywords.length} ключей
          {group.exclusion_keywords.length > 0 && `, ${group.exclusion_keywords.length} исключений`}
          <ChevronDown
            size={14}
            className={`transition-transform ${isOpen ? "rotate-180" : ""}`}
          />
        </span>
      </button>
      {isOpen && (
        <div className="space-y-3 border-t border-white/[0.06] px-5 py-4">
          <div>
            <div className="mb-1 text-[11px] text-zinc-500">Ключевые слова</div>
            <Chips items={group.keywords} tone="positive" />
          </div>
          {group.exclusion_keywords.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] text-zinc-500">
                Исключения — встретилось, и группа не срабатывает
              </div>
              <Chips items={group.exclusion_keywords} tone="negative" />
            </div>
          )}
          {group.okpd2_codes && group.okpd2_codes.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] text-zinc-500">Коды ОКПД2 этой группы</div>
              <Chips items={group.okpd2_codes} tone="muted" />
            </div>
          )}
          {group.search_queries.length > 0 && (
            <div>
              <div className="mb-1 text-[11px] text-zinc-500">
                Фразы для поиска на площадках
              </div>
              <Chips items={group.search_queries} tone="muted" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function RelevanceProfileSection({ isAdmin }: { isAdmin: boolean }) {
  const [profile, setProfile] = useState<RelevanceProfile | null>(null);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [pending, setPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (profile) return;
    void (async () => {
      try {
        setProfile(await api.get<RelevanceProfile>("/relevance"));
        const { pending: left } = await api.get<{ pending: number }>("/relevance/ai-pending");
        setPending(left);
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить профиль");
      }
    })();
  }, [profile]);

  /** ИИ-отбор пачками: каждая проверка — вызов модели, и «разобрать всё» одним запросом
   * упёрлось бы в таймаут. Пачка за нажатие, остаток показан на кнопке. */
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
        `Пересчитано тендеров: ${result.processed}. Прошли профиль: ${result.passed}, ` +
          `отсеяно: ${result.rejected}. Отсеянные не удалены — они видны в списке, если снять ` +
          "галочку «Только прошедшие профиль релевантности».",
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Пересчёт не удался");
    } finally {
      setIsRecalculating(false);
    }
  };

  return (
    <SettingsPanel
      title="Профиль отбора"
      description={
        <>
          Что система считает «нашим» тендером: группы потребностей с ключевыми словами и
          исключениями. Это дешёвый фильтр до ИИ-анализа (раздел 5.1.1 ТЗ); не прошедшие его
          закупки не удаляются, а скрываются в списке переключателем «по профилю».
        </>
      }
    >
      {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
      {notice && <SettingsNotice tone="success">{notice}</SettingsNotice>}

      {isAdmin && (
        <SettingsGroup id="relevance-actions" label="Действия">
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
            title="Пересчёт по собранным тендерам"
            description="Правка групп влияет только на будущий сбор. Пересчёт применяет текущий профиль ко всем уже собранным закупкам."
            control={
              <button
                onClick={() => void recalculate()}
                disabled={isRecalculating}
                className={secondaryButtonClass}
              >
                {isRecalculating ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                Пересчитать
              </button>
            }
          />
        </SettingsGroup>
      )}

      {!profile && !error && (
        <div className="flex items-center gap-2 text-sm text-zinc-500">
          <Loader2 size={14} className="animate-spin" />
          Загрузка профиля…
        </div>
      )}

      {profile && (
        <>
          <SettingsGroup
            id="relevance-queries"
            label={`Поисковые фразы · ${profile.search_queries.length}`}
            hint="Этими фразами система ищет на площадках — от списка напрямую зависит, что вообще попадёт в базу."
          >
            <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
              <Chips items={profile.search_queries} tone="muted" />
            </div>
          </SettingsGroup>

          <SettingsGroup
            id="relevance-groups"
            label={`Группы потребностей · ${profile.groups.length}`}
            hint={`${KEYWORD_HINT} Группа срабатывает, если совпал хотя бы один ключ и не совпало ни одно исключение.`}
          >
            {profile.groups.map((group) => (
              <GroupRow key={group.id} group={group} />
            ))}
          </SettingsGroup>
        </>
      )}
    </SettingsPanel>
  );
}
