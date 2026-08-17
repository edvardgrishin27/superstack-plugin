#!/usr/bin/env python3
"""SUPERSTACK — разбор контракта возврата. «Готово» проверяется, а не читается.

Зачем это код.

Передача таска собирается скриптом и требует контракт возврата дословно. А то,
что вернулось, до сих пор читал человек глазами — то есть последнее звено
цепочки держалось ровно на том, против чего построена вся остальная: на
внимательном прочтении утверждения.

Здесь блок разбирается, и его внутренние противоречия становятся кодом возврата.
Главное из них — единственное, ради чего файл стоит писать:

  СТАТУС «DONE» ПРИ КРАСНЫХ ТЕСТАХ В ТОМ ЖЕ БЛОКЕ.

Исполнитель не врёт: он честно печатает и «сделано», и `npm test → 2 failed`,
потому что первое про его работу, а второе про прогон. Читающий глазами видит
крупное слово DONE и пролистывает строку с числом. Так красное уезжает в коммит
с пометкой «готово», и находится это через восемь тасков.

Что ещё считается нарушением, а не придиркой:

  · блока нет вовсе — таск не закончен, сколько бы кода ни появилось;
  · DONE без единого файла — работа, которой не видно;
  · TESTS без результата прогона — «я не запускал», записанное иначе;
  · блок длиннее 25 строк — исполнитель час работал и хочет признания; восемь
    таких блоков стоят оркестратору ровно того же, что восемь диффов, только
    приезжают через другую дверь.

  python3 contract.py check <файл-с-ответом> [--task 02] [--json]

  код 0 — контракт годен, 1 — нарушен, 2 — блока нет, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DONE, CONCERNS = "DONE", "DONE_WITH_CONCERNS"
BLOCKED, NEEDS_CONTEXT = "BLOCKED", "NEEDS_CONTEXT"

#: Работа не поместилась в один контекст и передаётся дальше.
#:
#: Раньше такого исхода не было вовсе, и это дорого стоило: исполнитель, у
#: которого кончается место, либо торопится и сдаёт недоделанное как DONE, либо
#: возвращает BLOCKED — «не смог», хотя смог наполовину. Оба ответа врут о
#: причине, и оба стоят следующей попытки с нуля.
#:
#: Передавать разрешено ТОЛЬКО зелёное: красный прогон, ушедший в чужой
#: контекст, становится чужой поломкой, которую никто не заказывал.
HANDOFF = "HANDOFF"
STATUSES = (DONE, CONCERNS, BLOCKED, NEEDS_CONTEXT, HANDOFF)

FIELDS = ("STATUS", "FILES", "TESTS", "INTERFACES", "REQUIREMENTS",
          "CONCERNS", "BLOCKERS", "HANDOFF")

MAX_LINES = 25

#: Сколько раз работу можно передать дальше. Третья передача означает не
#: длинную работу, а неверную нарезку: часть, которая не помещается в три
#: контекста, была разрезана неправильно, и следующий контекст потратится так
#: же, как первые два. Это дефект планирования, и он записывается им.
MAX_HANDOFFS = 2

#: Признаки красного прогона в строке TESTS. Ищем ЧИСЛО, а не слово: «упало 0»
#: и «0 failed» не должны считаться провалом, а «2 failed» — обязаны.
_RED = re.compile(r"\b(\d+)\s*(failed|failing|упал\w*|провал\w*)\b"
                  # Код возврата — такой же исход прогона, как «2 failed», и
                  # для сборки, линтера или бюджетного скрипта он единственный
                  # доступный: у них нет числа прошедших тестов. Живой случай:
                  # исполнитель честно вернул «npm run build → exit 0, npm test
                  # → 0 tests, exit 0», а контракт объявил это «я не запускал».
                  # Отказ обиден вдвойне — он наказывает за точный ответ.
                  r"|\b(?:exit|код|code)\s*[:=]?\s*([1-9]\d*)\b", re.I)
_GREEN = re.compile(r"\b(\d+)\s*(passed|passing|прош\w*|ok)\b"
                    r"|\b(?:exit|код|code)\s*[:=]?\s*(0)\b", re.I)
#: Строка TESTS обязана нести исход прогона, а не намерение его запустить.
_RAN = re.compile(r"(→|->|:)\s*\S")


def parse(text: str) -> dict:
    """Вытащить поля контракта. Отсутствующие — просто отсутствуют."""
    got, order = {}, []
    key = None
    for raw in text.splitlines():
        m = re.match(r"^\s*(" + "|".join(FIELDS) + r")\s*:\s*(.*)$", raw)
        if m:
            key = m.group(1)
            got[key] = m.group(2).strip()
            order.append(key)
        elif key and raw.strip() and not raw.startswith("#"):
            got[key] = (got[key] + " " + raw.strip()).strip()
    return {"fields": got, "order": order}


def _lines_of_block(text: str) -> int:
    """Длина самого блока, а не всего ответа: рассуждения до него не в счёт."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines)
                  if re.match(r"^\s*STATUS\s*:", l)), None)
    return 0 if start is None else len([l for l in lines[start:] if l.strip()])


