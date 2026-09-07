"""Тесты получения документов тендера с площадок (раздел 5.2, 5.1 ТЗ).

До этой правки документы по-настоящему скачивались только с ЕИС, а остальные восемь адаптеров
подставляли вместо файлов ссылку на карточку тендера. Внешне всё выглядело исправно: документ
в карточке есть, статус «успех» — только текста нет, и ИИ-анализ по таким тендерам не работал
вовсе (а это 3400 закупок одного лишь ЭТП ГПБ).

Разметка здесь — сокращённые фрагменты реальных страниц: именно на них ловятся ошибки выбора
ячейки с именем файла и путаница между служебными ссылками площадки и документами закупки.
Сеть в тестах не дёргается — проверяются разборщики.
"""

from __future__ import annotations

import json

from app.adapters.eis_documents import normalize_eis_number
from app.adapters.etpgpb import _parse_document_items
from app.adapters.fabrikant import _find_documentation_url, _parse_documentation_page
from app.adapters.sberbank_ast import _parse_card_documents
from app.adapters.tektorg import _parse_next_data_documents
from app.adapters.zakazrf import _parse_document_links


def test_etpgpb_documents_take_name_from_block_not_url():
    """В ссылке ЭТП ГПБ вместо имени файла — хеш (`.../name/6a9581bf517144.92966613`).
    Если брать имя оттуда, в карточке будут неразличимые строки, а формат для разбора текста
    не определится."""

    html = """
    <div class="procedureCardSection">
      <h2 class="vTitle">Документация</h2>
      <div class="procedureCardDocList">
        <div class="procedureDocItem vTxt">
          <a href="https://etp.gpb.ru/file/get/t/LotDocuments/id/6985867/name/6a9581bf517144.92966613"
             class="procedureDocItem__iconWrapper"><svg></svg></a>
          <div class="procedureDocItem__info">
            <a href="https://etp.gpb.ru/file/get/t/LotDocuments/id/6985867/name/6a9581bf517144.92966613">
              Документация_38_АЭ_26_для_МСП.doc</a>
            <div>31.08.2026, 13:29</div><div>ЭЦП</div>
          </div>
        </div>
      </div>
    </div>
    """

    documents = _parse_document_items(html)

    assert len(documents) == 1, "повторяющиеся ссылки одного документа должны схлопываться"
    assert documents[0].file_name == "Документация_38_АЭ_26_для_МСП.doc"
    assert documents[0].url.startswith("https://etp.gpb.ru/file/get/")


def test_etpgpb_page_without_documents_returns_empty():
    assert _parse_document_items("<div>Документация отсутствует</div>") == []


def test_zakazrf_takes_only_files_not_signatures():
    """На карточке ЕЭТП рядом с каждым файлом висит ссылка на его электронную подпись
    (`/File/Signature/...`). Подпись — не документ закупки: скачав её вместо файла, система
    получит бессмысленный бинарник вместо текста требований."""

    html = """
    <table><tr>
      <td><a href="https://zakupki.gov.ru/44fz/filestore/public/1.0/download/priz/file.html?uid=01A0">
        n851337_Описание_объекта_закупки.doc</a></td>
      <td><a href="/File/Signature/Guid/20bdcb6e?SKey=6518">ЭП</a></td>
    </tr>
    <tr>
      <td><a href="https://zakupki.gov.ru/44fz/filestore/public/1.0/download/priz/file.html?uid=01A1">
        n851337_Приложение_4_НМЦК.xls</a></td>
      <td><a href="/File/Signature/Guid/9b877cc4?SKey=353E">ЭП</a></td>
    </tr>
    <tr><td><a href="/Home/About">О площадке</a></td></tr>
    </table>
    """

    documents = _parse_document_links(html)

    assert [document.file_name for document in documents] == [
        "n851337_Описание_объекта_закупки.doc",
        "n851337_Приложение_4_НМЦК.xls",
    ]


def test_fabrikant_finds_documentation_page_then_files():
    """У Фабриканта файлов на карточке нет — там только ссылка на страницу документации,
    и её адрес содержит внутренний идентификатор процедуры, не совпадающий с её номером."""

    card = """
    <a href="/trades/atom/PriceMonitoring/?action=view&id=1011973">Процедура</a>
    <a href="/trades/atom/PriceMonitoring/?action=file_documentations_view&procedure_id=1011973">
      Документация по мониторингу</a>
    """

    documentation_url = _find_documentation_url(card)
    assert documentation_url is not None
    assert "file_documentations_view" in documentation_url
    assert documentation_url.startswith("https://www.fabrikant.ru")

    listing = """
    <a href="/trades/atom/PriceMonitoring/?action=file_documentations_get_file&document_id=2668313">
      Скачать файлзапрос ЭТКП.docx</a>
    <a href="/trades/atom/PriceMonitoring/?action=file_documentations_get_file&document_id=2668316">
      Скачать файлПриложение № 1  ТЗ.pdf</a>
    <a href="/help">Помощь</a>
    """

    documents = _parse_documentation_page(listing)

    # Имя приклеено к тексту кнопки — префикс «Скачать файл» не должен попадать в название
    # документа, иначе все файлы в карточке начинаются одинаково.
    assert [document.file_name for document in documents] == [
        "запрос ЭТКП.docx",
        "Приложение № 1  ТЗ.pdf",
    ]


