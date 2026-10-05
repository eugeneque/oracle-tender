"""Личные профили релевантности и дерево ОКПД2 (05.10.2026).

Дороже всего ошибиться в трёх местах:

* **как сочетаются правила** — исключение сильнее всего, слова и коды сужают друг друга;
  «или» дало бы специалисту больше закупок, чем он выбрал;
* **кто может менять** — профили общие, но чужой фильтр правит только автор или админ;
* **фильтр списка** — профиль обязан сузить список, а удалённый профиль — объясниться, а не
  показать пустоту.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import SessionLocal
from app.models.relevance_profile import RelevanceProfile
from app.models.source import Source
from app.models.tender import Tender
from app.services import okpd2_service
from app.services.relevance_profile_service import profile_matches


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _profile(**overrides) -> RelevanceProfile:
    defaults = dict(
        id=uuid.uuid4(),
        name="Тест",
        keywords=[],
        exclusion_keywords=[],
        okpd2_codes=[],
        match_mode="any",
    )
    defaults.update(overrides)
    return RelevanceProfile(**defaults)


class _T:
    """Минимум полей, по которым фильтр читает закупку."""

    def __init__(self, title: str, okpd2_code: str | None = None):
        self.title = title
        self.customer_name = None
        self.procurement_method = None
        self.okpd2_code = okpd2_code


# --- правила ------------------------------------------------------------------------------


def test_keywords_any_and_all():
    tender = _T("Поставка счетчиков электроэнергии для АСКУЭ")
    assert profile_matches(_profile(keywords=["счетчик*", "газ"]), tender)
    assert not profile_matches(_profile(keywords=["счетчик*", "газ"], match_mode="all"), tender)
    assert profile_matches(_profile(keywords=["счетчик*", "аскуэ"], match_mode="all"), tender)


def test_exclusion_beats_everything():
    tender = _T("Поставка счетчиков воды", okpd2_code="26.51.63.121")
    profile = _profile(keywords=["счетчик*"], exclusion_keywords=["вод*"], okpd2_codes=["26.51"])
    assert not profile_matches(profile, tender)


def test_keywords_and_okpd2_narrow_each_other():
    profile = _profile(keywords=["счетчик*"], okpd2_codes=["26.51"])
    assert profile_matches(profile, _T("Счетчики", okpd2_code="26.51.63.130"))
    # Слова есть, код чужой — не подходит; код свой, слов нет — тоже.
    assert not profile_matches(profile, _T("Счетчики", okpd2_code="27.12.10"))
    assert not profile_matches(profile, _T("Поставка кабеля", okpd2_code="26.51.63.130"))
    assert not profile_matches(profile, _T("Счетчики", okpd2_code=None))


def test_only_okpd2_codes_work_without_keywords():
    profile = _profile(okpd2_codes=["26.51", "43.21"])
    assert profile_matches(profile, _T("Что угодно", okpd2_code="43.21.10.110"))
    assert not profile_matches(profile, _T("Что угодно", okpd2_code="62.01"))


def test_empty_profile_matches_nothing():
    assert not profile_matches(_profile(), _T("Счетчики"))


# --- справочник ОКПД2 ---------------------------------------------------------------------


def test_okpd2_tree_levels_and_section_selection():
    sections = okpd2_service.children_of(None)
    assert [node.code for node in sections][:3] == ["A", "B", "C"]
    assert len(sections) == 21

    section_h = okpd2_service.get_node("H")
    assert section_h is not None
    # Выбор раздела записывает его классы: буква в закупке не хранится.
    assert section_h.select_codes and all(code[:2].isdigit() for code in section_h.select_codes)
    assert {node.code for node in okpd2_service.children_of("H")} == set(section_h.select_codes)

    meters = okpd2_service.get_node("26.51.63")
    assert meters is not None and meters.has_children
    assert okpd2_service.get_node("26.51.63.130") is not None


def test_okpd2_search_by_code_and_words():
    by_code = okpd2_service.search("26.51.63")
    assert by_code and all(node.code.startswith("26.51.63") for node in by_code)
    by_words = okpd2_service.search("счетчики электроэнергии")
    assert by_words and all("электроэнерги" in node.name.lower() for node in by_words)


def test_okpd2_prefix_validation():
    assert okpd2_service.clean_prefixes(["26.51", " 26.51 ", "", "43"]) == ["26.51", "43"]
    with pytest.raises(ValueError):
        okpd2_service.clean_prefixes(["26.51%"])


def test_okpd2_dictionary_endpoints(client, admin_token):
    roots = client.get("/dictionaries/okpd2", headers=_auth(admin_token))
    assert roots.status_code == 200 and len(roots.json()) == 21
    children = client.get("/dictionaries/okpd2?parent=26.51", headers=_auth(admin_token))
    assert children.status_code == 200 and children.json()
    assert client.get("/dictionaries/okpd2?parent=99.99", headers=_auth(admin_token)).status_code == 404
    found = client.get("/dictionaries/okpd2/search?q=26.51.63", headers=_auth(admin_token))
    assert found.status_code == 200 and found.json()
    named = client.get(
        "/dictionaries/okpd2/lookup?code=26.51&code=H", headers=_auth(admin_token)
    ).json()
    assert {item["code"] for item in named} == {"26.51", "H"}


# --- API профилей и фильтр списка ---------------------------------------------------------


def _make_user(client, admin_token) -> str:
    username = f"spec_{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/users",
        headers=_auth(admin_token),
        json={"username": username, "password": "SpecPass123!", "full_name": "Специалист"},
    )
    assert created.status_code == 201, created.text
    login = client.post("/auth/login", json={"username": username, "password": "SpecPass123!"})
    return login.json()["access_token"]


def _make_tender(title: str, okpd2: str | None = None) -> Tender:
    db = SessionLocal()
    try:
        source = Source(
            key=f"rp_{uuid.uuid4().hex[:8]}", name="Площадка", url="https://example.test", type="eis"
        )
        db.add(source)
        db.flush()
        tender = Tender(
            source_id=source.id,
            external_id=f"RP-{uuid.uuid4().hex[:8]}",
            title=title,
            status="collecting_bids",
            currency="RUB",
            source_url="https://example.test/t",
            okpd2_code=okpd2,
            application_end=datetime.now(timezone.utc) + timedelta(days=3),
        )
        db.add(tender)
        db.commit()
        db.refresh(tender)
        return tender
    finally:
        db.close()


def test_profile_crud_permissions_and_list_filter(client, admin_token):
    specialist = _make_user(client, admin_token)
    marker = uuid.uuid4().hex[:6]
    mine = _make_tender(f"Поставка щитовых {marker} приборов", okpd2="26.51.63.130")
    other = _make_tender(f"Покупка бумаги {marker}", okpd2="17.12.14")

    created = client.post(
        "/relevance/profiles",
        headers=_auth(specialist),
        json={
            "name": f"Щитовые {marker}",
            "keywords": [f"{marker}"],
            "exclusion_keywords": ["бумаг*"],
            "okpd2_codes": [],
        },
    )
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["can_edit"] is True and profile["matched_count"] >= 1

    # Дубль имени у того же автора.
    again = client.post(
        "/relevance/profiles",
        headers=_auth(specialist),
        json={"name": f"щитовые {marker}", "keywords": ["x"]},
    )
    assert again.status_code == 409

    # Пустой профиль не принимается.
    empty = client.post(
        "/relevance/profiles", headers=_auth(specialist), json={"name": "Пусто"}
    )
    assert empty.status_code == 422

    # Профиль общий: админ видит, но правит и удаляет как админ; чужой специалист — нет.
    outsider = _make_user(client, admin_token)
    listed = client.get("/relevance/profiles", headers=_auth(outsider)).json()
    seen = next(item for item in listed if item["id"] == profile["id"])
    assert seen["can_edit"] is False
    denied = client.put(
        f"/relevance/profiles/{profile['id']}",
        headers=_auth(outsider),
        json={"name": "Взлом", "keywords": ["x"]},
    )
    assert denied.status_code == 403
    assert (
        client.delete(f"/relevance/profiles/{profile['id']}", headers=_auth(outsider)).status_code
        == 403
    )

    # Фильтр списка: нужная закупка есть, бумаги нет.
    page = client.get(
        f"/tenders?feed=standard&relevance_profile={profile['id']}&limit=200",
        headers=_auth(specialist),
    )
    assert page.status_code == 200, page.text
    ids = {item["id"] for item in page.json()["items"]}
    assert str(mine.id) in ids and str(other.id) not in ids

    # Правка правил пересчитывает совпадения: теперь профиль ловит и бумагу.
    updated = client.put(
        f"/relevance/profiles/{profile['id']}",
        headers=_auth(admin_token),
        json={"name": f"Щитовые {marker}", "keywords": [f"{marker}"], "okpd2_codes": []},
    )
    assert updated.status_code == 200 and updated.json()["matched_count"] >= 2
    page = client.get(
        f"/tenders?feed=standard&relevance_profile={profile['id']}&limit=200",
        headers=_auth(specialist),
    )
    assert str(other.id) in {item["id"] for item in page.json()["items"]}

    # Закупка, появившаяся после пересчёта, добирается без правки правил.
    late = _make_tender(f"Ещё щитовой {marker}")
    page = client.get(
        f"/tenders?feed=standard&relevance_profile={profile['id']}&limit=200",
        headers=_auth(specialist),
    )
    assert str(late.id) in {item["id"] for item in page.json()["items"]}

    # Удаление автором; фильтр по удалённому объясняется 404, а не пустым списком.
    assert (
        client.delete(f"/relevance/profiles/{profile['id']}", headers=_auth(specialist)).status_code
        == 204
    )
    gone = client.get(
        f"/tenders?feed=standard&relevance_profile={profile['id']}", headers=_auth(specialist)
    )
    assert gone.status_code == 404


def test_okpd2_filter_accepts_several_prefixes(client, admin_token):
    marker = uuid.uuid4().hex[:6]
    first = _make_tender(f"Один {marker}", okpd2="26.51.63.130")
    second = _make_tender(f"Два {marker}", okpd2="43.21.10.110")
    third = _make_tender(f"Три {marker}", okpd2="17.12.14")
    response = client.get(
        f"/tenders?feed=standard&search={marker}&okpd2=26.51&okpd2=43.21&limit=50",
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert ids == {str(first.id), str(second.id)} and str(third.id) not in ids
    bad = client.get("/tenders?okpd2=26.51%25", headers=_auth(admin_token))
    assert bad.status_code == 422


# --- общие профили, фразы сбора, предпросмотр, воронка ------------------------------------


def test_default_profiles_are_admin_only(client, admin_token):
    specialist = _make_user(client, admin_token)
    marker = uuid.uuid4().hex[:6]
    body = {"name": f"Общий {marker}", "keywords": ["тест*"], "is_default": True}
    assert client.post("/relevance/profiles", headers=_auth(specialist), json=body).status_code == 403

    created = client.post("/relevance/profiles", headers=_auth(admin_token), json=body)
    assert created.status_code == 201, created.text
    profile = created.json()
    assert profile["is_default"] is True and profile["okpd2_mode"] == "narrow"

    # Специалист видит общий профиль, но не правит и не удаляет его.
    listed = client.get("/relevance/profiles", headers=_auth(specialist)).json()
    seen = next(item for item in listed if item["id"] == profile["id"])
    assert seen["can_edit"] is False
    assert (
        client.put(
            f"/relevance/profiles/{profile['id']}",
            headers=_auth(specialist),
            json={**body, "is_default": False},
        ).status_code
        == 403
    )
    assert client.delete(f"/relevance/profiles/{profile['id']}", headers=_auth(specialist)).status_code == 403
    assert client.delete(f"/relevance/profiles/{profile['id']}", headers=_auth(admin_token)).status_code == 204


def test_migrated_groups_keep_either_mode():
    """Бывшие группы системного профиля — «достаточно одного»: код товара пропускает
    безликое «Поставка оборудования», как раньше."""

    profile = _profile(keywords=["счетчик*"], okpd2_codes=["26.51.63.130"], okpd2_mode="either")
    assert profile_matches(profile, _T("Поставка оборудования", okpd2_code="26.51.63.130"))
    assert profile_matches(profile, _T("Поставка счетчиков", okpd2_code=None))


def test_collection_terms_crud(client, admin_token):
    specialist = _make_user(client, admin_token)
    phrase = f"тестовая фраза {uuid.uuid4().hex[:6]}"
    assert client.post("/relevance/terms", headers=_auth(specialist), json={"phrase": phrase}).status_code == 403
    assert (
        client.post("/relevance/terms", headers=_auth(admin_token), json={"phrase": "счетчик*"}).status_code
        == 422
    )
    created = client.post("/relevance/terms", headers=_auth(admin_token), json={"phrase": f"  {phrase} "})
    assert created.status_code == 201, created.text
    term = created.json()
    assert term["phrase"] == phrase
    assert (
        client.post("/relevance/terms", headers=_auth(admin_token), json={"phrase": phrase.upper()}).status_code
        == 409
    )

    from app.services import relevance_service

    db = SessionLocal()
    try:
        assert phrase in relevance_service.search_queries(db)
        off = client.patch(
            f"/relevance/terms/{term['id']}", headers=_auth(admin_token), json={"is_active": False}
        )
        assert off.status_code == 200 and off.json()["is_active"] is False
        db.expire_all()
        assert phrase not in relevance_service.search_queries(db)
    finally:
        db.close()

    terms = client.get("/relevance/terms", headers=_auth(specialist))
    assert terms.status_code == 200 and any(item["id"] == term["id"] for item in terms.json())
    assert client.delete(f"/relevance/terms/{term['id']}", headers=_auth(admin_token)).status_code == 204


def test_preview_shows_count_and_samples(client, admin_token):
    marker = uuid.uuid4().hex[:6]
    _make_tender(f"Предпросмотр {marker} zetaword")
    _make_tender(f"Предпросмотр {marker} zetaword бумага")
    response = client.post(
        "/relevance/profiles/preview",
        headers=_auth(admin_token),
        json={"keywords": ["zetaword"], "exclusion_keywords": ["бумаг*"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] >= 1 and body["total"] >= body["count"]
    assert all("бумага" not in item["title"] for item in body["samples"])


def test_funnel_layers_add_up(client, admin_token):
    mark = uuid.uuid4().hex[:6]
    source = _make_source()
    ours = _tender_on(source, f"Воронка {mark} omegaword")
    _tender_on(source, f"Воронка {mark} чужое")
    created = client.post(
        "/relevance/profiles",
        headers=_auth(admin_token),
        json={"name": f"Воронка {mark}", "keywords": ["omegaword"], "source_keys": [source.key]},
    )
    profile_id = created.json()["id"]

    funnel = client.get(
        f"/tenders/funnel?feed=standard&relevance_profile={profile_id}&hide_ai_rejected=true&search={mark}",
        headers=_auth(admin_token),
    )
    assert funnel.status_code == 200, funnel.text
    body = funnel.json()
    # Слои убывают и сходятся с итогом списка.
    assert body["collected"] >= body["after_profiles"] >= body["after_ai"] >= body["shown"]
    assert body["shown"] == 1
    item = body["profiles"][0]
    assert item["profile_id"] == profile_id and item["matched"] == 1 and item["in_scope"] == 2
    page = client.get(
        f"/tenders?feed=standard&relevance_profile={profile_id}&hide_ai_rejected=true&search={mark}",
        headers=_auth(admin_token),
    ).json()
    assert [row["id"] for row in page["items"]] == [ours]


def test_default_selection_uses_default_profiles(client, admin_token):
    """`relevance_profile_default` — бывшая галочка «Только прошедшие профиль», теперь тем же
    механизмом, что и выбранные вручную профили."""

    mark = uuid.uuid4().hex[:6]
    ours = _make_tender(f"Поставка счетчиков электрической энергии {mark}")
    junk = _make_tender(f"Бумага для заметок {mark}")
    response = client.get(
        f"/tenders?feed=standard&relevance_profile_default=true&search={mark}",
        headers=_auth(admin_token),
    )
    assert response.status_code == 200, response.text
    ids = {item["id"] for item in response.json()["items"]}
    assert str(ours.id) in ids and str(junk.id) not in ids


# --- профили и площадки -------------------------------------------------------------------


def _make_source() -> Source:
    db = SessionLocal()
    try:
        source = Source(
            key=f"ps_{uuid.uuid4().hex[:8]}", name="Площадка", url="https://example.test", type="eis"
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source
    finally:
        db.close()


def _tender_on(source: Source, title: str) -> str:
    db = SessionLocal()
    try:
        tender = Tender(
            source_id=source.id,
            external_id=f"PS-{uuid.uuid4().hex[:8]}",
            title=title,
            status="collecting_bids",
            currency="RUB",
            source_url="https://example.test/t",
            application_end=datetime.now(timezone.utc) + timedelta(days=3),
        )
        db.add(tender)
        db.commit()
        return str(tender.id)
    finally:
        db.close()


def test_profiles_scoped_to_sources_and_combined(client, admin_token):
    mark = uuid.uuid4().hex[:6]
    a, b = _make_source(), _make_source()
    only_alpha = _tender_on(a, f"Закупка {mark} alphaword")
    only_beta = _tender_on(a, f"Закупка {mark} betaword")
    both = _tender_on(a, f"Закупка {mark} alphaword betaword")
    other_source = _tender_on(b, f"Закупка {mark} gammaword")

    def make(word: str, source_keys: list[str]) -> str:
        response = client.post(
            "/relevance/profiles",
            headers=_auth(admin_token),
            json={"name": f"{word} {mark}", "keywords": [word], "source_keys": source_keys},
        )
        assert response.status_code == 201, response.text
        assert response.json()["source_keys"] == source_keys
        return response.json()["id"]

    alpha = make("alphaword", [a.key])
    beta = make("betaword", [a.key])

    def listing(profile_ids: list[str], mode: str = "any") -> set[str]:
        query = "&".join(f"relevance_profile={pid}" for pid in profile_ids)
        response = client.get(
            f"/tenders?feed=standard&search={mark}&limit=50&{query}&relevance_profile_mode={mode}",
            headers=_auth(admin_token),
        )
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    # Один профиль на площадке A: сужает её закупки, закупку площадки B не трогает.
    assert listing([alpha]) == {only_alpha, both, other_source}
    # Два профиля на одной площадке: «любой» — объединение, «все» — пересечение.
    assert listing([alpha, beta]) == {only_alpha, only_beta, both, other_source}
    assert listing([alpha, beta], "all") == {both, other_source}

    # Профиль на несколько площадок: привязка правится без пересчёта правил.
    updated = client.put(
        f"/relevance/profiles/{alpha}",
        headers=_auth(admin_token),
        json={"name": f"alphaword {mark}", "keywords": ["alphaword"], "source_keys": [a.key, b.key]},
    )
    assert updated.status_code == 200 and updated.json()["source_keys"] == [a.key, b.key]
    assert listing([alpha]) == {only_alpha, both}

    # Без привязки — на все площадки.
    cleared = client.put(
        f"/relevance/profiles/{alpha}",
        headers=_auth(admin_token),
        json={"name": f"alphaword {mark}", "keywords": ["alphaword"], "source_keys": []},
    )
    assert cleared.json()["source_keys"] == []
    assert listing([alpha]) == {only_alpha, both}

    unknown = client.post(
        "/relevance/profiles",
        headers=_auth(admin_token),
        json={"name": f"Неизвестная {mark}", "keywords": ["x"], "source_keys": ["no_such_source"]},
    )
    assert unknown.status_code == 422


def test_concurrent_requests_do_not_collide(client, admin_token):
    """Список, доска и счётчики открываются одновременно с одним профилем: пересчёты не
    должны вставлять одни и те же совпадения (раньше — IntegrityError и 500)."""

    import threading

    from sqlalchemy import func, update

    from app.services import relevance_profile_service

    marker = uuid.uuid4().hex[:6]
    _make_tender(f"Гонка {marker} alphaword")
    created = client.post(
        "/relevance/profiles",
        headers=_auth(admin_token),
        json={"name": f"Гонка {marker}", "keywords": ["alphaword"]},
    )
    profile_id = uuid.UUID(created.json()["id"])

    db = SessionLocal()
    try:
        # Все закупки «изменились» после пересчёта — каждому запросу есть что досчитывать.
        db.execute(update(Tender).where(Tender.title.ilike("%alphaword%")).values(updated_at=func.now()))
        db.commit()
    finally:
        db.close()

    errors: list[str] = []

    def work() -> None:
        session = SessionLocal()
        try:
            relevance_profile_service.prepare_for_filter(session, profile_id)
        except Exception as exc:  # noqa: BLE001 - тест фиксирует любой сбой
            errors.append(type(exc).__name__)
        finally:
            session.close()

    threads = [threading.Thread(target=work) for _ in range(6)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert errors == []
