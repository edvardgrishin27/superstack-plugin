#!/usr/bin/env python3
"""Чужое знание входит скиллами по задачам, а не документом целиком.

Внешний корпус приходит документом: одна большая тема, внутри которой
перемешаны десять задач. Положить его в скилл целиком просто и почти
бесполезно — скилл выбирается по описанию «когда меня брать», а у документа
такого ответа нет: он подтягивается либо всегда, либо никогда.

Творческая часть — где проходят швы — работа модели. Здесь заперта форма,
которую иначе теряют:

  · имя без префикса источника сталкивается с твоим скиллом, и выигрывает
    случайный;
  · описание, обещающее тему целиком, означает, что нарезки не было;
  · происхождение не названо, и через полгода не отличить выстраданное на
    своих прогонах от переписанного из чужой статьи.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_distill", at("tools", "distill.py"))
ds = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ds)


class Каталог(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _скилл(self, имя: str, тело: str) -> None:
        d = self.root / имя
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(тело, encoding="utf-8")

    def _годный(self, имя="спекфёрст-описать-идею") -> None:
        self._скилл(имя, "\n".join([
            "---", f"name: {имя}",
            "description: Когда нужно описать идею проекта до спеки.",
            "---", "", "Источник: spec-first methodology", "",
            "## Когда брать", "", "человек говорит «хочу сделать»", ""]))


class Форма(Каталог):

    def test_годный_скилл_проходит(self):
        """Обратный контроль: проверка, никогда не пропускающая, бесполезна."""
        self._годный()
        v = ds.check(self.root, "спекфёрст")
        self.assertEqual(v["status"], "pass", v)

    def test_имя_без_префикса_ловится(self):
        self._скилл("описать-идею", "\n".join([
            "---", "name: описать-идею", "description: Когда нужно описать идею.",
            "---", "", "Источник: чужая статья", ""]))
        # Без префикса скилл вообще не попадает в выборку источника —
        # проверка честно говорит, что дистиллировать нечего.
        v = ds.check(self.root, "спекфёрст")
        self.assertEqual(v["status"], "unknown")

    def test_документ_целиком_ловится(self):
        self._скилл("спекфёрст-всё", "\n".join([
            "---", "name: спекфёрст-всё",
            "description: Полное руководство: методология и ещё справочник по "
            "конфигурации, а также генератор.",
            "---", "", "Источник: spec-first", ""]))
        v = ds.check(self.root, "спекфёрст")
        self.assertIn("document-not-a-task", [f["id"] for f in v["findings"]])

    def test_происхождение_обязательно(self):
        self._скилл("спекфёрст-идея", "\n".join([
            "---", "name: спекфёрст-идея", "description: Когда нужно описать идею.",
            "---", "", "тело без источника", ""]))
        v = ds.check(self.root, "спекфёрст")
        self.assertIn("no-provenance", [f["id"] for f in v["findings"]])

    def test_скилл_без_описания_не_выбирается_никогда(self):
        self._скилл("спекфёрст-немой", "\n".join([
            "---", "name: спекфёрст-немой", "---", "", "Источник: чужое", ""]))
        v = ds.check(self.root, "спекфёрст")
        self.assertIn("no-description", [f["id"] for f in v["findings"]])

    def test_нечего_проверять_это_не_успех(self):
        v = ds.check(self.root, "спекфёрст")
        self.assertEqual(v["status"], "unknown")


class Шаблон(unittest.TestCase):

    def test_шаблон_несёт_префикс_источник_и_одну_задачу(self):
        t = ds.template("спекфёрст", "описать идею")
        self.assertIn("name: спекфёрст-описать-идею", t)
        self.assertIn("Источник: спекфёрст", t)
        self.assertIn("Одна задача", t)

    def test_шаблон_велит_назвать_расхождение_с_нашим(self):
        """Чужое, противоречащее нашему подходу, обязано быть названо вслух."""
        self.assertIn("отличается от нашего", ds.template("и", "з"))


if __name__ == "__main__":
    unittest.main()
