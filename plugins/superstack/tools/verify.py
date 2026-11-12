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
  5. ОТЧЁТ ПРОГОНЩИКА ПЕРЕВЕШИВАЕТ СЛОВА ИЗ ЧУЖОГО ТЕСТА. Признак пустоты,
     найденный где угодно в выводе, — это в том числе тело чужого assert.
     Набор, о падениях которого прогонщик сообщил САМ, остаётся провалом при
     любых словах в его выводе; прогон, который прочитать не вышло, честно
     называется непрочитанным, а не зелёным.

  python3 verify.py [каталог-проекта]        -> вердикт человеку + JSON в stdout
  python3 verify.py --json [каталог]         -> только JSON

  код 0 — прошло, 1 — не прошло, 2 — проверять нечем, 3 — ошибка вызова

  Потолок попыток. Гейт запускается заново на каждый вызов — сам по себе он
  не помнит, что чинил это же самое три раза подряд. Счётчик подряд идущих
  провалов ОДНОЙ И ТОЙ ЖЕ поломки ОДНОГО проекта живёт на диске (путь — из
  SUPERSTACK_VERIFY_STATE). Смена поломки начинает счёт заново: сборка,
  упавшая дважды, а затем красный линт — это НЕ третий заход одной серии.
  Повтор БЕЗ изменений в дереве счётчик не двигает вовсе — гейт зовут минимум
  из трёх мест на один и тот же ход (фаза 5 напрямую, планка через builtin
  verify, Stop-хук verify-gate.py), и без починки между вызовами это тот же
  прогон, а не новая попытка. После MAX_ATTEMPTS заходов подряд гейт
  подменяет "next" в вердикте с "почини" на явную сдачу: заход тем же
  способом не сходится, нужен другой подход или уточнение задачи от
  человека. Статус и коды возврата это НЕ меняет — "next" читает тот, кто
  решает, продолжать ли цикл починки, а не машина.

  Раскладка. Здесь — что запускать, как это прочитать и какой код вернуть.
  Рядом два соседа, каждый со своим контрактом:

    tools/verify_report.py   — что о своём наборе сказал САМ прогонщик
    tools/verify_attempts.py — потолок попыток и его состояние на диске

  Оба входят в подпись проверяющего (`_ЧАСТИ`): правка в любом из них
  обесценивает прошлое зелёное так же, как правка гейта.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Соседние модули лежат в этом же каталоге, но при загрузке файлом
# (spec_from_file_location — так его берут и тесты, и Stop-хук) каталог скрипта
# в sys.path не попадает: без явной вставки «import verify_report» упал бы там,
# где при обычном запуске работает. Тот же приём, что у соседних инструментов.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Имена перечислены полностью, а не «что нужно прямо здесь»: гейт остаётся
# ЕДИНЫМ адресом для тех, кто зовёт его снаружи (tools/what.py, Stop-хук,
# тесты), и переезд куска внутрь соседнего модуля не имеет права менять этот
# адрес. Часть имён отсюда не вызывается — это re-export, а не мусор.
import atomic_store  # noqa: E402
import verify_attempts  # noqa: E402
import verify_report  # noqa: E402
from verify_attempts import (  # noqa: E402,F401
    MAX_ATTEMPTS, STATE_ENV, _FINGERPRINT_SKIP,
    _failure_signature, _finalize, _give_up_text, _load_attempts,
    _save_attempts, _series, _state_path, _tree_fingerprint,
    apply_attempt_ceiling,
)
from verify_report import (  # noqa: E402,F401
    EMPTY_MARKERS, STUB_MARKERS,
    REPORT_EMPTY, REPORT_FAILED, REPORT_RAN, REPORT_UNCLEAR,
    _is_pytest, _nothing_was_checked, _pytest_collected, _pytest_report,
    _pytest_summary, _read_the_run,
)

