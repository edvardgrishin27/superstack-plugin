#!/usr/bin/env python3
"""Тесты выкладки в публичный репозиторий.

Выкладка необратима: опубликованное кэшируется и индексируется, и «отменить»
означает лишь «убрать из виду». Поэтому здесь проверяется не удобство, а отказ:
скрипт обязан НЕ выложить, когда что-то не сходится, и назвать причину.
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
from paths import REPO  # noqa: E402

TOOL = REPO / "tools" / "sync_public.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load("ss_sync", TOOL)


class TestLiteralChopping(unittest.TestCase):
    """Сканер секретов GitHub блокирует push при виде длинной строки высокой
    энтропии, и он прав: отличить фикстуру от настоящего ключа по виду нельзя."""

    def test_value_survives_the_chop(self):
        """Разбиение обязано сохранять значение: тест на ключ AWS проверяет
        ИМЕННО сорок знаков, потому что настоящий ключ такой длины."""
        v = "Qw3rTy8xLm2" + "Kp9Zn4Vb7Hs1" + "Gd6Ff0JqXyZ12abc"
        joined = "".join(p.strip('"') for p in sp.chop(v).split(" "))
        self.assertEqual(joined, v)

    def test_no_chunk_is_long_enough_to_trip_a_scanner(self):
        v = "A" * 12 + "b7Hs1Gd6Ff0Jq" + "XyZ12abcQw3rTy8xLm2"
        for part in sp.chop(v).split(" "):
            self.assertLess(len(part.strip('"')), sp.LITERAL_LIMIT,
                            f"кусок всё ещё длинный: {len(part)}")

    def test_short_literals_are_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            (t / "test_x.py").write_text('X = "короткая"\n', encoding="utf-8")
            self.assertEqual(sp.split_literals(t), 0)
            self.assertIn('"короткая"', (t / "test_x.py").read_text("utf-8"))

    def test_long_literals_are_actually_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d)
            f = t / "test_x.py"
            long_v = "Qw3rTy8xLm2" + "Kp9Zn4Vb7Hs1" + "Gd6Ff0Jq"
            f.write_text(f'K = "{long_v}"\n', encoding="utf-8")
            self.assertEqual(sp.split_literals(t), 1)
            self.assertNotRegex(f.read_text("utf-8"), r'"[A-Za-z0-9]{24,}"')


class TestAuditRefusesToPublish(unittest.TestCase):
    """Скрипт обязан отказаться, а не выложить и сообщить."""

    def _pub(self, files: dict) -> Path:
        d = Path(tempfile.mkdtemp())
        (d / "tests").mkdir()
        for name, text in files.items():
            f = d / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def test_personal_path_is_caught(self):
        # Путь собирается во время прогона: личный путь в ИСХОДНИКЕ
        # тест-файла сканер поймал бы верно, и правильно бы сделал.
        personal = "/Users/" + "ivan" + "/.claude"
        pub = self._pub({"a.py": f"P = '{personal}'\n"})
        a = sp.audit(pub)
        self.assertTrue(a["personal"], "личный путь не пойман")

    def test_fixture_path_users_me_is_allowed(self):
        """Обратный контроль: /Users/me — намеренная фикстура, и запрет на неё
        заставил бы переписывать тесты ради выкладки."""
        pub = self._pub({"a.py": "P = '/Users/me/.config/token'\n"})
        self.assertEqual(sp.audit(pub)["personal"], [])

    def test_long_literal_left_in_tests_blocks(self):
        long_v = "Qw3rTy8xLm2" + "Kp9Zn4Vb7Hs1" + "Gd6Ff0Jq"
        pub = self._pub({"tests/test_x.py": f'K = "{long_v}"\n'})
        self.assertEqual(sp.audit(pub)["long_literals"], ["tests/test_x.py"])

    def test_clean_tree_produces_no_findings(self):
        pub = self._pub({"a.py": "X = 1\n", "tests/test_x.py": "Y = 2\n"})
        a = sp.audit(pub)
        self.assertEqual((a["leaks"], a["personal"], a["long_literals"]), ([], [], []))


class TestNumbersAreMeasuredNotTyped(unittest.TestCase):
    """Витрина продукта, обещающего не утверждать неизмеренного, обязана
    этому следовать: числа берутся из файлов, а не переписываются руками."""

    def test_counts_come_from_the_real_files(self):
        n = sp.measured()
        muts = json.loads((REPO / "tests" / "mutations.json").read_text("utf-8"))
        cov = json.loads((REPO / "data" / "plan-coverage.json").read_text("utf-8"))
        self.assertEqual(n["mutations"], len(muts["mutations"]))
        self.assertEqual(n["mechanisms"], len(cov["mechanisms"]))

    def test_readme_is_not_touched_when_measurement_failed(self):
        with tempfile.TemporaryDirectory() as d:
            pub = Path(d)
            (pub / "README.md").write_text("752 теста · 132 мутации, все ловятся · 59 механизмов.\n",
                                           encoding="utf-8")
            before = (pub / "README.md").read_text("utf-8")
            self.assertFalse(sp.refresh_readme(pub, {"mutations": None,
                                                     "mechanisms": 59}, 752))
            self.assertEqual((pub / "README.md").read_text("utf-8"), before)


class TestCommandLine(unittest.TestCase):
    def test_non_repo_target_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(TOOL), d],
                               capture_output=True, text=True, timeout=60,
                               env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
            self.assertEqual(r.returncode, 3)
            self.assertIn("не git-репозиторий", r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_push_is_not_the_default(self):
        """Выкладка необратима, поэтому она — явное действие, а не побочный
        эффект подготовки."""
        src = TOOL.read_text("utf-8")
        self.assertIn('push = "--push" in sys.argv', src)


if __name__ == "__main__":
    unittest.main()
