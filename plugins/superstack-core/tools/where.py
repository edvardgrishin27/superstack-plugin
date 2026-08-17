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

ПОЧЕМУ ВЕРСИЯ ВЫБИРАЕТСЯ, А НЕ БЕРЁТСЯ ПЕРВАЯ ПОПАВШАЯСЯ.

Обновление плагина НЕ УДАЛЯЕТ прежнюю версию из кэша: рядом оказываются
`superstack-guard/0.2.0/` и `superstack-guard/0.2.1/`. Первая версия этого файла
возвращала `sorted(hits)[0]` — то есть СТАРШУЮ по алфавиту, а значит младшую по
номеру. Измерено сразу после обновления семи пакетов до 0.2.1: скилл из 0.2.1
звал `verify.py` из 0.2.0 и делал это молча.

Отказ здесь — худшего сорта: ничего не падает. Свежий скилл работает по старым
инструментам, поправка выглядит не применившейся, и человек идёт искать её в
своём коде. Ровно «не вижу изменений при верном коде = смотрит старую сборку».

Поэтому среди кандидатов первым идёт тот, чья версия СОВПАДАЕТ с версией своего
пакета (скилл 0.2.1 обязан звать инструменты 0.2.1 — они писались вместе), затем
старшая по номеру, и только потом безверсионная раскладка репозитория.

  python3 where.py verify.py          -> абсолютный путь в stdout, код 0
  python3 where.py --all              -> все найденные инструменты, код 0

  код 0 — найден, 1 — не найден, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

#: Каталог версии в установленной раскладке: `<пакет>/<версия>/tools/<файл>`.
#: В репозитории такого уровня нет, и это законная вторая раскладка.
_VERSION_DIR = re.compile(r"^\d+(?:\.\d+)*$")


#: Какой пакет даёт какой инструмент. Нужна для внятного отказа:
#: движок 2.1.42 не тянет зависимости, поэтому выборочная установка
#: оставляет дыры, и «нет такого файла» читается как поломка продукта.
#: Соответствие карты дереву проверяется тестом.
OWNER = {
    "adjudicate.py": "superstack-core",
    "adr.py": "superstack-spec",
    "apply.py": "superstack-install",
    "artifact_graph.py": "superstack-spec",
    "autonomy.py": "superstack-guard",
    "baseline.py": "superstack-brain",
    "blind_accept.py": "superstack-guard",
    "contract.py": "superstack-build",
    "crew.py": "superstack-build",
    "design_brief.py": "superstack-spec",
    "doctor.py": "superstack-brain",
    "enable.py": "superstack-core",
    "fix.py": "superstack-control",
    "gates.py": "superstack-spec",
    "handoff.py": "superstack-build",
    "learn.py": "superstack-brain",
    "lint_rules.py": "superstack-core",
    "live_panel.py": "superstack-core",
    "plain_ru.py": "superstack-core",
    "derive_phase.py": "superstack-core",
    "measure_run.py": "superstack-control",
    "page_check.py": "superstack-guard",
    "installed_drift.py": "superstack-control",
    "log.py": "superstack-core",
    "manifest.py": "superstack-spec",
    "memory_file.py": "superstack-brain",
    "memory_lint.py": "superstack-brain",
    "oops.py": "superstack-control",
    "premortem.py": "superstack-spec",
    "progress.py": "superstack-build",
    "project_doctor.py": "superstack-guard",
    "prove.py": "superstack-guard",
    "prove_tests.py": "superstack-guard",
    "render.py": "superstack-core",
    "render_html.py": "superstack-core",
    "review.py": "superstack-guard",
    "skill_test.py": "superstack-brain",
    "spec_lint.py": "superstack-spec",
    "state.py": "superstack-guard",
    "verify.py": "superstack-guard",
    "what.py": "superstack-control",
    "where.py": "superstack-core",
}


def roots(start: Path) -> list:
    """Каталоги, где могут лежать пакеты. Порядок = порядок доверия."""
    out = [start]
    # Соседи: .../plugins/<этот>/tools/where.py -> .../plugins/
    for up in (start.parent, start.parent.parent):
        if up.is_dir() and up not in out:
            out.append(up)
    return out


def own_version(start: Path) -> "str | None":
    """Версия своего пакета — из манифеста, а не из имени каталога.

    Имя каталога совпадает с версией только в установленной раскладке; манифест
    есть в обеих, и он источник истины для самого движка.
    """
    try:
        v = json.loads(
            (start / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return v.get("version") if isinstance(v, dict) else None


def _rank(p: Path, want: "str | None") -> tuple:
    """Ключ выбора среди одноимённых: своя версия → старшая → безверсионная."""
    d = p.parent.parent.name
    ver = d if _VERSION_DIR.match(d) else None
    same = 0 if (want is not None and ver == want) else 1
    # Отрицание делает сортировку по возрастанию сортировкой по УБЫВАНИЮ
    # номера; безверсионный путь получает положительный ключ и уходит в конец.
    order = tuple(-int(x) for x in ver.split(".")) if ver else (1,)
    return (same, order, str(p))


def pick(hits: list, want: "str | None") -> Path:
    return sorted(hits, key=lambda p: _rank(p, want))[0]


def find(name: str, start: Path = None) -> "Path | None":
    start = start or Path(__file__).resolve().parent.parent
    own = start / "tools" / name
    if own.is_file():
        return own
    want = own_version(start)
    seen = set()
    for base in roots(start):
        if base in seen:
            continue
        seen.add(base)
        # Сначала прямые соседи, потом произвольная глубина — чтобы одноимённый
        # файл из чужого дерева не обошёл настоящий пакет.
        for pat in (f"*/tools/{name}", f"*/*/tools/{name}"):
            hits = list(base.glob(pat))
            if hits:
                return pick(hits, want)
    return None


def main() -> int:
    argv = sys.argv[1:]
    if len(argv) != 1:
        print("вызов: where.py <имя_инструмента.py> | --all", file=sys.stderr)
        return 3

    start = Path(__file__).resolve().parent.parent
    if argv[0] == "--all":
        # Оба шаблона: без второго список пуст в установленной раскладке, где
        # между пакетом и `tools/` стоит каталог версии.
        found = {}
        for base in roots(start):
            for pat in ("*/tools/*.py", "*/*/tools/*.py"):
                for p in base.glob(pat):
                    found.setdefault(p.name, []).append(p)
        want = own_version(start)
        for n in sorted(found):
            print(f"{n}\t{pick(found[n], want)}")
        return 0

    hit = find(argv[0], start)
    if hit is None:
        who = OWNER.get(argv[0])
        looked = " · ".join(str(r) for r in roots(start))
        if who:
            # Назвать ПАКЕТ, а не только факт отсутствия. Зависимости в движке
            # 2.1.42 не работают, поэтому выборочная установка оставляет дыры,
            # и отказ «нет такого файла» человек читает как поломку продукта.
            print(f"НЕ УДАЛОСЬ: инструмента «{argv[0]}» нет — он живёт в "
                  f"пакете {who}, а тот не установлен.", file=sys.stderr)
            print(f"  поставить: CLAUDECODE= claude plugin install "
                  f"{who}@superstack", file=sys.stderr)
            print("  это не поломка: зависимости между пакетами движок пока "
                  "не тянет, и недостающий ставится вручную", file=sys.stderr)
        else:
            print(f"НЕ УДАЛОСЬ: инструмента «{argv[0]}» нет ни в одном "
                  "пакете — это неверное имя, а не поломка окружения.",
                  file=sys.stderr)
            print(f"  искал в: {looked}", file=sys.stderr)
        return 1
    print(str(hit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
