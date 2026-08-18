#!/usr/bin/env python3
"""Осмотр проекта человека: можно ли доверять зелёному в этом репозитории.

Здесь заперты две вещи, и вторая важнее первой.

ПЕРВАЯ — что осмотр находит: тестов нет, «ни одного падения» засчитано за
«прошло», секрет в отслеживаемом файле, `.env` не закрыт.

ВТОРАЯ — что он НЕ находит там, где всё в порядке. Инструмент, краснеющий на
здоровом проекте, выключают целиком, и вместе с шумом пропадают настоящие
находки. Это не теория: первая версия дала на собственном репозитории семь
провалов, и все семь были ложными — `|| true` в уборке `trap`, канонический
пример AWS из документации, тесты, настроенные через `conftest.py`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

_s = importlib.util.spec_from_file_location(
    "ss_project_doctor", PKG / "tools" / "project_doctor.py")
pd = importlib.util.module_from_spec(_s)
_s.loader.exec_module(pd)


#: Секрет-фикстура. Собирается из кусков НАМЕРЕННО: цельный литерал такой
#: длины (а) ловится сканером секретов GitHub при push, (б) разбивается
#: выкладкой автоматически — и разбивается прямо внутри строки, которую тест
#: пишет в файл. Тогда в проверяемом файле оказывается не токен, а два куска,
#: и доктор перестаёт его находить. Собранная здесь строка попадает в фикстуру
#: целой, а в исходнике длинного литерала нет.
FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1"
FAKE_TOKEN_2 = "ghp_" + "SEKRETNOEZNACHENIE12345"

class Project(unittest.TestCase):
    """Крошечный проект, где ответ известен заранее."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def healthy(self):
        """Проект, в котором всё, что осмотр умеет проверить, в порядке."""
        self.write("package.json", json.dumps({"scripts": {"test": "vitest run"}}))
        self.write("src/app.test.js", "test('works', () => {})\n")
        self.write(".superstack/mutations.json",
                   json.dumps({"mutations": [{"id": "m.a"}]}))
        self.write(".github/workflows/ci.yml", "jobs:\n  t:\n    run: npm test\n")

    def levels(self, v: dict) -> list:
        return [f["level"] for f in v["findings"]]


class TestItFindsWhatMakesGreenALie(Project):

    def test_no_test_command_is_a_failure(self):
        self.write("src/app.js", "export const x = 1\n")
        v = pd.run(self.root)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("команда тестов" in f["what"] for f in v["findings"]))

    def test_no_test_file_is_a_failure(self):
        """Скрипт `test` может возвращать ноль, не проверив ничего."""
        self.write("package.json", json.dumps({"scripts": {"test": "echo ok"}}))
        v = pd.run(self.root)
        self.assertTrue(any("ни одного тестового файла" in f["what"]
                            for f in v["findings"]), v["findings"])

    def test_a_muted_check_is_a_failure(self):
        self.healthy()
        self.write("ci.sh", "npm test || true\n")
        v = pd.run(self.root)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("|| true" in f["what"] for f in v["findings"]))

    def test_continue_on_error_is_a_failure(self):
        self.healthy()
        self.write(".github/workflows/x.yml", "steps:\n  - continue-on-error: true\n")
        self.assertEqual(pd.run(self.root)["status"], "fail")

    def test_a_secret_outside_tests_is_a_failure(self):
        self.healthy()
        self.write("src/config.js", f'const t = "{FAKE_TOKEN}"\n' )
        v = pd.run(self.root)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("токен GitHub" in f["what"] for f in v["findings"]))

    def test_the_secret_value_is_never_printed(self):
        """Отчёт называет место, а не значение: иначе осмотр размножает то,
        что нашёл, — в вывод, в журнал и в историю чата."""
        self.healthy()
        self.write("src/config.js", f'const t = "{FAKE_TOKEN_2}"\n' )
        v = pd.run(self.root)
        self.assertNotIn("SEKRETNOEZNACHENIE", json.dumps(v, ensure_ascii=False))
        self.assertNotIn("SEKRETNOEZNACHENIE", pd.human(v))

    def test_unignored_env_is_a_failure(self):
        self.healthy()
        self.write(".env", "TOKEN=x\n")
        self.write(".gitignore", "node_modules\n")
        v = pd.run(self.root)
        self.assertEqual(v["status"], "fail")


class TestItStaysQuietOnAHealthyProject(Project):
    """Каждый случай ниже был ложным провалом первой версии."""

    def test_a_healthy_project_has_no_failures(self):
        self.healthy()
        v = pd.run(self.root)
        self.assertNotIn("FAIL", self.levels(v), [f for f in v["findings"]
                                                  if f["level"] == "FAIL"])

    def test_cleanup_with_or_true_is_not_a_muted_check(self):
        """`rm -rf "$TMP" 2>/dev/null || true` в trap — уборка, а не проверка."""
        self.healthy()
        self.write("run.sh", 'trap \'rm -rf "$TMPD" 2>/dev/null || true\' EXIT\n')
        v = pd.run(self.root)
        self.assertNotIn("FAIL", self.levels(v))

    def test_a_documentation_placeholder_is_not_a_secret(self):
        """`AKIAIOSFODNN7EXAMPLE` — пример из документации AWS, а
        `ghp_xxxxxxxxxxxxxxxxxxxx` — то, что пишут в README."""
        self.healthy()
        self.write("README.md", "ключ: AKIAIOSFODNN7EXAMPLE\n"
                                "токен: ghp_xxxxxxxxxxxxxxxxxxxxxxxx\n")
        v = pd.run(self.root)
        self.assertNotIn("FAIL", self.levels(v))

    def test_pytest_configured_by_conftest_counts_as_a_test_command(self):
        self.write("tests/conftest.py", "import pytest\n")
        self.write("tests/test_x.py", "def test_x(): assert True\n")
        v = pd.run(self.root)
        self.assertFalse(any("команда тестов не найдена" in f["what"]
                             for f in v["findings"]), v["findings"])

    def test_a_fixture_secret_is_a_warning_not_a_failure(self):
        """Настоящий ключ в тестах — тоже ключ, но ловится он сверкой по
        значению, а не по форме; здесь провал был бы ложным на каждом наборе."""
        self.healthy()
        self.write("tests/test_scan.py", f'KEY = "{FAKE_TOKEN}"\n' )
        v = pd.run(self.root)
        self.assertNotIn("FAIL", self.levels(v))
        self.assertIn("WARN", self.levels(v))


class TestTheVerdictIsAnExitCode(Project):

    def test_failures_exit_one_warnings_exit_zero(self):
        self.healthy()
        self.assertEqual(pd.run(self.root)["status"], "pass")
        self.write("src/config.js", f'const t = "{FAKE_TOKEN}"\n' )
        self.assertEqual(pd.run(self.root)["status"], "fail")

    def test_every_finding_names_a_place(self):
        """Находка без файла и строки — мнение, а не находка."""
        self.healthy()
        self.write("ci.sh", "npm test || true\n")
        for f in pd.run(self.root)["findings"]:
            with self.subTest(what=f["what"][:40]):
                self.assertTrue(f["where"], f)


if __name__ == "__main__":
    unittest.main()
