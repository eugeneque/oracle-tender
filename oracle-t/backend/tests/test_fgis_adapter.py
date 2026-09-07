"""Тесты адаптера ФГИС (раздел 4.2, 5.3 ТЗ — Этап 4).

Переписаны вместе с адаптером под **реальный** API реестра «Утверждённые типы СИ». Фикстуры
ниже — сокращённые, но дословные по структуре ответы живого сервиса (разведка 30.08.2026),
включая его неудобные особенности: поля-списки, приходящие JSON-строкой, несколько версий
«Описания типа» в одной карточке и полнотекстовый поиск, возвращающий чужие записи.
"""

from __future__ import annotations

import json
from datetime import date

import app.adapters.fgis as fgis_module
from app.adapters.fgis import FgisAdapter, brand_tokens, latest_description_type


class _FakeResponse:
    def __init__(self, *, json_data=None, json_error: Exception | None = None, text: str = "", content: bytes = b""):
        self._json_data = json_data
        self._json_error = json_error
        self.text = text
        self.content = content

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json_data


def _list_response(docs: list[dict], num_found: int | None = None):
    return _FakeResponse(
        json_data={"response": {"numFound": num_found if num_found is not None else len(docs), "docs": docs}}
    )


MIRTEK_DOC = {
    "title": "Счетчики электрической энергии однофазные многофункциональные",
    "number": "61891-15",
    "notation": '"МИРТЕК-12-РУ"',
    "manufacturers": 'Общество с ограниченной ответственностью «МИРТЕК», РОССИЯ, г. Таганрог',
    "mit_uuid": "b5a1bff5-49f1-ec43-768e-ea5d695666d7",
}

# Запись, которую полнотекстовый поиск подмешивает в выдачу: слово из запроса встретилось
# где-то ещё, а изготовитель совсем другой (реальный случай — «Завод EJF, ЧЕХИЯ» в поиске
# по «Нижегородский завод»).
FOREIGN_DOC = {
    "title": "Трансформаторы напряжения",
    "number": "98351-26",
    "notation": "РВ 103",
    "manufacturers": "Завод «EJF», ЧЕХИЯ, Videnska 117, 61900 Brno",
    "mit_uuid": "ffffffff-0000-0000-0000-000000000000",
}


def _patch_get(monkeypatch, responses):
    """Подменяет сетевой слой очередью ответов (по одному на запрос)."""

    queue = list(responses)

    def _fake(client, method, url, **kwargs):
        if not queue:
            raise AssertionError(f"неожиданный дополнительный запрос к {url}")
        return queue.pop(0)

    monkeypatch.setattr(fgis_module, "fetch_with_retry", _fake)
    return queue


def test_brand_tokens_strips_legal_form_but_keeps_distinguishing_words():
    assert brand_tokens('Общество с ограниченной ответственностью "МИРТЕК"') == ["миртек"]
    # «завод» — шумное слово, из-за него поиск по этому названию давал 2808 чужих записей
    assert "завод" not in brand_tokens('ФГУП «Нижегородский завод им.М.В.Фрунзе»')
    # ...а вот «нпо» отбрасывать нельзя: без него название вырождается в «мир» и цепляет
    # ООО «Мир» из Казани как своё.
    assert brand_tokens('ООО «НПО "МИР"»') == ["нпо", "мир"]


def test_search_filters_out_foreign_records_of_fulltext_search(monkeypatch):
    _patch_get(monkeypatch, [_list_response([MIRTEK_DOC, FOREIGN_DOC])])

    results = FgisAdapter().search_by_manufacturer('ООО «МИРТЕК»', fetch_cards=False)

    assert [r.si_code for r in results] == ["61891-15"]
    result = results[0]
    assert result.notation == "МИРТЕК-12-РУ"  # кавычки реестра сняты
    assert result.mit_uuid == MIRTEK_DOC["mit_uuid"]
    assert result.matched_by == "legal"
    assert result.raw["number"] == "61891-15"


