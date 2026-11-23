"""«Рынок ПУ»: правила аналитика по ПРОМЭНЕРГО, МИРТЕК и «Умному учёту» (19.11.2026, раздел 114).

Замечания аналитика к 3 кв. 2026 («Комментарии. Промэнерго и Миртек.docx», 16 скриншотов детализации)
просили распространить на все периоды. Отчёт считается из справочника (модификации, написания) и
помесячного агрегата, поэтому «все периоды» — это правка справочника и правил привязки, а не
пересчёт одного квартала. Правила здесь — детерминированные, без ИИ: ИИ ошибался именно на них.

1. **МИРТЕК: первичная поверка — ПП 890 «соответствует»** — только у трёх базовых строк справочника
   («МИРТЕК-12-РУ», «МИРТЕК-212-РУ», «МИРТЕК-232-РУ» без обозначения исполнения). «Несоответствующие ПУ не
   производим» — замечание аналитика 19.11.2026 относилось именно к ним (ИИ ставил «не соответствует»
   1 084 + 960 + 170 поверкам 3 кв. 2026). Решение — `smart_890_source = 'manual'`. Распространение на
   все модификации МИРТЕК (раздел 114, допущение 1) аналитик опровергла 20.11.2026: радио-исполнения
   (RF433/RF2400), W32 с классом A0.5R1 и др. она считает «не соответствует», — поэтому остальные
   строки решает ИИ или сама аналитик, а уже выставленное ею вручную правило не перезаписывает
   (раздел 119).
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
# Строки, к которым относится замечание «не производим несоответствующие» (ключи нормализации).
MIRTEK_BASE_NAMES = ("МИРТЕК-12-РУ", "МИРТЕК-212-РУ", "МИРТЕК-232-РУ")
MIRTEK_PRIMARY_NOTE = (
    "первичная поверка: МИРТЕК производит только соответствующие ПП РФ 890 ПУ (замечание аналитика 19.11.2026)"
)

# ── Счётчики газа ──────────────────────────────────────────────────────────────────────────

_GAS_KEY = re.compile(r"ome[gг]a[эe]tk")
GAS_REASON = "счётчик газа, а не электроэнергии: поверка случайно попала под номер описания типа"


def gas_reason(raw: str | None) -> str | None:
    """Причина исключения написания: «ОМЕГА ЭТК …» — газовые счётчики (замечание 19.11.2026)."""

    return GAS_REASON if raw and _GAS_KEY.search(modification_key(raw)) else None


# ── Чужие приборы и старые описания типа у МИРТЕК (ответ аналитика 23.11.2026) ──────────────

_FOREIGN_RAW = re.compile(r"^(мод\.?\s*)?(ладога|меркурий)\b", re.IGNORECASE)
_OLD_TYPE_RAW = re.compile(r"^миртек-?(301\b|3-ру(?!\d))", re.IGNORECASE)
FOREIGN_REASON = "не счётчик производителя типа (чужой прибор): не учитывается (ответ аналитика 23.11.2026)"
OLD_TYPE_REASON = "старое описание типа МИРТЕК-3-РУ / МИРТЕК-301, срок действия истёк: не учитывается (ответ аналитика 23.11.2026)"
# Опечатки поверителей: верное написание — МИРТЕК-232-РУ (67661-17). «323-РУ» — только под этим типом.
_TYPO_KEYS = {modification_key("МИРТЕК-2232-РУ"): None, modification_key("МИРТЕК-323-РУ"): "67661-17"}
TYPO_TARGET = "МИРТЕК-232-РУ"


def mirtek_exclusion_reason(raw: str | None) -> str | None:
    """Причина не учитывать написание типа МИРТЕК: чужой прибор («Ладога 0102», «Меркурий 234 …») или старое описание."""

    if not raw:
        return None
    text_ = raw.strip().casefold()
    if _FOREIGN_RAW.search(text_):
        return FOREIGN_REASON
    if _OLD_TYPE_RAW.search(text_.replace(" ", "")):
        return OLD_TYPE_REASON
    return None


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
    if spec:
        return spec.candidates
    key = modification_key(raw)
    if key in _TYPO_KEYS and _TYPO_KEYS[key] in (None, mit_number):
        return (TYPO_TARGET,)
    return ()


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


def apply_mirtek_exclusions(conn: Connection) -> int:
    """Чужие приборы и старые описания типа у МИРТЕК: метка `excluded_reason`, убрать из агрегата,
    пустые модификации-пустышки («МИРТЕК-301», «МИРТЕК-3-РУ», «Ладога 0102» …) удалить."""

    found = [
        (vid, reason, mod_id)
        for vid, raw, mod_id in conn.execute(
            text(
                "SELECT v.id, v.raw, v.modification_id FROM meter_market_variants v "
                "JOIN meter_market_types t ON t.id = v.type_id JOIN meter_market_manufacturers f ON f.id = t.manufacturer_id "
                "WHERE f.name = :f AND v.excluded_reason IS NULL"
            ),
            {"f": MIRTEK},
        )
        if (reason := mirtek_exclusion_reason(raw))
    ]
    for vid, reason, _ in found:
        conn.execute(text("UPDATE meter_market_variants SET excluded_reason = :r WHERE id = :v"), {"r": reason, "v": vid})
    ids = [vid for vid, _, _ in found]
    if ids:
        conn.execute(text("DELETE FROM meter_verification_monthly WHERE variant_id = ANY(:v)"), {"v": ids})
        # Привязка исключённого написания ни на что не влияет, а модификация-пустышка должна освободиться.
        conn.execute(text("UPDATE meter_market_variants SET modification_id = NULL, mapped_by = NULL WHERE id = ANY(:v)"), {"v": ids})
    _drop_empty_modifications(conn, [m for _, _, m in found if m])
    return len(ids)


def _drop_empty_modifications(conn: Connection, ids: list[int]) -> None:
    """Удалить модификации без написаний (после отвязки чужих приборов и опечаток)."""

    if ids:
        conn.execute(
            text(
                "DELETE FROM meter_market_modifications m WHERE m.id = ANY(:i) AND NOT EXISTS "
                "(SELECT 1 FROM meter_market_variants v WHERE v.modification_id = m.id)"
            ),
            {"i": ids},
        )


def apply_mirtek_typos(conn: Connection) -> int:
    """«МИРТЕК-2232-РУ» и «МИРТЕК-323-РУ» (под 67661-17) — опечатки, это МИРТЕК-232-РУ: написания
    переходят к модификации «МИРТЕК-232-РУ» своего типа (`manual` — ИИ не пересматривает)."""

    moved = 0
    dropped: list[int] = []
    for vid, raw, type_id, number, mod_id in conn.execute(
        text(
            "SELECT v.id, v.raw, v.type_id, t.mit_number, v.modification_id FROM meter_market_variants v "
            "JOIN meter_market_types t ON t.id = v.type_id JOIN meter_market_manufacturers f ON f.id = t.manufacturer_id "
            "WHERE f.name = :f AND v.excluded_reason IS NULL"
        ),
        {"f": MIRTEK},
    ).all():
        key = modification_key(raw)
        if key not in _TYPO_KEYS or _TYPO_KEYS[key] not in (None, number):
            continue
        target = _mod_by_key(conn, type_id).get(modification_key(TYPO_TARGET))
        if target is None or target["id"] == mod_id:
            continue
        conn.execute(
            text("UPDATE meter_market_variants SET modification_id = :m, mapped_by = 'manual' WHERE id = :v"),
            {"m": target["id"], "v": vid},
        )
        if mod_id:
            dropped.append(mod_id)
        moved += 1
    _drop_empty_modifications(conn, dropped)
    return moved


def apply_mirtek_smart(conn: Connection) -> int:
    """Три базовые строки МИРТЕК — «соответствует ПП 890» решением аналитика.

    Не трогает решение, уже выставленное человеком (`manual`) с любым значением: раньше условие
    «не true или не manual» превращало ручное «не соответствует» обратно в «да» (раздел 119)."""

    now = datetime.now(timezone.utc)
    return conn.execute(
        text(
            "UPDATE meter_market_modifications m SET smart_890 = true, smart_890_source = 'manual', "
            "smart_890_note = :n, smart_890_at = :at, updated_at = now() "
            "FROM meter_market_types t, meter_market_manufacturers f "
            "WHERE t.id = m.type_id AND f.id = t.manufacturer_id AND f.name = :f "
            "AND m.name = ANY(:names) AND m.smart_890_source IS DISTINCT FROM 'manual'"
        ),
        {"n": MIRTEK_PRIMARY_NOTE, "at": now, "f": MIRTEK, "names": list(MIRTEK_BASE_NAMES)},
    ).rowcount or 0


# ── МИРТЕК: протокол Pn в обозначении (22.11.2026, раздел 120) ─────────────────────────────

_ELEMENT_TOKEN = re.compile(r"^(S|SS|ST|TT|T)$")
_PROTOCOL_TOKEN = re.compile(r"^P\d+$")
# Интерфейсы блоков ⑨/⑩ описания типа: RS485, RF433/1, G/5, PF/1, MOD, CAN … — не функции.
_IFACE_TOKEN = re.compile(r"^(RS232|RS485|CAN|RF\d*(/\d+)?|RFWF.*|RFLT.*|G(/[\d.]+)?|PF/\d+|PO/\d+|E/\d+|MOD(/\d+)?)$")
# Блок ⑫ «Дополнительные функции»: B, H, In, K, L, M, O, Qn, R, U, Vn, W/n, Y, Z/n — «HKMOQ1V3», «LOQ1V3», «V3».
_FUNCTIONS_TOKEN = re.compile(r"^[BHIKLMOQRUVWYZ][A-Z0-9/]*$")
PROTOCOL_NOTE_YES = "в обозначении указан протокол {p} (блок «Поддерживаемые протоколы обмена» описания типа)"
PROTOCOL_NOTE_D = "в обозначении указан признак D — измерение в двух направлениях (ответ аналитика 23.11.2026)"
PROTOCOL_NOTE_NO = (
    "явное несоответствие: в обозначении есть блок функций, но нет ни протокола Pn, ни признака D — "
    "протокол «МИРТЕК» (ответ аналитика 23.11.2026)"
)
PROTOCOL_NOTE_UNKNOWN = (
    "обозначение неполное — явного несоответствия нет, считаем «соответствует» (ответ аналитика 23.11.2026)"
)


def protocol_verdict(raw: str | None) -> tuple[bool | None, str | None]:
    """ПП 890 по написанию МИРТЕК (ответ аналитика 23.11.2026).

    «P и D — признаки соответствия, сомнительное считать соответствующим, отклоняем только явные»:
    - в обозначении есть протокол `Pn` (P1, P2) или признак `D` (два направления) — «соответствует»;
    - обозначение дошло до блока функций («HKMOQ1V3», «LOQ1V3», «V3») и нет ни `Pn`, ни `D` — «не соответствует»
      (явный случай: у отмеченных ею «нет» написаний всегда есть блок функций без `-P`);
    - обозначение неполное («МИРТЕК-232-РУ», «…-W32-A15R1», «…-ST-G») — «соответствует».
    Для пустого написания — `None`. Допущение: «P и D — одним из признаков» прочитано как «достаточно любого»."""

    if not raw:
        return None, None
    tokens = raw.upper().translate(_CYR_TO_LAT).replace(" ", "").split("-")
    start = next((i for i, t in enumerate(tokens) if _ELEMENT_TOKEN.match(t)), None)
    tail = tokens[start + 1 :] if start is not None else []
    for token in tail:
        if _PROTOCOL_TOKEN.match(token):
            return True, PROTOCOL_NOTE_YES.format(p=token)
    if "D" in tail:
        return True, PROTOCOL_NOTE_D
    if any(_FUNCTIONS_TOKEN.match(t) and not _IFACE_TOKEN.match(t) for t in tail):
        return False, PROTOCOL_NOTE_NO
    return True, PROTOCOL_NOTE_UNKNOWN


def apply_mirtek_protocol(conn: Connection) -> int:
    """Выставить `meter_market_variants.smart_890` по правилу протокола у написаний типов МИРТЕК.
    Ручное решение аналитика (`manual`) не трогается; повторный запуск ничего не меняет."""

    rows = conn.execute(
        text(
            "SELECT v.id, v.raw, v.smart_890, v.smart_890_source, v.smart_890_note FROM meter_market_variants v "
            "JOIN meter_market_types t ON t.id = v.type_id JOIN meter_market_manufacturers f ON f.id = t.manufacturer_id "
            "WHERE f.name = :f AND v.excluded_reason IS NULL AND v.smart_890_source IS DISTINCT FROM 'manual'"
        ),
        {"f": MIRTEK},
    ).all()
    now = datetime.now(timezone.utc)
    changed = 0
    for variant_id, raw, current, source, note in rows:
        verdict, why = protocol_verdict(raw)
        if verdict is None:
            if source == "rule":  # правило перестало применяться — вернуть решение модификации
                conn.execute(
                    text("UPDATE meter_market_variants SET smart_890 = NULL, smart_890_source = NULL, "
                         "smart_890_note = NULL, smart_890_at = NULL WHERE id = :v"),
                    {"v": variant_id},
                )
                changed += 1
            continue
        if current is verdict and source == "rule" and note == why:
            continue
        conn.execute(
            text("UPDATE meter_market_variants SET smart_890 = :s, smart_890_source = 'rule', smart_890_note = :n, "
                 "smart_890_at = :at WHERE id = :v"),
            {"s": verdict, "n": why, "at": now, "v": variant_id},
        )
        changed += 1
    return changed


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
    excluded = apply_mirtek_exclusions(conn)
    typos = apply_mirtek_typos(conn)
    protocol = apply_mirtek_protocol(conn)
    return {"mirtek_excluded": excluded, "mirtek_typos": typos, "mirtek_protocol": protocol, "gas_variants": gas, "smart_unit_rows": unit, "mirtek_smart": mirtek, **{f"iprom_{k}": v for k, v in iprom.items()}}


if __name__ == "__main__":
    from app.db.session import engine

    with engine.begin() as connection:
        print(apply_all(connection))
