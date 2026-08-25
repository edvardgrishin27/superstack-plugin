#!/usr/bin/env python3
"""История собирается по объявленной нарезке, а не «как получилось».

История git — документ, который читает следующий человек, и чаще всего это ты
через месяц. Когда нарезка не объявлена, она получается сама: один коммит на
всё, потому что «работа же связана», — и ответ на «почему здесь так» приходится
искать в диффе на четыре тысячи строк. Откатить одну неудачную часть тоже
нельзя: она вплетена в удачные.

Здесь заперты три отказа:

  · «не объявлено» читается как «история в порядке»;
  · объявленный кусок без коммита проходит молча;
  · коммит, смешавший два куска, не называется — а он лишает смысла обе
    записи сразу.
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
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_commit_plan",
                                            at("tools", "commit_plan.py"))
cp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(cp)

КУСКИ = [
    {"id": "схема", "why": "чтобы откатывать миграции отдельно от кода",
     "paths": ["migrations/"]},
    {"id": "экран", "why": "чтобы вернуть старый вид, не трогая данные",
     "paths": ["src/ui/"]},
]


class План(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()

    def _план(self, d: dict) -> None:
        (self.root / ".superstack" / "commits.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def test_не_объявлено_это_не_порядок(self):
        куски, отказ = cp.план(self.root)
        self.assertIsNone(куски)
        self.assertIn("не то же самое", отказ)

    def test_кусок_без_причины_отвергается(self):
        """Через месяц кусок без причины читается как случайность."""
        self._план({"slices": [{"id": "схема", "paths": ["migrations/"]}]})
        куски, отказ = cp.план(self.root)
        self.assertIsNone(куски)
        self.assertIn("без причины", отказ)

    def test_годный_план_читается(self):
        self._план({"slices": КУСКИ})
        куски, _ = cp.план(self.root)
        self.assertEqual(len(куски), 2)


class Расхождение(unittest.TestCase):

    def test_чистая_нарезка_сходится(self):
        """Обратный контроль: проверка, всегда находящая расхождение, бесполезна."""
        коммиты = [("схема: миграция", ["migrations/001.sql"]),
                   ("экран: форма", ["src/ui/форма.tsx"])]
        self.assertEqual(cp.drift(коммиты, КУСКИ), [])

    def test_смешанный_коммит_называется(self):
        """Откатить один кусок после смешения больше нельзя."""
        коммиты = [("всё сразу", ["migrations/001.sql", "src/ui/форма.tsx"])]
        нашли = cp.drift(коммиты, КУСКИ)
        смешанные = [r for r in нашли if r["id"] == "mixed-commit"]
        self.assertEqual(len(смешанные), 1)
        self.assertEqual(смешанные[0]["slices"], ["схема", "экран"])

    def test_объявленный_кусок_без_коммита_называется(self):
        коммиты = [("схема: миграция", ["migrations/001.sql"])]
        нашли = cp.drift(коммиты, КУСКИ)
        без = [r for r in нашли if r["id"] == "slice-without-commit"]
        self.assertEqual([r["slice"] for r in без], ["экран"])
        self.assertIn("не трогая данные", без[0]["why"])

    def test_файлы_вне_кусков_не_считаются_смешением(self):
        """README рядом с правкой — не нарушение нарезки."""
        коммиты = [("схема: миграция", ["migrations/001.sql", "README.md"]),
                   ("экран: форма", ["src/ui/форма.tsx"])]
        self.assertEqual(cp.drift(коммиты, КУСКИ), [])


class Разбор(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=str(self.root), check=False)

    def test_без_истории_это_не_успех(self):
        коммиты, отказ = cp.история(self.root, "origin/main")
        self.assertIsNone(коммиты)


if __name__ == "__main__":
    unittest.main()
