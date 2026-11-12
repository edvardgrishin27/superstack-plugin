#!/usr/bin/env python3
"""Вопрос об уроке не стоит лишнего хода.

Хук на событии `Stop` отдавал `additionalContext`. На этом событии
дополнительный текст продолжает разговор (так сказано в соседнем хуке
закрытия хода), то есть каждый ход с правками получал лишний платный ход.
Вопрос ушёл в конец прогона: фаза 7 зовёт `learn.py` один раз после сдачи.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from paths import PKG

HOOKS = PKG / "hooks"
PHASE7 = PKG / "skills" / "go" / "phases" / "7-переживёт.md"


class TestNoHookAsksOnStop(unittest.TestCase):
    def test_no_stop_hook_returns_additional_context(self):
        """Ни один хук, подключённый на Stop, не отдаёт additionalContext."""
        cfg = json.loads((HOOKS / "hooks.json").read_text("utf-8"))["hooks"]
        for запись in cfg.get("Stop", []):
            for h in запись["hooks"]:
                m = re.search(r"hooks/([\w.-]+\.py)", h["command"])
                self.assertIsNotNone(m, h["command"])
                файл = HOOKS / m.group(1)
                self.assertTrue(файл.is_file(), f"хук в никуда: {файл}")
                self.assertNotIn("additionalContext", файл.read_text("utf-8")
                                 .replace("`additionalContext`", ""),
                                 f"{файл.name} продолжает разговор на Stop")

    def test_the_lesson_hook_is_not_wired_to_stop(self):
        сырой = (HOOKS / "hooks.json").read_text("utf-8")
        self.assertNotIn("session-lesson", сырой,
                         "вопрос об уроке снова подключён к событию Stop")

    def test_the_lesson_hook_file_is_gone(self):
        self.assertFalse((HOOKS / "session-lesson.py").exists())
        self.assertFalse((HOOKS / "session-lesson.sh").exists())


class TestPhaseSevenAsksOnce(unittest.TestCase):
    def setUp(self):
        self.text = PHASE7.read_text("utf-8")

    def test_the_phase_calls_the_lesson_tool(self):
        self.assertIn("tools/learn.py", self.text)
        self.assertTrue((PKG / "tools" / "learn.py").is_file())

    def test_the_phase_names_the_question_and_the_bar(self):
        self.assertIn("есть ли урок на всегда", self.text)
        for слово in ("проверк", "паттерн отказа", "тупик"):
            self.assertIn(слово, self.text, f"условие планки не названо: {слово}")

    def test_silence_is_named_as_the_usual_answer(self):
        self.assertIn("НЕ ЗАПИСЫВАЙ", self.text)

    def test_the_question_is_asked_after_delivery_once(self):
        self.assertIn("один раз", self.text)


if __name__ == "__main__":
    unittest.main()
