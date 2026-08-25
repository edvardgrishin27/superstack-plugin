#!/usr/bin/env python3
"""Тесты маршрутизации журнала находок (tools/learn.py).

Что эти тесты обязаны держать — и почему именно это.

learn.py — единственный инструмент домена, который ПИШЕТ на диск. Без отбора
самоулучшение превращается в накопление: 254 скилла на диске автора при
реальном использовании 33 — доказательство лежит в докстринге самого модуля.
Здесь проверяется вторая половина отбора: КУДА едет прошедшая планку находка
и не плодит ли она дубль, если та же находка встретилась ещё раз в чуть
другой формулировке.

  1. МАРШРУТИЗАЦИЯ — не один адресат. Факт про проект, конвенция для файлов
     и процедура, повторённая трижды, обязаны расходиться по разным меткам
     destination — иначе триаж ничего не решает.
  2. СЛИЯНИЕ ПО СХОЖЕСТИ — не только по точному id. Другая формулировка
     заголовка (падеж, регистр, точка на конце) обязана поднимать счётчик
     существующей записи, а не создавать вторую с другим id.
  3. ГЕРМЕТИЧНОСТЬ. HOME подставной, настоящий ~/.claude не читается и не
     пишется. Хранилище SHARED (data/knowledge/learned внутри репозитория)
     тестами не трогается вовсе — только --scope local, изолированный через
     HOME. Часы не участвуют в маршрутизации, поэтому дата параметром здесь
     не нужна (в отличие от memory_lint).
  4. ОЖИДАЕМОЕ НЕ БЕРЁТСЯ ИЗ ПРОВЕРЯЕМОГО КОДА. Коды возврата, номера порога
     подтверждений и текст меток написаны в тесте литералами.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at  # noqa: E402

ROOT = REPO
TOOL = at("tools", "learn.py")

BASE_ENV = {
    "SUPERSTACK_IGNORE_PAUSE": "1",
    "PATH": "",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "NO_COLOR": "1",
}

#: Порог подтверждений для маршрута "skill" — совпадает с SKILL_CONFIRMATIONS
#: в tools/learn.py, но записан здесь литералом: сверка с константой модуля
#: прошла бы при любом значении константы и ничего не доказывала бы.
SKILL_CONFIRMATIONS = 3


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


learn = _load("ss_learn", TOOL)


class LearnFixture(unittest.TestCase):
    """HOME подставной: LOCAL-хранилище (~/.claude/superstack/learned)
    живёт внутри temp и исчезает с тестом. SHARED (внутри репозитория) не
    трогается ни разу — ни один тест здесь не запрашивает --scope shared
    и не вызывает promote."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(parents=True)
        self.local_dir = self.home / ".claude" / "superstack" / "learned"

    def tearDown(self):
        self.tmp.cleanup()

    def run_add(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(BASE_ENV)
        env["HOME"] = str(self.home)
        return subprocess.run(
            [sys.executable, str(TOOL), "add", *args],
            capture_output=True, text=True, timeout=60, env=env)

    def add(self, title: str, *, kind: str = None, paths: str = None,
            check: str = "тест", failure: str = "паттерн отказа",
            deadend: str = "тупик") -> subprocess.CompletedProcess:
        args = ["--title", title, "--check", check,
                "--failure", failure, "--deadend", deadend]
        if kind is not None:
            args += ["--kind", kind]
        if paths is not None:
            args += ["--paths", paths]
        r = self.run_add(*args)
        self.assertNotIn("Traceback", r.stderr, r.stderr)
        return r

    def entries(self) -> list:
        if not self.local_dir.is_dir():
            return []
        return [json.loads(f.read_text(encoding="utf-8"))
                for f in sorted(self.local_dir.glob("*.json"))]

    def only_entry(self) -> dict:
        es = self.entries()
        self.assertEqual(len(es), 1, es)
        return es[0]


# ---------------------------------------------------------------------------
class TestRouteIsAPureClassifier(unittest.TestCase):
    """route() проверяется напрямую — без подпроцесса и без диска: это
    чистая функция трёх аргументов, и порядок её решений (paths сильнее
    счётчика) — то, что должно быть видно без всей машинерии cmd_add."""

    def test_fact_with_no_paths_goes_to_memory(self):
        self.assertEqual(learn.route("fact", [], 1), "memory")

    def test_paths_route_to_rule_regardless_of_kind(self):
        self.assertEqual(learn.route("fact", ["src/a.py"], 1), "rule")
        self.assertEqual(learn.route("procedure", ["src/a.py"], 5), "rule")

    def test_procedure_below_threshold_stays_memory(self):
        self.assertEqual(learn.route("procedure", [], SKILL_CONFIRMATIONS - 1), "memory")

    def test_procedure_at_threshold_routes_to_skill(self):
        self.assertEqual(learn.route("procedure", [], SKILL_CONFIRMATIONS), "skill")

    def test_fact_never_routes_to_skill_no_matter_confirmations(self):
        """Счётчик один и тот же не превращает факт в процедуру: только
        заявленный --kind procedure открывает дорогу к скиллу."""
        self.assertEqual(learn.route("fact", [], 99), "memory")

    def test_paths_beats_procedure_confirmations(self):
        """Порядок проверок задан явно: paths — заявление автора и оно
        сильнее счётчика повторений, даже когда процедура уже созрела."""
        self.assertEqual(learn.route("procedure", ["src/a.py"], SKILL_CONFIRMATIONS), "rule")


# ---------------------------------------------------------------------------
class TestRoutingThroughCli(LearnFixture):
    """Тот же route(), но через реальный cmd_add: маршрут обязан быть виден
    в записанном JSON (поле destination), а не только в возвращаемом коде."""

    def test_plain_fact_is_routed_to_memory_silently(self):
        r = self.add("Факт про архитектуру проекта")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.only_entry()["destination"], "memory")
        self.assertNotIn("МАРШРУТ", r.stdout)

    def test_paths_route_the_entry_to_rule_and_say_so(self):
        r = self.add("Money-объект в наивной арифметике молча ломается",
                      paths="src/money.py,src/pricing.py")
        self.assertEqual(r.returncode, 0)
        entry = self.only_entry()
        self.assertEqual(entry["destination"], "rule")
        self.assertEqual(entry["paths"], ["src/money.py", "src/pricing.py"])
        self.assertIn("МАРШРУТ", r.stdout)
        self.assertIn("ПРАВИЛО", r.stdout)

    def test_procedure_reaches_skill_route_only_on_third_confirmation(self):
        """Первые два раза — память (счётчик ещё не дорос), третий раз (два
        подтверждения по точному id + исходная запись = 3) — скилл."""
        title = "getCurrentPositionAsync в помещении виснет, а не падает"
        r1 = self.add(title, kind="procedure")
        self.assertEqual(self.only_entry()["destination"], "memory")
        self.assertNotIn("МАРШРУТ", r1.stdout)

        r2 = self.add(title, kind="procedure")
        self.assertIn("ПОДТВЕРЖДЕНО повторно", r2.stdout)
        self.assertEqual(self.only_entry()["confirmations"], 2)
        self.assertEqual(self.only_entry()["destination"], "memory")

        r3 = self.add(title, kind="procedure")
        entry = self.only_entry()
        self.assertEqual(entry["confirmations"], 3)
        self.assertEqual(entry["destination"], "skill")
        self.assertIn("МАРШРУТ", r3.stdout)
        self.assertIn("СКИЛЛ", r3.stdout)


