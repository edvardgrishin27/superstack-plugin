#!/usr/bin/env python3
"""Человеческий русский на всём, что видит человек.

Почему это отдельный набор, а не вычитка.

Жаргон нельзя заметить самому: чтобы увидеть непонятное слово, нужно перестать
его понимать. Строка «таск 06 — страница по принесённой системе; я жду
возврата» перечитывалась автором как совершенно ясная. Человек, который не
пишет код, не понял в ней НИ ОДНОГО слова и сказал об этом трижды за один час —
про подпись этапа, про держателя хода и про сноску у полосы.

Отсюда конструкция: список слов лежит данными, поиск делает машина, находка —
код возврата. И отдельно заперто то, что чинит причину, а не случай: каждый
держатель хода и каждый этап ОБЯЗАНЫ иметь человеческое пояснение, иначе
следующий добавленный этап приедет на языке автора и никто этого не заметит.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

TOOL = PKG / "tools" / "plain_ru.py"
_s = importlib.util.spec_from_file_location("ss_plain_ru", TOOL)
ru = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ru)

_p = importlib.util.spec_from_file_location(
    "ss_progress_ru", PKG / "tools" / "progress.py")
pr = importlib.util.module_from_spec(_p)
_p.loader.exec_module(pr)

_l = importlib.util.spec_from_file_location(
    "ss_live_panel_ru", PKG / "tools" / "live_panel.py")
lp = importlib.util.module_from_spec(_l)
_l.loader.exec_module(lp)


class TestJargonIsFoundInAnyForm(unittest.TestCase):
    """Русский склоняет. Поиск по точной форме был бы зелёным и бесполезным:
    живые вхождения почти всегда стоят в падеже — «таском», «таски», «деплоя»."""

    def test_the_exact_word_is_found(self):
        self.assertTrue(ru.find_jargon("таск 06 готов"))

    def test_declined_forms_are_found_too(self):
        for form in ("таском", "таски", "таска", "деплоя", "коммиты"):
            with self.subTest(form=form):
                self.assertTrue(ru.find_jargon(f"жду {form} сегодня"),
                                f"«{form}» прошло мимо словаря")

    def test_plain_russian_is_left_alone(self):
        clean = "помощник переделывает страницу под твою дизайн-систему"
        self.assertEqual(ru.find_jargon(clean), [])

    def test_every_word_has_a_replacement(self):
        """Слово без замены — это запрет без выхода: автор всё равно напишет
        его, потому что сказать иначе нечем."""
        for stem, better in ru.load()["jargon"]["words"].items():
            with self.subTest(word=stem):
                self.assertTrue(better.strip(), f"«{stem}» нечем заменить")
                self.assertNotIn(stem, better.lower(),
                                 f"замена для «{stem}» повторяет само слово")


class TestEveryVisibleThingHasHumanWords(unittest.TestCase):
    """Причина, а не случай.

    Панель показывает роль и этап из состояния. Стоит добавить восьмую роль или
    одиннадцатый этап — и на экран приедет служебное слово, а заметит это
    человек, а не набор. Поэтому полнота словаря проверяется здесь.
    """

    def test_every_holder_of_the_turn_is_translated(self):
        words = ru.load()["roles"]["map"]
        for owner in pr.OWNERS:
            with self.subTest(owner=owner):
                self.assertIn(owner, words, f"«{owner}» нечем назвать человеку")
                self.assertTrue(words[owner].strip())

    def test_every_phase_has_a_plain_explanation(self):
        words = ru.load()["phases"]["map"]
        for phase in lp.PHASES:
            with self.subTest(phase=phase):
                self.assertIn(phase, words,
                              f"этап «{phase}» ничего не объясняет человеку")
                self.assertGreater(len(words[phase]), 20,
                                   "пояснение короче, чем название")

    def test_every_task_status_is_translated(self):
        words = ru.load()["statuses"]["map"]
        for status in pr.TASK_STATES:
            with self.subTest(status=status):
                self.assertIn(status, words)

    def test_the_dictionary_itself_speaks_plainly(self):
        """Словарь, объясняющий жаргон жаргоном, не помогает никому."""
        for key, text in ru.load()["copy"]["map"].items():
            with self.subTest(key=key):
                self.assertEqual(ru.find_jargon(text), [],
                                 f"в надписи «{key}» остался жаргон")


class TestTheCheckIsAnExitCode(unittest.TestCase):
    """Правило без принуждения держится до следующей строки, написанной в
    спешке. Проверено на себе: правило было записано и нарушено в тот же час."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "текст.md"

    def _run(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_jargon_returns_one(self):
        self.f.write_text("деплой таска после мёржа", encoding="utf-8")
        r = self._run("check", str(self.f))
        self.assertEqual(r.returncode, 1)
        self.assertIn("задача", r.stderr, "не сказано, чем заменить")

    def test_clean_text_returns_zero(self):
        self.f.write_text("помощник дописал страницу, жду ответа",
                          encoding="utf-8")
        self.assertEqual(self._run("check", str(self.f)).returncode, 0)

    def test_an_unknown_command_is_refused(self):
        self.assertEqual(self._run("почини", "всё").returncode, 3)


class TestThePhaseLabelCannotCarryJargon(unittest.TestCase):
    """Подпись этапа пишет модель, а читает человек. Отказ стоит одной правки;
    пропущенная подпись живёт на экране весь прогон и врёт молча."""

    def test_a_jargon_detail_is_refused(self):
        with self.assertRaises(ValueError) as e:
            pr.set_phase(dict(pr.EMPTY), "Пишем код", "исполнитель",
                         "таск 07 ждёт деплоя")
        self.assertIn("таск", str(e.exception))

    def test_a_plain_detail_passes(self):
        d = pr.set_phase(dict(pr.EMPTY), "Пишем код", "исполнитель",
                         "помощник дописывает страницу")
        self.assertEqual(d["phase"]["owner"], "исполнитель")


class TestTheSkillKeepsItPlain(unittest.TestCase):
    """Правило, которого нет в скилле, не действует: прогон ведёт он, а не
    память автора между сессиями."""

    def setUp(self):
        from paths import REPO
        self.t = (PKG / "skills" / "go"
                  / "SKILL.md").read_text("utf-8")

    def test_the_skill_names_the_plain_russian_check(self):
        self.assertIn("plain_ru.py", self.t)

    def test_the_skill_lists_the_real_phase_names(self):
        """Выдуманное название этапа не совпадёт ни с одним в панели, и работа
        будет идти «неизвестно где» — подсветки не будет вовсе."""
        for phase in lp.PHASES:
            with self.subTest(phase=phase):
                self.assertIn(phase, self.t)

    def test_the_skill_serves_the_panel_without_caching(self):
        """Через обычный http.server браузер показывает прежнюю страницу, и
        человек смотрит на устаревшее, не зная об этом."""
        self.assertIn("live_panel.py", self.t)
        self.assertIn("--serve", self.t)
        self.assertNotIn("python3 -m http.server 8787", self.t)


if __name__ == "__main__":
    unittest.main()
