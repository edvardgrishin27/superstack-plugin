#!/usr/bin/env python3
"""Тяжёлый MCP выдаётся адресно: одной роли, а не всем.

Браузерные MCP — самые дорогие схемы на ход: их описания уезжают в контекст
каждой роли, которой они выданы, и платит за это каждый ход, а не тот, где ими
пользовались. Ради бюджета их отключают первыми — и убирают единственный способ
ПОСМОТРЕТЬ на результат. Экономия своими руками создаёт дыру с верификацией.

Здесь заперты оба перекоса, и они разные по природе:

  · выдано лишним — счёт приходит не за то, чем пользовались;
  · не выдано нужной — роль-оракул превращается в ещё одного читателя
    отчётов, и это дороже, потому что незаметно.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at, PKG  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_mcp", at("tools", "mcp_routing.py"))
mr = importlib.util.module_from_spec(_s)
_s.loader.exec_module(mr)

ТЯЖЁЛЫЕ = [{"prefix": "mcp__playwright", "needs": "оракул",
            "why": "единственный способ посмотреть на результат",
            "cost": "самая тяжёлая схема на ход"}]


class Список(unittest.TestCase):

    def test_каждый_тяжёлый_называет_роль_цену_и_пользу(self):
        d, отказ = mr.каталог()
        self.assertIsNotNone(d, отказ)
        for h in d["heavy"]:
            with self.subTest(h["prefix"]):
                self.assertTrue(h.get("needs"))
                self.assertTrue(h.get("why"))
                self.assertTrue(h.get("cost"))

    def test_правило_называет_оба_перекоса(self):
        d, _ = mr.каталог()
        self.assertIn("Выдать всем", d["rule"])
        self.assertIn("Не выдать никому", d["rule"])


class Раздача(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.каталог = Path(self.tmp.name)

    def _агент(self, имя: str, инструменты: str) -> None:
        (self.каталог / f"{имя}.md").write_text(
            f"---\nname: {имя}\ntools: {инструменты}\n---\n\nтело\n",
            encoding="utf-8")

    def test_читает_роли_и_инструменты_из_файла(self):
        self._агент("оракул", "Read, mcp__playwright__navigate")
        self.assertEqual(mr.роли(self.каталог),
                         {"оракул": ["Read", "mcp__play" "wright__n" "avigate"]})

    def test_адресная_раздача_проходит(self):
        """Обратный контроль: проверка, всегда находящая перекос, бесполезна."""
        self._агент("оракул", "Read, mcp__playwright__navigate")
        self._агент("строитель", "Read, Write, Bash")
        self.assertEqual(mr.findings(mr.роли(self.каталог), ТЯЖЁЛЫЕ), [])

    def test_лишний_владелец_ловится(self):
        self._агент("оракул", "Read, mcp__playwright__navigate")
        self._агент("строитель", "Read, Write, mcp__playwright__navigate")
        (f,) = mr.findings(mr.роли(self.каталог), ТЯЖЁЛЫЕ)
        self.assertEqual((f["id"], f["role"]), ("granted-to-extra-role", "строитель"))

    def test_отсутствие_у_нужной_роли_ловится(self):
        """Не экономия, а тихая потеря проверки."""
        self._агент("оракул", "Read, Grep")
        self._агент("строитель", "Read, Write")
        (f,) = mr.findings(mr.роли(self.каталог), ТЯЖЁЛЫЕ)
        self.assertEqual(f["id"], "missing-where-required")
        self.assertEqual(f["role"], "оракул")

    def test_нет_нужной_роли_вовсе_это_не_находка(self):
        """Пока оракула нет в наборе ролей, требовать ему браузер не с кого."""
        self._агент("строитель", "Read, Write")
        self.assertEqual(mr.findings(mr.роли(self.каталог), ТЯЖЁЛЫЕ), [])


class ЖивойНабор(unittest.TestCase):

    def test_наши_агенты_без_перекоса(self):
        d, _ = mr.каталог()
        нашли = mr.findings(mr.роли(PKG / "agents"), d["heavy"])
        self.assertEqual(нашли, [], f"перекос в собственных агентах: {нашли}")


if __name__ == "__main__":
    unittest.main()
