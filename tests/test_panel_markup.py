#!/usr/bin/env python3
"""Разметка панели — данные в отдельном файле, а не строка внутри инструмента.

Почти тысяча строк разметки жила прямо в `live_panel.py`, и это не вопрос
вкуса. Разметка ничего не решает: не ветвится, не считает, не выбирает. Держа
её в одном файле с логикой, мы платили дважды — файл нельзя было прочитать
целиком, а страницу нельзя было ни открыть, ни показать как страницу.

Переезд в `data/panel-page.html` добавляет ровно один новый способ сломаться,
и он тихий: файл, который есть у автора и не приезжает к человеку — или
приезжает, но не находится, потому что путь считается от текущего каталога.
Оба отказа выглядят одинаково — пустой экран без единого признака поломки, —
и оба заперты здесь:

  · страница действительно собирается ИЗ ФАЙЛА, а не из зашитой копии;
  · панель собирается из ЛЮБОГО текущего каталога, а не только из корня
    репозитория, где её случайно запускает разработчик;
  · установленная копия пакета несёт разметку с собой и строит панель без
    репозитория рядом;
  · пропажа файла — это отказ с названным путём, а не пустая страница.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

TOOL = PKG / "tools" / "live_panel.py"
_s = importlib.util.spec_from_file_location("ss_live_panel_markup", TOOL)
lp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(lp)

_p = importlib.util.spec_from_file_location(
    "ss_progress_markup", PKG / "tools" / "progress.py")
pr = importlib.util.module_from_spec(_p)
_p.loader.exec_module(pr)

ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
       "PYTHONDONTWRITEBYTECODE": "1"}

#: Метка, которой правка файла разметки помечается в тесте. Ищется потом в
#: собранной странице: дошла — страница действительно читает файл.
МЕТКА = "<!--проверка-происхождения-разметки-->"


class Песочница(unittest.TestCase):
    """Свой временный каталог на тест. Рабочее дерево не трогается вовсе:
    его в этот же момент может гонять соседний прогон."""

    def setUp(self):
        врем = tempfile.TemporaryDirectory()
        self.addCleanup(врем.cleanup)
        self.дом = Path(врем.name)

    def копия_пакета(self) -> Path:
        """Пакет отдельно от репозитория — ровно то, что ставится человеку."""
        куда = self.дом / "установлено" / "superstack"
        shutil.copytree(PKG, куда,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return куда

    def прогон(self, где: str = "проект") -> Path:
        """Каталог прогона с записанным состоянием — минимум, нужный панели."""
        d = self.дом / где / ".superstack"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps(pr.EMPTY, ensure_ascii=False),
                                      encoding="utf-8")
        return d


class РазметкаЛежитФайлом(Песочница):
    """Страница берётся С ДИСКА, а не из строки в коде.

    Проверяется не форма, а происхождение: правка файла разметки обязана быть
    видна в собранной странице. Зашитая в код разметка на такую правку не
    отозвалась бы ничем — и тест бы это увидел.
    """

    def test_страница_собирается_из_файла_разметки(self):
        пакет = self.копия_пакета()
        разметка = пакет / "data" / "panel-page.html"
        разметка.write_text(разметка.read_text("utf-8") + МЕТКА + "\n",
                            encoding="utf-8")
        run = self.прогон()
        p = subprocess.run([sys.executable, str(пакет / "tools" / "live_panel.py"),
                            str(run)],
                           capture_output=True, text=True, timeout=120,
                           env=ENV, cwd=str(self.дом))
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertIn(МЕТКА, (run / "panel.html").read_text("utf-8"),
                      "правка файла разметки не дошла до страницы — значит "
                      "страница собирается не из него")

    def test_разметка_лежит_в_пакете_и_едет_в_поставку(self):
        """Файл обязан быть ВНУТРИ пакета: ставится он, а не репозиторий."""
        self.assertTrue(lp.PAGE.is_file(), f"нет файла разметки: {lp.PAGE}")
        self.assertIn(PKG, list(lp.PAGE.parents),
                      f"разметка лежит вне пакета ({lp.PAGE}) — установленной "
                      "копии она не достанется")


class РазметкаНаходитсяОткудаУгодно(Песочница):
    """Путь к разметке считается от файла инструмента, а не от текущего
    каталога. Относительный путь работает ровно у того, кто запускает из корня
    репозитория, — то есть у автора и ни у кого больше."""

    def test_панель_собирается_из_чужого_текущего_каталога(self):
        run = self.прогон()
        чужой = self.дом / "совсем-другой-каталог"
        чужой.mkdir()
        p = subprocess.run([sys.executable, str(TOOL), str(run)],
                           capture_output=True, text=True, timeout=120,
                           env=ENV, cwd=str(чужой))
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        self.assertIn("<!doctype html>", (run / "panel.html").read_text("utf-8"))

    def test_установленная_копия_несёт_разметку_с_собой(self):
        """Пакет, скопированный без репозитория, строит панель сам.

        Это и есть установка: `plugins/superstack` уезжает целиком, а `tests/`
        и корень репозитория — нет. Разметка, оставшаяся снаружи пакета,
        отсюда была бы недостижима.
        """
        пакет = self.копия_пакета()
        run = self.прогон("чужой-проект")
        p = subprocess.run([sys.executable, str(пакет / "tools" / "live_panel.py"),
                            str(run)],
                           capture_output=True, text=True, timeout=120,
                           env=ENV, cwd=str(self.дом))
        self.assertEqual(p.returncode, 0, p.stderr[-500:])
        страница = (run / "panel.html").read_text("utf-8")
        self.assertIn("<!doctype html>", страница)
        self.assertIn(lp.SNAPSHOT_BEGIN, страница)


class ПропажаРазметкиЭтоОтказ(Песочница):
    """Пустая страница — худший из возможных ответов: она выглядит как
    «ничего не происходит», а не как поломка. Поэтому отсутствие файла
    называется вслух и вместе с путём, по которому его искали."""

    def test_отсутствие_файла_называется_вслух_с_путём(self):
        было = lp.PAGE
        lp.PAGE = self.дом / "нет-такого-файла.html"
        self.addCleanup(setattr, lp, "PAGE", было)
        with self.assertRaises(OSError) as поймано:
            lp._markup()
        self.assertIn("нет-такого-файла.html", str(поймано.exception),
                      "отказ не назвал путь, по которому искали разметку")


if __name__ == "__main__":
    unittest.main()
