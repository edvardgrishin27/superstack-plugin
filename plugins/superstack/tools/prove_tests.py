#!/usr/bin/env python3
"""SUPERSTACK — доказать, что тесты ПРОЕКТА могли быть красными.

Зачем это, если тесты уже зелёные.

Зелёный прогон отвечает на вопрос «сколько тестов выполнилось». Вопрос, ради
которого их пишут, — «мог ли хоть один упасть», — он не отвечает никогда, и
разница между этими двумя видна только когда что-то ломаешь нарочно.

Механизм найден на живом прогоне, не выведен. Исполнитель вернул `3 passed
(0 failed)`, тесты стояли на названном шве, ожидаемые значения были литералами.
Я заменил зависимость `notify.send` на заглушку, возвращающую ту же форму и НЕ
ОТПРАВЛЯЮЩУЮ НИЧЕГО, — прогон остался `3 passed, 0 failed`. Критерий «форма
отправляется» был выполнен по букве и обойдён по сути: тест утверждал
`result.ok === true`, а `ok` выставляет вызывающий код, а не факт доставки.

Два режима, и оба намеренно НЕ выдумывают поломок.

  · ЗАМЕНА — `find`/`replace` в файле. Точная, переносимая, пишется под
    конкретный критерий приёмки.
  · ЗАГЛУШКА — `stub`: файл целиком заменяется телом, которое возвращает ту же
    форму и ничего не делает. Ровно тот случай с `notify`, и он не зависит от
    языка: убить зависимость всегда можно, подменив её целиком.

Три правила, без которых это превратится в театр:

  1. КРАСНЫЙ НАБОР ДО МУТАЦИЙ — НЕ ИЗМЕРЕНИЕ. Если проект красный сам по себе,
     любая мутация «поймана», и отчёт будет блестящим. Сначала зелёный, потом
     ломаем.
  2. НОЛЬ ЗАРЕГИСТРИРОВАННЫХ ПОЛОМОК — «НЕ ПРОВЕРЯЛИ», А НЕ «ТЕСТЫ КРЕПКИЕ».
     Пустой набор мутаций не имеет права выглядеть успехом.
  3. ФАЙЛ ВОССТАНАВЛИВАЕТСЯ БАЙТ-В-БАЙТ И ДО ЛЮБОЙ ДРУГОЙ РАБОТЫ. Мутация,
     пережившая прогон, отравляет все следующие — это уже случалось трижды
     в нашем собственном репозитории.

  .superstack/mutations.json:
    {"test_cmd": "npm test",
     "mutations": [{"id": "...", "file": "...", "why": "...",
                    "find": "...", "replace": "..."},
                   {"id": "...", "file": "...", "why": "...", "stub": "..."}]}

  python3 prove_tests.py <корень проекта> [--set файл.json] [--json]

  код 0 — все поломки пойманы, 1 — есть выжившие, 2 — измерить не смог, 3 — вызов
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

TIMEOUT = 900


def _lock(root: Path) -> Path:
    return root / ".superstack" / ".mutation-lock"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def acquire(root: Path) -> "str | None":
    """Занять право ломать дерево. Мёртвый замок снимается сам."""
    p = _lock(root)
    if p.is_file():
        try:
            pid = int(p.read_text("utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            pid = None
        if pid and pid != os.getpid() and _alive(pid):
            return (f"дерево уже ломает процесс {pid} — второй прогон увидит "
                    "чужую поломку и отнесёт её на свой счёт")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{os.getpid()}\n", encoding="utf-8")
    except OSError as e:
        return f"замок не поставлен ({e}) — ломать вслепую нельзя"
    return None


def release(root: Path) -> None:
    try:
        _lock(root).unlink()
    except OSError:
        pass


def run_tests(root: Path, cmd: str) -> tuple:
    try:
        p = subprocess.run(shlex.split(cmd), cwd=str(root), capture_output=True,
                           text=True, timeout=TIMEOUT,
                           env={**os.environ, "NO_COLOR": "1", "CI": "1"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return p.returncode, ((p.stdout or "") + (p.stderr or ""))[-600:]


def apply(path: Path, m: dict) -> "bytes | None":
    """Внести поломку. Возвращает исходные байты либо None, если не вышло."""
    orig = path.read_bytes()
    if "stub" in m:
        path.write_text(m["stub"], encoding="utf-8")
        return orig
    text = orig.decode("utf-8", errors="replace")
    if m.get("find") not in text:
        return None
    path.write_text(text.replace(m["find"], m["replace"], 1), encoding="utf-8")
    return orig


def run(root: Path, spec: dict) -> dict:
    cmd = spec.get("test_cmd", "")
    muts = spec.get("mutations") or []
    if not cmd:
        return {"status": "unknown",
                "detail": "не задана команда тестов — ломать нечего и мерить нечем"}
    if not muts:
        # Пустой набор не имеет права выглядеть успехом: «не проверяли» и
        # «тесты крепкие» — разные утверждения, и второе тут не доказано ничем.
        return {"status": "unknown",
                "detail": "ни одной поломки не зарегистрировано — это «не "
                          "проверяли», а не «тесты держат»",
                "next": "на каждый критерий приёмки завести поломку, которая "
                        "обязана его уронить; для интеграции — заглушку, "
                        "возвращающую ту же форму и не делающую ничего"}

    code, tail = run_tests(root, cmd)
    if code != 0:
        return {"status": "unknown",
                "detail": f"набор красный ДО поломок (код {code}) — тогда любая "
                          "мутация «поймана», и отчёт будет блестящим",
                "tail": tail}

    survived, checked, broken = [], 0, []
    for m in muts:
        f = root / m.get("file", "")
        if not f.is_file():
            broken.append(f"{m.get('id')}: нет файла {m.get('file')}")
            continue
        orig = None
        try:
            orig = apply(f, m)
            if orig is None:
                broken.append(f"{m.get('id')}: якорь не найден в {m['file']}")
                continue
            rc, _ = run_tests(root, cmd)
            caught = rc != 0
        finally:
            if orig is not None:
                # Восстановление ДО любой другой работы: поломка, пережившая
                # прогон, отравляет все следующие.
                f.write_bytes(orig)
        checked += 1
        if not caught:
            survived.append({"id": m.get("id"), "file": m.get("file"),
                             "why": m.get("why", "")})

    if broken:
        return {"status": "unknown", "checked": checked, "survived": survived,
                "detail": "поломки не применились: " + "; ".join(broken[:5])}
    if survived:
        return {"status": "fail", "checked": checked, "survived": survived,
                "detail": f"выжило {len(survived)} из {checked} — тесты "
                          "остались зелёными при внесённой поломке",
                "next": "тест обязан падать, когда это ломается; пока не падает, "
                        "критерий выполнен по букве и обойдён по сути"}
    return {"status": "pass", "checked": checked, "survived": [],
            "detail": f"{checked} поломок, каждая роняет набор"}


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
    takes = {"--set"}
    plain, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in takes:
            skip = True
        elif not a.startswith("--"):
            plain.append(a)
    if len(plain) != 1:
        print("вызов: prove_tests.py <корень проекта> [--set файл.json]",
              file=sys.stderr)
        return 3
    root = Path(plain[0]).resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 3

    src = Path(argv[argv.index("--set") + 1]) if "--set" in argv \
        else root / ".superstack" / "mutations.json"
    if not src.is_file():
        print(f"НЕ УДАЛОСЬ: нет набора поломок: {src}", file=sys.stderr)
        return 3
    try:
        spec = json.loads(src.read_text("utf-8"))
    except ValueError as e:
        print(f"НЕ УДАЛОСЬ: набор поломок не разобран: {e}", file=sys.stderr)
        return 3

    held = acquire(root)
    if held:
        print(f"НЕ УДАЛОСЬ: {held}", file=sys.stderr)
        return 2
    try:
        v = run(root, spec)
    finally:
        release(root)

    if "--json" not in argv:
        head = {"pass": "ТЕСТЫ ДЕРЖАТ", "fail": "ТЕСТЫ НЕ ДЕРЖАТ",
                "unknown": "ИЗМЕРИТЬ НЕ СМОГ"}
        print(f"{head[v['status']]}: {v['detail']}", file=sys.stderr)
        for s in v.get("survived", []):
            print(f"  ! выжила: {s['id']} ({s['file']}) — {s['why']}",
                  file=sys.stderr)
        if v.get("next"):
            print(f"  дальше: {v['next']}", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]


if __name__ == "__main__":
    sys.exit(main())
