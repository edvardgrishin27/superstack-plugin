#!/usr/bin/env python3
"""SUPERSTACK — состязательный проход: где разваливается сама задача.

Зачем он, если есть гейты.

Все проверки системы спрашивают одно: сделали ли то, о чём просили. Задача при
этом может быть полной, непротиворечивой и понятной — и описывать вещь, которая
работать не будет. Ни один гейт ниже по течению этого не заметит: каждый из них
сверяет результат с просьбой, а сомнительна сама просьба.

Семь вопросов, и все они заданы ЗАДАЧЕ, а не человеку. Ответы находит агент;
человеку уходит только то, что решить может он один.

  1. ПРОВАЛ         сдано, работает, никто не пользуется. Что произошло?
  2. СТОЛКНОВЕНИЕ   какие два требования не могут быть верны одновременно?
  3. НЕПРОВЕРЕННОЕ  что человек считает решённым, а оно не решено?
  4. ЦЕНА           что съест половину сборки ради малой доли ценности?
  5. УСЛОВИЕ        чего в задаче нет, но без чего результат бессмыслен?
  6. ВТОРАЯ НЕДЕЛЯ  что случится, когда это перестанет быть новым?
  7. ВТОРОЙ АКТОР   кто ещё будет этим пользоваться, кроме описанного?

Что здесь принуждается кодом:

  · ПРОХОД НЕ ИМЕЕТ ПРАВА НИЧЕГО ВЫЧЕРКНУТЬ. Он порождает вопросы, добавки,
    строки «вне рамок» и допущения — и никогда снятое требование. Требование,
    которое проход считает плохой идеей, остаётся требованием человека: самое
    большее, что оно заработало, — один вопрос с названной ценой.

  · МЕНЬШЕ ТРЁХ НАХОДОК — ПРОХОД СДЕЛАН ДЛЯ ГАЛОЧКИ. Не потому, что задача
    хороша, а потому, что так выглядит непроведённая работа. Пять-семь на
    реальном проекте — обычный урожай.

  · НАХОДКА — ПРО ЗАДАЧУ, А НЕ ПРО ЧЕЛОВЕКА. «Заявки идут круглосуточно, а
    мастер один» — работа. «Ты уверен, что это кому-то нужно» — мнение,
    которое ничего не покупает и портит весь проход.

  python3 premortem.py add <файл> --q провал --what "..." --then "..."
  python3 premortem.py show <файл> [--mode full|semi|interview]

  код 0 — проход состоялся, 1 — нарушен, 2 — не проведён, 3 — вызов
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

QUESTIONS = {
    "провал": "сдано, работает, никто не пользуется — что произошло",
    "столкновение": "какие два требования не могут быть верны одновременно",
    "непроверенное": "что считается решённым, а не решено",
    "цена": "что съест половину сборки ради малой доли ценности",
    "условие": "чего в задаче нет, но без чего результат бессмыслен",
    "вторая-неделя": "что случится, когда это перестанет быть новым",
    "второй-актор": "кто ещё будет этим пользоваться, кроме описанного",
}

#: Во что находка ПРАВА превратиться. Снятого требования в списке нет и быть
#: не может: проход исследует задачу, а распоряжается ею человек.
ASK, ADDITION, OUT_OF_SCOPE, ASSUMPTION = "вопрос", "добавка", "вне-рамок", "допущение"
OUTCOMES = (ASK, ADDITION, OUT_OF_SCOPE, ASSUMPTION)

#: Ниже этого числа проход выглядит проведённым и таковым не является.
MIN_FINDINGS = 3

#: Признаки суждения о человеке вместо разбора задачи. Ловится грубо и
#: намеренно: точность здесь дешевле пропуска, потому что один такой вопрос
#: обесценивает весь проход в глазах человека.
_ABOUT_PERSON = re.compile(
    r"\bты\s+(уверен|точно|правда)\b|\bкому[- ]то\s+нужн|\bзачем\s+тебе\b"
    r"|\bстоит\s+ли\s+вообще\b", re.I)

EMPTY = {"schema": "superstack.premortem.v1", "ran": False,
         "findings": [], "updated": None}


def load(path: Path) -> dict:
    if not path.is_file():
        return json.loads(json.dumps(EMPTY))
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(EMPTY))
    for k, v in EMPTY.items():
        d.setdefault(k, json.loads(json.dumps(v)))
    return d


def save(path: Path, data: dict, now: str = None) -> None:
    from datetime import datetime, timezone
    data["updated"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def add(data: dict, q: str, what: str, then: str, outcome: str = ASK) -> dict:
    """Записать находку. Снять требование ею нельзя ни при каких условиях."""
    if q not in QUESTIONS:
        raise ValueError(f"неизвестный вопрос: {q} — есть: "
                         + ", ".join(QUESTIONS))
    if outcome not in OUTCOMES:
        raise ValueError(
            f"находка не может стать «{outcome}»: проход исследует задачу, а "
            "распоряжается ею человек — снятое требование не входит в список "
            f"допустимых исходов ({', '.join(OUTCOMES)})")
    if not what.strip() or not then.strip():
        raise ValueError("находка обязана назвать И что не сходится, И что с "
                         "этим делать — половина не даёт ни вопроса, ни решения")
    if _ABOUT_PERSON.search(what) or _ABOUT_PERSON.search(then):
        raise ValueError(
            "это суждение о человеке, а не разбор задачи: такой вопрос ничего "
            "не покупает и обесценивает весь проход. Назови, что именно в "
            "задаче не сходится")
    data["ran"] = True
    data["findings"].append({"q": q, "what": what.strip(), "then": then.strip(),
                             "outcome": outcome})
    return data


def route(finding: dict, mode: str) -> str:
    """Что происходит с находкой при этом режиме работы.

    Проход одинаков во всех режимах; различается только, КТО закрывает
    найденное. Смешение этих двух вещей и превращает «полный автомат плюс
    глубокая проработка» в противоречие.
    """
    if finding["outcome"] != ASK:
        return finding["outcome"]
    return ASSUMPTION if mode == "full" else ASK


def verdict(data: dict, mode: str = "semi") -> dict:
    n = len(data["findings"])
    by_q = {q: sum(1 for f in data["findings"] if f["q"] == q) for q in QUESTIONS}
    routed = [{**f, "goes": route(f, mode)} for f in data["findings"]]

    broken, unmeasured = [], []
    if not data["ran"]:
        unmeasured.append("проход не проводился — задача не разбиралась вовсе")
    elif n < MIN_FINDINGS:
        # Не «задача хороша», а «так выглядит непроведённая работа». Разница
        # в том, что первое нечем проверить, а второе — повод переспросить.
        broken.append(f"находок {n} при нижней отметке {MIN_FINDINGS} — на "
                      "реальной задаче это признак прохода для галочки, а не "
                      "признак безупречной задачи")
    untouched = [q for q, c in by_q.items() if c == 0]
    if data["ran"] and len(untouched) > 4:
        unmeasured.append("не задано вопросов: " + ", ".join(untouched[:7]))

    return {"ran": data["ran"], "found": n, "by_question": by_q,
            "routed": routed, "mode": mode,
            "broken": broken, "unmeasured": unmeasured,
            "status": "fail" if broken else ("unknown" if unmeasured else "pass"),
            "detail": (f"{n} находок" if data["ran"] else "не проводился")}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


_TAKES = {"--q", "--what", "--then", "--outcome", "--mode"}


def _one(argv, name, default=""):
    return argv[argv.index(name) + 1] if name in argv and \
        argv.index(name) + 1 < len(argv) else default


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in _TAKES:
            skip = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) != 2 or plain[0] not in ("add", "show"):
        print("вызов: premortem.py add|show <файл> ...", file=sys.stderr)
        return 3
    cmd, path = plain[0], Path(plain[1])
    data = load(path)

    if cmd == "add":
        try:
            data = add(data, _one(argv, "--q"), _one(argv, "--what"),
                       _one(argv, "--then"), _one(argv, "--outcome", ASK))
        except ValueError as e:
            print(f"НЕ УДАЛОСЬ: {e}", file=sys.stderr)
            return 3
        save(path, data)

    v = verdict(data, _one(argv, "--mode", "semi"))
    if "--json" not in argv:
        head = {"pass": "ПРОХОД СОСТОЯЛСЯ", "fail": "ПРОХОД НАРУШЕН",
                "unknown": "ПРОХОД НЕ ПРОВЕДЁН"}
        print(f"{head[v['status']]}: {v['detail']}", file=sys.stderr)
        for f in v["routed"]:
            print(f"  [{f['q']}] {f['what']} → {f['goes']}", file=sys.stderr)
        for b in v["broken"]:
            print(f"  ! {b}", file=sys.stderr)
        for u in v["unmeasured"]:
            print(f"  ? {u}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    # `add` отвечает за запись: проход, ещё не набравший урожай, — это не
    # отказ записи, а середина работы. Общий вердикт даёт `show`.
    if cmd == "add":
        return 0
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
