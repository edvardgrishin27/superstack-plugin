#!/usr/bin/env python3
"""Критерий приёмки без адреса — это решение, возвращённое исполнителю.

Дефект, ради которого инструмент существует. `handoff.py` проверял у критериев
ровно одно: что список непуст. «Работает корректно» проходило передачу, и
дальше исполнитель сам решал, где смотреть и что считать выполненным, — то
есть критерий, заведённый ради того, чтобы это решал не он, возвращал решение
ему же.

Здесь заперта ФОРМА: адрес назван, границы проведены, критерии не перетекают.
Правоту критерия не проверяет ничто — и инструмент говорит об этом на каждом
зелёном, чтобы «критерий можно проверить» не читалось как «критерий верный».
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, skill_text  # noqa: E402

TOOL = PKG / "tools" / "criteria.py"
_s = importlib.util.spec_from_file_location("ss_criteria", TOOL)
cr = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cr)

ГОДНЫЙ = {"что": "заявка доходит до подтверждения",
          "где": "tests/test_bot.py::test_confirm",
          "высоко": "тест зелёный и в ответе виден номер заявки",
          "низко": "ответ приходит, номера нет — заявка не создана"}


class Основа(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "tests").mkdir()
        (self.d / "tests" / "test_bot.py").write_text("# есть\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def запуск(self, критерии: list, *флаги: str):
        состояние = {"schema": "superstack.progress.v1",
                     "waves": {"1": [{"id": "01", "acceptance": критерии}]}}
        f = self.d / "state.json"
        f.write_text(json.dumps(состояние, ensure_ascii=False), encoding="utf-8")
        p = subprocess.run([sys.executable, str(TOOL), str(f), "01",
                            "--root", str(self.d), *флаги],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr


class АдресОбязателен(Основа):

    def test_полный_критерий_проходит(self):
        код, текст = self.запуск([ГОДНЫЙ], "--check")
        self.assertEqual(код, 0, текст)

    def test_критерий_строкой_не_проходит(self):
        """Ровно та форма, которую пропускал прежний блокер."""
        код, текст = self.запуск(["работает корректно"], "--check")
        self.assertEqual(код, 1)
        self.assertIn("адрес не назван", текст)

    def test_несуществующий_путь_валит(self):
        """Адрес, ведущий в никуда, хуже отсутствующего: он выглядит адресом."""
        код, текст = self.запуск([dict(ГОДНЫЙ, где="tests/нет.py::x")], "--check")
        self.assertEqual(код, 1)
        self.assertIn("не существует", текст)

    def test_команда_адрес_законный(self):
        """`npm test` проверить на диске нельзя — и валить его нельзя тоже."""
        код, текст = self.запуск(
            [{"что": "набор зелёный", "где": "npm test",
              "высоко": "код 0", "низко": "любой ненулевой"}], "--check")
        self.assertEqual(код, 0, текст)

    def test_отговорка_вместо_адреса(self):
        """«в коде» и «app.py» одной длины — различает список, а не порог."""
        код, текст = self.запуск([dict(ГОДНЫЙ, где="в коде")], "--check")
        self.assertEqual(код, 1)
        self.assertIn("отговорка", текст)

    def test_короткий_но_настоящий_адрес_проходит(self):
        код, текст = self.запуск([dict(ГОДНЫЙ, где="app.py")], "--check")
        self.assertEqual(код, 1, "путь есть в адресе, но файла нет — верный отказ")
        self.assertIn("не существует", текст)


class ГраницаПроведена(Основа):

    def test_без_низко_не_проходит(self):
        """Без нижней границы «почти работает» засчитывается как сделанное."""
        к = dict(ГОДНЫЙ)
        del к["низко"]
        код, текст = self.запуск([к], "--check")
        self.assertEqual(код, 1)
        self.assertIn("нет поля «низко»", текст)

    def test_пустая_строка_не_считается_полем(self):
        код, _ = self.запуск([dict(ГОДНЫЙ, высоко="   ")], "--check")
        self.assertEqual(код, 1)


class КритерииНеПеретекают(Основа):

    def test_два_критерия_в_одно_место_без_мимо(self):
        """Один изъян уронит оба, а то, что проверял второй, не проверит никто."""
        код, текст = self.запуск([ГОДНЫЙ, dict(ГОДНЫЙ, что="другое")], "--check")
        self.assertEqual(код, 1)
        self.assertIn("перетекают", текст)

    def test_с_мимо_разрешено(self):
        второй = dict(ГОДНЫЙ, что="другое", мимо="текст ответа — не этот критерий")
        первый = dict(ГОДНЫЙ, мимо="номер заявки — не этот критерий")
        код, текст = self.запуск([первый, второй], "--check")
        self.assertEqual(код, 0, текст)

    def test_разные_адреса_разграничивать_нечего(self):
        код, текст = self.запуск(
            [ГОДНЫЙ, dict(ГОДНЫЙ, что="другое", где="npm test")], "--check")
        self.assertEqual(код, 0, текст)


class ПроверятьНечегоНеЗначитПрошло(Основа):

    def test_нет_критериев_код_два(self):
        код, текст = self.запуск([], "--check")
        self.assertEqual(код, 2, "отсутствие критериев принято за пройденное")
        self.assertIn("ПРОВЕРЯТЬ НЕЧЕГО", текст)

    def test_показ_без_check_не_гейт(self):
        self.assertEqual(self.запуск(["работает"])[0], 0)
        self.assertEqual(self.запуск(["работает"], "--check")[0], 1)

    def test_непроверенное_названо_и_на_зелёном(self):
        код, текст = self.запуск([ГОДНЫЙ], "--check")
        self.assertEqual(код, 0)
        self.assertIn("НЕ ПРОВЕРЕНО здесь", текст)
        self.assertIn("верен ли адрес", текст)


class СтрогостьОбъявляется(unittest.TestCase):
    """Молчаливое ужесточение сломало бы проект на середине волны."""

    def test_умолчание_нестрогое(self):
        self.assertFalse(cr.строгие({}))
        self.assertFalse(cr.строгие({"criteria": "свободные"}))

    def test_объявленное_строгое(self):
        self.assertTrue(cr.строгие({"criteria": "строгие"}))


class ФазаВелитПисатьАдреса(unittest.TestCase):
    """Инструмент, до которого не дотягивается скилл, неотличим от отсутствующего."""

    @classmethod
    def setUpClass(cls):
        cls.текст = skill_text("go")

    def test_скилл_зовёт_инструмент(self):
        self.assertIn("criteria.py", self.текст)

    def test_скилл_велит_объявить_строгость(self):
        """Проверяется ВЫЗОВ, а не имя ключа: ключ — деталь, недостижимая
        команда — дефект. Раньше здесь стояла строка с несуществующим
        `progress.py set`, и тест на имя ключа этого не замечал."""
        self.assertIn("progress.py\" criteria .superstack/state.json строгие",
                      self.текст)

    def test_скилл_показывает_как_писать_критерий(self):
        self.assertIn("--criterion", self.текст)

    def test_названы_поля_критерия(self):
        for поле in ("где", "высоко", "низко"):
            self.assertIn(f"«{поле}»", self.текст, f"поле {поле} не названо")


if __name__ == "__main__":
    unittest.main()
