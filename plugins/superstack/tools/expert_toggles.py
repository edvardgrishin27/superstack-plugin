#!/usr/bin/env python3
"""SUPERSTACK — четыре выключателя: один продукт, а не два.

Зачем это вообще существует.

Из 29 механизмов, помогающих опытному, четыре прямо мешают новичку. Не потому,
что сложны, а потому, что каждый ОТКЛАДЫВАЕТ обратную связь: новичок узнаёт о
поломке позже, чем мог бы, и к тому моменту уже не помнит, какое из своих
действий её вызвало. Опытный это терпит ради скорости — он помнит свои шаги.

Соблазн здесь — сделать два продукта: «для новичков» и «для профи». Это ошибка,
и дорогая: два продукта расходятся через месяц, а человек, выросший из первого,
переезжает во второй как в чужой. Один продукт с четырьмя выключателями решает
то же самое и не делится.

Три правила:

  1. ПО УМОЛЧАНИЮ ВЫКЛЮЧЕНО. Механизм, мешающий новичку, включается только
     явно. Умолчание «включено» превращает выключатель в ловушку: тот, кому
     он вредит, о нём и не узнает.
  2. ВКЛЮЧЕНИЕ НАЗЫВАЕТ ЦЕНУ. Рядом с каждым — чем он помогает и чем мешает.
     Выбор без цены это не выбор.
  3. НЕИЗВЕСТНЫЙ ВЫКЛЮЧАТЕЛЬ — ОТКАЗ. Опечатка в имени иначе читается как
     «включено» ровно у того механизма, которого нет, а настоящий остаётся
     выключенным молча.

  .superstack/expert.json: {"enabled": ["worktree-sandbox"]}

  python3 expert_toggles.py <корень> [--json]
  python3 expert_toggles.py --list

  код 0 — набор согласован, 1 — включено неизвестное, 2 — список не прочитан,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

СПИСОК = Path(__file__).resolve().parent.parent / "data" / "expert-only.json"
СПЕКА = ".superstack/expert.json"


def каталог(path: Path = СПИСОК) -> tuple:
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"список выключателей не прочитан: {e}"
    механизмы = d.get("mechanisms") or []
    if not механизмы:
        return None, "список выключателей пуст"
    return d, ""


def state(root: Path, d: dict) -> dict:
    """Что включено, что выключено и что названо неизвестным именем."""
    известные = {м["id"]: м for м in d["mechanisms"]}
    включено: list = []
    p = root / СПЕКА
    if p.is_file():
        try:
            своё = json.loads(p.read_text("utf-8"))
            включено = [str(x) for x in (своё.get("enabled") or [])]
        except (OSError, ValueError):
            включено = []
    # Опечатка в имени иначе читается как «включено» у механизма, которого
    # нет, а настоящий остаётся выключенным молча.
    чужие = [и for и in включено if и not in известные]
    on = [и for и in включено if и in известные]
    return {
        "on": [{"id": и, "helps": известные[и]["helps"],
                "hurts": известные[и]["hurts"]} for и in on],
        "off": [{"id": и, "what": м["what"], "hurts": м["hurts"]}
                for и, м in известные.items() if и not in on],
        "unknown": чужие,
    }


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    d, отказ = каталог()
    if d is None:
        print(f"НЕ УДАЛОСЬ: {отказ}", file=sys.stderr)
        return 2

    if "--list" in argv:
        print(json.dumps(d["mechanisms"], ensure_ascii=False, indent=1))
        return 0

    plain = [a for a in argv if not a.startswith("--")]
    if len(plain) != 1:
        print("вызов: expert_toggles.py <корень> [--json] | --list",
              file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    v = state(root, d)
    v["status"] = "fail" if v["unknown"] else "pass"
    v["detail"] = (f"включено неизвестных: {len(v['unknown'])}"
                   if v["unknown"] else
                   f"включено {len(v['on'])} из {len(v['on']) + len(v['off'])}")
    if "--json" not in argv:
        print(("НАБОР НЕ СОГЛАСОВАН: " if v["unknown"] else "ВЫКЛЮЧАТЕЛИ: ")
              + v["detail"], file=sys.stderr)
        for м in v["on"]:
            print(f"  + {м['id']}: помогает — {м['helps']}", file=sys.stderr)
            print(f"      цена: {м['hurts']}", file=sys.stderr)
        for м in v["off"]:
            print(f"  · {м['id']} выключен: {м['what']}", file=sys.stderr)
        for и in v["unknown"]:
            print(f"  ! нет такого выключателя: {и}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 1 if v["unknown"] else 0


if __name__ == "__main__":
    sys.exit(main())