TIMEOUT = 900          # 15 минут на одну проверку
OUTPUT_KEEP = 4000     # сколько знаков вывода оставляем как улику


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
    unclear: bool = False   # прогон был, а его отчёт разобрать не вышло


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
    read = _read_the_run(check, code, out)
    return Result(check, code, out[-OUTPUT_KEEP:],
                  read == REPORT_EMPTY, read == REPORT_UNCLEAR)


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
        # Отчёт прогонщика разобрать не вышло — это не «зелено». Гасится
        # ТОЛЬКО успех: понизить провал до «не смог проверить» значило бы
        # отпереть ход перед красным набором, то есть вернуть чинимое.
        if state == "pass" and r.unclear:
            state = "unclear"
        evidence.append({"check": r.check.id, "label": r.check.label,
                         "state": state, "code": r.code,
                         "cmd": " ".join(r.check.cmd),
                         "output_tail": r.output[-1200:]})
        if state == "fail":
            blockers.append(f"{r.check.label}: код возврата {r.code}")
        elif state == "empty":
            blockers.append(f"{r.check.label}: прогон был, но не проверено ничего")
        elif state == "unclear":
            blockers.append(f"{r.check.label}: прогон был, но его отчёт не разобран")

    failed = [e for e in evidence if e["state"] == "fail"]
    empty = [e for e in evidence if e["state"] == "empty"]
    unclear = [e for e in evidence if e["state"] == "unclear"]
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
        # Пустая проверка рядом с зелёными — «не смог проверить», а НЕ провал.
        # Разница не в строгости, а в диагнозе: провал означает «код сломан,
        # чини код», пустота — «доказательства нет, заведи тест». Отправив
        # человека чинить работающий код, гейт тратит его время и учит себе
        # не верить. Код возврата 2 держит ход закрытым ровно так же — зелёным
        # это не становится.
        status = "absent"
        nxt = f"{empty[0]['label']}: прогон был, но проверять нечего — завести тест"
    elif unclear:
        # Третий диагноз, а не округление до двух: не «чини код» и не «заведи
        # тест», а «прочитать прогонщика не вышло». Ход держится закрытым тем
        # же кодом 2 — зелёным непрочитанное не становится.
        status = "absent"
        nxt = (f"{unclear[0]['label']}: отчёт прогонщика не разобран — "
               f"доказательства нет ({unclear[0]['cmd']})")
    elif unrunnable:
        # Зелёное рядом с непроверенным не даёт зелёного вердикта: иначе одна
        # прошедшая проверка закрывает ход за ту, которую никто не запускал.
        status = "absent"
        nxt = f"осталось непроверенным: {unrunnable[0].check.label} — {unrunnable[0].reason}"
    elif not _a_test_passed(evidence):
        # Правило 1 из шапки: проверка поведения — это тест. Сборка, линт и
        # типы говорят, что код собирается, но не что он делает то, что надо.
        # Проект, объявивший только их, не получает «прошло».
        status = "absent"
        blockers.append("ни одной проверки с меткой «тесты»")
        nxt = "собирается, но поведение не проверено — завести хотя бы один тест"
    else:
        status = "pass"
        nxt = "гейт пройден: можно закрывать ход"

    return {"gate": "verify", "status": status, "project": str(project),
            "blockers": blockers, "evidence": evidence, "next": nxt}


def _a_test_passed(evidence: list) -> bool:
    """Есть ли среди улик прошедшая проверка с меткой «тесты»."""
    return any(e["label"] == "тесты" and e["state"] == "pass" for e in evidence)


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
# отпечаток, при котором ЗЕЛЁНОЕ МОЖНО НЕ ПЕРЕПРОВЕРЯТЬ
# --------------------------------------------------------------------------
# У отпечатка ДВА потребителя, и требования у них разные.
#
# Счётчику попыток (`apply_attempt_ceiling`) хватает грубого: он решает,
# тратить ли попытку, и худшее, что бывает, — потрачена лишняя. Ему остаётся
# `_tree_fingerprint`, ровно тот, что был.
#
# Stop-хук (hooks/verify-gate.py) решает другое — проверять ли ВООБЩЕ. Его
# молчание — утверждение о РАВЕНСТВЕ ДВУХ СОСТОЯНИЙ, а такое утверждение
# требует знать входы, а не только дерево. `_tree_fingerprint` их не знает:
# он собран из `git diff HEAD` и `git ls-files -o --exclude-standard`, то
# есть уважает `.gitignore` — а `.env.local`, от которого зависит прогон,
# лежит именно там. Измерено: значение в нём менялось с 1 на 0, дерево для
# git оставалось прежним, хук возвращал ноль и закрывал ход, а свежий прогон
# в этом же состоянии падал. Поэтому у переиспользования отпечаток свой.

