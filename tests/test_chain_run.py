#!/usr/bin/env python3
"""Сквозной прогон волны 1: настоящие инструменты подряд, без модели.

Это единственный тест цепочки. Тесты отдельных инструментов подкладывают
файлы руками и не видят, что один инструмент пишет запись под одним именем,
а другой читает под другим. Здесь цепочка идёт процессами: зелёная на целом
плагине и красная, с названной стадией, на плагине с разорванной стыковкой.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO  # noqa: E402

CHAIN = REPO / "tools" / "chain_run.py"


def run_chain(*args: str):
    return subprocess.run([sys.executable, str(CHAIN), *args],
                          capture_output=True, text=True, timeout=900)


class ChainTest(unittest.TestCase):
    def broken_copy(self, rel: str, old: str, new: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="chain-copy-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        copy = tmp / "superstack"
        shutil.copytree(PKG, copy, ignore=shutil.ignore_patterns("__pycache__"))
        f = copy / rel
        text = f.read_text("utf-8")
        self.assertEqual(text.count(old), 1, "якорь поломки пропал из файла")
        f.write_text(text.replace(old, new), "utf-8")
        return copy

    def test_whole_chain_is_green(self):
        r = run_chain()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("цепочка прошла", r.stdout)

    def test_split_record_name_names_the_autonomy_stage(self):
        copy = self.broken_copy(
            "tools/verify.py", 'RECORD = ".superstack/verify-last.json"',
            'RECORD = ".superstack/verify-result.json"')
        r = run_chain("--plugin", str(copy))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("сорвалась стадия «автономия читает запись»", r.stdout)

    def test_report_trusting_claimed_names_the_report_stage(self):
        copy = self.broken_copy(
            "tools/report.py",
            '"проверено": [t for t in tasks if t.get("status") == PROVEN],',
            '"проверено": [t for t in tasks '
            'if t.get("status") in (PROVEN, CLAIMED)],')
        r = run_chain("--plugin", str(copy))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("сорвалась стадия «отчёт: слово только у доказанного»",
                      r.stdout)

    def test_bad_call_is_exit_3(self):
        self.assertEqual(run_chain("--nope").returncode, 3)
        self.assertEqual(run_chain("--plugin", "/нет/такой").returncode, 3)


if __name__ == "__main__":
    unittest.main()
