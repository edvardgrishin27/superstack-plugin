#!/usr/bin/env python3
"""Витрина и тексты говорят то, что держит код (шаг W1.S8).

Пять расхождений между словом и кодом, и у каждого здесь свой тест:
  · хук закрытия хода молчал, когда отпускал без проверки;
  · фаза плана говорила два противоположных правила про согласие на план;
  · первое предложение хука старта звало проверять настройки, а не строить;
  · витрина обещала «заговорит сам», «ничего не удаляет» и «стоп» сильнее кода;
  · заявленная нижняя версия Python нигде не проверялась.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO  # noqa: E402

GATE = PKG / "hooks" / "verify-gate.py"
FIRST_RUN = PKG / "hooks" / "first-run.py"
PHASE_PLAN = PKG / "skills" / "go" / "phases" / "3-план.md"
AUTONOMY = PKG / "tools" / "autonomy.py"
APPLY = PKG / "tools" / "apply.py"
WORKFLOW = REPO / ".github" / "workflows" / "gates.yml"
README_RU = (REPO / "README.md").read_text("utf-8")
README_EN = (REPO / "README.en.md").read_text("utf-8")

ФРАЗА_ПОВТОР = "ход закрыт без новой проверки"
ФРАЗА_СБОЙ = "проверка не смогла запуститься, ход закрыт без неё"


class TestGateNamesItsOwnRelease(unittest.TestCase):
    """Хук, который отпускает ход молча, неотличим от пройденной проверки."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.state = base / "state"
        self.project = base / "project"
        self.project.mkdir()
        self.plug = base / "plug"
        (self.plug / "hooks").mkdir(parents=True)
        (self.plug / "tools").mkdir(parents=True)
        self.script = self.plug / "hooks" / "verify-gate.py"
        shutil.copy2(GATE, self.script)

    def tearDown(self):
        self.tmp.cleanup()

    def run_gate(self, session: str, active: bool):
        env = {**os.environ,
               "SUPERSTACK_STATE_DIR": str(self.state),
               "SUPERSTACK_PROJECT_DIR": str(self.project)}
        for k in ("SUPERSTACK_DISABLE", "SUPERSTACK_IGNORE_PAUSE",
                  "SUPERSTACK_GATE_TIMEOUT"):
            env.pop(k, None)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "projects").write_text(str(self.project) + "\n",
                                             encoding="utf-8")
        вход = json.dumps({"session_id": session, "stop_hook_active": active,
                           "cwd": str(self.project)})
        return subprocess.run([sys.executable, str(self.script)], input=вход,
                              capture_output=True, text=True, timeout=60,
                              env=env)

    def message(self, r) -> str:
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return ""
        data = json.loads(r.stdout)
        self.assertNotIn("decision", data, "отпуск не должен блокировать ход")
        self.assertNotIn("hookSpecificOutput", data,
                         "additionalContext продолжил бы ход")
        return data.get("systemMessage", "")

    def test_a_repeated_stop_is_named_aloud_once_per_session(self):
        первый = self.message(self.run_gate("s9", True))
        self.assertIn(ФРАЗА_ПОВТОР, первый)
        второй = self.message(self.run_gate("s9", True))
        self.assertEqual(второй, "", "та же сессия услышала фразу дважды")

    def test_a_failure_inside_the_hook_is_named_aloud_and_exits_zero(self):
        src = self.script.read_text("utf-8")
        якорь = "    verify = Path(__file__).resolve().parent.parent"
        self.assertEqual(src.count(якорь), 1)
        self.script.write_text(
            src.replace(якорь, '    raise RuntimeError("сбой хука")\n' + якорь),
            encoding="utf-8")
        первый = self.message(self.run_gate("s7", False))
        self.assertIn(ФРАЗА_СБОЙ, первый)
        второй = self.message(self.run_gate("s7", False))
        self.assertEqual(второй, "", "та же сессия услышала сбой дважды")


class TestPlanConsentIsOneRule(unittest.TestCase):
    """Согласие на план нужно на любой ступени: решение владельца 11.11.2026."""

    def test_the_plan_phase_does_not_waive_the_plan_consent(self):
        text = PHASE_PLAN.read_text("utf-8")
        self.assertNotIn("без подтверждения плана", text)
        self.assertIn("План показывается и ждёт «да» на любой ступени", text)
        self.assertIn("Код не пишется, пока человек не сказал «да»", text)

    def test_level_three_does_not_claim_to_skip_the_plan(self):
        sys.path.insert(0, str(PKG / "tools"))
        import autonomy
        for ступень, слова in autonomy.LEVELS.items():
            self.assertNotIn("без подтверждения плана", слова, ступень)


