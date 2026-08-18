#!/usr/bin/env python3
"""SUPERSTACK — финальный отчёт прогона. Из файлов, а не по памяти.

Зачем это код.

К последней фазе контекст ведущего самый загрязнённый за прогон: в нём осели
восемь возвратов, три ревью и десяток починок. Отчёт, написанный по памяти,
получается пересказом впечатлений — и первым из него выпадает то, что человеку
нужнее всего: чего НЕ сделали.

Здесь отчёт собирается из того, что лежит на диске: состояние частей, манифест
требований, вердикт слепой приёмки, находки ревью. Ничего не додумывается: чего
в файлах нет, того нет и в отчёте.

Три правила, ради которых стоило писать инструмент:

  · ГОТОВО — только доказанное кодом возврата. «Со слов помощника» идёт
    отдельной строкой и другим словом.
  · ЧТО ЖДЁТ ЧЕЛОВЕКА — раньше похвал. Заглушки, пустые настройки и открытые
    вопросы приёмки собраны в один список: это единственное, что он должен
    сделать сам.
  · ЧЕГО НЕ ПРОВЕРИЛИ — отдельным разделом. «Не нашли» и «не смогли проверить»
    разные утверждения, и второе нельзя прятать в первое.

  python3 report.py <каталог .superstack> [--html <файл>]

  код 0 — отчёт собран, 2 — прогона нет, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROVEN, CLAIMED = "proven", "claimed"


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return default if default is not None else {}


def gather(run: Path) -> dict:
    state = _load(run / "state.json")
    manifest = _load(run / "manifest.json")
    tasks = [t for w in (state.get("waves") or {}).values() for t in w]
    reqs = manifest.get("requirements") or []
    blind = manifest.get("blind") or {}

    findings = []
    for f in sorted(run.glob("review-*.json")):
        findings.extend(_load(f).get("findings") or [])

    return {
        "проверено": [t for t in tasks if t.get("status") == PROVEN],
        "со_слов": [t for t in tasks if t.get("status") == CLAIMED],
        "не_закрыто": [t for t in tasks
                       if t.get("status") not in (PROVEN, CLAIMED)],
        "требования": reqs,
        "приёмка": blind,
        "находки": findings,
        "долг": state.get("debt") or {},
        "этап": (state.get("phase") or {}).get("name", ""),
    }


def human(d: dict) -> str:
    """Отчёт словами, которые понимает человек без опыта в коде."""
    out = []
    total = len(d["проверено"]) + len(d["со_слов"]) + len(d["не_закрыто"])
    out.append("ЧТО ГОТОВО")
    if d["проверено"]:
        for t in d["проверено"]:
            out.append(f"  ✓ {t['name']} — проверено машиной")
    else:
        out.append("  ничего не доказано проверкой")
    if d["со_слов"]:
        out.append("")
        out.append("СДЕЛАНО СО СЛОВ ПОМОЩНИКА — проверка не подтверждала")
        for t in d["со_слов"]:
            out.append(f"  · {t['name']}")

    ask = []
    for r in d["требования"]:
        if r.get("status") in ("placeholder", "open"):
            ask.append(r.get("basis") or r.get("quote") or r.get("id"))
    for kind, items in (d["долг"] or {}).items():
        ask.extend(items or [])
    for q in (d["приёмка"].get("undisclosed") or []):
        ask.append(q)
    if ask:
        out.append("")
        out.append("ЧТО ЖДЁТ ТЕБЯ")
        for a in ask:
            out.append(f"  → {a}")

    checked = d["приёмка"].get("checked") or []
    if checked:
        out.append("")
        out.append("НЕЗАВИСИМАЯ ПРИЁМКА — сверяла с твоей первой просьбой")
        for c in checked:
            out.append(f"  {c.get('verdict', '?')}: {c.get('where', '')}")

    # Закрытые находки в отчёт не идут: список, где половина строк уже
    # неверна, теряет доверие целиком — проверено на первом же живом отчёте,
    # где из одиннадцати строк открытыми были шесть.
    blocking = [f for f in d["находки"]
                if f.get("blocking") and not f.get("closed")]
    fixed = [f for f in d["находки"] if f.get("closed")]
    if blocking:
        out.append("")
        out.append("НАЙДЕНО И НЕ ЗАКРЫТО")
        for f in blocking:
            out.append(f"  ! {f.get('what', '')}")

    if fixed:
        out.append("")
        out.append("НАЙДЕНО И ПОЧИНЕНО")
        for f in fixed:
            out.append(f"  ✓ {f.get('what', '')[:90]}")
            out.append(f"      чем: {f.get('closed', '')[:100]}")

    out.append("")
    out.append(f"Частей работы: {total} · проверено машиной: {len(d['проверено'])}")
    return "\n".join(out)


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
        print("вызов: report.py <каталог .superstack> [--json]", file=sys.stderr)
        return 3
    run = Path(plain[0])
    if not (run / "state.json").is_file():
        print(f"НЕ УДАЛОСЬ: в {run} нет состояния прогона", file=sys.stderr)
        return 2
    d = gather(run)
    if "--json" in argv:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(human(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
