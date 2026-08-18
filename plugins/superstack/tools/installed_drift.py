#!/usr/bin/env python3
"""SUPERSTACK — то ли запускается, что ты правишь.

Зачем это существует.

Права агента приёмки исправили в репозитории: `tools: Read` стало
`tools: Read, Bash, Glob, Grep`. Тест позеленел, мутация начала ловиться,
планка взялась целиком. Через десять минут приёмка запустилась и первой строкой
доложила: «у меня только чтение, `npm test` выполнить нечем».

Правка была в рабочем дереве. Агент берётся из УСТАНОВЛЕННОГО плагина — копии в
кэше, которая обновляется отдельной командой. Всё, что проверяет набор, читает
репозиторий; всё, что исполняется на деле, читает кэш. Между ними может лежать
любая разница, и она не видна ни одному тесту: набор зелёный, потому что
проверяет намерение, а не то, что запустится.

Здесь эта разница считается прямо: файл за файлом, по отпечатку.

Что НЕ делается: обновление. Установку плагинов выполняет человек своей
командой — инструмент только показывает, что и насколько разошлось.

  python3 installed_drift.py <корень репозитория> [--json] [--plugin имя]

  код 0 — установленное совпадает с исходниками, 1 — разошлось,
  2 — установленного нет (сравнивать не с чем), 3 — ошибка вызова
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

CACHE = Path.home() / ".claude" / "plugins" / "cache"

#: Что сравнивается. Только то, что ИСПОЛНЯЕТСЯ: инструменты, скиллы, агенты,
#: хуки и данные. Тесты и служебное в установленный плагин не едут, и их
#: расхождение ничего не означает.
WATCH = ("tools/*.py", "tools/*.sh", "skills/*/SKILL.md", "agents/*.md",
         "hooks/*.sh", "hooks/*.json", "data/*.json", ".claude-plugin/plugin.json")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def newest_installed(name: str) -> "Path | None":
    """Самая свежая установленная версия пакета.

    Версии сравниваются ЧИСЛАМИ, а не строками: иначе «0.2.10» оказывается
    старше «0.2.9», и сверка молча берёт не ту копию.
    """
    root = CACHE / "superstack" / name
    if not root.is_dir():
        return None
    def key(p: Path):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (0,)
    versions = sorted((p for p in root.iterdir() if p.is_dir()), key=key)
    return versions[-1] if versions else None


def compare(repo: Path, name: str) -> dict:
    installed = newest_installed(name)
    if installed is None:
        return {"plugin": name, "status": "unknown", "installed": None,
                "detail": "пакет не установлен — сравнивать не с чем"}

    src = repo / "plugins" / name
    changed, missing = [], []
    for pattern in WATCH:
        for f in sorted(src.glob(pattern)):
            rel = f.relative_to(src)
            there = installed / rel
            if not there.is_file():
                missing.append(str(rel))
            elif _sha(f) != _sha(there):
                changed.append(str(rel))
    return {
        "plugin": name,
        "status": "fail" if (changed or missing) else "pass",
        "installed": installed.name,
        "changed": changed,
        "missing": missing,
        "detail": (f"{len(changed)} файлов правлены после установки, "
                   f"{len(missing)} не установлены вовсе — запускается версия "
                   f"{installed.name}, а не то, что ты правишь"
                   if changed or missing else
                   f"установленная версия {installed.name} совпадает с исходниками"),
    }


def check(repo: Path, only: str = None) -> dict:
    names = ([only] if only else
             sorted(p.name for p in (repo / "plugins").iterdir() if p.is_dir()))
    parts = [compare(repo, n) for n in names]
    bad = [p for p in parts if p["status"] == "fail"]
    none = [p for p in parts if p["status"] == "unknown"]
    return {
        "status": "fail" if bad else ("unknown" if len(none) == len(parts) else "pass"),
        "plugins": parts,
        "detail": (f"разошлось пакетов: {len(bad)} из {len(parts)} — правки не "
                   "доедут до дела, пока плагин не обновлён"
                   if bad else "установленное совпадает с исходниками"),
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
    plain = [a for a in argv if not a.startswith("--")]
    if not plain:
        print("вызов: installed_drift.py <корень репозитория> [--json] [--plugin имя]",
              file=sys.stderr)
        return 3
    repo = Path(plain[0])
    if not (repo / "plugins").is_dir():
        print(f"НЕ УДАЛОСЬ: в {repo} нет каталога plugins", file=sys.stderr)
        return 3
    only = argv[argv.index("--plugin") + 1] if "--plugin" in argv else None

    v = check(repo, only)
    if "--json" in argv:
        print(json.dumps(v, ensure_ascii=False, indent=1))
    else:
        head = {"pass": "ЗАПУСКАЕТСЯ ТО, ЧТО ТЫ ПРАВИШЬ",
                "fail": "ЗАПУСКАЕТСЯ НЕ ТО, ЧТО ТЫ ПРАВИШЬ",
                "unknown": "НЕ УСТАНОВЛЕНО"}[v["status"]]
        print(f"{head}: {v['detail']}", file=sys.stderr)
        for p in v["plugins"]:
            if p["status"] == "fail":
                for f in (p.get("changed") or [])[:6]:
                    print(f"  {p['plugin']} {p['installed']}: {f}", file=sys.stderr)
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
