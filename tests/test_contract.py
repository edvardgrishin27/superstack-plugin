#!/usr/bin/env python3
"""Контракт возврата: «готово» проверяется, а не читается.

Последнее звено цепочки держалось на том, против чего построена вся остальная
система: на внимательном прочтении утверждения. Промпт исполнителю собирался
кодом и требовал контракт дословно — а вернувшийся блок читал человек глазами.

Ради одной проверки тут стоило писать файл целиком:

  СТАТУС «DONE» ПРИ КРАСНЫХ ТЕСТАХ В ТОМ ЖЕ БЛОКЕ.

Это не ложь исполнителя. Он честно печатает и «сделано», и `npm test → 2 failed`:
первое про его работу, второе про прогон. Читающий глазами видит крупное слово
и пролистывает строку с числом — так красное уезжает в коммит с пометкой
«готово», и находится через восемь тасков.
"""
from __future__ import annotations

import importlib.util
import unittest

from paths import at

CONTRACT = at("tools", "contract.py")
_s = importlib.util.spec_from_file_location("superstack_contract", CONTRACT)
ct = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ct)

GOOD = """STATUS: DONE
FILES: src/signup/index.js, src/signup/index.test.js
TESTS: npm test → 5 passed, 0 failed
INTERFACES: submitSignup({name, phone}) -> {ok}
REQUIREMENTS: R02 done
CONCERNS: —
BLOCKERS: —"""


class TestTheContradictionInsideTheBlock(unittest.TestCase):

    def test_done_with_failing_tests_is_broken(self):
        t = GOOD.replace("5 passed, 0 failed", "3 passed, 2 failed")
        v = ct.check(t)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("при красных тестах" in b for b in v["broken"]), v)

    def test_zero_failed_is_not_red(self):
        """«0 failed» и «2 failed» отличаются числом, а не словом. Поиск по
        слову дал бы красное на каждом зелёном прогоне, и проверку сняли бы."""
        self.assertEqual(ct.check(GOOD)["status"], "pass")

    def test_russian_wording_is_caught_too(self):
        t = GOOD.replace("5 passed, 0 failed", "5 прошло, 2 упало")
        self.assertTrue(any("при красных" in b for b in ct.check(t)["broken"]))

    def test_concerns_status_is_held_to_the_same_bar(self):
        """`DONE_WITH_CONCERNS` — это тоже «сделано», и оговорка не отменяет
        красный прогон."""
        t = GOOD.replace("STATUS: DONE", "STATUS: DONE_WITH_CONCERNS") \
                .replace("5 passed, 0 failed", "1 passed, 4 failed")
        self.assertTrue(any("при красных" in b for b in ct.check(t)["broken"]))


class TestAbsenceIsNotSuccess(unittest.TestCase):

    def test_no_block_at_all_is_unknown_not_pass(self):
        v = ct.check("Я всё сделал, форма работает отлично.")
        self.assertEqual(v["status"], "unknown")
        self.assertIn("таск не закончен", v["detail"])

    def test_tests_line_without_an_outcome_is_broken(self):
        t = GOOD.replace("TESTS: npm test → 5 passed, 0 failed",
                         "TESTS: буду запускать")
        self.assertTrue(any("не несёт исхода" in b for b in ct.check(t)["broken"]))

    def test_empty_tests_line_is_broken(self):
        t = GOOD.replace("TESTS: npm test → 5 passed, 0 failed", "TESTS:")
        self.assertTrue(any("нет строки TESTS" in b or "не несёт исхода" in b
                            for b in ct.check(t)["broken"]))

    def test_done_without_files_is_broken(self):
        t = GOOD.replace("FILES: src/signup/index.js, src/signup/index.test.js",
                         "FILES:")
        self.assertTrue(any("без единого файла" in b for b in ct.check(t)["broken"]))

    def test_blocked_without_a_blocker_is_broken(self):
        t = ("STATUS: BLOCKED\nFILES:\nTESTS: не запускал\n"
             "REQUIREMENTS: R02 нет\nBLOCKERS:")
        self.assertTrue(any("без названного блокера" in b
                            for b in ct.check(t)["broken"]))

    def test_blocked_with_a_blocker_passes(self):
        t = ("STATUS: BLOCKED\nFILES:\nTESTS: не запускал\n"
             "REQUIREMENTS: R02 нет\nBLOCKERS: нет пакета `libphonenumber`")
        self.assertEqual(ct.check(t)["status"], "pass")

    def test_unknown_status_is_broken(self):
        t = GOOD.replace("STATUS: DONE", "STATUS: ГОТОВО")
        self.assertTrue(any("неизвестный статус" in b for b in ct.check(t)["broken"]))


