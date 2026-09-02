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
from paths import PKG  # noqa: E402

TOOL = PKG / "tools" / "report.py"
_s = importlib.util.spec_from_file_location("ss_report", TOOL)
rp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(rp)

_r = importlib.util.spec_from_file_location(
    "ss_review_report", PKG / "tools" / "review.py")
rv = importlib.util.module_from_spec(_r)
_r.loader.exec_module(rv)

_m = importlib.util.spec_from_file_location(
    "ss_memory_file_report", PKG / "tools" / "memory_file.py")
mf = importlib.util.module_from_spec(_m)
_m.loader.exec_module(mf)


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


class TestTheRunEndsWithAWayIn(unittest.TestCase):
    """Отчёт кончался текстом — и человек уходил, ни разу не открыв продукт.

    За весь прогон живьём его поднимали только помощники: ведущий для снимка и
    приёмщик для вердикта. Три ссылки, которые человек получал, вели в рабочую
    страницу, план и холст дизайна — ни одна в то, что построено.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / ".superstack"
        self.run.mkdir()
        run_dir(str(self.run), [{"id": "01", "name": "к", "status": "proven"}])

    def _команды(self, body: str) -> None:
        """Память проекта пишется ТЕМ ЖЕ инструментом, каким её читает отчёт:
        фикстура, собранная руками, доказывала бы разбор, а не стык."""
        p = self.root / mf.detect(self.root)["file"]
        mf.write_block(p, mf.apply_section("", "Команды", body))

    def test_the_recorded_launch_command_is_the_last_section(self):
        self._команды("| Команда | Что делает |\n|---|---|\n"
                      "| `npm run dev` | поднимает сайт |")
        text = rp.human(rp.gather(self.run))
        self.assertIn("КАК ОТКРЫТЬ", text)
        self.assertIn("npm run dev", text)
        self.assertLess(text.index("Частей работы"), text.index("КАК ОТКРЫТЬ"),
                        "раздел обязан быть последним — им человек уходит")

    def test_an_empty_section_never_becomes_a_command(self):
        """Живой прогон, а не фикстура: память заведена, команд в неё ещё не
        писали. Скелет держит в таблице прочерк — и он уехал в отчёт КАК
        команда, со стрелкой «эта строка поднимает построенное», указывающей
        на тире. Подтверждение без содержимого: человек уходит выполнять то,
        чего ему не дали."""
        случаи = {
            "скелет после init": mf.SKELETON.format(project="Живой", about=""),
            "раздел из одних тире": mf.apply_section("", "Команды", "— | –"),
            "раздела нет вовсе": "# Живой\n\nодна строка про проект\n",
        }
        p = self.root / mf.detect(self.root)["file"]
        for имя, блок in случаи.items():
            with self.subTest(случай=имя):
                mf.write_block(p, блок)
                text = rp.human(rp.gather(self.run))
                self.assertIn("не записан", text)
                self.assertNotIn("↑", text, "стрелка указывает на пустое место")

    def test_without_commands_it_says_so_and_hands_the_work_back(self):
        """«Не записаны» честнее выдуманной команды: выполнив не ту строку,
        человек ничего не увидит и решит, что не работает сам продукт."""
        text = rp.human(rp.gather(self.run))
        self.assertIn("КАК ОТКРЫТЬ", text)
        self.assertIn("команды запуска не записаны", text)
        self.assertIn("→ команды запуска не записаны", text)


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
