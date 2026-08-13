#!/usr/bin/env python3
"""Где что лежит после разделения на семь плагинов.

Зачем отдельный модуль. Тесты — единица РАЗРАБОТКИ, плагин — единица УСТАНОВКИ:
пользователь тесты не ставит, поэтому набор остаётся одним и живёт в корне
репозитория. Но инструменты разъехались по семи каталогам, и прежнее
`ROOT / "tools" / "verify.py"` перестало существовать.

Здесь путь ищется ПО ИМЕНИ среди всех плагинов. Тест не обязан помнить, в каком
пакете лежит инструмент, — иначе каждое будущее перемещение файла означало бы
правку двадцати тестовых файлов, и в какой-то момент это перестанут делать
аккуратно.

Неоднозначность — ошибка, а не выбор наугад: `hooks/hooks.json` есть у трёх
плагинов, и молчаливый выбор первого дал бы тест, проверяющий не тот файл.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS = REPO / "plugins"


def plug(name: str) -> Path:
    """Каталог конкретного плагина. Для случаев, где имя файла неоднозначно."""
    d = PLUGINS / name
    if not d.is_dir():
        raise FileNotFoundError(f"нет плагина {name} в {PLUGINS}")
    return d


def at(*parts: str) -> Path:
    """Путь внутри любого плагина, найденный по имени.

    Порядок поиска: сначала плагины, потом корень репозитория (там лежат сами
    тесты, набор мутаций и карта плана — они не принадлежат ни одному пакету).
    """
    rel = Path(*parts)
    hits = sorted(p / rel for p in PLUGINS.glob("*") if (p / rel).exists())
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        where = ", ".join(h.parent.parent.name for h in hits)
        raise AssertionError(
            f"«{rel}» есть сразу в нескольких плагинах ({where}) — "
            f"назови нужный явно через plug('superstack-…')")
    if (REPO / rel).exists():
        return REPO / rel
    raise FileNotFoundError(f"«{rel}» нет ни в одном плагине и ни в корне")


def every(*parts: str) -> list:
    """Все совпадения по имени — для проверок вида «у каждого плагина есть X»."""
    rel = Path(*parts)
    return sorted(p / rel for p in PLUGINS.glob("*") if (p / rel).exists())
