"""Квалификационные требования к участнику: вид, порог, период, подтверждение, срок и сверка
с «Моей компанией» — кодом (17.11.2026).

Перечень Bitrix24 «Что еще нужно от ИИ», пп. 9–13 (раздел 113 ARCHITECTURE.md). Требования к
участнику и так извлекаются моделью при разборе документации (`requirements.kind =
'participant'`) с цитатой, но в Bitrix24 уходили одной строкой «Нужно проверить подтверждающие
документы» — без порога («не менее 3 договоров»), периода («за последние 3 года»), того, чем
подтверждать, и без ответа «есть ли это у нас».

Почему кодом: порог, период и перечень подтверждающих документов в требовании записаны
стандартными оборотами, и их извлечение из уже найденной цитаты не требует второго запроса к
модели (и не выдумывает: нет оборота — нет поля). Сверка с компанией — с тем, что заполнено в
«Моей компании» (лицензии, опыт, выполненные договоры); чего там нет, то «нужно проверить», а не
«не соответствует». Общие требования закона (не банкрот, нет недоимки, не в РНП) — декларация
участника: их выполнение подтверждается самой заявкой.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_profile import CompanyProfile

# Вид требования (п. 9): лицензии, опыт, персонал, склады, оборудование, сертификаты; прочие
# ресурсы и документы (п. 10). Порядок важен: первое совпадение. Общие требования закона —
# первыми: в длинной формулировке о недоимке встречается и «бухгалтерская отчётность».
CATEGORIES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("STATUTORY", "Общие требования закона (декларация)",
     re.compile(r"ликвидац|банкрот|недоимк|задолженност\w+\s+по\s+(?:налог|иным)|реестр\w*\s+недобросовестн|"
                r"судимост|иностранн\w+\s+агент|офшор|конфликт\w*\s+интерес|административн\w+\s+ответственност|"
                r"правоспособност|дееспособност|приостановлени\w+\s+деятельност", re.I)),
    ("LICENSE", "Лицензия, допуск, СРО, аккредитация",
     re.compile(r"лиценз|\bСРО\b|саморегулир|аккредит|допуск\w*\s+к\s+работ|свидетельств\w+\s+о\s+допуск", re.I)),
    ("EXPERIENCE", "Опыт исполнения договоров",
     re.compile(r"опыт|исполнен\w+\s+(?:договор|контракт)|аналогичн\w+\s+(?:договор|контракт|поставк|работ)", re.I)),
    ("STAFF", "Персонал, квалификация работников",
     re.compile(r"персонал|сотрудник|работник|специалист|групп\w+\s+(?:допуска\s+)?по\s+электробезопасн|удостоверени|аттестац", re.I)),
    ("WAREHOUSE", "Склад, производственная база",
     re.compile(r"склад|производственн\w+\s+(?:баз|площад|мощност)|помещени", re.I)),
    ("EQUIPMENT", "Оборудование, материально-технические ресурсы",
     re.compile(r"оборудовани|материально-техническ|техническ\w+\s+средств|транспорт|техник", re.I)),
    ("CERTIFICATE", "Сертификаты, системы менеджмента",
     re.compile(r"сертификат|ISO|ГОСТ\s+Р?\s*ИСО|систем\w+\s+менеджмент", re.I)),
    ("FINANCE", "Финансовое положение, отчётность",
     re.compile(r"отч[её]тност|бухгалтер|выручк|финансов|аудитор|баланс", re.I)),
)

_THRESHOLD_RE = re.compile(
    r"(?:не\s+менее|не\s+меньше|не\s+ниже|не\s+более|минимум|от|свыше|более)\s+"
    r"(\d[\d\s]*(?:[.,]\d+)?)\s*(%|процент\w*|руб\w*|млн\.?\s*руб\w*|тыс\.?\s*руб\w*|"
    r"договор\w*|контракт\w*|челов\w*|специалист\w*|ед\w*|шт\w*|лет|год\w*|м2|кв\.?\s*м\w*)?"
    r"(?:\s+(?:от\s+)?(?:НМЦ\w*|начальн\w+\s+\(?максимальн\w+\)?\s+цен\w+))?",
    re.I,
)
_NMC_THRESHOLD_RE = re.compile(
    r"не\s+менее\s+(?:чем\s+)?(?:\d+\s*%\s+(?:от\s+)?)?(?:начальн\w+\s+\(?максимальн\w+\)?\s+цен\w+|НМЦ\w*)",
    re.I,
)
_PERIOD_RE = re.compile(
    r"(?:за|в\s+течение)\s+(?:последни[ехй]\s+)?(\d+|одного|двух|трех|трёх|пяти)?\s*"
    r"(?:\(\w+\)\s*)?(лет|года?|месяц\w*|отч[её]тн\w+\s+(?:год|период))"
    r"(?:\s*,?\s*предшествующ\w+\s+[^.;]{0,60})?",
    re.I,
)
_CONFIRMATION_RE = re.compile(
    r"(?:подтвержда\w+|подтвержд[её]н\w*|приложи\w+|прилагаю\w+|"
    r"(?:предоставл|представл)\w+\s+(?=копи|документ|справк|выписк|сведени|оригинал|скан))"
    r"\s*([^.;]{5,240})",
    re.I,
)
_BEFORE_SUBMIT_RE = re.compile(
    r"(?:до|не\s+позднее|на\s+дату|на\s+момент)\s+(?:дня\s+|даты\s+|момента\s+|окончания\s+срока\s+)?"
    r"(?:подачи|окончания\s+(?:срока\s+)?подачи|вскрытия|рассмотрения)\s+заяв\w+[^.;]{0,120}",
    re.I,
)
_SPECIAL_RE = re.compile(r"аккредитац\w+\s+на\s+площадк|регистрац\w+\s+в\s+(?:ЕИС|реестр)|осмотр|выезд|согласован\w+\s+с\s+заказчик|предквалификац|внесен\w+\s+в\s+реестр", re.I)

STATUS_MEETS = "meets"
STATUS_MISSING = "missing"
STATUS_CHECK = "check"
STATUS_DECLARATION = "declaration"
_STATUS_LABELS = {
    STATUS_MEETS: "Соответствует по данным «Моей компании»",
    STATUS_MISSING: "Не хватает: в «Моей компании» нет подтверждения",
    STATUS_CHECK: "Нужно проверить подтверждающие документы",
    STATUS_DECLARATION: "Декларируется в заявке",
}


@dataclass
class Qualification:
    category: str | None
    category_label: str | None
    threshold: str | None
    period: str | None
    confirmation: str | None
    deadline: str | None
    special: bool
    status: str
    status_label: str
    basis: str | None
    missing: list[str] = field(default_factory=list)

    def data(self) -> dict[str, Any]:
        return {
            "qualification_kind": self.category_label,
            "threshold": self.threshold,
            "period": self.period,
            "confirmation": self.confirmation,
            "deadline": self.deadline,
            "special_condition": "Да" if self.special else None,
            "result": self.status_label,
            "company_basis": self.basis,
            "missing": "; ".join(self.missing) or None,
        }


def _clip(text: str | None, limit: int = 300) -> str | None:
    text = " ".join((text or "").split()).strip(" ,;:")
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def classify(text: str) -> tuple[str | None, str | None]:
    for code, label, pattern in CATEGORIES:
        if pattern.search(text or ""):
            return code, label
    return None, None


def parse(text: str) -> dict[str, Any]:
    """Порог, период, подтверждение и срок — дословными оборотами из требования."""

    text = text or ""
    threshold = _THRESHOLD_RE.search(text)
    if not (threshold and threshold.group(2)):
        threshold = _NMC_THRESHOLD_RE.search(text) or threshold
    period = _PERIOD_RE.search(text)
    confirmation = _CONFIRMATION_RE.search(text)
    deadline = _BEFORE_SUBMIT_RE.search(text)
    return {
        "threshold": _clip(threshold.group(0)) if threshold and (
            threshold.re is _NMC_THRESHOLD_RE or threshold.group(2) or "нмц" in threshold.group(0).lower()
        ) else None,
        "period": _clip(period.group(0)) if period and (period.group(1) or "отч" in period.group(0).lower()) else None,
        "confirmation": _clip(confirmation.group(0)) if confirmation else None,
        "deadline": _clip(deadline.group(0)) if deadline else None,
        "special": bool(_SPECIAL_RE.search(text)),
    }


def _number(text: str | None) -> int | None:
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def company_facts(db: Session) -> dict[str, Any]:
    """Что о нас известно из «Моей компании»: по всем компаниям группы."""

    licenses: list[str] = []
    projects = 0
    years = 0
    staff: list[dict] = []
    facilities: list[dict] = []
    equipment: list[dict] = []
    headcount = 0
    revenue = None
    for profile in db.scalars(select(CompanyProfile)):
        for item in profile.licenses or []:
            if isinstance(item, dict) and item.get("name"):
                licenses.append(str(item["name"]))
        projects += len(profile.past_projects or [])
        years = max(years, profile.years_of_experience or 0)
        # Ресурсы (раздел 115) — заполняет тендерный отдел; численность и выручка — из Rusprofile.
        resources = profile.resources or {}
        staff += [x for x in resources.get("staff") or [] if isinstance(x, dict)]
        facilities += [x for x in resources.get("facilities") or [] if isinstance(x, dict)]
        equipment += [x for x in resources.get("equipment") or [] if isinstance(x, dict)]
        dossier = profile.rusprofile_data or {}
        if isinstance(dossier.get("headcount"), (int, str)) and str(dossier["headcount"]).isdigit():
            headcount = max(headcount, int(dossier["headcount"]))
        finance = dossier.get("finance") or {}
        try:
            amount = float(finance.get("revenue_amount")) if finance.get("revenue_amount") else None
        except (TypeError, ValueError):
            amount = None
        if amount is not None and (revenue is None or amount > revenue[0]):
            revenue = (amount, finance.get("revenue"), finance.get("year"))
    return {
        "licenses": licenses, "projects": projects, "years": years, "staff": staff, "facilities": facilities,
        "equipment": equipment, "headcount": headcount, "revenue": revenue,
    }


_RUB_RE = re.compile(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(млрд|млн|тыс)?\.?\s*руб", re.I)


def _rubles(text: str | None) -> float | None:
    match = _RUB_RE.search(text or "")
    if not match:
        return None
    value = float(match.group(1).replace(" ", "").replace(",", "."))
    return value * {"млрд": 1e9, "млн": 1e6, "тыс": 1e3}.get((match.group(2) or "").lower(), 1)


def _stems(text: str | None) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{5,}", (text or "").lower().replace("ё", "е"))}


_GENERIC_STEMS = {"налич", "обору", "техни", "средс", "необх", "испол", "догов", "участ", "закуп", "матер", "ресур"}


def _license_match(text: str, licenses: list[str]) -> str | None:
    lowered = text.lower()
    for name in licenses:
        words = [w for w in re.findall(r"[а-яёa-z0-9]{3,}", name.lower()) if w not in {"лицензия", "работ", "услуг"}]
        if words and any(word in lowered for word in words[:6]):
            return name
    return None


def assess(text: str, facts: dict[str, Any]) -> Qualification:
    """Разбор требования и сверка с компанией (пп. 11–12)."""

    category, label = classify(text)
    parsed = parse(text)
    status, basis, missing = STATUS_CHECK, None, []
    if category == "STATUTORY":
        status = STATUS_DECLARATION
        basis = "Общее требование закона: подтверждается декларацией в составе заявки"
    elif category == "LICENSE":
        found = _license_match(text, facts["licenses"])
        if found:
            status, basis = STATUS_MEETS, f"Лицензия в «Моей компании»: {found[:200]}"
        elif "если" in text.lower() and "лицензируем" in text.lower():
            status, basis = STATUS_CHECK, "Требование условное (если деятельность лицензируемая)"
        else:
            status = STATUS_MISSING if facts["licenses"] else STATUS_CHECK
            missing.append("Подходящая лицензия/допуск не найдены в «Моей компании»")
    elif category == "EXPERIENCE":
        need_years = _number(parsed["threshold"]) if parsed["threshold"] and re.search(r"лет|год", parsed["threshold"]) else None
        need_contracts = _number(parsed["threshold"]) if parsed["threshold"] and re.search(r"договор|контракт", parsed["threshold"]) else None
        if need_years is not None and facts["years"]:
            ok = facts["years"] >= need_years
            status = STATUS_MEETS if ok else STATUS_MISSING
            basis = f"Опыт работы — {facts['years']} лет (требуется не менее {need_years})"
            if not ok:
                missing.append(f"Опыт меньше {need_years} лет")
        elif need_contracts is not None and facts["projects"]:
            ok = facts["projects"] >= need_contracts
            status = STATUS_MEETS if ok else STATUS_CHECK
            basis = f"В «Моей компании» выполненных договоров: {facts['projects']} (требуется не менее {need_contracts}); предмет и сумму сверить"
        elif facts["projects"]:
            status = STATUS_CHECK
            basis = f"В «Моей компании» выполненных договоров: {facts['projects']} — сверить предмет, сумму и период"
        else:
            missing.append("Выполненные договоры не заполнены в «Моей компании»")
    elif category == "STAFF":
        need = _number(parsed["threshold"]) if parsed["threshold"] and re.search(r"челов|специалист|сотрудник|работник", parsed["threshold"]) else None
        words = _stems(text) - _GENERIC_STEMS
        matched = [x for x in facts.get("staff", []) if _stems(f"{x.get('role')} {x.get('qualification')}") & words]
        group_needed = bool(re.search(r"групп\w*\s+(?:допуска\s+)?по\s+электробезопасн|аттестац|удостоверени", text, re.I))
        count = sum(int(x.get("count") or 0) for x in matched)
        if matched:
            qualified = not group_needed or any(re.search(r"групп|аттест|удостовер|допуск", x.get("qualification") or "", re.I) for x in matched)
            if (need is None or count >= need) and qualified:
                status = STATUS_MEETS
            else:
                status = STATUS_MISSING
                if need is not None and count < need:
                    missing.append(f"Подходящего персонала {count}, требуется не менее {need}")
                if not qualified:
                    missing.append("Квалификация (группа допуска, аттестация) в «Моей компании» не указана")
            basis = "Персонал в «Моей компании»: " + "; ".join(f"{x.get('role')} — {x.get('count') or '?'}" for x in matched[:5])
        elif need is not None and facts.get("headcount") and not group_needed:
            status = STATUS_MEETS if facts["headcount"] >= need else STATUS_MISSING
            basis = f"Среднесписочная численность (Rusprofile) — {facts['headcount']} (требуется не менее {need})"
        else:
            basis = "Персонал этой квалификации в «Моей компании» не указан"
            missing.append("Указать персонал в разделе «Ресурсы» «Моей компании»")
    elif category == "WAREHOUSE":
        found = [x for x in facts.get("facilities", []) if _stems(x.get("kind")) & _stems(text)]
        if found:
            status, basis = STATUS_MEETS, "Площади в «Моей компании»: " + "; ".join(
                f"{x.get('kind')} {x.get('address') or ''} {x.get('area') or ''}".strip() for x in found[:3])
        else:
            basis = "Склады и площадки в «Моей компании» не указаны"
            missing.append("Указать склад или площадку в разделе «Ресурсы»")
    elif category == "EQUIPMENT":
        words = _stems(text) - _GENERIC_STEMS
        found = [x for x in facts.get("equipment", []) if _stems(f"{x.get('name')} {x.get('note')}") & words]
        if found:
            status, basis = STATUS_MEETS, "Оборудование в «Моей компании»: " + "; ".join(
                f"{x.get('name')} — {x.get('count') or '?'}" for x in found[:5])
        else:
            basis = "Подходящее оборудование в «Моей компании» не указано"
            missing.append("Указать оборудование в разделе «Ресурсы»")
    elif category == "FINANCE":
        need = _rubles(parsed["threshold"])
        revenue = facts.get("revenue")
        if revenue and need is not None:
            status = STATUS_MEETS if revenue[0] >= need else STATUS_MISSING
            basis = f"Выручка {revenue[1]} за {revenue[2]} год (Rusprofile), требуется: {parsed['threshold']}"
        elif revenue:
            basis = f"Выручка {revenue[1]} за {revenue[2]} год (Rusprofile); порог в требовании — относительно цены или не указан"
        else:
            basis = "Финансовые показатели не загружены (Rusprofile)"
    elif category is not None:
        basis = "Сведений этого вида в «Моей компании» нет — подтверждение готовит тендерный отдел"
    return Qualification(
        category=category,
        category_label=label,
        threshold=parsed["threshold"],
        period=parsed["period"],
        confirmation=parsed["confirmation"],
        deadline=parsed["deadline"],
        special=parsed["special"],
        status=status,
        status_label=_STATUS_LABELS[status],
        basis=basis,
        missing=missing,
    )
