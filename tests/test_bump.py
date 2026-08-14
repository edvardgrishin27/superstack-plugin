#!/usr/bin/env python3
"""Подъём версии набора.

Почему это вообще проверяется тестом: каталог установки назван версией, и
пропущенное место означает пакет, который не обновится. Набор, разъехавшийся по
версиям, хуже необновлённого — часть скиллов начнёт работать по инструментам
другой версии, и ничего при этом не упадёт.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("ss_bump", REPO / "tools" / "bump.py")
bump = importlib.util.module_from_spec(_s)
_s.loader.exec_module(bump)


class TestBump(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".claude-plugin").mkdir(parents=True)
        self._market(["a", "b"], "0.2.1")
        for n in ("a", "b"):
            self._plugin(n, "0.2.1")
        self._old = (bump.REPO, bump.MARKET)
        bump.REPO = self.root
        bump.MARKET = self.root / ".claude-plugin" / "marketplace.json"
        self.addCleanup(self._restore)

    def _restore(self):
        bump.REPO, bump.MARKET = self._old

    def _plugin(self, name, ver):
        d = self.root / "plugins" / f"superstack-{name}" / ".claude-plugin"
        d.mkdir(parents=True, exist_ok=True)
        (d / "plugin.json").write_text(
            json.dumps({"name": f"superstack-{name}", "version": ver}),
            encoding="utf-8")

    def _market(self, names, ver):
        (self.root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "superstack", "plugins": [
                {"name": f"superstack-{n}", "source": f"./plugins/superstack-{n}",
                 "version": ver} for n in names]}), encoding="utf-8")

    def _versions(self):
        pl = {p.parts[-3]: json.loads(p.read_text("utf-8"))["version"]
              for p in bump.manifests()}
        mk = {e["name"]: e["version"] for e in
              json.loads(bump.MARKET.read_text("utf-8"))["plugins"]}
        return pl, mk

    def test_patch_moves_every_manifest_and_every_entry(self):
        bump.write("0.2.2", dry=False)
        pl, mk = self._versions()
        self.assertEqual(set(pl.values()), {"0.2.2"})
        self.assertEqual(set(mk.values()), {"0.2.2"},
                         "запись маркетплейса осталась на прежней версии — "
                         "движок сравнивает именно её и решит, что обновлять "
                         "нечего")

    def test_a_split_set_is_refused_not_quietly_aligned(self):
        """Разошедшиеся версии — признак, что набор собран не обычным путём.
        Молча выровнять значит стереть этот признак."""
        self._plugin("b", "0.2.0")
        now, seen = bump.current()
        self.assertIsNone(now)
        self.assertIn("0.2.0", set(seen.values()))

    def test_going_backwards_is_refused(self):
        """Откат назад выглядит успешным и не доходит до тех, у кого стоит
        новее: движок обновляет только вперёд."""
        self.assertFalse(bump.newer("0.2.0", "0.2.1"))
        self.assertTrue(bump.newer("0.2.2", "0.2.1"))

    def test_versions_compare_as_numbers_not_strings(self):
        self.assertTrue(bump.newer("0.10.0", "0.9.0"),
                        "0.10.0 проиграл 0.9.0 — сравнение строками")

    def test_dry_run_writes_nothing(self):
        before = json.loads((self.root / "plugins" / "superstack-a" /
                             ".claude-plugin" / "plugin.json").read_text("utf-8"))
        bump.write("0.9.9", dry=True)
        after = json.loads((self.root / "plugins" / "superstack-a" /
                            ".claude-plugin" / "plugin.json").read_text("utf-8"))
        self.assertEqual(before, after)

    def test_next_patch(self):
        self.assertEqual(bump.nxt("0.2.1"), "0.2.2")
        self.assertEqual(bump.nxt("0.9.9"), "0.9.10")


class TestTheRealRepoIsOnOneVersion(unittest.TestCase):
    """Обратный контроль на живом дереве: пакеты и записи маркетплейса стоят на
    одной версии. Расхождение здесь означает половину набора, которая не
    обновится у людей."""

    def test_all_packages_and_entries_agree(self):
        pl = {p.parts[-3]: json.loads(p.read_text("utf-8")).get("version")
              for p in sorted(REPO.glob("plugins/*/.claude-plugin/plugin.json"))}
        mk = {e["name"]: e.get("version") for e in json.loads(
            (REPO / ".claude-plugin" / "marketplace.json").read_text("utf-8")
        )["plugins"]}
        self.assertEqual(len(set(pl.values())), 1, f"пакеты разошлись: {pl}")
        self.assertEqual(set(mk.values()), set(pl.values()),
                         f"маркетплейс разошёлся с пакетами: {mk} vs {pl}")


if __name__ == "__main__":
    unittest.main()