def check(text: str, task: str = None, handoffs: int = 0) -> dict:
    """Разбор блока. `handoffs` — сколько раз эту часть уже передавали.

    Счётчик приходит извне, из состояния прогона: сам блок о прошлых передачах
    не знает, а третья подряд означает не длинную работу, а неверную нарезку.
    """
    p = parse(text)
    f = p["fields"]
    if "STATUS" not in f:
        return {"status": "unknown", "task": task, "contract": f,
                "detail": "блока контракта нет — таск не закончен, сколько бы "
                          "кода ни появилось: нечем обновить состояние и нечего "
                          "передать следующему таску",
                "broken": []}

    broken = []
    st = f["STATUS"].split()[0] if f["STATUS"] else ""
    if st not in STATUSES:
        broken.append(f"неизвестный статус «{f['STATUS']}» — ожидается один из "
                      + ", ".join(STATUSES))

    tests = f.get("TESTS", "")
    red = _RED.search(tests)
    red_n = int(red.group(1)) if red else 0
    ran = bool(_RAN.search(tests)) and bool(_GREEN.search(tests) or red)

    if st in (DONE, CONCERNS):
        if red_n > 0:
            # Ради этой проверки файл и написан.
            broken.append(
                f"статус «{st}» при красных тестах в том же блоке "
                f"({tests.strip()[:80]}) — исполнитель не врёт: «сделано» про "
                "его работу, а число про прогон. Глазами видно слово, а не "
                "число, и красное уезжает в коммит с пометкой «готово»")
        if not tests.strip():
            broken.append("нет строки TESTS — «готово» без прогона это "
                          "утверждение, а не результат")
        elif not ran:
            broken.append(f"TESTS не несёт исхода прогона ({tests.strip()[:60]}) "
                          "— «я не запускал», записанное иначе")
        if not f.get("FILES", "").strip():
            broken.append("DONE без единого файла — работа, которой не видно")

    if st == BLOCKED and not f.get("BLOCKERS", "").strip():
        broken.append("BLOCKED без названного блокера — отказ, по которому "
                      "нельзя ничего предпринять")

    if st == HANDOFF:
        # Передавать можно только зелёное. Красный прогон, ушедший в чужой
        # контекст, становится чужой поломкой: следующий тратит своё место на
        # разбор чужой, не зная даже, чинил ли её кто-то до него.
        if red_n > 0:
            broken.append(
                f"передача работы при красных тестах ({tests.strip()[:60]}) — "
                "следующий получит чужую поломку и потратит свой контекст на "
                "её разбор; передавать можно только зелёное, иначе это BLOCKED")
        if not tests.strip() or not ran:
            broken.append("передача без прогона тестов — принимающий не знает, "
                          "что из сделанного работает, и начнёт с проверки всего")
        # «Что дальше» — половина смысла передачи. Без неё принимающий
        # восстанавливает замысел по коду, то есть платит второй раз за то,
        # что передающий уже знал.
        if not f.get("HANDOFF", "").strip() and not f.get("CONCERNS", "").strip():
            broken.append("передача без строки HANDOFF: не сказано, на чём "
                          "остановился и что делать следующему")
        if handoffs >= MAX_HANDOFFS:
            broken.append(
                f"передача {handoffs + 1}-я при потолке {MAX_HANDOFFS} — часть, "
                "не поместившаяся в три контекста, разрезана неверно: следующий "
                "потратится так же, как первые два. Это дефект нарезки, и чинить "
                "его нужно в плане, а не выдачей ещё одного контекста")

    n = _lines_of_block(text)
    if n > MAX_LINES:
        broken.append(f"блок на {n} строк при потолке {MAX_LINES} — восемь "
                      "таких стоят оркестратору того же, что восемь диффов, "
                      "только приезжают через другую дверь")

    missing = [k for k in ("FILES", "TESTS", "REQUIREMENTS") if k not in f]
    unmeasured = ([f"нет полей: {', '.join(missing)}"] if missing else [])

    return {"status": "fail" if broken else ("unknown" if unmeasured else "pass"),
            "task": task, "contract": f, "block_lines": n,
            "tests_red": red_n, "tests_ran": ran,
            "broken": broken, "unmeasured": unmeasured,
            "detail": (f"{len(broken)} нарушений" if broken
                       else f"контракт годен: {st}")}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    takes = {"--task", "--handoffs"}
    plain, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in takes:
            skip = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) != 2 or plain[0] != "check":
        print("вызов: contract.py check <файл-с-ответом> [--task 02]",
              file=sys.stderr)
        return 3
    p = Path(plain[1])
    if not p.is_file():
        print(f"НЕ УДАЛОСЬ: нет файла {p}", file=sys.stderr)
        return 3
    task = argv[argv.index("--task") + 1] if "--task" in argv else None

    try:
        done = int(argv[argv.index("--handoffs") + 1]) if "--handoffs" in argv else 0
    except (ValueError, IndexError):
        print("НЕ УДАЛОСЬ: --handoffs ждёт число", file=sys.stderr)
        return 3
    v = check(p.read_text("utf-8", errors="replace"), task, done)
    if "--json" not in argv:
        head = {"pass": "КОНТРАКТ ГОДЕН", "fail": "КОНТРАКТ НАРУШЕН",
                "unknown": "КОНТРАКТА НЕТ"}
        print(f"{head[v['status']]}: {v['detail']}", file=sys.stderr)
        for b in v["broken"]:
            print(f"  ! {b}", file=sys.stderr)
        for u in v.get("unmeasured", []):
            print(f"  ? {u}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
