"""Реестр адаптеров источников: связывает `Source.adapter_key` с классом-реализацией
`SourceAdapter`. Планировщик и сервис опроса обращаются только сюда — им не нужно знать
о конкретных классах адаптеров (раздел 6.3 ТЗ).

Здесь только **тендерные** источники. Адаптеры справочника продукции (ФГИС, сайт
производителя) реализуют другой контракт — «карточка типа СИ» / «карточка товара»
вместо «список тендеров», — поэтому живут в отдельном реестре `CATALOG_ADAPTER_KEYS`
ниже, и `get_adapter` их сознательно не отдаёт: иначе плановый опрос площадок начал бы
вызывать у них несуществующий `list_new_tenders`."""

from __future__ import annotations

from app.adapters.base import SourceAdapter
from app.adapters.eis import EisAdapter
from app.adapters.etpgpb import EtpgpbAdapter
from app.adapters.etprf import EtprfAdapter
from app.adapters.fabrikant import FabrikantAdapter
from app.adapters.lot_online import LotOnlineAdapter
from app.adapters.roseltorg import RoseltorgAdapter
from app.adapters.sberbank_ast import SberbankAstAdapter
from app.adapters.tektorg import TektorgAdapter
from app.adapters.zakazrf import ZakazrfAdapter

_ADAPTERS: dict[str, type[SourceAdapter]] = {
    "eis": EisAdapter,
    "zakazrf": ZakazrfAdapter,
    "roseltorg": RoseltorgAdapter,
    "etprf": EtprfAdapter,
    "etpgpb": EtpgpbAdapter,
    "sberbank_ast": SberbankAstAdapter,
    "fabrikant": FabrikantAdapter,
    "tektorg": TektorgAdapter,
    "lot_online": LotOnlineAdapter,
}


def get_adapter(
    adapter_key: str | None, *, search_keywords: list[str] | None = None
) -> SourceAdapter | None:
    """Создаёт адаптер источника.

    `search_keywords` приходит из профиля релевантности (раздел 5.1.1 ТЗ) и заменяет
    зашитые в модуле адаптера умолчания. Пока охват жил константами в коде, система искала
    только «счетчик электрической энергии» и «прибор учета электрической энергии» — целые
    типы закупок (поверка, монтаж, обслуживание) не попадали в базу вовсе. `None` оставляет
    умолчания адаптера: так работают тесты и разовые запуски вне профиля.
    """

    if adapter_key is None:
        return None
    adapter_cls = _ADAPTERS.get(adapter_key)
    if adapter_cls is None:
        return None
    if search_keywords:
        try:
            return adapter_cls(search_keywords=list(search_keywords))
        except TypeError:
            # Не каждый адаптер ищет по ключевым словам (например, тот, что забирает всю
            # ленту целиком) — для него ключи просто неприменимы.
            return adapter_cls()
    return adapter_cls()


def registered_adapter_keys() -> list[str]:
    return list(_ADAPTERS.keys())


# --- Адаптеры справочника продукции (раздел 4.2, 4.3, 5.3 ТЗ) ---
#
# Отдельный реестр, потому что интерфейс другой: у ФГИС это «найти тип СИ и скачать
# «Описание типа»», у сайта производителя — «обойти каталог и снять характеристики».
# Общее у них с тендерными адаптерами — принцип (список → детали → изоляция ошибок →
# журнал) и то, что ядро (планировщик, сервис синхронизации) обращается к ним только через
# ключ источника, не зная классов. Сами классы здесь не импортируются: этот модуль грузится
# на старте приложения, а адаптеры каталога нужны только тем сервисам, что их вызывают.
CATALOG_ADAPTER_KEYS: frozenset[str] = frozenset({"fgis", "mirtek_site"})


def is_catalog_adapter(adapter_key: str | None) -> bool:
    return adapter_key in CATALOG_ADAPTER_KEYS