def test_fabrikant_card_without_documentation_link():
    assert _find_documentation_url("<a href='/trades/atom/View/?id=1'>Процедура</a>") is None


def test_tektorg_reads_documents_and_protocols_from_next_data():
    """ТЭК-Торг отдаёт данные страницы в `__NEXT_DATA__`; документы лежат и у самой процедуры,
    и у протоколов — в протоколах публикуются разъяснения, полезные для анализа."""

    payload = {
        "props": {
            "pageProps": {
                "procedureItem": {
                    "documents": [
                        {
                            "filename": "Техническое_задание_АО_ПЭС.docx",
                            "httpLink": "https://api.tektorg.ru/open-api/documents/procedure/19706873",
                        },
                        {
                            "filename": "Документация.doc",
                            "httpLink": "https://api.tektorg.ru/open-api/documents/procedure/19706874",
                        },
                    ],
                    "protocols": [
                        {
                            "documents": [
                                {
                                    "filename": "Протокол_подведения_итогов.docx",
                                    "httpLink": "https://api.tektorg.ru/open-api/documents/protocol/2234541",
                                }
                            ]
                        }
                    ],
                }
            }
        }
    }
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'

    documents = _parse_next_data_documents(html)

    assert [document.file_name for document in documents] == [
        "Техническое_задание_АО_ПЭС.docx",
        "Документация.doc",
        "Протокол_подведения_итогов.docx",
    ]


def test_tektorg_page_without_next_data_is_not_an_error():
    assert _parse_next_data_documents("<html><body>Ничего</body></html>") == []


def test_sberbank_ast_picks_file_name_cell_not_section_title():
    """У Сбербанк-АСТ имя файла лежит в третьей ячейке из шести, а первые две пусты. Взяв
    «первую непустую», разборщик в первом прогоне выдал заголовок секции «Файлы документации»
    вместо «Документация о закупке в электронной форме 01.DOC» — и формат файла определить
    было невозможно."""

    html = """
    <table id="PurchaseDocumentationInfo_DocFilesRO">
      <tr><th>Ид файла</th><th>Дата</th><th>Имя файла</th><th>Описание</th><th>Ссылка</th><th>Сохранить</th></tr>
      <tr><td></td><td>Файлы документации</td>
          <td>Документация о закупке в электронной форме 01.DOC</td><td></td><td></td>
          <td><a href="https://utp.sberbank-ast.ru/Trade/File/DownloadFile?fid=04b8f94e">Сохранить</a></td></tr>
    </table>
    <table id="OtherBlock">
      <tr><td>Онлайн-помощь</td>
          <td><a href="https://utp.sberbank-ast.ru/Main/File/DownLoadFile?fid=838ee153">памяткой</a></td></tr>
    </table>
    """

    documents = _parse_card_documents(html)

    assert len(documents) == 1, "справочные памятки площадки не должны попадать в документы"
    assert documents[0].file_name == "Документация о закупке в электронной форме 01.DOC"


def test_sberbank_ast_without_documents_table():
    assert _parse_card_documents("<html><body>Закупка без документов</body></html>") == []


class TestEisFallbackNumbers:
    """Запасной путь через ЕИС применим только к закупкам с реестровым номером.

    Ошибка в обе стороны стоит дорого: пропустив номер, система оставит тендер без
    документов; приняв за номер идентификатор площадки (`ПИ602053`), будет ходить в ЕИС
    впустую на каждой карточке."""

    def test_44fz_number(self):
        assert normalize_eis_number("0711200022926000024") == "0711200022926000024"

    def test_223fz_number(self):
        assert normalize_eis_number("32616326147") == "32616326147"

    def test_lot_suffix_is_stripped(self):
        # Lot-online нумерует лоты внутри закупки, в ЕИС закупка числится без суффикса.
        assert normalize_eis_number("32110674684.lot1") == "32110674684"

    def test_platform_identifiers_are_rejected(self):
        for value in ("ПИ602053", "SBR003-260002867000007.1", "АП123145", "3490451", ""):
            assert normalize_eis_number(value) is None, value

    def test_none_is_safe(self):
        assert normalize_eis_number(None) is None
