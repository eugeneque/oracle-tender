"""Справочные данные источников тендеров (раздел 4.1 ТЗ — 12 источников, включая ЕИС).

Каждая запись — (key, name, url, type, adapter_status, note). `key` — стабильный
машинный идентификатор источника, используется для связи строки `sources` с классом
адаптера в `app/adapters/registry.py` (в самом ТЗ не описан явно, так как раздел 7 даёт
только структуру таблиц; без него нечем было бы сопоставить строку БД с кодом адаптера).

`adapter_status` на момент сидирования отражает состояние по волне подключения источников
(раздел 9 ТЗ, Этап 2 → Этап 12): реализованные адаптеры перечислены как `implemented`,
остальные — `not_implemented` с примечанием об очереди. Это поле обновляется по мере
реализации новых адаптеров (см. `app/adapters/registry.py`), сидирование задаёт только
стартовое состояние при первом создании таблицы.
"""

# (key, name, url, type, adapter_status, note)
SOURCES: list[tuple[str, str, str, str, str, str | None]] = [
    (
        "eis",
        "ЕИС",
        "https://zakupki.gov.ru/epz/order/extendedsearch/results.html",
        "eis",
        "implemented",
        None,
    ),
    (
        "rts_tender",
        "РТС-тендер",
        "https://www.rts-tender.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ). При предварительной проверке площадка отвечала"
        " HTTP 503 — похоже на защиту от ботов; потребуется отдельное исследование обхода.",
    ),
    (
        "fabrikant",
        "Фабрикант",
        "https://www.fabrikant.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
    (
        "lot_online",
        "Lot-online",
        "https://gz.lot-online.ru/etp_front/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
    (
        "tektorg",
        "Tektorg",
        "https://www.tektorg.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ). Фронтенд построен на Next.js — вероятно"
        " потребует Playwright.",
    ),
    (
        "etpgpb",
        "ЭТП ГПБ",
        "https://etpgpb.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
    (
        "etpgpb_strateg",
        "ЭТП ГПБ (Стратег)",
        "https://etpgpb.ru/products/strateg/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
    (
        "astgoz",
        "АСТ ГОЗ",
        "https://www.astgoz.ru/page/index",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
    (
        "etprf",
        "ЭТП РФ",
        "https://etprf.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
    (
        "sberbank_ast",
        "Сбербанк-АСТ",
        "https://www.sberbank-ast.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ). Фронтенд — Vue-приложение (`<div id=\"app\">`)"
        " — вероятно потребует Playwright.",
    ),
    (
        "roseltorg",
        "Росэлторг",
        "https://www.roseltorg.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
    (
        "zakazrf",
        "ЕЭТП (zakazrf)",
        "https://etp.zakazrf.ru/",
        "etp_federal_commercial",
        "not_implemented",
        "В очереди на реализацию (Этап 12 ТЗ).",
    ),
]


# Источники справочника продукции (раздел 4.2, 4.3, 5.3 ТЗ) — не тендерные площадки.
#
# Заведены отдельным списком, а не строками в `SOURCES`, по двум причинам. Во-первых,
# исторической: `SOURCES` сидируется миграцией 0003, а эти источники появились в 0031, и
# слияние списков создало бы на существующих базах дубли по уникальному `key`. Во-вторых,
# смысловой: у них другой контракт адаптера (не «список тендеров», а «карточка типа СИ» /
# «карточка товара»), и опрос тендеров обязан их пропускать — см. `CATALOG_SOURCE_TYPES`
# в app/models/source.py.
#
# (key, name, url, type, adapter_key, adapter_status, polling_schedule, note)
CATALOG_SOURCES: list[tuple[str, str, str, str, str, str, str, str | None]] = [
    (
        "fgis",
        "ФГИС «Аршин» (Госреестр СИ)",
        "https://fgis.gost.ru/fundmetrology/cm/mits",
        "fgis",
        "fgis",
        "implemented",
        "daily",
        "Источник характеристик продукции, а не тендеров (раздел 4.2, 5.3 ТЗ): реестр"
        " утверждённых типов СИ и документы «Описание типа». Работает по двум триггерам —"
        " ревалидация сохранённых карточек по расписанию и поиск по событию из модуля"
        " сопоставления продукции.",
    ),
    (
        "mirtek_site",
        "Сайт МИРТЕК (каталог продукции)",
        "https://mirtekgroup.com/produkciya",
        "manufacturer_site",
        "mirtek_site",
        "implemented",
        "weekly",
        "Каталог собственной продукции МИРТЕК — основной источник эксплуатационных"
        " характеристик для своих моделей (в «Описании типа» ФГИС только сертификационные"
        " данные). Собираются обе категории электросчётчиков и все российские заводские"
        " исполнения (Таганрог, Владивосток); «Казахстан» и «Беларусь» не собираются —"
        " для российских тендеров нерелевантны.",
    ),
]


# Сайты производителей-конкурентов (раздел 4.3, 5.3 ТЗ). Разведаны вручную 04.09.2026,
# профили разметки — в `app/adapters/manufacturer_catalog.py`.
#
# Отдельным списком от `CATALOG_SOURCES` по той же причине, что и тот от `SOURCES`:
# сидируются разными миграциями, и слияние создало бы дубли по уникальному `key`.
COMPETITOR_CATALOG_SOURCES: list[tuple[str, str, str, str, str, str, str, str | None]] = [
    (
        "energomera",
        "Сайт Энергомеры (каталог счётчиков)",
        "https://www.energomera.ru/ru/products/meters",
        "manufacturer_site",
        "energomera",
        "implemented",
        "weekly",
        "Четыре категории электросчётчиков плюс раздел «снятые с серийного производства» —"
        " он и даёт статус. Высоковольтные приборы учёта не собираются, как и у МИРТЕК."
        " robots.txt закрывает /documentations/: ссылки на документы сохраняются, файлы"
        " не скачиваются.",
    ),
    (
        "kpsz",
        "Сайт КПЗ (каталог счётчиков)",
        "https://kpsz.ru/production/",
        "manufacturer_site",
        "kpsz",
        "implemented",
        "weekly",
        "Вся продукция на одной странице, категории размечены заголовками секций."
        " Домен без www: www.kpsz.ru редиректит, и без перехода по редиректу каталог"
        " не приходит вовсе. «Дисплей потребителя» в справочник не идёт — не счётчик.",
    ),
    (
        "promenergo",
        "Сайт Промэнерго (каталог счётчиков)",
        "https://promenergo-rt.ru/production/ipu/",
        "manufacturer_site",
        "promenergo",
        "implemented",
        "weekly",
        "Одна категория «Интеллектуальные приборы учёта электроэнергии». Название позиции"
        " длинное («Однофазный прибор учета электроэнергии i-PROM.1»), в справочник идёт"
        " код модели, полное название — в «Наименование полное».",
    ),
]


# Вторая волна сайтов конкурентов (разведка 05.09.2026). Профили — там же,
# в `app/adapters/manufacturer_catalog.py`.
COMPETITOR_CATALOG_SOURCES_WAVE2: list[tuple[str, str, str, str, str, str, str, str | None]] = [
    (
        "taipit",
        "Сайт Тайпит (каталог НЕВА)",
        "https://www.meters.taipit.ru/catalog/neva/",
        "manufacturer_site",
        "taipit",
        "implemented",
        "weekly",
        "Четыре категории счётчиков НЕВА плюс раздел «Снятые с производства» — он и даёт"
        " статус. Позиции адресуются числовым идентификатором, наименование позиции"
        " («НЕВА МТ 113 AS OP 5(100) А») и есть обозначение прибора целиком.",
    ),
    (
        "nartis",
        "Сайт Нартис (приборы учёта электроэнергии)",
        "https://www.nartis.ru/catalog/pribory-ucheta-elektricheskoy-energii/",
        "manufacturer_site",
        "nartis",
        "implemented",
        "weekly",
        "Таблицы характеристик на карточке нет — только описание, документы и ПО;"
        " характеристики извлекаются AI-разбором описания. Полное наименование берётся из"
        " листинга: H1 карточки короткий («НАРТИС-И100»).",
    ),
    (
        "rotek",
        "Сайт Ротек (приборы учёта электроэнергии)",
        "https://rotek.ru/product/smart-pribory-ucheta1/pribory-ucheta-elektroenergii/",
        "manufacturer_site",
        "rotek",
        "implemented",
        "weekly",
        "Четыре модели РТМ; карточки лежат на первом уровне (/product/rotek-rtm-01s/),"
        " а не внутри раздела, и ссылки на них абсолютные на rotek.ru без www.",
    ),
]

# Третья волна сайтов конкурентов (разведка 05.09.2026). Профили — там же,
# в `app/adapters/manufacturer_catalog.py`.
COMPETITOR_CATALOG_SOURCES_WAVE3: list[tuple[str, str, str, str, str, str, str, str | None]] = [
    (
        "waviot",
        "Сайт WAVIoT (счётчики электрической энергии)",
        "https://waviot.ru/catalog/power-meters/",
        "manufacturer_site",
        "waviot",
        "implemented",
        "weekly",
        "Пять моделей ФОБОС. Таблиц характеристик на карточке нет — они свёрстаны парами"
        " блоков; юрлицо-изготовитель («ООО «Телематические Решения») указано прямо на"
        " карточке товара.",
    ),
    (
        "rim",
        "Сайт РиМ (счётчики электроэнергии)",
        "https://www.ao-rim.ru/product/schyetchiki/",
        "manufacturer_site",
        "rim",
        "implemented",
        "weekly",
        "Классическое и сплит-исполнение; высоковольтные счётчики в справочник не идут."
        " Наименование — обозначение прибора («РиМ 189.40»), полного названия сайт не даёт"
        " ни в листинге, ни на карточке.",
    ),
    (
        "milur",
        "Сайт Милур (счётчики электрической энергии)",
        "https://miluris.ru/produktsiya/",
        "manufacturer_site",
        "milur",
        "implemented",
        "weekly",
        "Однофазные, трёхфазные и снятые с производства; листинг разбит на страницы"
        " (PAGEN_1). Наименование позиции и есть обозначение прибора с исполнением"
        " («Милур 107S.22-RZ-1L-DT»).",
    ),
    (
        "incotex",
        "Сайт Инкотекс (счётчики Меркурий)",
        "https://www.incotexcom.ru/catalogue",
        "manufacturer_site",
        "incotex",
        "implemented",
        "weekly",
        "robots.txt сайта требует Crawl-delay: 10 — обход идёт с паузой в десять секунд и"
        " потому долгий. Карточки адресуются коротким кодом модели (/catalogue/201-8).",
    ),
    (
        "mir",
        "Сайт НПО МИР (умные счётчики электроэнергии)",
        "https://mir-omsk.ru/products/equipment/smart-metering-unit/",
        "manufacturer_site",
        "mir",
        "implemented",
        "weekly",
        "Восемь моделей МИР С-04/С-05/С-07. H1 карточки набран прописными, поэтому"
        " наименование берётся из H2 и листинга. На карточках лежат свидетельства об"
        " утверждении типа СИ.",
    ),
    (
        "pulsar",
        "Сайт Пульсар (счётчики электроэнергии)",
        "https://pulsarm.ru/products/pribory-ucheta/schetchiki-elektroenergii-elektroschetchiki/",
        "manufacturer_site",
        "pulsar",
        "implemented",
        "weekly",
        "Однофазные и трёхфазные, листинг разбит на страницы (PAGEN_1); в разделах счётчиков"
        " лежат также антенны, ретрансляторы и терминалы — они отсеиваются. Изготовитель —"
        " рязанское НПП «Тепловодохран», pulsarm.ru его торговый дом.",
    ),
]
