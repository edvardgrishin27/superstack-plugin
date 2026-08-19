#!/usr/bin/env python3
"""Техдолг ищется счётом, а не впечатлением от чтения.

«Пора переписать» зависит от того, кто последним лез в файл, и попадает не
туда: переписывают неприятное для чтения, а болит обычно другое — то, что
приходится править ЧАСТО и что при этом большое. Частая правка означает
неустоявшееся решение, размер — что каждая правка дорогая.

Здесь заперты три отказа:

  · рейтинг без слагаемых — гадание с ранжированием;
  · «нет истории» читается как «долга нет»;
  · чужие каталоги и замки́ пакетов попадают в счёт и топят настоящее.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_debt", at("tools", "debt.py"))
db = importlib.util.module_from_spec(_s)
_s.loader.exec_module(db)


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _файл(self, путь: str, строк: int) -> None:
        p = self.root / путь
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"строка {i}\n" for i in range(строк)),
                     encoding="utf-8")


class Счёт(Проект):

    def test_большое_и_горячее_поднимается_наверх(self):
        self._файл("src/большой.py", 400)
        self._файл("src/мелкий.py", 20)
        места = db.hotspots(self.root, {"src/большой.py": 9,
                                        "src/мелкий.py": 9}, 10)
        self.assertEqual(места[0]["file"], "src/большой.py")

    def test_каждое_место_несёт_слагаемые(self):
        """Рейтинг без слагаемых — гадание с ранжированием."""
        self._файл("src/большой.py", 400)
        (м,) = db.hotspots(self.root, {"src/большой.py": 9}, 10)
        self.assertEqual(м["edits"], 9)
        self.assertEqual(м["lines"], 400)
        self.assertEqual(м["score"], 3600)

    def test_мелкое_и_редкое_не_долг(self):
        """Обратный контроль: инструмент, находящий долг везде, выключат."""
        self._файл("src/мелкий.py", 20)
        self.assertEqual(db.hotspots(self.root, {"src/мелкий.py": 2}, 10), [])

    def test_чужие_каталоги_и_замки_не_считаются(self):
        self._файл("node_modules/пакет/index.js", 900)
        self._файл("package-lock.json", 5000)
        места = db.hotspots(self.root,
                            {"node_modules/пакет/index.js": 40,
                             "package-lock.json": 40}, 10)
        self.assertEqual(места, [])

    def test_исчезнувший_файл_не_ломает_счёт(self):
        """История помнит удалённое; на диске его нет, и это не ошибка."""
        self.assertEqual(db.hotspots(self.root, {"src/удалённый.py": 50}, 10), [])


class НетИстории(Проект):

    def test_без_git_это_не_чисто(self):
        """«Не найти» и «не смотреть» — разные вещи."""
        правки, отказ = db.churn(self.root, 26)
        self.assertIsNone(правки)

    def test_репозиторий_без_правок_за_окно(self):
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=False)
        правки, отказ = db.churn(self.root, 26)
        self.assertIsNone(правки)
        self.assertIn("считать не по чему", отказ)


if __name__ == "__main__":
    unittest.main()
