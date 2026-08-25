#!/usr/bin/env python3
"""Ступень самостоятельности: ручка, которую нельзя повернуть словами.

Зачем ручка. Сегодня объём самостоятельности задаётся молча: где-то спросили,
где-то нет, и человек узнаёт границу по факту — когда что-то случилось без него.

Зачем подъём — проверка, а не запись. Всё, что здесь автоматизируется, стоит на
доказательствах: пропускать подтверждение плана осмысленно там, где приёмка
поймает расхождение; закрывать ход без человека — там, где тесты доказали, что
умеют падать. Ступень, выставленная желанием, снимает подтверждения, не добавив
проверок, и первым же прогоном превращается в «оно само что-то сделало».
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

_s = importlib.util.spec_from_file_location(
    "ss_autonomy", PKG / "tools" / "autonomy.py")
au = importlib.util.module_from_spec(_s)
_s.loader.exec_module(au)


class Project(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()

    def write(self, rel: str, data) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data if isinstance(data, str)
                     else json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def with_tests(self):
        self.write("package.json", {"scripts": {"test": "node --test"}})

    def with_verify(self, code: int = 0):
        self.write(".superstack/verify-last.json", {"exit_code": code})

    def with_mutations(self):
        self.write(".superstack/mutations.json", {"mutations": [{"id": "m.a"}]})

    def with_blind(self):
        self.write(".superstack/manifest.json", {"blind": {"R01": "done"}})


class TestAFreshProjectStartsAtZero(Project):

    def test_no_record_means_zero(self):
        self.assertEqual(au.read(self.root)["level"], 0)

    def test_a_broken_record_is_read_as_zero(self):
        """Повреждённая запись не имеет права читаться как высокая ступень:
        ошибка разбора не является разрешением действовать без человека."""
        self.write(".superstack/autonomy.json", "{сломано")
        self.assertEqual(au.read(self.root)["level"], 0)

    def test_an_out_of_range_level_is_read_as_zero(self):
        self.write(".superstack/autonomy.json", {"level": 9})
        self.assertEqual(au.read(self.root)["level"], 0)

    def test_zero_needs_nothing(self):
        self.assertTrue(au.can(self.root, 0)["ok"])


class TestEachStepNamesItsCondition(Project):

    def test_first_step_needs_a_test_command(self):
        self.assertFalse(au.can(self.root, 1)["ok"])
        self.with_tests()
        self.assertTrue(au.can(self.root, 1)["ok"])

    def test_second_step_needs_a_gate_that_answers(self):
        """«Проверять нечем» — не основание писать код без подтверждения."""
        self.with_tests()
        v = au.can(self.root, 2)
        self.assertFalse(v["ok"])
        self.assertTrue(any("верификации" in m for m in v["missing"]), v)
        self.with_verify()
        self.assertTrue(au.can(self.root, 2)["ok"])

    def test_a_gate_that_could_not_measure_does_not_count(self):
        """Код 2 означает «проверять нечем» и ступени не покупает."""
        self.with_tests()
        self.with_verify(code=2)
        self.assertFalse(au.can(self.root, 2)["ok"])

    def test_a_red_gate_still_counts_as_an_answer(self):
        """Красный прогон — рабочая проверка: она ответила кодом. Требовать
        зелёного значило бы поощрять проекты, где гейт молчит."""
        self.with_tests()
        self.with_verify(code=1)
        self.assertTrue(au.can(self.root, 2)["ok"])

    def test_third_step_needs_proof_that_tests_can_fail(self):
        self.with_tests()
        self.with_verify()
        self.with_blind()
        v = au.can(self.root, 3)
        self.assertFalse(v["ok"])
        self.assertTrue(any("поломки" in m for m in v["missing"]), v)

    def test_third_step_needs_blind_acceptance_to_have_run(self):
        self.with_tests()
        self.with_verify()
        self.with_mutations()
        v = au.can(self.root, 3)
        self.assertFalse(v["ok"])
        self.assertTrue(any("приёмка" in m for m in v["missing"]), v)

    def test_third_step_opens_when_everything_is_proven(self):
        self.with_tests()
        self.with_verify()
        self.with_mutations()
        self.with_blind()
        self.assertTrue(au.can(self.root, 3)["ok"])


class TestThereIsNoFourthStep(Project):
    """Четвёртая ступень означала бы, что система сама решает, ЧТО строить.
    Это другое решение, и из желания не подтверждать план оно не следует."""

    def test_the_ceiling_is_three(self):
        self.assertEqual(au.MAX_LEVEL, 3)
        self.assertNotIn(4, au.LEVELS)


if __name__ == "__main__":
    unittest.main()
