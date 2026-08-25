#!/usr/bin/env python3
"""Сканеры секретов: каскад из трёх слоёв, а не одна проверка.

Каждый слой по отдельности дырявый, и дыры у них разные:

  · только до коммита — обходится флагом `--no-verify`, причём именно в спешке;
  · только до слияния — ловит секрет, который УЖЕ в истории ветки, а
    попавшее в git и уехавшее на сервер считается утёкшим навсегда;
  · только по расписанию — находит вчерашнее.

Проверяется наличие слоёв, а не находки сканера: вопрос «стоит ли», а не
«находит ли». Отдельно заперто то, что легко подделать, — «security» в имени
шага защитой не является.
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

TOOL = at("tools", "scan_cascade.py")

ХУК = "#!/bin/sh\ngitleaks protect --staged || exit 1\n"
CI_PR = ("name: проверки\non:\n  pull_request:\njobs:\n  scan:\n"
         "    steps:\n      - run: gitleaks detect\n")
CI_CRON = ("name: ночное\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs:\n"
           "  scan:\n    steps:\n      - run: semgrep --config auto\n")


class Проект(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".git" / "hooks").mkdir(parents=True)

    def _файл(self, путь: str, текст: str) -> None:
        p = self.root / путь
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(текст, encoding="utf-8")

    def _прогон(self) -> tuple:
        p = subprocess.run([sys.executable, str(TOOL), str(self.root)],
                           capture_output=True, text=True, timeout=60)
        return p.returncode, json.loads(p.stdout), p.stderr

    def _все_три(self) -> None:
        self._файл(".git/hooks/pre-commit", ХУК)
        self._файл(".github/workflows/pr.yml", CI_PR)
        self._файл(".github/workflows/ночное.yml", CI_CRON)


class ОдинСлойНеКаскад(Проект):

    def test_пустой_проект_роняет(self):
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertEqual(len(v["missing"]), 3)

    def test_только_до_коммита_не_считается(self):
        """Локальный хук обходится одним флагом — именно в спешке."""
        self._файл(".git/hooks/pre-commit", ХУК)
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("pre_merge", v["missing"])
        self.assertIn("scheduled", v["missing"])

    def test_только_ci_не_считается(self):
        """Проверка до слияния ловит секрет, уже попавший в историю ветки."""
        self._файл(".github/workflows/pr.yml", CI_PR)
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("pre_commit", v["missing"])

    def test_все_три_слоя_проходят(self):
        """Обратный контроль: проверка, никогда не зеленеющая, бесполезна."""
        self._все_три()
        код, v, _ = self._прогон()
        self.assertEqual(код, 0, v)
        self.assertTrue(all(v["layers"].values()))


class ПодделкаНеСчитается(Проект):

    def test_слово_security_защитой_не_является(self):
        """Увидеть «security» в имени шага и записать это в защиту —
        обмануть себя дешевле, чем настроить сканер."""
        self._все_три()
        self._файл(".git/hooks/pre-commit",
                   "#!/bin/sh\necho 'security check passed'\n")
        код, v, _ = self._прогон()
        self.assertEqual(код, 1)
        self.assertIn("pre_commit", v["missing"])

    def test_каждый_найденный_слой_называет_адрес(self):
        """Слой без файла проверить нельзя, а значит нельзя и засчитать."""
        self._все_три()
        _, v, _ = self._прогон()
        for имя, знач in v["layers"].items():
            with self.subTest(имя):
                self.assertTrue(знач["file"])
                self.assertTrue(знач["scanner"])

    def test_другой_менеджер_хуков_тоже_считается(self):
        """husky и lefthook — те же три слоя, только другой файл."""
        self._файл(".husky/pre-commit", ХУК)
        self._файл(".github/workflows/pr.yml", CI_PR)
        self._файл(".github/workflows/ночное.yml", CI_CRON)
        self.assertEqual(self._прогон()[0], 0)


class НеРепозиторий(unittest.TestCase):

    def test_без_git_это_не_провал_проекта(self):
        """«Ставить не на что» и «не поставили» — разные утверждения."""
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, str(TOOL), d],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
