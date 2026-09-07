import uuid
from datetime import datetime

from pydantic import BaseModel


class LogEntryOut(BaseModel):
    """Строка журнала в формате раздела 5.9 ТЗ: время, уровень, компонент, действие,
    результат, детали, пользователь."""

    id: uuid.UUID
    timestamp: datetime
    level: str
    component: str
    action: str
    result: str
    details: str | None
    user_name: str | None


class LogPage(BaseModel):
    """Страница журнала. `total` считается отдельным запросом — в интерфейсе видно, сколько
    записей отобрал фильтр, а не только сколько поместилось на экран."""

    items: list[LogEntryOut]
    total: int


class LogFacets(BaseModel):
    """Значения для выпадающих списков фильтра — берутся из самих данных, а не из
    захардкоженного перечня: компоненты добавляются вместе с новыми сервисами."""

    components: list[str]
    levels: list[str]
