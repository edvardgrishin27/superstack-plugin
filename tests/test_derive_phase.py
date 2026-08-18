#!/usr/bin/env python3
"""Этап вычисляется по следам на диске — и это проверяется на КАЖДОМ этапе.

Почему набор устроен как лестница.

Панель показывала этап, записанный отдельной командой. За один вечер её забыли
дважды: сначала помощник писал код, пока на экране висел дизайн; потом проверки
закончились, а экран час показывал «Проверяем». Оба раза человек смотрел на
панель и видел неправду — уверенную, без единого признака, что она устарела.

Первая починка двигала этап автоматически ровно в одну сторону — в «Пишем код».
Этого мало и это было видно сразу: остальные восемь переходов по-прежнему
держались на дисциплине того, кто ведёт прогон. Здесь проверяется, что ни один
этап не остался на честном слове: каталог наполняется следами по одному, и на
каждом шаге вывод обязан назвать правильный этап и правильного держателя хода.
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

TOOL = plug("superstack-core") / "tools" / "derive_phase.py"
_s = importlib.util.spec_from_file_location("ss_derive_phase", TOOL)
dp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(dp)

_l = importlib.util.spec_from_file_location(
    "ss_live_panel_derive", plug("superstack-core") / "tools" / "live_panel.py")
lp = importlib.util.module_from_spec(_l)
_l.loader.exec_module(lp)

_p = importlib.util.spec_from_file_location(
    "ss_progress_derive", plug("superstack-build") / "tools" / "progress.py")
pr = importlib.util.module_from_spec(_p)
_p.loader.exec_module(pr)


def state(*tasks) -> dict:
    d = json.loads(json.dumps(pr.EMPTY))
    if tasks:
        d["waves"]["1"] = list(tasks)
    return d


def task(tid: str, status: str) -> dict:
    return {"id": tid, "name": f"часть {tid}", "status": status}


class Run:
    """Каталог прогона, который наполняется следами по одному."""

    def __init__(self, tmp: str):
        self.p = Path(tmp)

    def brief(self):
        (self.p / "2026-08-15-brief.md").write_text("хочу сайт", encoding="utf-8")
        return self

    def premortem(self):
        (self.p / "premortem.json").write_text("{}", encoding="utf-8")
        return self

    def spec(self):
        (self.p / "spec.md").write_text("# спека", encoding="utf-8")
        return self

    def design_prompt(self):
        (self.p / "design-brief.json").write_text("{}", encoding="utf-8")
        return self

    def design_system(self):
        (self.p / "design").mkdir(exist_ok=True)
        (self.p / "design" / "SYSTEM.md").write_text("# система", encoding="utf-8")
        return self

    def design_screens(self):
        (self.p / "design").mkdir(exist_ok=True)
        (self.p / "design" / "SCREENS.md").write_text("# экраны", encoding="utf-8")
        return self

    def tasks(self, *ts):
        (self.p / "state.json").write_text(json.dumps(state(*ts)), encoding="utf-8")
        return self

    def returned(self, tid="01"):
        (self.p / f"return-{tid}.txt").write_text("STATUS: DONE", encoding="utf-8")
        return self

    def blocking_finding(self):
        (self.p / "review-01.json").write_text(
            json.dumps({"findings": [{"axis": "manifest", "blocking": True}]}),
            encoding="utf-8")
        return self

    def report(self):
        (self.p / "report.html").write_text("<p>итог</p>", encoding="utf-8")
        return self

    def phase(self):
        return dp.derive(self.p)


class TestEveryPhaseIsDerivedFromTraces(unittest.TestCase):
    """Лестница: каждый след добавляется поверх прошлых, как в живом прогоне."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Run(self.tmp.name)

    def test_an_empty_run_waits_for_the_human(self):
        got = self.r.phase()
        self.assertEqual(got["name"], "Записали просьбу")
        self.assertEqual(got["owner"], dp.HUMAN)

    def test_a_brief_alone_is_the_first_phase(self):
        self.assertEqual(self.r.brief().phase()["name"], "Записали просьбу")

    def test_the_premortem_moves_it_on(self):
        self.assertEqual(self.r.brief().premortem().phase()["name"],
                         "Разобрали задачу")

    def test_the_spec_moves_it_on(self):
        self.assertEqual(self.r.brief().premortem().spec().phase()["name"],
                         "Описали, что строим")

    def test_a_prompt_without_the_system_waits_for_the_human(self):
        """Промпт собран, системы нет — человек ушёл её делать. Ровно тот
        случай, где оба молчат и каждый ждёт другого."""
        got = self.r.brief().spec().design_prompt().phase()
        self.assertEqual(got["name"], "Дизайн-система")
        self.assertEqual(got["owner"], dp.HUMAN)

    def test_the_brought_system_returns_the_turn(self):
        got = self.r.brief().spec().design_prompt().design_system().phase()
        self.assertEqual(got["name"], "Дизайн-система")
        self.assertEqual(got["owner"], dp.SYSTEM)

    def test_the_screens_move_it_on(self):
        got = self.r.brief().spec().design_system().design_screens().phase()
        self.assertEqual(got["name"], "Дизайн экранов")

    def test_tasks_mean_the_plan_exists(self):
        got = self.r.brief().spec().design_system().tasks(task("01", "waiting")).phase()
        self.assertEqual(got["name"], "План работ")

    def test_a_running_task_means_code_is_being_written(self):
        got = self.r.brief().spec().tasks(task("01", "running")).phase()
        self.assertEqual(got["name"], "Пишем код")
        self.assertEqual(got["owner"], dp.EXECUTOR)

    def test_returns_without_running_mean_checking(self):
        got = self.r.brief().spec().tasks(task("01", "claimed")).returned().phase()
        self.assertEqual(got["name"], "Проверяем")
        self.assertEqual(got["owner"], dp.SYSTEM)

    def test_all_proven_means_acceptance(self):
        got = self.r.brief().spec().tasks(task("01", "proven")).returned().phase()
        self.assertEqual(got["name"], "Приёмка")

    def test_the_report_is_the_last_phase(self):
        got = self.r.brief().spec().tasks(task("01", "proven")).report().phase()
        self.assertEqual(got["name"], "Отчёт")

    def test_every_phase_of_the_panel_is_reachable(self):
        """Прямая проверка требования: ни один этап не остался без вывода.

        Этап, которого вывод не умеет назвать, на панели не загорится никогда —
        и узнать об этом можно было бы только не увидев его в живом прогоне.
        """
        seen = set()
        for build in (
            lambda r: r,
            lambda r: r.brief(),
            lambda r: r.brief().premortem(),
            lambda r: r.brief().premortem().spec(),
            lambda r: r.brief().spec().design_prompt(),
            lambda r: r.brief().spec().design_system().design_screens(),
            lambda r: r.brief().spec().tasks(task("01", "waiting")),
            lambda r: r.brief().spec().tasks(task("01", "running")),
            lambda r: r.brief().spec().tasks(task("01", "claimed")).returned(),
            lambda r: r.brief().spec().tasks(task("01", "proven")).returned(),
            lambda r: r.brief().spec().tasks(task("01", "proven")).report(),
        ):
            with tempfile.TemporaryDirectory() as t:
                seen.add(build(Run(t))["name"] if isinstance(build(Run(t)), dict)
                         else build(Run(t)).phase()["name"])
        self.assertEqual(set(lp.PHASES) - seen, set(),
                         "эти этапы вывод не умеет назвать — на панели они не "
                         "загорятся никогда")


