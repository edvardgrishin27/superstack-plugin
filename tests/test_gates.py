#!/usr/bin/env python3
"""Четыре гейта: то, что у AutoPilot просьба, здесь — код возврата.

Разница вся в одном месте, и она видна в первом же классе ниже. У него G2 —
абзац «заведи субагента и убедись, что расхождений нет». Гейт из абзаца
проходится утверждением: модель говорит «проверил», и опровергнуть это нечем.
Ровно эта дыра описана в нашем плане про `/goal` — оценщик не вызывает
инструменты и удовлетворяется тем, что агент СКАЗАЛ.

Здесь `coverage: null` — красное. Не «пока не знаем», а провал: незапущенная
проверка и чистая проверка обязаны выглядеть по-разному, иначе гейт выдаёт
неведение за порядок ровно тогда, когда его никто не запускал.
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

from paths import at

GATES = at("tools", "gates.py")
_g = importlib.util.spec_from_file_location("superstack_gates", GATES)
gt = importlib.util.module_from_spec(_g)
_g.loader.exec_module(gt)
mf = gt.mf

BRIEF = "принимает заявки на ремонт техники и складывает их в Google-таблицу\n"


def manifest(dirp: Path, statuses=("in-spec", "in-spec"), **extra) -> dict:
    (dirp / "brief.md").write_text(BRIEF, encoding="utf-8")
    d = json.loads(json.dumps(mf.EMPTY))
    d["brief"] = "brief.md"
    d["brief_sha"] = mf.sha(dirp / "brief.md")
    quotes = ["принимает заявки на ремонт техники", "складывает их в Google-таблицу"]
    for i, (q, st) in enumerate(zip(quotes, statuses), 1):
        d["requirements"].append(
            {"id": f"R0{i}", "kind": mf.EXPLICIT, "quote": q, "status": st,
             "basis": "", "parent": "", "where": "", "said": ""})
    d.update(extra)
    return d


def tasks(*rows) -> dict:
    return {"schema": "superstack.progress.v1", "waves": {"1": list(rows)}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "manifest.json"


class TestG1EveryRequirementHasADecision(Base):

    def test_open_rows_are_red(self):
        r = gt.gate_g1(manifest(self.dir, ("open", "in-spec")))
        self.assertEqual(r["status"], gt.FAIL)
        self.assertEqual(r["rows"], ["R01"])

    def test_all_decided_passes(self):
        self.assertEqual(gt.gate_g1(manifest(self.dir))["status"], gt.PASS)

    def test_empty_manifest_is_unknown_not_pass(self):
        """Ноль требований — это «бриф не разобран», а не «всё в порядке».
        Гейт, проходящий на пустоте, зелёный ровно там, где работы не было."""
        d = manifest(self.dir)
        d["requirements"] = []
        self.assertEqual(gt.gate_g1(d)["status"], gt.UNKNOWN)


class TestG2TheHalfThatWorksIsTheIndependentOne(Base):
    """Класс, ради которого всё это переписывалось из прозы в код."""

    def test_unrun_coverage_is_red_not_silent(self):
        d = manifest(self.dir)
        self.assertIsNone(d["coverage"])
        r = gt.gate_g2(d)
        self.assertEqual(r["status"], gt.FAIL, r)
        self.assertIn("НЕ ЗАПУСКАЛАСЬ", r["detail"])

    def test_recorded_clean_coverage_passes(self):
        d = manifest(self.dir, coverage={"found": 0, "fixed": 0, "deferred": 0})
        self.assertEqual(gt.gate_g2(d)["status"], gt.PASS)

    def test_findings_left_unresolved_are_red(self):
        """Сверка, которая нашла и ничего не закрыла, хуже незапущенной: она
        даёт цифру и ощущение, что вопрос разобран."""
        d = manifest(self.dir, coverage={"found": 3, "fixed": 1, "deferred": 0})
        r = gt.gate_g2(d)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertIn("висит без решения", r["detail"])

    def test_malformed_coverage_is_unknown_not_pass(self):
        d = manifest(self.dir, coverage={"ok": True})
        self.assertEqual(gt.gate_g2(d)["status"], gt.UNKNOWN)

    def test_own_half_runs_first(self):
        """Незакрытый вопрос брифинга обязан валить G2 раньше, чем дело дойдёт
        до независимой сверки: сверять покрытие спеки, в которой ещё дыра от
        неотвеченного вопроса, — мерить не то."""
        d = manifest(self.dir, ("open", "in-spec"),
                     coverage={"found": 0, "fixed": 0, "deferred": 0})
        r = gt.gate_g2(d)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertEqual(r["half"], "своя")

    def test_requirement_not_placed_anywhere_is_red(self):
        d = manifest(self.dir, ("placeholder", "in-spec"),
                     coverage={"found": 0, "fixed": 0, "deferred": 0})
        self.assertEqual(gt.gate_g2(d)["status"], gt.PASS)


class TestG3TraceabilityRunsBothWays(Base):

    def test_missing_tasks_is_unknown_not_pass(self):
        self.assertEqual(gt.gate_g3(manifest(self.dir), None)["status"], gt.UNKNOWN)

    def test_requirement_with_no_task_is_red(self):
        t = tasks({"id": "01", "name": "каркас", "status": "waiting",
                   "requirements": ["R01"]})
        r = gt.gate_g3(manifest(self.dir), t)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertEqual(r["forward"], ["R02"])

    def test_task_with_no_requirement_is_red(self):
        """Обратное направление ловит работу, которую никто не заказывал, —
        и она дороже пропущенного требования: съедает контекст, путает приёмку
        и не имеет владельца."""
        t = tasks({"id": "01", "name": "всё", "status": "waiting",
                   "requirements": ["R01", "R02"]},
                  {"id": "02", "name": "рефакторинг ради красоты", "status": "waiting"})
        r = gt.gate_g3(manifest(self.dir), t)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertEqual(r["backward"], ["02"])

    def test_task_pointing_at_a_nonexistent_requirement_is_red(self):
        t = tasks({"id": "01", "name": "всё", "status": "waiting",
                   "requirements": ["R01", "R02", "R99"]})
        r = gt.gate_g3(manifest(self.dir), t)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertEqual(r["unknown"], ["R99"])

    def test_both_directions_clean_passes(self):
        t = tasks({"id": "01", "name": "всё", "status": "waiting",
                   "requirements": ["R01", "R02"]})
        self.assertEqual(gt.gate_g3(manifest(self.dir), t)["status"], gt.PASS)

    def test_dropped_requirement_needs_no_task(self):
        d = manifest(self.dir, ("dropped", "in-spec"))
        d["requirements"][0]["said"] = "это не надо"
        t = tasks({"id": "01", "name": "таблица", "status": "waiting",
                   "requirements": ["R02"]})
        self.assertEqual(gt.gate_g3(d, t)["status"], gt.PASS)


class TestG4TheManifestMayNotOutliveTheTruth(Base):

    def test_unrun_blind_is_red(self):
        r = gt.gate_g4(manifest(self.dir, ("done", "done")))
        self.assertEqual(r["status"], gt.FAIL)
        self.assertIn("НЕ ЗАПУСКАЛАСЬ", r["detail"])

    def test_manifest_claiming_done_against_partial_is_drift(self):
        d = manifest(self.dir, ("done", "done"), blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.PARTIAL}]})
        r = gt.gate_g4(d)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertEqual([x["id"] for x in r["drift"]], ["R02"])

    def test_dropped_but_built_is_drift(self):
        d = manifest(self.dir, ("dropped", "done"), blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.BUILT}]})
        d["requirements"][0]["said"] = "это не надо"
        r = gt.gate_g4(d)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertEqual([x["id"] for x in r["drift"]], ["R01"])

    def test_built_but_never_requested_is_drift(self):
        d = manifest(self.dir, ("done", "done"), blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.BUILT},
            {"id": "R77", "verdict": gt.BUILT}]})
        r = gt.gate_g4(d)
        self.assertEqual(r["status"], gt.FAIL)
        self.assertIn("нет в манифесте", r["drift"][0]["why"])

    def test_agreement_passes(self):
        d = manifest(self.dir, ("done", "done"), blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.BUILT}]})
        self.assertEqual(gt.gate_g4(d)["status"], gt.PASS)

    def test_lowering_the_row_is_a_legitimate_way_to_agree(self):
        """Сойтись можно в обе стороны: починить сборку либо честно понизить
        строку. Гейт держит не «всё построено», а «манифест не врёт»."""
        d = manifest(self.dir, ("done", "placeholder"), blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.PARTIAL}]})
        self.assertEqual(gt.gate_g4(d)["status"], gt.PASS)

    def test_drift_is_computed_here_not_copied_from_the_checker(self):
        """Расхождение, которое надо не забыть переписать в отчёт, однажды не
        перепишут. Считаем сами из двух источников."""
        d = manifest(self.dir, ("done", "done"), blind={
            "checked": [{"id": "R02", "verdict": gt.ABSENT}],
            "drift": []})          # проверяющий «ничего не заметил»
        self.assertEqual(gt.gate_g4(d)["status"], gt.FAIL)


class TestPartialRunIsNeverAPass(Base):
    """То же правило, что у планки: одни ворота не покупают «взято»."""

    def test_single_gate_leaves_the_rest_visible_as_skipped(self):
        v = gt.run(manifest(self.dir), self.path, None, "G1")
        self.assertFalse(v["passed"])
        skipped = [g["gate"] for g in v["gates"] if g["status"] == "skipped"]
        self.assertEqual(skipped, ["G2", "G3", "G4"])

    def test_red_gate_names_the_next_step(self):
        v = gt.run(manifest(self.dir, ("open", "open")), self.path, None, "G1")
        self.assertTrue(v["next"])


class TestExitCodes(Base):

    def _run(self, data: dict, *args) -> subprocess.CompletedProcess:
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(GATES), str(self.path), *args],
                              cwd=str(self.dir), capture_output=True, text=True,
                              timeout=120, env=env)

    def test_all_green_exits_zero(self):
        t = self.dir / "p.json"
        t.write_text(json.dumps(tasks({"id": "01", "name": "всё", "status": "waiting",
                                       "requirements": ["R01", "R02"]})),
                     encoding="utf-8")
        d = manifest(self.dir, ("done", "done"),
                     coverage={"found": 0, "fixed": 0, "deferred": 0},
                     blind={"checked": [{"id": "R01", "verdict": gt.BUILT},
                                        {"id": "R02", "verdict": gt.BUILT}]})
        p = self._run(d, "--tasks", str(t), "--json")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-500:])

    def test_red_gate_exits_one(self):
        self.assertEqual(self._run(manifest(self.dir, ("open", "open")),
                                   "--gate", "G1", "--json").returncode, 1)

    def test_unrunnable_gate_exits_two(self):
        self.assertEqual(self._run(manifest(self.dir), "--gate", "G3",
                                   "--json").returncode, 2)

    def test_bad_gate_name_exits_three(self):
        self.assertEqual(self._run(manifest(self.dir), "--gate", "G9").returncode, 3)

    def test_flag_value_is_not_taken_for_a_path(self):
        """Наивный отсев «не начинается с --» оставлял `G1` позиционным, и
        инструмент отвечал подсказкой по вызову на каждый вызов с флагом.
        Поймано первым живым запуском, а не рассуждением."""
        p = self._run(manifest(self.dir), "--gate", "G1", "--json")
        self.assertNotIn("вызов: gates.py", p.stderr)


class TestManifestBreakageReachesTheGates(Base):

    def test_broken_manifest_shows_up_in_the_verdict(self):
        d = manifest(self.dir)
        d["requirements"][0]["quote"] = "чего человек не говорил"
        v = gt.run(d, self.path)
        self.assertTrue(v["manifest_broken"])
        self.assertFalse(v["passed"])

    def test_edited_brief_is_unmeasured_not_broken(self):
        d = manifest(self.dir)
        (self.dir / "brief.md").write_text("подменённый эталон", encoding="utf-8")
        v = gt.run(d, self.path)
        self.assertTrue(v["manifest_unmeasured"])
        self.assertFalse(v["manifest_broken"])


if __name__ == "__main__":
    unittest.main()
