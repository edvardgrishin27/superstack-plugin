#!/usr/bin/env python3
"""SUPERSTACK — доктор ходит сам, а не когда человек о нём вспомнит.

Зачем расписание, если инструмент уже есть.

Доктор отвечает на вопрос «что из установленного протухло, умерло или стало
лишним». Ответ на него меняется НЕ от работы человека, а от чужих релизов:
репозиторий заархивировали, возможность стала нативной, плагин перестали
трогать. Событие происходит снаружи и в тишине — и потому «позвать доктора»
никогда не оказывается сегодняшней задачей. Механизм, о котором надо вспомнить,
для неразработчика равен отсутствующему.

Поэтому доктор ставится в расписание, а его итог уходит в тот же исходящий
канал, что и остальное. Иначе он будет находить протухшее в терминале, куда
никто не смотрит.

Три правила:

  1. РАСПИСАНИЕ СТАВИТСЯ ТОЛЬКО ПО ЯВНОМУ СОГЛАСИЮ. Это правка машины
     человека, и делать её молча нельзя — та же граница, что у запретов.
  2. ПОСТАВЛЕНО ИЛИ НЕТ — ПРОВЕРЯЕТСЯ ЧТЕНИЕМ, А НЕ ПАМЯТЬЮ. «Вроде
     настраивали» — не состояние системы.
  3. ЗАДАЧА БЕЗ ДОСТАВКИ БЕСПОЛЕЗНА. Сгенерированная задача обязана
     содержать шаг отправки итога: находка, которую никто не прочитал,
     ничем не отличается от ненайденной.

  python3 schedule_doctor.py --check            стоит ли расписание
  python3 schedule_doctor.py --install --yes    поставить

  код 0 — стоит (или поставлено), 1 — не стоит, 2 — прочитать не удалось,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ЗАДАЧИ = Path.home() / ".claude" / "scheduled-tasks"
ИМЯ = "superstack-doctor"
ЗОВЁМ = "doctor.py"
ДОСТАВКА = "notify.py"


def task_body(корень_плагина: str) -> str:
    """Тело задачи. Доктор + доставка итога: находка без читателя бесполезна."""
    return "\n".join([
        "---",
        f"name: {ИМЯ}",
        "description: Еженедельная проверка: что из установленного протухло, "
        "умерло или стало лишним",
        "---",
        "",
        "Проверь актуальность установленного и пришли итог человеку.",
        "",
        "## 1. Осмотр",
        "",
        "```bash",
        f'python3 "{корень_плагина}/tools/{ЗОВЁМ}" --json',
        "```",
        "",
        "## 2. Итог человеку",
        "",
        "Две строки: что протухло и что с этим делать. Без пересказа всего "
        "отчёта — человек читает это с телефона.",
        "",
        "```bash",
        f'python3 "{корень_плагина}/tools/{ДОСТАВКА}" . --text "<две строки>"',
        "```",
        "",
        "Нечего сказать — молчи: пустой еженедельный отчёт учит его "
        "пропускать все следующие.",
        "",
    ])


def installed(корень: Path = ЗАДАЧИ) -> dict:
    """Стоит ли расписание. Читается с диска, а не вспоминается."""
    d = корень / ИМЯ
    файл = d / "SKILL.md"
    if not файл.is_file():
        return {"present": False, "why": f"задачи нет: {d}"}
    try:
        текст = файл.read_text("utf-8", errors="replace")
    except OSError as e:
        return {"present": None, "why": f"прочитать не удалось: {e}"}
    if ЗОВЁМ not in текст:
        return {"present": False, "file": str(файл),
                "why": "задача есть, но доктора в ней нет"}
    if ДОСТАВКА not in текст:
        # Находка, которую никто не прочитал, ничем не отличается от
        # ненайденной.
        return {"present": False, "file": str(файл),
                "why": "задача зовёт доктора, но итог никуда не уходит"}
    return {"present": True, "file": str(файл)}


def install(корень_плагина: str, корень: Path = ЗАДАЧИ) -> dict:
    d = корень / ИМЯ
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(task_body(корень_плагина), encoding="utf-8")
    except OSError as e:
        return {"status": "unknown", "detail": f"записать не удалось: {e}"}
    return {"status": "pass", "detail": f"расписание поставлено: {d}"}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    корень = Path(os.environ.get("CLAUDE_PLUGIN_ROOT")
                  or Path(__file__).resolve().parent.parent)

    if "--install" in argv:
        if "--yes" not in argv:
            # Правка машины человека молча — та же граница, что у запретов.
            print("НЕ УДАЛОСЬ: расписание ставится только с явным --yes:\n"
                  f"  будет создано {ЗАДАЧИ / ИМЯ}", file=sys.stderr)
            return 3
        v = install(str(корень))
        print(v["detail"], file=sys.stderr)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 0 if v["status"] == "pass" else 2

    if "--check" not in argv:
        print("вызов: schedule_doctor.py --check | --install --yes",
              file=sys.stderr)
        return 3

    v = installed()
    if v["present"] is None:
        print(f"ПРОВЕРИТЬ НЕ СМОГ: {v['why']}", file=sys.stderr)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 2
    if v["present"]:
        print(f"РАСПИСАНИЕ СТОИТ: {v['file']}", file=sys.stderr)
    else:
        print(f"РАСПИСАНИЯ НЕТ: {v['why']}", file=sys.stderr)
        print("  дальше: доктор отвечает на вопрос, который меняется от ЧУЖИХ "
              "релизов — вспомнить о нём вовремя нельзя", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 0 if v["present"] else 1


if __name__ == "__main__":
    sys.exit(main())
