#!/usr/bin/env python3
"""Проверки, которых исполнитель не видит.

Зачем они нужны.

Всё, что исполнитель видит, находится внутри его оптимизационной петли. Имея
критерий, он со временем удовлетворит ИМЕННО его — и это не жульничество, а
работа по заданию. Отсюда простое следствие: пока все проверки лежат в промпте,
«критерий выполнен» и «сделано то, что нужно» — одно и то же утверждение, и
разойтись они не могут даже там, где обязаны.

Часть проверок обязана остаться снаружи петли. Тогда у приёмки появляется
вопрос, ответ на который никто заранее не оптимизировал.

Зачем проверять протечку КОДОМ, а не следить за собой.

Скрытность — свойство ТЕКСТА, уходящего агенту, а текст собирается из полей
таска. Одна неосторожная правка сборщика — и скрытое поле поедет в промпт,
оставаясь «скрытым» по названию поля. Отказ при этом молчит: прогон зелёный,
скрытая проверка «прошла», и никто не узнает, что она была подсказкой.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import plug  # noqa: E402


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


ho = _load("ss_handoff_hold", plug("superstack-build") / "tools" / "handoff.py")
pr = _load("ss_progress_hold", plug("superstack-build") / "tools" / "progress.py")

HIDDEN = "галерея с нулём работ показывает текст, а не пустую сетку"


def _task(**kw) -> dict:
    base = {"id": "02", "name": "галерея", "status": "waiting",
            "requirements": ["R02"], "zone": ["src/gallery/"],
            "goal": "человек видит работы",
            "acceptance": ["сетка рендерится из файла"],
            "holdout": [HIDDEN]}
    base.update(kw)
    return base


class TestHoldoutIsStoredNextToTheCriteria(unittest.TestCase):

    def test_progress_records_hidden_checks(self):
        d = pr.set_task({"schema": "x", "waves": {}, "stages": [], "updated": ""},
                        "02", "галерея", 1, "waiting",
                        acceptance=["видимый критерий"], holdout=[HIDDEN])
        t = d["waves"]["1"][0]
        self.assertEqual(t["holdout"], [HIDDEN])
        self.assertEqual(t["acceptance"], ["видимый критерий"])

    def test_rewriting_a_task_does_not_lose_them(self):
        """Статус меняют чаще, чем проверки. Молчаливая потеря скрытых проверок
        не покраснела бы нигде: их отсутствие выглядит как «их и не заводили»."""
        d = pr.set_task({"schema": "x", "waves": {}, "stages": [], "updated": ""},
                        "02", "галерея", 1, "waiting", holdout=[HIDDEN])
        d = pr.set_task(d, "02", "галерея", None, "running")
        self.assertEqual(d["waves"]["1"][0]["holdout"], [HIDDEN])


class TestHoldoutNeverReachesTheBuilder(unittest.TestCase):

    def _prompt(self, task: dict) -> str:
        return ho.build({"waves": {"1": [task]}}, task, "границы модулей",
                        "раздел спеки", "npm test")

    def test_the_prompt_does_not_carry_the_hidden_check(self):
        p = self._prompt(_task())
        self.assertNotIn(HIDDEN, p)

    def test_the_visible_criteria_still_reach_him(self):
        """Обратный контроль: спрятав лишнее, мы получили бы исполнителя,
        который не знает, когда закончил."""
        p = self._prompt(_task())
        self.assertIn("сетка рендерится из файла", p)

    def test_a_leak_is_detected_in_the_built_text(self):
        """Скрытность проверяется по готовому тексту, а не по намерению его
        автора: поле может остаться «скрытым» по названию и уехать в промпт."""
        task = _task()
        leaked = ho.holdout_leak(task, "промпт, где сказано: " + HIDDEN)
        self.assertEqual(len(leaked), 1)

    def test_a_clean_prompt_reports_no_leak(self):
        self.assertEqual(ho.holdout_leak(_task(), self._prompt(_task())), [])

    def test_a_short_hidden_line_does_not_cause_false_alarms(self):
        """Короткая строка вроде «ok» встретится в любом промпте, и гейт,
        краснеющий на ней, начнут обходить."""
        self.assertEqual(ho.holdout_leak(_task(holdout=["ok"]), "всё ok здесь"), [])

    def test_a_task_without_hidden_checks_is_still_valid(self):
        """Скрытые проверки — усиление, а не обязанность: требовать их на
        каждом таске значило бы плодить их ради заполнения поля."""
        p = self._prompt(_task(holdout=[]))
        self.assertIn("сетка рендерится из файла", p)


if __name__ == "__main__":
    unittest.main()
