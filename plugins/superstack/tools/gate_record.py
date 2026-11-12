#!/usr/bin/env python3
"""SUPERSTACK — свежа ли запись гейта: единственный источник слова «доказано».

Зачем это отдельный модуль. Раньше «доказано» ставилось по числу из вызова:
`--exit-code 0` печатал тот, кто вёл работу, а не запущенная проверка. Слово
«проверено машиной» в отчёте стояло на словах ведущего. Теперь «доказано»
выводится из записи, которую гейт (tools/verify.py) оставляет сам после
прогона. Читают её двое — запись хода работ (tools/progress.py) и мост фазы
проверки (tools/state_fields.py), — и оба через эту функцию: два своих
определения «свежей зелёной» разошлись бы молча.

Свежая — значит три вещи сразу:
  · запись лежит в папке `.superstack` рядом с файлом хода работ;
  · в ней код 0;
  · её отпечаток равен отпечатку проекта прямо сейчас — тем же кодом, каким
    гейт считает, можно ли не перепроверять зелёное. Правка после прогона
    делает запись устаревшей, и «доказано» она уже не даёт.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Что слышит человек, когда «доказано» поставить не на чем.
СО_СЛОВ = "записал как «со слов»: свежей проверки нет"

#: Имя записи — то же, что пишет гейт (verify.RECORD).
ИМЯ = "verify-last.json"


def свежая_зелёная(state_path: Path) -> "dict | None":
    """Запись гейта рядом с файлом хода работ — если она зелёная и свежая.

    None — «доказано» ставить не на чем: записи нет, она не разбирается, в ней
    не ноль, отпечатка нет или он не совпал. Любая неполнота знания читается
    как «нет», а не как «наверное, свежая».
    """
    папка = Path(state_path).expanduser().resolve().parent
    if папка.name != ".superstack":
        return None
    try:
        запись = json.loads((папка / ИМЯ).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(запись, dict):
        return None
    код = запись.get("exit_code")
    след = запись.get("fingerprint")
    if type(код) is not int or код != 0 or not isinstance(след, str) or not след:
        return None
    import verify
    try:
        сейчас = verify._reuse_fingerprint(папка.parent)
    except Exception:  # noqa: BLE001 — сбой отпечатка не даёт «доказано»
        return None
    return запись if сейчас == след else None
