#!/usr/bin/env python3
"""SUPERSTACK — числа панели: каждое посчитано из файла, который его записал.

Зачем отдельно от записи состояния.

Это разные занятия. `progress.py` ПИШЕТ ход стройки — и там же живёт правило
«готово без доказательства записать нельзя». Здесь ничего не пишется вовсе:
всё, что панель показывает числами, СЧИТАЕТСЯ по четырём файлам на диске —
ход стройки, требования человека (manifest.json), последняя проверка
(verify-last.json) и снимки (checkpoint.py).

Панель показывает десятки чисел, и у каждого есть ровно один способ стать
неправдой — быть вписанным моделью «по смыслу». Чего в файлах нет, того нет
и на экране: пустое место с подписью честнее правдоподобной цифры.

Ввоз односторонний и это важно. Отсюда не импортируется ни запись состояния,
ни панель: сюда ввозится только стандартная библиотека, а соседний инструмент
подгружается по пути в момент вызова. Поэтому кольца при загрузке нет ни в
одну сторону, в каком бы порядке модули ни грузили.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

#: Состояние части работы. Порядок важен: он же порядок убывания доверия.
#:
#: Словарь объявлен ЗДЕСЬ, а не там, где по нему пишут. Причина простая: этими
#: четырьмя словами тут МЕРЯЮТ — каждое число панели считается по ним, — а
#: запись состояния берёт их отсюда. Объявить их в обоих местах значило бы
#: завести два словаря, которые разойдутся молча, и «доказано» стало бы двумя
#: разными «доказано».
PROVEN = "proven"        # гейт вернул ноль — единственное настоящее «готово»
CLAIMED = "claimed"      # агент сказал «готово», доказательства нет
RUNNING = "running"
WAITING = "waiting"
TASK_STATES = (PROVEN, CLAIMED, RUNNING, WAITING)

#: Наша шкала размера работы — по числу частей, а не по строкам кода и не по
#: часам. Человеку нужен масштаб («это комната, а не дом»), и назвать его
#: можно только тем, что уже посчитано.
РАЗМЕРЫ = ((1, "size_shelf"), (3, "size_room"), (8, "size_flat"))
РАЗМЕР_БОЛЬШЕ = "size_house"

#: Виды требований, у которых левая колонка ведомости — ДОСЛОВНЫЕ слова
#: человека. Остальное вывела система, и это обязано быть помечено: чужая
#: догадка, поданная как просьба человека, — самая тихая подмена в продукте.
СЛОВА_ЧЕЛОВЕКА = ("explicit", "answer")

#: Сколько строк ведомости и событий показывать. Ограничение не косметика:
#: страница на шестьдесят требований перестаёт читаться целиком, и человек
#: видит не больше, а меньше. Сколько строк скрыто — говорится вслух.
СТРОК_ВЕДОМОСТИ = 12
СОБЫТИЙ = 6


def _подгрузить(имя: str, псевдоним: str):
    """Соседний инструмент по пути. Отказ — исключение, а не пустой ответ."""
    import importlib.util
    p = Path(__file__).resolve().parent / имя
    spec = importlib.util.spec_from_file_location(псевдоним, p)
    if spec is None or spec.loader is None:
        raise ImportError(f"не загружается: {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def требования(run_dir: Path) -> list:
    """Требования человека — из файла, который их записывал.

    Читается ФАЙЛ, а не поле состояния: числа покрытия раньше приезжали в
    состояние отдельной командой `req --total N --covered N`, то есть их
    вписывал тот же, кто отчитывался о работе. Пропущенная команда молча
    оставляла вчерашние числа, а опечатка в ней не ловилась ничем.
    """
    try:
        d = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    except (OSError, ValueError):
        return []
    reqs = d.get("requirements")
    return reqs if isinstance(reqs, list) else []


def _покрытие(reqs: list) -> dict:
    """Сколько из просьбы закрыто. Снятое не считается ни туда, ни сюда."""
    def сколько(*статусы):
        return sum(1 for r in reqs if r.get("status") in статусы)
    снято = сколько("dropped")
    return {"total": len(reqs), "done": сколько("done"),
            "live": len(reqs) - снято, "dropped": снято,
            "deferred": сколько("deferred")}


def _размер(частей: int) -> "str | None":
    if частей <= 0:
        return None
    for предел, ключ in РАЗМЕРЫ:
        if частей <= предел:
            return ключ
    return РАЗМЕР_БОЛЬШЕ


def проверка(run_dir: Path) -> "dict | None":
    """Последняя проверка: код возврата и, если его считали, число тестов.

    Число тестов показывается ТОЛЬКО когда оно записано. Посчитать его «по
    смыслу» здесь ничего не стоит, и именно поэтому нельзя: цифра рядом с
    кодом возврата читается как измеренная.
    """
    try:
        d = json.loads((run_dir / "verify-last.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    code = d.get("exit_code") if isinstance(d, dict) else None
    if not isinstance(code, int):
        return None
    tests = d.get("tests")
    return {"exit_code": code,
            "tests": tests if isinstance(tests, int) else None,
            "at": d.get("updated") or d.get("at") or None}


def снимки(root: Path) -> list:
    """Снимки прогона — у того, кто их делает, а не по своей памяти о них.

    Отказ не молчит: панель без снимков и панель, которой их не отдали, — два
    разных утверждения, и второе обязано быть слышно тому, кто её собирает.
    """
    try:
        mod = _подгрузить("checkpoint.py", "ss_checkpoint_facts")
    except (OSError, ValueError, ImportError) as e:
        print(f"НЕ СМОГ прочитать снимки (checkpoint.py: {e}) — на панели их "
              "не будет", file=sys.stderr)
        return []
    try:
        return mod.список(root).get("снимки") or []
    except (OSError, ValueError, AttributeError, mod.НетGit) as e:
        print(f"НЕ СМОГ прочитать снимки ({e}) — на панели их не будет",
              file=sys.stderr)
        return []


def _снимок_части(снимки_: list, имя: str) -> str:
    """Снимок, подписанный этой частью работы. Нет — пусто, а не «наверное»."""
    for с in снимки_:
        if имя and имя in (с.get("этап") or ""):
            return с.get("sha") or ""
    return ""


def _подтверждение(rid: str, tasks: list, статус: str, снимки_: list) -> dict:
    """Правая колонка ведомости: чем машина подтвердила это требование.

    Подтверждает только код возврата на части работы. Статус `done` в
    требованиях ставит агент — это его слово, и оно попадает в «со слов», а не
    в «доказано»: иначе ведомость двойной записи схлопывается в одну колонку,
    где обещание подтверждает само себя.
    """
    свои = [t for t in tasks if rid in (t.get("requirements") or [])]
    доказанные = [t for t in свои if t.get("status") == PROVEN]
    if доказанные:
        п = max(доказанные, key=lambda t: t.get("finished") or "")
        return {"state": PROVEN, "code": п.get("exit_code"),
                "at": п.get("finished"),
                "sha": _снимок_части(снимки_, п.get("name") or "")}
    if статус in ("dropped", "deferred"):
        return {"state": статус}
    if any(t.get("status") == RUNNING for t in свои):
        return {"state": RUNNING}
    if any(t.get("status") == CLAIMED for t in свои) or статус == "done":
        return {"state": CLAIMED}
    return {"state": WAITING}


def ведомость(reqs: list, tasks: list, снимки_: list) -> list:
    """Строки двойной записи: слева обещание, справа подтверждение."""
    строки = []
    for r in reqs:
        вывод = r.get("kind") not in СЛОВА_ЧЕЛОВЕКА
        строки.append({
            "id": r.get("id") or "",
            "quote": r.get("quote") or "",
            "basis": r.get("basis") or "",
            "inferred": вывод,
            "parent": r.get("parent") or "",
            **_подтверждение(r.get("id") or "", tasks, r.get("status") or "",
                             снимки_),
        })
    return строки


def _события(tasks: list, снимки_: list) -> list:
    """Лента «что произошло» — только из того, что уже записано с временем."""
    лента = []
    for t in tasks:
        имя = t.get("name") or t.get("id") or ""
        статус = t.get("status")
        if статус == PROVEN and t.get("finished"):
            лента.append({"at": t["finished"], "key": "proven", "name": имя,
                          "code": t.get("exit_code")})
        elif статус == CLAIMED and t.get("finished"):
            лента.append({"at": t["finished"], "key": "claimed", "name": имя})
        elif статус == RUNNING and t.get("started"):
            лента.append({"at": t["started"], "key": "started", "name": имя})
    for с in снимки_:
        лента.append({"at": с.get("когда"), "key": "snapshot",
                      "name": с.get("этап") or "", "sha": с.get("sha") or ""})
    лента = [e for e in лента if e.get("at")]
    лента.sort(key=lambda e: e["at"], reverse=True)
    return лента[:СОБЫТИЙ]


def панель_факты(data: dict, run_dir: Path, phases: tuple) -> dict:
    """Всё, что панель показывает числами, — посчитанное здесь и из файлов.

    Порядок этапов приходит аргументом, а не заводится вторым списком рядом:
    рисует их панель, и два списка разошлись бы молча — на экране осталось бы
    «этап 7 из 10» при девяти этапах.
    """
    tasks = [t for w in (data.get("waves") or {}).values() for t in w]
    сколько = {s: sum(1 for t in tasks if t.get("status") == s)
               for s in TASK_STATES}
    всего = len(tasks)
    # Доля — ТОЛЬКО от доказанного. Заявленное идёт своей линейкой и своим
    # числом; складывать их нельзя, и поэтому они здесь даже не рядом.
    доказано = сколько[PROVEN]
    названо = доказано + сколько[CLAIMED]
    reqs = требования(run_dir)
    снимки_ = снимки(run_dir.parent)
    строки = ведомость(reqs, tasks, снимки_)
    итого = {"proven": 0, "claimed": 0, "running": 0, "waiting": 0,
             "dropped": 0, "deferred": 0}
    for с in строки:
        итого[с["state"]] = итого.get(с["state"], 0) + 1
    имя_этапа = ((data.get("phase") or {}).get("name")) or ""
    пройдено = phases.index(имя_этапа) if имя_этапа in phases else None
    начали = sorted(t["started"] for t in tasks if t.get("started"))
    долг = data.get("debt") or {}
    return {
        "parts": {
            "proven": доказано, "claimed": сколько[CLAIMED],
            "running": сколько[RUNNING], "waiting": сколько[WAITING],
            "total": всего,
            "percent_proven": round(100 * доказано / всего) if всего else None,
            "percent_said": round(100 * названо / всего) if всего else None,
            "said": названо, "gap": сколько[CLAIMED],
            "size": _размер(всего),
        },
        "stages": {"done": пройдено, "now": None if пройдено is None
                   else пройдено + 1, "total": len(phases)},
        "requirements": _покрытие(reqs),
        "ledger": строки[:СТРОК_ВЕДОМОСТИ],
        "ledger_shown": min(len(строки), СТРОК_ВЕДОМОСТИ),
        "ledger_total": len(строки),
        "totals": итого,
        "verify": проверка(run_dir),
        "checkpoints": {"count": len(снимки_),
                        "last": снимки_[0] if снимки_ else None},
        "events": _события(tasks, снимки_),
        "started": начали[0] if начали else None,
        "caveats": sum(len(v) for v in долг.values()),
    }