def test_search_marks_brand_only_matches_for_human_review(monkeypatch):
    """Совпадение по торговому имени не равно совпадению по юрлицу: под маркой «Пульсар»
    в реестре есть компании, не связанные с ООО «НПП "ТЕПЛОВОДОХРАН"»."""

    other_company = {
        "title": "Счетчики электрической энергии",
        "number": "12345-20",
        "notation": "Пульсар-1",
        "manufacturers": 'ООО «Пульсар», РОССИЯ, г. Москва',
        "mit_uuid": "aaaa",
    }
    # Два запроса: по токену юрлица и по токену бренда.
    _patch_get(monkeypatch, [_list_response([]), _list_response([other_company])])

    results = FgisAdapter().search_by_manufacturer(
        'ООО «НПП "ТЕПЛОВОДОХРАН"»', brand_name="Пульсар", fetch_cards=False
    )

    assert [r.matched_by for r in results] == ["brand"]


def test_search_paginates_until_all_records_seen(monkeypatch):
    """Широкий термин (у НПО «МИР» это просто «мир») даёт тысячи записей, свои лежат
    вперемешку по всей выдаче — без пагинации часть типов терялась бы молча."""

    monkeypatch.setattr(fgis_module, "SEARCH_ROWS", 2)
    page1 = [FOREIGN_DOC, FOREIGN_DOC]
    page2 = [MIRTEK_DOC]
    _patch_get(monkeypatch, [_list_response(page1, num_found=3), _list_response(page2, num_found=3)])

    results = FgisAdapter().search_by_manufacturer('ООО «МИРТЕК»', fetch_cards=False)

    assert [r.si_code for r in results] == ["61891-15"]


def test_search_stops_at_scan_ceiling(monkeypatch):
    """Потолок сканирования не даёт зациклиться на бесконечно широкой выдаче."""

    monkeypatch.setattr(fgis_module, "SEARCH_ROWS", 2)
    monkeypatch.setattr(fgis_module, "MAX_SCAN_RECORDS", 4)
    full_page = _list_response([FOREIGN_DOC, FOREIGN_DOC], num_found=10_000)
    monkeypatch.setattr(fgis_module, "fetch_with_retry", lambda *a, **k: full_page)

    assert FgisAdapter().search_by_manufacturer('ООО «МИРТЕК»', fetch_cards=False) == []


def test_search_handles_html_instead_of_json(monkeypatch):
    """Симптом неверного пути: ФГИС отдаёт SPA-оболочку с кодом 200 — именно так вёл себя
    URL из раздела 4.2 ТЗ, на который адаптер ходил раньше."""

    _patch_get(monkeypatch, [_FakeResponse(json_error=ValueError("not json"), text="<html>…</html>")])

    assert FgisAdapter().search_by_manufacturer('ООО «МИРТЕК»', fetch_cards=False) == []


def test_search_handles_network_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise ConnectionError("недоступен")

    monkeypatch.setattr(fgis_module, "fetch_with_retry", _raise)

    assert FgisAdapter().search_by_manufacturer('ООО «МИРТЕК»', fetch_cards=False) == []


