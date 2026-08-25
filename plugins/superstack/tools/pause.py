#!/usr/bin/env python3
"""SUPERSTACK — тормоз. Работает, даже когда агент не отвечает.

Почему на Python, а не на оболочке.

Аварийная остановка обязана работать ВЕЗДЕ. Скрипт на `sh` не запускается там,
где оболочки нет, — и человек, которому надо остановить работу прямо сейчас,
получает «команда не найдена». Это худшая из возможных поломок: она случается
ровно в тот момент, когда всё уже идёт не так.

Питон здесь есть по определению: им написаны все остальные инструменты, и без
него не работает ничего. Поэтому тормоз переехал сюда, а `pause.sh` остался
тонкой обёрткой — у кого он в пальцах, у того и продолжит работать.

Два правила, оба выстраданы:

  1. «ПАУЗА» ПЕЧАТАЕТСЯ ТОЛЬКО ПОСЛЕ ТОГО, как флаг реально оказался на диске.
     Раньше сообщение шло безусловно: человек в аварийной ситуации читал
     «остановлено», а система продолжала работать.
  2. «СНЯТО» И «НЕЧЕГО БЫЛО СНИМАТЬ» — РАЗНЫЕ СОБЫТИЯ. Одинаковый ответ на оба
     означает, что подтверждение снятия ничего не подтверждает.

  python3 pause.py on       поставить
  python3 pause.py off      снять
  python3 pause.py          спросить

  код 0 — сделано, 1 — не удалось, 10 — на паузе (для `status`)

  Человеческие строки идут в stdout — так печатал прежний скрипт, и порт не
  имеет права менять наблюдаемое. Ошибки остаются в stderr.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path


def флаг() -> Path:
    return Path.home() / ".claude" / "superstack" / "PAUSE"


def поставить() -> int:
    f = флаг()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        отметка = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write_text(отметка + "\n", encoding="utf-8")
    except OSError as e:
        print(f"НЕ УДАЛОСЬ записать флаг {f}: {e}", file=sys.stderr)
        return 1
    # Проверка ПОСЛЕ записи, а не вместо неё: успех системного вызова и файл
    # на диске — разные утверждения, и человеку нужно второе.
    if not (f.is_file() and f.stat().st_size):
        print(f"НЕ УДАЛОСЬ: флаг пуст после записи: {f}", file=sys.stderr)
        return 1
    print(f"ПАУЗА подтверждена. Флаг: {f}")
    print(f'Снять: python3 "{Path(__file__).resolve()}" off')
    return 0


def снять() -> int:
    f = флаг()
    был = f.is_file()
    try:
        f.unlink(missing_ok=True)
    except OSError as e:
        print(f"НЕ УДАЛОСЬ снять паузу: {e}", file=sys.stderr)
        return 1
    if f.exists():
        print(f"НЕ УДАЛОСЬ снять паузу: {f} на месте", file=sys.stderr)
        return 1
    print("пауза снята" if был else "паузы не было")
    return 0


def спросить() -> int:
    f = флаг()
    if f.is_file():
        try:
            с = f.read_text("utf-8").strip()
        except OSError:
            с = "?"
        print(f"НА ПАУЗЕ с {с}")
        return 10
    print("работает")
    return 0


def main() -> int:
    for поток in (sys.stdout, sys.stderr):
        if (getattr(поток, "encoding", "") or "").lower().replace("-", "") != "utf8" \
                and hasattr(поток, "reconfigure"):
            поток.reconfigure(encoding="utf-8", errors="replace")
    команда = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    return {"on": поставить, "off": снять}.get(команда, спросить)()


if __name__ == "__main__":
    sys.exit(main())
