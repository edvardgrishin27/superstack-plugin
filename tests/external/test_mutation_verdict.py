#!/usr/bin/env python3
"""Исход поломки: поймана, выжила — или прогон не состоялся.

Сценарий пришёл из внешней проверки и переписан здесь регрессионным тестом
ПРАВИЛЬНОГО поведения. Ворота мутаций считали исход одной строкой
`caught = code != 0`, а `_sh` отдаёт 124 при таймауте и 127, когда процесс не
запустился вовсе. Обе аварии окружения читались как «поломка поймана»: на
коротком таймауте ворота отвечали «все 1 мутаций пойманы», не выполнив ни
одного прогона. Главное число продукта — «каждая зарегистрированная поломка
роняет набор» — держалось на том, что прогон вообще состоялся.

Проект здесь крошечный и ответ в нём известен заранее: тест `assert True` и
отдельный файл `VALUE = 1`, которого этот тест не читает. Такая подмена обязана
ВЫЖИТЬ. При заведомо срывающемся таймауте тот же прогон обязан сказать
«не состоялся», а не «поймана», — это три разных исхода, а не два.

Тест самодостаточен: только стандартная библиотека и сам инструмент.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

РЕПО = Path(__file__).resolve().parents[2]

_спек = importlib.util.spec_from_file_location(
    "ss_gauntlet_verdict", РЕПО / "tools" / "gauntlet.py")
gt = importlib.util.module_from_spec(_спек)
_спек.loader.exec_module(gt)

#: Подмена в файле, которого не читает ни один тест проекта. Верный ответ на
#: неё — «выжила»; любой другой означает, что ворота отвечают не по прогону.
МУТАЦИЯ = {"id": "проба.значение", "file": "значение.py",
           "find": "VALUE = 1", "replace": "VALUE = 2",
           "why": "подмена в файле, который не читает ни один тест проекта"}


@contextlib.contextmanager
def базовый_прогон_включён():
    """Снять признак «мы внутри чужого прогона набора».

    Базовый прогон — это прогон ВСЕГО набора, а в наборе есть тесты, которые
    зовут эти же ворота; поэтому изнутри набора ворота его не гоняют. Здесь
    признак снимается нарочно: проверяется само поведение, а не условие
    пропуска, и ответ не зависит от того, кто запустил набор — человек или
    прогон мутаций.
    """
    прежние = {имя: os.environ.pop(имя, None)
               for имя in ("PYTEST_CURRENT_TEST", "SUPERSTACK_MUTATION_RUN")}
    try:
        yield
    finally:
        for имя, значение in прежние.items():
            if значение is None:
                os.environ.pop(имя, None)
            else:
                os.environ[имя] = значение


class Проект(unittest.TestCase):
    """Дерево на два файла, где верный исход поломки известен заранее."""

    тело_теста = "def test_набор():\n    assert True\n"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)
        (self.корень / "tests").mkdir()
        (self.корень / "tests" / "test_набор.py").write_text(
            self.тело_теста, encoding="utf-8")
        (self.корень / "значение.py").write_text("VALUE = 1\n", encoding="utf-8")

    def срывающийся_таймаут(self):
        """Таймаут, которого заведомо не хватит ни одному прогону."""
        прежний = gt.MUTATION_TIMEOUT
        gt.MUTATION_TIMEOUT = 0.01
        self.addCleanup(setattr, gt, "MUTATION_TIMEOUT", прежний)


class ИсходПоломкиТрёхзначен(Проект):
    """Поймана, выжила, не состоялся прогон — и третье не равно первому."""

    def test_подмена_которую_никто_не_читает_выживает(self):
        with базовый_прогон_включён():
            v = gt._mutate_all([МУТАЦИЯ], self.корень)
        self.assertEqual(v["status"], "fail", v)
        self.assertEqual([s["id"] for s in v["survived"]], ["проба.значение"], v)
        self.assertEqual(v["caught"], 0, v)
        self.assertEqual(v["inconclusive"], [], v)

    def test_сорванный_таймаут_не_считается_пойманной(self):
        """Ровно тот случай из внешней проверки: 124 вместо ответа набора."""
        self.срывающийся_таймаут()
        with базовый_прогон_включён():
            v = gt._mutate_all([МУТАЦИЯ], self.корень)
        self.assertEqual([x["id"] for x in v["inconclusive"]],
                         ["проба.значение"], v)
        self.assertEqual(v["caught"], 0, v)
        self.assertEqual(v["survived"], [], v)
        self.assertEqual(v["status"], "unknown", v)

    def test_отчёт_называет_четыре_числа(self):
        """Одно громкое число вместо четырёх честных читается как проверка."""
        self.срывающийся_таймаут()
        with базовый_прогон_включён():
            v = gt._mutate_all([МУТАЦИЯ], self.корень)
        for кусок in ("зарегистрировано 1", "выполнено 0",
                      "поймано 0", "неизвестно 1"):
            self.assertIn(кусок, v["detail"], v)


class ВоротаНеОтдаютЧистыйPass(Проект):
    """Ненулевое «неизвестно» — это «не смог проверить», а не «взято»."""

    def test_несостоявшийся_прогон_гасит_вердикт_ворот(self):
        (self.корень / "tests" / "mutations.json").write_text(
            json.dumps({"mutations": [МУТАЦИЯ]}, ensure_ascii=False),
            encoding="utf-8")
        прежний = gt.PLUG
        gt.PLUG = self.корень
        self.addCleanup(setattr, gt, "PLUG", прежний)
        self.срывающийся_таймаут()
        with базовый_прогон_включён():
            v = gt.gate_mutations()
        self.assertEqual(v["status"], "unknown", v)
        self.assertEqual(v["caught"], 0, v)


class КрасныйБазовыйПрогонОбесцениваетИсходы(Проект):
    """Набор, красный ДО поломки, делает «пойманными» все мутации разом.

    Это самый дешёвый способ получить «все пойманы», не проверив ничего: набор
    падал и без подмены. По числам отчёта такой прогон неотличим от честного,
    поэтому базовый прогон обязан идти первым и называть причину.
    """

    тело_теста = "def test_набор():\n    assert False\n"

    def test_исходы_не_читаются_и_причина_названа(self):
        with базовый_прогон_включён():
            v = gt._mutate_all([МУТАЦИЯ], self.корень)
        self.assertEqual(v["status"], "unknown", v)
        self.assertEqual(v["caught"], 0, v)
        self.assertEqual(v["executed"], 0, v)
        self.assertIn("красный", v["detail"], v)
        self.assertNotIn("поймана", v["detail"], v)


if __name__ == "__main__":
    unittest.main()
