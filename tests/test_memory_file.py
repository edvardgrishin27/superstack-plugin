#!/usr/bin/env python3
"""Память проекта: детекция, маркеры, и чужой текст, который нельзя стереть.

Три механизма, и второй — тот, где цена ошибки не измеряется временем.

В `CLAUDE.md` живого репозитория лежат правила, выстраданные командой.
Инструмент, который перезаписывает файл целиком, стирает их один раз, тихо, и
замечают это нескоро. У AutoPilot про это сказано «всё вне маркеров
неприкосновенно» — и держится это на том, что модель прочитает фразу. Здесь
байты снаружи сверяются отпечатком до и после записи, а запись, изменившая их,
не считается состоявшейся.

Третий механизм проверил сам себя на первом же прогоне: скелет обещал
`dashboard.html`, которого в момент записи ещё нет, — и проверка покраснела на
собственном шаблоне. Правило «файл документирует то, что ЕСТЬ» ловит своего
автора так же, как чужого.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from paths import at

MF = at("tools", "memory_file.py")
_s = importlib.util.spec_from_file_location("superstack_memory_file", MF)
mf = importlib.util.module_from_spec(_s)
_s.loader.exec_module(mf)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


class TestDetectionNeverAsksTheHuman(Base):
    """Какой файл — процессное решение, а не продуктовое. Спрашивать о нём
    значит тратить время человека на то, что видно из репозитория."""

    def test_existing_claude_wins(self):
        (self.root / "CLAUDE.md").write_text("x", encoding="utf-8")
        (self.root / ".cursor").mkdir()
        self.assertEqual(mf.detect(self.root, {})["file"], "CLAUDE.md")

    def test_existing_agents_wins(self):
        (self.root / "AGENTS.md").write_text("x", encoding="utf-8")
        (self.root / ".claude").mkdir()
        self.assertEqual(mf.detect(self.root, {})["file"], "AGENTS.md")

    def test_both_exist_takes_the_one_that_already_holds_the_block(self):
        (self.root / "CLAUDE.md").write_text("пусто", encoding="utf-8")
        (self.root / "AGENTS.md").write_text(f"{mf.START}\nописание\n{mf.END}",
                                             encoding="utf-8")
        self.assertEqual(mf.detect(self.root, {})["file"], "AGENTS.md")

    def test_both_exist_without_a_block_leaves_the_other_alone(self):
        (self.root / "CLAUDE.md").write_text("a", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("b", encoding="utf-8")
        d = mf.detect(self.root, {})
        self.assertEqual(d["file"], "AGENTS.md")
        self.assertIsNone(d["pointer"])

    def test_claude_directory_points_at_claude(self):
        (self.root / ".claude").mkdir()
        self.assertEqual(mf.detect(self.root, {})["file"], "CLAUDE.md")

    def test_environment_marks_claude_code(self):
        self.assertEqual(mf.detect(self.root, {"CLAUDECODE": "1"})["file"],
                         "CLAUDE.md")

    def test_cursor_and_codex_point_at_agents(self):
        for d, want in ((".cursor", "AGENTS.md"), (".codex", "AGENTS.md")):
            with self.subTest(dir=d):
                r = Path(tempfile.mkdtemp(dir=self.root))
                (r / d).mkdir()
                self.assertEqual(mf.detect(r, {})["file"], want)

    def test_unrecognised_agent_gets_a_pointer(self):
        d = mf.detect(self.root, {})
        self.assertEqual((d["file"], d["pointer"]), ("AGENTS.md", "CLAUDE.md"))

    def test_recognised_agent_gets_no_pointer(self):
        """При опознанном агенте второй файл — второе место для рассинхрона,
        и оно рассинхронится."""
        (self.root / ".claude").mkdir()
        self.assertIsNone(mf.detect(self.root, {})["pointer"])


class TestForeignTextSurvivesByteForByte(Base):
    """Правила, выстраданные командой, стираются один раз и тихо."""

    def _round_trip(self, original: str) -> str:
        p = self.root / "CLAUDE.md"
        p.write_text(original, encoding="utf-8")
        mf.write_block(p, "первое описание")
        mf.write_block(p, "переписанное описание")
        parts = mf.split(p.read_text("utf-8"))
        return parts[0] + parts[2]

    def test_plain_user_text_is_preserved(self):
        original = "# Правила команды\n\nНикогда не коммить в main напрямую.\n"
        self.assertIn("Никогда не коммить в main напрямую.",
                      self._round_trip(original))

    def test_trailing_whitespace_and_unicode_are_preserved(self):
        """Проверка байт-в-байт, а не «похоже»: съеденный пробел в конце строки
        — уже чужая правка, и человек будет искать её в своём дифе."""
        original = "правило с хвостом   \n\nи ещё — тире, «кавычки», ёж\t\n"
        p = self.root / "CLAUDE.md"
        p.write_text(original, encoding="utf-8")
        mf.write_block(p, "описание")
        before, _, after = mf.split(p.read_text("utf-8"))
        self.assertEqual((before + after).rstrip("\n"), original.rstrip("\n"))

    def test_text_that_looks_like_a_marker_is_not_mistaken_for_one(self):
        """Человек мог написать про маркеры в своей же документации."""
        original = f"Мы используем метку вида {mf.START[:-4]}... не трогай.\n"
        kept = self._round_trip(original)
        self.assertIn("не трогай", kept)

    def test_second_write_replaces_only_our_block(self):
        p = self.root / "CLAUDE.md"
        p.write_text("хвост человека\n", encoding="utf-8")
        mf.write_block(p, "первое")
        mf.write_block(p, "второе")
        t = p.read_text("utf-8")
        self.assertIn("второе", t)
        self.assertNotIn("первое", t)
        self.assertIn("хвост человека", t)
        self.assertEqual(t.count(mf.START), 1)

    def test_file_without_markers_gains_them_without_losing_content(self):
        p = self.root / "AGENTS.md"
        p.write_text("старое описание без маркеров\n", encoding="utf-8")
        mf.write_block(p, "наше")
        t = p.read_text("utf-8")
        self.assertIn("старое описание без маркеров", t)
        self.assertIn("наше", t)


class TestBrokenMarkersAreRefused(Base):

    def test_end_before_start_is_not_a_pair(self):
        self.assertIsNone(mf.split(f"{mf.END}\nтело\n{mf.START}"))

    def test_two_starts_are_refused(self):
        self.assertIsNone(mf.split(f"{mf.START}\na\n{mf.START}\nb\n{mf.END}"))

    def test_check_names_broken_markers(self):
        (self.root / "CLAUDE.md").write_text("нет никаких маркеров\n",
                                             encoding="utf-8")
        v = mf.check(self.root, "CLAUDE.md")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("маркеры" in b for b in v["broken"]), v)


class TestTheFileDocumentsWhatExists(Base):
    """Файл памяти умирает не от старости, а от несовпадения: он выглядит
    источником правды и тихо расходится с кодом."""

    def _write(self, body: str):
        (self.root / "CLAUDE.md").write_text(
            f"{mf.START}\n{body}\n{mf.END}\n", encoding="utf-8")

    def test_nonexistent_path_is_red(self):
        self._write("Запуск описан в `docs/setup.md`, длинное описание проекта.")
        v = mf.check(self.root, "CLAUDE.md")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("не существует" in b for b in v["broken"]), v)

    def test_existing_path_is_clean(self):
        (self.root / "docs").mkdir()
        (self.root / "docs" / "setup.md").write_text("x", encoding="utf-8")
        self._write("Запуск описан в `docs/setup.md`, и это длинное описание "
                    "проекта, чтобы блок не считался пустым.")
        self.assertEqual(mf.check(self.root, "CLAUDE.md")["status"], "pass")

    def test_the_skeleton_promises_only_what_init_creates(self):
        """Первый же прогон покраснел на собственном шаблоне: он обещал
        `dashboard.html`, которого в момент записи ещё нет. Правило ловит
        своего автора так же, как чужого."""
        mf.write_block(self.root / "CLAUDE.md",
                       mf.SKELETON.format(project="Проект", about="о нём"))
        for sub in mf.SKELETON_DIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.assertEqual(mf.check(self.root, "CLAUDE.md")["status"], "pass")

    def test_secret_shaped_value_is_red(self):
        self._write("Ключ `docs/` тут: sk-abcdefghijklmnopqrstuvwx, "
                    "и ещё длинный текст описания проекта для объёма.")
        (self.root / "docs").mkdir()
        v = mf.check(self.root, "CLAUDE.md")
        self.assertTrue(any("значение ключа" in b for b in v["broken"]), v)

    def test_missing_file_is_unknown_not_pass(self):
        v = mf.check(self.root, "CLAUDE.md")
        self.assertEqual(v["status"], "unknown")

    def test_near_empty_block_is_unmeasured(self):
        self._write("коротко")
        v = mf.check(self.root, "CLAUDE.md")
        self.assertEqual(v["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