class TestFirstRunInvitesBuilding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def context(self) -> str:
        env = {**os.environ, "SUPERSTACK_STATE_DIR": self.tmp.name}
        env.pop("SUPERSTACK_DISABLE", None)
        r = subprocess.run([sys.executable, str(FIRST_RUN)], capture_output=True,
                           text=True, timeout=30, env=env)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_the_first_offer_asks_what_to_build(self):
        ctx = self.context()
        self.assertIn("построить", ctx)
        self.assertIn("добавь", ctx)
        self.assertIn("верни как было", ctx)

    def test_the_first_offer_is_not_a_settings_audit_and_has_no_bare_stop(self):
        ctx = self.context()
        self.assertNotIn("настройку Claude Code", ctx)
        self.assertNotIn("настроен твой Claude Code", ctx)
        self.assertNotIn("скилл superstack", ctx)
        self.assertNotIn("«стоп»", ctx, "«стоп» ставит бессрочную паузу везде")

    def test_a_person_who_already_named_a_goal_is_left_alone(self):
        self.assertIn("уже назвал", self.context())


class TestShowcaseSaysWhatTheCodeHolds(unittest.TestCase):
    def test_no_blanket_promise_that_every_line_is_a_lock(self):
        for имя, текст in (("README.md", README_RU), ("README.en.md", README_EN)):
            self.assertNotIn("не обещание, а место в коде", текст, имя)
            self.assertNotIn("not a promise but a place in the code", текст, имя)

    def test_the_never_section_stays_but_the_claims_are_narrowed(self):
        self.assertIn("Чего SUPERSTACK не делает никогда", README_RU)
        self.assertIn("What SUPERSTACK never does", README_EN)
        for текст in (README_RU, README_EN):
            self.assertNotIn("Не пишет код до подтверждения плана", текст)
            self.assertNotIn("Writes no code before you approve the plan", текст)
            self.assertNotIn("Never continues after", текст)
            self.assertNotIn("Не продолжает после «стоп»", текст)
            self.assertNotIn("**Ничего не удаляет.**", текст)
            self.assertNotIn("**Deletes nothing.**", текст)
            self.assertNotIn("Не выполняет разрушающие команды", текст)
            self.assertNotIn("Never runs destructive commands", текст)

    def test_the_stop_claim_says_running_work_is_not_interrupted(self):
        self.assertIn("идущую работу он не прерывает", README_RU)
        self.assertIn("does not interrupt work already running", README_EN)

    def test_it_does_not_promise_it_will_speak_on_its_own(self):
        self.assertNotIn("заговорит сам", README_RU)
        self.assertNotIn("talk first", README_EN.lower())

    def test_the_english_page_does_not_say_the_product_talks_only_russian(self):
        self.assertNotIn("talks to you in Russian", README_EN)

    def test_there_is_no_list_of_known_unfinished_things(self):
        self.assertNotIn("не** сделанное", README_RU)
        self.assertNotIn("не сделанное", README_RU)
        self.assertNotIn("Known **not** done", README_EN)
        # Факт про Windows остаётся в требованиях.
        self.assertIn("Windows", README_RU)
        self.assertIn("no live run has ever been done", README_EN)


class TestPythonFloorHasAJob(unittest.TestCase):
    def test_apply_uses_no_call_missing_in_python_38(self):
        код = APPLY.read_text("utf-8")
        self.assertNotIn("is_relative_to", код)

    def test_the_floor_in_both_readmes_is_the_floor_the_build_runs(self):
        yml = WORKFLOW.read_text("utf-8")
        версии = re.findall(r'python-version:\s*"(3\.\d+)"', yml)
        низшая = min(версии, key=lambda v: int(v.split(".")[1]))
        for имя, текст in (("README.md", README_RU), ("README.en.md", README_EN)):
            m = re.search(r"Python (3\.\d+)", текст)
            self.assertIsNotNone(m, имя)
            self.assertEqual(
                m.group(1), низшая,
                f"{имя} обещает Python {m.group(1)}, а сборка гоняет набор "
                f"только от {низшая}")

    def test_the_lowest_version_runs_the_test_suite(self):
        yml = WORKFLOW.read_text("utf-8")
        i = yml.index('python-version: "3.8"')
        хвост = yml[i:i + 400]
        self.assertIn("pytest -q tests/", хвост)


if __name__ == "__main__":
    unittest.main()
