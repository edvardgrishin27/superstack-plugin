#!/usr/bin/env python3
"""«Доказано» ставит прогон, а не модель.

Что здесь держится. Гейт верификации сам пишет запись своего последнего
вердикта. По этой записи, и только по ней, часть работы получает «доказано»,
ступень самостоятельности 2 открывается, а панель показывает код возврата.
Ручной «код 0» в вызове — это слово ведущего, и он даёт только «со слов».

Почему всё на настоящем прогоне. Раньше этот файл подкладывала заготовка в
тестах, а в жизни его не писал никто: тесты были зелёными, а человеку
говорили «гейт ни разу не отвечал» даже после зелёной проверки.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402
import real_gate  # noqa: E402

TOOLS = PKG / "tools"
PROGRESS = TOOLS / "progress.py"
STATE_FIELDS = TOOLS / "state_fields.py"
AUTONOMY = TOOLS / "autonomy.py"
СО_СЛОВ = "записал как «со слов»: свежей проверки нет"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Проект(unittest.TestCase):
    """Временный git-проект и свой файл счётчика попыток."""

    body = real_gate.GREEN

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = real_gate.project(Path(self.tmp.name) / "проект", self.body)
        self.attempts = Path(self.tmp.name) / "attempts.json"
        self.state = self.root / ".superstack" / "state.json"

    def gate(self) -> subprocess.CompletedProcess:
        return real_gate.run_gate(self.root, self.attempts)

    def tool(self, path: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(path), *args],
                              capture_output=True, text=True, timeout=300,
                              env=real_gate.gate_env(self.attempts))

    def statuses(self) -> dict:
        data = json.loads(self.state.read_text("utf-8"))
        return {t["id"]: t["status"] for w in data["waves"].values() for t in w}


class ГейтПишетСвоюЗапись(Проект):

    def test_a_green_run_opens_step_two_without_any_fixture(self):
        """Цепочка целиком: прогон → запись → ступень 2. Файл никто не подкладывает."""
        self.assertIsNone(real_gate.record(self.root), "запись уже лежит до прогона")
        r = self.gate()
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        rec = real_gate.record(self.root)
        self.assertIsNotNone(rec, "гейт прошёл, а записи своего вердикта не оставил")
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(rec.get("tests"), 1, "число тестов измерено, но не записано")
        self.assertTrue(rec.get("updated"))
        self.assertIsInstance(rec.get("fingerprint"), str)
        a = self.tool(AUTONOMY, str(self.root), "--can", "2")
        self.assertEqual(a.returncode, 0, a.stderr[-400:])

    def test_no_superstack_folder_means_no_record(self):
        (self.root / ".superstack").rmdir()
        self.assertEqual(self.gate().returncode, 0)
        self.assertFalse((self.root / ".superstack").exists(),
                         "гейт завёл служебную папку в чужом проекте")

    def test_a_failed_write_keeps_the_verdict_and_says_so(self):
        """Не легла запись — это не провал проверки, но и не молчание."""
        (self.root / ".superstack" / "verify-last.json").mkdir()
        r = self.gate()
        self.assertEqual(r.returncode, 0, "неудача записи поменяла вердикт")
        self.assertIn("verify-last.json", r.stderr)


class ПовторНеТратитПопытку(Проект):
    body = real_gate.RED

    def test_the_record_does_not_move_the_attempt_counter(self):
        первый = self.gate()
        второй = self.gate()
        self.assertEqual((первый.returncode, второй.returncode), (1, 1))
        self.assertIsNotNone(real_gate.record(self.root))
        self.assertEqual(json.loads(первый.stdout)["attempt"],
                         json.loads(второй.stdout)["attempt"],
                         "запись вердикта сдвинула отпечаток: повтор без правок "
                         "потратил попытку")


class ДоказаноТолькоПоЗаписи(Проект):

    def setUp(self):
        super().setUp()
        self.assertEqual(self.tool(PROGRESS, "init", str(self.state), "проба")
                         .returncode, 0)

    def proven(self) -> subprocess.CompletedProcess:
        return self.tool(PROGRESS, "task", str(self.state), "01", "часть",
                         "--wave", "1", "--status", "proven", "--exit-code", "0")

    def test_a_hand_zero_without_a_record_is_only_claimed(self):
        r = self.proven()
        self.assertEqual(r.returncode, 0, "запись не состоялась")
        self.assertEqual(self.statuses()["01"], "claimed",
                         "ручной ноль в вызове дал «доказано»")
        self.assertIn(СО_СЛОВ, r.stderr)

    def test_a_fresh_green_record_gives_proven(self):
        self.assertEqual(self.gate().returncode, 0)
        r = self.proven()
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertEqual(self.statuses()["01"], "proven", r.stderr[-300:])
        self.assertNotIn(СО_СЛОВ, r.stderr)

    def test_an_edit_after_the_run_makes_the_record_stale(self):
        self.assertEqual(self.gate().returncode, 0)
        (self.root / "tests" / "test_one.py").write_text(
            real_gate.GREEN + "\n# правка после прогона\n", encoding="utf-8")
        self.proven()
        self.assertEqual(self.statuses()["01"], "claimed",
                         "запись о другом состоянии дерева дала «доказано»")

    def test_a_red_record_gives_claimed(self):
        (self.root / "tests" / "test_one.py").write_text(real_gate.RED,
                                                         encoding="utf-8")
        self.assertEqual(self.gate().returncode, 1)
        self.proven()
        self.assertEqual(self.statuses()["01"], "claimed")


class МостФазыПроверки(Проект):
    """Фаза проверки переводит ждущие и заявленные части в «доказано» — только
    по свежей зелёной записи гейта."""

    def setUp(self):
        super().setUp()
        self.tool(PROGRESS, "init", str(self.state), "проба")
        self.tool(PROGRESS, "task", str(self.state), "01", "ждёт", "--wave", "1",
                  "--status", "waiting", "--requirements", "R01")
        self.tool(PROGRESS, "task", str(self.state), "02", "со слов",
                  "--wave", "1", "--status", "claimed")
        self.tool(PROGRESS, "task", str(self.state), "03", "в работе",
                  "--wave", "2", "--status", "running")
        self.before = self.statuses()

    def promote(self) -> subprocess.CompletedProcess:
        return self.tool(STATE_FIELDS, "promote", str(self.state))

    def test_without_a_record_nothing_changes(self):
        r = self.promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn(СО_СЛОВ, r.stderr)
        self.assertEqual(self.statuses(), self.before)

    def test_a_red_record_changes_nothing(self):
        (self.root / "tests" / "test_one.py").write_text(real_gate.RED,
                                                         encoding="utf-8")
        self.assertEqual(self.gate().returncode, 1)
        self.assertEqual(self.promote().returncode, 1)
        self.assertEqual(self.statuses(), self.before)

    def test_a_green_record_proves_waiting_and_claimed(self):
        self.assertEqual(self.gate().returncode, 0)
        r = self.promote()
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertEqual(self.statuses(),
                         {"01": "proven", "02": "proven", "03": "running"})
        data = json.loads(self.state.read_text("utf-8"))
        первая = next(t for t in data["waves"]["1"] if t["id"] == "01")
        self.assertEqual(первая.get("requirements"), ["R01"],
                         "перевод в «доказано» потерял требования части")


class ТолькоСборкаНеЗелёная(unittest.TestCase):
    """«Прошло» требует хотя бы одной прошедшей проверки с меткой «тесты»."""

    @classmethod
    def setUpClass(cls):
        cls.vf = _load("ss_verify_gate_record", TOOLS / "verify.py")

    def _result(self, cid: str, label: str):
        vf = self.vf
        return vf.Result(vf.Check(cid, label, ("true",), "проба"), 0, "", False)

    def test_build_alone_is_absent(self):
        v = self.vf.verdict([self._result("node:build", "сборка")], Path("."))
        self.assertEqual(v["status"], "absent")
        self.assertEqual(self.vf.EXIT[v["status"]], 2)
        self.assertIn("собирается, но поведение не проверено", v["next"])

    def test_lint_and_types_alone_are_absent(self):
        v = self.vf.verdict([self._result("node:lint", "линт"),
                             self._result("node:tsc", "типы")], Path("."))
        self.assertEqual(v["status"], "absent")

    def test_a_passing_test_with_a_build_is_green(self):
        v = self.vf.verdict([self._result("node:test", "тесты"),
                             self._result("node:build", "сборка")], Path("."))
        self.assertEqual(v["status"], "pass")

    def _package(self, scripts: dict) -> subprocess.CompletedProcess:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "package.json").write_text(json.dumps({"scripts": scripts}),
                                           encoding="utf-8")
        return real_gate.run_gate(root, root / "attempts.json")

    @unittest.skipUnless(__import__("shutil").which("npm"), "npm не установлен")
    def test_a_real_build_only_package_gets_code_two(self):
        r = self._package({"build": "python3 -c \"print('собрано')\""})
        self.assertEqual(r.returncode, 2, r.stderr[-400:])

    @unittest.skipUnless(__import__("shutil").which("npm"), "npm не установлен")
    def test_a_real_test_and_build_package_gets_code_zero(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "check_one.py").write_text(real_gate.GREEN, encoding="utf-8")
        (root / "package.json").write_text(json.dumps({"scripts": {
            "test": "python3 -m pytest -q check_one.py",
            "build": "python3 -c \"print('собрано')\""}}), encoding="utf-8")
        r = real_gate.run_gate(root, root / "attempts.json")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])


class СчётчикНеизвестнойФормы(Проект):
    """До 26.08 значение счётчика было числом. Такие записи живут на диске
    навсегда, и чтение их как записи с полями роняло гейт трассировкой."""

    body = real_gate.RED

    def setUp(self):
        super().setUp()
        self.attempts.write_text(json.dumps({str(self.root): 3}), encoding="utf-8")

    def test_the_gate_reads_a_number_as_no_series(self):
        r = self.gate()
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.returncode, 1, r.stderr[-400:])
        self.assertEqual(json.loads(r.stdout)["attempt"], 1)
        записано = json.loads(self.attempts.read_text("utf-8"))[str(self.root)]
        self.assertIsInstance(записано, dict, "старая запись не заменена")

    def test_the_router_reads_a_number_as_no_series(self):
        mr = _load("ss_model_" "route_gat" "e_record", TOOLS / "model_route.py")
        with mock.patch.dict(os.environ,
                             {"SUPERSTACK_VERIFY_STATE": str(self.attempts)}):
            число, _ = mr.заход(self.root)
        self.assertEqual(число, 1)


if __name__ == "__main__":
    unittest.main()
