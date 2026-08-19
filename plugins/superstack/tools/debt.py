#!/usr/bin/env python3
"""SUPERSTACK — техдолг ищется счётом, а не впечатлением от чтения.

Зачем считать, если можно посмотреть.

«Этот файл давно пора переписать» — суждение, которое зависит от того, кто
последним в него лез. Оно попадает не туда: переписывают то, что неприятно
читать, а болит обычно другое — то, что приходится править ЧАСТО и что при
этом большое. Частая правка означает, что решение здесь не устоялось; размер
означает, что каждая такая правка дорогая. Вместе это счёт, а не вкус.

Три правила:

  1. ЭТО КАНДИДАТ, А НЕ ПРИГОВОР. Роутер большой и горячий по своей природе —
     и это нормально. Инструмент называет места, а решает человек.
  2. НЕТ ИСТОРИИ — НЕТ ОТВЕТА. Свежий репозиторий даёт код 2, а не «долга
     нет»: не найти и не смотреть — разные вещи.
  3. СЧЁТ ОБЪЯСНЁН. Рядом с каждым местом — сколько правок и сколько строк,
     чтобы с числом можно было спорить. Рейтинг без слагаемых — гадание с
     ранжированием.

  python3 debt.py <корень> [--weeks 26] [--top 10] [--json]

  код 0 — кандидатов нет, 1 — есть кандидаты, 2 — считать не по чему,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

НЕДЕЛЬ = 26
СКОЛЬКО = 10

#: Ниже этого счёта место не стоит внимания: два правки в файле на сто строк
#: не долг, а обычная жизнь.
ПОРОГ = 500

ПРОПУСК = ("/node_modules/", "/.git/", "/dist/", "/build/", "/vendor/",
           "package-lock.json", "yarn.lock", "poetry.lock")
РАСШИРЕНИЯ = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".kt",
              ".swift", ".java", ".php", ".rs", ".sh")


def churn(root: Path, недель: int) -> tuple:
    """{файл: сколько раз правился}. Нет истории — это «не смог», а не «ноль»."""
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotepath=false", "log",
             f"--since={недель} weeks ago", "--name-only", "--pretty=format:"],
            cwd=str(root), capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git не отработал ({e})"
    if p.returncode != 0:
        хвост = (p.stderr or "").strip()
        # Пустой репозиторий — не поломка, а «истории ещё нет». Технический
        # текст git тут читается человеком как «сломалось», хотя всё в порядке.
        if "does not have any commits" in хвост:
            return None, "в репозитории ещё нет коммитов — считать не по чему"
        return None, f"git log вернул {p.returncode}: {хвост[-160:]}"
    счёт: dict = {}
    for строка in p.stdout.splitlines():
        имя = строка.strip()
        if not имя:
            continue
        счёт[имя] = счёт.get(имя, 0) + 1
    if not счёт:
        return None, f"за {недель} недель правок не было — считать не по чему"
    return счёт, ""


def _строк(p: Path) -> int:
    try:
        return len(p.read_text("utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def hotspots(root: Path, правки: dict, сколько: int) -> list:
    """Места по счёту «правок × строк». Каждое несёт свои слагаемые."""
    места = []
    for имя, n in правки.items():
        путь = root / имя
        if any(з in "/" + имя for з in ПРОПУСК):
            continue
        if путь.suffix not in РАСШИРЕНИЯ or not путь.is_file():
            continue
        строк = _строк(путь)
        счёт = n * строк
        if счёт < ПОРОГ:
            continue
        места.append({"file": имя, "edits": n, "lines": строк, "score": счёт})
    места.sort(key=lambda м: -м["score"])
    return места[:сколько]


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    берут = {"--weeks", "--top"}
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
        print("вызов: debt.py <корень> [--weeks N] [--top N]", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3
    недель = int(argv[argv.index("--weeks") + 1]) if "--weeks" in argv else НЕДЕЛЬ
    сколько = int(argv[argv.index("--top") + 1]) if "--top" in argv else СКОЛЬКО

    правки, отказ = churn(root, недель)
    if правки is None:
        print(f"СЧИТАТЬ НЕ ПО ЧЕМУ: {отказ}", file=sys.stderr)
        print(json.dumps({"status": "unknown", "detail": отказ},
                         ensure_ascii=False, indent=1))
        return 2

    места = hotspots(root, правки, сколько)
    v = {"status": "fail" if места else "pass", "hotspots": места,
         "weeks": недель,
         "detail": (f"мест под вниманием: {len(места)}" if места
                    else f"за {недель} недель горячих мест нет")}
    if "--json" not in argv:
        print(("ГОРЯЧИЕ МЕСТА: " if места else "ЧИСТО: ") + v["detail"],
              file=sys.stderr)
        for м in места:
            print(f"  · {м['file']}: правок {м['edits']} × строк {м['lines']}"
                  f" = {м['score']}", file=sys.stderr)
        if места:
            print("  это кандидаты, а не приговор: роутер большой и горячий "
                  "по своей природе", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 1 if места else 0


if __name__ == "__main__":
    sys.exit(main())