class TestTheTurnGoesToTheHumanWhenItMust(unittest.TestCase):
    """«Ход на человеке» — самое дорогое состояние панели: пока оно не названо,
    все ждут друг друга молча."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Run(self.tmp.name)

    def test_a_blocking_finding_hands_the_turn_over(self):
        got = (self.r.brief().spec().tasks(task("01", "claimed"))
               .returned().blocking_finding().phase())
        self.assertEqual(got["owner"], dp.HUMAN)

    def test_without_findings_the_system_keeps_working(self):
        got = self.r.brief().spec().tasks(task("01", "claimed")).returned().phase()
        self.assertEqual(got["owner"], dp.SYSTEM)

    def test_the_holders_are_the_same_words_as_in_progress(self):
        """Разошлись бы молча, и панель показала бы роль, которой нет в словаре
        человеческих слов — то есть служебное слово."""
        for owner in (dp.HUMAN, dp.SYSTEM, dp.EXECUTOR):
            self.assertIn(owner, pr.OWNERS)


class TestTheAnswerCarriesItsGrounds(unittest.TestCase):
    """Вывод без оснований — оракул: угадал или нет, проверить нечем."""

    def test_the_reason_is_stated(self):
        with tempfile.TemporaryDirectory() as t:
            got = Run(t).brief().spec().tasks(task("01", "running")).phase()
            self.assertTrue(got["why"].strip())
            self.assertIn("в работе", got["facts"])

    def test_the_reason_speaks_plain_russian(self):
        import importlib.util as iu
        s = iu.spec_from_file_location(
            "ss_plain_derive", plug("superstack-core") / "tools" / "plain_ru.py")
        ru = iu.module_from_spec(s)
        s.loader.exec_module(ru)
        with tempfile.TemporaryDirectory() as t:
            for build in (lambda r: r.brief(),
                          lambda r: r.brief().spec().tasks(task("01", "running")),
                          lambda r: r.brief().spec().design_prompt()):
                got = build(Run(tempfile.mkdtemp()))
                self.assertEqual(ru.find_jargon(got.phase()["why"]), [],
                                 f"в основании жаргон: {got.phase()['why']}")


class TestThePanelServesTheDerivedPhase(unittest.TestCase):
    """Вычисление, которое никуда не приезжает, ничего не чинит: панель обязана
    брать этап отсюда, а не из записи, которую забывают сделать."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_the_served_state_carries_the_derived_phase(self):
        Run(self.tmp.name).brief().spec().tasks(task("01", "running"))
        got = lp.state_with_phase(self.d)
        self.assertEqual(got["phase"]["name"], "Пишем код")
        self.assertEqual(got["phase"]["owner"], dp.EXECUTOR)

    def test_a_stale_written_phase_does_not_win(self):
        """Ровно живой случай: в записи «Дизайн-система», а помощник уже пишет."""
        Run(self.tmp.name).brief().spec().tasks(task("01", "running"))
        d = json.loads((self.d / "state.json").read_text("utf-8"))
        d["phase"] = {"name": "Дизайн-система", "owner": "человек",
                      "detail": "ушёл собирать систему", "since": "2026-08-17T10:00:00+00:00"}
        (self.d / "state.json").write_text(json.dumps(d), encoding="utf-8")
        got = lp.state_with_phase(self.d)
        self.assertEqual(got["phase"]["name"], "Пишем код")

    def test_a_caption_from_another_phase_is_dropped(self):
        """Заголовок верный, объяснение под ним от прошлого часа — самая тихая
        форма вранья, заметить её нельзя."""
        Run(self.tmp.name).brief().spec().tasks(task("01", "running"))
        d = json.loads((self.d / "state.json").read_text("utf-8"))
        d["phase"] = {"name": "Дизайн-система", "owner": "человек",
                      "detail": "ушёл собирать систему"}
        (self.d / "state.json").write_text(json.dumps(d), encoding="utf-8")
        self.assertEqual(lp.state_with_phase(self.d)["phase"]["detail"], "")

    def test_the_caption_survives_when_the_phase_agrees(self):
        Run(self.tmp.name).brief().spec().tasks(task("01", "running"))
        d = json.loads((self.d / "state.json").read_text("utf-8"))
        d["phase"] = {"name": "Пишем код", "owner": "исполнитель",
                      "detail": "помощник пишет страницу", "since": "2026-08-17T10:00:00+00:00"}
        (self.d / "state.json").write_text(json.dumps(d), encoding="utf-8")
        got = lp.state_with_phase(self.d)["phase"]
        self.assertEqual(got["detail"], "помощник пишет страницу")
        self.assertEqual(got["since"], "2026-08-17T10:00:00+00:00",
                         "счётчик обнулился — застрявший этап выглядел бы свежим")


