#!/usr/bin/env python3
"""Деструктивные команды: один список и слой, который его исполняет.

Список деструктивных команд — единственная межрепозиторная константа корпуса:
пять источников в четырёх разных слоях исполнения, содержимое почти дословно
совпадает. Поэтому проверять надо не содержимое, а два свойства, без которых
список бесполезен: он ДОХОДИТ до слоя исполнения и он не правит чужую машину
молча.

Три отказа, которые здесь заперты:

  · «часть запретов стоит» выдаётся за «запреты стоят»;
  · нечитаемые настройки читаются как «недостающего нет»;
  · инструмент дописывает настройки человека без явного разрешения — тот же
    захват, от которого он защищает.
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

DENY = at("tools", "deny_list.py")
СПИСОК = at("data", "destructive-commands.json")


class Перечень(unittest.TestCase):

    def setUp(self):
        self.d = json.loads(СПИСОК.read_text("utf-8"))

    def test_список_не_пуст(self):
        self.assertGreaterEqual(len(self.d["commands"]), 10)

    def test_у_каждой_команды_есть_причина_и_счёт_источников(self):
        """Запись без причины нельзя ни обсудить, ни отменить осознанно."""
        for c in self.d["commands"]:
            with self.subTest(c.get("pattern")):
                self.assertTrue(c.get("pattern"))
                self.assertTrue(c.get("why"), "запрет без причины — суеверие")
                self.assertGreaterEqual(c.get("sources", 0), 3,
                                        "константа корпуса — это ≥3 источника")

    def test_шаблоны_не_повторяются(self):
        шаблоны = [c["pattern"] for c in self.d["commands"]]
        self.assertEqual(len(шаблоны), len(set(шаблоны)))

    def test_правило_запрещает_молчаливое_удаление(self):
        """Запись уходит из списка с причиной или не уходит вовсе.

        Первое, что делает оптимизирующий агент с мешающим запретом, — удаляет
        его. Правило обязано называть это вслух, иначе список усыхает до
        удобного.
        """
        self.assertIn("молчаливое удаление", self.d["rule"].lower())


class Настройки(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / "settings.json"

    def _записать(self, d) -> None:
        self.p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def _запуск(self, *флаги: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(DENY), *флаги, str(self.p)],
                              capture_output=True, text=True, timeout=60)

    def test_пустые_настройки_дают_полный_список_недостающего(self):
        self._записать({})
        p = self._запуск("--check")
        self.assertEqual(p.returncode, 1)
        всего = len(json.loads(СПИСОК.read_text("utf-8"))["commands"])
        self.assertEqual(len(json.loads(p.stdout)["missing"]), всего)

    def test_недостающие_названы_поимённо(self):
        """«Часть запретов стоит» и «запреты стоят» — разные утверждения."""
        полный = json.loads(СПИСОК.read_text("utf-8"))["commands"]
        self._записать({"permissions": {"deny": [полный[0]["pattern"]]}})
        p = self._запуск("--check")
        self.assertEqual(p.returncode, 1)
        нет = json.loads(p.stdout)["missing"]
        self.assertNotIn(полный[0]["pattern"], нет)
        self.assertEqual(len(нет), len(полный) - 1)

    def test_полные_настройки_зелёные(self):
        """Обратный контроль: проверка, никогда не зеленеющая, бесполезна."""
        полный = json.loads(СПИСОК.read_text("utf-8"))["commands"]
        self._записать({"permissions": {"deny": [c["pattern"] for c in полный]}})
        self.assertEqual(self._запуск("--check").returncode, 0)

    def test_нет_файла_это_не_успех(self):
        p = self._запуск("--check")
        self.assertEqual(p.returncode, 2, "отсутствие настроек — не «всё стоит»")

    def test_битые_настройки_это_не_успех(self):
        self.p.write_text("{не json", encoding="utf-8")
        self.assertEqual(self._запуск("--check").returncode, 2)


class Запись(Настройки):

    def test_без_явного_разрешения_не_пишет(self):
        """Инструмент, правящий машину человека молча, — тот же захват."""
        self._записать({"permissions": {"deny": []}})
        было = self.p.read_text("utf-8")
        p = self._запуск("--apply")
        self.assertEqual(p.returncode, 3)
        self.assertEqual(self.p.read_text("utf-8"), было, "файл всё-таки изменён")

    def test_с_разрешением_дописывает_и_настройки_остаются_валидными(self):
        self._записать({"permissions": {"allow": ["Bash(ls *)"]}})
        p = self._запуск("--apply", "--yes")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(self.p.read_text("utf-8"))
        self.assertEqual(d["permissions"]["allow"], ["Bash(ls *)"],
                         "соседний ключ затёрт")
        self.assertEqual(self._запуск("--check").returncode, 0)

    def test_повторная_запись_не_плодит_дубли(self):
        self._записать({})
        self._запуск("--apply", "--yes")
        сколько = len(json.loads(self.p.read_text("utf-8"))["permissions"]["deny"])
        self._запуск("--apply", "--yes")
        self.assertEqual(
            len(json.loads(self.p.read_text("utf-8"))["permissions"]["deny"]),
            сколько)

    def test_deny_не_список_это_отказ(self):
        self._записать({"permissions": {"deny": "всё"}})
        self.assertEqual(self._запуск("--apply", "--yes").returncode, 2)


if __name__ == "__main__":
    unittest.main()
