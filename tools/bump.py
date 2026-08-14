#!/usr/bin/env python3
"""SUPERSTACK — поднять версию всех пакетов и записей маркетплейса.

Зачем скриптом.

Каталог установки НАЗВАН версией: `~/.claude/plugins/cache/<маркетплейс>/
<пакет>/<версия>/`. Движок сравнивает версию записи маркетплейса с установленной
и без подъёма считает, что обновлять нечего, — выкладка уходит в репозиторий и
не доезжает ни до кого. Молча: `git push` зелёный, `plugin update` говорит
«актуально», и починка живёт только у автора.

Мест, где версия записана, пятнадцать: семь `plugin.json` и семь записей в
`marketplace.json` плюс восьмая-связка, когда она появится. Правка руками
третий релиз подряд — ровно тот случай, который сам инструмент велит выносить
в код: повторяемое действие, которое однажды сделают наспех и пропустят одно
место из пятнадцати. Пропущенное место означает пакет, который не обновится, —
и набор разъедется по версиям, что хуже, чем не обновиться целиком.

Расхождение версий ДО подъёма — отказ, а не повод «выровнять заодно»: набор,
пакеты которого разошлись, собран не тем способом, каким собирается обычно, и
молча приводить его к одной версии значит прятать это.

  python3 bump.py                 поднять patch (0.2.1 -> 0.2.2)
  python3 bump.py 0.3.0           поставить явную версию
  python3 bump.py --dry           показать, что сделает, и ничего не писать

  код 0 — поднято, 1 — не смог (расхождение или откат назад), 3 — вызов
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARKET = REPO / ".claude-plugin" / "marketplace.json"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def manifests() -> list:
    return sorted(REPO.glob("plugins/*/.claude-plugin/plugin.json"))


def current() -> "tuple[str | None, dict]":
    """Общая версия набора и карта «файл → версия». None, если разошлись."""
    seen = {}
    for p in manifests():
        try:
            seen[p] = json.loads(p.read_text("utf-8")).get("version")
        except (OSError, ValueError):
            seen[p] = None
    vals = set(seen.values())
    return (vals.pop() if len(vals) == 1 else None), seen


def nxt(v: str) -> str:
    a, b, c = v.split(".")
    return f"{a}.{b}.{int(c) + 1}"


def newer(a: str, b: str) -> bool:
    """a строго новее b — по числам, а не по строкам (0.10.0 > 0.9.0)."""
    return tuple(int(x) for x in a.split(".")) > tuple(int(x) for x in b.split("."))


def write(version: str, dry: bool) -> list:
    touched = []
    for p in manifests():
        d = json.loads(p.read_text("utf-8"))
        if d.get("version") != version:
            d["version"] = version
            if not dry:
                p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
            touched.append(str(p.relative_to(REPO)))
    m = json.loads(MARKET.read_text("utf-8"))
    for entry in m.get("plugins", []):
        if entry.get("version") != version:
            entry["version"] = version
            touched.append(f"marketplace:{entry.get('name')}")
    if not dry:
        MARKET.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return touched


def main() -> int:
    argv = sys.argv[1:]
    dry = "--dry" in argv
    plain = [a for a in argv if not a.startswith("--")]
    if len(plain) > 1:
        print("вызов: bump.py [версия] [--dry]", file=sys.stderr)
        return 3

    now, seen = current()
    if now is None:
        print("НЕ УДАЛОСЬ: версии пакетов разошлись — набор собран не так, как "
              "собирается обычно, и выравнивать это молча нельзя:",
              file=sys.stderr)
        for p, v in sorted(seen.items()):
            print(f"  {p.parts[-3]}: {v}", file=sys.stderr)
        return 1

    want = plain[0] if plain else nxt(now)
    if not SEMVER.match(want):
        print(f"НЕ УДАЛОСЬ: «{want}» не версия вида 1.2.3", file=sys.stderr)
        return 3
    if not newer(want, now):
        # Откат назад означает, что у людей установлено НОВЕЕ выложенного, и
        # обновление до них не дойдёт — при этом всё выглядит успешным.
        print(f"НЕ УДАЛОСЬ: {want} не новее текущей {now} — обновление не "
              "дойдёт до тех, у кого уже стоит более новая", file=sys.stderr)
        return 1

    touched = write(want, dry)
    print(f"{'БУДЕТ' if dry else 'ПОДНЯТО'}: {now} -> {want}", file=sys.stderr)
    print(f"  мест: {len(touched)}", file=sys.stderr)
    print(json.dumps({"from": now, "to": want, "touched": touched,
                      "dry": dry}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
