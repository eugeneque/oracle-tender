"""Справочные данные производителей (раздел 4.3 ТЗ) — МИРТЕК (заказчик) + 12 конкурентов
из ТЗ + производители, добавленные по замечанию тестировщика 18.09.2026.

Сайт МИРТЕК и ООО «НПК "Инкотекс"» в ТЗ не указан (раздел 11, открытый вопрос №3 — заказчик
должен предоставить ссылку на продукцию Инкотекса отдельно); оставлены `None`, заполняются
позже через админ-CRUD, когда появятся данные."""

# (legal_name, brand_name, website, is_mirtek)
MANUFACTURERS: list[tuple[str, str | None, str | None, bool]] = [
    ('ООО «МИРТЕК»', "МИРТЕК", None, True),
    ('ООО «Завод Нартис»', "Нартис", "https://nartis-region.ru/counters", False),
    ('АО «Энергомера»', "Энергомера", "https://www.energomera.ru/ru/products/meters", False),
    ('ООО «НПО "МИР"»', "МИР", "https://mir-omsk.ru/products/equipment/", False),
    (
        'ООО «Телематические Решения»',
        "Waviot",
        "https://waviot.ru/catalog/",
        False,
    ),
    (
        'ООО «НПП "ТЕПЛОВОДОХРАН"»',
        "Пульсар",
        "https://pulsarm.ru/products/pribory-ucheta/schetchiki-elektroenergii-elektroschetchiki/",
        False,
    ),
    ('ООО «НПК "Инкотекс"»', "Инкотекс", None, False),
    (
        'ООО «ПРОМЭНЕРГО»',
        "Промэнерго",
        "https://promenergo-rt.ru/production/ipu/",
        False,
    ),
    ('ООО «КПЗ»', "КПЗ", "https://www.kpsz.ru/production/#prod1", False),
    ('ООО «Тайпит»', "Тайпит", "https://www.meters.taipit.ru/catalog/", False),
    (
        'АО «Радио и Микроэлектроника»',
        "РиМ",
        "https://www.ao-rim.ru/product/",
        False,
    ),
    (
        'ООО «МИЛУР Интеллектуальные Системы»',
        "Милур",
        "https://miluris.ru/produktsiya/",
        False,
    ),
    (
        'ООО «НТЦ Ротек»',
        "Ротек",
        "https://www.rotek.ru/product/promyshlennaya-avtomatizatsiya-i-industrialnyy-internet-veshchey-iiot/",
        False,
    ),
]

# Производители, добавленные по замечанию тестировщика 18.09.2026 («дополнить
# производителей»): заметные на рынке интеллектуальных приборов учёта, но не попавшие
# в раздел 4.3 ТЗ. Отдельный список, а не дописка в `MANUFACTURERS`: тот уже вставлен
# миграцией 0014, и повторная вставка на действующих базах дала бы дубли.
# (legal_name, brand_name, website, is_mirtek)
MANUFACTURERS_ADDED_2026_09: list[tuple[str, str | None, str | None, bool]] = [
    ("АО «Ленэлектро»", "Ленэлектро", "https://lenelectro.ru/product/schetchikielektroenergii", False),
    ("ООО «Матрица»", "Матрица", "https://www.matritca.ru/production/equipment/", False),
    ("ООО «ТехноЭнерго»", "ТехноЭнерго", "https://te-nn.ru/", False),
    ("ООО «Эльстер Метроника»", "Метроника (АЛЬФА)", "https://www.izmerenie.ru/catalog/", False),
]

# Доля рынка (оценка Onside за 2024 год) была здесь с 18.09.2026 и убрана 13.10.2026 по
# замечанию тендерного отдела: опубликована только у пяти производителей и в работе не нужна.
# Данные остались в миграции 0048, колонки удалены миграцией 0073.
