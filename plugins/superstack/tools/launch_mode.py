#!/usr/bin/env python3
"""SUPERSTACK — чем запускать работу: маршрутизация, а не команда, которую помнят.

Зачем это считает машина.

Ступеней автономии четыре, и они не взаимозаменяемы: `/goal` — это УСЛОВИЕ
(«работай, пока не станет так»), `/loop` — это ЧАСТОТА («заглядывай раз в N»),
задачи по расписанию нужны там, где требуется доступ к локальным файлам, а
Routines — когда ноутбук закрыт. Путают их постоянно, потому что все четыре
про «повторяй».

Человек не обязан этого знать. Он описал задачу — способ запуска выводится из
её формы. Команда, которую надо вспомнить, для неразработчика равна отсутствию
возможности.

Три правила, ради которых всё написано:

  1. У КАЖДОЙ СТУПЕНИ НАЗВАН ПОТОЛОК. Не достоинства, а то, о чём молчат:
     `/goal` судит стенограмму и не вызывает инструменты; `/loop` умирает
     вместе с сессией; расписание требует живого компьютера; Routines не
     видит локальных файлов. Рекомендация без потолка — реклама.
  2. `/goal` БЕЗ ДЕТЕРМИНИРОВАННОГО ГЕЙТА — НЕ АВТОНОМИЯ, А НАДЕЖДА. Если
     Stop-гейт не подключён, эта ступень не предлагается вовсе.
  3. НЕТ СИГНАЛОВ — НЕТ РЕКОМЕНДАЦИИ. Пустой проект получает «не знаю» и код
     2, а не самый популярный ответ. Угаданная маршрутизация хуже отсутствующей:
     её выполняют.

  python3 launch_mode.py <корень проекта> [--json]

  код 0 — способ выбран, 2 — выбрать не из чего, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

СОСТОЯНИЕ = ".superstack/state.json"
ПЛАНКА = ".superstack/bar.json"

#: Четыре ступени и то, о чём молчат. Потолок — обязательное поле: ступень без
#: названного потолка превращает выбор в рекламу.
СТУПЕНИ = {
    "plan": {
        "что": "обычный ход с подтверждениями",
        "когда": "работа короткая либо человек рядом и хочет видеть шаги",
        "потолок": "требует человека на каждом решении — автономии здесь нет",
    },
    "goal": {
        "что": "/goal <условие> — работай, пока не станет так",
        "когда": "у готовности есть машинно-проверяемое условие",
        "потолок": "оценщик судит по стенограмме и НЕ вызывает инструменты; "
                   "без детерминированного гейта это надежда, а не автономия",
    },
    "loop": {
        "что": "/loop — заглядывать раз в интервал",
        "когда": "ждём внешнее событие: прогон CI, ответ, чужую правку",
        "потолок": "умирает вместе с сессией, потолок семь дней",
    },
    "schedule": {
        "что": "задача по расписанию",
        "когда": "нужен доступ к локальным файлам и повтор изо дня в день",
        "потолок": "компьютер должен быть жив в момент запуска",
    },
}


def сигналы(root: Path) -> dict:
    """Что известно о форме задачи. Ничего не выдумывается: чего нет — то None."""
    s: dict = {"tasks": None, "has_bar": None, "stop_gate": None,
               "waits_external": None}
    p = root / СОСТОЯНИЕ
    if p.is_file():
        try:
            d = json.loads(p.read_text("utf-8"))
            задачи = d.get("tasks") or []
            s["tasks"] = len(задачи) if isinstance(задачи, list) else None
            s["waits_external"] = bool(d.get("waiting_on_external"))
        except (OSError, ValueError):
            pass
    s["has_bar"] = (root / ПЛАНКА).is_file()
    настройки = Path.home() / ".claude" / "settings.json"
    if настройки.is_file():
        try:
            d = json.loads(настройки.read_text("utf-8"))
            s["stop_gate"] = bool((d.get("hooks") or {}).get("Stop"))
        except (OSError, ValueError):
            s["stop_gate"] = None
    return s


def choose(s: dict) -> dict:
    """Ступень по форме задачи. Каждый выбор объяснён сигналом, а не вкусом."""
    if s.get("waits_external"):
        return {"mode": "loop", "why": "работа ждёт внешнего события — здесь нужна "
                                       "частота, а не условие"}
    if s.get("has_bar") and s.get("stop_gate"):
        return {"mode": "goal", "why": "у готовности есть машинная черта (планка) "
                                       "и подпёртый гейт — условие проверяемо"}
    if s.get("has_bar") and s.get("stop_gate") is False:
        return {"mode": "plan",
                "why": "планка есть, но Stop-гейт не подключён: /goal без "
                       "детерминированного гейта — надежда, а не автономия"}
    задач = s.get("tasks")
    if задач is not None and задач > 0:
        return {"mode": "plan", "why": f"работа разложена на {задач} задач, "
                                       "черты готовности пока нет"}
    return {}


def run(root: Path) -> dict:
    s = сигналы(root)
    выбор = choose(s)
    if not выбор:
        # Угаданная маршрутизация хуже отсутствующей: её выполняют.
        return {"status": "unknown", "signals": s,
                "detail": "выбрать не из чего: ни задач, ни планки, ни сигнала "
                          "об ожидании",
                "next": "разложить работу или завести планку — способ запуска "
                        "выводится из формы задачи, а не из привычки"}
    ст = СТУПЕНИ[выбор["mode"]]
    return {"status": "pass", "mode": выбор["mode"], "signals": s,
            "detail": f"{ст['что']} — {выбор['why']}",
            "ceiling": ст["потолок"],
            "alternatives": {к: v["когда"] for к, v in СТУПЕНИ.items()
                             if к != выбор["mode"]}}


def human(v: dict) -> str:
    if v["status"] != "pass":
        строки = [f"ЧЕМ ЗАПУСКАТЬ — НЕ ЗНАЮ: {v['detail']}"]
        if v.get("next"):
            строки.append(f"  дальше: {v['next']}")
        return "\n".join(строки)
    return "\n".join([
        f"ЗАПУСКАТЬ ТАК: {v['detail']}",
        f"  о чём молчат: {v['ceiling']}",
    ])


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
        print("вызов: launch_mode.py <корень проекта> [--json]", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3
    v = run(root)
    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 0 if v["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
