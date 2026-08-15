#!/usr/bin/env python3
"""Экипаж: волны, зоны, ярус — и последовательный полёт.

Первые три детерминированы полностью, и модели незачем их прикидывать. Четвёртая
проверка — та, ради которой файл интереснее остальных.

AutoPilot называет последовательный полёт отказом по умолчанию и там же честно
пишет, что изнутри он «выглядит точно как правильный»: два вызова субагента в
двух сообщениях исполняются один за другим, и параллельность, посчитанная в
плане, молча теряется на доставке. Видимого симптома нет — человек просто ждёт
час вместо двадцати минут.

Увидеть нельзя, измерить можно. Товарищи по волне обязаны иметь отметки старта
в пределах секунд; разброс в минуты доказывает гуськовый полёт из состояния,
а не из добрых намерений.
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

CREW = at("tools", "crew.py")
_c = importlib.util.spec_from_file_location("superstack_crew", CREW)
cr = importlib.util.module_from_spec(_c)
_c.loader.exec_module(cr)


def T(tid, wave, *, zone=None, blocked=(), started=None, name="таск"):
    t = {"id": tid, "name": name, "status": "waiting", "blockedBy": list(blocked)}
    if zone is not None:
        t["zone"] = list(zone)
    if started is not None:
        t["started"] = started
    return wave, t


def state(*pairs) -> dict:
    waves = {}
    for w, t in pairs:
        waves.setdefault(str(w), []).append(t)
    return {"schema": "superstack.progress.v1", "waves": waves}


class TestWavesAreComputedNotGuessed(unittest.TestCase):

    def test_chain(self):
        tasks = [T("01", 1)[1], T("02", 2, blocked=["01"])[1],
                 T("03", 3, blocked=["02"])[1]]
        w, bad = cr.compute_waves(tasks)
        self.assertEqual(bad, [])
        self.assertEqual(w, {"01": 1, "02": 2, "03": 3})

    def test_diamond_takes_the_longest_path(self):
        """Волна = 1 + MAX волны блокеров, не минимум и не среднее: таск,
        ждущий двоих, не может стартовать раньше медленного из них."""
        tasks = [T("01", 1)[1], T("02", 2, blocked=["01"])[1],
                 T("03", 3, blocked=["02"])[1],
                 T("04", 4, blocked=["01", "03"])[1]]
        w, _ = cr.compute_waves(tasks)
        self.assertEqual(w["04"], 4)

    def test_independent_tasks_are_all_wave_one(self):
        tasks = [T(f"0{i}", 1)[1] for i in range(1, 5)]
        w, _ = cr.compute_waves(tasks)
        self.assertEqual(set(w.values()), {1})

    def test_cycle_is_named_not_resolved_at_random(self):
        tasks = [T("01", 1, blocked=["02"])[1], T("02", 1, blocked=["01"])[1]]
        _, bad = cr.compute_waves(tasks)
        self.assertTrue(any("цикл" in b for b in bad), bad)

    def test_unknown_blocker_is_named(self):
        tasks = [T("01", 1, blocked=["99"])[1]]
        _, bad = cr.compute_waves(tasks)
        self.assertTrue(any("несуществующие блокеры" in b for b in bad), bad)

    def test_declared_wave_disagreeing_with_dependencies_is_caught(self):
        s = state(T("01", 1), T("02", 1, blocked=["01"]))
        v = cr.check(s)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("волны разошлись" in p for p in v["problems"]), v)


class TestZonesAreTerritoryNotFileLists(unittest.TestCase):

    def test_identical_zones_overlap(self):
        self.assertTrue(cr.zones_overlap(["src/bot/"], ["src/bot/"]))

    def test_nested_zones_overlap_in_both_directions(self):
        """`src/bot/` и `src/bot/intake/` — одна территория. Два субагента там
        перезапишут друг друга, и потеря будет молчаливой."""
        self.assertTrue(cr.zones_overlap(["src/bot/"], ["src/bot/intake/"]))
        self.assertTrue(cr.zones_overlap(["src/bot/intake/"], ["src/bot/"]))

    def test_trailing_slash_does_not_matter(self):
        self.assertTrue(cr.zones_overlap(["src/bot"], ["src/bot/"]))

    def test_sibling_zones_do_not_overlap(self):
        self.assertFalse(cr.zones_overlap(["src/bot/"], ["src/admin/"]))

    def test_prefix_of_a_name_is_not_nesting(self):
        """`src/bot/` и `src/bottom/` начинаются одинаково и территорией не
        пересекаются. Сравнение по сырой строке дало бы ложный конфликт и
        развело бы по волнам то, что могло лететь вместе."""
        self.assertFalse(cr.zones_overlap(["src/bot/"], ["src/bottom/"]))

    def test_clash_inside_one_wave_is_caught(self):
        s = state(T("01", 1, zone=["src/bot/"]), T("02", 1, zone=["src/bot/x/"]))
        v = cr.check(s)
        self.assertTrue(any("делят территорию" in p for p in v["problems"]), v)

    def test_same_zone_in_different_waves_is_fine(self):
        s = state(T("01", 1, zone=["src/bot/"], started="2026-08-14T10:00:00+00:00"),
                  T("02", 2, zone=["src/bot/"], blocked=["01"],
                    started="2026-08-14T10:10:00+00:00"))
        v = cr.check(s)
        self.assertFalse([p for p in v["problems"] if "делят территорию" in p], v)

    def test_missing_zone_is_unmeasured_not_clean(self):
        """Без зоны непересечение проверить нечем. Молчаливое «чисто» здесь
        означало бы, что волна признана безопасной без единой проверки."""
        s = state(T("01", 1), T("02", 1))
        v = cr.check(s)
        self.assertTrue(any("без зоны" in u for u in v["unmeasured"]), v)


class TestTierComesFromTheProduct(unittest.TestCase):

    def test_known_bands(self):
        for n, tier in ((0, "T0"), (2, "T1"), (3, "T1"), (4, "T2"),
                        (8, "T2"), (9, "T3"), (16, "T3")):
            with self.subTest(n=n):
                self.assertEqual(cr.tier_of(n), tier)

    def test_one_task_is_between_bands(self):
        """Один таск дороже, чем ноль или два: граница отдельного контекста
        стоит дороже работы внутри него."""
        self.assertIsNone(cr.tier_of(1))
        self.assertEqual(cr.tier_check([{"id": "01"}])["status"], "fail")

    def test_above_the_ceiling_is_refused(self):
        r = cr.tier_check([{"id": f"{i:02d}"} for i in range(17)])
        self.assertEqual(r["status"], "fail")
        self.assertIn("потолке", r["detail"])

    def test_declared_tier_may_not_disagree_with_the_count(self):
        r = cr.tier_check([{"id": f"{i:02d}"} for i in range(5)], declared="T1")
        self.assertEqual(r["status"], "fail")
        self.assertIn("объявлен T1", r["detail"])

    def test_matching_declaration_passes(self):
        r = cr.tier_check([{"id": f"{i:02d}"} for i in range(5)], declared="T2")
        self.assertEqual(r["status"], "pass")


class TestSerialFlightIsMeasuredFromTheClock(unittest.TestCase):
    """Единственный способ поймать отказ, который изнутри неотличим от нормы."""

    def test_wave_launched_together_is_clean(self):
        w = {"01": 1, "02": 1}
        tasks = [T("01", 1, started="2026-08-14T10:00:00+00:00")[1],
                 T("02", 1, started="2026-08-14T10:00:07+00:00")[1]]
        self.assertEqual(cr.serial_flight(tasks, w)["serial"], [])

    def test_wave_flown_one_at_a_time_is_caught(self):
        w = {"01": 1, "02": 1}
        tasks = [T("01", 1, started="2026-08-14T10:00:00+00:00")[1],
                 T("02", 1, started="2026-08-14T10:18:00+00:00")[1]]
        s = cr.serial_flight(tasks, w)["serial"]
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["spread_seconds"], 1080)

    def test_a_wave_of_one_is_never_serial(self):
        tasks = [T("01", 1, started="2026-08-14T10:00:00+00:00")[1]]
        self.assertEqual(cr.serial_flight(tasks, {"01": 1})["serial"], [])

    def test_missing_stamps_are_unmeasured_not_clean(self):
        w = {"01": 1, "02": 1}
        tasks = [T("01", 1, started="2026-08-14T10:00:00+00:00")[1], T("02", 1)[1]]
        r = cr.serial_flight(tasks, w)
        self.assertEqual(r["serial"], [])
        self.assertTrue(r["unmeasured"])

    def test_unparsable_stamp_is_unmeasured(self):
        w = {"01": 1, "02": 1}
        tasks = [T("01", 1, started="вчера вечером")[1],
                 T("02", 1, started="2026-08-14T10:00:00+00:00")[1]]
        self.assertTrue(cr.serial_flight(tasks, w)["unmeasured"])

    def test_startedAt_spelling_is_accepted_too(self):
        w = {"01": 1, "02": 1}
        tasks = [{"id": "01", "startedAt": "2026-08-14T10:00:00+00:00"},
                 {"id": "02", "startedAt": "2026-08-14T10:19:00+00:00"}]
        self.assertEqual(len(cr.serial_flight(tasks, w)["serial"]), 1)


class TestPartialCheckIsNeverAPass(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "state.json"

    def _run(self, s: dict, *args) -> subprocess.CompletedProcess:
        self.path.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(CREW), *args, str(self.path)],
                              cwd=str(self.dir), capture_output=True, text=True,
                              timeout=120, env=env)

    def _clean(self) -> dict:
        return state(T("01", 1, zone=["src/a/"], started="2026-08-14T10:00:00+00:00"),
                     T("02", 1, zone=["src/b/"], started="2026-08-14T10:00:05+00:00"),
                     T("03", 2, zone=["src/c/"], blocked=["01"],
                       started="2026-08-14T10:10:00+00:00"))

    def test_full_clean_check_exits_zero(self):
        p = self._run(self._clean(), "check", "--json")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-500:])

    def test_single_part_never_exits_zero(self):
        """То же правило, что у планки: одна проверка не покупает «разложен».
        Иначе `crew.py zones` за долю секунды печатает успех, не посмотрев ни
        волны, ни ярус, ни полёт."""
        p = self._run(self._clean(), "zones", "--json")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-500:])
        self.assertIn("остальные не смотрели", p.stdout)

    def test_broken_part_exits_one(self):
        s = state(T("01", 1, zone=["src/bot/"], started="2026-08-14T10:00:00+00:00"),
                  T("02", 1, zone=["src/bot/x/"], started="2026-08-14T10:00:03+00:00"))
        self.assertEqual(self._run(s, "zones", "--json").returncode, 1)

    def test_no_tasks_is_unknown(self):
        self.assertEqual(self._run({"waves": {}}, "check", "--json").returncode, 2)

    def test_bad_call_exits_three(self):
        self.assertEqual(self._run(self._clean(), "нетакое", "--json").returncode, 3)

    def test_declared_flag_value_is_not_taken_for_a_path(self):
        p = self._run(self._clean(), "check", "--declared", "T1", "--json")
        self.assertNotIn("вызов: crew.py", p.stderr)


if __name__ == "__main__":
    unittest.main()


class TestAVanishedBuilderIsNotWork(unittest.TestCase):
    """Исполнитель, не вернувшийся вовсе, выглядит как работа, которая идёт.

    Найдено на живом прогоне и найдено глазами, а не механизмом: субагент был
    запущен, завершился, не вернул контракт и не создал ни одного файла. Таск
    остался `running` с отметкой старта — и остался бы ею навсегда.

    Ни одна другая проверка этого не видит. Контракт возврата разбирает
    ВЕРНУВШИЙСЯ блок; гейт верификации — код, которого нет; расчёт волн —
    разброс отметок у тех, кто стартовал.
    """

    def _now(self):
        from datetime import datetime, timezone
        return datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    def _task(self, minutes: int, status: str = "running") -> dict:
        from datetime import timedelta
        return {"id": "01", "status": status, "zone": ["src/"],
                "started": (self._now() - timedelta(minutes=minutes)).isoformat()}

    def test_a_long_silent_task_is_reported(self):
        self.assertEqual(cr.stalled([self._task(55)], self._now()),
                         [{"id": "01", "minutes": 55}])

    def test_a_task_still_within_the_budget_is_not(self):
        """Порог щедрый намеренно: медленная работа не должна выглядеть
        как пропавшая, иначе проверку начнут игнорировать."""
        self.assertEqual(cr.stalled([self._task(9)], self._now()), [])

    def test_a_finished_task_is_never_stalled(self):
        self.assertEqual(cr.stalled([self._task(600, "proven")], self._now()), [])

    def test_a_running_task_without_a_stamp_is_not_guessed_about(self):
        """Без отметки старта возраст неизвестен, а выдуманный возраст хуже
        отсутствующего: он превращает догадку в отчёт."""
        self.assertEqual(cr.stalled([{"id": "01", "status": "running"}],
                                    self._now()), [])

    def test_the_check_reports_it_as_a_problem(self):
        state = {"waves": {"1": [self._task(55)]}}
        v = cr.check(state)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("молчит" in p for p in v["problems"]), v["problems"])
