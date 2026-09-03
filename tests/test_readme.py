#!/usr/bin/env python3
"""Витрину читает человек, который ещё ничего не поставил.

Три вещи сбивали его на первом экране. Обещание, что текст установки годится
любому агенту, — команды `claude plugin …` есть только в Claude Code, и тот,
кто попробовал в другом, получал отказ и читал его как поломку продукта.
Двадцать строк про снятие семи пакетов, которых у нового человека никогда не
было, стояли в основном потоке. И список из трёх скиллов там, где их семь, —
без тех двух, которые новичок зовёт первыми: «не работает» и «верни как было».

Витрин теперь две. Русская README.md — главная: её открывают первой.
Английская README.en.md — для тех, кто пришёл извне, и она полная, не огрызок.
Проверки ниже идут ПО ОБЕИМ: половина, из которой уехала суть, хуже
отсутствующей — на неё ведёт переключатель языка первой строкой, и человек
читает её как единственную.

Отдельно — строки-сводки с числами. Их переписывает `tools/sync_public.py` по
своей форме; разойдётся форма — витрина молча покажет числа прошлого релиза,
и это единственная поломка здесь, которую нельзя заметить глазом.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO  # noqa: E402

README_RU = (REPO / "README.md").read_text("utf-8")
README_EN = (REPO / "README.en.md").read_text("utf-8")

#: Ровно те формы, которые подставляет sync_public.refresh_readme.
СВОДКА_RU = re.compile(
    r"^\d+ тест\w* · \d+ мутаци\w*, все ловятся · \d+ механизм\w*\.", re.M)
СВОДКА_EN = re.compile(
    r"^\d+ tests · \d+ mutations, all caught · \d+ mechanisms\.", re.M)

#: Раздел про снятие семи пакетов — в каждой витрине своим заголовком.
СЕМЬ_ПАКЕТОВ = {"README.md": "Если ставили до версии 0.3.0",
                "README.en.md": "If you installed before version 0.3.0"}

ОБЕ = (("README.md", README_RU), ("README.en.md", README_EN))


class TestBothShowcasesPointAtEachOther(unittest.TestCase):
    """Переключатель языка — первой строкой, и он обязан вести в живой файл.

    Ссылка на отсутствующую половину хуже её отсутствия: на GitHub она
    выглядит рабочей и приводит на 404.
    """

    def test_the_russian_front_page_offers_english_in_its_first_line(self):
        первая = README_RU.splitlines()[0]
        self.assertIn("README.en.md", первая,
                      "первая строка главной витрины не ведёт на английскую — "
                      f"там сейчас: {первая!r}")

    def test_the_english_page_leads_back_in_its_first_line(self):
        первая = README_EN.splitlines()[0]
        self.assertIn("(README.md)", первая,
                      "с английской витрины нет дороги обратно на главную — "
                      f"первая строка: {первая!r}")


class TestReadmeDoesNotMisleadANewcomer(unittest.TestCase):

    def test_install_text_promises_claude_code_and_nothing_else(self):
        for имя, текст in ОБЕ:
            for чужой in ("Cursor", "Codex"):
                self.assertNotIn(
                    чужой, текст,
                    f"{имя} зовёт ставить плагин в {чужой}, где команд "
                    "`claude plugin …` нет — человек получит отказ и решит, "
                    "что сломан продукт")

    def test_the_seven_packages_section_is_out_of_the_main_flow(self):
        for имя, текст in ОБЕ:
            заголовок = СЕМЬ_ПАКЕТОВ[имя]
            self.assertIn(заголовок, текст,
                          f"{имя}: раздел про старую установку пропал целиком")
            self.assertLess(
                текст.index("<details>"), текст.index(заголовок),
                f"{имя}: снятие семи пакетов снова стоит в основном потоке — "
                "новый человек читает про то, чего у него никогда не было")

    def test_what_to_say_next_names_the_two_skills_a_newcomer_calls_first(self):
        for имя, текст in ОБЕ:
            основной_поток = текст[:текст.index("<details>")]
            for скилл in ("/fix", "/oops"):
                self.assertIn(
                    скилл, основной_поток,
                    f"{имя} не называет {скилл} в основном потоке — а именно "
                    "его зовут словами «не работает» и «верни как было»")

    def test_the_summary_lines_match_what_sync_public_rewrites(self):
        self.assertRegex(
            README_RU, СВОДКА_RU,
            "русской строки-сводки нет или её форма разошлась с подстановкой "
            "в tools/sync_public.py — выкладка перестанет обновлять числа на "
            "главной странице")
        self.assertRegex(
            README_EN, СВОДКА_EN,
            "английской строки-сводки нет или её форма разошлась с "
            "подстановкой в tools/sync_public.py — выкладка перестанет "
            "обновлять числа")


#: Четыре набора, которые витрина сравнивает по именам. Первая колонка —
#: наша: без неё таблица перестаёт быть сравнением и становится обзором чужого.
КОЛОНКИ = ("SUPERSTACK", "Superpowers", "Skills Matt Pocock")

#: Заголовок раздела про границы — в каждой витрине свой.
ГРАНИЦЫ = {"README.md": "## Чего SUPERSTACK не делает никогда",
           "README.en.md": "## What SUPERSTACK never does"}

#: Снимок панели — свой у каждой языковой версии, потому что панель двуязычная.
СНИМОК = {"README.md": "assets/panel-ru.jpg", "README.en.md": "assets/panel-en.jpg"}

#: Строка шапки таблицы сравнения: одна и та же форма в обеих витринах.
ШАПКА = re.compile(r"^\|[^\n]*\|$", re.M)


class TestTheShowcaseComparesItselfHonestly(unittest.TestCase):
    """Сравнение, границы и настоящий снимок — в обеих витринах.

    Человек выбирает между соседними наборами до установки, и выбирает он по
    той половине, которую открыл. Таблица, уехавшая из английской версии,
    оставляет пришедшего извне без единственного ответа на его вопрос.

    Про снимок отдельно: картинка витрины ломается тише всего. Файл не
    переехал вместе с текстом — на GitHub остаётся серый значок, и страница
    выглядит недоделанной ровно там, где обещает аккуратность.
    """

    def test_the_comparison_table_names_all_four_sets_in_both_showcases(self):
        for имя, текст in ОБЕ:
            шапки = [s for s in ШАПКА.findall(текст) if "SUPERSTACK" in s]
            self.assertTrue(
                шапки,
                f"{имя}: таблицы сравнения нет — человек выбирает между "
                "соседними наборами вслепую")
            шапка = шапки[0]
            для_нас = [k for k in КОЛОНКИ if k not in шапка]
            self.assertEqual(
                для_нас, [],
                f"{имя}: в шапке таблицы сравнения нет колонок {для_нас} — "
                f"сравнение неполное: {шапка!r}")

    def test_the_never_does_section_is_there_in_both_showcases(self):
        for имя, текст in ОБЕ:
            self.assertIn(
                ГРАНИЦЫ[имя], текст,
                f"{имя}: раздел про то, чего система не делает никогда, "
                "пропал — обещания остались, границы нет")

    def test_the_panel_screenshot_points_at_a_file_that_exists(self):
        for имя, текст in ОБЕ:
            путь = СНИМОК[имя]
            self.assertIn(путь, текст,
                          f"{имя}: снимка панели нет — раздел «как устроено» "
                          "рассказывает про страницу, которую не показывает")
            self.assertTrue(
                (REPO / путь).is_file(),
                f"{имя} ссылается на {путь}, а файла нет — на GitHub это "
                "серый значок вместо картинки, и заметит его читатель, а не мы")


if __name__ == "__main__":
    unittest.main()
