import { useEffect, useState } from "react";
import { ChevronDown, Loader2, RefreshCw, Sparkles } from "lucide-react";

import { ApiError, api } from "../../api/client";
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
    <div className="rounded-lg border border-white/[0.07] bg-white/[0.02]">
      <button
        onClick={() => setIsOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left"
      >
        <span className="text-xs text-zinc-200">{group.name}</span>
        <span className="flex items-center gap-2 text-[11px] text-zinc-600">
          {group.keywords.length} ключей
          {group.exclusion_keywords.length > 0 && `, ${group.exclusion_keywords.length} исключений`}
          <ChevronDown
            size={14}
            className={`transition-transform ${isOpen ? "rotate-180" : ""}`}
          />
        </span>
      </button>
      {isOpen && (
        <div className="space-y-2 border-t border-white/[0.06] px-3 py-2.5">
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
  const [isExpanded, setIsExpanded] = useState(false);
  const [profile, setProfile] = useState<RelevanceProfile | null>(null);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [pending, setPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!isExpanded || profile) return;
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
  }, [isExpanded, profile]);

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
        `Проверено моделью: ${result.checked}. Подобрано: ${result.relevant}, ` +
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
    <div className="mt-6 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="text-sm font-semibold text-zinc-100">Профиль релевантности</h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Что система вообще считает «нашим» тендером: девять групп потребностей с
            исключениями. Дешёвый фильтр до дорогого ИИ-анализа (раздел 5.1.1 ТЗ).
          </p>
        </div>
        <ChevronDown
          size={18}
          className={`shrink-0 text-zinc-500 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {isExpanded && (
        <div className="border-t border-white/[0.08] px-5 py-4">
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

          <p className="mb-3 text-[11px] leading-relaxed text-zinc-600">
            {KEYWORD_HINT} Группа срабатывает, если совпал хотя бы один ключ и не совпало ни
            одно исключение. Тендер, не прошедший ни одну группу, не удаляется — он просто
            скрыт в списке галочкой «Только прошедшие профиль релевантности».
          </p>

          {profile && (
            <>
              <div className="mb-4 rounded-lg border border-white/[0.07] bg-black/20 p-3">
                <div className="mb-1.5 text-[11px] text-zinc-500">
                  Чем система ищет на площадках ({profile.search_queries.length} фраз) — от
                  этого списка напрямую зависит, что вообще попадёт в базу
                </div>
                <Chips items={profile.search_queries} tone="muted" />
              </div>

              <div className="space-y-1.5">
                {profile.groups.map((group) => (
                  <GroupRow key={group.id} group={group} />
                ))}
              </div>
            </>
          )}

          {!profile && !error && (
            <div className="flex items-center gap-2 text-xs text-zinc-600">
              <Loader2 size={13} className="animate-spin" />
              Загрузка профиля…
            </div>
          )}

          {isAdmin && (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <button
                onClick={() => void runAiCheck()}
                disabled={isChecking || pending === 0}
                className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
              >
                {isChecking ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Sparkles size={13} />
                )}
                {pending === 0
                  ? "Все закупки проверены моделью"
                  : `Проверить моделью${pending === null ? "" : ` (осталось ${pending})`}`}
              </button>
              <span className="text-[11px] text-zinc-600">
                Новые закупки модель проверяет сама при сборе; кнопка нужна для накопленного
                архива.
              </span>
            </div>
          )}

          {isAdmin && (
            <button
              onClick={() => void recalculate()}
              disabled={isRecalculating}
              className="mt-4 flex items-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-200 hover:bg-white/5 disabled:opacity-50"
            >
              {isRecalculating ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <RefreshCw size={13} />
              )}
              Пересчитать по всем собранным тендерам
            </button>
          )}
        </div>
      )}
    </div>
  );
}
