#!/usr/bin/env python3
"""Передача таска: обязательное вложено кодом, а не вспомнено.

Самое острое наблюдение AutoPilot: «правило, живущее только в этом файле, не
существует». Фазовые файлы читает оркестратор, код пишет субагент, который их
никогда не увидит. Там же сказано, что теряется чаще всего тестовый контракт —
он читается как совет, а не как вход.

У него это предупреждение, обращённое к модели. Здесь промпт собирает скрипт:
контракт вложен в сборку, и забыть его нельзя — не потому, что об этом просят,
а потому, что промпт без него не собирается этим кодом вовсе.

Второе, что здесь заперто, — отказ передавать неполный таск. Таск без критериев
приёмки исполнитель закончит там, где ему показалось достаточным; без команды
тестов «зелёный прогон» будет означать «я не запускал». Оба провала тихие, и
оба видны ДО того, как потрачен контекст исполнителя.
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

from paths import at

HANDOFF = at("tools", "handoff.py")
_h = importlib.util.spec_from_file_location("superstack_handoff", HANDOFF)
ho = importlib.util.module_from_spec(_h)
_h.loader.exec_module(ho)


def task(tid="01", **over):
    t = {"id": tid, "name": "приём заявок", "goal": "клиент пишет боту",
         "requirements": ["R01"], "zone": ["src/bot/"],
         "acceptance": ["диалог доходит до подтверждения"],
         "quotes": ["принимает заявки на ремонт техники"]}
    t.update(over)
    return t


def state(*tasks, wave="1"):
    return {"schema": "superstack.progress.v1", "waves": {wave: list(tasks)}}


class TestTheContractCannotBeForgotten(unittest.TestCase):
    """Класс, ради которого передача стала скриптом."""

    def test_testing_contract_is_always_in_the_prompt(self):
        p = ho.build(state(task()), task(), "уже построено", "", "npm test")
        self.assertIn("Ожидаемое значение бери откуда угодно, кроме кода под тестом", p)

    def test_testing_contract_goes_verbatim_not_summarised(self):
        """Пересказ и есть способ, которым правило перестаёт доезжать: каждый
        пересказ короче предыдущего, и первой выпадает строка про источник
        ожидаемого значения — та единственная, что ловит тавтологический тест."""
        p = ho.build(state(task()), task(), "x", "", "npm test")
        self.assertIn(ho.TESTING_CONTRACT, p)

    def test_return_contract_is_always_in_the_prompt(self):
        p = ho.build(state(task()), task(), "x", "", "npm test")
        self.assertIn(ho.RETURN_CONTRACT, p)

    def test_bounds_are_always_in_the_prompt(self):
        p = ho.build(state(task()), task(), "x", "", "npm test")
        self.assertIn("Не придумывай фактов о человеке", p)

    def test_verbatim_brief_quotes_reach_the_executor(self):
        """Сорок токенов, стоящие между свежим контекстом и правдоподобным
        перетолкованием заказа."""
        p = ho.build(state(task()), task(), "x", "", "npm test")
        self.assertIn("принимает заявки на ремонт техники", p)

    def test_what_was_already_built_comes_before_the_work(self):
        p = ho.build(state(task()), task(), "СИГНАТУРЫ ОТСЮДА", "", "npm test")
        self.assertIn("СИГНАТУРЫ ОТСЮДА", p)

    def test_the_context_ceiling_is_always_in_the_prompt(self):
        """Исполнитель не видит своих токенов и не знает, что у контекста есть
        край. Подойдя к нему без предупреждения, он выбирает между «сдать
        недоделанное как готовое» и «вернуть отказ»: первое врёт о готовности,
        второе о причине — он не «не смог», он не поместился.

        Считать предложено вызовы инструментов: единственная величина, которую
        он может посчитать сам.
        """
        p = ho.build(state(task()), task(), "x", "", "npm test")
        self.assertIn(ho.CEILING, p)
        self.assertIn("вызовы инструментов", p)

    def test_the_prompt_forbids_handing_on_red_work(self):
        """Красное, ушедшее дальше, становится чужой поломкой: принимающий
        тратит своё место на разбор того, чего не делал."""
        p = ho.build(state(task()), task(), "x", "", "npm test")
        self.assertIn("Красное не передавай", p)

    def test_the_return_contract_offers_the_handoff_status(self):
        p = ho.build(state(task()), task(), "x", "", "npm test")
        self.assertIn("HANDOFF", p)


class TestIncompleteTaskIsNotHandedOver(unittest.TestCase):

    def _blockers(self, t, interfaces="что-то", spec="", cmd="npm test", st=None):
        return ho.blockers(st or state(t), t, interfaces, spec, cmd)

    def test_task_without_requirements_is_refused(self):
        b = self._blockers(task(requirements=[]))
        self.assertTrue(any("не служит ни одному требованию" in x for x in b), b)

    def test_task_without_acceptance_criteria_is_refused(self):
        """Исполнитель не узнает, когда закончил, и вернёт то, что показалось
        достаточным."""
        b = self._blockers(task(acceptance=[]))
        self.assertTrue(any("критериев приёмки" in x for x in b), b)

    def test_missing_test_command_is_refused(self):
        b = self._blockers(task(), cmd="")
        self.assertTrue(any("команда тестов" in x for x in b), b)

    def test_missing_zone_is_refused(self):
        b = self._blockers(task(zone=[]))
        self.assertTrue(any("нет зоны" in x for x in b), b)

    def test_empty_interfaces_is_fine_for_the_first_task(self):
        """Первый и задаёт границы — ему читать нечего по построению."""
        b = self._blockers(task("01"), interfaces="", st=state(task("01")))
        self.assertFalse([x for x in b if "interfaces" in x], b)

    def test_empty_interfaces_is_refused_for_a_later_task(self):
        """Иначе архитектуру задаст тот, кто увидел одну восьмую задачи, а
        остальные будут её обходить."""
        st = state(task("01"), task("02"))
        b = self._blockers(task("02"), interfaces="", st=st)
        self.assertTrue(any("interfaces.md пуст" in x for x in b), b)

    def test_named_spec_section_that_is_not_in_the_spec_is_refused(self):
        t = task(spec_sections=["Истории 1-5", "Решения §9"])
        b = self._blockers(t, spec="## Истории 1-5\nтекст\n")
        self.assertTrue(any("которых в ней нет" in x for x in b), b)

    def test_complete_task_has_no_blockers(self):
        self.assertEqual(self._blockers(task()), [])


class TestExitCodes(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "iface.md").write_text("# Что уже построено\n", encoding="utf-8")

    def _run(self, st, tid, *args):
        (self.dir / "s.json").write_text(json.dumps(st, ensure_ascii=False),
                                         encoding="utf-8")
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(HANDOFF), "s.json", tid, *args],
                              cwd=str(self.dir), capture_output=True, text=True,
                              timeout=120, env=env)

    def test_complete_task_exits_zero_and_prints_the_prompt(self):
        p = self._run(state(task()), "01", "--interfaces", "iface.md",
                      "--test-cmd", "npm test")
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertIn("Тесты — обязательное", p.stdout)

    def test_incomplete_task_exits_one_and_prints_no_prompt(self):
        """Отказ обязан быть ПУСТЫМ на выходе: напечатанный наполовину промпт
        кто-нибудь скопирует."""
        p = self._run(state(task(acceptance=[])), "01", "--interfaces", "iface.md",
                      "--test-cmd", "npm test")
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("Тесты — обязательное", p.stdout)

    def test_unknown_task_exits_three(self):
        p = self._run(state(task()), "99", "--interfaces", "iface.md")
        self.assertEqual(p.returncode, 3)

    def test_flag_value_is_not_taken_for_a_positional(self):
        p = self._run(state(task()), "01", "--interfaces", "iface.md",
                      "--test-cmd", "npm test")
        self.assertNotIn("вызов: handoff.py", p.stderr)


if __name__ == "__main__":
    unittest.main()
