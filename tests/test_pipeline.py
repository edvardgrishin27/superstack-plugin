#!/usr/bin/env python3
"""Цепочка целиком: выход одного инструмента — вход следующего.

Файл написан после сквозного прогона на выдуманном проекте, и его существование
— вывод из этого прогона. Тысяча тестов проверяла каждый инструмент на СВОИХ
фикстурах, которые я же и сочинял под него. Стыковку не проверял никто, и там
нашлось шесть дефектов подряд:

  · `progress.py` не умел записывать блокеры — и `crew.py` клал все таски в
    первую волну, объявляя расхождение и ложные пересечения зон;
  · не умел записывать отметку старта — и обнаружение последовательного полёта,
    механизм, которым я хвалился, не имел что мерить;
  · не умел записывать критерии приёмки, цель и цитаты — и `handoff.py` честно
    отказывал в передаче КАЖДОГО таска;
  · перезапись таска теряла всё, чего не передали в этот раз;
  · `handoff.py` отдавал исполнителю спеку целиком вместо названных разделов;
  · гейт G1 был непроходим в той фазе, где стоит.

Каждый инструмент при этом работал. Урок: фикстура, написанная под инструмент,
доказывает только то, что он согласен сам с собой.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from paths import at


def _load(name: str):
    p = at("tools", name)
    s = importlib.util.spec_from_file_location(f"pipe_{name[:-3]}", p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


pg = _load("progress.py")
cr = _load("crew.py")
ho = _load("handoff.py")
gt = _load("gates.py")
mf = gt.mf

BRIEF = ("Хочу сайт студии керамики. Чтобы люди видели работы и записывались\n"
         "на мастер-класс. Записи должны падать мне в телеграм.\n")


class Chain(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "brief.md").write_text(BRIEF, encoding="utf-8")
        self.state = self.dir / "state.json"

    def plan(self):
        """План, нарезанный ТОЛЬКО через progress.py — как это делает скилл."""
        d = pg.load(self.state)
        pg.set_task(d, "01", "каркас и галерея", 1, pg.RUNNING,
                    requirements=["R01"], zone=["src/"],
                    started="2026-08-14T12:00:00+00:00",
                    goal="посетитель видит работы",
                    acceptance=["галерея открывается", "пустой список не ломает страницу"],
                    quotes=["люди видели работы"], spec_sections=["Границы и швы"])
        pg.set_task(d, "02", "форма записи", 2, pg.RUNNING,
                    requirements=["R02"], zone=["src/signup/"], blocked_by=["01"],
                    started="2026-08-14T12:20:00+00:00",
                    goal="посетитель оставляет имя и телефон",
                    acceptance=["форма отправляется", "пустой телефон даёт ошибку"],
                    quotes=["записывались на мастер-класс"],
                    spec_sections=["Границы и швы"])
        pg.set_task(d, "03", "отправка в телеграм", 2, pg.RUNNING,
                    requirements=["R03"], zone=["src/notify/"], blocked_by=["01"],
                    started="2026-08-14T12:20:05+00:00",
                    goal="владелец получает запись",
                    acceptance=["сообщение доходит", "отказ сети не теряет запись"],
                    quotes=["Записи должны падать мне в телеграм"],
                    spec_sections=["Границы и швы"])
        pg.save(self.state, d, now="2026-08-14T12:20:05+00:00")
        return d

    def manifest(self, statuses=("in-spec", "in-spec", "in-spec")):
        d = json.loads(json.dumps(mf.EMPTY))
        d["brief"] = "brief.md"
        d["brief_sha"] = mf.sha(self.dir / "brief.md")
        for i, (q, st) in enumerate(zip(
                ["люди видели работы", "записывались на мастер-класс",
                 "Записи должны падать мне в телеграм"], statuses), 1):
            d["requirements"].append(
                {"id": f"R0{i}", "kind": mf.EXPLICIT, "quote": q, "status": st,
                 "basis": "", "parent": "", "where": "spec", "said": ""})
        return d


class TestPlanFeedsTheCrew(Chain):
    """`progress.py` -> `crew.py`. Раньше здесь терялись блокеры и отметки."""

    def test_blockers_survive_the_write(self):
        d = self.plan()
        two = next(t for t in cr.rows(d) if t["id"] == "02")
        self.assertEqual(two.get("blockedBy"), ["01"])

    def test_crew_computes_the_waves_the_plan_declared(self):
        v = cr.check(self.plan(), declared="T1")
        self.assertFalse([p for p in v["problems"] if "волны разошлись" in p], v)

    def test_no_false_zone_clash(self):
        """Без блокеров все таски падали в первую волну, и зона `src/`
        «пересекалась» с `src/signup/` — конфликт, которого нет."""
        v = cr.check(self.plan(), declared="T1")
        self.assertFalse([p for p in v["problems"] if "делят территорию" in p], v)

    def test_serial_flight_has_something_to_measure(self):
        """Механизм, которым мы отличаемся, был слеп: отметок старта в
        состоянии не было вовсе, и он вечно отвечал «не смог проверить»."""
        d = self.plan()
        v = cr.check(d, declared="T1")
        self.assertFalse([u for u in v["unmeasured"] if "отметок старта" in u], v)

    def test_serial_flight_actually_fires_on_a_serial_wave(self):
        d = self.plan()
        for t in d["waves"]["2"]:
            if t["id"] == "03":
                t["started"] = "2026-08-14T12:45:00+00:00"
        v = cr.check(d, declared="T1")
        self.assertTrue([p for p in v["problems"] if "гуськом" in p], v)

    def test_running_status_stamps_the_start_by_itself(self):
        """Пропущенная отметка тише неверной и дороже: она делает проверку
        параллельности бессильной, ничего об этом не сказав."""
        import subprocess, os, sys
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run([sys.executable, str(at("tools", "progress.py")), "task",
                        str(self.state), "07", "таск", "--wave", "1",
                        "--status", "running"], cwd=str(self.dir),
                       capture_output=True, timeout=60, env=env)
        got = next(t for t in cr.rows(pg.load(self.state)) if t["id"] == "07")
        self.assertIn("started", got)


class TestPlanFeedsTheHandoff(Chain):
    """`progress.py` -> `handoff.py`. Раньше передача отказывала на каждом."""

    def test_a_task_cut_by_progress_can_be_handed_over(self):
        d = self.plan()
        task = next(t for t in cr.rows(d) if t["id"] == "02")
        bad = ho.blockers(d, task, "границы", "## Границы и швы\nтекст\n", "npm test")
        self.assertEqual(bad, [], bad)

    def test_the_prompt_carries_what_the_plan_recorded(self):
        d = self.plan()
        task = next(t for t in cr.rows(d) if t["id"] == "02")
        p = ho.build(d, task, "границы", "", "npm test")
        self.assertIn("записывались на мастер-класс", p)
        self.assertIn("форма отправляется", p)
        self.assertIn("посетитель оставляет имя и телефон", p)

    def test_rewriting_a_task_does_not_lose_its_criteria(self):
        """Статус меняют чаще, чем критерии. Молчаливая потеря делала передачу
        невозможной со второго вызова — а второй вызов бывает всегда."""
        d = self.plan()
        pg.set_task(d, "02", "форма записи", 2, pg.PROVEN, exit_code=0)
        task = next(t for t in cr.rows(d) if t["id"] == "02")
        for key in ("acceptance", "quotes", "goal", "zone", "blockedBy",
                    "requirements", "started"):
            with self.subTest(key=key):
                self.assertIn(key, task)

    def test_only_the_named_spec_sections_reach_the_executor(self):
        """Исполнитель, видящий всю спеку, перестаёт быть исполнителем одного
        таска: он начинает решать за соседние."""
        spec = ("# Спека\n\n## Что должно получиться\nсекрет соседнего таска\n\n"
                "## Границы и швы\nнужный кусок\n")
        got = ho.extract_sections(spec, ["Границы и швы"])
        self.assertIn("нужный кусок", got)
        self.assertNotIn("секрет соседнего таска", got)

    def test_naming_no_sections_means_nothing_not_everything(self):
        spec = "# Спека\n\n## Что-то\nтекст\n"
        self.assertEqual(ho.extract_sections(spec, []), "")


class TestGatesReadTheSameState(Chain):

    def test_g3_traces_against_the_plan_progress_wrote(self):
        v = gt.gate_g3(self.manifest(), self.plan())
        self.assertEqual(v["status"], gt.PASS, v)

    def test_g1_is_passable_at_its_own_phase(self):
        """Гейт стоит после брифинга, а раскладывает требования спека —
        следующая фаза. Требуя ноль `open`, он был непроходим в своём месте:
        пришлось бы писать спеку до гейта, который её разрешает."""
        d = self.manifest(("open", "open", "open"))
        for r in d["requirements"]:
            r["basis"] = "подтверждено в брифинге"
        self.assertEqual(gt.gate_g1(d)["status"], gt.PASS)

    def test_open_without_a_reason_is_still_red(self):
        d = self.manifest(("open", "in-spec", "in-spec"))
        v = gt.gate_g1(d)
        self.assertEqual(v["status"], gt.FAIL)
        self.assertEqual(v["rows"], ["R01"])


class TestWriteCodesReportTheWriteNotTheWholeState(Chain):
    """Конвейер с `set -e` умирал на первой команде, а человек читал отказ
    там, где всё записалось верно."""

    def _run(self, tool: str, *args):
        import subprocess, os, sys
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(at("tools", tool)), *args],
                              cwd=str(self.dir), capture_output=True,
                              text=True, timeout=60, env=env)

    def test_manifest_init_on_an_empty_project_exits_zero(self):
        p = self._run("manifest.py", "init", "m.json", "brief.md")
        self.assertEqual(p.returncode, 0, p.stderr[-300:])

    def test_first_premortem_finding_exits_zero(self):
        p = self._run("premortem.py", "add", "pm.json", "--q", "провал",
                      "--what", "никто не приходит на сайт",
                      "--then", "спросить, откуда возьмутся посетители")
        self.assertEqual(p.returncode, 0, p.stderr[-300:])

    def test_a_write_that_breaks_the_state_still_exits_one(self):
        """Послабление касается неполноты, а не нарушения: запись, сделавшая
        состояние неверным, — это провал записи."""
        self._run("manifest.py", "init", "m.json", "brief.md")
        self._run("manifest.py", "add", "m.json", "R01",
                  "--quote", "люди видели работы")
        (self.dir / "brief.md").write_text("подменённый эталон", encoding="utf-8")
        d = json.loads((self.dir / "m.json").read_text("utf-8"))
        d["requirements"][0]["status"] = "dropped"      # снято без слов человека
        (self.dir / "m.json").write_text(json.dumps(d, ensure_ascii=False),
                                         encoding="utf-8")
        p = self._run("manifest.py", "show", "m.json")
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-300:])


if __name__ == "__main__":
    unittest.main()
