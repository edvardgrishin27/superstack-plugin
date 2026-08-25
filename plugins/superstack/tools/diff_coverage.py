#!/usr/bin/env python3
"""SUPERSTACK — покрытие по ДИФФУ: непокрытой становится видна новая строка.

Зачем это отдельно от общего процента.

Общее покрытие проекта — число, которое почти невозможно уронить. Добавь сто
строк без единого теста в репозиторий на двадцать тысяч — процент шевельнётся
на десятые доли, порог в CI останется зелёным, и ровно новый код уедет в прод
непроверенным. Чем больше проект, тем надёжнее он прячет свежую дыру.

Поэтому считается другое: из строк, которые ИЗМЕНИЛИСЬ, сколько покрыто. Здесь
большой зелёный проект не помогает, а маленький не мешает.

Три отказа, ради которых всё написано:

  1. НЕТ ОТЧЁТА — НЕ ЗЕЛЁНОЕ. Отсутствие покрытия даёт код 2 и слова «измерить
     нечем», а не «покрыто». Иначе проект без замера проходит гейт лучше
     проекта с замером — тот же стимул, что убивает тесты на второй неделе.
  2. ОБЩИЙ ПРОЦЕНТ НЕ ЗАСЧИТЫВАЕТСЯ ЗА ПОКРЫТИЕ ПРАВКИ. Это разные величины,
     и подменять первой вторую — самый частый способ показать зелёное.
  3. НОВЫЙ ФАЙЛ — ТОЖЕ ИЗМЕНЕНИЕ. `git diff` не показывает неотслеживаемое;
     проверка, смотрящая только на него, молчит именно там, ради чего написана.

  .superstack/diff-coverage.json (необязателен):
    {"report": "coverage.xml", "threshold": 80}

  python3 diff_coverage.py <корень> [--base <ветка>] [--threshold N] [--json]

  код 0 — порог взят либо покрывать нечего, 1 — ниже порога,
  2 — измерить нечем, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

СПЕКА = ".superstack/diff-coverage.json"
ПОРОГ = 80

#: Где обычно лежит отчёт. Порядок — по распространённости, а не по вкусу.
ОТЧЁТЫ = ("coverage.xml", "coverage/coverage.xml", "coverage.json",
          "coverage/lcov.info", "lcov.info", "coverage/coverage-final.json")

#: Файлы, покрытие которых не спрашивают: тесты проверяют код, а не себя.
НЕ_СЧИТАЕМ = ("test_", "_test.", "/tests/", "/test/", ".spec.", ".test.",
              "/migrations/", "/node_modules/")


def _sh(cmd: list, root: Path) -> tuple:
    try:
        p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                           timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if p.returncode != 0:
        return None, (p.stderr or "").strip()[-200:]
    return p.stdout, ""


def changed_lines(root: Path, base: str) -> tuple:
    """{файл: {номера изменённых строк}}. Новый файл — тоже изменение.

    Общий процент по проекту сюда не заглядывает вовсе: считаются ровно те
    строки, которые тронули, — иначе большой зелёный проект спрячет свежую
    непокрытую строку.
    """
    итог: dict = {}
    вывод, отказ = _sh(["git", "-c", "core.quotepath=false", "diff", "-U0",
                        base, "--"], root)
    if вывод is None:
        return None, f"git diff не отработал: {отказ}"
    файл = None
    for строка in вывод.splitlines():
        if строка.startswith("+++ b/"):
            файл = строка[6:].strip()
        elif строка.startswith("@@") and файл:
            m = re.search(r"\+(\d+)(?:,(\d+))?", строка)
            if m:
                нач, сколько = int(m.group(1)), int(m.group(2) or 1)
                итог.setdefault(файл, set()).update(range(нач, нач + сколько))

    новые, отказ = _sh(["git", "-c", "core.quotepath=false", "ls-files", "-o",
                        "--exclude-standard"], root)
    if новые is None:
        return None, f"git ls-files не отработал: {отказ}"
    for имя in (s for s in новые.splitlines() if s.strip()):
        p = root / имя
        try:
            n = len(p.read_text("utf-8", errors="replace").splitlines())
        except OSError:
            continue
        if n:
            итог.setdefault(имя, set()).update(range(1, n + 1))
    return итог, ""


def _covered_from_xml(текст: str) -> dict:
    """Cobertura/coverage.py XML: {файл: {строка: покрыта}}."""
    из_файла: dict = {}
    корень = ET.fromstring(текст)
    for cls in корень.iter("class"):
        имя = cls.get("filename") or ""
        строки = из_файла.setdefault(имя, {})
        for l in cls.iter("line"):
            try:
                строки[int(l.get("number"))] = int(l.get("hits", "0")) > 0
            except (TypeError, ValueError):
                continue
    return из_файла


def _covered_from_lcov(текст: str) -> dict:
    из_файла: dict = {}
    текущий = None
    for строка in текст.splitlines():
        if строка.startswith("SF:"):
            текущий = строка[3:].strip()
            из_файла.setdefault(текущий, {})
        elif строка.startswith("DA:") and текущий:
            try:
                n, hits = строка[3:].split(",")[:2]
                из_файла[текущий][int(n)] = int(hits) > 0
            except ValueError:
                continue
    return из_файла


def _covered_from_json(текст: str) -> dict:
    d = json.loads(текст)
    из_файла: dict = {}
    files = d.get("files") if isinstance(d, dict) else None
    if isinstance(files, dict):                       # coverage.py --format=json
        for имя, тело in files.items():
            строки = {n: True for n in (тело.get("executed_lines") or [])}
            строки.update({n: False for n in (тело.get("missing_lines") or [])})
            из_файла[имя] = строки
        return из_файла
    if isinstance(d, dict):                           # istanbul coverage-final
        for имя, тело in d.items():
            карта = (тело or {}).get("statementMap") or {}
            счёт = (тело or {}).get("s") or {}
            строки: dict = {}
            for ключ, место in карта.items():
                n = ((место or {}).get("start") or {}).get("line")
                if n:
                    строки[int(n)] = bool(счёт.get(ключ, 0))
            из_файла[имя] = строки
    return из_файла


def покрытие(p: Path) -> tuple:
    """{файл: {строка: покрыта}} либо причина, почему измерить нечем."""
    try:
        текст = p.read_text("utf-8", errors="replace")
    except OSError as e:
        return None, f"отчёт не прочитан: {e}"
    try:
        if p.suffix.lower() == ".xml":
            return _covered_from_xml(текст), ""
        if p.name.endswith(".info") or текст.lstrip().startswith(("TN:", "SF:")):
            return _covered_from_lcov(текст), ""
        return _covered_from_json(текст), ""
    except Exception as e:                             # noqa: BLE001
        return None, f"отчёт не разобран ({type(e).__name__}: {e})"


def _норма(имя: str) -> str:
    return имя.replace("\\", "/").lstrip("./")


def uncovered_changed(правки: dict, отчёт: dict) -> tuple:
    """(непокрытые, всего измеримых). Считаются ТОЛЬКО строки из диффа."""
    по_хвосту = {}
    for имя, строки in отчёт.items():
        по_хвосту[_норма(имя)] = строки

    непокрытые, всего = [], 0
    for файл, номера in правки.items():
        имя = _норма(файл)
        if any(з in "/" + имя for з in НЕ_СЧИТАЕМ):
            continue
        строки = по_хвосту.get(имя)
        if строки is None:
            подходят = [v for k, v in по_хвосту.items()
                        if k.endswith(имя) or имя.endswith(k)]
            строки = подходят[0] if len(подходят) == 1 else None
        if строки is None:
            continue
        for n in sorted(номера):
            if n in строки:
                всего += 1
                if not строки[n]:
                    непокрытые.append(f"{имя}:{n}")
    return непокрытые, всего


def run(root: Path, base: str, порог: int, отчёт_путь: "Path | None") -> dict:
    правки, отказ = changed_lines(root, base)
    if правки is None:
        return {"status": "unknown", "detail": отказ}
    if not правки:
        return {"status": "pass", "detail": "изменённых строк нет"}

    if отчёт_путь is None:
        for имя in ОТЧЁТЫ:
            if (root / имя).is_file():
                отчёт_путь = root / имя
                break
    if отчёт_путь is None or not Path(отчёт_путь).is_file():
        return {"status": "unknown",
                "detail": "измерить нечем: отчёта о покрытии нет "
                          f"({', '.join(ОТЧЁТЫ[:3])}…)",
                "next": "прогнать тесты с покрытием и указать отчёт в " + СПЕКА}

    отчёт, почему = покрытие(Path(отчёт_путь))
    if отчёт is None:
        return {"status": "unknown", "detail": "измерить нечем: " + почему}

    непокрытые, всего = uncovered_changed(правки, отчёт)
    if not всего:
        return {"status": "unknown",
                "detail": "измерить нечем: ни одна изменённая строка не попала "
                          "в отчёт — отчёт старше правок или про другой код",
                "next": "перепрогнать тесты с покрытием после правок"}

    процент = round((всего - len(непокрытые)) / всего * 100)
    итог = {"changed_measurable": всего, "uncovered": непокрытые[:40],
            "percent": процент, "threshold": порог,
            "report": str(отчёт_путь)}
    if процент < порог:
        return {"status": "fail", **итог,
                "detail": f"покрытие правки {процент}% при пороге {порог}%: "
                          f"непокрытых строк {len(непокрытые)}",
                "next": "тест на изменённые строки; общий процент по проекту "
                        "этого не заменяет"}
    return {"status": "pass", **итог,
            "detail": f"покрытие правки {процент}% при пороге {порог}%"}


def human(v: dict) -> str:
    голова = {"pass": "ПРАВКА ПОКРЫТА", "fail": "ПРАВКА НЕ ПОКРЫТА",
              "unknown": "ИЗМЕРИТЬ НЕ СМОГ"}
    строки = [f"{голова[v['status']]}: {v['detail']}"]
    for s in v.get("uncovered", [])[:15]:
        строки.append(f"  ! без теста: {s}")
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
    берут = {"--base", "--threshold", "--report"}
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
        print("вызов: diff_coverage.py <корень> [--base X] [--threshold N]",
              file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    спека = {}
    п = root / СПЕКА
    if п.is_file():
        try:
            спека = json.loads(п.read_text("utf-8")) or {}
        except ValueError as e:
            print(f"НЕ УДАЛОСЬ: {СПЕКА} не разобран: {e}", file=sys.stderr)
            return 3

    base = argv[argv.index("--base") + 1] if "--base" in argv else "HEAD"
    порог = int(argv[argv.index("--threshold") + 1]) if "--threshold" in argv \
        else int(спека.get("threshold", ПОРОГ))
    отчёт = None
    if "--report" in argv:
        отчёт = Path(argv[argv.index("--report") + 1])
    elif спека.get("report"):
        отчёт = root / спека["report"]

    v = run(root, base, порог, отчёт)
    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
