#!/usr/bin/env python3
"""SUPERSTACK — границы слоёв держит машина, а не внимательность ревьюера.

Зачем это, если есть код-ревью.

Когда код пишет модель, объём правок растёт быстрее, чем внимание того, кто их
читает. Архитектурное правило «домен не знает про базу» живёт в голове и в
паре абзацев документации — то есть ровно до первого удобного импорта. Никто
не сломает границу нарочно; её сломают по дороге к работающей фиче, и на ревью
это выглядит как обычная строка `import`.

Поэтому граница либо машинный инвариант, либо её нет. Здесь она объявляется
данными и проверяется счётом: какой слой в какой имеет право смотреть.

Два отказа, ради которых всё написано:

  1. ГРАНИЦЫ ОБЪЯВЛЯЕТ ПРОЕКТ, А НЕ ИНСТРУМЕНТ. Угадывать слои по именам
     каталогов — это выдавать своё мнение за архитектуру. Не объявлено —
     «проверять нечего» и код 2, а не «нарушений нет».
  2. НАРУШЕНИЕ НАЗЫВАЕТ АДРЕС. Файл, строку и обе стороны границы. Находка
     без адреса — мнение, а мнение здесь запрещено.

  .superstack/layers.json:
    {"layers": [
      {"name": "домен",      "paths": ["src/domain/"], "may_import": []},
      {"name": "приложение", "paths": ["src/app/"],    "may_import": ["домен"]},
      {"name": "инфра",      "paths": ["src/infra/"],  "may_import": ["домен", "приложение"]}]}

  python3 layer_guard.py <корень проекта> [--json]

  код 0 — границы целы, 1 — есть нарушения, 2 — границы не объявлены,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

СПЕКА = ".superstack/layers.json"

#: Формы импорта, которые встречаются в живых проектах. Регулярка намеренно
#: простая: разбирать чужой синтаксис целиком значит писать компилятор, а
#: пропущенная экзотика ловится ревью — в отличие от границы, которую ревью
#: как раз и не ловит.
ИМПОРТЫ = (
    re.compile(r"""(?m)^\s*import\s+[^'"\n]*from\s+['"]([^'"]+)['"]"""),
    re.compile(r"""(?m)^\s*import\s+['"]([^'"]+)['"]"""),
    re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"(?m)^\s*from\s+([\w.]+)\s+import\s"),
    re.compile(r"(?m)^\s*import\s+([\w.]+)\s*$"),
)

РАСШИРЕНИЯ = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".kt", ".swift")
ПРОПУСК = ("/node_modules/", "/.git/", "/venv/", "/__pycache__/", "/dist/",
           "/build/", "/.next/")


def спека(root: Path) -> tuple:
    """(слои, причина отказа). Не объявлено — «проверять нечего», не «чисто»."""
    p = root / СПЕКА
    if not p.is_file():
        return None, ("границы слоёв не объявлены: нет " + СПЕКА +
                      " — проверять нечего, и это не то же самое, что «нарушений нет»")
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{СПЕКА} не разобран ({e})"
    слои = d.get("layers") if isinstance(d, dict) else None
    if not слои:
        return None, f"в {СПЕКА} ни одного слоя — объявить нечего"
    for сл in слои:
        if not сл.get("name") or not сл.get("paths"):
            return None, f"слой без имени или без путей в {СПЕКА}"
    return слои, ""


def _слой_пути(путь: str, слои: list) -> "str | None":
    """К какому слою относится путь. Совпадение по префиксу, а не по смыслу."""
    имя = путь.replace("\\", "/").lstrip("./")
    лучший, длина = None, -1
    for сл in слои:
        for префикс in сл["paths"]:
            п = префикс.replace("\\", "/").lstrip("./")
            if имя.startswith(п) and len(п) > длина:
                лучший, длина = сл["name"], len(п)
    return лучший


def _цель(импорт: str, файл: str, слои: list) -> "str | None":
    """Куда ведёт импорт: относительный считается от файла, остальной — от корня."""
    if импорт.startswith("."):
        цель = (Path(файл).parent / импорт).as_posix()
        цель = re.sub(r"/\./", "/", цель)
        while "/../" in цель:
            цель = re.sub(r"[^/]+/\.\./", "", цель, count=1)
        return _слой_пути(цель, слои)
    return _слой_пути(импорт.replace(".", "/"), слои)


def violations(root: Path, слои: list) -> list:
    """Импорты, пересекающие объявленную границу. Каждый — с адресом."""
    можно = {сл["name"]: set(сл.get("may_import") or []) for сл in слои}
    найдено = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix not in РАСШИРЕНИЯ:
            continue
        отн = f.relative_to(root).as_posix()
        if any(з in "/" + отн for з in ПРОПУСК):
            continue
        откуда = _слой_пути(отн, слои)
        if откуда is None:
            continue
        try:
            текст = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        строки = текст.splitlines()
        for рег in ИМПОРТЫ:
            for m in рег.finditer(текст):
                куда = _цель(m.group(1), отн, слои)
                if куда is None or куда == откуда or куда in можно.get(откуда, set()):
                    continue
                номер = текст[:m.start()].count("\n") + 1
                найдено.append({
                    "file": отн, "line": номер,
                    "from_layer": откуда, "to_layer": куда,
                    "import": m.group(1),
                    "text": (строки[номер - 1].strip() if номер <= len(строки) else ""),
                    "why": f"«{откуда}» не имеет права смотреть в «{куда}»"})
    return найдено


def run(root: Path) -> dict:
    слои, почему = спека(root)
    if слои is None:
        return {"status": "unknown", "detail": почему,
                "next": "объявить слои и их права в " + СПЕКА}
    нарушения = violations(root, слои)
    if нарушения:
        return {"status": "fail", "violations": нарушения[:40],
                "detail": f"нарушений границы: {len(нарушения)}",
                "next": "граница либо машинный инвариант, либо её нет: "
                        "перенести зависимость или изменить объявленные права"}
    return {"status": "pass", "layers": [сл["name"] for сл in слои],
            "detail": f"границы целы: слоёв {len(слои)}"}


def human(v: dict) -> str:
    голова = {"pass": "ГРАНИЦЫ ЦЕЛЫ", "fail": "ГРАНИЦА НАРУШЕНА",
              "unknown": "ПРОВЕРЯТЬ НЕЧЕГО"}
    строки = [f"{голова[v['status']]}: {v['detail']}"]
    for н in v.get("violations", [])[:15]:
        строки.append(f"  ! {н['file']}:{н['line']}  {н['why']}")
        if н.get("text"):
            строки.append(f"      {н['text']}")
    if v.get("next"):
        строки.append(f"  дальше: {v['next']}")
    return "\n".join(строки)


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
        print("вызов: layer_guard.py <корень проекта> [--json]", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3
    v = run(root)
    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
