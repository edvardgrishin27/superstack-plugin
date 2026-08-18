#!/usr/bin/env python3
"""Тесты базовой линии.

Что именно эти тесты держат.

Инструмент существует ради одного утверждения — «стало хуже» или «не стало».
Поэтому здесь проверяется не форма вывода, а ПОВЕДЕНИЕ: какой код возврата
получает вызывающий, когда метрика выросла в дурную сторону; и, что важнее,
какой код он получает, когда метрику НЕ УДАЛОСЬ сравнить. Второе — главная
ловушка: «не нашёл» и «не смог проверить» обязаны быть разными утверждениями,
иначе исчезнувшая метрика читается как «ну значит не ухудшилось», и замер
начинает врать ровно в тот момент, когда он и нужен.

Переносимость. Ни один тест не читает настоящий ~/.claude, сеть, npm и часы:
факты и находки подаются файлами, метка снимка — параметром, каталог хранения —
временный. Проверка флага паузы гоняется на подставном HOME, а не на своём.

Ожидаемые числа в тестах написаны РУКАМИ и посчитаны глазами по входному
файлу. Ни одно из них не берётся из baseline.py — иначе тест сверял бы код
сам с собой.
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
from paths import REPO, at  # noqa: E402

ROOT = REPO
TOOL = at("tools", "baseline.py")
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
       "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Регистрация до исполнения обязательна: dataclass ищет свой модуль в
    # sys.modules и без этого падает на разборе аннотаций.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bl = _load("ss_baseline", TOOL)


# --- вход: факты и находки собираются руками, а не берутся с машины --------

def facts(**over) -> dict:
    """Факты в формате probe/collect.py. Значения по умолчанию — «здоровая»
    машина; тест меняет ровно то, что проверяет."""
    base = {
        "inv.skills.listing_chars": 12000,
        "inv.skills.over_budget_ratio": 1.0,
        "inv.skills.count": 20,
        "ev.skills_with_tests": 5,
        "hooks.manifest.count": 4,
        "hooks.wired.count": 4,
        "hooks.dormant.count": 0,
        "mem.largest_index_bytes": 4096,
    }
    base.update(over)
    return {k: {"value": v, "provenance": "EXTRACTED"} for k, v in base.items()}


def findings(sevs=(), trustworthy=True) -> dict:
    return {"findings": [{"id": f"R{i}", "severity": s} for i, s in enumerate(sevs)],
            "coverage": {"trustworthy": trustworthy}}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.store = self.d / "store"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, data) -> Path:
        p = self.d / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def run_tool(self, *args, env=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, timeout=120,
                              env=env or ENV)

    def snapshot(self, stamp: str, f=None, g=None, store=None, extra=()):
        args = ["snapshot", "--dir", str(store or self.store), "--stamp", stamp, "--json"]
        if f is not None:
            args += ["--facts", str(self.write(f"facts-{stamp}.json", f))]
        if g is not None:
            args += ["--findings", str(self.write(f"find-{stamp}.json", g))]
        args += list(extra)
        p = self.run_tool(*args)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def diff(self, before: str, after: str, store=None):
        p = self.run_tool("diff", "--dir", str(store or self.store), "--json",
                          before, after)
        return p


# --------------------------------------------------------------------------
# замер
# --------------------------------------------------------------------------

class TestSnapshotMeasures(Fixture):
    def test_facts_and_findings_turn_into_named_metrics(self):
        """Числа замера — те же, что в пробах и находках, а не пересчитанные.

        Ожидания посчитаны руками по входу: находок пять, из них одна critical,
        две high, одна low и одна с тяжестью, которой нет в списке.
        """
        snap = self.snapshot(
            "A",
            f=facts(**{"inv.skills.listing_chars": 74000, "inv.skills.count": 283,
                       "hooks.dormant.count": 3, "mem.largest_index_bytes": 51200}),
            g=findings(("critical", "high", "high", "low", "странная")))
        m = snap["metrics"]
        self.assertEqual(m["skills.listing_chars"]["value"], 74000)
        self.assertEqual(m["skills.count"]["value"], 283)
        self.assertEqual(m["hooks.dormant"]["value"], 3)
        self.assertEqual(m["memory.index_bytes"]["value"], 51200)
        self.assertEqual(m["findings.critical"]["value"], 1)
        self.assertEqual(m["findings.high"]["value"], 2)
        self.assertEqual(m["findings.medium"]["value"], 0)
        self.assertEqual(m["findings.low"]["value"], 1)
        # Находка с незнакомой тяжестью не пропадает: она видна в итоге.
        self.assertEqual(m["findings.total"]["value"], 5)

    def test_absent_fact_is_named_unmeasured_and_not_zero(self):
        """Пропавшая проба обязана называться, а не превращаться в ноль.

        Ноль вместо пропуска — это «мы измерили и там пусто». Здесь не
        измеряли вовсе, и следующий diff обязан это унаследовать.
        """
        f = facts()
        del f["mem.largest_index_bytes"]
        snap = self.snapshot("A", f=f)
        self.assertNotIn("memory.index_bytes", snap["metrics"])
        named = {u["metric"]: u["reason"] for u in snap["unmeasured"]}
        self.assertIn("memory.index_bytes", named)
        self.assertIn("mem.largest_index_bytes", named["memory.index_bytes"])

    def test_stamp_names_the_file_and_comes_from_outside(self):
        self.snapshot("2026-01-01T00-00-00Z", f=facts())
        self.assertTrue((self.store / "2026-01-01T00-00-00Z.json").is_file())

    def test_stamp_cannot_escape_the_store(self):
        """Метка едет в имя файла, значит она — вход, которому нельзя верить."""
        p = self.run_tool("snapshot", "--dir", str(self.store),
                          "--stamp", "../evil", "--json",
                          "--facts", str(self.write("f.json", facts())))
        self.assertEqual(p.returncode, 3)
        self.assertFalse((self.d / "evil.json").exists())

    def test_existing_baseline_is_not_overwritten(self):
        """Базовая линия не затирается: точка отсчёта, которую переписали,
        перестаёт быть точкой отсчёта."""
        self.snapshot("A", f=facts())
        before = (self.store / "A.json").read_bytes()
        p = self.run_tool("snapshot", "--dir", str(self.store), "--stamp", "A",
                          "--json", "--facts", str(self.write("f2.json", facts())))
        self.assertEqual(p.returncode, 3)
        self.assertEqual((self.store / "A.json").read_bytes(), before)

    def test_no_source_is_a_call_error(self):
        p = self.run_tool("snapshot", "--dir", str(self.store), "--stamp", "A", "--json")
        self.assertEqual(p.returncode, 3)

    def test_broken_source_refuses_instead_of_empty_snapshot(self):
        """Битый файл фактов — отказ вслух. Пустой замер вместо него дал бы
        снимок без метрик, и следующий diff назвал бы это «не хуже»."""
        bad = self.d / "bad.json"
        bad.write_text("{ это не json", encoding="utf-8")
        p = self.run_tool("snapshot", "--dir", str(self.store), "--stamp", "A",
                          "--json", "--facts", str(bad))
        self.assertEqual(p.returncode, 3)
        self.assertFalse((self.store / "A.json").exists())


# --------------------------------------------------------------------------
# сравнение: вердикт
# --------------------------------------------------------------------------

class TestDiffVerdict(Fixture):
    def test_growing_listing_cost_is_worse_and_code_1(self):
        self.snapshot("A", f=facts(**{"inv.skills.listing_chars": 12000}))
        self.snapshot("B", f=facts(**{"inv.skills.listing_chars": 74000}))
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 1, p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(d["status"], "worse")
        worse = {r["metric"]: r for r in d["worse"]}
        self.assertIn("skills.listing_chars", worse)
        self.assertEqual(worse["skills.listing_chars"]["delta"], 62000)
        self.assertEqual(worse["skills.listing_chars"]["verdict"], "ОТКАТИТЬ")

    def test_shrinking_listing_cost_is_not_worse_and_code_0(self):
        self.snapshot("A", f=facts(**{"inv.skills.listing_chars": 74000}))
        self.snapshot("B", f=facts(**{"inv.skills.listing_chars": 12000}))
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "better")

    def test_dormant_hooks_growth_is_worse(self):
        """Хук объявлен и не подключён: документация обещает механизм,
        которого нет. Рост этого разрыва — регрессия."""
        self.snapshot("A", f=facts(**{"hooks.dormant.count": 0}))
        self.snapshot("B", f=facts(**{"hooks.dormant.count": 2}))
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("hooks.dormant", [r["metric"] for r in json.loads(p.stdout)["worse"]])

    def test_memory_index_growth_is_worse(self):
        self.snapshot("A", f=facts(**{"mem.largest_index_bytes": 4096}))
        self.snapshot("B", f=facts(**{"mem.largest_index_bytes": 51200}))
        self.assertEqual(self.diff("A", "B").returncode, 1)

    def test_new_critical_finding_is_worse(self):
        self.snapshot("A", f=facts(), g=findings(("low",)))
        self.snapshot("B", f=facts(), g=findings(("low", "critical")))
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 1, p.stderr)
        names = [r["metric"] for r in json.loads(p.stdout)["worse"]]
        self.assertIn("findings.critical", names)
        self.assertIn("findings.total", names)

    def test_losing_tested_skills_is_worse(self):
        """Единственная метрика, где падение — регрессия: скилл без теста
        нельзя проверить, и его «улучшение» никем не измеряется."""
        self.snapshot("A", f=facts(**{"ev.skills_with_tests": 5}))
        self.snapshot("B", f=facts(**{"ev.skills_with_tests": 2}))
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 1, p.stderr)
        row = {r["metric"]: r for r in json.loads(p.stdout)["worse"]}["skills.with_tests"]
        self.assertEqual(row["delta"], -3)

    def test_watch_metric_never_produces_a_verdict(self):
        """Число скиллов — наблюдение, а не приговор. Скилл, заменивший три
        удалённых, поднимет счётчик и опустит стоимость; вердикт «хуже» на
        таком росте объявил бы регрессией правильную правку."""
        self.snapshot("A", f=facts(**{"inv.skills.count": 20}))
        self.snapshot("B", f=facts(**{"inv.skills.count": 283}))
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertNotEqual(d["status"], "worse")
        self.assertIn("skills.count", [r["metric"] for r in d["watch"]])
        self.assertEqual(d["worse"], [])


# --------------------------------------------------------------------------
# сравнение: непроверенное
# --------------------------------------------------------------------------

class TestUnmeasuredIsNotClean(Fixture):
    def test_metric_lost_between_snapshots_is_named_and_kills_trust(self):
        """ГЛАВНАЯ ловушка. Метрика была и исчезла — это не «не ухудшилось».

        Код возврата остаётся нулём (по измеренному хуже не стало), но замер
        обязан быть помечен как неполный и метрика названа поимённо. Иначе
        сломавшаяся проба выглядит как чистый результат.
        """
        f = facts()
        broken = facts()
        del broken["mem.largest_index_bytes"]
        self.snapshot("A", f=f)
        self.snapshot("B", f=broken)
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertFalse(d["trustworthy"])
        self.assertIn("memory.index_bytes", [u["metric"] for u in d["unmeasured"]])
        self.assertTrue(d["trust_reasons"])

    def test_incomplete_rule_coverage_kills_trust(self):
        """Находки, снятые при неполном охвате правил, — это «сколько успели
        посчитать», а не «сколько их». Сравнивать можно, выдавать за чистый
        замер — нет."""
        self.snapshot("A", f=facts(), g=findings(("low",), trustworthy=True))
        self.snapshot("B", f=facts(), g=findings(("low",), trustworthy=False))
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(json.loads(p.stdout)["trustworthy"])

    def test_no_common_metrics_is_code_2_not_code_0(self):
        """Ноль сравнимых метрик — отсутствие ответа, а не ответ «не хуже»."""
        self.snapshot("A", f=facts())              # только facts.*
        self.snapshot("B", g=findings(("low",)))   # только findings.*
        p = self.diff("A", "B")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)

    def test_broken_snapshot_is_code_2(self):
        self.snapshot("A", f=facts())
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "B.json").write_text("{ огрызок", encoding="utf-8")
        self.assertEqual(self.diff("A", "B").returncode, 2)

    def test_foreign_schema_is_code_2(self):
        self.snapshot("A", f=facts())
        (self.store / "B.json").write_text(
            json.dumps({"schema": "чужое", "metrics": {}}), encoding="utf-8")
        self.assertEqual(self.diff("A", "B").returncode, 2)

    def test_missing_snapshot_is_code_3(self):
        self.snapshot("A", f=facts())
        self.assertEqual(self.diff("A", "нет-такого").returncode, 3)

    def test_direction_disagreement_is_unmeasured_not_a_guess(self):
        """Если два снимка расходятся в том, куда метрике расти, инструмент
        не выбирает сторону молча: метрика уходит в непроверенное."""
        a = bl.build_snapshot("A", facts(), None, "", None, None)
        b = bl.build_snapshot("B", facts(), None, "", None, None)
        b["metrics"]["skills.listing_chars"]["direction"] = "higher_better"
        d = bl.compare(a, b)
        self.assertIn("skills.listing_chars",
                      [u["metric"] for u in d["unmeasured"]])
        self.assertEqual(d["worse"], [])


# --------------------------------------------------------------------------
# детерминизм
# --------------------------------------------------------------------------

class TestDeterminism(Fixture):
    def test_compare_does_not_read_the_clock(self):
        """Сравнение обязано быть чистым от времени.

        Проверяется буквально: на месте `time` и `datetime` в модуле ставится
        ловушка, любое обращение к которой падает. Если сравнение когда-нибудь
        начнёт спрашивать «который час» — например, чтобы приписать выводу
        дату, — этот тест покраснеет, а не молча пустит недетерминированность.
        """
        class Trap:
            def __getattr__(self, name):
                raise AssertionError(
                    f"сравнение полезло в часы: обращение к {name}")

        a = bl.build_snapshot("A", facts(**{"inv.skills.listing_chars": 10}),
                              None, "", None, None)
        b = bl.build_snapshot("B", facts(**{"inv.skills.listing_chars": 20}),
                              None, "", None, None)
        saved_dt = bl.datetime
        saved_time = getattr(bl, "time", None)
        bl.datetime = Trap()
        bl.time = Trap()
        try:
            d = bl.compare(a, b)
        finally:
            bl.datetime = saved_dt
            if saved_time is None:
                del bl.time
            else:
                bl.time = saved_time
        self.assertEqual(d["status"], "worse")

    def test_same_stamp_and_same_input_give_identical_bytes(self):
        """Один вход и одна метка — один и тот же файл. Иначе снимок нельзя
        сверять и нельзя воспроизводить."""
        f = facts()
        g = findings(("high",))
        one, two = self.d / "s1", self.d / "s2"
        self.snapshot("A", f=f, g=g, store=one)
        self.snapshot("A", f=f, g=g, store=two)
        self.assertEqual((one / "A.json").read_bytes(), (two / "A.json").read_bytes())

    def test_snapshot_keeps_no_absolute_paths(self):
        """В снимке лежат имена файлов, а не абсолютные пути: снимок с путями
        перестал бы быть переносимым артефактом и различался бы между машинами
        при одинаковом содержании."""
        snap = self.snapshot("A", f=facts(), g=findings(("low",)))
        self.assertNotIn(str(self.d), json.dumps(snap["sources"], ensure_ascii=False))


# --------------------------------------------------------------------------
# обвязка
# --------------------------------------------------------------------------

class TestPlumbing(Fixture):
    def test_json_goes_to_stdout_and_human_text_to_stderr(self):
        self.snapshot("A", f=facts())
        self.snapshot("B", f=facts(**{"inv.skills.listing_chars": 99000}))
        p = self.run_tool("diff", "--dir", str(self.store), "A", "B")
        self.assertEqual(p.returncode, 1)
        json.loads(p.stdout)                       # stdout разбирается машиной
        self.assertIn("СТАЛО ХУЖЕ", p.stderr)      # человеку — в stderr

    def test_pause_flag_stops_the_tool(self):
        """Тормоз соблюдается, а не только попадает в отчёт.

        Флаг ставится в ПОДСТАВНОМ HOME: тест не должен ни читать, ни трогать
        настоящий ~/.claude, иначе он мерил бы машину, а не код.
        """
        home = self.d / "home"
        (home / ".claude" / "superstack").mkdir(parents=True)
        (home / ".claude" / "superstack" / "PAUSE").write_text("2026-01-01",
                                                              encoding="utf-8")
        env = {k: v for k, v in ENV.items() if k != "SUPERSTACK_IGNORE_PAUSE"}
        env["HOME"] = str(home)
        p = self.run_tool("snapshot", "--dir", str(self.store), "--stamp", "A",
                          "--json", env=env)
        self.assertEqual(p.returncode, 10, p.stderr)

    def test_bad_subcommand_is_code_3_not_code_2(self):
        """Код 2 занят смыслом «не смог сравнить». Ошибка вызова обязана
        отличаться, иначе вызывающий скрипт пойдёт чинить метрики вместо
        собственной командной строки."""
        self.assertEqual(self.run_tool("сравни-как-нибудь").returncode, 3)

    def test_tool_file_exists(self):
        """Улика для планки. Это проверка НАЛИЧИЯ файла, а не поведения —
        поведение держат тесты выше."""
        self.assertTrue(TOOL.is_file())

    def test_rollback_rule_is_stated_in_words(self):
        """ПОДСТРОЧНАЯ проверка прозы, не поведенческая — и здесь это сказано
        прямо. Правило отката обязано быть названо словами в выводе, потому
        что читатель решает по тексту, а не по коду возврата."""
        self.snapshot("A", f=facts())
        self.snapshot("B", f=facts(**{"inv.skills.listing_chars": 99000}))
        p = self.run_tool("diff", "--dir", str(self.store), "A", "B")
        self.assertIn("ОТКАТ", p.stderr.upper())
        self.assertIn("откат", json.loads(p.stdout)["rollback_rule"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