class TestCommandLine(unittest.TestCase):

    def _run(self, d, *args):
        return subprocess.run([sys.executable, str(TOOL), str(d), *args],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_a_missing_directory_is_named(self):
        r = self._run(Path("/нет/такого/каталога"))
        self.assertEqual(r.returncode, 3)
        self.assertIn("НЕ УДАЛОСЬ", r.stderr)

    def test_name_only_prints_one_line(self):
        with tempfile.TemporaryDirectory() as t:
            Run(t).brief().spec().tasks(task("01", "running"))
            r = self._run(Path(t), "--name")
            self.assertEqual(r.returncode, 0, r.stderr[-200:])
            self.assertEqual(r.stdout.strip(), "Пишем код")


if __name__ == "__main__":
    unittest.main()


class TestClosedFindingsDoNotHoldTheTurn(unittest.TestCase):
    """Починенная находка не должна держать ход на человеке: «сейчас нужен ты»
    там, где от него ничего не нужно, — ложная тревога, а после пары таких
    панель перестают читать целиком."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_an_open_finding_hands_the_turn_over(self):
        r = (Run(self.tmp.name).brief().spec()
             .tasks(task("01", "claimed")).returned().blocking_finding())
        self.assertEqual(r.phase()["owner"], dp.HUMAN)

    def test_a_closed_finding_does_not(self):
        r = Run(self.tmp.name).brief().spec().tasks(task("01", "claimed")).returned()
        (Path(self.tmp.name) / "review-01.json").write_text(
            json.dumps({"findings": [{"axis": "manifest", "blocking": True,
                                      "closed": "часть 09: починено"}]}),
            encoding="utf-8")
        self.assertEqual(r.phase()["owner"], dp.SYSTEM)
