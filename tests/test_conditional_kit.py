#!/usr/bin/env python3
"""Добавки сверх базы: условие и то, что ставится, лежат данными.

Базовый набор получает каждый — он одинаково полезен всем. Остальное полезно
не всем, и ставить «на всякий случай» значит платить контекстом за то, чего у
человека нет. Обратная ошибка тише и дороже: нужное не ставится, потому что в
момент установки никто не вспомнил спросить про размер дерева или вторую
машину.

Здесь заперты три отказа:

  · условие живёт в коде, и список растёт только у того, кто лезет в исходник;
  · невычислимое условие пропускается молча — такая добавка неотличима от
    невыполнимой, и обе выглядят как «не нужно»;
  · предложение превращается в установку без согласия.
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

TOOL = at("tools", "conditional_kit.py")
_s = importlib.util.spec_from_file_location("ss_kit", TOOL)
ck = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ck)

ДОБАВКИ = [
    {"id": "add.графы", "when": "proj.source_files > 400", "what": "графы",
     "why": "на большом дереве поиск по имени даёт не то", "class": "GATE"},
    {"id": "add.память", "when": "mem.store_count > 1", "what": "перенос памяти",
     "why": "половина накопленного невидима на второй машине", "class": "GATE"},
]


class Каталог(unittest.TestCase):

    def test_у_каждой_добавки_есть_условие_что_и_почему(self):
        d, отказ = ck.каталог()
        self.assertIsNotNone(d, отказ)
        for a in d["additions"]:
            with self.subTest(a["id"]):
                self.assertTrue(a.get("when"))
                self.assertTrue(a.get("what"))
                self.assertGreater(len(a.get("why", "")), 30,
                                   "причина названа формально")

    def test_условия_написаны_грамматикой_правил(self):
        """Своя грамматика разошлась бы с настоящей молча."""
        d, _ = ck.каталог()
        движок = ck._движок()
        for a in d["additions"]:
            with self.subTest(a["id"]):
                # Имена фактов подставляем нулями: важно, что выражение
                # РАЗБИРАЕТСЯ тем же движком, а не что оно истинно.
                значения = {"proj.source_files": 0, "mem.store_count": 0,
                            "disc.all_on_top_tier": False, "proj.has_ui": False}
                движок.evaluate(a["when"], значения)


class Предложения(unittest.TestCase):

    def setUp(self):
        self.движок = ck._движок().evaluate

    def test_подходящее_предлагается(self):
        v = ck.proposals(ДОБАВКИ, {"proj.source_files": 900,
                                   "mem.store_count": 1}, self.движок)
        self.assertEqual([a["id"] for a in v["propose"]], ["add.графы"])

    def test_неподходящее_не_предлагается(self):
        """Обратный контроль: набор, предлагающий всё, перестают читать."""
        v = ck.proposals(ДОБАВКИ, {"proj.source_files": 10,
                                   "mem.store_count": 1}, self.движок)
        self.assertEqual(v["propose"], [])
        self.assertEqual(len(v["not_applicable"]), 2)

    def test_невычислимое_условие_названо_а_не_проглочено(self):
        v = ck.proposals(ДОБАВКИ, {}, self.движок)
        self.assertEqual(len(v["skipped"]), 2)
        self.assertEqual(v["propose"], [])
        for s in v["skipped"]:
            self.assertIn("условие не вычислено", s["why"])

    def test_предложение_несёт_причину(self):
        v = ck.proposals(ДОБАВКИ, {"proj.source_files": 900,
                                   "mem.store_count": 1}, self.движок)
        self.assertIn("не то", v["propose"][0]["why"])


class Прогон(unittest.TestCase):

    def _факты(self, d: dict) -> Path:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8")
        json.dump({к: {"value": v} for к, v in d.items()}, tmp,
                  ensure_ascii=False)
        tmp.close()
        self.addCleanup(lambda: Path(tmp.name).unlink(missing_ok=True))
        return Path(tmp.name)

    def _запуск(self, путь: Path):
        import subprocess
        p = subprocess.run([sys.executable, str(TOOL), str(путь)],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, json.loads(p.stdout), p.stderr

    def test_факты_в_форме_сборщика_читаются(self):
        """Сборщик отдаёт {факт: {value: …}}, и это форма по умолчанию."""
        код, v, _ = self._запуск(self._факты({"proj.source_files": 900,
                                              "mem.store_count": 1,
                                              "disc.all_on_top_tier": False,
                                              "proj.has_ui": False}))
        self.assertEqual(код, 1)
        self.assertIn("add.graph-navigation", [a["id"] for a in v["propose"]])

    def test_вывод_напоминает_что_это_предложение(self):
        _, _, текст = self._запуск(self._факты({"proj.source_files": 900,
                                                "mem.store_count": 1,
                                                "disc.all_on_top_tier": False,
                                                "proj.has_ui": False}))
        self.assertIn("предложение, а не установка", текст)


if __name__ == "__main__":
    unittest.main()
