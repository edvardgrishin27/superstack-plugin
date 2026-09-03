#!/usr/bin/env python3
"""Английский режим всего, что читает человек.

Продукт объясняется человеку по-русски: отчёт, ответ на «где я», подписи
панели, отказы инструментов. Человек, поставивший плагин с английской
страницы, доводил стройку до конца и получал итог на языке автора — то есть
продукт, которым нельзя пользоваться, не зная русского.

Здесь заперто три вещи, и каждая — поведение, а не наличие файла:

  · язык лежит ЗАПИСЬЮ в состоянии прогона, и её читают инструменты. Не
    договорённость с моделью «отвечай на языке человека»: почти весь текст,
    который человек читает, собирают инструменты, а не модель;
  · русское умолчание. Прогон, заведённый до этой записи, не имеет права
    заговорить иначе на середине;
  · жаргон-проверка знает английский СВОИМ списком и отвечает тем же кодом
    возврата. Список без кода возврата — это правило, которое не нарушается.
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
СРЕДА = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}


def _модуль(имя: str, файл: Path):
    s = importlib.util.spec_from_file_location(имя, файл)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


lang = _модуль("ss_lang", TOOLS / "lang.py")
rp = _модуль("ss_report_lang", TOOLS / "report.py")


def _прогон(корень: Path, язык: "str | None") -> Path:
    """Папка прогона с одной доказанной частью работы и записанным языком."""
    run = корень / ".superstack"
    run.mkdir(parents=True, exist_ok=True)
    state = {"waves": {"1": [{"id": "01", "name": "landing",
                              "status": "proven"}]},
             "debt": {}, "phase": {"name": "Отчёт", "owner": "человек",
                                   "detail": "смотри итог"}}
    if язык is not None:
        state["lang"] = язык
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (run / "manifest.json").write_text(
        json.dumps({"requirements": [], "blind": {}}), encoding="utf-8")
    return run


class TestTheRecordedLanguageReachesTheHuman(unittest.TestCase):
    """Одна запись в состоянии — и весь текст для человека меняет язык.

    Проверяются РАЗНЫЕ инструменты (отчёт и ответ на «где я»): язык, который
    работает в одном месте и не работает в соседнем, хуже отсутствующего —
    человек видит половину продукта на своём языке и решает, что вторая
    половина сломана.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_english_is_written_once_and_the_report_follows(self):
        run = _прогон(self.root, "en")
        текст = rp.human(rp.gather(run))
        self.assertIn("HOW TO OPEN", текст)
        self.assertIn("WHAT IS READY", текст)
        self.assertNotIn("КАК ОТКРЫТЬ", текст)

    def test_the_same_record_turns_the_where_am_i_answer(self):
        run = _прогон(self.root, "en")
        r = subprocess.run([sys.executable, str(TOOLS / "what.py"),
                            str(self.root)], capture_output=True, text=True,
                           timeout=60, env=СРЕДА)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        строка = json.loads(r.stdout)["line"]
        self.assertIn("We stopped at the step", строка)
        self.assertIn("Report", строка, "название этапа осталось русским")
        self.assertTrue(run.is_dir())

    def test_without_the_record_everything_stays_russian(self):
        """Умолчание — не догадка о человеке, а прежнее поведение.

        Проект, заведённый до этой записи, обязан говорить ровно так же, как
        говорил вчера: смена языка на середине прогона выглядит поломкой.
        """
        run = _прогон(self.root, None)
        self.assertEqual(lang.read(run), "ru")
        self.assertEqual(lang.read(self.root), "ru")
        self.assertIn("КАК ОТКРЫТЬ", rp.human(rp.gather(run)))

    def test_an_unknown_language_is_not_taken_at_its_word(self):
        """`lang: "de"` — это опечатка, а не немецкий.

        Говорить с человеком на языке, надписей для которого нет, значит
        показать ему пустые строки там, где он ждёт текст.
        """
        run = _прогон(self.root, "de")
        self.assertEqual(lang.read(run), "ru")

    def test_the_first_phrase_decides_the_language(self):
        self.assertEqual(lang.detect("сделай мне сайт про кофе"), "ru")
        self.assertEqual(lang.detect("build me a site about coffee"), "en")


class TestTheJargonCheckSpeaksEnglishToo(unittest.TestCase):
    """Тот же код возврата, другой список.

    Английский жаргон не переводится из русского списка: слово попадает туда
    по тому же правилу — у него ЕСТЬ обычная замена, и человек без опыта в
    коде его не поймёт.
    """

    def _check(self, текст: str, *флаги: str):
        return subprocess.run(
            [sys.executable, str(TOOLS / "plain_ru.py"), "check", *флаги, "-"],
            input=текст, capture_output=True, text=True, timeout=60, env=СРЕДА)

    def test_english_jargon_is_a_finding(self):
        r = self._check("The gate blocked the deploy and the hook printed "
                        "a stack trace.", "--lang", "en")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for слово in ("gate", "deploy", "hook", "stack trace"):
            with self.subTest(слово=слово):
                self.assertIn(слово, r.stderr)

    def test_plain_english_is_left_alone(self):
        r = self._check("The site is ready and there is nothing left for you "
                        "to do.", "--lang", "en")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_free_tail_does_not_turn_the_report_into_a_finding(self):
        """«repo» внутри «report» — не находка.

        Русский хвост свободный, потому что язык склоняет. Тот же приём в
        английском заставил бы проверку ругаться на собственный отчёт, и её
        перестали бы читать целиком.
        """
        r = self._check("The report is ready.", "--lang", "en")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_unknown_language_is_refused_not_silently_skipped(self):
        """Молча проверить русским списком английский текст и вернуть ноль —
        это «проверил» вместо «не проверял»."""
        r = self._check("The gate blocked the deploy.", "--lang", "de")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
