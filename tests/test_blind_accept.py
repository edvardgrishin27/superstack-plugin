#!/usr/bin/env python3
"""Тесты слепой приёмки.

Механизм отвечает на вопрос, который не задаёт больше ни один гейт: сделали ли
ТО, о чём просили. Безупречно сделанное не то проходит все шесть ворот планки.

Главное свойство под проверкой — слепота обеспечивается ПАКЕТОМ, а не
инструкцией. Просьба «не подглядывай в спеку» механизмом не является:
подглядывание нечем проверить, а невключённое нечем прочитать.
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
from paths import REPO, at, plug  # noqa: E402

TOOL = plug("superstack-guard") / "tools" / "blind_accept.py"
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ba = _load("ss_blind", TOOL)


def diff_for(*paths: str) -> str:
    """Правдоподобный diff по перечисленным файлам."""
    out = []
    for p in paths:
        out.append(f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n"
                   f"@@ -1 +1,2 @@\n+строка в {p}\n")
    return "".join(out)


class TestBlindnessIsEnforcedNotRequested(unittest.TestCase):
    """Судья не видит пересказ — потому что пересказа нет в пакете."""

    def test_spec_files_never_reach_the_judge(self):
        p = ba.build_packet("сделай вход по коду",
                            diff_for("src/login.py", ".claude/specs/login.md"))
        self.assertEqual(p["shown_files"], ["src/login.py"])
        self.assertNotIn("specs/login.md", p["changes"])

    def test_every_retelling_shape_is_recognised(self):
        for path in (".claude/specs/x.md", "PLAN.md", ".planning/roadmap.md",
                     "tasks/T-12.md", "SPEC.md", "REVIEW.md"):
            with self.subTest(path=path):
                self.assertTrue(ba.is_retelling(path), f"пересказ не опознан: {path}")

    def test_ordinary_code_is_not_mistaken_for_a_retelling(self):
        """Обратный контроль: вырезав лишнее, судья получил бы пустой пакет
        и вынес вердикт о работе, которой не видел."""
        for path in ("src/plan_view.py", "docs/README.md", "tests/test_spec.py",
                     "app/specs.ts"):
            with self.subTest(path=path):
                self.assertFalse(ba.is_retelling(path), f"код принят за пересказ: {path}")

    def test_hidden_files_are_named_not_silently_dropped(self):
        p = ba.build_packet("просьба", diff_for("src/a.py", "PLAN.md"))
        self.assertEqual([h["path"] for h in p["hidden_files"]], ["PLAN.md"])
        self.assertTrue(p["hidden_files"][0]["why"])


class TestOurOwnRetellingIsCutToo(unittest.TestCase):
    """Список ловил ЧУЖИЕ форматы пересказа и не ловил свой.

    `.planning/`, `PLAN.md`, тикеты — всё это чужие соглашения, и они были
    закрыты. А манифест требований, состояние плана, границы модулей и отчёты
    ревью лежат в `.superstack/`, все они являются пересказом просьбы, и ни
    один под прежние правила не подпадал. Судья получил бы прочтение брифа,
    сделанное тем же, кто писал код, и сверял бы пересказ с пересказом.
    """

    def test_the_run_directory_never_reaches_the_judge(self):
        for path in (".superstack/manifest.json", ".superstack/state.json",
                     ".superstack/interfaces.md", ".superstack/review-03.json",
                     ".superstack/premortem.json"):
            with self.subTest(path=path):
                self.assertTrue(ba.is_retelling(path), f"пересказ не опознан: {path}")

    def test_a_file_merely_mentioning_superstack_is_still_code(self):
        """Обратный контроль: вырезание идёт по каталогу, а не по слову."""
        self.assertFalse(ba.is_retelling("src/superstack_client.py"))


class TestLeaksInsideTheBodyAreCaught(unittest.TestCase):
    """Вырезание работает по путям; следы пересказа умеют приезжать ВНУТРИ
    файлов, которые пройти обязаны, — например, в комментарии к тесту.

    Свойство независимости, которое никто не проверяет, — это свойство
    независимости, которого нет: пока проверки не было, слепота держалась на
    полноте одного списка регулярок, а список молчит, когда в нём чего-то
    не хватает.
    """

    def _v(self, body: str) -> dict:
        d = (f"diff --git a/src/a.js b/src/a.js\n--- a/src/a.js\n+++ b/src/a.js\n"
             f"@@ -1 +1,2 @@\n+{body}\n")
        return ba.verdict(ba.build_packet("хочу сайт", d))

    def test_a_manifest_id_in_a_comment_is_a_leak(self):
        v = self._v("// R03: форма отправляется")
        self.assertEqual(v["status"], "leaked", v)

    def test_a_path_to_the_run_directory_is_a_leak(self):
        v = self._v("const plan = require('../.superstack/state.json')")
        self.assertEqual(v["status"], "leaked", v)

    def test_a_phrase_pointing_at_the_retelling_is_a_leak(self):
        v = self._v("// по спецификации здесь должно быть 3 места")
        self.assertEqual(v["status"], "leaked", v)

    def test_clean_code_still_passes(self):
        """Обратный контроль: перестраховка сделала бы гейт непроходимым, и
        его начали бы обходить."""
        v = self._v("export const seatsLeft = (slot) => slot.seats - slot.taken")
        self.assertEqual(v["status"], "ready", v)

    def test_a_leak_is_a_failure_not_a_note(self):
        """Судья, увидевший пересказ, вынесет вердикт о совпадении пересказа
        с кодом — и вердикт будет выглядеть точно как настоящий."""
        self.assertEqual(ba.EXIT["leaked"], 1)


class TestNothingToJudgeIsNotSuccess(unittest.TestCase):
    """«Не смог проверить» и «прошло» — разные утверждения."""

    def test_missing_request_blocks_the_gate(self):
        v = ba.verdict(ba.build_packet("   ", diff_for("src/a.py")))
        self.assertEqual(v["status"], "unknown")
        self.assertEqual(ba.EXIT[v["status"]], 2)
        self.assertIn("не сохранена", v["next"])

    def test_only_retelling_changed_is_not_a_pass(self):
        """Изменили одну спеку — работы нет, и судить нечего."""
        v = ba.verdict(ba.build_packet("просьба", diff_for("PLAN.md")))
        self.assertEqual(v["status"], "unknown")
        self.assertIn("пересказ", v["next"])

    def test_unparsable_changes_are_not_shown_to_the_judge(self):
        v = ba.verdict(ba.build_packet("просьба", "просто текст без заголовков"))
        self.assertEqual(v["status"], "unknown")
        self.assertIn("не разобраны", v["next"])

    def test_real_work_makes_the_packet_ready(self):
        """Позитивный контроль: гейт, не пропускающий ничего, бесполезен так же,
        как гейт, пропускающий всё."""
        v = ba.verdict(ba.build_packet("сделай вход", diff_for("src/login.py")))
        self.assertEqual(v["status"], "ready")
        self.assertEqual(ba.EXIT[v["status"]], 0)


class TestPacketCarriesOnlyTwoThings(unittest.TestCase):
    def test_no_conversation_or_spec_fields_leak_in(self):
        p = ba.build_packet("просьба", diff_for("src/a.py"))
        allowed = {"gate", "request", "changes", "shown_files", "hidden_files",
                   "diff_parsed", "truncated"}
        self.assertEqual(set(p), allowed, "в пакете появилось лишнее поле")

    def test_oversized_changes_are_marked_not_silently_cut(self):
        big = "diff --git a/src/a.py b/src/a.py\n" + "+x\n" * 200_000
        p = ba.build_packet("просьба", big)
        self.assertTrue(p["truncated"], "обрезка не названа")
        self.assertLessEqual(len(p["changes"]), ba.MAX_DIFF)


class TestCommandLine(unittest.TestCase):
    def _run(self, request: str, diff: str):
        d = Path(tempfile.mkdtemp())
        (d / "r.txt").write_text(request, encoding="utf-8")
        (d / "d.diff").write_text(diff, encoding="utf-8")
        return subprocess.run([sys.executable, str(TOOL), "pack", "--json",
                               str(d / "r.txt"), str(d / "d.diff")],
                              capture_output=True, text=True, timeout=60, env=ENV)

    def test_exit_zero_only_when_there_is_something_to_judge(self):
        self.assertEqual(self._run("сделай вход", diff_for("src/a.py")).returncode, 0)
        self.assertEqual(self._run("", diff_for("src/a.py")).returncode, 2)

    def test_bad_arguments_are_named_not_crashed(self):
        r = subprocess.run([sys.executable, str(TOOL), "pack", "/нет", "/нет"],
                           capture_output=True, text=True, timeout=60, env=ENV)
        self.assertEqual(r.returncode, 3)
        self.assertIn("НЕ УДАЛОСЬ", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestAgentContract(unittest.TestCase):
    def setUp(self):
        self.text = (plug("superstack-guard") / "agents"
                     / "blind-acceptance.md").read_text("utf-8")
        self.fm = self.text.split("---")[1]
        self.flat = " ".join(self.text.split())

    def test_judge_can_only_read(self):
        tools = [t.strip() for t in
                 __import__("re").search(r"^tools:\s*(.+)$", self.fm,
                                         __import__("re").M).group(1).split(",")]
        self.assertEqual(tools, ["Read"],
                         "судье дали больше чтения — приёмка перестаёт быть слепой")

    def test_judge_runs_on_another_model(self):
        self.assertRegex(self.fm, r"(?m)^model:\s*fable\s*$")

    def test_forbids_manufactured_objections(self):
        self.assertIn("Не выдумывай замечаний", self.flat)

    def test_names_the_three_ways_to_fail(self):
        for phrase in ("Не сделали то", "Сделали то, о чём не просили",
                       "Сделали похожее"):
            self.assertIn(phrase, self.flat, f"не назван способ промаха: {phrase}")


if __name__ == "__main__":
    unittest.main()
