#!/usr/bin/env python3
"""Витрину читает человек, который ещё ничего не поставил.

Три вещи сбивали его на первом экране. Обещание, что текст установки годится
любому агенту, — команды `claude plugin …` есть только в Claude Code, и тот,
кто попробовал в другом, получал отказ и читал его как поломку продукта.
Двадцать строк про снятие семи пакетов, которых у нового человека никогда не
было, стояли в основном потоке. И список из трёх скиллов там, где их семь, —
без тех двух, которые новичок зовёт первыми: «не работает» и «верни как было».

Витрин теперь две. Английская — главная: репозиторий открыт всему миру, и
русская первая страница отсекала читателя раньше, чем он доходил до сути.
Русская — полная, не огрызок. Проверки ниже идут ПО ОБЕИМ: половина, из
которой уехала суть, хуже отсутствующей — на неё ведёт переключатель языка
первой строкой, и человек читает её как единственную.

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

README = (REPO / "README.md").read_text("utf-8")
README_RU = (REPO / "README.ru.md").read_text("utf-8")

#: Ровно те формы, которые подставляет sync_public.refresh_readme.
СВОДКА_RU = re.compile(
    r"^\d+ тест\w* · \d+ мутаци\w*, все ловятся · \d+ механизм\w*\.", re.M)
СВОДКА_EN = re.compile(
    r"^\d+ tests · \d+ mutations, all caught · \d+ mechanisms\.", re.M)

#: Раздел про снятие семи пакетов — в каждой витрине своим заголовком.
СЕМЬ_ПАКЕТОВ = {"README.md": "If you installed before version 0.3.0",
                "README.ru.md": "Если ставили до версии 0.3.0"}

ОБЕ = (("README.md", README), ("README.ru.md", README_RU))


class TestBothShowcasesPointAtEachOther(unittest.TestCase):
    """Переключатель языка — первой строкой, и он обязан вести в живой файл.

    Ссылка на отсутствующую половину хуже её отсутствия: на GitHub она
    выглядит рабочей и приводит на 404.
    """

    def test_the_english_front_page_offers_russian_in_its_first_line(self):
        первая = README.splitlines()[0]
        self.assertIn("README.ru.md", первая,
                      "первая строка главной витрины не ведёт на русскую — "
                      f"там сейчас: {первая!r}")

    def test_the_russian_page_leads_back_in_its_first_line(self):
        первая = README_RU.splitlines()[0]
        self.assertIn("README.md", первая,
                      "с русской витрины нет дороги обратно на главную — "
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
            README, СВОДКА_EN,
            "английской строки-сводки нет или её форма разошлась с "
            "подстановкой в tools/sync_public.py — выкладка перестанет "
            "обновлять числа на главной странице")
        self.assertRegex(
            README_RU, СВОДКА_RU,
            "русской строки-сводки нет или её форма разошлась с подстановкой "
            "в tools/sync_public.py — выкладка перестанет обновлять числа")


if __name__ == "__main__":
    unittest.main()
