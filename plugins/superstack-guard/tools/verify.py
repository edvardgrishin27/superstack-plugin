#!/usr/bin/env python3
"""SUPERSTACK — гейт верификации. Решение принимает СКРИПТ, а не читатель.

Зачем это отдельный инструмент, а не абзац в инструкции.

«Готово» от модели — утверждение, а не доказательство. «Должно работать» и
«тесты должны проходить» означают ровно одно: работа не сделана. Инструкция,
которая просит агента проверить себя, не является механизмом: её можно
исполнить формально, а результат всё равно пишет тот, кого проверяют.

Поэтому вердикт здесь считает код. Он запускает то, что проект объявил сам,
и отдаёт машиночитаемый результат с кодом возврата. Спорить с кодом возврата
нельзя.

Три правила, ради которых всё и написано:

  1. НЕТ ПРОВЕРКИ — ЭТО НЕ ЗЕЛЁНЫЙ. Отсутствие тестов даёт `absent` и код 2,
     а не `pass`. Иначе пустой проект проходит гейт лучше настоящего, и это
     ровно тот стимул, который убивает тесты в проекте на второй неделе.
  2. ПРОГОН БЕЗ ЕДИНОГО ТЕСТА — ТОЖЕ НЕ ЗЕЛЁНЫЙ. `pytest` без тестов выходит
     с кодом 5, `jest --passWithNoTests` — с нулём. Формально успех, фактически
     не проверено ничего. Такой исход помечается `absent`, а не `pass`.
  3. ЗАПУСКАЕТСЯ ТОЛЬКО ТО, ЧТО ПРОЕКТ ОБЪЯВИЛ. Команды не выдумываются: если
     в package.json нет `test`, тестов нет — так и сказано. Каталог с именем
     `test/` объявлением питоновских тестов не является: в Go- и JS-репозиториях
     это обычно золотые файлы.
  4. «НЕ НАШЁЛ», «НЕ СМОГ ПРОВЕРИТЬ» И «ПРОВАЛИЛОСЬ» — ТРИ РАЗНЫХ УТВЕРЖДЕНИЯ.
     Объявленная проверка, которую нечем запустить (pytest не установлен),
     называется и гасит вердикт, но не выдаётся за красные тесты. Заглушка
     `npm init` («no test specified» + код 1) — это «тестов нет», а не «тесты
     красные»: чинить там нечего, и требовать починки значит врать.

  python3 verify.py [каталог-проекта]        -> вердикт человеку + JSON в stdout
  python3 verify.py --json [каталог]         -> только JSON

  код 0 — прошло, 1 — не прошло, 2 — проверять нечем, 3 — ошибка вызова

  Потолок попыток. Гейт запускается заново на каждый вызов — сам по себе он
  не помнит, что чинил это же самое три раза подряд. Счётчик подряд идущих
  провалов ОДНОГО проекта живёт на диске (путь — из SUPERSTACK_VERIFY_STATE)
  и после MAX_ATTEMPTS заходов подряд подменяет "next" в вердикте с "почини"
  на явную сдачу: заход тем же способом не сходится, нужен другой подход или
  уточнение задачи от человека. Статус и коды возврата это НЕ меняет —
  "next" читает тот, кто решает, продолжать ли цикл починки, а не машина.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 900          # 15 минут на одну проверку
OUTPUT_KEEP = 4000     # сколько знаков вывода оставляем как улику

# Потолок попыток починки одного и того же провала. Не круглое число ради
# красоты — механизм независимо переоткрывался в пяти репозиториях, и число
# сошлось каждый раз: меньше — гейт сдаётся раньше настоящей смены подхода;
# больше — ночной цикл ревью→фикс успевает сжечь подписку раньше, чем
# кто-то заметит, что чинится одно и то же одним и тем же способом.
MAX_ATTEMPTS = 3

# Путь к счётчику попыток — ТОЛЬКО из переменной окружения (с дефолтом в
# ~/.claude/superstack). Без неё тесты писали бы счётчик в настоящий
# ~/.claude пользователя — герметичность потребовала бы подмены HOME
# целиком ради одного файла. Тот же довод, что для SUPERSTACK_LOG_DIR
# в tools/log.py.
STATE_ENV = "SUPERSTACK_VERIFY_STATE"


@dataclass(frozen=True)
class Check:
    """Одна проверка: что запускаем и почему считаем это доказательством."""
    id: str
    label: str
    cmd: tuple
    why: str


@dataclass(frozen=True)
class Result:
    check: Check
    code: int
    output: str
    empty: bool          # прогон был, но проверять оказалось нечего


@dataclass(frozen=True)
class Unrunnable:
    """Проверка объявлена проектом, но запустить её нечем.

    Отдельный тип, а не молчаливый выброс из списка: «проект не объявил
    проверок» и «проверки есть, но у меня нет чем их запустить» — разные
    утверждения. Первое чинится заведением теста, второе — установкой
    инструмента, и подменять одно другим значит врать про чужой репозиторий.
    """
    check: Check
    reason: str


# Признаки «инструмент отработал, но не проверил ничего». Формально успех —
# фактически пусто. Самая тихая форма зелёного, который ничего не стоит.
#
# Граница перед нулём (?<!\d) — не украшение. Без неё «0 passing» находится
# внутри «10 passing», и зелёный mocha-прогон на 10, 20, 100 тестах объявляется
# пустым: проект с настоящими тестами получает «проверять нечем» и гейт для
# него не зеленеет никогда.
EMPTY_MARKERS = (
    re.compile(r"no tests ran", re.I),
    re.compile(r"No tests found", re.I),
    re.compile(r"collected 0 items"),
    re.compile(r"Tests:\s+0 total", re.I),
    re.compile(r"(?<!\d)0 passing", re.I),
    re.compile(r"no test files", re.I),
    re.compile(r"\[no test files\]", re.I),
    re.compile(r"testing: warning: no tests to run", re.I),
)

# Заглушка, которую `npm init` кладёт в каждый второй пакет:
#   "test": "echo \"Error: no test specified\" && exit 1"
# Она отличается от прочих пустых прогонов НЕНУЛЕВЫМ кодом, поэтому и читается
# как «тесты есть и они красные». Диагноз ложный: тестов нет вовсе, чинить
# нечего, а ход при этом блокируется требованием починить несуществующее.
# Поэтому маркер действует при ЛЮБОМ коде возврата, в отличие от EMPTY_MARKERS.
STUB_MARKERS = (
    re.compile(r"no test specified", re.I),
)


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _pkg_runner(project: Path) -> tuple:
    """Чем запускать скрипты пакета — по файлу блокировки, а не по вкусу.

    Угадывание тут дорого: npm в проекте на pnpm переустановит дерево и
    сломает рабочий каталог человеку.
    """
    for lock, cmd in (("pnpm-lock.yaml", ("pnpm", "run")),
                      ("yarn.lock", ("yarn", "run")),
                      ("bun.lockb", ("bun", "run")),
                      ("package-lock.json", ("npm", "run"))):
        if (project / lock).is_file():
            return cmd
    return ("npm", "run")


# Имена скриптов, которые в экосистеме означают проверку. Порядок важен:
# сначала то, что ловит смысл, потом то, что ловит форму.
NODE_SCRIPTS = (
    ("test", "тесты", "красный тест — единственное доказательство, что код правда работает"),
    ("typecheck", "типы", "несходящиеся типы ломают то, что тесты не трогают"),
    ("tsc", "типы", "несходящиеся типы ломают то, что тесты не трогают"),
    ("lint", "линт", "правила проекта, которые нельзя нарушать молча"),
    ("build", "сборка", "то, что не собирается, не работает ни у кого"),
)


def _all_checks(project: Path) -> list:
    """Что этот проект объявил своей проверкой. Ничего не выдумываем."""
    checks: list = []

    pkg = project / "package.json"
    if pkg.is_file():
        scripts = (_json(pkg).get("scripts") or {})
        runner = _pkg_runner(project)
        seen: set = set()
        for name, label, why in NODE_SCRIPTS:
            if name in scripts and label not in seen:
                seen.add(label)
                checks.append(Check(f"node:{name}", label, runner + (name,), why))

    if (project / "go.mod").is_file():
        checks.append(Check("go:test", "тесты", ("go", "test", "./..."),
                            "красный тест — единственное доказательство"))
        checks.append(Check("go:vet", "статический разбор", ("go", "vet", "./..."),
                            "vet ловит то, что компилятор пропускает"))

    if (project / "Cargo.toml").is_file():
        checks.append(Check("rust:test", "тесты", ("cargo", "test"),
                            "красный тест — единственное доказательство"))

    if _has_python_tests(project):
        checks.append(Check("py:test", "тесты", ("python3", "-m", "pytest", "-q"),
                            "красный тест — единственное доказательство"))

    if not checks and _make_target(project, "test"):
        checks.append(Check("make:test", "тесты", ("make", "test"),
                            "цель test в Makefile — объявленная проверка проекта"))

    return checks


def _why_unrunnable(check: Check) -> str:
    """Пустая строка — запускать есть чем; иначе причина человеческим текстом.

    Наличие интерпретатора не означает наличия прогонщика: `python3` есть почти
    везде, а `pytest` ставится отдельно и на системном python3 его обычно нет.
    Без этой проверки `python3 -m pytest` падает с «No module named pytest» и
    кодом 1, то есть отсутствие инструмента подаётся как красные тесты, и
    модели приказывают чинить то, чего не существует.
    """
    if not shutil.which(check.cmd[0]):
        return f"нечем запустить: {check.cmd[0]} не найден"
    if check.cmd[:3] == ("python3", "-m", "pytest") and not _pytest_available():
        return "нечем запустить: python3 есть, модуля pytest нет"
    return ""


def _pytest_available() -> bool:
    # Спрашиваем тот же python3, который и будет запускать проверку, а не
    # интерпретатор этого скрипта: в PATH может стоять другой.
    try:
        p = subprocess.run(["python3", "-c", "import pytest"],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def detect_checks(project: Path) -> list:
    """Только то, что объявлено И что есть чем запустить."""
    return [c for c in _all_checks(project) if not _why_unrunnable(c)]


def unrunnable_checks(project: Path) -> list:
    """Объявлено, но запустить нечем. Молча выбрасывать это нельзя.

    Выброшенная проверка превращает «не смог проверить» в «проверять нечем»,
    то есть в утверждение о ЧУЖОМ репозитории, которого никто не проверял.
    """
    out = []
    for c in _all_checks(project):
        why = _why_unrunnable(c)
        if why:
            out.append(Unrunnable(c, why))
    return out


def _has_python_tests(project: Path) -> bool:
    """Каталог tests/ считается питоновским, только если в нём есть .py.

    Одно имя каталога ничего не значит: `test/` с золотыми файлами есть в
    Go- и JS-репозиториях сплошь и рядом. Раньше такой каталог выдумывал
    необъявленную проверку `python3 -m pytest` — то есть инструмент нарушал
    собственное правило «запускается только то, что проект объявил».
    """
    for d in ("tests", "test"):
        sub = project / d
        if sub.is_dir() and next(sub.rglob("*.py"), None) is not None:
            return True
    return any(project.glob("test_*.py")) or any(project.glob("*_test.py"))


def _make_target(project: Path, target: str) -> bool:
    mk = project / "Makefile"
    if not mk.is_file():
        return False
    try:
        return re.search(rf"(?m)^{re.escape(target)}\s*:", mk.read_text("utf-8")) is not None
    except OSError:
        return False


def _run(check: Check, project: Path) -> Result:
    try:
        p = subprocess.run(check.cmd, cwd=str(project), capture_output=True,
                           text=True, timeout=TIMEOUT,
                           env={**os.environ, "CI": "1", "NO_COLOR": "1"})
        out = (p.stdout or "") + (p.stderr or "")
        code = p.returncode
    except subprocess.TimeoutExpired:
        return Result(check, 124, f"проверка не завершилась за {TIMEOUT} секунд", False)
    except OSError as e:
        return Result(check, 127, str(e), False)
    # «Прогон был, проверять оказалось нечего» — отдельное состояние, не провал
    # и не успех. pytest отдаёт на это код 5; остальные — ноль и строчку в
    # выводе. Провал с ненулевым кодом сюда не относится: там тесты есть и они
    # красные, и путать эти два случая нельзя — лечатся они по-разному.
    return Result(check, code, out[-OUTPUT_KEEP:], _nothing_was_checked(code, out))


def _nothing_was_checked(code: int, out: str) -> bool:
    """Прогон был, но проверять оказалось нечего."""
    if code == 5:                       # pytest: тестов не найдено
        return True
    if any(rx.search(out) for rx in STUB_MARKERS):
        return True
    return code == 0 and any(rx.search(out) for rx in EMPTY_MARKERS)


def verdict(results: list, project: Path, unrunnable: tuple = ()) -> dict:
    """Машиночитаемый результат: решение принимает тот, кто это прочитает кодом."""
    if not results and not unrunnable:
        return {
            "gate": "verify", "status": "absent", "project": str(project),
            "blockers": ["проект не объявил ни одной проверки"],
            "evidence": [],
            "next": "завести хотя бы один тест — иначе «готово» останется "
                    "утверждением, которое нечем подтвердить",
        }

    blockers, evidence = [], []
    # Непроверенное идёт в улики ПЕРВЫМ и обязательно называется. Молчание тут
    # означало бы отчёт о репозитории, часть которого никто не смотрел.
    for u in unrunnable:
        evidence.append({"check": u.check.id, "label": u.check.label,
                         "state": "unknown", "code": None,
                         "cmd": " ".join(u.check.cmd),
                         "output_tail": u.reason})
        blockers.append(f"{u.check.label}: не смог проверить — {u.reason}")
    for r in results:
        # «Пусто» проверяется ПЕРВЫМ. Иначе pytest без тестов (код 5) читается
        # как провал, и человека отправляют чинить код, которого не существует.
        # Это два разных диагноза с разным лечением: красный тест — чинить код,
        # тестов нет — заводить тест.
        state = "empty" if r.empty else ("fail" if r.code != 0 else "pass")
        evidence.append({"check": r.check.id, "label": r.check.label,
                         "state": state, "code": r.code,
                         "cmd": " ".join(r.check.cmd),
                         "output_tail": r.output[-1200:]})
        if state == "fail":
            blockers.append(f"{r.check.label}: код возврата {r.code}")
        elif state == "empty":
            blockers.append(f"{r.check.label}: прогон был, но не проверено ничего")

    failed = [e for e in evidence if e["state"] == "fail"]
    empty = [e for e in evidence if e["state"] == "empty"]
    ran = [e for e in evidence if e["state"] != "unknown"]
    if failed:
        status = "fail"
        nxt = f"починить: {failed[0]['label']} ({failed[0]['cmd']})"
    elif not ran:
        # Проверки объявлены, но ни одна не запускалась. Это НЕ «проверять
        # нечем по вине проекта» — это «нечем проверить на этой машине».
        status = "absent"
        nxt = (f"поставить, чем запускать: {unrunnable[0].reason} "
               f"({' '.join(unrunnable[0].check.cmd)})")
    elif empty and len(empty) == len(ran):
        status = "absent"
        nxt = "проверки запускаются, но ни одного теста нет — закрывать нечем"
    elif empty:
        status = "fail"
        nxt = f"{empty[0]['label']}: прогон пустой, добавить тест"
    elif unrunnable:
        # Зелёное рядом с непроверенным не даёт зелёного вердикта: иначе одна
        # прошедшая проверка закрывает ход за ту, которую никто не запускал.
        status = "absent"
        nxt = f"осталось непроверенным: {unrunnable[0].check.label} — {unrunnable[0].reason}"
    else:
        status = "pass"
        nxt = "гейт пройден: можно закрывать ход"

    return {"gate": "verify", "status": status, "project": str(project),
            "blockers": blockers, "evidence": evidence, "next": nxt}


HEAD = {"pass": "ПРОШЛО", "fail": "НЕ ПРОШЛО", "absent": "ПРОВЕРЯТЬ НЕЧЕМ"}
EXIT = {"pass": 0, "fail": 1, "absent": 2}


def human(v: dict) -> str:
    lines = [HEAD.get(v["status"], v["status"])]
    for e in v["evidence"]:
        mark = {"pass": "+", "fail": "x", "empty": "?"}.get(e["state"], "?")
        lines.append(f"  {mark} {e['label']}  ({e['cmd']})")
    for b in v["blockers"]:
        lines.append(f"  ! {b}")
    if v.get("gave_up"):
        lines.append(f"  СТОП: заход №{v.get('attempt')} тем же способом — смени подход")
    lines.append(f"  дальше: {v['next']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# потолок попыток — отдельный слой поверх вердикта, статус не трогает
# --------------------------------------------------------------------------

def _state_path() -> Path:
    """Куда класть счётчик попыток. Смотри STATE_ENV наверху файла: путь
    обязан приходить из переменной окружения ради герметичности тестов."""
    raw = os.environ.get(STATE_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "superstack" / "verify-attempts.json"


def _load_attempts(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save_attempts(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        # Бухгалтерия попыток — не смысл существования гейта: инструмент,
        # упавший из-за собственного счётчика, хуже инструмента без счётчика
        # вовсе (тот же довод, что для записи журнала в tools/log.py).
        pass


def apply_attempt_ceiling(v: dict, project: Path, state_path: Path) -> dict:
    """Считает подряд идущие провалы ОДНОГО проекта и после MAX_ATTEMPTS
    подменяет "next": вместо "почини" — явная сдача с именем того, что не
    сошлось, и требованием сменить подход, а не повторить тот же заход.

    Статус и код возврата НЕ меняются — контракт 0/1/2 остаётся ровно тем
    же самым (см. tests/test_verify.py::test_exit_codes_are_distinct);
    решение "продолжать ли цикл починки" читает "next" и флаг gave_up,
    а не новый статус.

    Сброс — ТОЛЬКО на "pass". Не на "absent": "проверять нечем" — другое
    состояние (заведи тест), а не починка, которая сошлась; сбрасывать им
    серию провалов значило бы прятать её за временно недоступным
    прогонщиком (pytest не установлен на минуту — и три провала забыты).
    """
    state = _load_attempts(state_path)
    key = str(project)

    if v["status"] == "pass":
        if key in state:
            del state[key]
            _save_attempts(state_path, state)
        return v

    if v["status"] != "fail":
        return v          # "absent" серию не двигает — ни вперёд, ни назад

    attempt = int(state.get(key, 0)) + 1
    state[key] = attempt
    _save_attempts(state_path, state)

    v = dict(v)
    v["attempt"] = attempt
    v["gave_up"] = attempt >= MAX_ATTEMPTS
    if v["gave_up"]:
        stuck = v["blockers"][0] if v["blockers"] else v["next"]
        v["next"] = (
            f"сдаюсь чинить тем же способом: {attempt}-й заход подряд не "
            f"сходится на «{stuck}». Нужен другой подход или уточнение "
            f"задачи от человека — повтор того же самого не поможет."
        )
    return v


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = Path.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        print(f"ОСТАНОВЛЕНО: система на паузе\n  флаг: {flag}\n"
              f"  снять: tools/pause.sh off", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    args = [a for a in sys.argv[1:] if a != "--json"]
    quiet = "--json" in sys.argv[1:]
    if len(args) > 1:
        print("вызов: verify.py [--json] [каталог-проекта]", file=sys.stderr)
        return 3
    project = Path(args[0]).expanduser().resolve() if args else Path.cwd()
    if not project.is_dir():
        print(f"НЕ УДАЛОСЬ: каталога нет — {project}", file=sys.stderr)
        return 3

    results = [_run(c, project) for c in detect_checks(project)]
    v = verdict(results, project, tuple(unrunnable_checks(project)))
    v = apply_attempt_ceiling(v, project, _state_path())
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return EXIT.get(v["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