class TestTheEssayProblem(unittest.TestCase):
    """Исполнитель час работал и хочет признания. Восемь таких блоков стоят
    оркестратору того же, что восемь диффов, только приезжают другой дверью."""

    def test_a_long_block_is_broken(self):
        t = GOOD + "\n" + "\n".join(f"и ещё вот что: пункт {i}" for i in range(30))
        v = ct.check(t)
        self.assertTrue(any("при потолке" in b for b in v["broken"]), v)

    def test_reasoning_before_the_block_does_not_count(self):
        """Длина меряется от начала БЛОКА: рассуждения до него — не контракт,
        и наказывать за них значит требовать молчаливого исполнителя."""
        t = "\n".join(f"размышление {i}" for i in range(40)) + "\n" + GOOD
        self.assertEqual(ct.check(t)["status"], "pass")


class TestParsing(unittest.TestCase):

    def test_multiline_field_is_joined(self):
        t = GOOD.replace("CONCERNS: —",
                         "CONCERNS: валидация телефона упрощена\n  до длины строки")
        self.assertIn("до длины строки", ct.check(t)["contract"]["CONCERNS"])

    def test_fields_are_reported_back(self):
        f = ct.check(GOOD)["contract"]
        self.assertEqual(f["STATUS"], "DONE")
        self.assertIn("submitSignup", f["INTERFACES"])

    def test_missing_optional_fields_are_unmeasured_not_broken(self):
        t = "STATUS: BLOCKED\nBLOCKERS: нет доступа"
        v = ct.check(t)
        self.assertEqual(v["status"], "unknown")
        self.assertFalse(v["broken"])


class TestWorkCanBeHandedOnInsteadOfFaked(unittest.TestCase):
    """Исходов было четыре, и ни один не описывал «сделал половину».

    Исполнитель, у которого кончается место в контексте, выбирал между «сдать
    недоделанное как DONE» и «вернуть BLOCKED». Первое врёт о готовности,
    второе — о причине: он не «не смог», он не поместился. Оба ответа стоят
    следующей попытки с нуля, потому что сделанное не названо.

    Взято из autopilot (nick-vels), где статус передачи есть, а проверок при
    нём нет: там передача — просьба к модели. Здесь это код возврата.
    """

    GOOD = ("STATUS: HANDOFF\n"
            "FILES: src/booking/slots.ts\n"
            "TESTS: npm test -> 12 passed, 0 failed\n"
            "INTERFACES: reserve(slotId) -> {ok}\n"
            "REQUIREMENTS: R03 in progress\n"
            "HANDOFF: схема и запись готовы, дальше — отмена брони\n"
            "CONCERNS: нет\nBLOCKERS: нет")

    def test_a_green_handoff_passes(self):
        self.assertEqual(ct.check(self.GOOD)["status"], "pass")

    def test_red_work_may_not_be_handed_on(self):
        """Красное, ушедшее в чужой контекст, становится чужой поломкой:
        принимающий тратит своё место на разбор того, чего не делал."""
        red = self.GOOD.replace("12 passed, 0 failed", "10 passed, 2 failed")
        v = ct.check(red)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("красных" in b for b in v["broken"]))

    def test_a_handoff_without_a_run_is_refused(self):
        no_run = self.GOOD.replace("TESTS: npm test -> 12 passed, 0 failed",
                                   "TESTS: не запускал")
        self.assertEqual(ct.check(no_run)["status"], "fail")

    def test_a_handoff_must_say_where_it_stopped(self):
        """Без этого принимающий восстанавливает замысел по коду — платит
        второй раз за то, что передающий уже знал."""
        blind = (self.GOOD.replace("HANDOFF: схема и запись готовы, дальше — отмена брони\n", "")
                 .replace("CONCERNS: нет", "CONCERNS:"))
        v = ct.check(blind)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("HANDOFF" in b for b in v["broken"]))

    def test_the_third_handoff_is_a_planning_defect(self):
        """Часть, не поместившаяся в три контекста, разрезана неверно:
        четвёртый потратится так же, как первые три."""
        v = ct.check(self.GOOD, handoffs=ct.MAX_HANDOFFS)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("нарезк" in b for b in v["broken"]))

    def test_handoffs_below_the_ceiling_are_normal(self):
        self.assertEqual(ct.check(self.GOOD, handoffs=1)["status"], "pass")

    def test_the_ceiling_is_a_named_constant(self):
        self.assertIsInstance(ct.MAX_HANDOFFS, int)
        self.assertGreaterEqual(ct.MAX_HANDOFFS, 1)


