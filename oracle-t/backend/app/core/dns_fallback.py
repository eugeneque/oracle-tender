"""Резервные адреса для хостов с ненадёжным DNS (жалоба 28.09.2026: «опрос не отдаёт
информацию по приборам»).

Зона `gost.ru` отвечает SERVFAIL не только локальному резолверу, но и публичным (8.8.8.8,
1.1.1.1), хотя сам ФГИС по адресу отвечает за полсекунды. Без ФГИС каталог пуст: нет ни
кодов СИ, ни исполнений из реестра, а автозаполнение шло «успешно» с нулём найденного.

Подмена работает только когда обычное разрешение имени **упало**: рабочий DNS всегда в
приоритете, и смена адресов у ФГИС не сломает систему, пока DNS жив. Меняется лишь адрес
соединения — имя хоста в TLS (SNI) и проверка сертификата остаются прежними, поэтому
подменить сервер таким путём нельзя. Адреса задаются в `.env` (`DNS_FALLBACK_HOSTS`).
"""

from __future__ import annotations

import socket

from loguru import logger

_original_getaddrinfo = socket.getaddrinfo
_fallback: dict[str, list[str]] = {}


def parse(value: str) -> dict[str, list[str]]:
    """`host=ip1,ip2;host2=ip3` → {host: [ip1, ip2], host2: [ip3]}."""

    result: dict[str, list[str]] = {}
    for part in value.split(";"):
        host, _, addresses = part.partition("=")
        ips = [ip.strip() for ip in addresses.split(",") if ip.strip()]
        if host.strip() and ips:
            result[host.strip().lower()] = ips
    return result


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002 - сигнатура socket
    try:
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        ips = _fallback.get(str(host).lower()) if isinstance(host, str) else None
        if not ips:
            raise
        logger.warning(f"DNS не разрешил {host} — соединение по резервному адресу {ips[0]}")
        results = []
        for ip in ips:
            results.extend(_original_getaddrinfo(ip, port, family, type, proto, flags))
        return results


def install(value: str) -> None:
    _fallback.clear()
    _fallback.update(parse(value))
    if _fallback:
        socket.getaddrinfo = _getaddrinfo
