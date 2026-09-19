import { Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ApiError } from "../api/client";
import type { AiProviderKey, AiProviderStatus } from "../api/types";
import { chooseMyAiProvider } from "../hooks/useAiProvider";
import { AI_PROVIDERS } from "../utils/aiProviders";
import { AiProviderIcon } from "./AiProviderIcon";

/**
 * Компактный выбор своей модели ИИ — бейдж в шапке блока «Разбор ИИ», раскрывающийся в
 * список. Выбор персональный: меняет модель только текущему пользователю (18.09.2026).
 *
 * Именно здесь, а не только в учётной записи: пользователь смотрит на разбор и хочет
 * тут же сравнить, что скажет другая модель, — уходить ради этого в настройки долго.
 * Смена не запускает пересчёт сама: цифры на экране получены прежней моделью, и об этом
 * говорит подпись; новый разбор — кнопкой «Обновить разбор».
 *
 * Список рисуется через портал с абсолютными координатами: блок «Разбор ИИ» обрезает
 * содержимое (`overflow-hidden` ради скруглённой шапки), и вложенный список в нём не
 * помещался. Закрывается по клику снаружи, Escape и прокрутке — при прокрутке координаты
 * устаревают, и проще закрыть, чем гонять их за кнопкой.
 */
export function AiProviderPicker({ status }: { status: AiProviderStatus }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const toggle = () => {
    if (isOpen) {
      setIsOpen(false);
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) setPosition({ top: rect.bottom + 6, left: rect.left });
    setError(null);
    setIsOpen(true);
  };

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (listRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      setIsOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsOpen(false);
    };
    const onScroll = () => setIsOpen(false);
    window.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [isOpen]);

  const isClaude = status.active_provider === "claude";

  const choose = async (provider: AiProviderKey | null) => {
    setError(null);
    setIsBusy(true);
    try {
      await chooseMyAiProvider(provider);
      setIsOpen(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось переключить модель");
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onClick={toggle}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        title={`Ваша модель: ${status.label}${status.model ? ` (${status.model})` : ""}${
          status.source === "default" ? " — системная по умолчанию" : ""
        }. Нажмите, чтобы сменить`}
        className={`flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium transition-colors ${
          isClaude
            ? "border-orange-400/30 bg-orange-500/10 text-orange-200 hover:bg-orange-500/20"
            : "border-white/10 bg-white/5 text-zinc-300 hover:bg-white/10"
        }`}
      >
        <AiProviderIcon provider={status.active_provider} size={13} />
        {status.label}
        <ChevronDown size={11} className={`opacity-70 transition-transform ${isOpen ? "rotate-180" : ""}`} />
      </button>

      {isOpen &&
        createPortal(
        <div
          ref={listRef}
          role="listbox"
          aria-label="Личная модель ИИ"
          style={{ top: position.top, left: position.left }}
          className="fixed z-50 w-64 rounded-xl border border-white/10 bg-zinc-900 p-1.5 shadow-xl shadow-black/40"
        >
          <p className="px-2 pb-1.5 pt-1 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
            Личная модель
          </p>
          {AI_PROVIDERS.map((item) => {
            const isConfigured = status.configured_providers.includes(item.key);
            const isCurrent = status.active_provider === item.key;
            return (
              <button
                key={item.key}
                type="button"
                role="option"
                aria-selected={isCurrent}
                disabled={isBusy || !isConfigured}
                onClick={() => void choose(item.key)}
                className="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-xs text-zinc-200 hover:bg-white/5 disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent"
              >
                <AiProviderIcon provider={item.key} size={18} />
                <span className="flex-1 leading-tight">
                  <span className="block font-medium">{item.label}</span>
                  <span className="block text-[10px] text-zinc-500">
                    {isConfigured ? item.hint : "не настроено администратором"}
                  </span>
                </span>
                {isCurrent && <Check size={13} className="text-emerald-400" />}
              </button>
            );
          })}
          {status.source === "user" && (
            <button
              type="button"
              disabled={isBusy}
              onClick={() => void choose(null)}
              className="mt-1 w-full rounded-lg border-t border-white/[0.06] px-2 pb-1 pt-2 text-left text-[11px] text-zinc-500 hover:text-zinc-300 disabled:opacity-50"
            >
              Как в системе по умолчанию ({status.default_label})
            </button>
          )}
          {error && (
            <p className="mt-1 rounded-lg border border-red-500/20 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-300">
              {error}
            </p>
          )}
        </div>,
        document.body,
      )}
    </>
  );
}
