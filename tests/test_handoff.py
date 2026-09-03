#!/usr/bin/env python3
"""Передача таска: обязательное вложено кодом, а не вспомнено.

Правило, живущее только в файле фазы, не существует для того, кто этот файл
не читает. Фазовые файлы читает оркестратор, код пишет субагент, который их
никогда не увидит. Теряется чаще всего тестовый контракт — он читается как
совет, а не как вход.

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


ПАЛИТРА = {"фон": "#0a0a0c", "текст": "#f2f2f4",
           "приглушённый": "#86868f", "акцент": "#6355d9"}
ДОСКА = ("<div style=\"background:#0a0a0c;color:#f2f2f4\">"
         "<p style=\"color:#86868f\">т</p>"
         "<button style=\"background:#6355d9\">д</button></div>")


class TestInterfaceTaskWaitsForTheDesign(unittest.TestCase):
    """Дизайн объявлен — значит он обязан быть подтянут, а не пересказан.

    Живой случай, из которого выросла и фаза, и этот блокер: направление
    перевели в восемь чисел и отдали строителю. Он сверстал по всем числам, и
    человек увидел ГОТОВЫЙ САЙТ, ни разу не увидев дизайна: палитру выбрал
    строитель. Числа — это критерии приёмки, а не дизайн.
    """

    def холст(self, каталог: Path, токены: dict = None) -> Path:
        (каталог / "Main.dc.html").write_text(ДОСКА, encoding="utf-8")
        (каталог / "tokens.json").write_text(
            json.dumps(токены or ПАЛИТРА, ensure_ascii=False), encoding="utf-8")
        (каталог / "pulled.json").write_text(json.dumps(
            {"откуда": "extract", "когда": "2026-08-24T10:00:00Z",
             "холст": "https://claude.ai/public/artifacts/пример"},
            ensure_ascii=False), encoding="utf-8")
        return каталог

    def _b(self, st, design_dir=None):
        return ho.blockers(st, task(), "что-то", "", "npm test", None, design_dir)

    def test_project_without_a_declared_design_is_untouched(self):
        """Бэкенд и бот не должны спотыкаться о ворота, которых не объявляли."""
        self.assertEqual(self._b(state(task())), [])

    def test_declared_design_missing_from_the_handoff_is_refused(self):
        st = dict(state(task()), design_dir=".superstack/design")
        b = self._b(st)
        self.assertTrue(any("объявил дизайн" in x for x in b), b)

    def test_pulled_design_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self.холст(Path(tmp))
            st = dict(state(task()), design_dir=str(d))
            self.assertEqual(self._b(st, d), [])

    def test_invented_colour_stops_the_handoff(self):
        """Тот самый отказ: цвет, которого человек не видел, дальше не едет."""
        with tempfile.TemporaryDirectory() as tmp:
            d = self.холст(Path(tmp), dict(ПАЛИТРА, акцент="#ff0055"))
            st = dict(state(task()), design_dir=str(d))
            b = self._b(st, d)
            self.assertTrue(any("дизайн не подтянут" in x for x in b), b)
            self.assertTrue(any("#ff0055" in x for x in b), b)

    def test_nothing_to_check_is_not_a_pass(self):
        """Пустой каталог — это «дизайна нет», а не «дизайн в порядке»."""
        with tempfile.TemporaryDirectory() as tmp:
            st = dict(state(task()), design_dir=tmp)
            b = self._b(st, Path(tmp))
            self.assertTrue(any("дизайн не подтянут" in x for x in b), b)


class TestStrictCriteriaStopTheHandoff(unittest.TestCase):
    """Критерий без адреса возвращает исполнителю решение, ради которого он заведён."""

    АДРЕСНЫЙ = {"что": "заявка доходит до подтверждения",
                "где": "npm test",
                "высоко": "набор зелёный",
                "низко": "красный или не запускался"}

    def _b(self, st, t=None, корень=None):
        return ho.blockers(st, t or task(), "что-то", "", "npm test",
                           None, None, корень)

    def test_loose_project_is_untouched(self):
        """Проект, не объявлявший строгость, продолжает жить как жил."""
        self.assertEqual(self._b(state(task())), [])

    def test_string_criterion_is_refused_when_strict(self):
        t = task(acceptance=["работает корректно"])
        st = dict(state(t), criteria="строгие")
        b = self._b(st, t)
        self.assertTrue(any("критерии без адреса" in x for x in b), b)

    def test_addressed_criterion_passes_when_strict(self):
        t = task(acceptance=[self.АДРЕСНЫЙ])
        st = dict(state(t), criteria="строгие")
        self.assertEqual(self._b(st, t), [])

    def test_missing_file_in_where_is_refused(self):
        """Адрес, ведущий в никуда, хуже отсутствующего: он выглядит адресом."""
        with tempfile.TemporaryDirectory() as tmp:
            t = task(acceptance=[dict(self.АДРЕСНЫЙ, где="tests/нет.py::x")])
            st = dict(state(t), criteria="строгие")
            b = self._b(st, t, Path(tmp))
            self.assertTrue(any("не существует" in x for x in b), b)


class TestSingleEditPromptIsBuiltByCode(unittest.TestCase):
    """`/change` собирает промпт кодом, а не заготовкой в тексте скилла.

    Заготовка держится вниманием модели, и первыми из неё выпадают строки,
    выглядящие необязательными, — зона и команда тестов. Без зоны правка
    растекается по соседним файлам, без команды тестов «зелёный прогон»
    означает «я не запускал». Ожидание собрано по условию задачи: обе строки
    обязаны стоять в тексте, который уедет исполнителю.
    """

    def test_zone_and_test_command_reach_the_prompt(self):
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        p = subprocess.run(
            [sys.executable, str(HANDOFF),
             "--single", "убери поле телефона из формы заказа",
             "--zone", "src/order/checkout.tsx",
             "--done", "поля телефона нет, заказ отправляется",
             "--tests", "npm test"],
            capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assertIn("src/order/checkout.tsx", p.stdout)
        self.assertIn("npm test", p.stdout)
        self.assertIn("убери поле телефона из формы заказа", p.stdout)
