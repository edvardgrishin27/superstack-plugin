#!/usr/bin/env python3
"""Проверяется ТЕКСТ, который читает человек, а не форма ответа внутри.

Класс К10 из триажа: «тесты подтверждают форму, а не то, что читает человек —
165 зелёных тестов не видят перевёрнутый вердикт». Проверено 20.08: все четыре
названных дефекта в коде исправлены — число подставляется, вывод эвристики
помечается отдельно от измеренного, `/why` печатает буквальные значения,
цепочка сравнений работает. Не исправлено было ДРУГОЕ: их отсутствие ничем не
охранялось, и вернуться они могли молча.

Здесь конвейер гоняется целиком — факты → вычислитель → отчёт, — и утверждения
делаются о готовых строках. Разница принципиальная: находка может быть верной
внутри и нечитаемой снаружи, и человеку достаётся только второе.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

ADJUDICATE = at("tools", "adjudicate.py")
RENDER = at("tools", "render.py")

ФАКТЫ = {
    "a.count": {"value": 42, "probe": "проба", "evidence": "~/x",
                "provenance": "EXTRACTED"},
    "b.guess": {"value": True, "probe": "проба", "evidence": None,
                "provenance": "INFERRED"},
}

ПРАВИЛА = {"schema": "superstack.rules.v1", "rules": [
    {"id": "t.measured", "when": "a.count > 10", "severity": "critical",
     "class": "COST", "verdict": "ASK",
     "number": {"of": "a.count", "unit": "штук"},
     "beginner": {"headline": "Штук {n} — это дорого",
                  "plain": "Их читают при каждом старте."},
     "expert": {"claim": "их {a.count}", "evidence": ["a.count"]}},
    {"id": "t.inferred", "when": "b.guess == True", "severity": "low",
     "class": "COST", "verdict": "ASK",
     "beginner": {"headline": "Похоже, включено", "plain": "Стоит проверить."},
     "expert": {"claim": "вывод по косвенным признакам",
                "evidence": ["b.guess"]}},
]}


class Конвейер(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        корень = Path(cls.tmp.name)
        (корень / "rules").mkdir()
        (корень / "facts.json").write_text(json.dumps(ФАКТЫ, ensure_ascii=False),
                                           encoding="utf-8")
        (корень / "rules" / "t.rules.json").write_text(
            json.dumps(ПРАВИЛА, ensure_ascii=False), encoding="utf-8")
        p = subprocess.run([sys.executable, str(ADJUDICATE), "facts.json",
                            "rules/*.json"], cwd=str(корень),
                           capture_output=True, text=True, timeout=120)
        assert p.returncode in (0, 1), p.stderr[-400:]
        (корень / "f.json").write_text(p.stdout, encoding="utf-8")
        cls.корень = корень
        cls.находки = json.loads(p.stdout)["findings"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def показать(self, *аргументы: str) -> str:
        p = subprocess.run([sys.executable, str(RENDER), "f.json", *аргументы],
                           cwd=str(self.корень), capture_output=True,
                           text=True, timeout=120)
        return p.stdout + p.stderr


class ЧтоВидитНовичок(Конвейер):

    def test_заголовок_с_подставленным_числом(self):
        """`(?)` вместо числа — это находка, которую нечем проверить."""
        текст = self.показать("beginner")
        self.assertIn("Штук 42 — это дорого", текст)
        self.assertNotIn("{n}", текст)
        self.assertNotIn("(?)", текст)

    def test_идентификатор_правила_человеку_не_показывается(self):
        """`t.measured` — имя для машины. Человек читает предложение."""
        текст = self.показать("beginner")
        self.assertNotIn("t.measured", текст)

    def test_вердикт_словами_а_не_кодом(self):
        текст = self.показать("beginner")
        self.assertNotIn("ASK", текст, "в лицо человеку напечатан код вердикта")

    def test_тяжёлое_выше_лёгкого(self):
        """Перевёрнутый порядок — тот самый случай, которого 165 тестов не видели."""
        текст = self.показать("beginner")
        self.assertLess(текст.index("Штук 42"), текст.index("Похоже, включено"),
                        "лёгкая находка встала выше критической")


class ЧтоВидитВПочему(Конвейер):

    def test_буквальные_значения_напечатаны(self):
        """Пустой раздел хуже отсутствующего: он выглядит отвеченным."""
        текст = self.показать("why", "t.measured")
        self.assertIn("a.count = 42", текст)

    def test_утверждение_с_подставленным_фактом(self):
        self.assertIn("их 42", self.показать("why", "t.measured"))

    def test_названо_правило_источник(self):
        self.assertIn("t.rules.json", self.показать("why", "t.measured"))


class ИзмеренноеИВыведенноеРазличаются(Конвейер):
    """Флаг «это вывод эвристики» на КАЖДОЙ находке обесценивает сам флаг."""

    def test_измеренное_не_помечено_выводом(self):
        f = next(x for x in self.находки if x["id"] == "t.measured")
        self.assertFalse(f["rests_on_inference"])

    def test_выведенное_помечено(self):
        f = next(x for x in self.находки if x["id"] == "t.inferred")
        self.assertTrue(f["rests_on_inference"])

    def test_разница_видна_в_тексте(self):
        текст = self.показать("expert")
        self.assertIn("INFERRED", текст.upper(),
                      "класс достоверности не доехал до текста — читатель не "
                      "отличит измеренное от выведенного")


if __name__ == "__main__":
    unittest.main()
