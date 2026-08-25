#!/usr/bin/env python3
"""SUPERSTACK — заведён ли он в ЭТОМ проекте.

Зачем это вообще существует.

Плагины Claude Code ставятся глобально: `Scope: user`. Их хуки объявляются без
привязки к проекту, поэтому установленный SUPERSTACK начинает работать во ВСЕХ
проектах на машине — включая те, где человек его никогда не звал. Найдено
заказчиком на живом случае: в соседнем каталоге он пишет обучающие материалы,
а система спрашивала там про уроки и запускала гейт верификации.

Самый неприятный из этих хуков — гейт верификации: в чужом проекте он может не
дать закрыть ход, который человеку нечем закрывать, потому что ни тестов, ни
плана там нет и не предполагалось.

Выключатели у продукта были только глобальные (`SUPERSTACK_DISABLE=1`, файл
`PAUSE`) — то есть «выключить везде». Нужно ровно обратное: «включить там, где
позвали». Отсюда реестр.

ЧТО СЧИТАЕТСЯ ЗАВЕДЕНИЕМ. Явный вызов скилла: `/go`, `/superstack`, `/what`,
`/fix`, `/oops` первой строкой ставят отметку. Скиллы остаются доступны везде —
их вызывают руками, и отзываться они обязаны. Молчат ХУКИ, потому что хук
срабатывает сам, без спроса.

ПОЧЕМУ РЕЕСТР ГЛОБАЛЬНЫЙ, А НЕ ФАЙЛ В ПРОЕКТЕ. Файл в проекте уезжает в чужой
git и заводит систему у того, кто её не ставил. Заведение — решение человека на
его машине, и жить оно должно там же.

Вложенность учитывается: проект внутри заведённого дерева считается заведённым,
иначе работа в подкаталоге репозитория выглядела бы как чужой проект.

  python3 enable.py .              завести проект (идемпотентно)
  python3 enable.py . --check      только проверить: 0 — заведён, 1 — нет
  python3 enable.py . --forget     забыть проект
  python3 enable.py --list         показать заведённые

  код 0 — да/сделано, 1 — не заведён, 2 — не смог, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def registry() -> Path:
    state = os.environ.get("SUPERSTACK_STATE_DIR") or \
        str(Path.home() / ".claude" / "superstack")
    return Path(state) / "projects"


def known() -> list:
    try:
        return [ln.strip() for ln in
                registry().read_text("utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def enabled_for(path: Path) -> "str | None":
    """Заведён ли проект. Возвращает запись, которая его покрывает.

    Совпадение префиксное по СЕГМЕНТАМ пути: `/a/bc` не должен считаться
    заведённым из-за записи `/a/b`, хотя строкой одна начинается с другой.
    """
    p = path.resolve()
    for entry in known():
        e = Path(entry)
        if p == e or e in p.parents:
            return entry
    return None


def enable(path: Path) -> str:
    p = str(path.resolve())
    cur = known()
    if p not in cur:
        r = registry()
        r.parent.mkdir(parents=True, exist_ok=True)
        r.write_text("\n".join(cur + [p]) + "\n", encoding="utf-8")
    return p


def forget(path: Path) -> bool:
    p = str(path.resolve())
    cur = known()
    if p not in cur:
        return False
    registry().write_text("\n".join(x for x in cur if x != p) + "\n",
                          encoding="utf-8")
    return True


def main() -> int:
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    plain = [a for a in argv if not a.startswith("--")]
    unknown = flags - {"--check", "--forget", "--list", "--json"}
    if unknown:
        print(f"НЕ УДАЛОСЬ: неизвестный флаг {', '.join(sorted(unknown))}",
              file=sys.stderr)
        return 3

    if "--list" in flags:
        for p in known():
            print(p)
        return 0
    if len(plain) != 1:
        print("вызов: enable.py <каталог> [--check|--forget] | --list",
              file=sys.stderr)
        return 3

    path = Path(plain[0]).expanduser()
    if not path.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {path}", file=sys.stderr)
        return 3

    if "--check" in flags:
        hit = enabled_for(path)
        if "--json" in flags:
            print(json.dumps({"enabled": hit is not None, "covered_by": hit},
                             ensure_ascii=False))
        return 0 if hit else 1

    if "--forget" in flags:
        gone = forget(path)
        print("ЗАБЫТ" if gone else "не был заведён", file=sys.stderr)
        return 0

    p = enable(path)
    print(f"ЗАВЕДЁН: {p}", file=sys.stderr)
    print("  хуки SUPERSTACK работают здесь; в остальных проектах молчат",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
