#!/usr/bin/env python3
"""Документ идеи: то, что человек рассказывает ДО спеки.

Спека отвечает «что построить и как проверить». Она не отвечает на вопрос, из
которого всё растёт: зачем и кому. Без него первая же развилка решается вкусом
исполнителя, а человек узнаёт об этом на сдаче — когда построено не то, хотя
построено по спеке.

Здесь заперты три отказа:

  · галочку ставит тот, кого проверяют, — чеклист вместо проверки;
  · боль и мера без числа: ощущение нельзя ни проверить, ни опровергнуть;
  · раздел засчитывается по упоминанию в прозе, а не по заголовку.
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

TOOL = at("tools", "idea_lint.py")

ХОРОШИЙ = """# Идея

## Боль

Записываю клиентов в блокнот: 40 минут в день на переписывание и 2-3 потерянные
записи в неделю.

## Кто

Мастер маникюра, работает одна, сейчас ведёт запись в тетради и в переписке.

## Что изменится

Клиент сам выбирает время на странице, запись сразу попадает в список на день.

## Чем меряем

Ноль потерянных записей за две недели и меньше 10 минут в день на ведение.

## Границы

Онлайн-оплату и напоминания в этой версии не делаем.
"""


class Документ(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()

    def _идея(self, текст: str) -> None:
        (self.root / ".superstack" / "idea.md").write_text(текст, encoding="utf-8")

    def _прогон(self) -> tuple:
        p = subprocess.run([sys.executable, str(TOOL), str(self.root)],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, json.loads(p.stdout), p.stderr


class НетДокумента(Документ):

    def test_отсутствие_это_не_простая_идея(self):
        код, v, _ = self._прогон()
        self.assertEqual(код, 2)
        self.assertIn("зачем и кому", v["next"])


class Провалы(Документ):

    def test_боль_без_числа_не_боль(self):
        """«Людям неудобно» — по нему нельзя понять, стало ли лучше."""
        текст = ХОРОШИЙ.replace(
            "Записываю клиентов в блокнот: 40 минут в день на переписывание и 2-3 потерянные\nзаписи в неделю.",
            "Вести запись неудобно и долго.")
        self._идея(текст)
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        разделы = [b["section"] for b in v["problems"]]
        self.assertIn("Боль", разделы)

    def test_мера_без_числа_не_мера(self):
        текст = ХОРОШИЙ.replace(
            "Ноль потерянных записей за две недели и меньше 10 минут в день на ведение.",
            "Станет заметно лучше.")
        self._идея(текст)
        _, v, _ = self._прогон()
        self.assertIn("Чем меряем", [b["section"] for b in v["problems"]])

    def test_пропущенный_раздел_назван_с_причиной(self):
        self._идея(ХОРОШИЙ.split("## Границы")[0])
        _, v, _ = self._прогон()
        беда = [b for b in v["problems"] if b["section"] == "Границы"]
        self.assertEqual(len(беда), 1)
        self.assertIn("объём растёт молча", беда[0]["why"])

    def test_упоминание_в_прозе_разделом_не_является(self):
        """Иначе линт превращается в поиск подстроки, то есть ни во что."""
        self._идея("# Идея\n\nБоль тут очевидна, кто пользователь — понятно, "
                   "границы обсудим, чем меряем решим потом.\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertEqual(len(v["problems"]), 5)


class Годный(Документ):

    def test_полный_документ_проходит(self):
        """Обратный контроль: линт, не пропускающий ничего, выключают."""
        self._идея(ХОРОШИЙ)
        код, v, _ = self._прогон()
        self.assertEqual(код, 0, v)
        self.assertEqual(len(v["sections"]), 5)

    def test_шаблон_сам_проходит_только_после_заполнения(self):
        """Шаблон — это форма, а не ответ: пустая форма обязана падать."""
        p = subprocess.run([sys.executable, str(TOOL), "--template"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0)
        self._идея(p.stdout)
        self.assertEqual(self._прогон()[0], 1)


if __name__ == "__main__":
    unittest.main()
