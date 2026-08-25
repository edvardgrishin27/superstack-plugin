#!/usr/bin/env python3
"""SUPERSTACK — дерево интервью: три состояния и возможность передумать.

Зачем это не картинка.

Ценность дерева не в ветвях, а в трёх ответах, которые человек хочет получить
одним взглядом: что уже улажено, что можно решать прямо сейчас и что стоит
заблокированным — и чем именно. Граф этого не показывает: он показывает связи,
а состояние приходится вычитывать глазами.

И второе, без чего дерево остаётся украшением: к улаженному узлу нужно уметь
ВЕРНУТЬСЯ. Человек передумывает — это нормальная часть работы, а не сбой. Но
передумать в одном месте значит тронуть всё, что на этом стояло, и вот это
он сам не отследит. Отследить обязана система.

Три правила, ради которых всё написано:

  1. СОСТОЯНИЕ ВЫЧИСЛЯЕТСЯ, А НЕ СТАВИТСЯ СЛОВОМ. «Улажено» — это наличие
     ответа, «заблокировано» — неулаженная предпосылка. Ярлык, который можно
     проставить руками, рано или поздно разойдётся с делом.
  2. ПЕРЕДУМАЛ — ПРОПАГИРУЕТСЯ. Возврат к узлу снимает ответы со всего, что
     на нём стояло, и говорит, что именно снялось. Молчаливый возврат хуже
     запрета: человек уверен, что решения в силе, а они уже нет.
  3. ПУСТОЙ ФРОНТИР ПРИ НЕЗАКРЫТЫХ УЗЛАХ — ЭТО ТУПИК, А НЕ КОНЕЦ. Спрашивать
     нечего, а работа не готова: значит зависимости замкнулись в кольцо или
     блокер некому снять. Это отдельный код возврата, а не тишина.

  .superstack/interview.json ведётся этим инструментом.

  python3 interview.py <корень> add <id> --question "..." [--needs a,b] [--recommend "..."]
  python3 interview.py <корень> answer <id> --with "..."
  python3 interview.py <корень> reopen <id> --why "..."
  python3 interview.py <корень> show [--json]

  код 0 — есть что спросить или всё улажено, 1 — тупик: фронтир пуст, а узлы
  открыты, 2 — дерева нет, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ФАЙЛ = ".superstack/interview.json"

УЛАЖЕНО = "улажено"
ФРОНТИР = "фронтир"
ЗАБЛОКИРОВАНО = "заблокировано"


def _путь(root: Path) -> Path:
    return root / ФАЙЛ


def load(root: Path) -> tuple:
    p = _путь(root)
    if not p.is_file():
        return None, f"дерева интервью нет: {ФАЙЛ}"
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError) as e:
        return None, f"{ФАЙЛ} не разобран ({e})"
    if not isinstance(d, dict) or not isinstance(d.get("nodes"), list):
        return None, f"{ФАЙЛ} — не дерево интервью"
    return d, ""


def save(root: Path, d: dict) -> None:
    p = _путь(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def _улажен(узел: dict) -> bool:
    return bool((узел.get("answer") or "").strip())


def _состояние(узел: dict, по_id: dict) -> tuple:
    """(состояние, чем заблокирован). Вычисляется, а не читается из поля.

    Ярлык, который можно проставить руками, однажды разойдётся с делом — и
    разойдётся молча, потому что смотреть будут на ярлык.
    """
    if _улажен(узел):
        return УЛАЖЕНО, []
    держат = [n for n in (узел.get("needs") or [])
              if n in по_id and not _улажен(по_id[n])]
    отсутствуют = [n for n in (узел.get("needs") or []) if n not in по_id]
    if держат or отсутствуют:
        return ЗАБЛОКИРОВАНО, держат + [f"{n} (нет такого узла)" for n in отсутствуют]
    return ФРОНТИР, []


def _зависимые(id_: str, узлы: list) -> list:
    """Кто стоял на этом узле — прямо и через цепочку."""
    найдено, растёт = set(), True
    while растёт:
        растёт = False
        for у in узлы:
            if у["id"] in найдено:
                continue
            нужды = set(у.get("needs") or [])
            if id_ in нужды or (нужды & найдено):
                найдено.add(у["id"])
                растёт = True
    return sorted(найдено)


def states(d: dict) -> dict:
    """Три состояния разом — то, ради чего дерево вообще показывают."""
    по_id = {у["id"]: у for у in d["nodes"]}
    итог = {УЛАЖЕНО: [], ФРОНТИР: [], ЗАБЛОКИРОВАНО: []}
    for у in d["nodes"]:
        сост, держат = _состояние(у, по_id)
        запись = {"id": у["id"], "question": у.get("question", "")}
        if сост == УЛАЖЕНО:
            запись["answer"] = у.get("answer", "")
            if у.get("reopened_why"):
                запись["reopened_why"] = у["reopened_why"]
        if сост == ЗАБЛОКИРОВАНО:
            запись["blocked_by"] = держат
        if сост == ФРОНТИР and у.get("recommend"):
            запись["recommend"] = у["recommend"]
        итог[сост].append(запись)
    return итог


def reopen(d: dict, id_: str, почему: str) -> dict:
    """Вернуться к улаженному узлу. Всё, что на нём стояло, теряет ответ.

    Молчаливый возврат хуже запрета: человек уверен, что прежние решения в
    силе, а они уже нет — и узнаёт об этом на сдаче.
    """
    по_id = {у["id"]: у for у in d["nodes"]}
    if id_ not in по_id:
        return {"status": "unknown", "detail": f"нет такого узла: {id_}"}
    снято = []
    for цель in [id_] + _зависимые(id_, d["nodes"]):
        у = по_id[цель]
        if _улажен(у):
            у["previous_answer"] = у.get("answer", "")
            у["answer"] = ""
            снято.append(цель)
    по_id[id_]["reopened_why"] = почему
    return {"status": "pass", "reopened": id_, "cleared": снято,
            "detail": f"снято ответов: {len(снято)}"}


def human(v: dict) -> str:
    строки = []
    знак = {УЛАЖЕНО: "+", ФРОНТИР: "?", ЗАБЛОКИРОВАНО: "!"}
    for сост in (ФРОНТИР, ЗАБЛОКИРОВАНО, УЛАЖЕНО):
        узлы = v.get(сост) or []
        строки.append(f"{сост.upper()} ({len(узлы)})")
        for у in узлы:
            строки.append(f"  {знак[сост]} {у['id']}: {у.get('question', '')}")
            if у.get("blocked_by"):
                строки.append(f"      держат: {', '.join(у['blocked_by'])}")
            if у.get("recommend"):
                строки.append(f"      предлагаю: {у['recommend']}")
            if у.get("reopened_why"):
                строки.append(f"      вернулись: {у['reopened_why']}")
        строки.append("")
    return "\n".join(строки).rstrip()


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def _опция(argv: list, имя: str) -> "str | None":
    return argv[argv.index(имя) + 1] if имя in argv and \
        argv.index(имя) + 1 < len(argv) else None


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    берут = {"--question", "--needs", "--recommend", "--with", "--why"}
    plain, пропуск = [], False
    for a in argv:
        if пропуск:
            пропуск = False
            continue
        if a in берут:
            пропуск = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) < 2:
        print("вызов: interview.py <корень> add|answer|reopen|show [id] [опции]",
              file=sys.stderr)
        return 3
    root, команда = Path(plain[0]).resolve(), plain[1]
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    d, отказ = load(root)
    if d is None:
        if команда != "add":
            print(f"НЕ УДАЛОСЬ: {отказ}", file=sys.stderr)
            return 2
        d = {"schema": "superstack.interview.v1", "nodes": []}

    if команда == "add":
        if len(plain) < 3:
            print("вызов: add <id> --question «...»", file=sys.stderr)
            return 3
        id_ = plain[2]
        if any(у["id"] == id_ for у in d["nodes"]):
            print(f"НЕ УДАЛОСЬ: узел {id_} уже есть", file=sys.stderr)
            return 3
        нужды = [s.strip() for s in (_опция(argv, "--needs") or "").split(",")
                 if s.strip()]
        d["nodes"].append({"id": id_, "question": _опция(argv, "--question") or "",
                           "needs": нужды, "answer": "",
                           "recommend": _опция(argv, "--recommend") or ""})
        save(root, d)
        print(f"узел заведён: {id_}", file=sys.stderr)

    elif команда == "answer":
        if len(plain) < 3:
            print("вызов: answer <id> --with «...»", file=sys.stderr)
            return 3
        ответ = _опция(argv, "--with")
        if not (ответ or "").strip():
            # Пустой ответ «улаживает» узел, ничего не решив: ровно тот способ
            # закрыть интервью, не проведя его.
            print("НЕ УДАЛОСЬ: пустой ответ узел не улаживает", file=sys.stderr)
            return 3
        по_id = {у["id"]: у for у in d["nodes"]}
        if plain[2] not in по_id:
            print(f"НЕ УДАЛОСЬ: нет такого узла: {plain[2]}", file=sys.stderr)
            return 3
        по_id[plain[2]]["answer"] = ответ
        save(root, d)
        print(f"улажено: {plain[2]}", file=sys.stderr)

    elif команда == "reopen":
        if len(plain) < 3:
            print("вызов: reopen <id> --why «...»", file=sys.stderr)
            return 3
        почему = _опция(argv, "--why")
        if not (почему or "").strip():
            print("НЕ УДАЛОСЬ: возврат без причины — через неделю его не "
                  "отличить от ошибки", file=sys.stderr)
            return 3
        r = reopen(d, plain[2], почему)
        if r["status"] != "pass":
            print(f"НЕ УДАЛОСЬ: {r['detail']}", file=sys.stderr)
            return 3
        save(root, d)
        print(f"ВЕРНУЛИСЬ К {plain[2]}: {r['detail']}", file=sys.stderr)
        for c in r["cleared"]:
            print(f"  снят ответ: {c}", file=sys.stderr)

    elif команда != "show":
        print(f"НЕ УДАЛОСЬ: неизвестная команда {команда}", file=sys.stderr)
        return 3

    v = states(d)
    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))

    открытые = len(v[ФРОНТИР]) + len(v[ЗАБЛОКИРОВАНО])
    if открытые and not v[ФРОНТИР]:
        # Спрашивать нечего, а работа не готова: зависимости замкнулись в
        # кольцо либо блокер некому снять. Тишина здесь читается как «всё
        # улажено» — и это худший из возможных ответов.
        print("ТУПИК: спрашивать нечего, а узлы открыты", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
