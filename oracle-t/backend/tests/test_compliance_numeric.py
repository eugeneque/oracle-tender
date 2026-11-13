

def test_dimensions_limit_decided_by_code():
    from app.services.compliance_numeric import ENABLED, CatalogModel, prejudge, requirement_checks

    def model(name, value):
        return CatalogModel(name, {"Габаритные размеры": value}, set())

    # Точный размер — не предел: решает модель.
    assert requirement_checks("Габариты: 258*170*74 мм") is None
    # Пояснения в значении — не разбирается.
    assert requirement_checks("Габариты: не более 159×159×60 мм (без модуля связи)") is None
    limit = "Габариты: не более 90×130×62 мм"
    big = [model("A", "182×125×55 мм"), model("B", "130×90×70")]
    verdict = prejudge(limit, big, None, ENABLED, decisive=True)
    assert verdict is not None and verdict.status == "not_meets"
    # Не decisive (конкурент): «не соответствует» код не ставит.
    assert prejudge(limit, big, None, ENABLED) is None
    ok = prejudge(limit, big + [model("C", "85×125×60 мм")], None, ENABLED, decisive=True)
    assert ok is not None and ok.status == "meets"
    # Другой порядок сторон: прибор помещается при другом положении — подходит (12.11.2026).
    moved = prejudge(limit, [model("D", "130×90×60 мм")], None, ENABLED, decisive=True)
    assert moved is not None and moved.status == "meets"
    # Без двоеточия и с пояснением порядка сторон (32616566457).
    bare = "габариты не более 120 × 100 × 80 мм (высота × ширина × глубина)"
    assert requirement_checks(bare) is not None
    no = prejudge(bare, [model("D17", "130×90×70 мм")], None, ENABLED, decisive=True)
    assert no is not None and no.status == "not_meets"
    yes = prejudge(bare, [model("W", "110×95×70 мм")], None, ENABLED, decisive=True)
    assert yes is not None and yes.status == "meets"


def test_temperature_guard_overrides_wrong_not_meets():
    """32616566457: «УХЛ2.1 … рабочий диапазон температур от -40 до +70°C» — модель ставила
    МИРТЕК ✗ при приборе на −40…+70 °C (замечание 12.11.2026)."""

    from app.models.analysis import Requirement
    from app.services.compliance_numeric import CatalogModel
    from app.services.compliance_service import RequirementVerdict, apply_temperature_guard

    text = (
        "Все поставляемое оборудование должно соответствовать требованиям климатического исполнения "
        "УХЛ2.1 по ГОСТ 15150-69 и удовлетворять требованиям к рабочему диапазону температур от -40 до +70°C."
    )
    requirements = [Requirement(text=text)]

    def run(values):
        verdicts = {1: RequirementVerdict(number=1, status="not_meets", source="catalog", explanation="нет", confidence=0.8)}
        apply_temperature_guard(requirements, verdicts, lambda: [CatalogModel("D17", values, set())])
        return verdicts[1]

    assert run({"Диапазон рабочих температур": "от -40 до +70 °С"}).status == "no_data"
    assert run({"Диапазон рабочих температур": "от минус 45 до плюс 70 °C", "Климатическое исполнение": "УХЛ1"}).status == "meets"
    assert run({"Диапазон рабочих температур": "от -25 до +60 °C"}).status == "not_meets"
    assert run({"Диапазон рабочих температур": "от -40 до +70 °С", "Климатическое исполнение": "УХЛ3.1"}).status == "not_meets"
