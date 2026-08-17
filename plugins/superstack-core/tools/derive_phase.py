#!/usr/bin/env python3
"""SUPERSTACK — какой этап идёт СЕЙЧАС, по следам на диске.

Зачем это вычисляется, а не записывается.

Панель показывала этап, записанный отдельной командой. Команду можно забыть — и
её забывали дважды за один вечер: сначала помощник писал код, пока на экране
висел дизайн, потом проверки закончились, а экран час показывал «Проверяем».
Человек смотрел на панель и видел неправду, причём уверенную.

Записанный этап — это НАМЕРЕНИЕ того, кто вёл прогон. Следы на диске — ФАКТ.
Когда они расходятся, прав факт: бриф, спека, промпты, возвраты и состояние
задач появляются от работы, а не от намерения.

Порядок проверки идёт СВЕРХУ ВНИЗ, от позднего этапа к раннему, и побеждает
первый совпавший. Так пропущенный этап не ломает вывод: человек может принести
систему и не делать экраны отдельно — цепочка «каждый следующий после
предыдущего» на этом ломалась бы, а «самый поздний из начатых» нет.

  python3 derive_phase.py <каталог .superstack>          -> JSON
  python3 derive_phase.py <каталог .superstack> --name    -> только имя этапа

  код 0 — определено, 2 — нечего смотреть, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

#: Держатели хода — те же слова, что и в progress.py. Расхождение поймает тест.
HUMAN, SYSTEM, EXECUTOR = "человек", "система", "исполнитель"


def _tasks(state: dict) -> list:
    return [t for w in (state.get("waves") or {}).values() for t in w]


def _has(run: Path, pattern: str) -> bool:
    return any(run.glob(pattern))


def read_state(run: Path) -> dict:
    try:
        return json.loads((run / "state.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def facts(run: Path) -> dict:
    """Наблюдаемые следы. Отдельной функцией, чтобы их можно было показать.

    Вывод без оснований — это оракул: он либо угадал, либо нет, и проверить
    нечем. С основаниями человек видит, ПОЧЕМУ система решила именно так, и
    ошибка вывода становится заметной, а не просто неверной.
    """
    state = read_state(run)
    ts = _tasks(state)
    return {
        "бриф": _has(run, "*-brief.md"),
        "разбор": (run / "premortem.json").is_file(),
        "спека": (run / "spec.md").is_file(),
        "дизайн-система": ((run / "design" / "SYSTEM.md").is_file()
                           or (run / "design-brief.json").is_file()),
        "система принесена": (run / "design" / "SYSTEM.md").is_file(),
        "экраны": (run / "design" / "SCREENS.md").is_file(),
        "задачи": bool(ts),
        "в работе": sum(1 for t in ts if t.get("status") == "running"),
        "возвраты": sum(1 for _ in run.glob("return-*.txt")),
        "проверено": sum(1 for t in ts if t.get("status") == "proven"),
        "всего задач": len(ts),
        "приёмка": (run / "acceptance.json").is_file(),
        "отчёт": (run / "report.html").is_file() or (run / "REPORT.md").is_file(),
        "блокирующая находка": _blocking(run),
    }


def _blocking(run: Path) -> bool:
    """Есть ли находка ревью, которая не пускает дальше.

    Это единственный признак, ради которого читаются чужие файлы: он переводит
    ход на человека, а «ход на человеке» — самое дорогое состояние панели.
    Молчащая блокировка означает, что все ждут друг друга.
    """
    for f in run.glob("review-*.json"):
        try:
            d = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if any(x.get("blocking") for x in d.get("findings", [])):
            return True
    return False


def derive(run: Path) -> dict:
    """Этап и держатель хода — из фактов, с перечислением оснований."""
    f = facts(run)

    # Сверху вниз: побеждает самый поздний начатый этап.
    if f["отчёт"]:
        return _say("Отчёт", SYSTEM, f, "отчёт написан")
    if f["задачи"] and f["проверено"] == f["всего задач"]:
        who = HUMAN if f["блокирующая находка"] else SYSTEM
        return _say("Приёмка", who, f, "все части проверены")
    if f["приёмка"]:
        return _say("Приёмка", HUMAN, f, "приёмка началась")
    if f["в работе"]:
        return _say("Пишем код", EXECUTOR, f,
                    f"частей в работе: {f['в работе']}")
    if f["возвраты"] and f["задачи"]:
        # Никто не пишет, но не всё доказано — идут проверки. Ход на человеке,
        # если проверка уже упёрлась в находку: дальше решает он.
        who = HUMAN if f["блокирующая находка"] else SYSTEM
        return _say("Проверяем", who, f,
                    f"помощники вернули работу ({f['возвраты']}), доказано "
                    f"{f['проверено']} из {f['всего задач']}")
    if f["задачи"]:
        return _say("План работ", SYSTEM, f, "работа разбита на части")
    if f["экраны"]:
        return _say("Дизайн экранов", SYSTEM, f, "экраны принесены")
    if f["дизайн-система"]:
        # Промпт собран, а системы ещё нет — значит человек ушёл её делать.
        # Это ровно тот случай, когда оба молчат и каждый ждёт другого.
        who = SYSTEM if f["система принесена"] else HUMAN
        return _say("Дизайн-система", who, f,
                    "система принесена" if f["система принесена"]
                    else "промпт собран, ждём систему от тебя")
    if f["спека"]:
        return _say("Описали, что строим", SYSTEM, f, "спека написана")
    if f["разбор"]:
        return _say("Разобрали задачу", SYSTEM, f, "разбор записан")
    if f["бриф"]:
        return _say("Записали просьбу", SYSTEM, f, "просьба записана")
    return _say("Записали просьбу", HUMAN, f, "следов работы ещё нет")


def _say(name: str, owner: str, f: dict, why: str) -> dict:
    return {"name": name, "owner": owner, "why": why,
            "facts": {k: v for k, v in f.items() if v}}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain = [a for a in argv if not a.startswith("--")]
    if not plain:
        print("вызов: derive_phase.py <каталог .superstack> [--name]",
              file=sys.stderr)
        return 3
    run = Path(plain[0]).resolve()
    if not run.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {run}", file=sys.stderr)
        return 3
    got = derive(run)
    if "--name" in argv:
        print(got["name"])
    else:
        print(json.dumps(got, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
