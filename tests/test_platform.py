#!/usr/bin/env python3
"""Непроверенная платформа называется вслух, а не выясняется по ошибкам.

Хуки написаны на `sh`, тормоз — скрипт оболочки, прогон делали на macOS. На
Windows человек получит не отказ, а непонятные ошибки: хук молча не сработал,
пауза не нашлась, тест упал на `geteuid`. Он решит, что сломан его компьютер.

Худший исход — не «не работает», а «непонятно, что происходит». Здесь заперто
то, что превращает второе в первое:

  · платформа вне списка проверенных даёт код 2, а не молчаливый ноль;
  · сказано ПОИМЁННО, что именно не работает, а не «возможны проблемы»;
  · сказано и обратное — что работает, иначе человек решит, что не работает всё;
  · «проверено» определяется наличием оболочки, а не именем системы: Windows
    с Git Bash и без него — разные случаи, и объявлять их одинаково значит
    врать половине людей.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import at  # noqa: E402

TOOL = at("tools", "platform_check.py")
_s = importlib.util.spec_from_file_location("ss_platform", TOOL)
pc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(pc)


class ЗдесьПроверено(unittest.TestCase):

    def test_на_своей_платформе_код_ноль(self):
        p = subprocess.run([sys.executable, str(TOOL), "--json"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "pass")


class ЧужаяПлатформа(unittest.TestCase):
    """Проверяется подстановкой, а не ожиданием чужой машины."""

    def setUp(self):
        self._система = pc.platform.system
        self._which = pc.shutil.which
        self.addCleanup(setattr, pc.platform, "system", self._система)
        self.addCleanup(setattr, pc.shutil, "which", self._which)

    def _windows(self, есть_оболочка: bool) -> dict:
        pc.platform.system = lambda: "Windows"
        pc.shutil.which = lambda имя: ("C:\\sh.exe" if есть_оболочка else None)
        return pc.verdict()

    def test_windows_без_оболочки_это_не_успех(self):
        v = self._windows(есть_оболочка=False)
        self.assertEqual(v["status"], "unknown")

    def test_названо_что_именно_не_работает(self):
        """«Возможны проблемы» — не сообщение: под ним нельзя ничего решить."""
        v = self._windows(есть_оболочка=False)
        что = {b["что"] for b in v["broken"]}
        self.assertIn("хуки", что)
        self.assertNotIn("тормоз", что,
                         "тормоз переехал на Python и работает без оболочки — "
                         "держать его в списке поломок значит врать в другую "
                         "сторону")
        for b in v["broken"]:
            with self.subTest(b["что"]):
                self.assertGreater(len(b["почему"]), 30,
                                   "причина без объяснения бесполезна")

    def test_названо_и_что_работает(self):
        """Иначе человек решит, что не работает ВСЁ — и это тоже неправда."""
        v = self._windows(есть_оболочка=False)
        self.assertTrue(v["works"])

    def test_оболочка_есть_но_прогона_не_было(self):
        """Git Bash снимает половину поломок и НЕ делает платформу проверенной."""
        v = self._windows(есть_оболочка=True)
        self.assertEqual(v["status"], "unknown")
        self.assertEqual(v["broken"], [],
                         "с оболочкой хуки и тормоз работают — ломать их незачем")
        self.assertIn("живого прогона здесь не было", v["detail"])

    def test_различие_по_оболочке_а_не_по_имени(self):
        """Объявлять Windows с Git Bash и без него одинаково — врать половине."""
        без = self._windows(есть_оболочка=False)
        с = self._windows(есть_оболочка=True)
        self.assertNotEqual(без["broken"], с["broken"])


class ПроверенноеНеПодделать(unittest.TestCase):

    def test_список_проверенных_короткий(self):
        """Сюда попадает только то, где прогон реально прошёл от начала до конца."""
        self.assertEqual(set(pc.ПРОВЕРЕНЫ), {"Darwin", "Linux"})


class ПереносимостьИнструментов(unittest.TestCase):
    """Питоновская часть обязана работать там, где нет POSIX.

    Два места ломались молча и по-разному, и оба — не «падением», а НЕВЕРНЫМ
    ОТВЕТОМ, что хуже: команда разбиралась в мусор, а чужой замок объявлялся
    мёртвым.
    """

    def test_команда_с_обратными_слэшами_не_рассыпается(self):
        """В POSIX-режиме `C:\\проект\\npm.cmd` превращается в `C:проектnpm.cmd`:
        команда не найдётся, а сообщение будет про отсутствующий файл — человек
        пойдёт искать поломку не там."""
        import importlib.util
        s = importlib.util.spec_from_file_location("ss_bar_p", at("tools", "bar.py"))
        bar = importlib.util.module_from_spec(s)
        s.loader.exec_module(bar)
        было = bar.os.name
        try:
            bar.os.name = "nt"
            argv, отказ = bar._argv(Path("."), {"run": r"C:\проект\npm.cmd test"})
        finally:
            bar.os.name = было
        self.assertEqual(отказ, "")
        self.assertIn("\\", argv[0], f"путь рассыпался: {argv}")

    def test_чужой_замок_на_windows_считается_живым(self):
        """Направление ошибки выбрано по цене: счесть чужой замок мёртвым —
        сломать дерево тому, кто как раз меряет."""
        import importlib.util
        s = importlib.util.spec_from_file_location("ss_pt_p",
                                                   at("tools", "prove_tests.py"))
        pt = importlib.util.module_from_spec(s)
        s.loader.exec_module(pt)
        было = pt.os.name
        try:
            pt.os.name = "nt"
            self.assertTrue(pt._alive(999_999),
                            "на Windows «не знаю» обязано читаться как «жив»")
        finally:
            pt.os.name = было


if __name__ == "__main__":
    unittest.main()
