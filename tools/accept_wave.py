#!/usr/bin/env python3
"""Приёмка волны одной строкой. Решает код, а не чтение вывода.

Раньше приёмку делала самая дорогая модель: читала вывод тысячи тестов и
ждала планку, которая идёт часами. Решение «принята» не требует суждения, оно
считается. Здесь оно и считается, а человеку (и модели-приёмщику) достаётся
одна строка и код возврата.

  python3 tools/accept_wave.py N [--json] [--merge <папка с долями>] [--full]

Шаги волны и их поломки читаются из `data/waves.json` (ключ — id шага).
По очереди, на первой же неудаче остановка:

  1. весь набор тестов (`pytest tests/ -q`) — код 0;
  2. выборка всех поломок волны: ничего не выжило, выполнено ровно столько,
     сколько названо, статус `partial` (выборка не бывает «взятой планкой»);
  3. быстрая планка (`--quick --json`): ни `fail`, ни `unknown`;
  4. итог полной планки, снятый С ЭТОГО ЖЕ дерева. Полная планка идёт часами
     и сама не запускается: флаг `--full` запускает её в фоне и сразу
     возвращает «ждёт». Итог ложится в `.gauntlet/accept-N.json` вместе с
     отпечатком дерева. Вместо этого можно дать `--merge`: итог свёл CI.

Одна строка на выходе:

  Волна N принята
  Волна N не принята: <первая причина>
  Волна N ждёт полную планку

  код 0 — принята; 1 — не принята; 2 — не измерено (полная планка ещё идёт
  или снята с другого дерева, нет `waves.json`, дерево держит чужой прогон);
  3 — ошибка вызова.

Слово «принята» печатается только при коде 0. Планку и её коды этот скрипт
читает, а не заменяет. Дерево во время фонового прогона не правят: планка
мутирует файлы. Чужой прогон на дереве скрипт видит по тому же замку, что и
планка, и называет одной фразой.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Таймаут прогона набора и быстрой планки; полная планка таймаута не имеет.
STEP_TIMEOUT = 3600
FULL_TIMEOUT = 12 * 3600

#: Что не входит в отпечаток дерева: то, что меняется без правки кода.
SKIP_DIRS = {".git", ".gauntlet", "__pycache__", ".pytest_cache",
             ".mutation-backup"}
SKIP_FILES = {".mutation-lock", ".DS_Store"}

ACCEPTED, REJECTED, WAIT = "accepted", "rejected", "wait"


class Stop(Exception):
    """Приёмка остановлена: код и причина одной фразой."""

    def __init__(self, code: int, why: str, wait: bool = False):
        super().__init__(why)
        self.code, self.why, self.wait = code, why, wait


# --------------------------------------------------------------------------
# пути и команды (подменяются переменными окружения — для тестов)
# --------------------------------------------------------------------------
def waves_path() -> Path:
    return Path(os.environ.get("SUPERSTACK_ACCEPT_WAVES")
                or ROOT / "data" / "waves.json")


def result_dir() -> Path:
    return Path(os.environ.get("SUPERSTACK_ACCEPT_DIR") or ROOT / ".gauntlet")


def command(kind: str, default: list) -> list:
    raw = os.environ.get("SUPERSTACK_ACCEPT_CMD_" + kind)
    if not raw:
        return default
    try:
        cmd = json.loads(raw)
    except ValueError:
        raise Stop(3, f"подмена команды {kind} не разобрана")
    if not (isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd)):
        raise Stop(3, f"подмена команды {kind} не список строк")
    return cmd


def gauntlet_cmd(*args: str) -> list:
    return [sys.executable, str(ROOT / "tools" / "gauntlet.py"), *args]


def sh(cmd: list, timeout: int) -> tuple:
    """(код, вывод). Прогон, который не состоялся, — код 124 или 127."""
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout,
                           env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
                                "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "не завершилось вовремя"
    except OSError as e:
        return 127, str(e)


def json_of(out: str) -> "dict | None":
    """Первый объект JSON в выводе: планка печатает и текст, и объект."""
    start = out.find("{")
    while start != -1:
        try:
            d, _ = json.JSONDecoder().raw_decode(out[start:])
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
        start = out.find("{", start + 1)
    return None


def gate_of(report: "dict | None", name: str) -> "dict | None":
    for g in (report or {}).get("gates") or []:
        if g.get("gate") == name:
            return g
    return None


# --------------------------------------------------------------------------
# отпечаток дерева
# --------------------------------------------------------------------------
def tree_fingerprint(root: Path = None) -> str:
    """Отпечаток всех файлов дерева. Любая правка даёт новый.

    Итог полной планки относится к тому дереву, с которого снят. Правка после
    прогона делает итог чужим: слово «принята» на нём было бы словами.
    """
    root = root or ROOT
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_FILES or name.endswith(".pyc"):
                continue
            p = Path(dirpath) / name
            try:
                data = p.read_bytes()
            except OSError:
                continue
            h.update(str(p.relative_to(root)).encode("utf-8") + b"\0")
            h.update(hashlib.sha256(data).digest())
    return "sha256:" + h.hexdigest()


# --------------------------------------------------------------------------
# шаги волны
# --------------------------------------------------------------------------
def load_wave(n: int) -> tuple:
    """(id поломок без повторов, файлы тестов) волны N."""
    p = waves_path()
    try:
        data = json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        raise Stop(2, f"нет читаемого {p.name}")
    prefix = f"W{n}.S"
    steps = {k: v for k, v in data.items() if k.startswith(prefix)}
    if not steps:
        raise Stop(2, f"в {p.name} нет шагов волны {n}")
    muts, tests = [], []
    for rec in steps.values():
        muts += [m for m in rec.get("mutations") or [] if m not in muts]
        tests += [t for t in rec.get("tests") or [] if t not in tests]
    if not muts:
        raise Stop(1, f"у шагов волны {n} нет ни одной поломки")
    return muts, tests


def check_tests_exist(tests: list) -> None:
    gone = [t for t in tests if not (ROOT / t).is_file()]
    if gone:
        raise Stop(1, f"файл теста не найден: {gone[0]}")


# --------------------------------------------------------------------------
# замок
# --------------------------------------------------------------------------
def foreign_run() -> "str | None":
    """Чужой живой прогон на дереве, тем же замком, что держит планка."""
    spec = importlib.util.spec_from_file_location(
        "accept_wave_gauntlet", ROOT / "tools" / "gauntlet.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    lock = os.environ.get("SUPERSTACK_ACCEPT_LOCK")
    if lock:
        g._lock = lambda: Path(lock)
    other = g._чужой_замок()
    if not other:
        return None
    return f"дерево держит чужой прогон ({other['кто']}), дождись его"


# --------------------------------------------------------------------------
# проверки по очереди
# --------------------------------------------------------------------------
def pytest_is_green() -> None:
    code, out = sh(command("PYTEST", [sys.executable, "-m", "pytest",
                                      "tests/", "-q"]), STEP_TIMEOUT)
    if code != 0:
        raise Stop(1, "тесты красные" if code not in (124, 127)
                   else "тесты не запустились")


def caught_mutations(gate: dict, expected: int) -> "str | None":
    """Причина, по которой выборка поломок не считается пойманной, или None.

    Пойманы, когда одновременно: ничего не выжило, выполнено ровно столько,
    сколько названо, статус `partial`. `fail` — поломка выжила; `unknown` —
    прогон не состоялся.
    """
    if gate.get("survived"):
        return f"поломка выжила: {gate['survived'][0].get('id', '?')}"
    if gate.get("status") != "partial":
        return f"выборка поломок не пойманная (статус {gate.get('status')})"
    if gate.get("executed") != expected:
        return (f"выполнено поломок {gate.get('executed')} из {expected} "
                "названных")
    return None


def mutations_are_caught(ids: list) -> None:
    base = gauntlet_cmd("--gate", "мутации", "--json", "--mutation")
    code, out = sh(command("MUTATIONS", base) + [",".join(ids)], STEP_TIMEOUT)
    gate = gate_of(json_of(out), "мутации")
    if gate is None:
        raise Stop(1, "выборка поломок не измерена: нет ответа планки")
    why = caught_mutations(gate, len(ids))
    if why:
        raise Stop(1, why)


def quick_is_clean() -> None:
    code, out = sh(command("QUICK", gauntlet_cmd("--quick", "--json")),
                   STEP_TIMEOUT)
    report = json_of(out)
    if report is None:
        raise Stop(1, "быстрая планка не дала ответа")
    bad = [g for g in report.get("gates") or []
           if g.get("status") in ("fail", "unknown")]
    if bad:
        raise Stop(1, f"быстрая планка: {bad[0].get('gate')} — "
                   f"{bad[0].get('status')}")


# --------------------------------------------------------------------------
# полная планка
# --------------------------------------------------------------------------
def result_file(n: int) -> Path:
    return result_dir() / f"accept-{n}.json"


def running_file(n: int) -> Path:
    return result_dir() / f"accept-{n}.running"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def full_is_running(n: int) -> bool:
    try:
        pid = int(running_file(n).read_text("utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return pid_alive(pid)


def read_record(n: int) -> "dict | None":
    try:
        d = json.loads(result_file(n).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def full_bar_state(rec: "dict | None", tree: str) -> str:
    """ACCEPTED, REJECTED или WAIT по итогу полной планки и дереву."""
    if rec is None:
        return WAIT
    if rec.get("tree") != tree:
        return WAIT
    return ACCEPTED if rec.get("done") is True else REJECTED


def start_full_in_background(n: int) -> None:
    """Запустить полную планку отдельным процессом и сразу вернуться."""
    result_dir().mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(n), "--run-full"],
        cwd=str(ROOT), start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    running_file(n).write_text(f"{child.pid} {int(time.time())}\n", "utf-8")


def run_full_and_record(n: int) -> int:
    """Тело фонового процесса: прогнать планку, записать итог с отпечатком."""
    before = tree_fingerprint()
    code, out = sh(command("FULL", gauntlet_cmd("--json")), FULL_TIMEOUT)
    report = json_of(out) or {}
    after = tree_fingerprint()
    rec = {"wave": n, "done": bool(report.get("done")) and code == 0,
           "exit_code": code, "next": report.get("next", "нет ответа планки"),
           # Дерево правили во время прогона: итог не относится ни к одному.
           "tree": before if before == after else None,
           "at": int(time.time())}
    result_dir().mkdir(parents=True, exist_ok=True)
    tmp = result_dir() / f"accept-{n}.part"
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), "utf-8")
    tmp.rename(result_file(n))
    try:
        running_file(n).unlink()
    except OSError:
        pass
    return 0


def merged_full_bar(folder: str) -> str:
    """Итог полной планки по сведению долей, которое сделал CI."""
    base = gauntlet_cmd("--merge")
    code, out = sh(command("MERGE", base) + [folder], STEP_TIMEOUT)
    status = (json_of(out) or {}).get("status")
    if status == "pass":
        return ACCEPTED
    if status == "fail":
        raise Stop(1, "сведение долей не прошло")
    return WAIT


def full_bar(n: int, merge: "str | None", start: bool) -> str:
    if merge:
        return merged_full_bar(merge)
    state = full_bar_state(read_record(n), tree_fingerprint())
    if state == REJECTED:
        rec = read_record(n) or {}
        raise Stop(1, f"полная планка не взята: {rec.get('next', 'нет причины')}")
    if state == WAIT and start and not full_is_running(n):
        start_full_in_background(n)
    return state


# --------------------------------------------------------------------------
# общий порядок
# --------------------------------------------------------------------------
def accept(n: int, merge: "str | None", start: bool) -> tuple:
    """(код, строка)."""
    try:
        muts, tests = load_wave(n)
        check_tests_exist(tests)
        beda = foreign_run()
        if beda:
            raise Stop(2, beda)
        if full_is_running(n):
            return 2, f"Волна {n} ждёт полную планку"
        pytest_is_green()
        mutations_are_caught(muts)
        quick_is_clean()
        state = full_bar(n, merge, start)
    except Stop as s:
        return s.code, f"Волна {n} не принята: {s.why}"
    if state == ACCEPTED:
        return 0, f"Волна {n} принята"
    return 2, f"Волна {n} ждёт полную планку"


def usage(why: str) -> int:
    print(f"НЕ УДАЛОСЬ: {why}. Вызов: accept_wave.py N [--json] "
          "[--merge <папка с долями>] [--full]", file=sys.stderr)
    return 3


def main(argv: list) -> int:
    known = {"--json", "--merge", "--full", "--run-full"}
    stray = [a for a in argv if a.startswith("--") and a not in known]
    if stray:
        return usage(f"неизвестный флаг {stray[0]}")
    merge = None
    if "--merge" in argv:
        i = argv.index("--merge")
        if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
            return usage("после --merge нужна папка")
        merge = argv[i + 1]
    rest = [a for i, a in enumerate(argv)
            if not a.startswith("--") and not (i and argv[i - 1] == "--merge")]
    if len(rest) != 1 or not rest[0].isdigit():
        return usage("нужен один номер волны")
    n = int(rest[0])
    if "--run-full" in argv:
        return run_full_and_record(n)
    code, line = accept(n, merge, "--full" in argv)
    if "--json" in argv:
        print(json.dumps({"wave": n, "code": code, "line": line},
                         ensure_ascii=False))
    else:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
