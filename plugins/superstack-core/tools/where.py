#!/usr/bin/env python3
"""SUPERSTACK — найти инструмент соседнего пакета по имени.

Зачем это отдельный файл.

`${CLAUDE_PLUGIN_ROOT}` разворачивается в корень ТОГО плагина, чей скилл сейчас
исполняется. Скилл `/go` живёт в `superstack-build`, а зовёт `verify.py` из
`superstack-guard` и `render_html.py` из `superstack-core`. Написанное в лоб
`$CLAUDE_PLUGIN_ROOT/tools/verify.py` указывает на файл, которого нет, — и это
было написано, прожило несколько заходов и обнаружилось только замером.

Отказ при этом самый неприятный из возможных: «нет такого файла» выглядит
поломкой окружения, а не опечаткой в пути, и человек идёт чинить установку.

Поэтому предположение о раскладке пакетов живёт ЗДЕСЬ, в одном месте. Оно всё
ещё предположение — но теперь оно одно, названо вслух и проверяется тестом,
а не повторено в двадцати строках двадцати скиллов.

Порядок поиска — от самого надёжного к самому широкому:

  1. свой пакет (инструмент из того же плагина);
  2. соседи по каталогу плагинов — обычная раскладка маркетплейса;
  3. вверх до каталога `plugins/` и вширь — на случай другой раскладки.

Не нашли — код 1 и внятное сообщение с тем, где искали. Молчаливый возврат
пустой строки дал бы `python3 ""`, то есть ещё одну ошибку не по адресу.

  python3 where.py verify.py          -> абсолютный путь в stdout, код 0
  python3 where.py --all              -> все найденные инструменты, код 0

  код 0 — найден, 1 — не найден, 3 — ошибка вызова
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def roots(start: Path) -> list:
    """Каталоги, где могут лежать пакеты. Порядок = порядок доверия."""
    out = [start]
    # Соседи: .../plugins/<этот>/tools/where.py -> .../plugins/
    for up in (start.parent, start.parent.parent):
        if up.is_dir() and up not in out:
            out.append(up)
    return out


def find(name: str, start: Path = None) -> "Path | None":
    start = start or Path(__file__).resolve().parent.parent
    own = start / "tools" / name
    if own.is_file():
        return own
    seen = set()
    for base in roots(start):
        if base in seen:
            continue
        seen.add(base)
        # Сначала прямые соседи, потом произвольная глубина — чтобы одноимённый
        # файл из чужого дерева не обошёл настоящий пакет.
        for pat in (f"*/tools/{name}", f"*/*/tools/{name}"):
            hits = sorted(base.glob(pat))
            if hits:
                return hits[0]
    return None


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) != 1:
        print("вызов: where.py <имя_инструмента.py> | --all", file=sys.stderr)
        return 3

    start = Path(__file__).resolve().parent.parent
    if argv[0] == "--all":
        found = {}
        for base in roots(start):
            for p in sorted(base.glob("*/tools/*.py")):
                found.setdefault(p.name, str(p))
        for n, p in sorted(found.items()):
            print(f"{n}\t{p}")
        return 0

    hit = find(argv[0], start)
    if hit is None:
        looked = " · ".join(str(r) for r in roots(start))
        print(f"НЕ УДАЛОСЬ: инструмента «{argv[0]}» нет ни в одном пакете.\n"
              f"  искал в: {looked}\n"
              "  это не поломка окружения — это неверное имя либо пакет, "
              "который не установлен рядом", file=sys.stderr)
        return 1
    print(str(hit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
