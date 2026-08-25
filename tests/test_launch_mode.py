#!/usr/bin/env python3
"""Чем запускать работу — считает система, а не вспоминает человек.

Ступеней четыре, и они не взаимозаменяемы: `/goal` это УСЛОВИЕ, `/loop` это
ЧАСТОТА, расписание нужно там, где требуются локальные файлы, Routines — когда
ноутбук закрыт. Путают их постоянно, потому что все четыре про «повторяй».

Команда, которую надо вспомнить, для неразработчика равна отсутствию
возможности. Здесь заперты три отказа:

  · рекомендация без названного потолка — это реклама, а не выбор;
  · `/goal` предлагается там, где нет детерминированного гейта, то есть
    выдаётся надежда за автономию;
  · пустой проект получает самый популярный ответ вместо «не знаю».
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

_s = importlib.util.spec_from_file_location("ss_launch", at("tools", "launch_mode.py"))
lm = importlib.util.module_from_spec(_s)
_s.loader.exec_module(lm)


class Ступени(unittest.TestCase):

    def test_у_каждой_названы_и_когда_и_потолок(self):
        """Ступень без потолка превращает выбор в рекламу."""
        for имя, ст in lm.СТУПЕНИ.items():
            with self.subTest(имя):
                self.assertTrue(ст.get("когда"))
                self.assertTrue(ст.get("потолок"))
                self.assertGreater(len(ст["потолок"]), 20,
                                   "потолок назван формально")

    def test_условие_и_частота_не_перепутаны(self):
        self.assertIn("услови", lm.СТУПЕНИ["goal"]["что"] + lm.СТУПЕНИ["goal"]["когда"])
        self.assertIn("интервал", lm.СТУПЕНИ["loop"]["что"])


class Выбор(unittest.TestCase):

    def test_ожидание_внешнего_это_частота(self):
        v = lm.choose({"waits_external": True, "has_bar": True, "stop_gate": True})
        self.assertEqual(v["mode"], "loop")

    def test_планка_плюс_гейт_это_условие(self):
        v = lm.choose({"has_bar": True, "stop_gate": True})
        self.assertEqual(v["mode"], "goal")

    def test_планка_без_гейта_не_даёт_goal(self):
        """`/goal` без детерминированного гейта — надежда, а не автономия."""
        v = lm.choose({"has_bar": True, "stop_gate": False})
        self.assertEqual(v["mode"], "plan")
        self.assertIn("надежда", v["why"])

    def test_нет_сигналов_нет_рекомендации(self):
        """Угаданная маршрутизация хуже отсутствующей: её выполняют."""
        self.assertEqual(lm.choose({}), {})


class Прогон(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()

    def test_пустой_проект_даёт_код_два(self):
        v = lm.run(self.root)
        self.assertEqual(v["status"], "unknown")

    def test_вердикт_несёт_потолок_и_альтернативы(self):
        (self.root / ".superstack" / "bar.json").write_text("{}", encoding="utf-8")
        (self.root / ".superstack" / "state.json").write_text(
            json.dumps({"tasks": [1, 2, 3]}), encoding="utf-8")
        v = lm.run(self.root)
        self.assertEqual(v["status"], "pass")
        self.assertTrue(v["ceiling"])
        self.assertTrue(v["alternatives"])

    def test_задачи_без_планки_ведут_в_обычный_ход(self):
        (self.root / ".superstack" / "state.json").write_text(
            json.dumps({"tasks": [1, 2]}), encoding="utf-8")
        v = lm.run(self.root)
        self.assertEqual(v["mode"], "plan")
        self.assertIn("2 задач", v["detail"])


if __name__ == "__main__":
    unittest.main()
