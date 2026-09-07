"""Тесты адаптера сайтов производителей (раздел 5.3 ТЗ, п.3 алгоритма — Этап 4).

Сетевые вызовы замоканы: тесты проверяют логику поиска и ранжирования на разметке,
воспроизводящей реально встреченные варианты (см. докстринг `app/adapters/manufacturer_site.py`).
"""

from __future__ import annotations

import pytest

import app.adapters.manufacturer_site as site_module
from app.adapters.manufacturer_site import ManufacturerSiteAdapter, _model_tokens


class _FakeResponse:
    def __init__(self, text: str = "", content: bytes = b"", content_type: str = "text/html"):
        self.text = text
        self.content = content
        self.headers = {"content-type": content_type}


def _fake_site(pages: dict[str, str]):
    """Возвращает подменную `fetch_with_retry`, отдающую заранее заданные страницы."""

    def _fetch(client, method, url, **kwargs):
        key = url.split("#", 1)[0].rstrip("/")
        if key in pages:
            return _FakeResponse(text=pages[key])
        raise RuntimeError(f"404 {url}")

    return _fetch


@pytest.fixture(autouse=True)
def _no_crawl_delay(monkeypatch):
    monkeypatch.setattr(site_module.time, "sleep", lambda _s: None)


def test_finds_manual_by_link_text(monkeypatch):
    """Вариант «Энергомеры»: осмысленная подпись ссылки."""

    pages = {
        "https://m.test/catalog": '<a href="/catalog/ce101-r5">Счетчик CE101 R5</a>',
        "https://m.test/catalog/ce101-r5": (
            '<a href="/docs/ce101_re.pdf">Руководство по эксплуатации</a>'
            '<a href="/docs/ce101_st.pdf">Сертификат об утверждении типа</a>'
        ),
    }
    monkeypatch.setattr(site_module, "fetch_with_retry", _fake_site(pages))

    found = ManufacturerSiteAdapter().find_user_manual("https://m.test/catalog", "CE101 R5")

    assert found, "руководство должно быть найдено"
    assert found[0].url == "https://m.test/docs/ce101_re.pdf"
    # сертификат — не руководство, он не должен попасть в кандидаты вовсе
    assert all("ce101_st" not in c.url for c in found)


def test_finds_manual_by_href_when_link_text_is_generic(monkeypatch):
    """Вариант «Тайпит»/«Милур»: подпись ссылки неинформативна («PDF», «Скачать»), но
    ключевое слово есть в имени файла. Опора только на текст ссылки пропустила бы эти сайты —
    ради этого случая ключевые слова ищутся и в URL."""

    pages = {
        "https://m2.test/catalog": '<a href="/p/neva">НЕВА МТ 324</a>',
        "https://m2.test/p/neva": '<a href="/upload/manual-neva-mt-324.pdf">PDF</a>',
    }
    monkeypatch.setattr(site_module, "fetch_with_retry", _fake_site(pages))

    found = ManufacturerSiteAdapter().find_user_manual("https://m2.test/catalog", "НЕВА МТ 324")

    assert found
    assert found[0].url == "https://m2.test/upload/manual-neva-mt-324.pdf"


def test_prefers_manual_matching_requested_model(monkeypatch):
    """На карточке нередко висят документы соседних моделей — выбранным должен быть тот,
    что относится к запрошенной."""

    pages = {
        "https://m3.test/c": (
            '<a href="/docs/ce101_re.pdf">Руководство по эксплуатации</a>'
            '<a href="/docs/ce303_re.pdf">Руководство по эксплуатации</a>'
        )
    }
    monkeypatch.setattr(site_module, "fetch_with_retry", _fake_site(pages))

    found = ManufacturerSiteAdapter().find_user_manual("https://m3.test/c", "CE303")

    assert found[0].url.endswith("ce303_re.pdf")


def test_ignores_certificates_and_type_descriptions(monkeypatch):
    pages = {
        "https://m4.test/c": (
            '<a href="/d/x_st.pdf">Сертификат об утверждении типа средств измерений</a>'
            '<a href="/d/x_ot.pdf">Описание типа</a>'
            '<a href="/d/x_ds.pdf">Декларация о соответствии</a>'
            '<a href="/d/policy.pdf">Политика конфиденциальности</a>'
        )
    }
    monkeypatch.setattr(site_module, "fetch_with_retry", _fake_site(pages))

    assert ManufacturerSiteAdapter().find_user_manual("https://m4.test/c", "X") == []


def test_does_not_leave_the_site(monkeypatch):
    """Обход не должен уходить на внешние домены — мы ищем документацию конкретного
    производителя, а не гуляем по интернету."""

    visited: list[str] = []

    def _fetch(client, method, url, **kwargs):
        visited.append(url)
        if url.rstrip("/") == "https://m5.test/c":
            return _FakeResponse(
                text='<a href="https://other.test/manual-x.pdf">Руководство</a>'
                '<a href="https://other.test/page">Внешняя страница</a>'
            )
        return _FakeResponse(text="")

    monkeypatch.setattr(site_module, "fetch_with_retry", _fetch)

    ManufacturerSiteAdapter().find_user_manual("https://m5.test/c", "X")

    assert all("other.test" not in url for url in visited)


def test_respects_page_budget(monkeypatch):
    """Ограничение числа страниц — защита и от бесконечного обхода, и от чрезмерной
    нагрузки на чужой сайт."""

    fetched: list[str] = []

    def _fetch(client, method, url, **kwargs):
        fetched.append(url)
        # каждая страница ссылается на две новые — без бюджета обход не закончится
        n = len(fetched)
        return _FakeResponse(text=f'<a href="/p{n}a">a</a><a href="/p{n}b">b</a>')

    monkeypatch.setattr(site_module, "fetch_with_retry", _fetch)

    ManufacturerSiteAdapter(max_pages=5, max_depth=10).find_user_manual("https://m6.test/", "X")

    assert len(fetched) <= 5


def test_unreachable_page_does_not_abort_crawl(monkeypatch):
    """Раздел 5.9 ТЗ — сбой на одной странице не должен прерывать поиск."""

    pages = {
        "https://m7.test/c": '<a href="/broken">битая</a><a href="/ok">рабочая</a>',
        "https://m7.test/ok": '<a href="/d/manual-x.pdf">Руководство по эксплуатации</a>',
    }
    monkeypatch.setattr(site_module, "fetch_with_retry", _fake_site(pages))

    found = ManufacturerSiteAdapter().find_user_manual("https://m7.test/c", "X")

    assert found and found[0].url.endswith("manual-x.pdf")


def test_returns_empty_when_nothing_found(monkeypatch):
    monkeypatch.setattr(
        site_module, "fetch_with_retry", _fake_site({"https://m8.test/c": "<p>нет документов</p>"})
    )
    assert ManufacturerSiteAdapter().find_user_manual("https://m8.test/c", "X") == []


@pytest.mark.parametrize(
    "model,expected",
    [
        ("CE101 R5 145 M6", ["ce101", "r5", "145", "m6"]),
        ("НЕВА МТ 324", ["нева", "мт", "324"]),
        # односимвольные части отбрасываются — они дают ложные совпадения где угодно
        ("A-1", []),
    ],
)
def test_model_tokens(model, expected):
    assert _model_tokens(model) == expected


def test_download_document_text_returns_none_on_error(monkeypatch):
    def _raise(*args, **kwargs):
        raise ConnectionError("нет связи")

    monkeypatch.setattr(site_module, "fetch_with_retry", _raise)
    assert site_module.download_document_text("https://m.test/x.pdf") is None
