#!/usr/bin/env python3
"""Что стоит и не срабатывает — предложить, но не удалить.

Установленное копится и не убывает. Каждый скилл и хук платит собой за место в
контексте и во внимании, а понять, работает ли он, нельзя, глядя на файл: файл
на месте всегда. Через полгода система выглядит богатой и на треть состоит из
того, что не срабатывало ни разу.

Здесь заперты три отказа:

  · «не срабатывало» подменяется на «не нужно» — и первым под нож идёт
    аварийный выключатель, чьё молчание и есть лучшая новость;
  · пустой журнал читается как «всё лишнее»;
  · поставленное вчера судят за то, что оно не срабатывало вчера.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_prune", at("tools", "prune.py"))
pr = importlib.util.module_from_spec(_s)
_s.loader.exec_module(pr)

СЕЙЧАС = datetime(2026, 8, 19, tzinfo=timezone.utc)


def когда(дней: int) -> str:
    return (СЕЙЧАС - timedelta(days=дней)).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


class Кандидаты(unittest.TestCase):

    def test_молчащее_дольше_срока_попадает_в_кандидаты(self):
        соб = [("verify.py", когда(1)), ("learn.py", когда(120))]
        v = pr.candidates(соб, ["verify.py", "learn.py"], 8, СЕЙЧАС)
        self.assertEqual(v["status"], "fail")
        self.assertEqual([к["tool"] for к in v["candidates"]], ["learn.py"])

    def test_ни_разу_не_появлявшееся_названо_отдельно(self):
        соб = [("verify.py", когда(1)), ("verify.py", когда(120))]
        v = pr.candidates(соб, ["verify.py", "adr.py"], 8, СЕЙЧАС)
        (к,) = v["candidates"]
        self.assertEqual(к["tool"], "adr.py")
        self.assertIsNone(к["last_seen"])

    def test_тормоз_не_предлагают_удалять(self):
        """Аварийный выключатель не срабатывал ни разу, и это лучшее, что
        можно про него сказать."""
        соб = [("verify.py", когда(1)), ("verify.py", когда(120))]
        v = pr.candidates(соб, ["verify.py", "pause.sh", "oops.py"], 8, СЕЙЧАС)
        имена = [к["tool"] for к in v["candidates"]]
        self.assertNotIn("pause.sh", имена)
        self.assertNotIn("oops.py", имена)

    def test_всё_живое_даёт_зелёное(self):
        """Обратный контроль: проверка, всегда находящая лишнее, бесполезна."""
        соб = [("verify.py", когда(1)), ("learn.py", когда(3)),
               ("verify.py", когда(120))]
        v = pr.candidates(соб, ["verify.py", "learn.py"], 8, СЕЙЧАС)
        self.assertEqual(v["status"], "pass")

    def test_молодой_журнал_вердикта_не_даёт(self):
        """Поставленное вчера не срабатывало вчера."""
        соб = [("verify.py", когда(2)), ("learn.py", когда(1))]
        v = pr.candidates(соб, ["verify.py", "learn.py", "adr.py"], 8, СЕЙЧАС)
        self.assertEqual(v["status"], "unknown")
        self.assertIn("короче срока", v["detail"])


class Журнал(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.каталог = Path(self.tmp.name)

    def test_пустой_каталог_это_не_всё_лишнее(self):
        соб, отказ = pr.события(self.каталог)
        self.assertIsNone(соб)
        self.assertIn("журнала нет", отказ)

    def test_битые_строки_пропускаются_а_годные_читаются(self):
        (self.каталог / "события.jsonl").write_text(
            "не json\n" + json.dumps({"tool": "verify.py", "ts": когда(1)}) + "\n",
            encoding="utf-8")
        соб, _ = pr.события(self.каталог)
        self.assertEqual(соб, [("verify.py", когда(1))])

    def test_несколько_файлов_журнала_складываются(self):
        for i, имя in enumerate(("а.jsonl", "б.jsonl")):
            (self.каталог / имя).write_text(
                json.dumps({"tool": f"инструмент{i}.py", "ts": когда(i + 1)}) + "\n",
                encoding="utf-8")
        соб, _ = pr.события(self.каталог)
        self.assertEqual(len(соб), 2)


if __name__ == "__main__":
    unittest.main()
