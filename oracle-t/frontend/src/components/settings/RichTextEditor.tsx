import { useEffect, useRef, useState } from "react";
import {
  Bold,
  Eraser,
  Heading,
  Italic,
  Link2,
  List,
  ListOrdered,
  Strikethrough,
  Underline,
} from "lucide-react";

/**
 * Кнопка тулбара. `onMouseDown` с `preventDefault` — не украшение: без него нажатие
 * снимает выделение в редакторе ещё до клика, и «сделать полужирным» применяется к пустому
 * месту вместо выделенного текста.
 */
function ToolbarButton({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      className="flex h-8 w-8 items-center justify-center rounded-md text-zinc-400 hover:bg-white/10 hover:text-zinc-100"
    >
      {children}
    </button>
  );
}

/**
 * Редактор письма с форматированием — `contentEditable` плюс `document.execCommand`.
 *
 * **Почему не библиотека.** Нужен один экран с полудюжиной кнопок; редактор уровня
 * TipTap/Quill — это десяток пакетов в сборке, которая ставится офлайн, ради возможностей,
 * которых в письме всё равно не будет: почтовые клиенты не показывают ни сложные таблицы,
 * ни внешние стили. `execCommand` формально устарел, но реализован во всех браузерах и
 * остаётся тем, на чём такие редакторы и работают.
 *
 * **`styleWithCSS = false`** — ключевая строка. По умолчанию браузер оформляет выделение
 * инлайновым `style`, а сервер такие атрибуты снимает при очистке (`email_html.py`), и
 * форматирование пропало бы между «вижу» и «отправлено». С выключенным флагом получаются
 * теги `<b>`, `<i>`, `<u>` — ровно те, что разрешены к отправке.
 *
 * **Вставка идёт только текстом.** Из Word и веб-страниц прилетает разметка с классами и
 * `<style>`, которую сервер всё равно вычистит; показать её в редакторе значило бы соврать
 * о том, как будет выглядеть письмо.
 *
 * Компонент неуправляемый: React не переписывает `innerHTML` на каждый ввод — иначе курсор
 * прыгает в начало на каждой букве. Наружу отдаётся HTML через `onChange`, внутрь значение
 * попадает один раз при монтировании и по смене `resetKey` (после отправки письма).
 */
export function RichTextEditor({
  value,
  onChange,
  resetKey = 0,
  placeholder = "Текст письма…",
}: {
  value: string;
  onChange: (html: string) => void;
  resetKey?: number;
  placeholder?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [isEmpty, setIsEmpty] = useState(true);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.innerHTML = value;
    setIsEmpty(!node.textContent?.trim());
    try {
      document.execCommand("styleWithCSS", false, "false");
    } catch {
      // Старые браузеры этой команды не знают — форматирование останется инлайновым,
      // сервер приведёт его к разрешённым тегам.
    }
    // Значение подставляется только при монтировании и по явному сбросу: перерисовка
    // на каждый ввод ломает позицию курсора.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  const emit = () => {
    const node = ref.current;
    if (!node) return;
    setIsEmpty(!node.textContent?.trim());
    onChange(node.innerHTML);
  };

  const run = (command: string, argument?: string) => {
    ref.current?.focus();
    document.execCommand(command, false, argument);
    emit();
  };

  const addLink = () => {
    const url = window.prompt("Адрес ссылки", "https://");
    if (!url) return;
    if (!/^(https?:\/\/|mailto:|tel:)/i.test(url)) {
      window.alert("Ссылка должна начинаться с http://, https://, mailto: или tel:");
      return;
    }
    run("createLink", url);
  };

  const handlePaste = (event: React.ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    const text = event.clipboardData.getData("text/plain");
    document.execCommand("insertText", false, text);
    emit();
  };


  return (
    <div className="mt-1.5 overflow-hidden rounded-lg border border-white/10 bg-black/20 focus-within:border-indigo-500/50">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-white/[0.08] px-1.5 py-1">
        <ToolbarButton onClick={() => run("bold")} title="Полужирный">
          <Bold size={14} />
        </ToolbarButton>
        <ToolbarButton onClick={() => run("italic")} title="Курсив">
          <Italic size={14} />
        </ToolbarButton>
        <ToolbarButton onClick={() => run("underline")} title="Подчёркнутый">
          <Underline size={14} />
        </ToolbarButton>
        <ToolbarButton onClick={() => run("strikeThrough")} title="Зачёркнутый">
          <Strikethrough size={14} />
        </ToolbarButton>
        <span className="mx-1 h-5 w-px bg-white/10" />
        <ToolbarButton onClick={() => run("formatBlock", "<h2>")} title="Заголовок">
          <Heading size={14} />
        </ToolbarButton>
        <ToolbarButton onClick={() => run("insertUnorderedList")} title="Маркированный список">
          <List size={14} />
        </ToolbarButton>
        <ToolbarButton onClick={() => run("insertOrderedList")} title="Нумерованный список">
          <ListOrdered size={14} />
        </ToolbarButton>
        <ToolbarButton onClick={addLink} title="Ссылка">
          <Link2 size={14} />
        </ToolbarButton>
        <span className="mx-1 h-5 w-px bg-white/10" />
        <ToolbarButton
          onClick={() => {
            run("removeFormat");
            run("unlink");
          }}
          title="Убрать форматирование"
        >
          <Eraser size={14} />
        </ToolbarButton>
      </div>

      <div className="relative">
        {isEmpty && (
          <span className="pointer-events-none absolute left-3 top-3 text-sm text-zinc-600">
            {placeholder}
          </span>
        )}
        <div
          ref={ref}
          contentEditable
          suppressContentEditableWarning
          onInput={emit}
          onBlur={emit}
          onPaste={handlePaste}
          role="textbox"
          aria-multiline="true"
          aria-label="Текст письма"
          className="letter-body min-h-[180px] px-3 py-2.5 text-sm text-zinc-100 focus:outline-none"
        />
      </div>
    </div>
  );
}
