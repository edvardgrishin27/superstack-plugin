#!/usr/bin/env python3
"""Нечитаемый файл состояния не подменяется пустым.

Пять инструментов читали свой файл одинаково: «не разобрался — значит пусто».
Следующая запись клала это пустое поверх, и пропадало всё сразу: ход стройки,
требования со словами человека, находки ревью, разбор рисков, бриф дизайна.
Ошибки при этом не было ни одной, код возврата был ноль.

Теперь различаются два случая. Файла нет — законное начало, всё как раньше.
Файл есть, но не читается — рядом кладётся копия (имя несёт отпечаток
содержимого, повторное чтение того же файла копий не плодит), и инструмент
останавливается своим кодом с одной фразой человеку.
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

TOOLS = PKG / "tools"
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}

#: Код остановки «файл повреждён». Число записано здесь буквально: тест
#: держит договор с тем, кто зовёт инструмент, а не повторяет константу.
КОД_ПОВРЕЖДЁН = 5

#: Фраза, которую человек слышит при остановке.
ФРАЗА = "повреждён, копия сохранена, ничего не стёрто"

БИТОЕ = b"{"


def _модуль(имя: str):
    spec = importlib.util.spec_from_file_location(f"ss_broken_{имя}",
                                                  TOOLS / f"{имя}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _пуск(инструмент: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOLS / инструмент), *args],
                          capture_output=True, text=True, env=ENV, timeout=60)


#: Пишущая команда каждого инструмента и слово, которым он называет свой файл.
ПИШУЩИЕ = {
    "progress.py": (lambda f: ["task", f, "01", "каркас", "--wave", "1",
                               "--status", "waiting"], "файл хода"),
    "manifest.py": (lambda f: ["add", f, "R01", "--quote", "хочу сайт"],
                    "файл требований"),
    "review.py": (lambda f: ["find", f, "--axis", "craft", "--where", "a.py:1",
                             "--what", "не то", "--must", "так"],
                  "файл ревью"),
    "premortem.py": (lambda f: ["add", f, "--q", "провал", "--what", "никто",
                                "--then", "спросить"], "файл разбора рисков"),
    "design_brief.py": (lambda f: [f, "--product", "сайт мастера"],
                        "файл брифа дизайна"),
}


class TestBrokenFileStopsTheWriter(unittest.TestCase):
    """Для каждого из пяти: код не ноль, байты целы, рядом одна копия."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _копии(self, f: Path) -> list:
        return sorted(p.name for p in f.parent.glob(f.name + ".*.bak"))

    def test_each_writer_keeps_the_broken_bytes_and_sets_a_copy_aside(self):
        for инструмент, (команда, слово) in ПИШУЩИЕ.items():
            with self.subTest(инструмент=инструмент):
                f = self.dir / f"{инструмент}.json"
                f.write_bytes(БИТОЕ)
                r = _пуск(инструмент, *команда(str(f)))
                self.assertEqual(r.returncode, КОД_ПОВРЕЖДЁН,
                                 f"{инструмент}: {r.stderr[-400:]}")
                self.assertEqual(f.read_bytes(), БИТОЕ,
                                 f"{инструмент} затёр нечитаемый файл")
                self.assertIn(ФРАЗА, r.stderr)
                self.assertIn(слово, r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                копии = self._копии(f)
                self.assertEqual(len(копии), 1, копии)
                self.assertEqual((self.dir / копии[0]).read_bytes(), БИТОЕ)
                # Второй вызов по тому же файлу копию не добавляет.
                _пуск(инструмент, *команда(str(f)))
                self.assertEqual(self._копии(f), копии)

    def test_a_missing_file_still_starts_clean(self):
        for инструмент, (команда, _) in ПИШУЩИЕ.items():
            if инструмент == "manifest.py":
                continue  # у требований своё заведение, оно ниже
            with self.subTest(инструмент=инструмент):
                f = self.dir / f"new-{инструмент}.json"
                r = _пуск(инструмент, *команда(str(f)))
                self.assertEqual(r.returncode, 0, r.stderr[-400:])
                self.assertTrue(f.is_file())
                self.assertEqual(self._копии(f), [])

    def test_a_missing_manifest_still_starts_clean(self):
        бриф = self.dir / "brief.md"
        бриф.write_text("хочу сайт\n", encoding="utf-8")
        f = self.dir / "manifest.json"
        r = _пуск("manifest.py", "init", str(f), str(бриф))
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertEqual(self._копии(f), [])

    def test_a_changed_broken_file_gets_its_own_copy(self):
        f = self.dir / "state.json"
        f.write_bytes(БИТОЕ)
        команда, _ = ПИШУЩИЕ["progress.py"]
        _пуск("progress.py", *команда(str(f)))
        f.write_bytes(b'{"waves": ')
        _пуск("progress.py", *команда(str(f)))
        self.assertEqual(len(self._копии(f)), 2)

    def test_a_file_that_is_not_a_set_of_fields_is_broken_too(self):
        f = self.dir / "state.json"
        f.write_bytes(b"[]")
        команда, _ = ПИШУЩИЕ["progress.py"]
        r = _пуск("progress.py", *команда(str(f)))
        self.assertEqual(r.returncode, КОД_ПОВРЕЖДЁН, r.stderr[-400:])
        self.assertEqual(f.read_bytes(), b"[]")

    def test_explicit_fresh_start_over_a_broken_file_still_works(self):
        """«Начнём заново» по повреждённому файлу не упирается в стену:
        копия уже лежит рядом, и стирать больше нечего."""
        for инструмент in ("progress.py", "manifest.py"):
            with self.subTest(инструмент=инструмент):
                f = self.dir / f"fresh-{инструмент}.json"
                f.write_bytes(БИТОЕ)
                if инструмент == "progress.py":
                    args = ["init", str(f), "проект", "--fresh"]
                else:
                    бриф = self.dir / "brief.md"
                    бриф.write_text("хочу сайт\n", encoding="utf-8")
                    args = ["init", str(f), str(бриф), "--fresh"]
                r = _пуск(инструмент, *args)
                self.assertEqual(r.returncode, 0, r.stderr[-400:])
                self.assertTrue(any(
                    (self.dir / c).read_bytes() == БИТОЕ for c in self._копии(f)))

    def test_the_stop_code_is_named_once_in_the_shared_module(self):
        self.assertEqual(_модуль("atomic_store").ПОВРЕЖДЁН, КОД_ПОВРЕЖДЁН)


class TestReadersDoNotCrash(unittest.TestCase):
    """Тем, кто только читает, нечего беречь — но и падать им нельзя."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / ".superstack"
        self.run.mkdir()
        (self.run / "state.json").write_bytes(БИТОЕ)
        (self.run / "manifest.json").write_bytes(БИТОЕ)

    def test_report_on_a_broken_run_does_not_raise(self):
        rp = _модуль("report")
        rp.human(rp.gather(self.run))

    def test_panel_on_a_broken_run_does_not_raise(self):
        lp = _модуль("live_panel")
        lp.state_with_phase(self.run)
        self.assertTrue(lp.write_panel(self.run).is_file())

    def test_gates_name_the_broken_manifest_instead_of_reading_it_as_empty(self):
        f = self.run / "manifest.json"
        r = _пуск("gates.py", str(f), "--gate", "G1")
        self.assertEqual(r.returncode, КОД_ПОВРЕЖДЁН, r.stderr[-400:])
        self.assertIn(ФРАЗА, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(f.read_bytes(), БИТОЕ)

    def test_promote_stops_on_a_broken_state(self):
        f = self.run / "state.json"
        r = _пуск("state_fields.py", "promote", str(f))
        self.assertEqual(r.returncode, КОД_ПОВРЕЖДЁН, r.stderr[-400:])
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(f.read_bytes(), БИТОЕ)


class TestPhaseZeroStopsOnAnOccupiedStart(unittest.TestCase):
    """Три команды заведения шли одним блоком: отказ первой не останавливал
    вторую, и память затиралась после того, как требования отказали."""

    def setUp(self):
        self.текст = (PKG / "skills" / "go" / "phases" /
                      "0-завести.md").read_text("utf-8")

    def test_each_start_command_stops_the_block_on_refusal(self):
        for команда in ('tools/manifest.py" init', 'tools/memory_file.py" init'):
            with self.subTest(команда=команда):
                строка = next((s for s in self.текст.splitlines()
                               if команда in s), "")
                self.assertTrue(строка.rstrip().endswith("|| exit $?"),
                                f"отказ «{команда}» не останавливает блок: {строка}")

    def test_the_phase_names_code_four_of_memory_and_the_two_answers(self):
        self.assertIn("Код 4 у `memory_file.py`", self.текст)
        self.assertIn("«продолжим»", self.текст)
        self.assertIn("«начнём заново»", self.текст)


if __name__ == "__main__":
    unittest.main()
