#!/usr/bin/env python3
"""Потолок обращений наружу: цикл останавливается счётом, а не совестью.

Автономная петля обращается к внешнему справочнику столько раз, сколько ей
покажется нужным. Каждое обращение стоит денег или лимита, и растёт расход
тихо: ни одна отдельная попытка не выглядит лишней. Человек узнаёт о трате,
когда она уже случилась, — а он как раз тот, кто закрыл ноутбук и доверился
слову «автономно».

Здесь заперты три отказа:

  · «не объявлено» читается как «без ограничений»;
  · потолок предупреждает вместо того, чтобы останавливать;
  · счёт идёт по памяти модели, а не по журналу.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_quota", at("tools", "quota.py"))
q = importlib.util.module_from_spec(_s)
_s.loader.exec_module(q)


class Потолок(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()

    def _спека(self, d: dict) -> None:
        (self.root / ".superstack" / "quota.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def test_не_объявлено_это_не_безлимит(self):
        л, отказ = q.limits(self.root)
        self.assertIsNone(л)
        self.assertIn("не «без ограничений»", отказ)

    def test_ноль_потолком_не_является(self):
        self._спека({"limits": {"context7": 0}})
        л, отказ = q.limits(self.root)
        self.assertIsNone(л)

    def test_годная_спека_читается(self):
        self._спека({"limits": {"context7": 40}})
        л, _ = q.limits(self.root)
        self.assertEqual(л, {"context7": 40})


class Счёт(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.журнал = Path(self.tmp.name)

    def _события(self, источники: list) -> None:
        (self.журнал / "события.jsonl").write_text(
            "".join(json.dumps({"tool": "x", "external": и}) + "\n"
                    for и in источники), encoding="utf-8")

    def test_считает_по_журналу(self):
        self._события(["context7", "context7", "web"])
        self.assertEqual(q.spent(self.журнал), {"context7": 2, "web": 1})

    def test_битые_строки_не_ломают_счёт(self):
        (self.журнал / "события.jsonl").write_text(
            "не json\n" + json.dumps({"external": "web"}) + "\n", encoding="utf-8")
        self.assertEqual(q.spent(self.журнал), {"web": 1})

    def test_пустой_журнал_это_ноль_а_не_ошибка(self):
        self.assertEqual(q.spent(self.журнал), {})


class Вердикт(unittest.TestCase):

    def test_достигнутый_потолок_роняет(self):
        """Потолок, который можно перешагнуть, потолком не является."""
        v = q.verdict({"context7": 3}, {"context7": 3})
        self.assertEqual(v["status"], "fail")
        self.assertEqual(v["exhausted"], ["context7"])

    def test_запас_проходит(self):
        """Обратный контроль: счётчик, всегда роняющий, выключат."""
        v = q.verdict({"context7": 40}, {"context7": 2})
        self.assertEqual(v["status"], "pass")

    def test_нетронутый_источник_считается_нулём(self):
        v = q.verdict({"web": 5}, {})
        self.assertEqual(v["counters"][0]["spent"], 0)
        self.assertEqual(v["counters"][0]["left"], 5)

    def test_вердикт_называет_остаток_по_каждому(self):
        v = q.verdict({"a": 5, "b": 2}, {"a": 4})
        остатки = {c["source"]: c["left"] for c in v["counters"]}
        self.assertEqual(остатки, {"a": 1, "b": 2})


if __name__ == "__main__":
    unittest.main()
