#!/usr/bin/env python3
"""SUPERSTACK — история собирается по объявленной нарезке, а не «как получилось».

Зачем планировать коммиты.

История git — документ, который читает следующий человек, и чаще всего этот
человек ты через месяц. Когда нарезка не объявлена заранее, она получается сама:
один коммит на всё подряд, потому что «работа же связана», и через месяц ответ
на вопрос «почему здесь так» приходится искать в диффе на четыре тысячи строк.
Откатить одну неудачную часть тоже нельзя — она вплетена в удачные.

Нарезка объявляется ДО работы: какие куски, что в каждом и почему он отдельный.
Дальше это проверяется счётом, а не памятью.

Три правила:

  1. НЕ ОБЪЯВЛЕНО — НЕ ПРОВЕРЕНО. Нет плана коммитов — код 2 «сверять не с
     чем», а не «всё хорошо».
  2. КУСОК БЕЗ КОММИТА — НЕ СДЕЛАН. Объявили и не выделили — история уже
     разошлась с планом, и дальше расхождение только растёт.
  3. СМЕШАННЫЙ КОММИТ НАЗЫВАЕТСЯ. Коммит, тронувший файлы двух разных кусков,
     лишает смысла обе записи: откатить один кусок больше нельзя.

  .superstack/commits.json:
    {"slices": [{"id": "схема", "why": "чтобы откатывать миграции отдельно",
                 "paths": ["migrations/"]}]}

  python3 commit_plan.py <корень> [--base <ветка>] [--json]

  код 0 — история сходится с планом, 1 — расхождение, 2 — сверять не с чем,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

СПЕКА = ".superstack/commits.json"


def план(root: Path) -> tuple:
    p = root / СПЕКА
    if not p.is_file():
        return None, (f"нарезка не объявлена: нет {СПЕКА} — сверять не с чем, "
                      "и это не то же самое, что «история в порядке»")
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{СПЕКА} не разобран ({e})"
    куски = (d or {}).get("slices")
    if not куски:
        return None, f"в {СПЕКА} ни одного куска"
    for к in куски:
        if not к.get("id") or not к.get("paths"):
            return None, "кусок без имени или без путей"
        if not (к.get("why") or "").strip():
            # Кусок без причины нельзя ни обсудить, ни отменить осознанно:
            # через месяц он читается как случайность.
            return None, f"кусок «{к['id']}» без причины: почему он отдельный"
    return куски, ""


def история(root: Path, база: str) -> tuple:
    """[(заголовок, [файлы])] по коммитам ветки. Нет истории — «не смог»."""
    try:
        p = subprocess.run(
            ["git", "-c", "core.quotepath=false", "log", f"{база}..HEAD",
             "--name-only", "--pretty=format:%x00%s"],
            cwd=str(root), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"git не отработал ({e})"
    if p.returncode != 0:
        return None, f"git log вернул {p.returncode}: {(p.stderr or '').strip()[-160:]}"
    коммиты = []
    for кусок in p.stdout.split("\x00"):
        строки = [s.strip() for s in кусок.splitlines() if s.strip()]
        if not строки:
            continue
        коммиты.append((строки[0], строки[1:]))
    return коммиты, ""


def _кусок(файл: str, куски: list) -> "str | None":
    имя = файл.replace("\\", "/")
    for к in куски:
        for префикс in к["paths"]:
            if имя.startswith(префикс.replace("\\", "/").lstrip("./")):
                return к["id"]
    return None


def drift(коммиты: list, куски: list) -> list:
    """Расхождения истории с планом. Каждое называет кусок или коммит."""
    итог, покрыты = [], set()
    for заголовок, файлы in коммиты:
        тронуты = {_кусок(f, куски) for f in файлы}
        тронуты.discard(None)
        покрыты |= тронуты
        if len(тронуты) > 1:
            итог.append({"id": "mixed-commit", "commit": заголовок,
                         "slices": sorted(тронуты),
                         "why": "коммит тронул два объявленных куска — откатить "
                                "один из них больше нельзя"})
    for к in куски:
        if к["id"] not in покрыты:
            итог.append({"id": "slice-without-commit", "slice": к["id"],
                         "why": f"объявлен и не выделен: {к['why']}"})
    return итог


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    берут = {"--base"}
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
        print("вызов: commit_plan.py <корень> [--base <ветка>]", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3
    база = argv[argv.index("--base") + 1] if "--base" in argv else "origin/main"

    куски, отказ = план(root)
    if куски is None:
        print(f"СВЕРЯТЬ НЕ С ЧЕМ: {отказ}", file=sys.stderr)
        print(json.dumps({"status": "unknown", "detail": отказ},
                         ensure_ascii=False, indent=1))
        return 2
    коммиты, отказ = история(root, база)
    if коммиты is None:
        print(f"СВЕРЯТЬ НЕ С ЧЕМ: {отказ}", file=sys.stderr)
        print(json.dumps({"status": "unknown", "detail": отказ},
                         ensure_ascii=False, indent=1))
        return 2

    нашли = drift(коммиты, куски)
    v = {"status": "fail" if нашли else "pass", "drift": нашли,
         "commits": len(коммиты), "slices": [к["id"] for к in куски],
         "detail": (f"расхождений: {len(нашли)}" if нашли
                    else f"история сходится с планом: кусков {len(куски)}, "
                         f"коммитов {len(коммиты)}")}
    if "--json" not in argv:
        print(("ИСТОРИЯ РАЗОШЛАСЬ С ПЛАНОМ: " if нашли else "СХОДИТСЯ: ")
              + v["detail"], file=sys.stderr)
        for r in нашли:
            где = r.get("commit") or r.get("slice")
            print(f"  ! {где}: {r['why']}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 1 if нашли else 0


if __name__ == "__main__":
    sys.exit(main())
