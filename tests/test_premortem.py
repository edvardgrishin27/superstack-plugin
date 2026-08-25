#!/usr/bin/env python3
"""Состязательный проход: где разваливается сама задача.

Дыра, которую он закрывает, единственная в своём роде. Все остальные проверки
системы спрашивают «сделали ли то, о чём просили» — и молчат, если сомнительна
сама просьба. Задача может быть полной, непротиворечивой и понятной, описывая
вещь, которая работать не будет; ни один гейт ниже по течению этого не увидит,
потому что каждый сверяет результат с ней же.

Три вещи здесь заперты кодом, и первая — важнейшая.

Проход НЕ ИМЕЕТ ПРАВА ничего вычеркнуть. Он исследует задачу; распоряжается ею
человек. Требование, которое проход считает плохой идеей, остаётся требованием:
самое большее, что оно заработало, — один вопрос с названной ценой. Без этого
ограничения состязательный проход становится самым удобным способом тихо
избавиться от неудобной работы, прикрывшись анализом.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from paths import at

PM = at("tools", "premortem.py")
_s = importlib.util.spec_from_file_location("superstack_premortem", PM)
pm = importlib.util.module_from_spec(_s)
_s.loader.exec_module(pm)


def fresh():
    return json.loads(json.dumps(pm.EMPTY))


def full(data=None):
    """Проход с урожаем выше нижней отметки."""
    d = data or fresh()
    for q, what in (("провал", "заявки идут, а мастер не смотрит в бота"),
                    ("условие", "первые заявки взять неоткуда"),
                    ("вторая-неделя", "дубликаты от одного клиента"),
                    ("второй-актор", "мастер тоже пользователь, про него ни строчки")):
        pm.add(d, q, what, "решение по этой находке")
    return d


class TestThePassMayNotRemoveAnything(unittest.TestCase):
    """Иначе он становится удобным способом тихо избавиться от работы."""

    def test_dropping_a_requirement_is_not_an_allowed_outcome(self):
        with self.assertRaises(ValueError) as cm:
            pm.add(fresh(), "цена", "SMS дорого", "убрать", outcome="dropped")
        self.assertIn("распоряжается ею человек", str(cm.exception))

    def test_allowed_outcomes_are_exactly_four(self):
        self.assertEqual(set(pm.OUTCOMES),
                         {pm.ASK, pm.ADDITION, pm.OUT_OF_SCOPE, pm.ASSUMPTION})

    def test_each_allowed_outcome_is_accepted(self):
        for o in pm.OUTCOMES:
            with self.subTest(outcome=o):
                pm.add(fresh(), "цена", "дорого при малой пользе",
                       "назвать цену человеку", outcome=o)

    def test_the_costly_requirement_earns_a_question_not_a_verdict(self):
        d = pm.add(fresh(), "цена", "SMS съест половину сборки",
                   "спросить, нужен ли этот канал, назвав цену")
        self.assertEqual(d["findings"][0]["outcome"], pm.ASK)


class TestAFindingIsAboutTheTaskNotThePerson(unittest.TestCase):
    """«Заявки идут круглосуточно, а мастер один» — работа.
    «Ты уверен, что это кому-то нужно» — мнение, которое ничего не покупает."""

    def test_doubting_the_person_is_refused(self):
        for text in ("ты уверен, что это кому-то нужно",
                     "зачем тебе вообще этот проект",
                     "стоит ли вообще это делать"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as cm:
                    pm.add(fresh(), "провал", text, "подумать")
                self.assertIn("суждение о человеке", str(cm.exception))

    def test_the_same_doubt_in_the_action_is_refused_too(self):
        with self.assertRaises(ValueError):
            pm.add(fresh(), "провал", "заявки некому читать",
                   "спросить, точно ли кому-то нужно")

    def test_a_real_finding_passes(self):
        d = pm.add(fresh(), "условие",
                   "заявки идут круглосуточно, а мастер один",
                   "очередь и обещание ответа в рабочие часы")
        self.assertEqual(len(d["findings"]), 1)

    def test_half_a_finding_is_refused(self):
        with self.assertRaises(ValueError):
            pm.add(fresh(), "условие", "что-то не сходится", "")
        with self.assertRaises(ValueError):
            pm.add(fresh(), "условие", "", "что-то сделать")

    def test_unknown_question_is_refused(self):
        with self.assertRaises(ValueError):
            pm.add(fresh(), "красота", "некрасиво", "покрасить")


class TestATooCleanPassIsSuspicious(unittest.TestCase):
    """Не «задача хороша», а «так выглядит непроведённая работа»: первое нечем
    проверить, второе — повод переспросить."""

    def test_below_the_floor_is_red(self):
        d = pm.add(fresh(), "провал", "никто не читает заявки", "поставить очередь")
        v = pm.verdict(d)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("для галочки" in b for b in v["broken"]), v)

    def test_at_the_floor_it_passes(self):
        d = fresh()
        for q in ("провал", "условие", "цена"):
            pm.add(d, q, f"находка по {q}", "что делать")
        self.assertNotEqual(pm.verdict(d)["status"], "fail")

    def test_not_run_at_all_is_unknown_not_clean(self):
        v = pm.verdict(fresh())
        self.assertEqual(v["status"], "unknown")
        self.assertFalse(v["broken"])

    def test_touching_too_few_questions_is_unmeasured(self):
        d = fresh()
        for i in range(4):
            pm.add(d, "провал", f"находка {i}", "что делать")
        v = pm.verdict(d)
        self.assertTrue(any("не задано вопросов" in u for u in v["unmeasured"]), v)

    def test_a_broad_pass_is_clean(self):
        self.assertEqual(pm.verdict(full())["status"], "pass")


class TestModeDecidesWhoClosesTheFinding(unittest.TestCase):
    """Проход одинаков во всех режимах; различается, КТО закрывает найденное.
    Смешение этих двух вещей и делает «полный автомат плюс глубокая
    проработка» противоречием."""

    def test_in_full_a_question_becomes_a_recorded_assumption(self):
        d = pm.add(fresh(), "цена", "SMS дорого", "решить, нужен ли канал")
        self.assertEqual(pm.verdict(d, "full")["routed"][0]["goes"], pm.ASSUMPTION)

    def test_elsewhere_it_stays_a_question(self):
        d = pm.add(fresh(), "цена", "SMS дорого", "решить, нужен ли канал")
        for mode in ("semi", "interview", "manual"):
            with self.subTest(mode=mode):
                self.assertEqual(pm.verdict(d, mode)["routed"][0]["goes"], pm.ASK)

    def test_non_question_outcomes_do_not_move_with_the_mode(self):
        d = pm.add(fresh(), "второй-актор", "мастер тоже пользователь",
                   "экран мастера", outcome=pm.ADDITION)
        for mode in ("full", "semi"):
            with self.subTest(mode=mode):
                self.assertEqual(pm.verdict(d, mode)["routed"][0]["goes"],
                                 pm.ADDITION)

    def test_the_pass_itself_does_not_change_with_the_mode(self):
        d = full()
        self.assertEqual(pm.verdict(d, "full")["found"],
                         pm.verdict(d, "interview")["found"])


class TestPersistence(unittest.TestCase):

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "pm.json"
            d = full()
            pm.save(p, d, now="2026-08-14T00:00:00+00:00")
            back = pm.load(p)
            self.assertTrue(back["ran"])
            self.assertEqual(len(back["findings"]), 4)

    def test_unreadable_file_gives_an_unrun_pass_not_a_clean_one(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "pm.json"
            p.write_text("{сломано", encoding="utf-8")
            self.assertFalse(pm.load(p)["ran"])


if __name__ == "__main__":
    unittest.main()
