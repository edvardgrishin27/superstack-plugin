#!/usr/bin/env python3
"""SUPERSTACK — отметка о сверке карты механизмов с планом.

Зачем это вообще нужно.

`data/plan-coverage.json` написала та же модель, которая по нему строила. Ворота
«план» проверяют соответствие КАРТЕ, а не соответствие карты ПЛАНУ — неполнота
карты невидима для них всех. Это уже случилось: «34 из 34» горело зелёным, пока
независимая сверка не нашла 14 групп пропусков, и карта выросла до 137.

Сверку провели один раз. Механизмом она не стала: ни один инструмент не смотрел
на файл плана, и следующая его правка разошлась бы с картой ровно так же молча.
Здесь — самая дешёвая часть, которую можно посчитать кодом: **отпечаток плана,
с которым карту сверяли**. Совпал — сверка относится к нынешнему плану; не
совпал — план правился, и полнота карты снова НЕИЗВЕСТНА.

Чем это НЕ является, и это важнее того, чем является. Отметка не доказывает,
что карта полна: её ставит тот, кто сверял, и врать ей можно. Она доказывает
ровно одно — что после сверки план не менялся. Полноту по-прежнему устанавливает
чтение плана другой моделью; механизм лишь не даёт расхождению пройти незаметно.

  python3 plan_stamp.py --by <кто сверял> [--note <чем закончилось>]
  python3 plan_stamp.py --show
  python3 plan_stamp.py --by <кто> --map <своя карта>   (для тестов)

  код 0 — отметка поставлена, 2 — плана нет, 3 — ошибка вызова
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAP = REPO / "data" / "plan-coverage.json"

#: Путь к плану переопределяется переменной среды — иначе ни один тест не
#: сможет проверить эту сверку, не читая настоящий план на машине автора.
PLAN_ENV = "SUPERSTACK_PLAN"
PLAN_DEFAULT = Path.home() / ".claude" / "plans" / "typed-waddling-hamster.md"


def plan_path() -> Path:
    v = os.environ.get(PLAN_ENV)
    return Path(v).expanduser() if v else PLAN_DEFAULT


def digest(p: Path) -> str:
    """Отпечаток плана. Любая правка — новый отпечаток, и это намеренно строго:
    решать, «существенная» ли правка, значит снова судить прозой."""
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    argv = sys.argv[1:]

    def opt(name: str) -> "str | None":
        return argv[argv.index(name) + 1] if name in argv and \
            argv.index(name) + 1 < len(argv) else None

    путь_карты = Path(opt("--map")) if opt("--map") else MAP
    try:
        карта = json.loads(путь_карты.read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(f"НЕ УДАЛОСЬ: карта не прочитана: {e}", file=sys.stderr)
        return 3

    if "--show" in argv:
        print(json.dumps(карта.get("reconciled") or {}, ensure_ascii=False, indent=1))
        return 0

    кто = opt("--by")
    if not кто:
        # Отметка без имени — это «кто-то когда-то сверял»: ровно то, что уже
        # один раз прошло за проверку и оказалось ничем.
        print("НЕ УДАЛОСЬ: нужно --by <кто сверял>. Отметка без имени "
              "сверяющего ничего не утверждает", file=sys.stderr)
        return 3

    p = plan_path()
    if not p.is_file():
        print(f"НЕ УДАЛОСЬ: плана нет по пути {p} — сверять не с чем",
              file=sys.stderr)
        return 2

    было = (карта.get("reconciled") or {}).get("digest")
    карта["reconciled"] = {
        "digest": digest(p),
        "date": dt.date.today().isoformat(),
        "by": кто,
        "mechanisms": len(карта.get("mechanisms") or []),
        # Путь пишется через `~`: абсолютный тащит в файл домашний
        # каталог человека, а файл уезжает в публичный репозиторий.
        "plan": str(p).replace(str(Path.home()), "~"),
        "note": opt("--note") or "",
    }
    путь_карты.write_text(json.dumps(карта, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"СВЕРКА ОТМЕЧЕНА: {кто}, механизмов {карта['reconciled']['mechanisms']}",
          file=sys.stderr)
    if было and было != карта["reconciled"]["digest"]:
        print("  план с прошлой сверки менялся — отпечаток обновлён",
              file=sys.stderr)
    print(json.dumps(карта["reconciled"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
