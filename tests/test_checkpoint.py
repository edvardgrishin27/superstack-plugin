#!/usr/bin/env python3
"""Снимки папки продукта: у «верни как было» появляется адрес.

Дефект, ради которого написан инструмент. Сборка /go писала в папку продукта
код и страницы и не оставляла ни одной точки возврата: единственный откат в
системе возвращал настройки Claude Code, а вчерашний код возвращать было
нечем. На просьбу «верни как было» человек слышал «откатывать нечего».

Здесь заперта вся цепочка целиком, а не наличие файла: папка без хранилища
берётся под снимки, правка попадает в снимок, возврат к первому снимку
возвращает прежнее содержимое — И ПРИ ЭТОМ не уносит с собой более поздние
снимки. Последнее — не украшение: возврат, стирающий историю, отнимает адрес
у следующей просьбы «верни как было», и делает это молча.
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

ИНСТРУМЕНТ = at("tools", "checkpoint.py")


class Снимки(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.папка = Path(self.tmp.name)

    def _прогон(self, *args: str) -> tuple:
        p = subprocess.run([sys.executable, str(ИНСТРУМЕНТ), str(self.папка),
                            *args, "--json"],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, json.loads(p.stdout)

    def _этапы(self) -> list:
        код, v = self._прогон("list")
        return [s["этап"] for s in v["снимки"]]

    def test_вернуть_как_было_имеет_адрес(self):
        """init заводит снимки, snap запоминает правку, back возвращает файл —
        и более поздний снимок после возврата остаётся на месте."""
        файл = self.папка / "страница.txt"
        файл.write_text("было\n", encoding="utf-8")

        код, v = self._прогон("init")
        self.assertEqual(код, 0, v["линия"])
        self.assertTrue((self.папка / ".git").is_dir(),
                        "папка не взята под снимки — возвращать не к чему")

        файл.write_text("стало\n", encoding="utf-8")
        код, v = self._прогон("snap", "Пишем код")
        self.assertEqual(код, 0, v["линия"])
        self.assertTrue(v["снято"], v["линия"])
        self.assertIn("Пишем код", self._этапы())

        код, v = self._прогон("back", "как было до начала")
        self.assertEqual(код, 0, v["линия"])
        self.assertEqual(файл.read_text(encoding="utf-8"), "было\n",
                         "возврат к снимку не вернул прежнее содержимое")
        self.assertIn("Пишем код", self._этапы(),
                      "возврат унёс с собой более поздний снимок: следующая "
                      "просьба «верни как было» осталась без адреса")


if __name__ == "__main__":
    unittest.main()