if __name__ == "__main__":
    unittest.main()


class TestAnExpectedFailureIsNotABrokenRun(unittest.TestCase):
    """Ненулевой код возврата не всегда провал.

    Гейт, доказавший свою работу отказом — «сборка выпуска не пустила пример
    наполнения» — это УСПЕХ проверки, и числом он неотличим от поломки.
    Живой случай: исполнитель честно написал «npm run build:release → exit 1
    (пример наполнения)», а контракт объявил это красным прогоном и завернул
    работу целиком.

    Угадывать здесь нельзя, поэтому исход называется дословно. Пометка — это
    утверждение исполнителя, за которое он отвечает, и она снимает ровно тот
    прогон, рядом с которым стоит.
    """

    BASE = ("STATUS: DONE\nFILES: src/a.ts\nTESTS: {}\n"
            "INTERFACES: —\nREQUIREMENTS: R01 done\nCONCERNS: нет\nBLOCKERS: нет")

    def _check(self, tests: str):
        return ct.check(self.BASE.format(tests))

    def test_a_green_run_passes(self):
        v = self._check("npm test -> 169 passed, exit 0")
        self.assertEqual(v["status"], "pass")

    def test_a_real_failure_is_still_caught(self):
        v = self._check("npm test -> 167 passed, 2 failed")
        self.assertEqual(v["status"], "fail")
        self.assertEqual(v["tests_red"], 2)

    def test_a_gate_proving_itself_by_refusing_is_not_red(self):
        v = self._check("npm test -> 169 passed, exit 0 | "
                        "npm run build:release -> exit 1 (ожидаемый отказ: пример наполнения)")
        self.assertEqual(v["status"], "pass", v.get("broken"))

    def test_the_mark_does_not_cover_a_later_failure(self):
        """Иначе одна пометка оправдала бы все провалы блока разом — и это был
        бы самый дешёвый способ провести красное как зелёное."""
        v = self._check("npm run build:release -> exit 1 (ожидаемый отказ) | "
                        "npm test -> 3 failed")
        self.assertEqual(v["status"], "fail")
        self.assertEqual(v["tests_red"], 3)

    def test_the_parser_does_not_crash_on_an_exit_code(self):
        """Инструмент, падающий с трассировкой вместо вердикта, хуже строгого:
        он не судит вовсе. Так и случилось на живом возврате."""
        v = self._check("npm run build -> exit 1")
        self.assertIn(v["status"], ("pass", "fail"))
