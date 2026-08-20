#!/usr/bin/env python3
"""Вес скилла на загрузке — тем же прибором, каким меряем чужое.

Продукт считает конституцию человека и зовёт находкой всё, что толще двухсот
строк. Свой главный скилл не считал никто, и он вырос до 55 килобайт — вчетверо
выше того потолка, который мы ставим другим. Асимметрия «прибор для чужих, не
для себя» — ровно то, что все остальные ворота ловят этажом ниже.

Здесь заперты три отказа:

  · потолка нет вовсе — правило без числа не нарушается никогда;
  · текст вынесен в файл, который читают КАЖДЫЙ раз, и это назвали облегчением;
  · вынесенный файл не назван в стержне — фаза недостижима, зато вес красивый.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_gauntlet_weight",
                                            REPO / "tools" / "gauntlet.py")
gt = importlib.util.module_from_spec(_s)
_s.loader.exec_module(gt)


class ПотолокЕсть(unittest.TestCase):

    def test_потолок_назван_числом(self):
        """Правило без числа не нарушается никогда — и потому не правило."""
        self.assertIsInstance(gt.ВЕС_ПОТОЛОК, int)
        self.assertGreater(gt.ВЕС_ПОТОЛОК, 0)

    def test_ворота_в_списке(self):
        self.assertIn("вес", {имя for имя, _ in gt.GATES})

    def test_все_скиллы_под_потолком(self):
        v = gt.gate_weight()
        self.assertEqual(v["status"], "pass", v.get("detail"))

    def test_вес_считается_в_байтах_а_не_в_символах(self):
        """Русский символ — два байта. Мерить символами значит занижать вдвое,
        и ровно так я однажды отчитался «33 445» там, где было 55 011."""
        v = gt.gate_weight()
        go = PKG / "skills" / "go" / "SKILL.md"
        текст = go.read_text("utf-8")
        self.assertEqual(v["weights"]["go"]["старт"],
                         len(текст.encode("utf-8")))
        self.assertGreater(len(текст.encode("utf-8")), len(текст))


class ФазаЧитаемаяВсегда(unittest.TestCase):
    """Вынести текст в файл и читать его каждый раз — не облегчение.

    Без этого правила ворота обходятся за минуту: весь скилл уезжает в
    `phases/всё.md`, стержень худеет до килобайта, а грузится столько же.
    """

    def test_маркер_объявлен(self):
        self.assertTrue(gt.ЧИТАЕТСЯ_ВСЕГДА)

    def test_такая_фаза_входит_в_стартовый_вес(self):
        v = gt.gate_weight()
        for имя, вес in v["weights"].items():
            with self.subTest(имя):
                skill = PKG / "skills" / имя
                всегда = 0
                текст = (skill / "SKILL.md").read_text("utf-8")
                for ф in sorted(skill.glob("phases/*.md")):
                    место = текст.find(f"phases/{ф.name}")
                    рядом = текст[max(0, место - 400):место] if место >= 0 else ""
                    if gt.ЧИТАЕТСЯ_ВСЕГДА in рядом:
                        всегда += len(ф.read_text("utf-8").encode("utf-8"))
                self.assertEqual(
                    вес["старт"],
                    len(текст.encode("utf-8")) + всегда,
                    "фаза, читаемая всегда, обязана входить в стартовый вес")

    def test_условная_фаза_в_стартовый_вес_не_входит(self):
        """Обратный контроль: иначе выносить не имело бы смысла вовсе."""
        v = gt.gate_weight()
        self.assertLess(v["weights"]["go"]["старт"],
                        v["weights"]["go"]["максимум"],
                        "у go есть вынесенные фазы — максимум обязан быть больше")


class ДваЧислаВместоОдного(unittest.TestCase):
    """«228 из 228» читается как «228 механизмов работают». Это неправда.

    Улика — подстрока в файле; она доказывает НАЛИЧИЕ, а не работу. Работу
    доказывает зарегистрированная поломка. Пока число одно, слабая половина
    прячется за сильной формулировкой — и внешний разбор назвал это самым
    громким и самым слабым местом отчёта.
    """

    def test_ворота_плана_называют_оба_числа(self):
        v = gt.gate_plan()
        self.assertIn("proved_by_mutation", v)
        self.assertIn("substring_only", v)
        self.assertIn("только подстрокой", v["detail"])

    def test_сумма_сходится_с_числом_на_месте(self):
        v = gt.gate_plan()
        self.assertEqual(v["proved_by_mutation"] + v["substring_only"],
                         int(v["detail"].split()[0]))

    def test_подстрочных_меньше_чем_всех(self):
        """Обратный контроль: если бы поломок не было ни у одного механизма,
        число «проверено поломкой» было бы нулём — и это тоже надо видеть."""
        v = gt.gate_plan()
        self.assertGreater(v["proved_by_mutation"], 0)


if __name__ == "__main__":
    unittest.main()
