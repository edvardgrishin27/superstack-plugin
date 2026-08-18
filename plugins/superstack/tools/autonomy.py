#!/usr/bin/env python3
"""SUPERSTACK — сколько система делает без человека, и чем это заслужено.

Зачем ручка вообще. Сегодня объём самостоятельности задаётся молча: где-то
спросили, где-то нет, и человек узнаёт границу по факту — когда что-то
случилось без него. Явная ступень превращает это в решение, которое принято
один раз и записано.

Зачем ступень нельзя поднять словами. Всё, что здесь автоматизируется, стоит на
доказательствах: пропускать подтверждение плана осмысленно там, где приёмка
поймает расхождение, а закрывать ход без человека — там, где тесты доказали, что
умеют падать. Ступень, выставленная желанием, снимает подтверждения, не добавив
проверок, и первым же прогоном превращается в «оно само что-то сделало».

Поэтому подъём — это ПРОВЕРКА, а не запись. Каждая ступень называет своё условие
и путь, по которому оно измерено:

  0  ничего сама          человек подтверждает каждый шаг
  1  план по шаблону      есть тесты и команда, которой их гоняют
  2  код без подтверждения каждого таска
                          гейт верификации отвечает кодом 0/1, а не «проверять
                          нечем»: иначе «сделано» опирается на пустоту
  3  закрытие без подтверждения плана
                          зарегистрированы поломки (тесты умеют падать) И слепая
                          приёмка хоть раз выносила вердикт

Выше третьей ступени здесь нет намеренно. Четвёртая означала бы, что система
сама решает, ЧТО строить, а это другое решение, и оно не следует из желания не
подтверждать план.

  python3 autonomy.py <корень> --show
  python3 autonomy.py <корень> --set 2
  python3 autonomy.py <корень> --can 3      можно ли, и чего не хватает

  код 0 — сделано/можно, 1 — нельзя (условие не выполнено), 2 — не смог,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MAX_LEVEL = 3

LEVELS = {
    0: "ничего сама — человек подтверждает каждый шаг",
    1: "план по шаблону, код только с подтверждения",
    2: "код пишется без подтверждения каждого таска",
    3: "ход закрывается без подтверждения плана",
}


def state_file(root: Path) -> Path:
    return root / ".superstack" / "autonomy.json"


def read(root: Path) -> dict:
    try:
        d = json.loads(state_file(root).read_text("utf-8"))
    except (OSError, ValueError):
        return {"level": 0, "why": "не задавалась — начинаем с нуля"}
    lvl = d.get("level")
    if not isinstance(lvl, int) or not 0 <= lvl <= MAX_LEVEL:
        return {"level": 0, "why": "запись повреждена — считаем нулём"}
    return d


def _has_tests(root: Path) -> bool:
    if (root / "package.json").is_file():
        try:
            if "test" in json.loads(
                    (root / "package.json").read_text("utf-8")).get("scripts", {}):
                return True
        except ValueError:
            pass
    for m in ("pytest.ini", "pyproject.toml", "tox.ini", "Makefile",
              "tests/conftest.py", "go.mod", "Cargo.toml"):
        if (root / m).is_file():
            return True
    return False


def _verify_answers(root: Path) -> bool:
    """Гейт верификации отвечает кодом, а не «проверять нечем».

    Читается ЗАПИСЬ последнего прогона, а не запускается проверка: подъём
    ступени не должен зависеть от того, соберётся ли проект прямо сейчас.
    """
    f = root / ".superstack" / "verify-last.json"
    try:
        return json.loads(f.read_text("utf-8")).get("exit_code") in (0, 1)
    except (OSError, ValueError, AttributeError):
        return False


def _mutations_registered(root: Path) -> bool:
    for rel in (".superstack/mutations.json", "tests/mutations.json"):
        f = root / rel
        try:
            if len(json.loads(f.read_text("utf-8")).get("mutations", [])) > 0:
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def _blind_ran(root: Path) -> bool:
    try:
        return bool(json.loads(
            (root / ".superstack" / "manifest.json").read_text("utf-8")).get("blind"))
    except (OSError, ValueError, AttributeError):
        return False


#: Условие ступени: что обязано быть доказано и где это записано.
GATES = {
    1: [(_has_tests, "нет команды тестов — проверять работу нечем")],
    2: [(_has_tests, "нет команды тестов"),
        (_verify_answers, "гейт верификации ни разу не отвечал кодом 0 или 1: "
                          "«проверять нечем» — не основание писать код без "
                          "подтверждения")],
    3: [(_has_tests, "нет команды тестов"),
        (_verify_answers, "гейт верификации не отвечал кодом"),
        (_mutations_registered, "ни одной зарегистрированной поломки — что тесты "
                                "умеют падать, здесь никем не измерено"),
        (_blind_ran, "слепая приёмка ни разу не выносила вердикт — расхождение "
                     "«сделали не то» ловить нечем")],
}


def can(root: Path, level: int) -> dict:
    if level == 0:
        return {"ok": True, "level": 0, "missing": []}
    missing = [why for check, why in GATES.get(level, []) if not check(root)]
    return {"ok": not missing, "level": level, "missing": missing}


def write(root: Path, level: int, missing: list) -> None:
    f = state_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"level": level, "why": LEVELS[level],
                             "checked": [c for c in missing]},
                            ensure_ascii=False, indent=1), encoding="utf-8")


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def _level_arg(argv: list, flag: str) -> "int | None":
    if flag not in argv or argv.index(flag) + 1 >= len(argv):
        return None
    try:
        return int(argv[argv.index(flag) + 1])
    except ValueError:
        return None


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain = [a for a in argv if not a.startswith("--") and not a.isdigit()]
    if len(plain) != 1:
        print("вызов: autonomy.py <корень> --show | --set N | --can N",
              file=sys.stderr)
        return 3
    root = Path(plain[0]).expanduser().resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 2

    if "--show" in argv or (not any(f in argv for f in ("--set", "--can"))):
        cur = read(root)
        print(f"СТУПЕНЬ {cur['level']}: {LEVELS[cur['level']]}", file=sys.stderr)
        print(json.dumps(cur, ensure_ascii=False, indent=1))
        return 0

    want = _level_arg(argv, "--set")
    if want is None:
        want = _level_arg(argv, "--can")
    if want is None or not 0 <= want <= MAX_LEVEL:
        print(f"НЕ УДАЛОСЬ: ступень — целое от 0 до {MAX_LEVEL}", file=sys.stderr)
        return 3

    v = can(root, want)
    if not v["ok"]:
        print(f"СТУПЕНЬ {want} НЕ ЗАСЛУЖЕНА", file=sys.stderr)
        for x in v["missing"]:
            print(f"  ! {x}", file=sys.stderr)
        print("  подтверждения снимаются вместе с добавлением проверок, а не "
              "вместо них", file=sys.stderr)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 1

    if "--set" in argv:
        write(root, want, v["missing"])
        print(f"СТУПЕНЬ {want}: {LEVELS[want]}", file=sys.stderr)
    else:
        print(f"СТУПЕНЬ {want} ДОСТУПНА", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
