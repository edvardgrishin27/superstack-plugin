#!/usr/bin/env python3
"""То ли запускается, что правят.

Откуда взялся этот набор.

Права агента приёмки исправили в репозитории: `tools: Read` стало
`tools: Read, Bash, Glob, Grep`. Тест позеленел, мутация начала ловиться, планка
взялась целиком — семь ворот из семи. Через десять минут приёмка запустилась и
первой строкой доложила: «у меня только чтение, npm test выполнить нечем».

Правка лежала в рабочем дереве, а агент берётся из установленного плагина —
копии в кэше, которая обновляется отдельной командой. Всё, что проверяет набор,
читает репозиторий; всё, что исполняется, читает кэш. Между ними может быть
любая разница, и её не видит ни один тест: набор зелёный, потому что проверяет
намерение, а не то, что запустится.

Проверка на живой машине в тот момент: разошлось 5 пакетов из 7.
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
from paths import PKG  # noqa: E402

TOOL = PKG / "tools" / "installed_drift.py"
_s = importlib.util.spec_from_file_location("ss_installed_drift", TOOL)
dr = importlib.util.module_from_spec(_s)
_s.loader.exec_module(dr)


class Fake:
    """Репозиторий и кэш установленных пакетов рядом, во временном каталоге."""

    def __init__(self, tmp: str):
        self.root = Path(tmp)
        self.repo = self.root / "repo"
        self.cache = self.root / "cache" / "superstack"
        (self.repo / "plugins").mkdir(parents=True)
        self.cache.mkdir(parents=True)
        dr.CACHE = self.root / "cache"

    def source(self, plugin: str, rel: str, text: str):
        p = self.repo / "plugins" / plugin / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return self

    def installed(self, plugin: str, version: str, rel: str, text: str):
        p = self.cache / plugin / version / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return self


class TestDriftIsSeen(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fake(self.tmp.name)

    def test_identical_copies_pass(self):
        self.f.source("superstack-guard", "agents/judge.md", "tools: Read, Bash")
        self.f.installed("superstack-guard", "0.2.5", "agents/judge.md",
                         "tools: Read, Bash")
        self.assertEqual(dr.check(self.f.repo)["status"], "pass")

    def test_a_fixed_source_with_a_stale_install_fails(self):
        """Ровно живой случай: права поправили, запускается прежнее."""
        self.f.source("superstack-guard", "agents/judge.md", "tools: Read, Bash")
        self.f.installed("superstack-guard", "0.2.5", "agents/judge.md",
                         "tools: Read")
        v = dr.check(self.f.repo)
        self.assertEqual(v["status"], "fail")
        self.assertIn("agents/judge.md", v["plugins"][0]["changed"])

    def test_a_file_never_installed_is_named(self):
        """Новый инструмент, которого в установленном пакете нет вовсе, —
        не «расхождение», а отсутствие: его вызов упадёт «нет файла»."""
        self.f.source("superstack-guard", "tools/new.py", "print(1)")
        self.f.installed("superstack-guard", "0.2.5", "tools/old.py", "print(1)")
        v = dr.check(self.f.repo)
        self.assertIn("tools/new.py", v["plugins"][0]["missing"])

    def test_versions_are_compared_as_numbers(self):
        """Строкой «0.2.10» младше «0.2.9», и сверка молча берёт не ту копию."""
        self.f.source("superstack-guard", "tools/a.py", "новое")
        self.f.installed("superstack-guard", "0.2.9", "tools/a.py", "старое")
        self.f.installed("superstack-guard", "0.2.10", "tools/a.py", "новое")
        self.assertEqual(dr.check(self.f.repo)["status"], "pass")

    def test_a_missing_install_is_not_a_drift(self):
        """«Не установлено» и «установлено другое» — разные утверждения, и
        первое не должно краснеть: пакет могли не ставить намеренно."""
        self.f.source("superstack-guard", "tools/a.py", "x")
        self.assertEqual(dr.check(self.f.repo)["status"], "unknown")

    def test_only_executable_files_are_compared(self):
        """Тесты в установленный пакет не едут, и их расхождение ничего не
        значит — иначе проверка кричит всегда и её перестают читать."""
        self.f.source("superstack-guard", "tools/a.py", "x")
        self.f.source("superstack-guard", "tests/test_a.py", "совсем другое")
        self.f.installed("superstack-guard", "0.2.5", "tools/a.py", "x")
        self.assertEqual(dr.check(self.f.repo)["status"], "pass")


class TestExitCodes(unittest.TestCase):

    def _run(self, path):
        return subprocess.run([sys.executable, str(TOOL), str(path)],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_a_directory_without_plugins_is_named(self):
        with tempfile.TemporaryDirectory() as t:
            r = self._run(t)
            self.assertEqual(r.returncode, 3)
            self.assertIn("НЕ УДАЛОСЬ", r.stderr)


if __name__ == "__main__":
    unittest.main()
