#!/usr/bin/env python3
"""Четыре выключателя: один продукт, а не два.

Из 29 механизмов, помогающих опытному, четыре прямо мешают новичку — каждый
ОТКЛАДЫВАЕТ обратную связь. Новичок узнаёт о поломке позже, чем мог бы, и уже
не помнит, какое из своих действий её вызвало.

Соблазн — сделать два продукта. Они расходятся через месяц, а выросший из
первого переезжает во второй как в чужой. Здесь заперты три отказа:

  · умолчание «включено» превращает выключатель в ловушку: тот, кому механизм
    вредит, о нём и не узнает;
  · включение без названной цены — не выбор;
  · опечатка в имени читается как «включено» у механизма, которого нет, а
    настоящий остаётся выключенным молча.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_toggles",
                                            at("tools", "expert_toggles.py"))
et = importlib.util.module_from_spec(_s)
_s.loader.exec_module(et)


class Каталог(unittest.TestCase):

    def setUp(self):
        self.d, отказ = et.каталог()
        self.assertIsNotNone(self.d, отказ)

    def test_умолчание_выключено(self):
        self.assertEqual(self.d["default"], "off")

    def test_у_каждого_названы_и_польза_и_цена(self):
        """Выбор без цены — не выбор."""
        for м in self.d["mechanisms"]:
            with self.subTest(м["id"]):
                self.assertTrue(м.get("helps"))
                self.assertTrue(м.get("hurts"))
                self.assertGreater(len(м["hurts"]), 30,
                                   "цена названа формально")

    def test_их_ровно_четыре(self):
        """План называет четыре: остальные 25 «незаметны» и включены всем."""
        self.assertEqual(len(self.d["mechanisms"]), 4)

    def test_правило_запрещает_второй_продукт(self):
        self.assertIn("не два продукта", self.d["rule"])


class Состояние(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()
        self.d, _ = et.каталог()

    def _спека(self, включено: list) -> None:
        (self.root / ".superstack" / "expert.json").write_text(
            json.dumps({"enabled": включено}, ensure_ascii=False),
            encoding="utf-8")

    def test_без_спеки_всё_выключено(self):
        v = et.state(self.root, self.d)
        self.assertEqual(v["on"], [])
        self.assertEqual(len(v["off"]), 4)

    def test_включённое_несёт_цену_рядом(self):
        self._спека(["worktree-sandbox"])
        v = et.state(self.root, self.d)
        self.assertEqual(len(v["on"]), 1)
        self.assertIn("теряет файлы из виду", v["on"][0]["hurts"])

    def test_опечатка_в_имени_ловится(self):
        """Иначе включённым числится механизм, которого нет, а настоящий
        остаётся выключенным молча."""
        self._спека(["worktre-sandbox"])
        v = et.state(self.root, self.d)
        self.assertEqual(v["unknown"], ["worktre-sandbox"])
        self.assertEqual(v["on"], [])

    def test_битая_спека_не_включает_ничего(self):
        (self.root / ".superstack" / "expert.json").write_text("{не json",
                                                               encoding="utf-8")
        v = et.state(self.root, self.d)
        self.assertEqual(v["on"], [])
        self.assertEqual(v["unknown"], [])


if __name__ == "__main__":
    unittest.main()
