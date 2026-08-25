#!/usr/bin/env python3
"""Расхождение карты механизмов с планом обязано быть ВИДНЫМ.

Ворота «план» проверяют соответствие карте, а не соответствие карты плану.
Неполнота самой карты им невидима — и однажды «34 из 34» горело зелёным поверх
14 групп пропусков, найденных независимой сверкой. Карта тогда выросла до 137.

Сверка осталась разовой: ни один инструмент не смотрел на файл плана. Здесь
проверяется самая дешёвая часть, которую вообще можно посчитать кодом, —
отпечаток плана, с которым карту сверяли. Совпал — сверка относится к нынешнему
плану. Не совпал — полнота карты снова НЕИЗВЕСТНА, и это обязано быть сказано
вслух, а не пройти молча.

Чего эти тесты НЕ утверждают: что карта полна. Полноту устанавливает чтение
плана другой моделью; отметку ставит человек, и врать ей можно. Механизм лишь
не даёт расхождению остаться незамеченным.
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

from paths import at

GAUNTLET = at("tools", "gauntlet.py")
STAMP = at("tools", "plan_stamp.py")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gt = _load("ss_gauntlet_drift", GAUNTLET)
st = _load("ss_plan_stamp", STAMP)

КАРТА = {"schema": "superstack.plan-coverage.v1", "source": "тест",
         "mechanisms": [{"id": "X", "layer": "тест", "mechanism": "проверяемое",
                         "evidence": {"file": "README.md", "contains": "SUPERSTACK"}}]}


class Сверка(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.план = self.root / "план.md"
        self.план.write_text("восемь слоёв агентной ОС\n", encoding="utf-8")
        self.карта = self.root / "plan-coverage.json"
        self._записать(dict(КАРТА))
        self._было = os.environ.get(st.PLAN_ENV)
        os.environ[st.PLAN_ENV] = str(self.план)
        self.addCleanup(self._вернуть_среду)

    def _вернуть_среду(self):
        if self._было is None:
            os.environ.pop(st.PLAN_ENV, None)
        else:
            os.environ[st.PLAN_ENV] = self._было

    def _записать(self, d: dict) -> None:
        self.карта.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def _отметить(self, кто: str = "проверяющая модель") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(STAMP), "--by", кто,
                               "--map", str(self.карта)],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, st.PLAN_ENV: str(self.план)})


class БезОтметки(Сверка):

    def test_никогда_не_сверялись_это_не_успех(self):
        """Карта без отметки — «полноту никто не проверял», а не «всё сходится»."""
        v = gt.gate_reconciled(self.карта)
        self.assertEqual(v["status"], "unknown")

    def test_причина_названа(self):
        v = gt.gate_reconciled(self.карта)
        self.assertIn("сверял", v["detail"])


class СПланом(Сверка):

    def test_отметка_делает_ворота_зелёными(self):
        p = self._отметить()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(gt.gate_reconciled(self.карта)["status"], "pass")

    def test_правка_плана_гасит_вердикт(self):
        """Ровно тот случай, ради которого всё: план поменяли, карту — нет."""
        self._отметить()
        self.план.write_text("восемь слоёв агентной ОС\nи девятый\n",
                             encoding="utf-8")
        v = gt.gate_reconciled(self.карта)
        self.assertEqual(v["status"], "unknown")
        self.assertIn("правил", v["detail"])

    def test_пропавший_план_это_не_успех(self):
        self._отметить()
        self.план.unlink()
        self.assertEqual(gt.gate_reconciled(self.карта)["status"], "unknown")


class Отметка(Сверка):

    def test_без_имени_сверявшего_отказ(self):
        """«Кто-то когда-то сверял» — это ровно то, что уже прошло за проверку
        и оказалось ничем."""
        p = subprocess.run([sys.executable, str(STAMP), "--map", str(self.карта)],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 3)
        self.assertEqual(gt.gate_reconciled(self.карта)["status"], "unknown")

    def test_без_плана_не_отмечается(self):
        self.план.unlink()
        p = self._отметить()
        self.assertEqual(p.returncode, 2)

    def test_отметка_записывает_кто_и_сколько(self):
        self._отметить("вторая модель")
        d = json.loads(self.карта.read_text("utf-8"))["reconciled"]
        self.assertEqual(d["by"], "вторая модель")
        self.assertEqual(d["mechanisms"], len(КАРТА["mechanisms"]))
        self.assertTrue(d["digest"].startswith("sha256:"))


class НевыполненныеОбещания(Сверка):
    """Механизм, названный планом и не построенный, обязан быть виден воротам.

    Сверка находит два разных сорта расхождений: пропуски КАРТЫ (механизм есть
    в коде, но не описан) и пропуски КОДА (план обещал, продукт не содержит).
    Первые вписываются в карту с уликой. Вторые вписать нечем — улики не
    существует, — и если держать их в стороне молча, «156 из 156» загорится
    зелёным поверх невыполненных обещаний. Ровно та дыра, ради которой ворота
    и заводились, только этажом выше.
    """

    def _дыры(self, *строки: int) -> None:
        (self.карта.parent / "plan-gaps.json").write_text(
            json.dumps({"schema": "superstack.plan-gaps.v1",
                        "gaps": [{"plan_line": n, "mechanism": "не построено"}
                                 for n in строки]}, ensure_ascii=False),
            encoding="utf-8")

    def test_непостроенное_гасит_вердикт(self):
        self._отметить()
        self._дыры(166, 203)
        v = gt.gate_reconciled(self.карта)
        self.assertEqual(v["status"], "unknown")
        self.assertIn("не построено", v["detail"])

    def test_пустой_список_дыр_не_мешает(self):
        """Обратный контроль: ворота, никогда не зеленеющие, бесполезны."""
        self._отметить()
        self._дыры()
        self.assertEqual(gt.gate_reconciled(self.карта)["status"], "pass")

    def test_битый_список_дыр_это_не_успех(self):
        self._отметить()
        (self.карта.parent / "plan-gaps.json").write_text("{не json",
                                                          encoding="utf-8")
        self.assertEqual(gt.gate_reconciled(self.карта)["status"], "unknown")


class ОтметкаНеТащитДомашнийПуть(Сверка):
    """Файл карты уезжает в публичный репозиторий — вместе со всем, что в нём.

    Абсолютный путь к плану это домашний каталог человека: его имя, а иногда
    и имя работодателя. Проверка выкладки такое блокирует, и правильно —
    но чинить надо в источнике, а не глушить проверку.
    """

    def test_путь_записан_через_тильду(self):
        self._отметить()
        d = json.loads(self.карта.read_text("utf-8"))["reconciled"]
        self.assertFalse(d["plan"].startswith(str(Path.home())),
                         "в карту уехал абсолютный домашний путь")


class ВоротаВПланке(unittest.TestCase):

    def test_сверка_стоит_в_списке_ворот(self):
        """Ворота, не попавшие в список, не выполняются никогда — та же болезнь
        недостижимости, что и у инструмента без вызова."""
        self.assertIn("сверка", {имя for имя, _ in gt.GATES})


if __name__ == "__main__":
    unittest.main()
