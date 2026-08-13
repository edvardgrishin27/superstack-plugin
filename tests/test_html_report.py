#!/usr/bin/env python3
"""Тесты отчёта-страницы.

Поверхность другая, но три обязательства те же: охват печатается первым,
вывод эвристики отличим от измерения, значение секрета не попадает в вывод.
Плюс требование, которого у текстового рендера не было: страница обязана
быть самодостаточной — ни одного внешнего запроса.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at, plug  # noqa: E402

ROOT = REPO
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}

spec = importlib.util.spec_from_file_location("ss_html", at("tools", "render_html.py"))
rh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rh)


def finding(**kw) -> dict:
    base = {"id": "t.rule", "severity": "high", "class": "GATE", "verdict": "FIX",
            "headline": "заголовок", "plain": "простыми словами",
            "why": "зачем", "claim": "машинная формулировка",
            "evidence": {}, "provenance": {}, "rests_on_inference": False,
            "note": "", "rule_file": "rules/x.json"}
    base.update(kw)
    return base


def page(findings: list, coverage: dict | None = None, **extra) -> str:
    data = {"findings": findings, "rule_files": ["rules/core.rules.json"],
            "coverage": coverage or {"trustworthy": True, "rules_total": 22,
                                     "rules_skipped": 0}}
    data.update(extra)
    return rh.build(data)


class TestSelfContained(unittest.TestCase):
    def test_no_external_requests(self):
        """Строгая политика содержимого блокирует любой внешний хост."""
        html = page([finding()])
        self.assertEqual(re.findall(r'(?:src|href)=["\']https?://', html), [])
        self.assertNotIn("@import", html)

    def test_committed_to_one_look(self):
        """Дизайн-система Futura AI коммитится в чёрный, а не поддерживает две
        темы. Значит страница обязана ОБЪЯВИТЬ это, иначе браузер применит
        свои светлые умолчания к форм-элементам и полосам прокрутки."""
        html = page([finding()])
        self.assertIn('color-scheme" content="dark"', html)
        self.assertIn("color-scheme: dark", html)

    def test_no_colour_accents(self):
        """Цветные акценты в системе запрещены явно: тяжесть находки
        передаётся весом и прозрачностью, а не цветом."""
        html = page([finding(severity="critical"), finding(severity="low")])
        body = html[html.index("<style>"):html.index("</style>")]
        for banned in ("orange", "#FF6B00", "rgba(255, 107, 0", "rgba(255, 140, 0",
                       "blue", "green", "purple"):
            self.assertNotIn(banned, body, f"цветной акцент в палитре: {banned}")

    def test_glass_has_edge_light_and_lift(self):
        """Три признака настоящего стекла: свет на верхнем ребре, тонкая
        внутренняя рамка и настоящая тень. Без них это размытая коробка."""
        html = page([finding()])
        self.assertIn("inset 0 1px 0 rgba(255, 255, 255", html)
        self.assertIn("inset 0 0 0 1px rgba(255, 255, 255", html)
        self.assertIn("rgba(0, 0, 0, .45)", html)

    def test_refraction_is_used_once_not_per_card(self):
        """Каждый преломляющий слой дорог: список из тринадцати задёргается.
        Повторяющиеся карточки идут на frost, преломление — одна поверхность."""
        html = page([finding(), finding(), finding()],
                    {"trustworthy": False, "probe_errors": 1})
        self.assertEqual(html.count("@supports (backdrop-filter: url(#glass-refraction))"), 1)
        self.assertNotIn("url(#glass-refraction) blur(14px)", html)

    def test_refraction_does_not_kill_blur_elsewhere(self):
        """url() в одном значении с blur заставляет Safari и Firefox выбросить
        ВСЁ объявление, включая размытие. Поэтому оно отдельным правилом."""
        html = page([finding()])
        base = html[html.index(".warn {"):html.index("@supports")]
        self.assertIn("backdrop-filter: blur(20px)", base)
        self.assertNotIn("url(#glass-refraction)", base)

    def test_brand_mark_on_the_page(self):
        html = page([finding()])
        self.assertIn("FuturaAI", html)
        self.assertIn('class="brand-mark"', html)
        self.assertIn("FuturaAI", html[html.index("<title>"):html.index("</title>")])

    def test_semantic_structure(self):
        html = page([finding()])
        for tag in ("<header>", "<main>", "<footer>", "<h1>"):
            self.assertIn(tag, html)


class TestCoverageComesFirst(unittest.TestCase):
    def test_warning_precedes_findings(self):
        html = page([finding()], {"trustworthy": False, "rules_skipped": 4,
                                  "rules_total": 22})
        self.assertIn("Этот отчёт неполный", html)
        self.assertLess(html.index("Этот отчёт неполный"),
                        html.index('class="finding"'),
                        "предупреждение о неполноте стоит ПОСЛЕ находок")

    def test_every_gap_kind_is_named(self):
        html = page([], {"trustworthy": False, "rules_skipped": 1, "rules_total": 22,
                         "files_broken": 2, "probe_errors": 3,
                         "malformed_facts": 4, "scopes_unmeasured": 5})
        for text in ("не отработало проверок", "битых файлов", "упавших проб",
                     "фактов без значения", "не удалось заглянуть"):
            self.assertIn(text, html)

    def test_clean_run_has_no_warning(self):
        """Предупреждение, которое висит всегда, перестают читать."""
        self.assertNotIn("Этот отчёт неполный", page([finding()]))

    def test_empty_but_untrustworthy_does_not_say_all_clear(self):
        html = page([], {"trustworthy": False, "probe_errors": 1})
        self.assertNotIn("Ничего, что требовало бы вмешательства", html)
        self.assertIn("проверки выполнились не все", html)

    def test_empty_and_clean_says_all_clear(self):
        self.assertIn("Ничего, что требовало бы вмешательства", page([]))


class TestInferenceIsVisible(unittest.TestCase):
    def test_heuristic_finding_is_marked(self):
        html = page([finding(rests_on_inference=True)])
        self.assertIn("вывод эвристики", html)

    def test_measured_finding_is_not_marked(self):
        self.assertNotIn("вывод эвристики", page([finding()]))

    def test_per_fact_provenance_reaches_the_page(self):
        html = page([finding(evidence={"a.b": 1, "c.d": 2},
                             provenance={"a.b": "INFERRED", "c.d": "EXTRACTED"})])
        self.assertIn("← вывод эвристики", html)


class TestNothingLeaksIntoThePage(unittest.TestCase):
    def test_markup_in_values_is_escaped(self):
        html = page([finding(headline="<script>alert(1)</script>")])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_secret_shaped_value_is_not_invented(self):
        """Страница показывает то, что дали факты, и ничего сверх."""
        html = page([finding(evidence={"sec.secret_matches": [
            {"location": "permissions.allow[1]", "why": "пароль прямо в команде",
             "fingerprint": "aabbccddeeff", "value_len": 40}]},
            provenance={"sec.secret_matches": "INFERRED"})])
        self.assertIn("permissions.allow[1]", html)
        self.assertIn("aabbccddeeff", html)
        self.assertNotIn("sshpass -p", html)


class TestWhatComesNext(unittest.TestCase):
    """Блок «что дальше»: отчёт не имеет права заканчиваться диагнозом.

    Данные для продолжения лежат в поле class каждой находки. Значит блок
    обязан считать ИЗ них, а не сочинять: сколько решает человек, сколько
    пойдёт в план, сколько система сделает сама.
    """

    #: числа блока живут только в <b>…</b> — по ним и проверяется «ни одного
    #: числа, которого нет в данных»
    NUM = re.compile(r"<b>(\d+)</b>")

    @staticmethod
    def _text(block: str) -> str:
        """Видимый человеком текст. Цифры ищутся именно в нём: в разметке
        цифра есть всегда (h2), и проверка по сырому HTML ловила бы её."""
        return re.sub(r"<[^>]+>", " ", block)

    def _phrase(self, block: str) -> str:
        m = re.search(r'class="say-phrase">([^<]+)<', block)
        self.assertIsNotNone(m, "фразы продолжения нет вовсе")
        return m.group(1)

    def test_counts_and_order_come_from_the_data(self):
        """Ожидаемое посчитано руками по списку ниже, а не взято из кода:
        три BLOCK, два GATE, один AUTO — и именно в этом порядке, потому что
        сначала идёт то, без чего система не сдвинется."""
        block = rh.next_block({"findings": [
            finding(**{"class": "GATE"}), finding(**{"class": "AUTO"}),
            finding(**{"class": "BLOCK"}), finding(**{"class": "GATE"}),
            finding(**{"class": "BLOCK"}), finding(**{"class": "BLOCK"}),
        ]})
        self.assertEqual([int(x) for x in self.NUM.findall(block)], [3, 2, 1])
        # и ни одного числа помимо этих: всё, что видит человек, — из данных
        self.assertEqual(re.findall(r"\d+", self._text(block)), ["3", "2", "1"])
        self.assertLess(block.index("Решаешь только ты"), block.index("Покажу в плане"))
        self.assertLess(block.index("Покажу в плане"), block.index("Сделаю сам"))

    def test_class_absent_from_data_is_not_shown(self):
        """Строка с нулём — это интерфейс, сообщающий «пусто» ради симметрии."""
        block = rh.next_block({"findings": [finding(**{"class": "INFORM"})]})
        self.assertEqual(self.NUM.findall(block), ["1"])
        self.assertNotIn("Решаешь только ты", block)

    def test_no_findings_means_no_numbers_at_all(self):
        """Находок нет — блок обязан остаться осмысленным, а не показать
        четыре нуля."""
        block = rh.next_block({"findings": [], "coverage": {"trustworthy": True}})
        self.assertNotEqual(block.strip(), "")
        self.assertIsNone(re.search(r"\d", self._text(block)), block)

    def test_nothing_disappears_silently(self):
        """Класс вне набора нельзя выбросить: сумма строк обязана сойтись с
        числом находок, иначе блок врёт спокойным тоном. Ниже четыре находки,
        две из которых с классом, которого система не знает."""
        block = rh.next_block({"findings": [
            finding(**{"class": "GATE"}), finding(**{"class": "ЧТО-ТО НОВОЕ"}),
            finding(**{"class": "AUTO"}), finding(**{"class": None}),
        ]})
        self.assertEqual(sum(int(x) for x in self.NUM.findall(block)), 4)

    def test_exactly_one_phrase_to_say(self):
        """Меню из команд — снова терминал. Фраза ровно одна, в любом состоянии."""
        for items in ([], [finding(**{"class": "GATE"})],
                      [finding(**{"class": "INFORM"})],
                      [finding(**{"class": "BLOCK"}), finding(**{"class": "AUTO"})]):
            block = rh.next_block({"findings": items})
            self.assertEqual(block.count('class="say-phrase"'), 1, items)

    def test_phrase_follows_the_data_not_the_template(self):
        """Фраза — следующий шаг, а не украшение: когда применять нечего,
        звать план бессмысленно, и фраза обязана стать другой."""
        act = self._phrase(rh.next_block({"findings": [finding(**{"class": "GATE"})]}))
        idle = self._phrase(rh.next_block({"findings": [finding(**{"class": "INFORM"})]}))
        self.assertNotEqual(act, idle)
        self.assertEqual(
            act, self._phrase(rh.next_block({"findings": [finding(**{"class": "AUTO"})]})))
        self.assertEqual(idle, self._phrase(rh.next_block({"findings": []})))

    def test_empty_run_distinguishes_not_found_from_not_checked(self):
        """«Не нашёл» и «не смог проверить» — разные утверждения, и блок
        продолжения обязан их различать так же, как блок охвата: при битом
        охвате текст другой."""
        clean = rh.next_block({"findings": [], "coverage": {"trustworthy": True}})
        broken = rh.next_block({"findings": [],
                                "coverage": {"trustworthy": False, "probe_errors": 1}})
        self.assertNotEqual(clean, broken)
        self.assertNotIn("ни одна находка", broken)

    def test_block_stands_after_the_findings(self):
        html = page([finding(**{"class": "GATE"})])
        self.assertLess(html.rindex('class="finding"'), html.index('class="next"'))
        self.assertLess(html.index('class="next"'), html.index("<footer>"))

    def test_block_is_on_the_page_even_with_nothing_found(self):
        self.assertIn('class="next"', page([]))

    def test_promise_about_backup_and_quarantine_survives(self):
        """Тест на подстроку в прозе — поведенческим НЕ является. Держит
        только то, что обещание про копию до первой правки и про карантин
        вместо удаления со страницы не исчезло."""
        block = rh.next_block({"findings": [finding(**{"class": "AUTO"})]})
        self.assertIn("Копия настроек делается до первой правки", block)
        self.assertIn("карантин", block)
        self.assertIn("одной командой", block)


class TestCli(unittest.TestCase):
    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(at("tools", "render_html.py"))] + list(args),
            capture_output=True, text=True, timeout=60, cwd=str(REPO), env=ENV)

    def test_missing_args_is_a_message(self):
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_wrong_shape_is_named(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        json.dump({"а": 1}, fh)
        fh.close()
        r = self._run(fh.name)
        self.assertEqual(r.returncode, 2)
        self.assertIn("findings", r.stderr)

    def test_end_to_end_from_real_probe(self):
        facts = subprocess.run(
            [sys.executable, str(at("tools", "probe", "collect.py"))],
            capture_output=True, text=True, timeout=180, env=ENV)
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        fh.write(facts.stdout)
        fh.close()
        fi = subprocess.run(
            [sys.executable, str(at("tools", "adjudicate.py")), fh.name,
             str(plug("superstack-core") / "rules" / "*.json")], capture_output=True, text=True, timeout=60,
            cwd=str(REPO), env=ENV)
        fh2 = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                          encoding="utf-8")
        fh2.write(fi.stdout)
        fh2.close()
        r = self._run(fh2.name)
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        self.assertIn("<!doctype html>", r.stdout)
        self.assertIn("</html>", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
