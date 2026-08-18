#!/usr/bin/env python3
"""Что проверить на безопасность — набором, а не по памяти.

Прогон сборки отвечает «работает ли построенное». Вопрос «что из
непостроенного нас убьёт» он не задаёт и не может: в коде видно написанное, а
дыра выглядит как чистое место. Пропущенная проверка владения объектом
неотличима от аккуратного эндпоинта.

Здесь заперты три вещи:

  · набор зависит от того, ЧТО построено — лендингу нечего проверять про роли,
    а SaaS без проверки владения отдаёт чужие данные в первый же день;
  · неизвестный тип продукта не подменяется «общим набором»: тихая подстановка
    выдала бы неполную проверку за полную;
  · отметка «проверено» требует ссылки на файл — без неё она звучит ровно так
    же, как непроверенное, и стоит столько же.
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

TOOL = PKG / "tools" / "security_pack.py"
_s = importlib.util.spec_from_file_location("ss_security_pack", TOOL)
sp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(sp)


class TestThePackMatchesTheProduct(unittest.TestCase):

    def test_a_landing_page_does_not_get_role_checks(self):
        ids = [c["id"] for c in sp.pack_for("сайт")]
        self.assertNotIn("roles", ids)
        self.assertIn("secrets", ids)

    def test_a_saas_gets_ownership_and_roles(self):
        ids = [c["id"] for c in sp.pack_for("saas")]
        for must in ("ownership", "roles", "rls", "auth-brute"):
            self.assertIn(must, ids, f"в наборе SaaS нет проверки {must}")

    def test_an_ai_seller_gets_prompt_injection(self):
        """Продукт, где чужой текст попадает в промпт модели, без этой проверки
        отдаёт управление тому, кто написал текст."""
        ids = [c["id"] for c in sp.pack_for("ии-продавец")]
        self.assertIn("prompt-injection", ids)
        self.assertIn("llm-cost", ids)

    def test_an_unknown_kind_is_refused_not_guessed(self):
        with self.assertRaises(ValueError) as e:
            sp.pack_for("что-нибудь")
        self.assertIn("есть", str(e.exception))

    def test_every_check_carries_a_ready_question(self):
        """Тема для размышления не проверка. Вопрос должен требовать ПОКАЗАТЬ
        место в коде: «посмотрел, всё хорошо» звучит как непроверенное."""
        for c in sp.load()["checks"]:
            with self.subTest(check=c["id"]):
                self.assertGreater(len(c["промпт"]), 60)
                self.assertTrue(c["что"].strip())

    def test_every_kind_references_real_checks(self):
        data = sp.load()
        known = {c["id"] for c in data["checks"]}
        for kind, ids in data["kinds"]["map"].items():
            with self.subTest(kind=kind):
                self.assertEqual(set(ids) - known, set(),
                                 f"набор «{kind}» ссылается на несуществующее")


class TestDoneNeedsEvidence(unittest.TestCase):

    def test_marking_done_without_a_place_is_refused(self):
        with self.assertRaises(ValueError):
            sp.mark_done({}, "secrets", "   ")

    def test_marking_done_records_where(self):
        st = sp.mark_done({}, "secrets", "functions/api/book.ts:12-20")
        self.assertIn("secrets", st["done"])

    def test_status_counts_what_is_left(self):
        st = sp.mark_done({}, "secrets", "везде")
        v = sp.status(st, "сайт")
        self.assertEqual(v["status"], "fail")
        self.assertIn("xss", v["left"])

    def test_all_done_passes(self):
        st = {}
        for c in sp.pack_for("сайт"):
            st = sp.mark_done(st, c["id"], "проверено там-то")
        self.assertEqual(sp.status(st, "сайт")["status"], "pass")


class TestLoadIsAskedSeparately(unittest.TestCase):
    """Нагрузка — не «выдержит ли», а «что ломается первым»: без замера первым
    обычно ломается не сервер, а внешний сервис с лимитом."""

    def test_the_load_question_asks_for_numbers(self):
        p = sp.load()["load"]["промпт"]
        self.assertIn("процентиле", p)
        self.assertIn("упёрлось первым", p)


class TestExitCodes(unittest.TestCase):

    def _run(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_unchecked_returns_one(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "sec.json"
            r = self._run("status", str(f), "--kind", "сайт")
            self.assertEqual(r.returncode, 1)

    def test_unknown_kind_returns_three(self):
        self.assertEqual(self._run("for", "неведомое").returncode, 3)


if __name__ == "__main__":
    unittest.main()


class TestTheCatalogIsFilteredByWhatExists(unittest.TestCase):
    """Тип продукта — слишком грубая ось.

    Разметка по типам дала лендингу 104 обязательные проверки, среди них 22 про
    вход и 18 про платежи, которых у него нет вовсе. Человек открывает такой
    набор, видит требования не про свой продукт и закрывает его целиком — а
    вместе с ним закрывает те проверки, которые ему были нужны.

    Вторая ось — что в продукте ЕСТЬ. Она называется одной фразой и отсекает
    темы сразу: нет входа — нет и вопроса о чужих объектах.
    """

    def test_a_landing_page_is_not_asked_about_logins(self):
        topics = dict(sp.topics_of("сайт", "обязательный", has=["перс-данные"]))
        self.assertNotIn("аутентификация и сессии", topics)
        self.assertNotIn("платежи и деньги", topics)

    def test_filtering_by_capability_shrinks_the_pack(self):
        wide = len(sp.pick("сайт", "обязательный"))
        narrow = len(sp.pick("сайт", "обязательный", has=["перс-данные"]))
        self.assertLess(narrow, wide / 2,
                        "отбор по возможностям почти ничего не отсёк — значит он не работает")

    def test_what_every_product_has_is_never_filtered_out(self):
        """Ввод, секреты, инфраструктура и скорость есть у любого продукта,
        даже у страницы из одного экрана."""
        topics = dict(sp.topics_of("сайт", "обязательный", has=[]))
        for always in ("ввод и вывод", "секреты и конфигурация",
                       "инфраструктура и поставка"):
            self.assertIn(always, topics, f"«{always}» отсеклась, а нужна всегда")

    def test_a_saas_with_everything_gets_the_hard_topics(self):
        full = ["вход", "платежи", "ии", "файлы", "фон", "объёмы", "роли", "перс-данные"]
        topics = dict(sp.topics_of("saas", "обязательный", has=full))
        for must in ("доступ к данным и права", "LLM и агенты", "платежи и деньги"):
            self.assertIn(must, topics)

    def test_an_unknown_capability_is_refused(self):
        with self.assertRaises(ValueError):
            sp.needed_topics(["телепатия"])

    def test_levels_are_ordered_and_named(self):
        for lvl in sp.LEVELS:
            self.assertIn(lvl, sp.catalog()["уровни"])

    def test_the_catalog_is_not_empty_and_carries_sources(self):
        c = sp.catalog()
        self.assertGreater(len(c["проверки"]), 400,
                           "каталог подозрительно мал — исследование не доехало")
        self.assertGreater(len(c["источники"]), 0)

    def test_every_check_knows_its_level_and_topic(self):
        """Пункт без уровня не попадёт ни в один набор и потеряется молча."""
        bad = [c["id"] for c in sp.catalog()["проверки"]
               if not c.get("уровень") or not c.get("тема")]
        self.assertEqual(bad, [], f"без темы или уровня: {bad[:5]}")
