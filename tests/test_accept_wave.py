#!/usr/bin/env python3
"""Приёмка волны одной строкой: решает код, а не чтение вывода.

Все запуски подставные: команды подменяются переменными окружения на
заготовку `fixtures/accept/fake_run.py`. Настоящий набор тестов, настоящая
выборка поломок и настоящая полная планка здесь не идут: иначе тест
запускал бы сам себя и ждал часы.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO  # noqa: E402

TOOL = REPO / "tools" / "accept_wave.py"
FAKE = REPO / "tests" / "fixtures" / "accept" / "fake_run.py"

WAVES = {"W1.S1": {"mutations": ["a.one", "a.two"],
                   "tests": ["tests/test_accept_wave.py"]}}


def fake(*args) -> str:
    return json.dumps([sys.executable, str(FAKE), *map(str, args)])


def load_tool():
    spec = importlib.util.spec_from_file_location("accept_wave_probe", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AcceptWaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="accept-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.waves = self.tmp / "waves.json"
        self.waves.write_text(json.dumps(WAVES), "utf-8")
        self.dir = self.tmp / "gauntlet"
        self.lock = self.tmp / "lock"
        self.env = {
            "SUPERSTACK_ACCEPT_WAVES": str(self.waves),
            "SUPERSTACK_ACCEPT_DIR": str(self.dir),
            "SUPERSTACK_ACCEPT_LOCK": str(self.lock),
            "SUPERSTACK" "_ACCEPT_CM" "D_PYTEST": fake("exit", 0),
            "SUPERSTACK_" "ACCEPT_CMD_" "MUTATIONS": fake(
                "gate", "мутации", "partial", 0, 2),
            "SUPERSTAC" "K_ACCEPT_" "CMD_QUICK": fake("gate", "набор", "pass", 0, 0),
            "SUPERSTAC" "K_ACCEPT_" "CMD_FULL": fake("full", 1),
            "SUPERSTAC" "K_ACCEPT_" "CMD_MERGE": fake("merge", "pass"),
        }
        self.tool = load_tool()

    def run_tool(self, *args, **over):
        env = {**os.environ, **self.env, **{k: v for k, v in over.items()}}
        return subprocess.run([sys.executable, str(TOOL), *args], env=env,
                              capture_output=True, text=True, timeout=120)

    def full_record(self, done=True, tree=None, wave=1):
        self.dir.mkdir(exist_ok=True)
        (self.dir / f"accept-{wave}.json").write_text(json.dumps({
            "done": done, "next": "подставной итог",
            "tree": tree or self.tool.tree_fingerprint()}), "utf-8")

    def assertOneLine(self, r, code, start, *inside):
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        self.assertEqual(len(r.stdout.strip().splitlines()), 1, r.stdout)
        self.assertTrue(r.stdout.startswith(start), r.stdout)
        for s in inside:
            self.assertIn(s, r.stdout)

    # ---- принята ----
    def test_everything_green_on_this_tree_is_accepted(self):
        self.full_record()
        r = self.run_tool("1")
        self.assertOneLine(r, 0, "Волна 1 принята")

    # ---- красное ----
    def test_red_pytest_is_not_accepted(self):
        self.full_record()
        r = self.run_tool("1", SUPERSTACK_ACCEPT_CMD_PYTEST=fake("exit", 1))
        self.assertOneLine(r, 1, "Волна 1 не принята", "тест")

    def test_a_survived_mutation_is_not_accepted(self):
        self.full_record()
        r = self.run_tool("1", SUPERSTACK_ACCEPT_CMD_MUTATIONS=fake(
            "gate", "мутации", "partial", 1, 2))
        self.assertOneLine(r, 1, "Волна 1 не принята", "полом")

    def test_fewer_executed_than_named_is_not_accepted(self):
        self.full_record()
        r = self.run_tool("1", SUPERSTACK_ACCEPT_CMD_MUTATIONS=fake(
            "gate", "мутации", "partial", 0, 1))
        self.assertOneLine(r, 1, "Волна 1 не принята", "полом")

    def test_failed_mutation_gate_is_not_accepted(self):
        self.full_record()
        r = self.run_tool("1", SUPERSTACK_ACCEPT_CMD_MUTATIONS=fake(
            "gate", "мутации", "fail", 0, 2))
        self.assertOneLine(r, 1, "Волна 1 не принята", "полом")

    def test_red_quick_bar_is_not_accepted(self):
        self.full_record()
        for status in ("fail", "unknown"):
            r = self.run_tool("1", SUPERSTACK_ACCEPT_CMD_QUICK=fake(
                "gate", "набор", status, 0, 0))
            self.assertOneLine(r, 1, "Волна 1 не принята", "быстрая")

    # ---- полная планка ----
    def test_no_full_bar_result_waits(self):
        r = self.run_tool("1")
        self.assertOneLine(r, 2, "Волна 1 ждёт полную планку")
        self.assertNotIn("принята", r.stdout.replace("не принята", ""))

    def test_full_bar_result_of_another_tree_waits(self):
        self.full_record(tree="чужое-дерево")
        r = self.run_tool("1")
        self.assertOneLine(r, 2, "Волна 1 ждёт полную планку")

    def test_red_full_bar_is_not_accepted(self):
        self.full_record(done=False)
        r = self.run_tool("1")
        self.assertOneLine(r, 1, "Волна 1 не принята", "полная планка")

    def test_full_flag_starts_the_run_and_the_next_call_accepts(self):
        r = self.run_tool("1", "--full")
        self.assertOneLine(r, 2, "Волна 1 ждёт полную планку")
        rec = self.dir / "accept-1.json"
        for _ in range(100):
            if rec.is_file():
                break
            time.sleep(0.2)
        self.assertTrue(rec.is_file(), "фоновая планка не оставила итог")
        r = self.run_tool("1")
        self.assertOneLine(r, 0, "Волна 1 принята")

    def test_merge_of_ci_shards_stands_in_for_the_full_bar(self):
        r = self.run_tool("1", "--merge", str(self.tmp))
        self.assertOneLine(r, 0, "Волна 1 принята")
        r = self.run_tool("1", "--merge", str(self.tmp),
                          SUPERSTACK_ACCEPT_CMD_MERGE=fake("merge", "fail"))
        self.assertOneLine(r, 1, "Волна 1 не принята", "сведение")
        r = self.run_tool("1", "--merge", str(self.tmp),
                          SUPERSTACK_ACCEPT_CMD_MERGE=fake("merge", "unknown"))
        self.assertOneLine(r, 2, "Волна 1 ждёт полную планку")

    # ---- не измерено, ошибка вызова ----
    def test_missing_waves_file_is_not_measured(self):
        self.waves.unlink()
        r = self.run_tool("1")
        self.assertOneLine(r, 2, "Волна 1 не принята", "waves.json")

    def test_wave_without_steps_is_not_measured(self):
        r = self.run_tool("7")
        self.assertOneLine(r, 2, "Волна 7 не принята", "шаг")

    def test_foreign_lock_is_named_in_one_phrase(self):
        self.full_record()
        self.lock.write_text(f"{os.getpid()} планка: без флагов\n", "utf-8")
        r = self.run_tool("1")
        self.assertOneLine(r, 2, "Волна 1 не принята", "чужой прогон")

    def test_bad_call_is_code_3(self):
        for args in ([], ["x"], ["1", "--нет"], ["1", "--merge"]):
            r = self.run_tool(*args)
            self.assertEqual(r.returncode, 3, (args, r.stdout, r.stderr))

    def test_json_is_one_line_with_the_same_code(self):
        self.full_record()
        r = self.run_tool("1", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        d = json.loads(lines[0])
        self.assertEqual((d["code"], d["wave"]), (0, 1))
        self.assertEqual(d["line"], "Волна 1 принята")

    def test_real_waves_file_names_existing_tests_and_mutations(self):
        data = json.loads((REPO / "data" / "waves.json").read_text("utf-8"))
        known = {m["id"] for m in json.loads(
            (REPO / "tests" / "mutations.json").read_text("utf-8"))["mutations"]}
        for step, rec in data.items():
            for t in rec["tests"]:
                self.assertTrue((REPO / t).is_file(), f"{step}: нет {t}")
            for m in rec["mutations"]:
                self.assertIn(m, known, f"{step}: нет поломки {m}")


if __name__ == "__main__":
    unittest.main()
