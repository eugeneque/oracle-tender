"""«Рынок ПУ»: правила аналитика по ПРОМЭНЕРГО, МИРТЕК и «Умному учёту» (19.11.2026, раздел 114).

Замечания аналитика к 3 кв. 2026 («Комментарии. Промэнерго и Миртек.docx», 16 скриншотов детализации)
просили распространить на все периоды. Отчёт считается из справочника (модификации, написания) и
помесячного агрегата, поэтому «все периоды» — это правка справочника и правил привязки, а не
пересчёт одного квартала. Правила здесь — детерминированные, без ИИ: ИИ ошибался именно на них.

1. **МИРТЕК: первичная поверка — ПП 890 «соответствует».** «Несоответствующие ПУ не производим» —
   у типов МИРТЕК (61891-15, 65634-16, 67661-17, 67662-17) все модификации помечаются решением
   аналитика (`smart_890_source = 'manual'`): раньше ИИ ставил «не соответствует» 1 084 + 960 + 170
   поверкам 3 кв. 2026 (МИРТЕК-232-РУ, МИРТЕК-212-РУ, МИРТЕК-12-РУ без обозначения).
2. **Счётчики газа под номером описания типа** («ОМЕГА ЭТК GSM G4» у 61891-15, 22 поверки) попали
   туда случайно и не должны учитываться вовсе: у написания `excluded_reason`, агрегат его не
   содержит (`meter_market_loader.rebuild_monthly`), загрузчик ставит метку новым написаниям.
3. **«Нет модификации» с буквой U в заводском номере** у 61891-15 — выпуск ООО «Умный учет» по
   описанию типа МИРТЕК (10 085 поверок 3 кв. 2026): учитываются у «Умного учёта» как
   «МИРТЕК-12-РУ D17-2» (DIN-рейка, Bluetooth + mod, в РПП, ПП 890 «соответствует») — отдельное
   написание, потому что агрегат хранится по написанию, а не по заводскому номеру.
4. **ПРОМЭНЕРГО i-prom** (87373-22 — только i-prom.1, 87388-22 и 95540-25 — i-prom.3). «Модификации
   i-prom.2 не существует», ведущая цифра написания («2-1/2-M-R-Y-Y», «3-1/2-M-RG-Y-Y» у 87373-22) —
   не номер модели; корпус в обозначении — S (Split), M (М-МКД, DIN-рейка), P (Р-Моноблок), T —
   трансформаторное включение; «RR» — два интерфейса RS-485. Все исполнения внесены в РПП
   26.02.2025: 115 баллов, трансформаторного включения (3T) — 120. Написания приводятся к
   исполнениям справочника аналитика («i-prom.1 M-RG-Y», «i-prom.3T P-RG», «i-prom.3 3T-P-RG-N»).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services.meter_market_normalize import modification_key

MIRTEK = "МИРТЕК"
SMART_UNIT = "Умный учет"
MIRTEK_PRIMARY_NOTE = (
    "первичная поверка: МИРТЕК производит только соответствующие ПП РФ 890 ПУ (замечание аналитика 19.11.2026)"
)

# ── Счётчики газа ──────────────────────────────────────────────────────────────────────────

_GAS_KEY = re.compile(r"ome[gг]a[эe]tk")
GAS_REASON = "счётчик газа, а не электроэнергии: поверка случайно попала под номер описания типа"


def gas_reason(raw: str | None) -> str | None:
    """Причина исключения написания: «ОМЕГА ЭТК …» — газовые счётчики (замечание 19.11.2026)."""

    return GAS_REASON if raw and _GAS_KEY.search(modification_key(raw)) else None


# ── «Умный учет»: буква U в заводском номере ────────────────────────────────────────────────

SMART_UNIT_TYPE = "61891-15"
SMART_UNIT_RAW = "Нет модификации"
SMART_UNIT_SUFFIX = " · зав. № с U (Умный учет)"
SMART_UNIT_SOURCE_MOD = "МИРТЕК-12-РУ D17-2"
SMART_UNIT_MOD = "МИРТЕК-12-РУ D17-2 (Умный учет)"
_SERIAL_U = re.compile(r"[UУ]")  # латинская и кириллическая


def split_raw(mit_number: str | None, raw: str, serial: str | None) -> str | None:
    """Написание, под которым учитывается поверка с таким заводским номером, если оно отличается."""

    if mit_number == SMART_UNIT_TYPE and raw.strip() == SMART_UNIT_RAW and serial and _SERIAL_U.search(serial):
        return SMART_UNIT_RAW + SMART_UNIT_SUFFIX
    return None


# ── ПРОМЭНЕРГО i-prom ──────────────────────────────────────────────────────────────────────

RPP_DATE = date(2025, 2, 26)
SCORE_DIRECT = 115.0
SCORE_TRANSFORMER = 120.0
_CYR_TO_LAT = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")
_BODY_IFACE = re.compile(r"(?:^|[-/\d])([MSP])-([RGFL]{1,4})(?=-|$)")
_PREFIX = re.compile(r"^I-PROM\.?\d?T?-?")
_DESIGN = {"M": "din", "S": "split", "P": "cabinet"}
_INTERFACE = {
    "R": "RS",
    "RR": "RS",  # два интерфейса RS-485 (замечание 19.11.2026)
    "G": "GSM",
    "RG": "GSM и RS",
    "GR": "GSM и RS",
    "RF": "RF и RS",
    "FR": "RF и RS",
    "RL": "LoRaWAN и RS",
    "LR": "LoRaWAN и RS",
    "RGF": "RS, RF и GSM",
    "GRF": "RS, RF и GSM",
    "RFG": "RS, RF и GSM",
}
IPROM_NOTES = (
    "Обозначение i-prom: корпус S — Split, M — М-МКД (DIN-рейка), P — Р-Моноблок; T — трансформаторное "
    "включение. Интерфейсы: R — RS-485, RR — два RS-485, G — GSM, F — RF, L — LoRaWAN. 87373-22 — только "
    "i-prom.1 (i-prom.2 не существует, ведущая цифра написания не номер модели), 87388-22 и 95540-25 — i-prom.3. "
    "Все исполнения внесены в РПП 26.02.2025: 115 баллов, трансформаторного включения — 120."
)


@dataclass(frozen=True)
class IpromSpec:
    """Исполнение i-prom, к которому приводится написание: имена-кандидаты (первое — каноническое)."""

    candidates: tuple[str, ...]
    phase: str
    design: str
    interface: str
    score: float

    @property
    def name(self) -> str:
        return self.candidates[0]


def decode_iprom(mit_number: str | None, raw: str | None) -> IpromSpec | None:
    """Исполнение i-prom по написанию из поверки; `None` — правило не применимо (написание остаётся
    как есть: ложная привязка хуже честного «не размечено»)."""

    if mit_number not in ("87373-22", "87388-22", "95540-25") or not raw:
        return None
    code = raw.upper().translate(_CYR_TO_LAT).replace(",", ".").replace(" ", "")
    code = _PREFIX.sub("", code, count=1) if code.startswith("I-PROM") else code
    found = _BODY_IFACE.search(code)
    if found is None:
        return None
    body, letters = found.groups()
    interface = _INTERFACE.get(letters)
    if interface is None or not code[:1].isdigit():
        return None
    transformer = code.startswith("3T")
    if mit_number == "87373-22":
        if code[0] not in "123" or transformer or body == "P":
            return None
        return IpromSpec(
            (f"i-prom.1 {body}-{letters}-Y", f"i-prom.1 {body}-{letters}"), "1ф", _DESIGN[body], interface, SCORE_DIRECT
        )
    if not code.startswith("3") or body == "M":
        return None
    if mit_number == "95540-25":
        if not transformer or body != "P":
            return None
        return IpromSpec(
            (f"i-prom.3 3T-P-{letters}-N", f"3T-P-{letters}-N"), "3ф", "cabinet", interface, SCORE_TRANSFORMER
        )
    if transformer:
        if body != "P":
            return None
        return IpromSpec(
            (f"i-prom.3T P-{letters}", f"i-prom.3 3T-P-{letters}-N"), "3ф", "cabinet", interface, SCORE_TRANSFORMER
        )
    return IpromSpec(
        (f"i-prom.3 {body}-{letters}-Y", f"i-prom.3 {body}-{letters}", f"i-prom.3 {body}-{letters}-Y-N"),
        "3ф",
        _DESIGN[body],
        interface,
        SCORE_DIRECT,
    )


def rule_candidates(mit_number: str | None, raw: str | None) -> tuple[str, ...]:
    """Имена модификаций справочника, куда правило отправляет написание (для загрузчика и `remap_auto`)."""

    spec = decode_iprom(mit_number, raw)
    return spec.candidates if spec else ()


# ── Применение к справочнику (миграция и командная строка) ─────────────────────────────────


def _mod_by_key(conn: Connection, type_id: int) -> dict[str, dict]:
    rows = conn.execute(
        text(
            "SELECT id, name, in_rpp, score, rpp_included_at, interface, design, phase, smart_890, smart_890_source, "
            "localization_source FROM meter_market_modifications WHERE type_id = :t ORDER BY id"
        ),
        {"t": type_id},
    ).mappings()
    result: dict[str, dict] = {}
    for row in rows:
        result.setdefault(modification_key(row["name"]), dict(row))
    return result


def _recount(conn: Connection, variant_ids: list[int]) -> None:
    """Помесячный агрегат для написаний: пересчёт из сырых поверок (без остальных ~40 тыс. написаний)."""

    if not variant_ids:
        return
    conn.execute(text("DELETE FROM meter_verification_monthly WHERE variant_id = ANY(:v)"), {"v": variant_ids})
    conn.execute(
        text(
            "INSERT INTO meter_verification_monthly (variant_id, month, count) "
            "SELECT mv.variant_id, date_trunc('month', mv.verification_date)::date, count(*) "
            "FROM meter_verifications mv JOIN meter_market_variants v ON v.id = mv.variant_id "
            "WHERE mv.variant_id = ANY(:v) AND mv.applicable AND NOT mv.annulled AND v.excluded_reason IS NULL "
            "GROUP BY 1, 2"
        ),
        {"v": variant_ids},
    )


def _manufacturer_id(conn: Connection, name: str) -> int | None:
    return conn.execute(text("SELECT id FROM meter_market_manufacturers WHERE name = :n"), {"n": name}).scalar()


def _type_id(conn: Connection, mit_number: str) -> int | None:
    return conn.execute(text("SELECT id FROM meter_market_types WHERE mit_number = :n"), {"n": mit_number}).scalar()


def apply_gas_exclusion(conn: Connection) -> int:
    """Метит написания газовых счётчиков и убирает их из агрегата. Возвращает число написаний."""

    ids = [
        vid
        for vid, raw in conn.execute(
            text("SELECT id, raw FROM meter_market_variants WHERE (norm_key LIKE '%omeгaэtk%' OR norm_key LIKE '%omegaetk%') AND excluded_reason IS NULL")
        )
        if gas_reason(raw)
    ]
    if ids:
        conn.execute(
            text("UPDATE meter_market_variants SET excluded_reason = :r WHERE id = ANY(:v)"), {"r": GAS_REASON, "v": ids}
        )
        conn.execute(text("DELETE FROM meter_verification_monthly WHERE variant_id = ANY(:v)"), {"v": ids})
    return len(ids)


def apply_mirtek_smart(conn: Connection) -> int:
    """У типов МИРТЕК все модификации — «соответствует ПП 890» решением аналитика."""

    now = datetime.now(timezone.utc)
    return conn.execute(
        text(
            "UPDATE meter_market_modifications m SET smart_890 = true, smart_890_source = 'manual', "
            "smart_890_note = :n, smart_890_at = :at, updated_at = now() "
            "FROM meter_market_types t, meter_market_manufacturers f "
            "WHERE t.id = m.type_id AND f.id = t.manufacturer_id AND f.name = :f "
            "AND (m.smart_890 IS DISTINCT FROM true OR m.smart_890_source IS DISTINCT FROM 'manual')"
        ),
        {"n": MIRTEK_PRIMARY_NOTE, "at": now, "f": MIRTEK},
    ).rowcount or 0


def apply_smart_unit_split(conn: Connection) -> int:
    """Поверки «Нет модификации» с U в номере у 61891-15 → «Умный учет», исполнение D17-2."""

    type_id = _type_id(conn, SMART_UNIT_TYPE)
    owner = _manufacturer_id(conn, SMART_UNIT)
    if type_id is None or owner is None:
        return 0
    source = conn.execute(
        text("SELECT * FROM meter_market_modifications WHERE type_id = :t AND name = :n"),
        {"t": type_id, "n": SMART_UNIT_SOURCE_MOD},
    ).mappings().first()
    if source is None:
        return 0
    target = conn.execute(
        text("SELECT id FROM meter_market_modifications WHERE type_id = :t AND name = :n"),
        {"t": type_id, "n": SMART_UNIT_MOD},
    ).scalar()
    if target is None:
        target = conn.execute(
            text(
                "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface, mpi_years, comment, "
                "manufacturer_override_id, in_rpp, score, rpp_included_at, rpp_record_number, localization_source, "
                "smart_890, smart_890_source, smart_890_note, smart_890_at) "
                "VALUES (:t, :n, :ph, :d, :i, :mpi, :c, :o, :in, :s, :at, :rn, 'manual', true, 'manual', :sn, now()) "
                "RETURNING id"
            ),
            {
                "t": type_id, "n": SMART_UNIT_MOD, "ph": source["phase"], "d": source["design"],
                "i": source["interface"], "mpi": source["mpi_years"], "o": owner, "in": source["in_rpp"],
                "s": source["score"], "at": source["rpp_included_at"], "rn": source["rpp_record_number"],
                "c": "Выпуск ООО «Умный учет» по описанию типа МИРТЕК: в заводском номере есть буква U "
                "(замечание аналитика 19.11.2026). Атрибуты — как у «МИРТЕК-12-РУ D17-2».",
                "sn": "DIN-рейка, Bluetooth + mod, запись в РПП; соответствует ПП 890 (замечание аналитика 19.11.2026)",
            },
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO meter_market_rpp_records (modification_id, record_number, product_name, issued_at, valid_to, "
                "score, source) SELECT :n, record_number, product_name, issued_at, valid_to, score, source "
                "FROM meter_market_rpp_records WHERE modification_id = :s"
            ),
            {"n": target, "s": source["id"]},
        )
    raw = SMART_UNIT_RAW + SMART_UNIT_SUFFIX
    variant = conn.execute(
        text("SELECT id FROM meter_market_variants WHERE type_id = :t AND raw = :r"), {"t": type_id, "r": raw}
    ).scalar()
    if variant is None:
        variant = conn.execute(
            text(
                "INSERT INTO meter_market_variants (type_id, raw, norm_key, modification_id, mapped_by) "
                "VALUES (:t, :r, :k, :m, 'manual') RETURNING id"
            ),
            {"t": type_id, "r": raw, "k": modification_key(raw), "m": target},
        ).scalar_one()
    base = conn.execute(
        text("SELECT id FROM meter_market_variants WHERE type_id = :t AND raw = :r"), {"t": type_id, "r": SMART_UNIT_RAW}
    ).scalar()
    if base is None:
        return 0
    moved = conn.execute(
        text("UPDATE meter_verifications SET variant_id = :n WHERE variant_id = :o AND mi_number ~ '[UУ]'"),
        {"n": variant, "o": base},
    ).rowcount or 0
    if moved:
        _recount(conn, [variant, base])
    return moved


def _fix_existing(conn: Connection, mod: dict, spec: IpromSpec, now: datetime) -> bool:
    """Модификация справочника, уже заведённая автоматикой или ИИ: дозаполняет РПП, интерфейс, ПП 890."""

    changes: dict[str, object] = {}
    if not mod["in_rpp"]:
        changes.update(in_rpp=True, score=spec.score, rpp_included_at=RPP_DATE, localization_source="manual")
    if mod["interface"] in (None, "н/д"):
        changes["interface"] = spec.interface
    if mod["design"] in (None, "nd"):
        changes["design"] = spec.design
    if not mod["phase"]:
        changes["phase"] = spec.phase
    if mod["smart_890"] is not True and mod["smart_890_source"] != "manual":
        changes.update(
            smart_890=True, smart_890_source="manual", smart_890_at=now,
            smart_890_note="i-prom соответствует ПП 890 (замечание аналитика 19.11.2026)",
        )
    if not changes:
        return False
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    conn.execute(text(f"UPDATE meter_market_modifications SET {sets}, updated_at = now() WHERE id = :id"), {**changes, "id": mod["id"]})
    return True


def apply_iprom(conn: Connection) -> dict[str, int]:
    """Приводит написания i-prom всех периодов к исполнениям справочника (кроме ручных привязок)."""

    stats = {"remapped": 0, "created": 0, "fixed": 0}
    now = datetime.now(timezone.utc)
    for mit_number in ("87373-22", "87388-22", "95540-25"):
        type_id = _type_id(conn, mit_number)
        if type_id is None:
            continue
        conn.execute(
            text("UPDATE meter_market_types SET code_notes = :n WHERE id = :i AND code_notes IS NULL"),
            {"n": IPROM_NOTES, "i": type_id},
        )
        mods = _mod_by_key(conn, type_id)
        variants = conn.execute(
            text(
                "SELECT id, raw, modification_id, mapped_by FROM meter_market_variants "
                "WHERE type_id = :t AND mapped_by IS DISTINCT FROM 'manual' AND excluded_reason IS NULL"
            ),
            {"t": type_id},
        ).all()
        for variant_id, raw, current, _by in variants:
            spec = decode_iprom(mit_number, raw)
            if spec is None:
                continue
            mod = next((mods[modification_key(c)] for c in spec.candidates if modification_key(c) in mods), None)
            if mod is None:
                new_id = conn.execute(
                    text(
                        "INSERT INTO meter_market_modifications (type_id, name, phase, design, interface, in_rpp, score, "
                        "rpp_included_at, localization_source, smart_890, smart_890_source, smart_890_note, smart_890_at, comment) "
                        "VALUES (:t, :n, :ph, :d, :i, true, :s, :at, 'manual', true, 'manual', :sn, now(), :c) RETURNING id"
                    ),
                    {
                        "t": type_id, "n": spec.name, "ph": spec.phase, "d": spec.design, "i": spec.interface,
                        "s": spec.score, "at": RPP_DATE,
                        "sn": "i-prom соответствует ПП 890 (замечание аналитика 19.11.2026)",
                        "c": f"Заведено по правилу аналитика 19.11.2026 (раздел 114): написание «{raw}».",
                    },
                ).scalar_one()
                mod = {
                    "id": new_id, "name": spec.name, "in_rpp": True, "interface": spec.interface, "design": spec.design,
                    "phase": spec.phase, "smart_890": True, "smart_890_source": "manual",
                }
                mods[modification_key(spec.name)] = mod
                stats["created"] += 1
            else:
                # Каноническое имя из замечаний: «3T-P-RG-N — это i-prom.3 3T-P-RG-N».
                if mod["name"] != spec.name and modification_key(mod["name"]) == modification_key(spec.candidates[-1]) \
                        and modification_key(spec.name) not in mods:
                    conn.execute(
                        text("UPDATE meter_market_modifications SET name = :n WHERE id = :i"),
                        {"n": spec.name, "i": mod["id"]},
                    )
                    mods[modification_key(spec.name)] = {**mod, "name": spec.name}
                stats["fixed"] += _fix_existing(conn, mod, spec, now)
            if current != mod["id"]:
                conn.execute(
                    text("UPDATE meter_market_variants SET modification_id = :m, mapped_by = 'manual' WHERE id = :v"),
                    {"m": mod["id"], "v": variant_id},
                )
                stats["remapped"] += 1
    return stats


def apply_all(conn: Connection) -> dict[str, int]:
    """Все правила разом; повторный запуск ничего не меняет."""

    gas = apply_gas_exclusion(conn)
    unit = apply_smart_unit_split(conn)
    iprom = apply_iprom(conn)
    mirtek = apply_mirtek_smart(conn)
    return {"gas_variants": gas, "smart_unit_rows": unit, "mirtek_smart": mirtek, **{f"iprom_{k}": v for k, v in iprom.items()}}


if __name__ == "__main__":
    from app.db.session import engine

    with engine.begin() as connection:
        print(apply_all(connection))
