#!/usr/bin/env python3
"""SUPERSTACK — тяжёлый MCP выдаётся адресно: одной роли, а не всем.

Зачем это отдельный механизм.

Браузерные MCP — самые дорогие схемы на ход: их описания уезжают в контекст
КАЖДОЙ роли, которой они выданы, и платит за это каждый ход, а не тот, где ими
пользовались. Дальше начинается ловушка. Ради бюджета их отключают первыми — и
тем самым убирают единственный способ ПОСМОТРЕТЬ на результат, а не поверить
отчёту о нём. То есть экономия своими руками создаёт дыру с верификацией.

Выход не в том, чтобы выбрать между ценой и проверкой, а в адресности: тяжёлый
инструмент получает ровно та роль, чья петля без него невозможна.

Два перекоса, и оба названы одинаково громко:

  1. ВЫДАНО ЛИШНИМ. Роль, которой браузер не нужен, платит за него каждым
     ходом молча — счёт приходит не за то, чем пользовались.
  2. НЕ ВЫДАНО НУЖНОЙ. Роль-оракул без браузера превращается в ещё одного
     читателя отчётов. Это не экономия, а тихая потеря проверки — и она
     дороже, потому что незаметна.

  python3 mcp_routing.py <каталог агентов> [--json]

  код 0 — раздача адресная, 1 — есть перекос, 2 — читать нечего,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

СПИСОК = Path(__file__).resolve().parent.parent / "data" / "heavy-mcp.json"
ИМЯ = re.compile(r"(?m)^name:\s*(.+?)\s*$")
ИНСТРУМЕНТЫ = re.compile(r"(?m)^tools:\s*(.+?)\s*$")


def каталог(path: Path = СПИСОК) -> tuple:
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"список тяжёлых MCP не прочитан: {e}"
    if not (d.get("heavy") or []):
        return None, "список тяжёлых MCP пуст"
    return d, ""


def роли(каталог_агентов: Path) -> dict:
    """{роль: [инструменты]} из фронтматтера. Читается файл, а не память."""
    итог: dict = {}
    if not каталог_агентов.is_dir():
        return итог
    for f in sorted(каталог_агентов.glob("*.md")):
        try:
            голова = f.read_text("utf-8", errors="replace")[:1200]
        except OSError:
            continue
        имя = ИМЯ.search(голова)
        инстр = ИНСТРУМЕНТЫ.search(голова)
        итог[(имя.group(1) if имя else f.stem)] = [
            с.strip() for с in (инстр.group(1) if инстр else "").split(",")
            if с.strip()]
    return итог


def findings(раздача: dict, тяжёлые: list) -> list:
    """Перекосы в обе стороны. Каждый называет роль, инструмент и цену."""
    итог = []
    for h in тяжёлые:
        нужна = h["needs"]
        владеют = [роль for роль, инстр in раздача.items()
                   if any(и.startswith(h["prefix"]) for и in инстр)]
        лишние = [р for р in владеют if р != нужна]
        for р in лишние:
            итог.append({"id": "granted-to-extra-role", "role": р,
                         "mcp": h["prefix"],
                         "why": f"платит каждым ходом за то, чем не пользуется: "
                                f"{h['cost']}"})
        if нужна in раздача and нужна not in владеют:
            # Не экономия, а тихая потеря проверки — и она дороже, потому
            # что незаметна.
            итог.append({"id": "missing-where-required", "role": нужна,
                         "mcp": h["prefix"],
                         "why": f"без него роль перестаёт быть оракулом: {h['why']}"})
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
    plain = [a for a in argv if not a.startswith("--")]
    if len(plain) != 1:
        print("вызов: mcp_routing.py <каталог агентов> [--json]", file=sys.stderr)
        return 3
    d, отказ = каталог()
    if d is None:
        print(f"НЕ УДАЛОСЬ: {отказ}", file=sys.stderr)
        return 2
    агенты = Path(plain[0]).resolve()
    раздача = роли(агенты)
    if not раздача:
        print(f"ЧИТАТЬ НЕЧЕГО: агентов не найдено в {агенты}", file=sys.stderr)
        return 2

    нашли = findings(раздача, d["heavy"])
    v = {"status": "fail" if нашли else "pass", "findings": нашли,
         "roles": sorted(раздача),
         "detail": (f"перекосов: {len(нашли)}" if нашли
                    else f"раздача адресная: ролей {len(раздача)}")}
    if "--json" not in argv:
        print(("РАЗДАЧА С ПЕРЕКОСОМ: " if нашли else "РАЗДАЧА АДРЕСНАЯ: ")
              + v["detail"], file=sys.stderr)
        for f in нашли:
            print(f"  ! {f['role']} · {f['mcp']}: {f['why']}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 1 if нашли else 0


if __name__ == "__main__":
    sys.exit(main())
