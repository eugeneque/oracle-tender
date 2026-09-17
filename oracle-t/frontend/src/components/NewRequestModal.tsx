import { useRef, useState } from "react";
import type { DragEvent, FormEvent } from "react";
import { FilePlus2, FileText, Loader2, Upload, X } from "lucide-react";

import { ApiError, postForm } from "../api/client";
import type { ManualRequestOut } from "../api/types";

/** Форматы, которые бэкенд умеет разбирать до текста (`app/services/document_extraction.py`).
 * Остальные файлы тоже сохранятся, но в ИИ-анализ уйдут пустыми. */
const ACCEPTED_EXTENSIONS =
  ".pdf,.doc,.docx,.rtf,.xls,.xlsx,.xlsm,.txt,.zip,.7z,.rar";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

/**
 * Ручная заявка: закупка, которую заказчик прислал напрямую, минуя площадки (решение
 * 15.09.2026). Заказчик присылает проект договора с характеристиками приборов письмом —
 * его прикладывают сюда, и дальше заявка проходит тот же конвейер, что и собранный
 * тендер: ИИ-анализ требований, матрица соответствия, карточка, история.
 *
 * Одна форма на всё: поля и файлы уходят одним multipart-запросом, а в ответ приходит
 * созданный тендер — родитель сразу открывает его карточку, где виден ход анализа.
 */
export function NewRequestModal({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (result: ManualRequestOut) => void;
}) {
  const [title, setTitle] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [price, setPrice] = useState("");
  const [deadline, setDeadline] = useState("");
  const [comment, setComment] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [runAnalysis, setRunAnalysis] = useState(true);
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = (incoming: FileList | File[]) => {
    const next = Array.from(incoming);
    if (next.length === 0) return;
    setFiles((prev) => {
      // Один и тот же файл, выбранный дважды, не должен уйти двумя документами.
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...next.filter((f) => !seen.has(`${f.name}:${f.size}`))];
    });
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    addFiles(event.dataTransfer.files);
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim()) {
      setError("Укажите наименование закупки");
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("title", title.trim());
      if (customerName.trim()) form.append("customer_name", customerName.trim());
      // Сумму принимает Decimal на сервере: запятая из русской раскладки — в точку,
      // пробелы-разделители тысяч — прочь.
      const normalizedPrice = price.replace(/\s/g, "").replace(",", ".");
      if (normalizedPrice) form.append("price", normalizedPrice);
      if (deadline) form.append("application_end", deadline);
      if (comment.trim()) form.append("comment", comment.trim());
      form.append("run_analysis", runAnalysis ? "true" : "false");
      for (const file of files) form.append("files", file, file.name);

      onCreated(await postForm<ManualRequestOut>("/tenders/manual", form));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать заявку");
      setIsSubmitting(false);
    }
  };

  const inputClass =
    "w-full rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4 py-8"
      onClick={onCancel}
    >
      <form
        onSubmit={(e) => void handleSubmit(e)}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-xl"
      >
        <div className="flex items-center justify-between border-b border-white/[0.08] px-5 py-4">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <FilePlus2 size={15} />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-white">Новая заявка</h2>
              <p className="text-xs text-zinc-500">
                Закупка, которую заказчик прислал напрямую — с проектом договора или ТЗ
              </p>
            </div>
          </div>
          <button type="button" onClick={onCancel} className="text-zinc-500 hover:text-zinc-200">
            <X size={18} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {error && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-400">
              {error}
            </div>
          )}

          <label className="block">
            <span className="mb-1 block text-xs text-zinc-400">
              Наименование закупки <span className="text-red-400">*</span>
            </span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Оказание услуги по предоставлению информации ИСУ…"
              autoFocus
              className={inputClass}
            />
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block sm:col-span-2">
              <span className="mb-1 block text-xs text-zinc-400">Заказчик</span>
              <input
                value={customerName}
                onChange={(e) => setCustomerName(e.target.value)}
                placeholder="АО «Новосибирскэнергосбыт»"
                className={inputClass}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-400">Сумма, ₽</span>
              <input
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                inputMode="decimal"
                placeholder="не указана"
                className={inputClass}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-400">Срок подачи предложения</span>
              <input
                type="date"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
                className={`${inputClass} [color-scheme:dark]`}
              />
            </label>
          </div>

          <label className="block">
            <span className="mb-1 block text-xs text-zinc-400">Комментарий</span>
            <textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={2}
              placeholder="Откуда пришёл документ, на что обратить внимание — попадёт в историю карточки"
              className={`${inputClass} resize-none`}
            />
          </label>

          <div>
            <span className="mb-1 block text-xs text-zinc-400">
              Документы — проект договора, ТЗ, спецификация
            </span>
            <div
              role="button"
              tabIndex={0}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
              }}
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              className={`flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border border-dashed px-4 py-5 text-center transition-colors ${
                isDragging
                  ? "border-indigo-500/60 bg-indigo-500/10"
                  : "border-white/[0.12] bg-white/[0.02] hover:border-white/25"
              }`}
            >
              <Upload size={18} className="text-zinc-500" />
              <span className="text-sm text-zinc-300">
                Перетащите файлы сюда или нажмите, чтобы выбрать
              </span>
              <span className="text-xs text-zinc-600">
                PDF, Word, Excel, RTF, TXT или архив — до 50 МБ каждый
              </span>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={ACCEPTED_EXTENSIONS}
                className="hidden"
                onChange={(e) => {
                  if (e.target.files) addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>

            {files.length > 0 && (
              <div className="mt-2 space-y-1.5">
                {files.map((file, index) => (
                  <div
                    key={`${file.name}:${file.size}`}
                    className="flex items-center justify-between gap-3 rounded-lg border border-white/[0.08] bg-white/[0.02] px-3 py-1.5"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <FileText size={14} className="shrink-0 text-zinc-500" />
                      <span className="truncate text-sm text-zinc-200" title={file.name}>
                        {file.name}
                      </span>
                      <span className="shrink-0 text-xs text-zinc-600">
                        {formatSize(file.size)}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeFile(index)}
                      className="shrink-0 text-zinc-600 hover:text-zinc-300"
                      title="Убрать файл"
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <label
            className={`flex items-center gap-2.5 text-sm ${
              files.length === 0 ? "text-zinc-600" : "text-zinc-300"
            }`}
            title={
              files.length === 0
                ? "Анализировать нечего — приложите хотя бы один документ"
                : undefined
            }
          >
            <input
              type="checkbox"
              checked={runAnalysis}
              disabled={files.length === 0}
              onChange={(e) => setRunAnalysis(e.target.checked)}
              className="h-4 w-4 rounded border-white/20 bg-white/5 accent-indigo-500"
            />
            Сразу запустить ИИ-анализ документов — извлечь требования к приборам
          </label>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-white/[0.08] px-5 py-4">
          <button
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
            className="rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 hover:bg-white/5 disabled:opacity-50"
          >
            Отмена
          </button>
          <button
            type="submit"
            disabled={isSubmitting}
            className="flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3.5 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {isSubmitting ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {files.length > 0 ? "Загружаю и разбираю файлы…" : "Создаю…"}
              </>
            ) : (
              "Создать заявку"
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