#: Файлы, которые проверка читает, а git о них молчит: `.env` и `.env.*`
#: лежат в `.gitignore` почти всегда и почти всегда меняют результат прогона.
#: Список КОРНЕВОЙ и закрытый — рекурсивный обход всего дерева на каждом
#: закрытии хода стоил бы дороже самой проверки; проект, у которого входы
#: лежат глубже, объявляет их сам (см. `_INPUTS_DECL`).
_INPUT_GLOBS = (".env", ".env.*")

#: Где проект объявляет собственные входы — та же папка и та же форма, что у
#: остальных его объявлений (`.superstack/*.json`):
#:     {"inputs": ["secrets/*.ini", "конфиг/боевой.yaml"]}
#: Сам файл объявления тоже считается входом: иначе смена объявления в
#: игнорируемой git папке прошла бы мимо отпечатка.
_INPUTS_DECL = ".superstack/verify-inputs.json"

#: Потолок на число файлов-входов. Объявление вида `**/*` иначе вешало бы
#: Stop-хук на каждом закрытии хода. Перебор потолка — не «входов нет», а
#: «набор входов снять не вышло»: зелёное после него не переиспользуется.
_INPUTS_MAX = 200

#: Вход бывает и мегабайтным фикстурным файлом — читаем кусками, целиком в
#: память не берём.
_INPUT_CHUNK = 1 << 20

#: Схема отпечатка. Живёт внутри него и меняется вместе с составом того, что
#: в него входит: отпечаток прошлой схемы не имеет права совпасть с нынешним.
_REUSE_SCHEMA = "superstack.reuse-fingerprint.v1"


