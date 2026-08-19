#!/usr/bin/env python3
"""SUPERSTACK — потолок обращений наружу: цикл останавливается счётом, а не совестью.

Зачем это в автономном прогоне.

Автономная петля обращается к внешнему справочнику (документация, поиск, чужое
API) столько раз, сколько ей покажется нужным. Каждое обращение стоит денег или
лимита, и растёт этот расход тихо: ни одна отдельная попытка не выглядит
лишней. Человек узнаёт о трате, когда она уже случилась, — а он как раз тот,
кто закрыл ноутбук и доверился слову «автономно».

Поэтому у обращений есть потолок, и считает его код. Не «старайся поменьше» —
инструкция такого рода исполняется ровно до первой трудной задачи.

Три правила:

  1. НЕ ОБЪЯВЛЕНО — НЕ БЕЗЛИМИТ. Отсутствие потолка даёт код 2 «считать
     нечего», а не разрешение тратить. Умолчание «без ограничений» — это
     счёт, который увидят потом.
  2. ПОТОЛОК ОСТАНАВЛИВАЕТ, А НЕ ПРЕДУПРЕЖДАЕТ. Достигнут — цикл обязан
     встать. Предупреждение, которое можно проигнорировать, игнорируют.
  3. СЧЁТ ИДЁТ ПО ЖУРНАЛУ, А НЕ ПО ПАМЯТИ МОДЕЛИ. «Кажется, я обращался пару
     раз» не является измерением.

  .superstack/quota.json: {"limits": {"context7": 40, "web": 15}}

  python3 quota.py <корень> --log <каталог журнала> [--json]

  код 0 — есть запас, 1 — потолок достигнут, цикл обязан встать,
  2 — считать нечего, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

СПЕКА = ".superstack/quota.json"

#: Поле события, по которому узнаётся обращение наружу. Пишет его сам
#: инструмент — подделать случайно нельзя.
ПОЛЕ = "external"


def limits(root: Path) -> tuple:
    p = root / СПЕКА
    if not p.is_file():
        return None, (f"потолок не объявлен: нет {СПЕКА} — это «считать нечего», "
                      "а не «без ограничений»")
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{СПЕКА} не разобран ({e})"
    л = (d or {}).get("limits")
    if not isinstance(л, dict) or not л:
        return None, f"в {СПЕКА} ни одного потолка"
    плохие = {к: v for к, v in л.items() if not isinstance(v, int) or v <= 0}
    if плохие:
        return None, f"потолок должен быть положительным числом: {sorted(плохие)}"
    return л, ""


def spent(журнал: Path) -> dict:
    """{источник: сколько обращений} по журналу. Память модели не считается."""
    итог: dict = {}
    if not журнал.is_dir():
        return итог
    for f in sorted(журнал.rglob("*.jsonl")):
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
            имя = d.get(ПОЛЕ)
            if имя:
                итог[str(имя)] = итог.get(str(имя), 0) + 1
    return итог


def verdict(лимиты: dict, потрачено: dict) -> dict:
    """Потолок достигнут — цикл встаёт. Предупреждение здесь не годится."""
    строки, исчерпаны = [], []
    for источник, потолок in sorted(лимиты.items()):
        сколько = потрачено.get(источник, 0)
        строки.append({"source": источник, "spent": сколько, "limit": потолок,
                       "left": max(0, потолок - сколько)})
        if сколько >= потолок:
            исчерпаны.append(источник)
    if исчерпаны:
        return {"status": "fail", "counters": строки, "exhausted": исчерпаны,
                "detail": "потолок достигнут: " + ", ".join(исчерпаны),
                "next": "цикл обязан встать и спросить человека: потолок, который "
                        "можно перешагнуть, потолком не является"}
    return {"status": "pass", "counters": строки,
            "detail": "запас есть: " + ", ".join(
                f"{с['source']} {с['spent']}/{с['limit']}" for с in строки)}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    берут = {"--log"}
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
        print("вызов: quota.py <корень> --log <каталог журнала>", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    лимиты, отказ = limits(root)
    if лимиты is None:
        print(f"СЧИТАТЬ НЕЧЕГО: {отказ}", file=sys.stderr)
        print(json.dumps({"status": "unknown", "detail": отказ},
                         ensure_ascii=False, indent=1))
        return 2

    журнал = Path(argv[argv.index("--log") + 1]).expanduser() \
        if "--log" in argv else (root / ".superstack" / "log")
    v = verdict(лимиты, spent(журнал))
    if "--json" not in argv:
        голова = {"pass": "ЗАПАС ЕСТЬ", "fail": "ПОТОЛОК ДОСТИГНУТ"}
        print(f"{голова[v['status']]}: {v['detail']}", file=sys.stderr)
        if v.get("next"):
            print(f"  дальше: {v['next']}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 0 if v["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
