#!/usr/bin/env python3
"""SUPERSTACK — планка ПРОЕКТА: черта готовности, которую считает код.

Зачем это, если уже есть гейт верификации.

`verify.py` отвечает «зелено ли сейчас», `prove_tests.py` — «держатся ли тесты
при внесённой поломке», `project_doctor.py` — «можно ли доверять зелёному в этом
репозитории». Ни один не отвечает на последний вопрос, которым всё и кончается:
**готово ли**. Пока на него отвечает переписка, ответ зависит от того, кто
вчитывался. Цель «все ошибки исправлены» удовлетворяется тем, что агент СКАЗАЛ,
будто исправил, — оценщик читает стенограмму, а не запускает инструменты.

Поэтому здесь то же устройство, которым SUPERSTACK меряет сам себя: список
ворот, у каждого своя команда, и вердикт — код возврата. Разница одна: ворота
называет проект, а не мы. Планка со своей конкретикой у каждого продукта, форма
у всех одна.

  .superstack/bar.json  (а если проект положил в `.claude/bar.json` — оттуда)
    {"schema": "superstack.bar.v1",
     "why": "что здесь считается готовым и почему",
     "gates": [
       {"name": "набор",    "why": "тесты зелёные",      "run": "npm test"},
       {"name": "мутации",  "why": "тесты что-то держат", "builtin": "mutations"},
       {"name": "верификация", "why": "зелено ли сейчас", "builtin": "verify"},
       {"name": "осмотр",   "why": "верить ли зелёному",  "builtin": "doctor"}]}

Три отказа, ради которых всё написано. Каждый — одна и та же подмена:
«не проверяли» выдаётся за «проверено и хорошо».

  1. ПЛАНКИ НЕТ — ЭТО НЕ ВЗЯТАЯ ПЛАНКА. Отсутствие файла даёт код 2, а не 0.
     Иначе проект без планки проходит лучше проекта с ней, и это ровно тот
     стимул, который убивает планку на второй неделе.
  2. ПУСТАЯ ПЛАНКА — ТОЖЕ НЕ ВЗЯТАЯ. Ноль ворот не имеет права выглядеть
     успехом: предъявить нечего и проверено ничего.
  3. ВОРОТА, КОТОРЫЕ НЕЧЕМ ЗАПУСТИТЬ, ГАСЯТ ВЕРДИКТ. Объявленная проверка,
     которую не на чем выполнить, называется вслух и держит код 2. Молча
     выпасть из счёта она не может — иначе планка самоопускается ровно там,
     где сломался инструмент, и делает это тихо.

Красное сильнее серого: если хоть одни ворота упали, вердикт красный, сколько
бы ни было непроверенных.

  python3 bar.py <корень проекта> [--json]

  код 0 — планка взята, 1 — красное, 2 — измерить не смог, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIMEOUT = 900

#: Оболочка сообщает так, что запускать было нечем. Отличать этот исход от
#: «команда отработала и вернула ошибку» обязательно: первое — дыра в приборе,
#: второе — находка о продукте, и путать их значит чинить не то.
UNRUNNABLE = 127

#: Ворота, которые не надо описывать командой: они уже есть в пакете. Значение —
#: файл инструмента и то, что этот инструмент доказывает. Словарь существует,
#: чтобы планка проекта читалась человеком, а не собиралась из путей.
BUILTINS = {
    "verify": ("verify.py", "зелено ли сейчас"),
    "mutations": ("prove_tests.py", "держатся ли тесты при внесённой поломке"),
    "doctor": ("project_doctor.py", "можно ли доверять зелёному в этом репозитории"),
}

#: Куда смотреть за планкой. Первый путь — рабочий, второй остался обещанием
#: и потому поддержан: проект, положивший файл туда, не должен молча остаться
#: без планки.
BAR_FILES = (".superstack/bar.json", ".claude/bar.json")

ШАБЛОН = ('{"schema": "superstack.bar.v1", "why": "...", "gates": '
          '[{"name": "набор", "why": "тесты зелёные", "run": "<команда тестов>"}, '
          '{"name": "мутации", "why": "тесты что-то держат", "builtin": "mutations"}]}')


def load_bar(root: Path) -> tuple:
    """Планка проекта из его собственного файла. Ничего не выдумываем.

    Возвращает `(спецификация, путь, причина отказа)`. Причина — человеческий
    текст, а не код: её печатают человеку, и «нет файла» должно читаться как
    задача, а не как сбой.
    """
    for rel in BAR_FILES:
        f = root / rel
        if f.is_file():
            try:
                return json.loads(f.read_text("utf-8")), f, None
            except ValueError as e:
                return None, f, f"планка не разобрана ({e})"
    return None, None, (f"нет файла планки: {' или '.join(BAR_FILES)} — "
                        "«планки нет» и «планка взята» разные утверждения")


def _argv(root: Path, g: dict) -> tuple:
    """Чем запускать эти ворота. Пустой argv — значит нечем, и это говорится."""
    if "builtin" in g:
        b = BUILTINS.get(g["builtin"])
        if not b:
            return [], (f"неизвестные встроенные ворота «{g['builtin']}» — "
                        f"есть: {', '.join(sorted(BUILTINS))}")
        return [sys.executable, str(HERE / b[0]), str(root), "--json"], ""
    cmd = g.get("run", "")
    if not cmd:
        return [], ("ворота объявлены, но запускать нечего: нет ни `run`, "
                    "ни `builtin`")
    try:
        # posix=False на Windows: в POSIX-режиме обратный слэш считается
        # экранированием, и `C:\проект\npm.cmd` разбирается в `C:проектnpm.cmd`.
        # Команда не найдётся, а сообщение будет про отсутствующий файл — то
        # есть человек пойдёт искать поломку не там.
        return shlex.split(cmd, posix=os.name != "nt"), ""
    except ValueError as e:
        return [], f"команда не разобрана ({e})"


def run_gate(root: Path, g: dict) -> dict:
    """Одни ворота: запустить и перевести код возврата в утверждение."""
    out = {"gate": g.get("name") or "без имени", "why": g.get("why", "")}
    argv, нечем = _argv(root, g)
    if not argv:
        return {**out, "status": "unknown", "detail": нечем}

    try:
        p = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                           timeout=TIMEOUT,
                           env={**os.environ, "NO_COLOR": "1", "CI": "1"})
        code, tail = p.returncode, ((p.stdout or "") + (p.stderr or ""))[-400:]
    except FileNotFoundError:
        code, tail = UNRUNNABLE, f"нет такой команды: {argv[0]}"
    except subprocess.TimeoutExpired:
        return {**out, "status": "unknown",
                "detail": f"не уложились в {TIMEOUT} с — измерить не удалось"}
    except OSError as e:
        return {**out, "status": "unknown", "detail": f"запустить не вышло: {e}"}

    if code == 10:
        return {**out, "status": "unknown", "detail": "система на паузе"}
    if code == UNRUNNABLE:
        return {**out, "status": "unknown",
                "detail": f"запустить нечем: {tail.strip()[-200:]}"}
    # Встроенные ворота говорят на том же языке кодов, что и планка: 2 — «не
    # смог», 3 — вызвали неправильно. И то и другое НЕ провал продукта.
    #
    # Чужая команда может говорить на нём же — тогда проект называет свои коды
    # сам, полем `unknown_on`. Без этого поля «не смог измерить» неотличимо от
    # «упало», и планка обвинит продукт в поломке сломанного прибора.
    неизмеримо = set(g.get("unknown_on") or ()) | ({2, 3} if "builtin" in g else set())
    if code in неизмеримо:
        return {**out, "status": "unknown",
                "detail": f"измерить не удалось (код {code})",
                "tail": tail.strip()[-200:]}
    if code == 0:
        return {**out, "status": "pass", "detail": "код 0"}
    return {**out, "status": "fail", "detail": f"код {code}",
            "tail": tail.strip()[-200:]}


def verdict(gates: list) -> dict:
    """Красное сильнее серого, серое сильнее зелёного."""
    red = [g for g in gates if g["status"] == "fail"]
    grey = [g for g in gates if g["status"] == "unknown"]
    if red:
        return {"status": "fail", "gates": gates,
                "detail": f"красных ворот: {len(red)} из {len(gates)}",
                "next": "чинится продукт, а не планка: ворота, снятые ради "
                        "зелёного, — это опущенная планка, и первым это "
                        "сделает любой оптимизирующий агент"}
    if grey:
        return {"status": "unknown", "gates": gates,
                "detail": f"не проверено ворот: {len(grey)} из {len(gates)}",
                "next": "непроверенное не считается взятым: дать команду "
                        "или убрать ворота вместе с причиной"}
    return {"status": "pass", "gates": gates,
            "detail": f"взято ворот: {len(gates)} из {len(gates)}"}


def run(root: Path, spec: dict) -> dict:
    gates = spec.get("gates") or []
    if not gates:
        # Пустая планка не имеет права выглядеть успехом — по той же причине,
        # по которой ноль зарегистрированных поломок не значит «тесты держат».
        return {"status": "unknown", "gates": [],
                "detail": "в планке ни одних ворот — это «не проверяли», "
                          "а не «предъявлять нечего, значит хорошо»",
                "next": f"описать хотя бы одни ворота: {ШАБЛОН}"}
    return verdict([run_gate(root, g) for g in gates])


def human(v: dict) -> str:
    head = {"pass": "ПЛАНКА ВЗЯТА", "fail": "ПЛАНКА НЕ ВЗЯТА",
            "unknown": "ИЗМЕРИТЬ НЕ СМОГ"}
    строки = [f"{head[v['status']]}: {v['detail']}"]
    знак = {"pass": "+", "fail": "!", "unknown": "?"}
    for g in v.get("gates", []):
        хвост = f" — {g['why']}" if g.get("why") else ""
        строки.append(f"  {знак[g['status']]} {g['gate']}: {g['detail']}{хвост}")
    if v.get("next"):
        строки.append(f"  дальше: {v['next']}")
    return "\n".join(строки)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def _utf8_stdio() -> None:
    """Печать по-русски не должна зависеть от локали.

    В окружении без UTF-8 — минимальный контейнер, cron с урезанным env,
    `PYTHONCOERCECLOCALE=0` — кодировка вывода оказывается ascii, и первый же
    русский символ роняет инструмент целиком. Человек получает не «проверка не
    прошла», а трейсбек вместо любого ответа. На macOS по умолчанию это не
    воспроизводится: интерпретатор сам приводит локаль C к C.UTF-8.
    """
    for поток in (sys.stdout, sys.stderr):
        кодировка = (getattr(поток, "encoding", "") or "").lower().replace("-", "")
        if кодировка != "utf8" and hasattr(поток, "reconfigure"):
            поток.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _utf8_stdio()
    halt_if_paused()
    argv = sys.argv[1:]
    plain = [a for a in argv if not a.startswith("--")]
    if len(plain) != 1:
        print("вызов: bar.py <корень проекта> [--json]", file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    spec, path, отказ = load_bar(root)
    if spec is None and path is not None:
        print(f"НЕ УДАЛОСЬ: {отказ}", file=sys.stderr)
        return 3
    if spec is None:
        v = {"status": "unknown", "gates": [], "detail": отказ,
             "next": f"положить планку в {BAR_FILES[0]}: {ШАБЛОН}"}
    else:
        v = run(root, spec)
        v["bar"] = str(path)

    if "--json" not in argv:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
