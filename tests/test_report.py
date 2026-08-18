#!/usr/bin/env python3
"""Финальный отчёт — из файлов, а не по памяти.

К последней фазе контекст ведущего самый загрязнённый за прогон: в нём осели
восемь возвратов, три ревью и десяток починок. Отчёт по памяти получается
пересказом впечатлений, и первым из него выпадает то, что человеку нужнее
всего: чего НЕ сделали.

Здесь заперто два правила, оба из живого отчёта:

  · «готово» — только доказанное кодом возврата; «со слов помощника» идёт
    отдельной строкой и другим словом;
  · закрытые находки не смешиваются с открытыми. В первом же собранном отчёте
    из одиннадцати строк «найдено и не закрыто» шесть были уже починены —
    список, где половина неверна, теряет доверие целиком.
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

TOOL = plug("superstack-build") / "tools" / "report.py"
_s = importlib.util.spec_from_file_location("ss_report", TOOL)
rp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rp)

_r = importlib.util.spec_from_file_location(
    "ss_review_report", plug("superstack-guard") / "tools" / "review.py")
rv = importlib.util.module_from_spec(_r)
_r.loader.exec_module(rv)


def run_dir(tmp: str, tasks: list, findings: list = None, reqs: list = None) -> Path:
    d = Path(tmp)
    (d / "state.json").write_text(json.dumps({
        "waves": {"1": tasks}, "debt": {}, "phase": {"name": "Отчёт"}}),
        encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({
        "requirements": reqs or [], "blind": {}}), encoding="utf-8")
    if findings is not None:
        (d / "review-01.json").write_text(
            json.dumps({"findings": findings}), encoding="utf-8")
    return d


class TestProofIsNotWords(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_claimed_work_is_named_differently(self):
        d = run_dir(self.tmp.name, [
            {"id": "01", "name": "каркас", "status": "proven"},
            {"id": "02", "name": "галерея", "status": "claimed"}])
        text = rp.human(rp.gather(d))
        self.assertIn("проверено машиной", text)
        self.assertIn("СО СЛОВ ПОМОЩНИКА", text)

    def test_nothing_proven_says_so_plainly(self):
        """Пустой раздел «готово» честнее умолчания: иначе отчёт выглядит
        успешным ровно потому, что хвалиться нечем."""
        d = run_dir(self.tmp.name, [{"id": "01", "name": "к", "status": "claimed"}])
        self.assertIn("ничего не доказано", rp.human(rp.gather(d)))


class TestClosedFindingsLeaveTheOpenList(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_closed_finding_moves_to_the_fixed_section(self):
        d = run_dir(self.tmp.name, [{"id": "01", "name": "к", "status": "proven"}],
                    findings=[{"axis": "craft", "what": "картинки битые",
                               "must": "картинки рисуются", "blocking": True,
                               "closed": "таск 08: SVG перевыпущен"}])
        text = rp.human(rp.gather(d))
        self.assertIn("НАЙДЕНО И ПОЧИНЕНО", text)
        self.assertNotIn("! картинки битые", text)

    def test_an_open_finding_stays_open(self):
        d = run_dir(self.tmp.name, [{"id": "01", "name": "к", "status": "proven"}],
                    findings=[{"axis": "craft", "what": "мест показывается неверно",
                               "must": "показывается остаток", "blocking": True}])
        self.assertIn("НАЙДЕНО И НЕ ЗАКРЫТО", rp.human(rp.gather(d)))

    def test_closing_requires_saying_what_closed_it(self):
        """Находка, снятая без основания, отличается от забытой только словом."""
        data = {"findings": [{"axis": "craft", "what": "x", "must": "y"}]}
        with self.assertRaises(ValueError):
            rv.close_finding(data, 0, "   ")

    def test_closing_a_missing_finding_is_refused(self):
        with self.assertRaises(ValueError):
            rv.close_finding({"findings": []}, 3, "чем-то")


class TestWhatTheHumanMustDoComesFirst(unittest.TestCase):

    def test_placeholders_are_listed_as_his_work(self):
        with tempfile.TemporaryDirectory() as t:
            d = run_dir(t, [{"id": "01", "name": "к", "status": "proven"}],
                        reqs=[{"id": "R01", "status": "placeholder",
                               "basis": "нужен телефон студии"}])
            self.assertIn("ЧТО ЖДЁТ ТЕБЯ", rp.human(rp.gather(d)))


class TestExitCodes(unittest.TestCase):

    def _run(self, path):
        return subprocess.run([sys.executable, str(TOOL), str(path)],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_no_run_returns_two(self):
        """«Прогона нет» и «прогон пустой» — разные утверждения."""
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(self._run(t).returncode, 2)

    def test_a_real_run_returns_zero(self):
        with tempfile.TemporaryDirectory() as t:
            run_dir(t, [{"id": "01", "name": "к", "status": "proven"}])
            self.assertEqual(self._run(t).returncode, 0)


if __name__ == "__main__":
    unittest.main()
