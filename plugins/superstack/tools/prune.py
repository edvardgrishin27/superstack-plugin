#!/usr/bin/env python3
"""SUPERSTACK — что стоит и не срабатывает: предложить к удалению, не удаляя.

Зачем это нужно.

Установленное копится и не убывает. Каждый скилл, правило и хук платит собой
за место в контексте и во внимании — а понять, работает ли он, нельзя, глядя
на файл: файл на месте всегда. Через полгода система выглядит богатой и на
треть состоит из того, что не срабатывало ни разу.

Здесь считается ровно одно: что из зарегистрированного НЕ появлялось в журнале
дольше срока. Не «что не нужно» — этого код не знает, — а «что не работало».
Разница принципиальная, и она определяет, что инструмент делает дальше.

Три правила:

  1. ПРЕДЛОЖИТЬ, А НЕ УДАЛИТЬ. «Не срабатывало» и «не нужно» — разные
     утверждения. Аварийный выключатель не срабатывал ни разу, и это лучшее,
     что можно про него сказать. Решение принимает человек.
  2. НЕТ ЖУРНАЛА — НЕТ ВЕРДИКТА. Пустой журнал означает, что судить не по
     чему: код 2, а не «всё лишнее».
  3. МОЛОДОЕ НЕ СУДИМ. Поставленное вчера не срабатывало вчера. Пока журнал
     короче срока наблюдения, вердикта нет ни у кого.

  python3 prune.py <каталог журнала> --weeks 8 [--json]

  код 0 — кандидатов нет, 1 — есть кандидаты, 2 — судить не по чему,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

НЕДЕЛЬ = 8

#: Что считается «сработало»: инструмент назвал себя в журнале. Строка события
#: пишется самим инструментом, поэтому подделать её случайно нельзя.
ПОЛЕ = "tool"

#: Инструменты, чьё молчание — хорошая новость, а не повод удалять. Список
#: маленький и объяснённый: без него первым под нож пойдёт тормоз.
МОЛЧАНИЕ_ЭТО_ХОРОШО = {
    "pause.sh": "аварийный выключатель: он и должен молчать",
    "oops.py": "откат: молчит, пока ничего не сломалось",
    "blind_accept.py": "слепая приёмка: зовут в конце работы, а не каждый ход",
}


def события(каталог: Path) -> tuple:
    """[(инструмент, когда)] из всех файлов журнала. Битые строки пропускаются."""
    если_нет = (None, f"журнала нет: {каталог}")
    if not каталог.is_dir():
        return если_нет
    собрано = []
    for f in sorted(каталог.rglob("*.jsonl")):
        try:
            текст = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for строка in текст.splitlines():
            if not строка.strip():
                continue
            try:
                d = json.loads(строка)
            except ValueError:
                continue
            имя, когда = d.get(ПОЛЕ), d.get("ts")
            if имя and когда:
                собрано.append((str(имя), str(когда)))
    if not собрано:
        return если_нет
    return собрано, ""


def _дата(s: str) -> "datetime | None":
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def candidates(события_: list, установлено: list, недель: int,
               сейчас: datetime) -> dict:
    """Кто не появлялся дольше срока. Молодой журнал вердикта не даёт."""
    порог = сейчас - timedelta(weeks=недель)
    последний: dict = {}
    самое_старое = None
    for имя, когда in события_:
        d = _дата(когда)
        if d is None:
            continue
        if имя not in последний or d > последний[имя]:
            последний[имя] = d
        if самое_старое is None or d < самое_старое:
            самое_старое = d

    if самое_старое is None or самое_старое > порог:
        # Поставленное вчера не срабатывало вчера. Судить не по чему.
        return {"status": "unknown",
                "detail": f"журнал короче срока наблюдения ({недель} нед.) — "
                          "вердикта нет ни у кого"}

    кандидаты, живые = [], []
    for имя in sorted(set(установлено)):
        if имя in МОЛЧАНИЕ_ЭТО_ХОРОШО:
            continue
        d = последний.get(имя)
        if d is None:
            кандидаты.append({"tool": имя, "last_seen": None,
                              "why": "не появлялся в журнале ни разу"})
        elif d < порог:
            кандидаты.append({"tool": имя,
                              "last_seen": d.strftime("%Y-%m-%d"),
                              "why": f"молчит дольше {недель} недель"})
        else:
            живые.append(имя)
    return {"status": "fail" if кандидаты else "pass",
            "candidates": кандидаты, "alive": живые,
            "detail": (f"кандидатов к удалению: {len(кандидаты)} из "
                       f"{len(кандидаты) + len(живые)}") if кандидаты
                      else f"всё живое: {len(живые)}"}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    берут = {"--weeks", "--tools"}
    plain, пропуск = [], False
    for a in argv:
        if пропуск:
            пропуск = False
            continue
        if a in берут:
            пропуск = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) != 1:
        print("вызов: prune.py <каталог журнала> [--weeks N]", file=sys.stderr)
        return 3
    каталог = Path(plain[0]).expanduser()
    недель = int(argv[argv.index("--weeks") + 1]) if "--weeks" in argv else НЕДЕЛЬ

    соб, отказ = события(каталог)
    if соб is None:
        print(f"СУДИТЬ НЕ ПО ЧЕМУ: {отказ}", file=sys.stderr)
        print(json.dumps({"status": "unknown", "detail": отказ},
                         ensure_ascii=False, indent=1))
        return 2

    установлено = [s.strip() for s in
                   (argv[argv.index("--tools") + 1] if "--tools" in argv
                    else "").split(",") if s.strip()]
    if not установлено:
        установлено = sorted({и for и, _ in соб})

    v = candidates(соб, установлено, недель, datetime.now(timezone.utc))
    if "--json" not in argv:
        голова = {"pass": "ВСЁ ЖИВОЕ", "fail": "ЕСТЬ КАНДИДАТЫ К УДАЛЕНИЮ",
                  "unknown": "СУДИТЬ НЕ ПО ЧЕМУ"}
        print(f"{голова[v['status']]}: {v['detail']}", file=sys.stderr)
        for к in v.get("candidates", [])[:20]:
            print(f"  ? {к['tool']}: {к['why']}", file=sys.stderr)
        if v.get("candidates"):
            print("  решает человек: «не срабатывало» и «не нужно» — разные "
                  "утверждения", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
