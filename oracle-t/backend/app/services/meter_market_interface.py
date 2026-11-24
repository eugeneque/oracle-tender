"""«Рынок ПУ»: интерфейс прибора — по коду интерфейса в самом написании (23.11.2026, раздел 122).

Замечание аналитика (Excel по «Заводу НАРТИС», 3 кв. 2026, тип 86199-22 НАРТИС-И100): интерфейс хранился
только на модификации справочника, а шаблонное сведение кладёт в одну модификацию написания с разными
кодами («НАРТИС-И100-W113-…» с RS485, RF2400/1, G/2 и без интерфейса — всё в «W113» с «RS»). Итог —
у «…-W113-2-A1R1-230-5-80A-S» (интерфейса в написании нет) стояло «RF и RS», у «…-ST-G/5-P1-…» — тоже
«RF и RS», хотя G/5 у НАРТИС — NB-IoT. Её требование: «чтобы интерфейсы сверялись с описанием типа
и руководством по эксплуатации, и спорные моменты выносились бы на согласование».

Решение того же класса, что ПП 890 по написанию (раздел 120):
- у типа — **таблица кодов интерфейсов** (`meter_market_types.interface_codes`, текст по строке на код,
  правит аналитик в карточке типа). Строка `RF2400/1 = ZigBee`; `/n` — любой номер модуля
  (`RF433/n = RF`); точный код главнее семейства. `= ? GSM` — код известен, но канал зависит от
  номера модуля (это знает только РЭ): написание уходит на согласование, «GSM» — предложение Совы.
  `A + B = ? RF, Bluetooth` — сочетание кодов, которое аналитику надо подтвердить. После `#` —
  комментарий; комментарий «из описания типа: …» помечает строку, взятую из описания типа, а не от аналитика:
  её расшифровка, расходящаяся с интерфейсом модификации, тоже идёт на согласование.
- `apply_interface_codes` раскладывает написание на токены и находит коды позиций 9–10 условного
  обозначения («Основной / Дополнительные интерфейсы» описания типа). Нет ни одного кода — «н/д»
  (интерфейс модификации **не** подставляется: ответ пользователя 23.11.2026 «н/д всегда»); код похож на
  интерфейс, но в таблице его нет, — на согласование.
- Отчёт берёт `coalesce(v.interface, m.interface)` (`INTERFACE_SQL`): у спорного написания значение пустое
  до решения аналитика, поэтому в отчёте остаётся интерфейс модификации, а предложение лежит в
  `interface_suggested`. Ручное решение (`interface_source = 'manual'`) правило не перезаписывает.
- Таблица работает только у типов, где она заполнена: у других производителей обозначения устроены
  иначе (НАРТИС-300 «131GAL», НЕВА «G4»), и молча менять им интерфейсы по общему разбору нельзя.
  «Заполнить из описания типа» (`codes_from_description`) — черновик таблицы для любого типа.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Connection

NO_INTERFACE = "н/д"
FROM_DESCRIPTION = "из описания типа"  # комментарий строки, взятой из описания типа, а не от аналитика
NOTE_NO_CODE = (
    "в написании нет кода интерфейса (позиции «Основной / Дополнительные интерфейсы» условного обозначения) — "
    "«н/д»; интерфейс модификации не подставляется (замечание аналитика 23.11.2026)"
)
# Порядок каналов в подписи: одно сочетание — одна подпись, иначе срез «Интерфейс» разъедется на «RS, RF»
# и «RF, RS». Неизвестные подписи — в конце по алфавиту.
ORDER = ("RS", "RS-232", "CAN", "RF", "PLC", "GSM", "NB-IoT", "LTE", "LoRaWAN", "NB-Fi", "ZigBee", "Bluetooth", "Wi-Fi", "Ethernet")
_CYR_TO_LAT = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")
_SPLIT = re.compile(r"[-\s,;]+")
_COUNT = re.compile(r"^\d+[X×]")  # «2×RS485» — два интерфейса RS-485
# Похоже на код интерфейса, даже если в таблице его нет («2400/1» без RF, «G1» вместо «G/1»).
_LOOKS_LIKE = re.compile(r"^(RS\d{3}|RF\d{3,4}|RFWF|RFLT|CAN|(G|PL|E)/\d+|\d{3,4}/\d+|G\d)(/\d+)?$")


@dataclass(frozen=True)
class CodeEntry:
    code: str  # «RF2400/1» или семейство «RF433/n»
    label: str | None  # None — подписи нет, только согласование
    review: bool = False
    comment: str = ""

    @property
    def from_description(self) -> bool:
        return self.comment.lower().startswith(FROM_DESCRIPTION)


@dataclass(frozen=True)
class Combo:
    codes: frozenset[str]
    label: str | None
    comment: str = ""


@dataclass
class CodeTable:
    exact: dict[str, CodeEntry] = field(default_factory=dict)
    family: dict[str, CodeEntry] = field(default_factory=dict)  # ключ — «RF433»
    combos: list[Combo] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.exact or self.family)

    def find(self, token: str) -> tuple[str, CodeEntry] | None:
        """Код таблицы для токена: точный, затем семейство «/n», затем без хвоста «/n» («RS485/1» → RS485)."""

        if token in self.exact:
            return token, self.exact[token]
        base, _, number = token.partition("/")
        if number and base in self.family:
            return f"{base}/n", self.family[base]
        if number and base in self.exact:
            return base, self.exact[base]
        return None


def _normalize_code(value: str) -> str:
    return value.strip().upper().translate(_CYR_TO_LAT).replace(" ", "")


def parse_table(source: str | None) -> CodeTable:
    """Текст таблицы → коды. Строки без «=» и пустые пропускаются (это свободный текст)."""

    table = CodeTable()
    for line in (source or "").splitlines():
        body, _, comment = line.partition("#")
        if "=" not in body:
            continue
        left, _, right = body.partition("=")
        right = right.strip()
        review = right.startswith("?")
        label = right.lstrip("?").strip() or None
        codes = [_normalize_code(c) for c in left.split("+") if c.strip()]
        if not codes:
            continue
        if len(codes) > 1:
            table.combos.append(Combo(frozenset(codes), label, comment.strip()))
            continue
        code = codes[0]
        entry = CodeEntry(code, label, review, comment.strip())
        if code.endswith("/N"):
            table.family[code[:-2]] = entry
        else:
            table.exact[code] = entry
    return table


def split_label(label: str | None) -> list[str]:
    """«RS, ZigBee» / «RF и RS» → каналы."""

    if not label:
        return []
    return [p.strip() for p in re.split(r",| и ", label) if p.strip() and p.strip() != NO_INTERFACE]


def join_label(parts: list[str]) -> str:
    unique = list(dict.fromkeys(parts))
    rank = {name: i for i, name in enumerate(ORDER)}
    unique.sort(key=lambda p: (rank.get(p, len(ORDER)), p))
    return ", ".join(unique) if unique else NO_INTERFACE


def same_interface(a: str | None, b: str | None) -> bool:
    return sorted(set(split_label(a))) == sorted(set(split_label(b)))


@dataclass(frozen=True)
class Decision:
    label: str | None  # итог для отчёта; None — на согласовании (отчёт берёт модификацию)
    suggested: str | None
    review: str | None  # почему на согласовании
    note: str
    codes: tuple[str, ...] = ()
    from_description: bool = False


_GLUED_PROTOCOL = re.compile(r"^(RF\d{3,4}/\d+)(P\d)$")  # «RF2400/1P1» — пропущен дефис перед протоколом
_COMPACT_RF2400 = re.compile(r"^RF2400(\d)$")  # «RF24003» — пропущена косая: RF2400/3
_BARE_FREQUENCY = re.compile(r"^\d{3,4}(/\d+)?$")


def interface_tokens(raw: str) -> list[str]:
    """Токены обозначения; явные опечатки ввода поверителем исправляются: «RF-868/1» → RF868/1,
    «RF2400/1P1» → RF2400/1 + P1, «RF24003» → RF2400/3, «2×RS485» → RS485. «2400/1» без RF
    не угадывается — на согласование."""

    tokens: list[str] = []
    for token in _SPLIT.split(raw.upper().translate(_CYR_TO_LAT)):
        token = _COUNT.sub("", token)
        if not token:
            continue
        token = _COMPACT_RF2400.sub(r"RF2400/\1", token)
        glued = _GLUED_PROTOCOL.match(token)
        if glued:
            tokens.extend(glued.groups())
            continue
        if tokens and tokens[-1] == "RF" and _BARE_FREQUENCY.match(token):
            tokens[-1] = "RF" + token
            continue
        tokens.append(token)
    return tokens


def decode(raw: str | None, table: CodeTable) -> Decision:
    """Интерфейс одного написания по таблице кодов типа."""

    found: list[tuple[str, str, CodeEntry]] = []  # токен, код таблицы, строка
    unknown: list[str] = []
    for token in interface_tokens(raw or ""):
        match = table.find(token)
        if match:
            found.append((token, *match))
        elif _LOOKS_LIKE.match(token):
            unknown.append(token)
    codes = tuple(dict.fromkeys(t for t, _, _ in found)) + tuple(unknown)
    if not found and not unknown:
        return Decision(NO_INTERFACE, None, None, NOTE_NO_CODE)

    parts: list[str] = []
    explained: list[str] = []
    reviews: list[str] = []
    for token, code, entry in found:
        if entry.label:
            parts.extend(split_label(entry.label))
        explained.append(f"{token} — {entry.label or '?'}" + (f" ({entry.comment})" if entry.comment else ""))
        if entry.review:
            reviews.append(
                f"код {token}: канал зависит от номера модуля, точной расшифровки нет"
                + (f" — {entry.comment}" if entry.comment else "")
            )
    for token in unknown:
        reviews.append(f"код «{token}» не расшифрован в таблице кодов типа")
    matched = {code.upper() for _, code, _ in found}
    for combo in table.combos:
        if combo.codes == matched and not unknown:
            reviews.append(
                f"сочетание {' + '.join(sorted(c.replace('/N', '/n') for c in combo.codes))} ждёт подтверждения аналитика"
                + (f" — {combo.comment}" if combo.comment else "")
            )
            if combo.label:
                parts = split_label(combo.label)
    suggested = join_label(parts) if parts else None
    note = "по коду интерфейса в написании: " + "; ".join(explained) if explained else "в написании код интерфейса без расшифровки"
    from_description = bool(found) and all(entry.from_description for _, _, entry in found)
    if reviews:
        return Decision(None, suggested, "; ".join(reviews), note, codes, from_description)
    return Decision(suggested, suggested, None, note, codes, from_description)


# ── Черновик таблицы из описания типа ──────────────────────────────────────────────────────

_BLOCK_START = re.compile(r"(основной|дополнительн\w*)\s+интерфейс", re.IGNORECASE)
_BLOCK_END = re.compile(r"поддерживаемые протоколы|дополнительные функции|протокол|модификация\s+N", re.IGNORECASE)
_LINE = re.compile(r"^\s*(?:\d+\s+)?([A-Z0-9][A-Za-z0-9/]{1,12})\s+[–—-]\s+(.+)$")
_DESCRIPTION_LABELS = (
    (re.compile(r"RS-?485", re.I), "RS"),
    (re.compile(r"RS-?232", re.I), "RS-232"),
    (re.compile(r"\bCAN\b"), "CAN"),
    (re.compile(r"NB[- ]?IoT", re.I), "NB-IoT"),
    (re.compile(r"NB[- ]?Fi", re.I), "NB-Fi"),
    (re.compile(r"GSM|GPRS|2G|3G", re.I), "GSM"),
    (re.compile(r"LTE|4G", re.I), "LTE"),
    (re.compile(r"LoRa", re.I), "LoRaWAN"),
    (re.compile(r"ZigBee", re.I), "ZigBee"),
    (re.compile(r"Bluetooth", re.I), "Bluetooth"),
    (re.compile(r"Wi-?Fi", re.I), "Wi-Fi"),
    (re.compile(r"Ethernet", re.I), "Ethernet"),
    (re.compile(r"PLC", re.I), "PLC"),
    (re.compile(r"2400\s*МГц|2,4\s*ГГц", re.I), None),  # ZigBee или Bluetooth — знает только РЭ
    (re.compile(r"(433|868|радиоканал|радиоинтерфейс)", re.I), "RF"),
)


def codes_from_description(description: str | None) -> str:
    """Черновик таблицы кодов из блоков «Основной / Дополнительные интерфейсы» описания типа.
    Описание называет вид канала, а не модуль: «радиоинтерфейс 2400 МГц» — ZigBee или Bluetooth, «GSM/GPRS
    G/n» — у НАРТИС G/5 оказался NB-IoT. Такие строки помечены «?» (на согласование)."""

    lines: list[str] = []
    seen: set[str] = set()
    inside = False
    for line in (description or "").splitlines():
        if _BLOCK_START.search(line):
            inside = True
        elif inside and _BLOCK_END.search(line):
            inside = False
        if not inside:
            continue
        match = _LINE.match(line.split(":", 1)[-1] if _BLOCK_START.search(line) else line)
        if not match:
            continue
        code = _normalize_code(match.group(1)).replace("/N", "/n")
        what = re.split(r",\s*где\b", match.group(2))[0].strip()
        if code in seen:
            continue
        seen.add(code)
        label = next((lab for pattern, lab in _DESCRIPTION_LABELS if pattern.search(what)), "?")
        if label is None:
            lines.append(f"{code} = ?  # {FROM_DESCRIPTION}: {what}; ZigBee или Bluetooth — по РЭ")
        elif code.endswith("/n") and label == "GSM":  # G/5 у НАРТИС — NB-IoT: модем по номеру модуля
            lines.append(f"{code} = ? {label}  # {FROM_DESCRIPTION}: {what}; канал зависит от номера модуля")
        else:
            lines.append(f"{code} = {label}  # {FROM_DESCRIPTION}: {what}")
    return "\n".join(lines)


# ── Применение к написаниям ────────────────────────────────────────────────────────────────


def apply_interface_codes(conn: Connection, type_ids: list[int] | None = None) -> dict[str, int]:
    """Выставить интерфейс написаний по таблице кодов их типа. Ручное решение не трогается; у типа без
    таблицы значения правила снимаются (отчёт вернётся к модификации). Повторный запуск ничего не меняет."""

    where = "v.excluded_reason IS NULL AND v.interface_source IS DISTINCT FROM 'manual'"
    params: dict = {}
    if type_ids is not None:
        where += " AND v.type_id = ANY(:ids)"
        params["ids"] = list(type_ids)
    rows = conn.execute(
        text(
            "SELECT v.id, v.raw, v.type_id, t.interface_codes, m.interface AS mod_interface, v.interface, v.interface_source, "
            "v.interface_note, v.interface_review, v.interface_suggested FROM meter_market_variants v "
            "JOIN meter_market_types t ON t.id = v.type_id LEFT JOIN meter_market_modifications m ON m.id = v.modification_id "
            f"WHERE {where}"
        ),
        params,
    ).mappings().all()
    tables: dict[int, CodeTable] = {}
    now = datetime.now(timezone.utc)
    stats = {"set": 0, "review": 0, "cleared": 0}
    for r in rows:
        table = tables.setdefault(r["type_id"], parse_table(r["interface_codes"]))
        if not table:
            if r["interface_source"] == "rule" or r["interface_review"]:
                conn.execute(
                    text("UPDATE meter_market_variants SET interface = NULL, interface_source = NULL, interface_note = NULL, "
                         "interface_review = NULL, interface_suggested = NULL, interface_at = NULL WHERE id = :v"),
                    {"v": r["id"]},
                )
                stats["cleared"] += 1
            continue
        d = decode(r["raw"], table)
        label, review = d.label, d.review
        mod = r["mod_interface"]
        if (
            label is not None and label != NO_INTERFACE and d.from_description
            and mod and mod != NO_INTERFACE and not same_interface(label, mod)
        ):
            label, review = None, f"расшифровка по описанию типа («{d.suggested}») расходится с модификацией («{mod}»)"
        new = (label, "rule", d.note[:1000], review, d.suggested)
        old = (r["interface"], r["interface_source"], r["interface_note"], r["interface_review"], r["interface_suggested"])
        if new == old:
            continue
        conn.execute(
            text("UPDATE meter_market_variants SET interface = :i, interface_source = 'rule', interface_note = :n, "
                 "interface_review = :r, interface_suggested = :s, interface_at = :at WHERE id = :v"),
            {"i": label, "n": new[2], "r": review, "s": d.suggested, "at": now, "v": r["id"]},
        )
        stats["review" if review else "set"] += 1
    return stats


# ── Таблица каналов НАРТИС от аналитика (23.11.2026) ───────────────────────────────────────

NARTIS_INTERFACE_CODES = """\
RS485 = RS  # аналитик 23.11.2026
RS232 = RS-232
CAN = CAN
RF433/n = RF  # аналитик 23.11.2026; описание типа: радиоинтерфейс 433 МГц
RF868/n = RF  # аналитик 23.11.2026; описание типа: радиоинтерфейс 868 МГц
RF2400/1 = ZigBee  # аналитик 23.11.2026; описание типа: радиоинтерфейс 2400 МГц
RF2400/2 = Bluetooth  # аналитик 23.11.2026; описание типа: радиоинтерфейс 2400 МГц
RF2400 = ZigBee  # без номера модуля — ZigBee (решение 23.11.2026)
RF2400/n = ?  # другой номер модуля 2400 МГц: ZigBee или Bluetooth — по РЭ
G/5 = NB-IoT  # аналитик 23.11.2026; в описании типа G/n — GSM/GPRS
G/n = GSM  # остальные модули G/n — GSM, как в описании типа (решение 23.11.2026); G/5 — NB-IoT строкой выше
PL/n = PLC
E/n = Ethernet
RFWF = Wi-Fi
RFLT = LTE
RF433/n + RF2400/2 = ? RF, Bluetooth  # в таблице аналитика «RF433 и RF2400/2 → ZigBee, Bluetooth»; по остальным строкам RF433 — RF: уточнить
"""
NARTIS_TYPES = ("86199-22", "86200-22")  # НАРТИС-И100, НАРТИС-И300: одна структура обозначения
NARTIS_CODE_NOTES = (
    "Каналы НАРТИС (аналитик 23.11.2026): RS485 — RS; RF433/n и RF868/n — RF; RF2400/1 и RF2400 без номера — ZigBee; RF2400/2 — Bluetooth; "
    "G/5 — NB-IoT, остальные G/n — GSM. Нет кода интерфейса в написании — «н/д»."
)


NARTIS_GSM_OLD_LINE = 'G/n = ? GSM  # в описании типа GSM/GPRS, но канал зависит от модуля (G/5 — NB-IoT)\n'
NARTIS_GSM_LINE = 'G/n = GSM  # остальные модули G/n — GSM, как в описании типа (решение 23.11.2026); G/5 — NB-IoT строкой выше\n'


def apply_nartis_gsm(conn: Connection) -> int:
    """G/n у НАРТИС — GSM (решение 23.11.2026, раздел 122): в таблицах, заведённых миграцией 0114, строка
    «G/n = ? GSM» заменяется на «G/n = GSM», написания типов пересчитываются. Правки таблицы аналитиком
    (строка уже другая) не трогаются."""

    rows = conn.execute(
        text("SELECT id, interface_codes FROM meter_market_types WHERE mit_number = ANY(:m) AND interface_codes LIKE :l"),
        {"m": list(NARTIS_TYPES), "l": "%" + NARTIS_GSM_OLD_LINE.strip() + "%"},
    ).all()
    for type_id, codes in rows:
        conn.execute(
            text("UPDATE meter_market_types SET interface_codes = :c WHERE id = :t"),
            {"c": codes.replace(NARTIS_GSM_OLD_LINE.strip(), NARTIS_GSM_LINE.strip()), "t": type_id},
        )
    if rows:
        apply_interface_codes(conn, [r[0] for r in rows])
    return len(rows)


NARTIS_RF2400_LINE = 'RF2400 = ZigBee  # без номера модуля — ZigBee (решение 23.11.2026)\n'


def apply_nartis_rf2400(conn: Connection) -> int:
    """«RF2400» без номера модуля у НАРТИС — ZigBee (решение 23.11.2026, раздел 122): строка дописывается в таблицы,
    где кода «RF2400» ещё нет (аналитик мог решить иначе — тогда не трогаем); написания типов пересчитываются."""

    rows = conn.execute(
        text("SELECT id, interface_codes FROM meter_market_types WHERE mit_number = ANY(:m) AND interface_codes IS NOT NULL"),
        {"m": list(NARTIS_TYPES)},
    ).all()
    changed = [type_id for type_id, codes in rows if "RF2400" not in parse_table(codes).exact]
    for type_id, codes in rows:
        if type_id in changed:
            conn.execute(
                text("UPDATE meter_market_types SET interface_codes = :c WHERE id = :t"),
                {"c": codes.rstrip("\n") + "\n" + NARTIS_RF2400_LINE, "t": type_id},
            )
    if changed:
        apply_interface_codes(conn, changed)
    return len(changed)


def apply_nartis_codes(conn: Connection) -> int:
    """Таблица каналов аналитика — типам НАРТИС-И100/И300, если у них таблицы ещё нет; её слова — в «Расшифровку кодов»."""

    rows = conn.execute(
        text("SELECT id, interface_codes, code_notes FROM meter_market_types WHERE mit_number = ANY(:m)"),
        {"m": list(NARTIS_TYPES)},
    ).mappings().all()
    changed = []
    for r in rows:
        if r["interface_codes"]:
            continue
        notes = r["code_notes"] or ""
        if NARTIS_CODE_NOTES not in notes:
            notes = (notes + "\n" + NARTIS_CODE_NOTES).strip()
        conn.execute(
            text("UPDATE meter_market_types SET interface_codes = :c, code_notes = :n WHERE id = :t"),
            {"c": NARTIS_INTERFACE_CODES, "n": notes, "t": r["id"]},
        )
        changed.append(r["id"])
    if changed:
        apply_interface_codes(conn, changed)
    return len(changed)


# ── Таблица каналов МИРТЕК-12-РУ (24.11.2026) ─────────────────────────────────────────────

MIRTEK_INTERFACE_CODES = """\
RS485 = RS  # описание типа 61891-15
RS232 = RS-232
CAN = CAN
RF433 = RF
RF433/n = RF  # описание типа: радиоинтерфейс 433 МГц
RF868 = RF
RF868/n = RF  # описание типа: радиоинтерфейс 868 МГц
RF2400 = RF  # сайт МИРТЕК: радиоканал 2400 МГц, не ZigBee/Bluetooth (правка аналитика 24.11.2026)
RF2400/n = RF
G/1 = GSM
G1 = GSM  # опечатка без косой
G/2 = GSM
G2 = GSM
G/5 = NB-IoT  # комбинированный GSM/GPRS + NB-IoT; у W9 — NB-IoT (правка аналитика 24.11.2026)
G5 = NB-IoT
G/5.1 = NB-IoT
G/n = GSM  # остальные модули G/n — GSM/GPRS по описанию типа
MOD = mod
MOD/n = mod
PF/n = PLC
PO/n = PLC
PL/n = PLC
E/n = Ethernet
RFWF = Wi-Fi
RFWF/n = Wi-Fi
RFLT = LTE
RFLT/n = LTE
"""
MIRTEK_TYPES = ("61891-15",)  # МИРТЕК-12-РУ
MIRTEK_CODE_NOTES = (
    "Каналы МИРТЕК-12-РУ (аналитик 24.11.2026): RS485 — RS; RF433/RF868/RF2400 — RF; G/1 и G/2 — GSM; "
    "G/5 и G/5.1 — NB-IoT; MOD — mod. Нет кода интерфейса в написании — «н/д»."
)


def apply_mirtek_codes(conn: Connection) -> int:
    """Таблица каналов МИРТЕК-12-РУ, если у типа её ещё нет; написания пересчитываются."""

    rows = conn.execute(
        text("SELECT id, interface_codes, code_notes FROM meter_market_types WHERE mit_number = ANY(:m)"),
        {"m": list(MIRTEK_TYPES)},
    ).mappings().all()
    changed = []
    for r in rows:
        if r["interface_codes"]:
            continue
        notes = r["code_notes"] or ""
        if MIRTEK_CODE_NOTES not in notes:
            notes = (notes + "\n" + MIRTEK_CODE_NOTES).strip()
        conn.execute(
            text("UPDATE meter_market_types SET interface_codes = :c, code_notes = :n WHERE id = :t"),
            {"c": MIRTEK_INTERFACE_CODES, "n": notes, "t": r["id"]},
        )
        changed.append(r["id"])
    if changed:
        apply_interface_codes(conn, changed)
    return len(changed)


if __name__ == "__main__":
    import sys

    from app.db.session import engine

    with engine.begin() as connection:
        if "--nartis" in sys.argv:
            print({"nartis_types": apply_nartis_codes(connection), "nartis_gsm": apply_nartis_gsm(connection), "nartis_rf2400": apply_nartis_rf2400(connection)})
        if "--mirtek" in sys.argv:
            print({"mirtek_types": apply_mirtek_codes(connection)})
        print(apply_interface_codes(connection))
