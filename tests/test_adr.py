#!/usr/bin/env python3
"""Архитектурные решения: по ярусу, с отвергнутым вариантом, и в них СМОТРЯТ.

Заказчик поставил условие точно: «если проект крупный — ARCHITECTURE и DESIGN
нужны, и важно, чтобы на них смотрели». Второе и есть трудная половина.

У AutoPilot ADR пишутся и дальше живут надеждой — там прямо сказано: «читай
один, когда решение вот-вот отменят», то есть вспомни о папке ровно в тот
момент, когда уверен, что она не нужна. Документ, который никто не перечитывает,
хуже отсутствующего: он выглядит источником правды и тихо расходится с кодом.

Здесь три механизма вместо надежды:

  · решение владеет ЗОНОЙ, и передача таска вкладывает его в промпт того, кто
    собрался в этой зоне писать — увидеть его он обязан, потому что оно придёт
    вместе с заданием;
  · проект, объявивший каталог решений, не может передать таск без него —
    «забыл флаг» означает ровно то же, что «решения нет»;
  · зона, которой больше нет в репозитории, делает решение протухшим, и это
    красное, а не примечание.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from paths import at

ADR = at("tools", "adr.py")
_a = importlib.util.spec_from_file_location("superstack_adr", ADR)
ad = importlib.util.module_from_spec(_a)
_a.loader.exec_module(ad)

HANDOFF = at("tools", "handoff.py")
_h = importlib.util.spec_from_file_location("superstack_handoff_adr", HANDOFF)
ho = importlib.util.module_from_spec(_h)
_h.loader.exec_module(ho)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dir = self.root / "docs" / "adr"
        (self.root / "src" / "bot").mkdir(parents=True)

    def write(self, n=1, *, zone="src/bot/", status="accepted", rejected="- Postgres — нужен сервер",
              serves="R01", name=None):
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / (name or f"{n:04d}-hranenie.md")
        p.write_text(
            f"---\nzone: [{zone}]\nserves: [{serves}]\nstatus: {status}\n---\n"
            f"# {n:04d} — Заявки хранятся в SQLite\n\n## Контекст\n\nнадо где-то хранить\n\n"
            f"## Решение\n\nSQLite\n\n## Почему\n\nнет сервера\n\n### Отвергнуто\n\n"
            f"{rejected}\n\n## Последствия\n\nпереезд при росте\n", encoding="utf-8")
        return p

    def check(self, **kw):
        adrs, bad = ad.read_all(self.dir)
        return ad.check(adrs, bad, self.root, kw.get("tier"), kw.get("manifest"))


class TestARejectedOptionIsMandatory(Base):
    """Код показывает, ЧТО выбрали, и молчит о том, что рассмотрели и не взяли."""

    def test_new_refuses_without_a_rejected_option(self):
        with self.assertRaises(ValueError) as cm:
            ad.new(self.dir, "SQLite", zone=["src/bot/"], serves=["R01"],
                   context="a", decision="b", because="c", rejected=[],
                   consequences="d")
        self.assertIn("без отвергнутого варианта", str(cm.exception))

    def test_new_refuses_a_blank_rejected_option(self):
        with self.assertRaises(ValueError):
            ad.new(self.dir, "SQLite", zone=["src/bot/"], serves=["R01"],
                   context="a", decision="b", because="c", rejected=["   "],
                   consequences="d")

    def test_new_refuses_without_a_zone(self):
        """Решение без зоны нечем вложить в промпт — оно снова живёт надеждой."""
        with self.assertRaises(ValueError) as cm:
            ad.new(self.dir, "SQLite", zone=[], serves=["R01"], context="a",
                   decision="b", because="c", rejected=["Postgres — сервер"],
                   consequences="d")
        self.assertIn("нечем вложить", str(cm.exception))

    def test_missing_rejected_section_is_caught_on_check(self):
        self.dir.mkdir(parents=True)
        (self.dir / "0001-x.md").write_text(
            "---\nzone: [src/bot/]\nserves: [R01]\nstatus: accepted\n---\n"
            "# 0001 — Решение\n\n## Почему\n\nпотому\n", encoding="utf-8")
        v = self.check(tier="T2")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("Отвергнуто" in b for b in v["broken"]), v)

    def test_empty_rejected_section_is_caught(self):
        self.write(rejected="")
        v = self.check(tier="T2")
        self.assertTrue(any("пуст" in b for b in v["broken"]), v)

    def test_numbering_increments(self):
        ad.new(self.dir, "первое", zone=["src/bot/"], serves=["R01"], context="a",
               decision="b", because="c", rejected=["x — почему нет"], consequences="d")
        p = ad.new(self.dir, "второе", zone=["src/bot/"], serves=["R01"], context="a",
                   decision="b", because="c", rejected=["y — почему нет"], consequences="d")
        self.assertTrue(p.name.startswith("0002-"), p.name)


class TestTierDecidesWhetherTheyAreNeeded(Base):
    """Для лендинга решать нечего; для приложения решают один раз и дорого."""

    def test_large_project_without_any_decision_is_red(self):
        v = self.check(tier="T3")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("решений нет ни одного" in b for b in v["broken"]), v)

    def test_small_project_needs_none(self):
        for tier in ("T0", "T1"):
            with self.subTest(tier=tier):
                v = self.check(tier=tier)
                self.assertFalse([b for b in v["broken"] if "решений нет" in b])

    def test_missing_tier_is_unmeasured_not_clean(self):
        v = self.check()
        self.assertTrue(any("ярус не передан" in u for u in v["unmeasured"]), v)


class TestStaleDecisionIsRed(Base):

    def test_vanished_zone_makes_the_decision_stale(self):
        self.write()
        (self.root / "src" / "bot").rmdir()
        v = self.check(tier="T2")
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("протухло" in b for b in v["broken"]), v)

    def test_existing_zone_is_clean(self):
        self.write()
        v = self.check(tier="T2", manifest={"requirements": []})
        self.assertEqual(v["status"], "pass", v)

    def test_duplicate_numbers_are_caught(self):
        self.write(1, name="0001-a.md")
        self.write(1, name="0001-b.md")
        v = self.check(tier="T2")
        self.assertTrue(any("повторяются" in b for b in v["broken"]), v)

    def test_superseded_by_a_nonexistent_decision_is_caught(self):
        self.write(1, status="superseded-by-0099")
        v = self.check(tier="T2")
        self.assertTrue(any("которого нет" in b for b in v["broken"]), v)

    def test_malformed_filename_is_named(self):
        self.dir.mkdir(parents=True)
        (self.dir / "решение.md").write_text("---\n---\n# x\n", encoding="utf-8")
        v = self.check(tier="T2")
        self.assertTrue(any("не по форме" in b for b in v["broken"]), v)


class TestBuildFindingsMustSurvive(Base):
    """`D##` — дорога, уже пройденная и найденная закрытой. Без записи по ней
    пойдут снова, и это самый дорогой вид переоткрытия."""

    def test_discovered_row_without_a_decision_is_red(self):
        self.write(serves="R01")
        m = {"requirements": [{"id": "D01", "kind": "discovered"}]}
        v = self.check(tier="T2", manifest=m)
        self.assertTrue(any("находки сборки без решения" in b for b in v["broken"]), v)

    def test_discovered_row_with_a_decision_passes(self):
        self.write(serves="R01, D01")
        m = {"requirements": [{"id": "D01", "kind": "discovered"}]}
        self.assertEqual(self.check(tier="T2", manifest=m)["status"], "pass")

    def test_missing_manifest_is_unmeasured(self):
        self.write()
        v = self.check(tier="T2")
        self.assertTrue(any("манифест не передан" in u for u in v["unmeasured"]), v)


class TestTheDecisionReachesWhoeverWritesInItsZone(Base):
    """Трудная половина условия заказчика: «важно, чтобы на него смотрели»."""

    def _task(self, zone=("src/bot/",)):
        return {"id": "01", "name": "приём", "goal": "клиент пишет боту",
                "requirements": ["R01"], "zone": list(zone),
                "acceptance": ["диалог доходит до подтверждения"]}

    def test_governing_decision_is_embedded_in_the_prompt(self):
        self.write()
        adrs = ho.read_adrs(self.dir)
        p = ho.build({"waves": {"1": [self._task()]}}, self._task(), "x", "",
                     "npm test", adrs)
        self.assertIn("правят твоей зоной", p)
        self.assertIn("Заявки хранятся в SQLite", p)

    def test_nested_zone_is_governed_too(self):
        self.write(zone="src/")
        self.assertEqual(len(ho.governing_adrs(ho.read_adrs(self.dir),
                                               ["src/bot/intake/"])), 1)

    def test_unrelated_zone_is_not_governed(self):
        self.write(zone="src/admin/")
        self.assertEqual(ho.governing_adrs(ho.read_adrs(self.dir), ["src/bot/"]), [])

    def test_superseded_decision_is_not_handed_down(self):
        """Отменённое решение в промпте хуже отсутствующего: исполнитель будет
        соблюдать то, что уже пересмотрели."""
        self.write(status="superseded-by-0002")
        self.write(2, name="0002-novoe.md")
        got = ho.governing_adrs(ho.read_adrs(self.dir), ["src/bot/"])
        self.assertEqual([a["file"] for a in got], ["0002-novoe.md"])

    def test_declaring_decisions_and_not_passing_them_is_refused(self):
        """Лазейка, закрытая явно: «забыл флаг» означает ровно то же, что
        «решения нет» — исполнитель пишет в зоне, не увидев её решения."""
        self.write()
        st = {"adr_dir": "docs/adr", "waves": {"1": [self._task()]}}
        bad = ho.blockers(st, self._task(), "x", "", "npm test", None)
        self.assertTrue(any("объявил решения" in b for b in bad), bad)

    def test_passing_them_clears_the_refusal(self):
        self.write()
        st = {"adr_dir": "docs/adr", "waves": {"1": [self._task()]}}
        self.assertEqual(ho.blockers(st, self._task(), "x", "", "npm test", self.dir), [])

    def test_project_without_decisions_is_not_forced_to_have_them(self):
        st = {"waves": {"1": [self._task()]}}
        self.assertEqual(ho.blockers(st, self._task(), "x", "", "npm test", None), [])


class TestTwoReadersDoNotDrift(Base):
    """`handoff.py` разбирает заголовок решения СВОИМ кодом — импортировать
    соседний пакет значит закладываться на то, как маркетплейс разложит плагины,
    а это сломается молча при установке у человека.

    Дублирование допустимо только пока оно проверяемо: если два читателя
    разойдутся в том, какой зоной правит решение, одно из двух — проверка или
    вложение в промпт — начнёт работать не на тех файлах, и обнаружится это
    очень нескоро.
    """

    def test_both_readers_extract_the_same_zones(self):
        self.write(1, zone="src/bot/", name="0001-a.md")
        self.write(2, zone="src/admin/, src/shared/", name="0002-b.md")
        self.write(3, zone="src/bot/", status="superseded-by-0001", name="0003-c.md")
        mine, _ = ad.read_all(self.dir)
        theirs = ho.read_adrs(self.dir)
        self.assertEqual([a["file"] for a in mine], [a["file"] for a in theirs])
        self.assertEqual([a["zone"] for a in mine], [a["zone"] for a in theirs])
        self.assertEqual([a["status"] for a in mine], [a["status"] for a in theirs])

    def test_both_readers_reject_the_same_filenames(self):
        self.dir.mkdir(parents=True)
        for bad in ("решение.md", "1-x.md", "0001_x.md", "0001-.md"):
            (self.dir / bad).write_text("---\nzone: [src/]\n---\n# x\n",
                                        encoding="utf-8")
        self.write(1, name="0001-ok.md")
        mine, _ = ad.read_all(self.dir)
        theirs = ho.read_adrs(self.dir)
        self.assertEqual([a["file"] for a in mine], ["0001-ok.md"])
        self.assertEqual([a["file"] for a in theirs], ["0001-ok.md"])


if __name__ == "__main__":
    unittest.main()
