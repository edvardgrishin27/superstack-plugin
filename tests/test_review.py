#!/usr/bin/env python3
"""Три оси ревью: не смешиваются, и находка знает свой адрес.

Худшее сочетание в ревью — «чисто написано» плюс «сделано не то». Оно выглядит
нормально ровно до тех пор, пока оси не разнесены: аккуратный код гасит
впечатление от того, что реализовано соседнее. Поэтому отчёты по осям здесь не
складываются, и это проверено, а не обещано.

Второй механизм ценнее первого и его нет нигде, кроме прозы AutoPilot: находка
маршрутизируется ОДНИМ вопросом — мог ли исполнитель знать? Он видел свой таск,
названные им разделы спеки и границы уже построенного. Слов человека он не
видел никогда. Значит находка по оси «манифест» — не его ошибка, и дозапрос к
нему требует догадаться о том, чего ему не показывали. Ровно здесь ревью
привычно винит исполнителя за чужую ошибку.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from paths import at

REVIEW = at("tools", "review.py")
_s = importlib.util.spec_from_file_location("superstack_review", REVIEW)
rv = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rv)


def fresh():
    return json.loads(json.dumps(rv.EMPTY))


class TestEachReviewerGetsOnlyItsOwnMaterial(unittest.TestCase):
    """Ревьюер, получивший материал чужой оси, судит и её — молча и плохо."""

    ALL = {"diff": "d", "requirements": ["R01"], "quotes": ["«так просили»"],
           "acceptance": ["условие"], "spec_sections": ["§2"],
           "interfaces": "границы", "conventions": "как здесь пишут",
           "craft_rules": "запахи"}

    def test_manifest_reviewer_gets_the_humans_words(self):
        p = rv.pack(rv.MANIFEST, self.ALL)
        self.assertIn("quotes", p["material"])
        self.assertIn("requirements", p["material"])

    def test_manifest_reviewer_does_not_get_the_spec(self):
        """Иначе он сверит пересказ с пересказом и подтвердит потерю."""
        p = rv.pack(rv.MANIFEST, self.ALL)
        self.assertNotIn("spec_sections", p["material"])
        self.assertIn("spec_sections", p["withheld"])

    def test_craft_reviewer_does_not_get_the_humans_words(self):
        p = rv.pack(rv.CRAFT, self.ALL)
        self.assertNotIn("quotes", p["material"])
        self.assertNotIn("requirements", p["material"])

    def test_craft_reviewer_gets_interfaces(self):
        """Единственный способ увидеть «изобретено заново»: без границ уже
        построенного этот класс находок невидим целиком."""
        self.assertIn("interfaces", rv.pack(rv.CRAFT, self.ALL)["material"])

    def test_withholding_is_named_not_silent(self):
        p = rv.pack(rv.SPEC, self.ALL)
        self.assertTrue(p["withheld"])
        self.assertIn("чужой оси", p["why_withheld"])

    def test_missing_material_is_unknown_not_pass(self):
        p = rv.pack(rv.MANIFEST, {"diff": "d"})
        self.assertEqual(p["status"], "unknown")
        self.assertIn("quotes", p["missing"])

    def test_unknown_axis_is_refused(self):
        with self.assertRaises(ValueError):
            rv.pack("качество", self.ALL)


class TestAFindingIsAConditionNotAWish(unittest.TestCase):

    def test_finding_without_a_condition_is_refused(self):
        """«Стоило бы аккуратнее» нельзя отправить исполнителю не переписав, а
        переписать можно только прочитав дифф — то есть заплатив ровно тем
        контекстом оркестратора, ради экономии которого ревью и вынесено."""
        with self.assertRaises(ValueError) as cm:
            rv.add_finding(fresh(), rv.CRAFT, "a.py:12", "некрасиво", "")
        self.assertIn("УСЛОВИЕ", str(cm.exception))

    def test_finding_without_an_axis_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            rv.add_finding(fresh(), "", "a.py:12", "плохо", "должно быть хорошо")
        self.assertIn("ось", str(cm.exception))

    def test_finding_without_what_is_refused(self):
        with self.assertRaises(ValueError):
            rv.add_finding(fresh(), rv.SPEC, "a.py:12", "  ", "условие")

    def test_valid_finding_is_recorded(self):
        d = rv.add_finding(fresh(), rv.SPEC, "a.py:12", "поле не сохраняется",
                           "поле видно после перезагрузки")
        self.assertEqual(len(d["findings"]), 1)


class TestRoutingAsksOneQuestion(unittest.TestCase):
    """Мог ли исполнитель знать?"""

    def test_manifest_finding_does_not_go_to_the_executor(self):
        f = {"axis": rv.MANIFEST, "where": "a.py", "what": "требование сжалось",
             "must": "статус виден", "blocking": True}
        r = rv.route(f)
        self.assertEqual(r["to"], rv.TO_SPEC)
        self.assertFalse(r["could_have_known"])

    def test_manifest_route_says_why(self):
        r = rv.route({"axis": rv.MANIFEST, "where": "", "what": "", "must": "",
                      "blocking": True})
        self.assertIn("не видел слов человека", r["why"])

    def test_spec_and_craft_go_to_the_executor(self):
        for axis in (rv.SPEC, rv.CRAFT):
            with self.subTest(axis=axis):
                r = rv.route({"axis": axis, "where": "", "what": "", "must": "",
                              "blocking": True})
                self.assertEqual(r["to"], rv.TO_EXECUTOR)
                self.assertTrue(r["could_have_known"])


class TestAxesAreReportedApart(unittest.TestCase):

    def test_verdict_keeps_the_axes_separate(self):
        d = fresh()
        rv.add_finding(d, rv.MANIFEST, "a", "требование сжалось", "статус виден")
        rv.add_finding(d, rv.CRAFT, "b", "дублирование", "вынести общее")
        v = rv.verdict(d)
        self.assertEqual(v["counts"], {"манифест": 1, "спека": 0, "ремесло": 1})
        self.assertEqual(len(v["by_axis"]["манифест"]), 1)

    def test_clean_craft_does_not_hide_a_manifest_finding(self):
        """Именно ради этого оси и разнесены: аккуратный код гасит впечатление
        от того, что реализовано соседнее."""
        d = fresh()
        rv.add_finding(d, rv.MANIFEST, "a", "сделано соседнее", "должно быть то")
        v = rv.verdict(d)
        self.assertEqual(v["status"], "fail")
        self.assertEqual(v["counts"]["ремесло"], 0)

    def test_advisory_finding_does_not_block(self):
        d = fresh()
        rv.add_finding(d, rv.CRAFT, "a", "можно проще", "вынести общее", False)
        self.assertEqual(rv.verdict(d)["status"], "pass")


class TestTwoFollowupsAreTheCeiling(unittest.TestCase):

    def test_first_two_are_allowed(self):
        d = fresh()
        for i in range(2):
            with self.subTest(i=i):
                self.assertTrue(rv.followup_allowed(d)["allowed"])
                d["followups"] += 1

    def test_third_is_refused(self):
        d = fresh()
        d["followups"] = 2
        r = rv.followup_allowed(d)
        self.assertFalse(r["allowed"])
        self.assertIn("СМЕНА ПОДХОДА", r["why"])

    def test_refusal_rejects_repeating_the_same_attempt(self):
        """Повтор той же попытки с надеждой — единственная версия, прямо
        запрещённая: контекст уже застрял в своей колее."""
        d = fresh(); d["followups"] = 2
        self.assertIn("не считается попыткой", rv.followup_allowed(d)["why"])


class TestTheReviewerOutlivesTheTask(unittest.TestCase):
    """Новый ревьюер на каждый таск не видит, что таск 05 противоречит 02."""

    def test_same_reviewer_across_tasks_is_fine(self):
        d = fresh()
        rv.reviewer_continuity(d, rv.CRAFT, "craft-1", 1)
        self.assertTrue(rv.reviewer_continuity(d, rv.CRAFT, "craft-1", 1)["ok"])

    def test_swap_inside_a_wave_is_a_process_defect(self):
        d = fresh()
        rv.reviewer_continuity(d, rv.CRAFT, "craft-1", 1)
        r = rv.reviewer_continuity(d, rv.CRAFT, "craft-2", 1)
        self.assertFalse(r["ok"])
        self.assertIn("перекрёстная память", r["why"])

    def test_refresh_at_a_wave_boundary_is_fine(self):
        """На границе волны перекрёстная память уже устарела — обновлять
        ревьюера там нормально и полезно."""
        d = fresh()
        rv.reviewer_continuity(d, rv.CRAFT, "craft-1", 1)
        self.assertTrue(rv.reviewer_continuity(d, rv.CRAFT, "craft-2", 2)["ok"])

    def test_axes_keep_their_own_reviewers(self):
        d = fresh()
        rv.reviewer_continuity(d, rv.CRAFT, "craft-1", 1)
        self.assertTrue(rv.reviewer_continuity(d, rv.MANIFEST, "man-1", 1)["ok"])


class TestPersistence(unittest.TestCase):

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "r.json"
            d = rv.add_finding(fresh(), rv.SPEC, "a.py:1", "не то", "должно так")
            rv.save(p, d, now="2026-08-14T00:00:00+00:00")
            back = rv.load(p)
            self.assertEqual(back["findings"], d["findings"])
            self.assertEqual(back["updated"], "2026-08-14T00:00:00+00:00")

    def test_unreadable_file_gives_an_empty_state_not_a_crash(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "r.json"
            p.write_text("{не json", encoding="utf-8")
            self.assertEqual(rv.load(p)["findings"], [])


class TestRepairKnowsWhyItFailed(unittest.TestCase):
    """Возврат работы бывает двух видов, и раньше оба шли одним маршрутом.

    НЕДОДЕЛКА — мог и не сделал: красный тест, блокирующая находка. Его
    контекст ещё держит задачу, дозапрос стоит одной строки.

    ОТКАЗ — пробовал и не смог: тот же контекст той же дорогой приведёт туда
    же. Раньше «не смог» получал дозапрос за дозапросом — три попытки
    повторить то, что уже не вышло.

    Взято из autopilot (nick-vels), где разделение описано прозой; здесь оно
    отвечает кодом возврата.
    """

    def test_a_shortfall_goes_back_to_the_same_worker(self):
        r = rv.repair_route({}, rv.SHORTFALL, "тест падает на пустом слоте")
        self.assertEqual(r["step"], "дозапрос")
        self.assertIn("тому же", r["to"])

    def test_a_refusal_skips_followups_entirely(self):
        """Дозапрос к тому, кто не смог, — повторение той же попытки."""
        r = rv.repair_route({}, rv.REFUSAL, "нет доступа к хранилищу")
        self.assertEqual(r["step"], "повтор")
        self.assertIn("свежий", r["to"])

    def test_the_ladder_ends_at_a_different_approach(self):
        d = {"followups": rv.MAX_FOLLOWUPS, "retries": rv.MAX_RETRIES}
        r = rv.repair_route(d, rv.SHORTFALL, "та же поломка")
        self.assertEqual(r["step"], "смена подхода")

    def test_after_the_ladder_it_goes_to_the_human(self):
        """Пятая попытка означает не упрямую задачу, а неверную нарезку."""
        d = {"followups": rv.MAX_FOLLOWUPS, "retries": rv.MAX_RETRIES,
             "approaches": rv.MAX_APPROACHES}
        r = rv.repair_route(d, rv.SHORTFALL, "снова то же")
        self.assertFalse(r["allowed"])
        self.assertIn("нарезка", r["why"])

    def test_repair_without_a_named_cause_is_a_refusal(self):
        """«Почини, чтобы проходило» — приглашение лечить симптом: подогнать
        тест, заглушить ошибку, вписать значение."""
        r = rv.repair_route({}, rv.SHORTFALL, "")
        self.assertEqual(r["step"], "отказ")
        self.assertIn("симптом", r["why"])

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            rv.repair_route({}, "как-нибудь", "причина")

    def test_attempts_are_counted_by_code_not_by_memory(self):
        d = {}
        d = rv.count_repair(d, "дозапрос")
        d = rv.count_repair(d, "повтор")
        self.assertEqual(d["followups"], 1)
        self.assertEqual(d["retries"], 1)


if __name__ == "__main__":
    unittest.main()
