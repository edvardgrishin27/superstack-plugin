#!/usr/bin/env python3
"""Хуки видят проект в подпапке: чат открыт в домашней папке, проект в `site`.

Корень сеанса шире отметки, и хук молчал весь сеанс. Вход хука несёт `cwd`,
и хук обязан им воспользоваться, но только когда `cwd` лежит внутри
отмеченной папки. Хуки запускаются отдельным процессом, как в проде.

Поле `cwd` общее для всех событий хуков и после `cd` показывает новую папку
(документация хуков Claude Code, раздел общих полей входа).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

GATE = PKG / "hooks" / "verify-gate.py"
PRECOMPACT = PKG / "hooks" / "precompact.py"
FIX = PKG / "tools" / "fix.py"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        база = Path(self.tmp.name).resolve()
        self.home = база / "home"
        self.state = база / "state"
        self.site = self.home / "site"
        self.other = self.home / "other"
        for d in (self.state, self.site / "tests", self.other / "tests"):
            d.mkdir(parents=True)
        # в other проверка зелёная
        (self.other / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8")
        # красная проверка в site
        (self.site / "tests" / "test_x.py").write_text(
            "def test_x():\n    assert False\n", encoding="utf-8")
        self.transcript = база / "t.jsonl"
        self.transcript.write_text('{"role":"user"}\n', encoding="utf-8")

    def mark(self, *папки: Path):
        (self.state / "projects").write_text(
            "".join(f"{p}\n" for p in папки), encoding="utf-8")

    def run_hook(self, hook: Path, session_root: Path, cwd: "Path | None"):
        вход = {"session_id": "s1", "transcript_path": str(self.transcript)}
        if cwd is not None:
            вход["cwd"] = str(cwd)
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state),
               "CLAUDE_PROJECT_DIR": str(session_root)}
        for k in ("SUPERSTACK_DISABLE", "SUPERSTACK_IGNORE_PAUSE",
                  "SUPERSTACK_PROJECT_DIR"):
            env.pop(k, None)
        return subprocess.run([sys.executable, str(hook)], capture_output=True,
                              text=True, timeout=120, env=env,
                              cwd=str(session_root), input=json.dumps(вход))

    def snapshots(self) -> list:
        d = self.state / "precompact"
        return list(d.glob("*.jsonl")) if d.is_dir() else []


class TestGateFollowsTheInputFolder(Fixture):
    def test_session_in_home_gate_works_on_the_marked_subfolder(self):
        self.mark(self.site)
        p = self.run_hook(GATE, self.home, self.site)
        self.assertEqual(p.returncode, 0)
        self.assertIn('"decision"', p.stdout,
                      "хук промолчал: корень сеанса — домашняя папка, а "
                      "`cwd` во входе указывает на отмеченную подпапку")
        self.assertIn("block", p.stdout)

    def test_unmarked_input_folder_stays_silent(self):
        self.mark(self.site)
        p = self.run_hook(GATE, self.home, self.other)
        self.assertEqual((p.stdout.strip(), p.returncode), ("", 0))

    def test_no_cwd_in_the_input_stays_silent(self):
        self.mark(self.site)
        p = self.run_hook(GATE, self.home, None)
        self.assertEqual((p.stdout.strip(), p.returncode), ("", 0))

    def test_marked_session_root_ignores_cwd(self):
        self.mark(self.site)
        # корень сеанса отмечен и красный; cwd указывает на чужую папку
        p = self.run_hook(GATE, self.site, self.other)
        self.assertIn("block", p.stdout)
        # корень отмечен и зелёный: cwd на красную site ничего не меняет
        self.mark(self.other)
        p = self.run_hook(GATE, self.other, self.site)
        self.assertEqual((p.stdout.strip(), p.returncode), ("", 0))


class TestPrecompactFollowsTheInputFolder(Fixture):
    def test_session_in_home_snapshot_is_kept_for_the_marked_subfolder(self):
        self.mark(self.site)
        p = self.run_hook(PRECOMPACT, self.home, self.site)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(self.snapshots(),
                        "копия разговора не сохранена: хук сжатия не увидел "
                        "отмеченную подпапку по `cwd`")

    def test_unmarked_input_folder_keeps_nothing(self):
        self.mark(self.site)
        p = self.run_hook(PRECOMPACT, self.home, self.other)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(self.snapshots(), [])

    def test_marked_session_root_works_whatever_cwd_says(self):
        self.mark(self.site)
        p = self.run_hook(PRECOMPACT, self.site, self.other)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(self.snapshots())


class TestFixNamesTheMissingMark(Fixture):
    def _fix(self, cwd: Path):
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state)}
        return subprocess.run([sys.executable, str(FIX), "--json"],
                              capture_output=True, text=True, timeout=60,
                              env=env, cwd=str(cwd))

    def test_unmarked_folder_is_named_as_the_broken_link(self):
        self.mark(self.site)
        p = self._fix(self.other)
        v = json.loads(p.stdout)
        self.assertEqual(v["status"], "broken")
        self.assertEqual(v["link"], "проверки в этой папке")
        self.assertIn("не включены", v["next"])

    def test_marked_folder_passes_that_link(self):
        self.mark(self.site)
        v = json.loads(self._fix(self.site).stdout)
        ids = [c["id"] for c in v["checked"]]
        self.assertIn("enabled", ids)
        self.assertTrue(next(c for c in v["checked"] if c["id"] == "enabled")["ok"])


if __name__ == "__main__":
    unittest.main()