# ---------------------------------------------------------------------------
class TestFindSimilarEntry(unittest.TestCase):
    """find_similar_entry() напрямую: похожий заголовок находится, разный —
    нет. Пороговое значение записано литералом, не взято из модуля."""

    def test_near_duplicate_title_is_found(self):
        existing = [{"id": "aaa111", "title": "Клавиатура перекрывает поле ввода в чатах"}]
        found = learn.find_similar_entry(existing, "клавиатура перекрывает поле ввода в чате.")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "aaa111")

    def test_unrelated_title_is_not_found(self):
        existing = [{"id": "aaa111", "title": "Клавиатура перекрывает поле ввода в чатах"}]
        found = learn.find_similar_entry(existing, "Money-объект в наивной арифметике молча ломается")
        self.assertIsNone(found)

    def test_empty_list_returns_none(self):
        self.assertIsNone(learn.find_similar_entry([], "что угодно"))

    def test_custom_threshold_is_honoured(self):
        """Порог — параметр функции, а не зашитое число: тест обязан суметь
        подвинуть его и увидеть другой результат, иначе это не порог."""
        existing = [{"id": "aaa111", "title": "теплое и мягкое"}]
        found_lenient = learn.find_similar_entry(existing, "тёплое и мягкое", threshold=0.5)
        found_strict = learn.find_similar_entry(existing, "совершенно другой текст", threshold=0.99)
        self.assertIsNotNone(found_lenient)
        self.assertIsNone(found_strict)


# ---------------------------------------------------------------------------
class TestMergeInsteadOfDuplicate(LearnFixture):
    """Сквозной сценарий через CLI: перефразированный заголовок обязан
    поднять confirmations существующей записи, а не создать вторую."""

    def test_rephrased_title_merges_into_existing_entry_not_a_new_one(self):
        self.add("Клавиатура перекрывает поле ввода в чатах")
        r = self.add("клавиатура перекрывает поле ввода в чате.")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("СОЧТЕНО ПОВТОРОМ", r.stdout)
        es = self.entries()
        self.assertEqual(len(es), 1, "перефразировка не должна плодить вторую запись: " + str(es))
        self.assertEqual(es[0]["confirmations"], 2)

    def test_unrelated_second_title_creates_a_second_entry(self):
        """Обратный контроль: слияние не должно срабатывать на несвязанных
        находках только потому, что обе — обычный текст на русском."""
        self.add("Клавиатура перекрывает поле ввода в чатах")
        r = self.add("Money-объект в наивной арифметике молча ломается")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("СОЧТЕНО ПОВТОРОМ", r.stdout)
        self.assertEqual(len(self.entries()), 2)


if __name__ == "__main__":
    unittest.main()
