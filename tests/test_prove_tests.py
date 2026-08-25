#!/usr/bin/env python3
"""Доказать, что тесты ПРОЕКТА могли быть красными.

Механизм найден на живом прогоне, а не выведен. Агент-исполнитель вернул
`3 passed (0 failed)`; тесты стояли на названном шве, ожидаемые значения были
литералами — по всем признакам добротно. Заменил зависимость `notify.send` на
заглушку, возвращающую ту же форму и НЕ ОТПРАВЛЯЮЩУЮ НИЧЕГО, — прогон остался
`3 passed, 0 failed`.

Критерий «форма отправляется» был выполнен по букве и обойдён по сути: тест
утверждал `result.ok === true`, а `ok` выставляет вызывающий код, а не факт
доставки. Из счётчика прошедших это невидимо в принципе: он отвечает, сколько
тестов выполнилось, а не мог ли хоть один упасть.

Три отказа здесь важнее самой проверки, и каждый — про то, как эта проверка
превращается в театр: красный набор ДО поломок, пустой набор поломок, и поломка,
пережившая прогон.
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

from paths import at

PT = at("tools", "prove_tests.py")
_s = importlib.util.spec_from_file_location("superstack_prove_tests", PT)
pt = importlib.util.module_from_spec(_s)
_s.loader.exec_module(pt)

#: Крошечный проект на голом python3: зависимость, код и тест, который её
#: НЕ проверяет — ровно форма найденного дефекта.
DEP_REAL = "def send(text):\n    open('sent.log', 'a').write(text)\n    return True\n"
DEP_SILENT = "def send(text):\n    return True\n"
CODE = ("from dep import send\n\n"
        "def submit(name):\n"
        "    if not name:\n"
        "        return {'ok': False, 'error': 'нужно имя'}\n"
        "    send(name)\n"
        "    return {'ok': True}\n")
WEAK_TEST = ("from code_ import submit\n\n"
             "def test_ok():\n"
             "    assert submit('Анна')['ok'] is True\n\n"
             "def test_empty():\n"
             "    assert submit('')['ok'] is False\n")


class Project(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "dep.py").write_text(DEP_REAL, encoding="utf-8")
        (self.root / "code_.py").write_text(CODE, encoding="utf-8")
        (self.root / "test_it.py").write_text(WEAK_TEST, encoding="utf-8")
        (self.root / ".superstack").mkdir()

    def spec(self, **over):
        d = {"test_cmd": f"{sys.executable} -m pytest -q",
             "mutations": [{"id": "dep.silent", "file": "dep.py",
                            "stub": DEP_SILENT,
                            "why": "отправка молчит, форма ответа та же"}]}
        d.update(over)
        return d


class TestTheSilentDependency(Project):
    """Тот самый случай, ради которого инструмент написан."""

    def test_a_test_that_does_not_check_delivery_lets_the_stub_survive(self):
        v = pt.run(self.root, self.spec())
        self.assertEqual(v["status"], "fail", v)
        self.assertEqual([s["id"] for s in v["survived"]], ["dep.silent"])

    def test_a_test_that_checks_delivery_catches_it(self):
        (self.root / "test_it.py").write_text(
            WEAK_TEST + "\n\ndef test_delivered(tmp_path, monkeypatch):\n"
            "    monkeypatch.chdir(tmp_path)\n"
            "    submit('Анна')\n"
            "    assert (tmp_path / 'sent.log').exists()\n", encoding="utf-8")
        v = pt.run(self.root, self.spec())
        self.assertEqual(v["status"], "pass", v)

    def test_the_file_comes_back_byte_exact(self):
        before = (self.root / "dep.py").read_bytes()
        pt.run(self.root, self.spec())
        self.assertEqual((self.root / "dep.py").read_bytes(), before)

    def test_a_find_replace_mutation_works_too(self):
        v = pt.run(self.root, self.spec(mutations=[
            {"id": "empty-name-accepted", "file": "code_.py",
             "find": "if not name:", "replace": "if False:",
             "why": "пустое имя проходит — тест обязан упасть"}]))
        self.assertEqual(v["status"], "pass", v)

    def test_a_missing_anchor_is_unknown_not_pass(self):
        v = pt.run(self.root, self.spec(mutations=[
            {"id": "нет-такого", "file": "code_.py",
             "find": "такой строки нет", "replace": "x", "why": "—"}]))
        self.assertEqual(v["status"], "unknown", v)


class TestTheThreeWaysThisBecomesTheatre(Project):

    def test_a_red_suite_before_the_mutations_is_not_a_measurement(self):
        """Если проект красный сам по себе, любая поломка «поймана», и отчёт
        будет блестящим при полном отсутствии проверки."""
        (self.root / "test_it.py").write_text(
            WEAK_TEST + "\n\ndef test_broken():\n    assert False\n",
            encoding="utf-8")
        v = pt.run(self.root, self.spec())
        self.assertEqual(v["status"], "unknown")
        self.assertIn("красный ДО поломок", v["detail"])

    def test_zero_mutations_is_not_a_pass(self):
        """«Не проверяли» и «тесты держат» — разные утверждения, и второе тут
        не доказано ничем."""
        v = pt.run(self.root, self.spec(mutations=[]))
        self.assertEqual(v["status"], "unknown")
        self.assertIn("не проверяли", v["detail"])
        self.assertTrue(v.get("next"))

    def test_missing_test_command_is_unknown(self):
        v = pt.run(self.root, self.spec(test_cmd=""))
        self.assertEqual(v["status"], "unknown")

    def test_the_tree_survives_a_crashing_test_command(self):
        pt.run(self.root, self.spec(test_cmd="такой-команды-нет"))
        self.assertEqual((self.root / "dep.py").read_text("utf-8"), DEP_REAL)


class TestTheLock(Project):

    def test_a_live_holder_blocks(self):
        (self.root / ".superstack" / ".mutation-lock").write_text("1\n",
                                                                  encoding="utf-8")
        held = pt.acquire(self.root)
        self.assertIsNotNone(held)
        self.assertIn("уже ломает", held)

    def test_a_dead_holder_does_not_block_forever(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        (self.root / ".superstack" / ".mutation-lock").write_text(
            f"{p.pid}\n", encoding="utf-8")
        self.assertIsNone(pt.acquire(self.root))

    def test_release_frees_it(self):
        pt.acquire(self.root)
        pt.release(self.root)
        self.assertFalse((self.root / ".superstack" / ".mutation-lock").exists())


class TestExitCodes(Project):

    def _run(self, spec):
        (self.root / ".superstack" / "mutations.json").write_text(
            json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(PT), str(self.root), "--json"],
                              capture_output=True, text=True, timeout=600, env=env)

    def test_survivor_exits_one(self):
        self.assertEqual(self._run(self.spec()).returncode, 1)

    def test_unmeasurable_exits_two(self):
        self.assertEqual(self._run(self.spec(mutations=[])).returncode, 2)

    def test_missing_set_exits_three(self):
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}
        p = subprocess.run([sys.executable, str(PT), str(self.root)],
                           capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(p.returncode, 3)


if __name__ == "__main__":
    unittest.main()