def test_search_without_meaningful_words_does_not_query(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("запрос не должен отправляться")

    monkeypatch.setattr(fgis_module, "fetch_with_retry", _fail)

    assert FgisAdapter().search_by_manufacturer("ООО") == []


CARD_DOC = {
    "number": "61891-15",
    "j_notation": json.dumps(['"МИРТЕК-12-РУ"']),
    "j_modification": json.dumps(["МИРТЕК-12-РУ-Х2-Х3-Х4, где:", "Х2 - Тип корпуса"]),
    "j_mpis": json.dumps([{"mpi": 192}]),
    "valid_to": "2030-06-29T00:00:00Z",
    "is_actual": True,
    "j_specifications": json.dumps(
        [
            {"doc_uuid": "old", "title": "Описание типа", "filename": "2024-61891-15.pdf"},
            {
                "doc_uuid": "new",
                "title": "Описание типа",
                "filename": "2026-61891-15.pdf",
                "version_num": "4",
            },
            {"doc_uuid": "mp", "title": "Методики поверки", "filename": "2026-mp61891-15.pdf"},
        ]
    ),
}


def test_enrich_from_card_reads_modifications_mpi_and_latest_description(monkeypatch):
    _patch_get(monkeypatch, [_list_response([MIRTEK_DOC]), _list_response([CARD_DOC])])

    result = FgisAdapter().search_by_manufacturer('ООО «МИРТЕК»')[0]

    assert result.mpi_months == 192
    assert result.valid_to == date(2030, 6, 29)
    assert result.is_actual is True
    assert "Х2 - Тип корпуса" in result.allowed_modifications
    # Из четырёх документов карточки выбран «Описание типа» свежей версии, а не методика
    # поверки и не прошлогодняя редакция.
    assert result.description_type_url.endswith("/new")
    assert result.description_type_version == "4"
    assert result.description_type_mirror_url == "https://all-pribors.ru/docs/2026-61891-15.pdf"


def test_latest_description_type_prefers_highest_version():
    latest = latest_description_type(CARD_DOC["j_specifications"])
    assert latest.doc_uuid == "new"


def test_latest_description_type_ignores_other_documents():
    assert latest_description_type(json.dumps([{"doc_uuid": "mp", "title": "Методики поверки"}])) is None


def test_enrich_survives_card_failure(monkeypatch):
    """Карточка не ответила — запись остаётся с данными из списка, а не теряется."""

    _patch_get(monkeypatch, [_list_response([MIRTEK_DOC]), _FakeResponse(json_error=ValueError("html"))])

    result = FgisAdapter().search_by_manufacturer('ООО «МИРТЕК»')[0]

    assert result.si_code == "61891-15"
    assert result.description_type_url is None


def test_fetch_description_type_text_uses_document_extraction(monkeypatch):
    _patch_get(monkeypatch, [_FakeResponse(content=b"%PDF-fake-content")])
    monkeypatch.setattr(
        "app.services.document_extraction.extract_text", lambda ext, content: f"извлечено:{ext}:{len(content)}"
    )

    text = FgisAdapter().fetch_description_type_text("https://fgis.gost.ru/fundmetrology/api/downloadfile/x")

    assert text == "извлечено:.pdf:17"


def test_fetch_description_type_falls_back_to_mirror(monkeypatch):
    """Главный практический сценарий: файловый эндпоинт ФГИС висит/отдаёт 504, тот же
    документ забирается из зеркала Госреестра."""

    calls: list[str] = []

    def _fake(client, method, url, **kwargs):
        calls.append(url)
        if "fgis.gost.ru" in url:
            raise TimeoutError("read timeout")
        return _FakeResponse(content=b"%PDF-mirror")

    monkeypatch.setattr(fgis_module, "fetch_with_retry", _fake)
    monkeypatch.setattr("app.services.document_extraction.extract_text", lambda ext, content: "текст описания")

    text = FgisAdapter().fetch_description_type_text(
        "https://fgis.gost.ru/fundmetrology/api/downloadfile/x",
        mirror_url="https://all-pribors.ru/docs/2026-61891-15.pdf",
    )

    assert text == "текст описания"
    assert len(calls) == 2 and "all-pribors.ru" in calls[1]


def test_fetch_description_type_returns_none_when_both_sources_fail(monkeypatch):
    def _raise(*args, **kwargs):
        raise ConnectionError("недоступен")

    monkeypatch.setattr(fgis_module, "fetch_with_retry", _raise)

    assert (
        FgisAdapter().fetch_description_type_text(
            "https://fgis.gost.ru/fundmetrology/api/downloadfile/x",
            mirror_url="https://all-pribors.ru/docs/2026-61891-15.pdf",
        )
        is None
    )


def test_fetch_description_type_returns_none_on_extraction_error(monkeypatch):
    _patch_get(monkeypatch, [_FakeResponse(content=b"broken")])

    def _raise_extract(ext, content):
        raise ValueError("не PDF")

    monkeypatch.setattr("app.services.document_extraction.extract_text", _raise_extract)

    assert FgisAdapter().fetch_description_type_text("https://fgis.gost.ru/x.pdf") is None
