#!/usr/bin/env python3
"""Сколько стоил прогон — по журналам, а не по ощущению.

Зачем набор существует.

Все пороги системы — «не больше шестнадцати частей», «потолок контекста», «два
дозапроса» — до сих пор держались на самооценке модели, которая своих токенов
не видит. Журналы сессий Claude Code пишет сам, и они единственный источник,
способный подтвердить порог или показать, что его давно пробили.

Здесь заперты два дефекта, оба найдены на живом журнале уже после того, как
инструмент «работал»:

  · одно сообщение лежит в журнале НЕСКОЛЬКО раз, по записи на блок ответа
    (5244 записи на 2505 сообщений, отдельные повторялись девять раз) —
    построчный подсчёт завышал расход вдвое;

  · потолок 120к относится к помощникам, а не к ведущей сессии, у которой окно
    на порядок больше. Общая мерка давала две с половиной тысячи «превышений»
    там, где всё в порядке.

Инструмент, который считает неверно, хуже отсутствующего: его числам верят и по
ним меняют пороги.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import plug  # noqa: E402

TOOL = plug("superstack-control") / "tools" / "measure_run.py"
_s = importlib.util.spec_from_file_location("ss_measure_run", TOOL)
mr = importlib.util.module_from_spec(_s)
_s.loader.exec_module(mr)


def answer(mid: str, out: int = 100, read: int = 1000, write: int = 0,
           when: str = "2026-08-17T10:00:00+00:00", side: bool = False) -> str:
    return json.dumps({
        "type": "assistant", "timestamp": when, "isSidechain": side,
        "message": {"id": mid, "role": "assistant", "usage": {
            "input_tokens": 2, "output_tokens": out,
            "cache_read_input_tokens": read,
            "cache_cre" "ation_inp" "ut_tokens": write}}}, ensure_ascii=False)


class TestOneMessageIsCountedOnce(unittest.TestCase):
    """Журнал хранит по записи на каждый блок ответа. Считать построчно —
    значит удваивать расход и объявлять контексты, которых не бывает."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "session.jsonl"

    def test_repeats_of_one_message_do_not_add_up(self):
        self.f.write_text("\n".join([answer("msg_1"), answer("msg_1"),
                                     answer("msg_1")]), encoding="utf-8")
        v = mr.measure_file(self.f)
        self.assertEqual(v["answers"], 1)
        self.assertEqual(v["tokens"]["out"], 100)

    def test_different_messages_do_add_up(self):
        self.f.write_text("\n".join([answer("msg_1"), answer("msg_2")]),
                          encoding="utf-8")
        self.assertEqual(mr.measure_file(self.f)["tokens"]["out"], 200)

    def test_a_broken_line_does_not_stop_the_count(self):
        """Журнал пишется на ходу, и последняя строка бывает оборванной."""
        self.f.write_text(answer("msg_1") + "\n{ порванная строка",
                          encoding="utf-8")
        self.assertEqual(mr.measure_file(self.f)["answers"], 1)


class TestTheCeilingBelongsToHelpers(unittest.TestCase):
    """У ведущей сессии окно на порядок больше, и её длинный контекст — норма
    работы, а не тревога. Одна мерка на двоих топит настоящие превышения в
    тысячах ложных."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "session.jsonl"

    def test_a_long_main_session_is_not_an_overrun(self):
        self.f.write_text(answer("msg_1", read=mr.CONTEXT_CEILING * 5),
                          encoding="utf-8")
        self.assertEqual(mr.measure_file(self.f)["context"]["helpers_over_ceiling"], 0)

    def test_a_helper_past_the_ceiling_is_counted(self):
        self.f.write_text(answer("msg_1", read=mr.CONTEXT_CEILING * 2, side=True),
                          encoding="utf-8")
        self.assertEqual(mr.measure_file(self.f)["context"]["helpers_over_ceiling"], 1)

    def test_the_ceiling_is_a_named_constant(self):
        self.assertIsInstance(mr.CONTEXT_CEILING, int)
        self.assertGreater(mr.CONTEXT_CEILING, 0)


class TestWaitingIsSeparatedFromWorking(unittest.TestCase):
    """Календарное время прогона и рабочее — разные величины: разница это
    ожидание человека, и она не стоит денег, но стоит вечера."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "session.jsonl"

    def test_a_short_gap_counts_as_work(self):
        self.f.write_text("\n".join([
            answer("m1", when="2026-08-17T10:00:00+00:00"),
            answer("m2", when="2026-08-17T10:01:00+00:00")]), encoding="utf-8")
        v = mr.measure_file(self.f)
        self.assertEqual(v["active_sec"], 60)
        self.assertEqual(v["idle_sec"], 0)

    def test_a_long_gap_counts_as_waiting(self):
        self.f.write_text("\n".join([
            answer("m1", when="2026-08-17T10:00:00+00:00"),
            answer("m2", when="2026-08-17T12:00:00+00:00")]), encoding="utf-8")
        v = mr.measure_file(self.f)
        self.assertEqual(v["active_sec"], 0)
        self.assertEqual(v["idle_sec"], 7200)


class TestNoDataIsNotZero(unittest.TestCase):
    """«Журналов нет» и «расхода ноль» — разные утверждения, и второе успокаивает
    там, где успокаивать нечем."""

    def test_a_project_without_logs_returns_two(self):
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([sys.executable, str(TOOL), t],
                               capture_output=True, text=True, timeout=60,
                               env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
            self.assertEqual(r.returncode, 2)
            self.assertIn("не «ноль", r.stdout + r.stderr)

    def test_a_missing_directory_is_named(self):
        r = subprocess.run([sys.executable, str(TOOL), "/нет/такого"],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
        self.assertEqual(r.returncode, 3)


class TestTheReportSpeaksPlainRussian(unittest.TestCase):
    """Отчёт читает человек, который не пишет код: «cache_read» ему не говорит
    ничего, а «перечитано из памяти» говорит."""

    def test_the_human_report_has_no_jargon(self):
        import importlib.util as iu
        s = iu.spec_from_file_location(
            "ss_plain_measure", plug("superstack-core") / "tools" / "plain_ru.py")
        ru = iu.module_from_spec(s)
        s.loader.exec_module(ru)
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "s.jsonl"
            f.write_text(answer("m1"), encoding="utf-8")
            text = mr.human({"status": "pass", "sessions": [mr.measure_file(f)],
                             "total": {"tokens": {"out": 1, "cache_read": 1,
                                                  "cache_write": 1, "in": 1},
                                       "cost": 1, "answers": 1, "tool_calls": 1,
                                       "active_sec": 60, "idle_sec": 60,
                                       "helpers_over_ceiling": 0,
                                       "context_max": 1000}})
        self.assertEqual(ru.find_jargon(text), [])
        self.assertIn("перечитано", text)


if __name__ == "__main__":
    unittest.main()
