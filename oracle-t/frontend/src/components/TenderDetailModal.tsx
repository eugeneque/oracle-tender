import type { Tender } from "../api/types";
import { TenderDetailPanel } from "./TenderDetailPanel";

/**
 * Карточка тендера в модальном окне — обёртка над `TenderDetailPanel`.
 *
 * Само содержимое карточки живёт в панели, потому что с 03.09.2026 показывается ещё и в
 * правой части двухпанельного режима (раздел 5.6 ТЗ). Здесь остаётся только оформление
 * модалки: затемнение, размеры и закрытие по клику вне окна.
 */
export function TenderDetailModal({
  tender,
  onClose,
  onChanged,
}: {
  tender: Tender;
  onClose: () => void;
  onChanged?: (tender: Tender) => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-8"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-7xl flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <TenderDetailPanel tender={tender} onClose={onClose} onChanged={onChanged} />
      </div>
    </div>
  );
}
