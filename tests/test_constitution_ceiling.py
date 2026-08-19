#!/usr/bin/env python3
"""Потолок конституции: файл читается каждой сессией целиком.

Объём постоянной инструкции — не вкус, а плата. Чем она длиннее, тем меньше
места остаётся задаче и тем вернее середину прочитают по диагонали. Правило,
не помещающееся во внимание, не работает, но выглядит написанным — и это
худший из исходов, потому что незаметен.

Поэтому потолок считается ЧИСЛОМ. Здесь заперто то, что легко потерять при
следующей правке: сам замер, оба его источника и то, что правило действительно
срабатывает на превышении.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, at  # noqa: E402

COLLECT = at("tools", "probe", "collect.py")
ПРАВИЛА = PKG / "rules" / "core.rules.json"
_s = importlib.util.spec_from_file_location("ss_adj_const", at("tools", "adjudicate.py"))
adj = importlib.util.module_from_spec(_s)
_s.loader.exec_module(adj)


def правило() -> dict:
    d = json.loads(ПРАВИЛА.read_text("utf-8"))
    (это,) = [r for r in d["rules"] if r["id"] == "cc.constitution-over-ceiling"]
    return это


class Замер(unittest.TestCase):
    """Объём измеряется пробой, а не оценивается на глаз."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.дом = Path(self.tmp.name) / "дом"
        (self.дом / ".claude").mkdir(parents=True)

    def _факты(self, cwd: Path) -> dict:
        p = subprocess.run([sys.executable, str(COLLECT)], cwd=str(cwd),
                           capture_output=True, text=True, timeout=300,
                           env={**os.environ, "HOME": str(self.дом),
                                "SUPERSTACK_IGNORE_PAUSE": "1"})
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        return json.loads(p.stdout)

    def test_личная_конституция_считается(self):
        (self.дом / ".claude" / "CLAUDE.md").write_text(
            "".join(f"строка {i}\n" for i in range(1, 251)), encoding="utf-8")
        ф = self._факты(self.дом)
        self.assertEqual(ф["cc.constitution_lines"]["value"], 250)

    def test_проектная_конституция_тоже_считается(self):
        """Платит человек за обе сразу — значит и мерить надо обе."""
        проект = Path(self.tmp.name) / "проект"
        проект.mkdir()
        (проект / "CLAUDE.md").write_text("".join(f"с {i}\n" for i in range(300)),
                                          encoding="utf-8")
        ф = self._факты(проект)
        self.assertEqual(ф["cc.constitution_lines"]["value"], 300)

    def test_отсутствие_файла_объяснено_в_улике(self):
        """Ноль без объяснения читается как «конституция пуста»."""
        ф = self._факты(self.дом)
        self.assertEqual(ф["cc.constitution_lines"]["value"], 0)
        self.assertIn("не найдено", ф["cc.constitution_lines"]["evidence"])


class Правило(unittest.TestCase):

    def test_срабатывает_на_превышении(self):
        self.assertTrue(adj.evaluate(правило()["when"], {"cc.constitution_lines": 201}))

    def test_молчит_в_пределах(self):
        """Обратный контроль: правило, срабатывающее всегда, выключат."""
        self.assertFalse(adj.evaluate(правило()["when"], {"cc.constitution_lines": 200}))

    def test_потолок_достижим(self):
        """Порог, до которого нельзя дорасти, — это правило, которое никогда
        не сработает: оно лежит в файле и создаёт видимость проверки."""
        import re
        (порог,) = re.findall(r"(\d+)", правило()["when"])
        self.assertLessEqual(int(порог), 1000)

    def test_объяснение_называет_цену_а_не_вкус(self):
        текст = json.dumps(правило(), ensure_ascii=False)
        self.assertIn("каждой сессии", текст)


if __name__ == "__main__":
    unittest.main()
