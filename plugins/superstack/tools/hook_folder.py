"""SUPERSTACK — папка проекта из входа хука (общая часть для хуков).

Зачем это существует. Чат десктопа часто открыт в домашней папке, а проект
заведён в её подпапке. Корень сеанса (`CLAUDE_PROJECT_DIR`) тогда шире
отметки, и хук молчал весь сеанс. Вход хука несёт поле `cwd`: папку, в
которой Claude работает сейчас (после `cd` — уже новую).

Правило узкое. Если корень сеанса не отмечен, а `cwd` лежит внутри отмеченной
папки — работаем на `cwd`. Искать ВСЕ отмеченные папки под корнем нельзя:
чат в домашней папке по другому делу начал бы блокироваться красными тестами
соседнего проекта.

Модуль ничего не печатает и не бросает исключений: при любом сбое отвечает
«нет», и хук молчит, как раньше.
"""
from __future__ import annotations

import json
from pathlib import Path


def папка(вход: str, позвали_здесь) -> "Path | None":
    """Папка `cwd` из входа, если она существует и отмечена; иначе None."""
    try:
        данные = json.loads(вход)
        если = данные.get("cwd") if isinstance(данные, dict) else None
        if not isinstance(если, str) or not если.strip():
            return None
        p = Path(если)
        if not p.is_absolute() or not p.is_dir():
            return None
        p = p.resolve()
        return p if позвали_здесь(p) else None
    except (ValueError, OSError, RuntimeError):
        return None