def _declared_inputs(project: Path) -> "list | None":
    """Файлы-входы этого проекта: свои по умолчанию плюс объявленные им.

    None — набор входов НЕИЗВЕСТЕН: объявление есть, но не разбирается; путь
    в нём ведёт наружу проекта; входов больше потолка. Читать None как
    «входов нет» нельзя — это разница между «проверил» и «не смотрел».
    """
    шаблоны = [_INPUTS_DECL, *_INPUT_GLOBS]
    объявление = project / _INPUTS_DECL
    if объявление.is_file():
        try:
            объявлено = json.loads(объявление.read_text("utf-8"))["inputs"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(объявлено, list) or \
                not all(isinstance(s, str) and s for s in объявлено):
            return None
        шаблоны += объявлено
    найдено: set = set()
    for шаблон in шаблоны:
        # Абсолютный путь и `..` уводят отпечаток за пределы проекта, то есть
        # туда, о чём он ничего не обещает. Это не «пусто», а «неизвестно».
        if шаблон.startswith("/") or ".." in Path(шаблон).parts:
            return None
        try:
            for p in project.glob(шаблон):
                if p.is_file():
                    найдено.add(p)
                    if len(найдено) > _INPUTS_MAX:
                        return None
        except (OSError, ValueError, IndexError):
            return None
    return sorted(найдено)


def _inputs_digest(project: Path) -> "str | None":
    """Свод входов: имя файла и sha256 его содержимого — и ничего больше.

    СОДЕРЖИМОЕ ВХОДОВ — СЕКРЕТЫ ЧЕЛОВЕКА. Наружу оно не выходит нигде: в
    строку идёт только хэш, а сама строка сворачивается в один хэш в
    `_reuse_fingerprint`. Ни значения, ни фрагмента нет ни в отпечатке, ни в
    вердикте, ни в журнале, ни в выводе хука.

    None — набор входов неизвестен или файл прочитать не вышло: равенство
    состояний в этом случае утверждать нечем.
    """
    пути = _declared_inputs(project)
    if пути is None:
        return None
    свод = []
    for p in пути:
        цифра = hashlib.sha256()
        try:
            with p.open("rb") as f:
                for кусок in iter(lambda: f.read(_INPUT_CHUNK), b""):
                    цифра.update(кусок)
            имя = p.relative_to(project).as_posix()
        except (OSError, ValueError):
            return None
        свод.append(f"{имя}:{цифра.hexdigest()}")
    return "\x1f".join(sorted(свод))


def _declared_commands(project: Path) -> str:
    """Список и текст объявленных проверок — тоже часть отпечатка.

    Смена команды меняет утверждение о проекте, даже когда ни один
    отслеживаемый файл не тронут: команду мог сменить и сам плагин. Берутся
    ОБЪЯВЛЕННЫЕ проверки, а не запускаемые: наличие прогонщика на машине —
    состояние машины, а его отпечаток не обещает (см. `_reuse_fingerprint`).
    """
    return "\x1f".join(f"{c.id} {' '.join(c.cmd)}" for c in _all_checks(project))


#: Из чего собран проверяющий, КРОМЕ этого файла. Вердикт считают три файла,
#: и подпись обязана покрывать все три: правка адаптера отчёта или потолка
#: попыток меняет то, ЧЕМ проверяли, ровно так же, как правка гейта.
#:
#: Список стоит здесь, а не выводится из sys.modules, ровно затем, чтобы
#: следующее разрезание гейта на части нельзя было провести молча: имя нового
#: соседа приходится дописать сюда, а забытое имя ловит проверка (см.
#: tests/test_verify.py::ПодписьПроверяющего). Пока подпись бралась с одного
#: файла, вынос половины гейта в соседний модуль выключил бы половину этого
#: механизма — механизм остался бы на месте и перестал бы что-либо покрывать.
#:
#: `atomic_store` вердикт не считает — им пишется запись вердикта. Он в
#: списке по тому же правилу «всё, что гейт берёт из пакета»: лишняя
#: перепроверка после его правки дешевле исключения из правила.
_ЧАСТИ = (verify_attempts, verify_report, atomic_store)


def _verifier_identity() -> "str | None":
    """Кто именно проверял: версия плагина и хэш ИСХОДНИКОВ самой проверки.

    Обновление плагина обязано обесценивать прошлое зелёное — тот же набор,
    прогнанный ДРУГИМ кодом, это другое доказательство. Версия из манифеста
    ловит обновление, не тронувшее эти файлы; хэш исходников ловит правку, не
    тронувшую версию. Манифест лежит по соседству и может отсутствовать
    (файл запустили в одиночку) — тогда версия честно называется «?», а не
    подменяется догадкой; хэш исходников при этом остаётся.

    Исходников несколько и порядок их закреплён (`_ЧАСТИ`): хэш, зависящий от
    порядка обхода, менялся бы сам по себе и обесценивал зелёное на ровном
    месте.

    None — хоть один исходник прочитать не вышло: сказать, кто проверял,
    нечем, и переиспользовать зелёное не на чем. Молчаливый пропуск
    непрочитанной части был бы подписью, которая покрывает не всё, о чём
    молчит.
    """
    я = Path(__file__).resolve()
    цифра = hashlib.sha256()
    try:
        for исходник in (я, *(Path(ч.__file__).resolve() for ч in _ЧАСТИ)):
            цифра.update(исходник.read_bytes())
    except (OSError, TypeError):
        return None
    try:
        манифест = json.loads((я.parent.parent / ".claude-plugin" /
                               "plugin.json").read_text("utf-8"))
        версия = str(манифест["version"])
    except (OSError, ValueError, KeyError, TypeError):
        версия = "?"
    return f"{версия}:{цифра.hexdigest()}"


def _reuse_fingerprint(project: Path) -> "str | None":
    """Отпечаток, при котором ЗЕЛЁНОЕ МОЖНО НЕ ПЕРЕПРОВЕРЯТЬ.

    Второй потребитель отпечатка (Stop-хук) решает не «тратить ли попытку», а
    «проверять ли вообще». Одного дерева для такого решения мало, поэтому
    сюда входят четыре части:

      · дерево — `_tree_fingerprint`, ровно то же, что и у счётчика попыток;
      · объявленные проверки, их список и текст — `_declared_commands`;
      · кто проверял: версия плагина и хэш исходника — `_verifier_identity`;
      · объявленные входы, ТОЛЬКО хэшем содержимого — `_inputs_digest`.

    ЧЕГО ОТПЕЧАТОК НЕ ПОКРЫВАЕТ, сказано здесь, а не подразумевается: всё
    остальное состояние машины. Переменные среды, сеть, часы, установленные
    версии прогонщиков в него не входят и входить не могут — снять их дёшево
    и полно неоткуда. Обещание ровно одно: дерево и ОБЪЯВЛЕННЫЕ входы. Где мы
    не знаем — мы не обещаем, и решение об этом принимает не молчание.

    None — «сигнала нет»: не git-репозиторий, набор входов неизвестен, свой
    же исходник не прочитался. Каждый источник None назван отдельной строкой
    намеренно: правило «неполнота гасит переиспользование» должно быть видно
    в коде, а не следовать из совпадения. Читать None как «не менялось»
    нельзя ни при каких условиях — так неполнота знания превращается в
    утверждение о равенстве.
    """
    дерево = _tree_fingerprint(project)
    if дерево is None:
        return None
    входы = _inputs_digest(project)
    if входы is None:
        return None
    кто = _verifier_identity()
    if кто is None:
        return None
    raw = "\x1e".join([_REUSE_SCHEMA, дерево, _declared_commands(project),
                       кто, входы])
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------
# запись последнего вердикта
# --------------------------------------------------------------------------
#: Где гейт оставляет свой последний вердикт. Её читают ступень
#: самостоятельности (tools/autonomy.py), панель (tools/panel_facts.py) и
#: запись хода работ: «доказано» ставится по ней, а не по числу из вызова.
#: Пишет её только гейт, и только в проекте, где плагин уже завёл свою папку:
#: заводить служебную папку в чужом репозитории проверка не вправе.
RECORD = ".superstack/verify-last.json"


def _measured_tests(results: list) -> "int | None":
    """Сколько тестов выполнено — только если это сказал сам прогонщик.

    Число берётся из итоговой строки pytest у КАЖДОЙ проверки с меткой
    «тесты». Хоть у одной сводки нет — числа нет вовсе: половина, выданная
    за целое, читалась бы как измерение.
    """
    total = None
    for r in results:
        if r.check.label != "тесты":
            continue
        counts = _pytest_summary(r.output)
        if counts is None:
            return None
        # Пропущенный тест не выполнялся — в число он не входит.
        total = (total or 0) + sum(counts.get(w, 0) for w in
                                   verify_report._RAN_WORDS if w != "skipped")
    return total


def write_record(project: Path, v: dict, results: list,
                 fingerprint: "str | None") -> None:
    """Оставить запись вердикта. Неудача записи — не провал проверки.

    Код возврата гейта от неё не меняется. Но и молчать нельзя: без записи
    «доказано» и ступень 2 недостижимы, и человек обязан узнать почему.
    """
    if not (project / ".superstack").is_dir():
        return
    from datetime import datetime, timezone
    rec = {"schema": "superstack.verify-last.v1",
           "exit_code": EXIT.get(v["status"], 1), "status": v["status"],
           "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "fingerprint": fingerprint}
    tests = _measured_tests(results)
    if tests is not None:
        rec["tests"] = tests
    try:
        atomic_store.записать_json(project / RECORD, rec, indent=1)
    except OSError as e:
        print(f"запись вердикта не легла ({RECORD}): {e}", file=sys.stderr)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = Path.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        print(f"ОСТАНОВЛЕНО: система на паузе\n  флаг: {flag}\n"
              f"  снять: скажи «включи» или позови /stop — сама пауза не пройдёт", file=sys.stderr)
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
    args = [a for a in sys.argv[1:] if a != "--json"]
    quiet = "--json" in sys.argv[1:]
    if len(args) > 1:
        print("вызов: verify.py [--json] [каталог-проекта]", file=sys.stderr)
        return 3
    project = Path(args[0]).expanduser().resolve() if args else Path.cwd()
    if not project.is_dir():
        print(f"НЕ УДАЛОСЬ: каталога нет — {project}", file=sys.stderr)
        return 3

    # Отпечаток — ДО прогона: собственный вывод проверок (кэш pytest, отчёты
    # покрытия) не должен попасть в снимок и исказить его для следующего
    # вызова (см. докстринг _tree_fingerprint).
    fingerprint = _tree_fingerprint(project)
    # Отпечаток для записи вердикта — тот, по которому зелёное переиспользуют:
    # «доказано» обещает равенство состояний, а не только дерева.
    след = (_reuse_fingerprint(project)
            if (project / ".superstack").is_dir() else None)
    results = [_run(c, project) for c in detect_checks(project)]
    v = verdict(results, project, tuple(unrunnable_checks(project)))
    v = apply_attempt_ceiling(v, project, _state_path(), fingerprint)
    write_record(project, v, results, след)
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return EXIT.get(v["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
