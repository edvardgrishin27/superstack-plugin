#!/usr/bin/env python3
"""Витрину читает человек, который ещё ничего не поставил.

Три вещи сбивали его на первом экране. Обещание, что текст установки годится
любому агенту, — команды `claude plugin …` есть только в Claude Code, и тот,
кто попробовал в другом, получал отказ и читал его как поломку продукта.
Двадцать строк про снятие семи пакетов, которых у нового человека никогда не
было, стояли в основном потоке. И список из трёх скиллов там, где их шесть, —
без тех двух, которые новичок зовёт первыми: «не работает» и «верни как было».

Отдельно — строка-сводка с числами. Её переписывает `tools/sync_public.py`
по своей форме; разойдётся форма — витрина молча покажет числа прошлого
релиза, и это единственная поломка здесь, которую нельзя заметить глазом.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO  # noqa: E402

README = (REPO / "README.md").read_text("utf-8")

#: Ровно та форма, которую подставляет sync_public.refresh_readme.
СВОДКА = re.compile(
    r"^\d+ тест\w* · \d+ мутаци\w*, все ловятся · \d+ механизм\w*\.", re.M)


class TestReadmeDoesNotMisleadANewcomer(unittest.TestCase):

    def test_install_text_promises_claude_code_and_nothing_else(self):
        for чужой in ("Cursor", "Codex"):
            self.assertNotIn(
                чужой, README,
                f"витрина зовёт ставить плагин в {чужой}, где команд "
                "`claude plugin …` нет — человек получит отказ и решит, "
                "что сломан продукт")

    def test_the_seven_packages_section_is_out_of_the_main_flow(self):
        self.assertIn("Если ставили до версии 0.3.0", README,
                      "раздел про старую установку пропал целиком")
        self.assertLess(
            README.index("<details>"),
            README.index("Если ставили до версии 0.3.0"),
            "снятие семи пакетов снова стоит в основном потоке — новый "
            "человек читает про то, чего у него никогда не было")

    def test_what_to_say_next_names_the_two_skills_a_newcomer_calls_first(self):
        глава = README[README.index("## Что сказать дальше"):
                       README.index("<details>")]
        for имя in ("/fix", "/oops"):
            self.assertIn(имя, глава,
                          f"«Что сказать дальше» не называет {имя} — а именно "
                          "его зовут словами «не работает» и «верни как было»")

    def test_the_summary_line_matches_what_sync_public_rewrites(self):
        self.assertRegex(
            README, СВОДКА,
            "строки-сводки нет или её форма разошлась с подстановкой в "
            "tools/sync_public.py — выкладка перестанет обновлять числа")
