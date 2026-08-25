#!/usr/bin/env python3
"""Покрытие по диффу: большой зелёный проект не должен прятать новую дыру.

Общий процент почти невозможно уронить. Сто строк без единого теста в
репозитории на двадцать тысяч сдвинут его на десятые доли — порог в CI
останется зелёным, и ровно новый код уедет непроверенным. Чем больше проект,
тем надёжнее он прячет свежую дыру.

Здесь заперты три отказа:

  · отсутствие отчёта читается как «покрыто»;
  · общий процент подменяет покрытие правки;
  · новый файл не попадает в изменённое, потому что `git diff` его не видит.
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

TOOL = at("tools", "diff_coverage.py")


def cobertura(файлы: dict) -> str:
    """{файл: {строка: покрыта}} → отчёт в формате coverage.py --xml."""
    куски = ['<?xml version="1.0" ?>', "<coverage><packages><package><classes>"]
    for имя, строки in файлы.items():
        куски.append(f'<class filename="{имя}"><lines>')
        for n, покрыта in sorted(строки.items()):
            куски.append(f'<line number="{n}" hits="{1 if покрыта else 0}"/>')
        куски.append("</lines></class>")
    куски.append("</classes></package></packages></coverage>")
    return "".join(куски)


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._git("init", "-q")
        self._git("config", "user.email", "т@т")
        self._git("config", "user.name", "тест")
        (self.root / "README.md").write_text("проект\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "первый")

    def _git(self, *a: str):
        return subprocess.run(["git", *a], cwd=str(self.root),
                              capture_output=True, text=True, timeout=60)

    def _файл(self, путь: str, строк: int) -> None:
        p = self.root / путь
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"строка {i}\n" for i in range(1, строк + 1)),
                     encoding="utf-8")

    def _отчёт(self, файлы: dict) -> None:
        (self.root / "coverage.xml").write_text(cobertura(файлы),
                                                encoding="utf-8")

    def _прогон(self, *флаги: str) -> tuple:
        p = subprocess.run([sys.executable, str(TOOL), str(self.root), *флаги],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, json.loads(p.stdout), p.stderr


class БезОтчёта(Проект):

    def test_нет_отчёта_это_не_покрыто(self):
        """Проект без замера не имеет права проходить гейт лучше проекта с ним."""
        self._файл("src/новое.py", 3)
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)
        self.assertIn("измерить нечем", v["detail"])

    def test_битый_отчёт_это_тоже_не_покрыто(self):
        self._файл("src/новое.py", 3)
        (self.root / "coverage.xml").write_text("<не xml", encoding="utf-8")
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)

    def test_отчёт_про_другой_код_не_засчитывается(self):
        """Отчёт старше правок покрывает не то — это «нечем», а не «покрыто»."""
        self._файл("src/новое.py", 3)
        self._отчёт({"src/старое.py": {1: True, 2: True}})
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)
        self.assertIn("не попала в отчёт", v["detail"])


class ПокрытиеПравки(Проект):

    def test_непокрытая_новая_строка_роняет(self):
        self._файл("src/новое.py", 4)
        self._отчёт({"src/новое.py": {1: True, 2: False, 3: False, 4: True}})
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("src/новое.py:2", v["uncovered"])
        self.assertEqual(v["percent"], 50)

    def test_покрытая_правка_проходит(self):
        """Обратный контроль: проверка, никогда не зеленеющая, бесполезна."""
        self._файл("src/новое.py", 4)
        self._отчёт({"src/новое.py": {n: True for n in range(1, 5)}})
        код, v, _ = self._прогон()
        self.assertEqual(код, 0, v)
        self.assertEqual(v["percent"], 100)

    def test_большой_зелёный_проект_не_прячет_новую_дыру(self):
        """Главное свойство: общий процент высок, покрытие ПРАВКИ — нет."""
        self._файл("src/новое.py", 2)
        отчёт = {"src/старое.py": {n: True for n in range(1, 200)}}
        отчёт["src/новое.py"] = {1: False, 2: False}
        self._отчёт(отчёт)
        код, v, _ = self._прогон()
        self.assertEqual(код, 1, "общий процент 99% скрыл непокрытую правку")
        self.assertEqual(v["percent"], 0)

    def test_порог_читается_из_спеки(self):
        self._файл("src/новое.py", 4)
        self._отчёт({"src/новое.py": {1: True, 2: True, 3: True, 4: False}})
        (self.root / ".superstack").mkdir()
        (self.root / ".superstack" / "diff-coverage.json").write_text(
            json.dumps({"threshold": 50}), encoding="utf-8")
        self.assertEqual(self._прогон()[0], 0, "порог 50 при покрытии 75")

    def test_тесты_не_требуют_покрытия_самих_себя(self):
        self._файл("tests/test_новое.py", 3)
        self._отчёт({"tests/test_новое.py": {1: False, 2: False, 3: False}})
        код, v, _ = self._прогон()
        self.assertEqual(код, 2, "тестовый файл посчитан как измеримый")


class НовыйФайлЭтоИзменение(Проект):

    def test_неотслеживаемый_файл_попадает_в_изменённое(self):
        """`git diff` не видит новых файлов, а новый код — это чаще всего они."""
        self._файл("src/совсем_новый.py", 3)
        self._отчёт({"src/совсем_новый.py": {1: False, 2: False, 3: False}})
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertEqual(v["changed_measurable"], 3)


if __name__ == "__main__":
    unittest.main()
