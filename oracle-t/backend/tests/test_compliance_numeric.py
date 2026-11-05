

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
    # Другой порядок сторон, подходящий только после перестановки — модели.
    assert prejudge(limit, [model("D", "130×90×60 мм")], None, ENABLED, decisive=True) is None
