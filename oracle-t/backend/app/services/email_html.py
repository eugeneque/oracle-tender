"""Разбор HTML письма, написанного администратором в редакторе (раздел 5.8 ТЗ).

Две задачи, и обе обязательные.

**Очистка.** Текст приходит из `contentEditable` в браузере, то есть из `document.exec-
Command`, который оставляет за собой мусор: `<font>`, инлайновые `style`, вложенные `<div>`,
а при вставке из Word — ещё и целые `<style>`-блоки с классами. Этот HTML попадает в два
места, где его нельзя показывать как есть: в чужой почтовый клиент и в журнал уведомлений
на нашей же странице. Поэтому разрешён короткий белый список тегов, все атрибуты, кроме
`href` у ссылок, снимаются, а `<script>`/`<style>` выбрасываются вместе с содержимым.
Чистится один раз, на сервере: очищенное сохраняется в БД, и всё, что читает `body_html`
потом, уже безопасно.

**Текстовая версия.** Письмо уходит как `multipart/alternative`: часть клиентов (и почти все
почтовые уведомления в мессенджерах) показывают только `text/plain`, а письмо без текстовой
части заметно охотнее попадает в спам. Отдельным полем её у администратора не спрашиваем —
никто не станет писать одно и то же дважды, — а получаем из того же HTML.

Своя реализация вместо `bleach`: нужен разбор ровно одного, полностью нам известного вида
HTML, и это дешевле новой зависимости в сборке, которая ставится офлайн.
"""

from __future__ import annotations

import re
from html import escape, unescape
from html.parser import HTMLParser

# Ровно то, что умеет ставить редактор в интерфейсе. Ни таблиц, ни картинок: картинку в
# письме всё равно пришлось бы куда-то класть (внешняя ссылка блокируется почтовыми
# клиентами, вложение — это другой формат письма), а таблицы у редактора нет.
ALLOWED_TAGS = {
    "p", "br", "hr", "div", "span",
    "b", "strong", "i", "em", "u", "s", "strike", "del",
    "a", "ul", "ol", "li",
    "h1", "h2", "h3",
    "blockquote", "code", "pre",
}
VOID_TAGS = {"br", "hr"}
# Теги, содержимое которых не текст письма, а разметка/код: выбрасываются целиком.
DROPPED_WITH_CONTENT = {"script", "style", "head", "title"}
# Ссылки только туда, куда осмысленно вести из письма. `javascript:` и `data:` — тот самый
# случай, ради которого очистка и существует.
ALLOWED_URL_SCHEMES = ("http://", "https://", "mailto:", "tel:")

# Переводы строки в тексте письма: в HTML они ничего не значат, а в plain-версии их надо
# получить из блочных тегов.
# `li` здесь нет намеренно: он превращается в маркер списка отдельным правилом, и
# перевод строки перед ним даёт сам маркер, а после — следующий элемент или конец списка.
_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "ul", "ol", "blockquote", "pre", "hr", "br"}

MAX_HTML_LENGTH = 100_000


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open: list[str] = []
        self._dropping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROPPED_WITH_CONTENT:
            self._dropping += 1
            return
        if self._dropping or tag not in ALLOWED_TAGS:
            # Запрещённый тег снимается, но его текст остаётся: администратор написал этот
            # текст осознанно, потерять его молча — хуже, чем потерять оформление.
            return
        if tag in VOID_TAGS:
            self.parts.append(f"<{tag}>")
            return
        self.parts.append(f"<{tag}{self._render_attrs(tag, attrs)}>")
        self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._dropping and tag in ALLOWED_TAGS and tag in VOID_TAGS:
            self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED_WITH_CONTENT:
            self._dropping = max(0, self._dropping - 1)
            return
        if self._dropping or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag not in self._open:
            # Закрывающий тег без открывающего — иначе он «закроет» чужой элемент.
            return
        # Незакрытые вложенные элементы закрываются здесь же: браузер это простит, почтовый
        # клиент — не обязательно.
        while self._open:
            current = self._open.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self._dropping:
            self.parts.append(escape(data, quote=False))

    def close_all(self) -> str:
        while self._open:
            self.parts.append(f"</{self._open.pop()}>")
        return "".join(self.parts)

    @staticmethod
    def _render_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> str:
        if tag != "a":
            return ""
        for name, value in attrs:
            if name.lower() != "href" or not value:
                continue
            url = unescape(value).strip()
            if url.lower().startswith(ALLOWED_URL_SCHEMES):
                return f' href="{escape(url, quote=True)}"'
        return ""


def sanitize_email_html(raw: str) -> str:
    """HTML письма, пригодный и для отправки, и для показа в журнале."""

    parser = _Sanitizer()
    parser.feed(raw or "")
    parser.close()
    return parser.close_all().strip()


def html_to_text(html: str) -> str:
    """Текстовая версия письма: блочные теги становятся переводами строки, остальные —
    исчезают. Разметку списков сохраняем маркером «-»: без него перечисление в plain-версии
    склеивается в одну неразличимую простыню."""

    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", html or "")
    text = re.sub(r"(?i)<li\b[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</li\s*>", "", text)
    text = re.sub(
        r"(?i)</?(" + "|".join(_BLOCK_TAGS) + r")\b[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = unescape(text)
    # Схлопываем пустые строки, порождённые парными блочными тегами (</p><p> — это две
    # подстановки подряд), но абзацный отступ в одну пустую строку сохраняем.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
