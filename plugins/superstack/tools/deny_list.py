#!/usr/bin/env python3
"""SUPERSTACK — деструктивные команды: список в одном месте и слой, который его исполняет.

Зачем это отдельный инструмент.

Список деструктивных команд — единственная межрепозиторная константа корпуса:
пять источников в четырёх разных слоях исполнения, содержимое почти дословно
совпадает. То есть спорить о содержимом нечего, а вот слой исполнения выбирать
надо, и от него зависит всё.

Слой выбран нативный — `permissions.deny` в настройках Claude Code. Причина:
запрет, который держит сам харнесс, срабатывает раньше любого нашего кода и не
зависит от того, запущен ли плагин, жив ли хук и не упал ли Python. Свой
PreToolUse-сторож был бы четвёртой копией одного и того же списка и добавил бы
ровно один новый отказ — собственный.

Что здесь считается кодом, а не мнением:

  1. НЕДОСТАЮЩИЕ ЗАПРЕТЫ НАЗЫВАЮТСЯ ПОИМЁННО. «Часть правил стоит» и «правила
     стоят» — разные утверждения; второе неверно, пока список не полон.
  2. ЗАПИСЬ В ЧУЖИЕ НАСТРОЙКИ — ТОЛЬКО ПО ЯВНОМУ ФЛАГУ. Инструмент, правящий
     машину человека молча, — это тот же захват, от которого он защищает.
  3. НЕЧИТАЕМЫЕ НАСТРОЙКИ — НЕ «ВСЁ ХОРОШО». Битый или отсутствующий файл даёт
     код 2, а не пустой список недостающего.

  python3 deny_list.py --check [файл настроек]     что не запрещено
  python3 deny_list.py --apply [файл настроек]     дописать запреты
  python3 deny_list.py --list                      сам перечень

  код 0 — все запреты на месте (или запись прошла), 1 — есть недостающие,
  2 — прочитать не удалось, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
СПИСОК = HERE.parent / "data" / "destructive-commands.json"
НАСТРОЙКИ = Path.home() / ".claude" / "settings.json"


def перечень(path: Path = СПИСОК) -> list:
    """Команды из единственного места. Список пуст — это отказ, а не пустота."""
    d = json.loads(path.read_text("utf-8"))
    return d.get("commands") or []


def missing(настройки: dict, команды: list) -> list:
    """Каких запретов НЕТ в настройках. Поимённо, а не числом.

    Сравнение точное, по строке правила: «похоже на запрет rm» — это суждение,
    а суждение здесь и запрещено. Правило записано иначе — считаем, что его
    нет, и человек увидит обе строки рядом.
    """
    deny = ((настройки.get("permissions") or {}).get("deny") or [])
    есть = set(deny) if isinstance(deny, list) else set()
    return [c for c in команды if c["pattern"] not in есть]


def _настройки(p: Path) -> tuple:
    """(данные, причина отказа). Отсутствие файла — не пустой словарь."""
    if not p.is_file():
        return None, f"нет файла настроек: {p}"
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"настройки не разобраны ({e}) — дописывать вслепую нельзя"
    return (d if isinstance(d, dict) else None), (
        "" if isinstance(d, dict) else "настройки — не объект")


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    режимы = {"--check", "--apply", "--list"}
    выбран = [a for a in argv if a in режимы]
    if len(выбран) != 1:
        print("вызов: deny_list.py --check|--apply|--list [файл настроек]",
              file=sys.stderr)
        return 3

    try:
        команды = перечень()
    except (OSError, ValueError) as e:
        print(f"НЕ УДАЛОСЬ: перечень не прочитан: {e}", file=sys.stderr)
        return 2
    if not команды:
        print("НЕ УДАЛОСЬ: перечень пуст — запрещать нечего", file=sys.stderr)
        return 2

    if выбран[0] == "--list":
        print(json.dumps(команды, ensure_ascii=False, indent=1))
        return 0

    пути = [a for a in argv if not a.startswith("--")]
    p = Path(пути[0]).expanduser() if пути else НАСТРОЙКИ
    данные, отказ = _настройки(p)
    if данные is None:
        print(f"НЕ УДАЛОСЬ: {отказ}", file=sys.stderr)
        return 2

    нет = missing(данные, команды)

    if выбран[0] == "--check":
        if "--json" not in argv:
            if нет:
                print(f"ЗАПРЕТОВ НЕ ХВАТАЕТ: {len(нет)} из {len(команды)}",
                      file=sys.stderr)
                for c in нет[:20]:
                    print(f"  ! {c['pattern']} — {c['why']}", file=sys.stderr)
            else:
                print(f"ВСЕ ЗАПРЕТЫ НА МЕСТЕ: {len(команды)}", file=sys.stderr)
        print(json.dumps({"missing": [c["pattern"] for c in нет],
                          "total": len(команды), "file": str(p)},
                         ensure_ascii=False, indent=1))
        return 1 if нет else 0

    # --apply: правка чужой машины
    разрешено = "--yes" in argv
    if not разрешено:
        # Инструмент, который правит настройки человека молча, — это тот же
        # захват, от которого он защищает. Флаг обязателен, и он явный.
        print("НЕ УДАЛОСЬ: правка настроек требует явного --yes.\n"
              f"  будет добавлено запретов: {len(нет)} в {p}",
              file=sys.stderr)
        return 3
    if not нет:
        print("добавлять нечего: все запреты уже стоят", file=sys.stderr)
        return 0

    perms = данные.setdefault("permissions", {})
    deny = perms.setdefault("deny", [])
    if not isinstance(deny, list):
        print("НЕ УДАЛОСЬ: permissions.deny — не список", file=sys.stderr)
        return 2
    deny.extend(c["pattern"] for c in нет)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(данные, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)
    print(f"ДОБАВЛЕНО ЗАПРЕТОВ: {len(нет)} в {p}", file=sys.stderr)
    print(json.dumps({"added": [c["pattern"] for c in нет], "file": str(p)},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
