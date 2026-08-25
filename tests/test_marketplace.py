#!/usr/bin/env python3
"""Маркетплейс: объявленное против существующего, в обе стороны.

Найдено при попытке впервые установить продукт. Манифест объявлял ровно один
плагин `./plugins/superstack`, каталога с таким именем не существовало, а семь
настоящих пакетов не были объявлены вовсе. Ворота «манифест» при этом были
зелёными: `claude plugin validate .` валидирует ТОЛЬКО корневой файл и молчит
о том, существует ли каталог из `source`.

Следствие: **продукт не устанавливался с момента разделения на семь** — и
именно поэтому сломанные пути внутри скиллов прожили несколько заходов
незамеченными. Их некому было выполнить.

Второй пласт того же: движок 2.1.42 отвергает ключ `dependencies` как
неизвестный, из-за чего шесть манифестов из семи были невалидны. Корневая
валидация об этом не знала, потому что пакеты не проверялись поштучно.

ЛОВУШКА ЭТИХ ТЕСТОВ, названная вслух. На исправленном репозитории проверке
нечего находить: все разности пусты. Мутация вида `if bad:` → `if False:`
выжила бы, ничего не сломав. Поэтому каждый тест строит ПОДДЕЛЬНОЕ дерево, где
дефект есть, и убеждается, что вердикт красный.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from paths import REPO

_g = importlib.util.spec_from_file_location("gauntlet_mk", REPO / "tools" / "gauntlet.py")
gt = importlib.util.module_from_spec(_g)
_g.loader.exec_module(gt)


class Tree(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".claude-plugin").mkdir(parents=True)
        (self.root / "plugins").mkdir()
        self._orig = gt.PLUG
        gt.PLUG = self.root
        self.addCleanup(setattr, gt, "PLUG", self._orig)

    def pkg(self, name, version="0.2.0", pkg_name=None, manifest=True, **extra):
        d = self.root / "plugins" / name
        (d / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        if manifest:
            body = {"name": pkg_name or name, "version": version}
            body.update(extra)
            (d / ".claude-plugin" / "plugin.json").write_text(
                json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return d

    def market(self, *entries):
        (self.root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "t", "owner": {"name": "x"},
                        "plugins": list(entries)}, ensure_ascii=False),
            encoding="utf-8")

    def entry(self, name, version="0.2.0", source=None):
        return {"name": name, "source": source or f"./plugins/{name}",
                "version": version}


class TestDeclaredMustExist(Tree):

    def test_source_pointing_at_nothing_is_red(self):
        """Ровно то состояние, в котором продукт прожил несколько заходов."""
        self.market(self.entry("superstack"))
        v = gt.marketplace_crosscheck()
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("не существует" in m for m in v["mismatches"]), v)

    def test_directory_without_a_manifest_is_red(self):
        self.pkg("a", manifest=False)
        self.market(self.entry("a"))
        v = gt.marketplace_crosscheck()
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("нет .claude-plugin" in m for m in v["mismatches"]), v)

    def test_matching_pair_passes(self):
        self.pkg("a")
        self.market(self.entry("a"))
        self.assertEqual(gt.marketplace_crosscheck()["status"], "pass")


class TestExistingMustBeDeclared(Tree):
    """Обратное направление — оно и пропустило семь пакетов разом."""

    def test_undeclared_package_is_red(self):
        self.pkg("a"); self.pkg("b")
        self.market(self.entry("a"))
        v = gt.marketplace_crosscheck()
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("записи в маркетплейсе нет" in m for m in v["mismatches"]), v)

    def test_orphan_directory_is_unknown_not_clean(self):
        """Каталог без манифеста выпадает из ОБЕИХ разностей сразу, и проверка
        зеленела бы на пустоте. Это «разберись», а не «всё хорошо»."""
        self.pkg("a")
        self.market(self.entry("a"))
        (self.root / "plugins" / "мусор").mkdir()
        v = gt.marketplace_crosscheck()
        self.assertEqual(v["status"], "unknown")
        self.assertIn("без манифеста", v["detail"])


class TestIdentityAndVersion(Tree):

    def test_name_mismatch_is_red(self):
        """Ключ установки `X@маркетплейс` указывал бы на пакет, зовущийся иначе."""
        self.pkg("a", pkg_name="совсем-другое")
        self.market(self.entry("a"))
        v = gt.marketplace_crosscheck()
        self.assertTrue(any("представляется как" in m for m in v["mismatches"]), v)

    def test_version_drift_is_red(self):
        """Версия записи — это имя каталога установки и точка сравнения при
        обновлении. Разойдясь с пакетом, она удовлетворяет semver не тем кодом."""
        self.pkg("a", version="0.3.0")
        self.market(self.entry("a", version="0.2.0"))
        v = gt.marketplace_crosscheck()
        self.assertTrue(any("версия записи" in m for m in v["mismatches"]), v)

    def test_duplicate_entry_is_red(self):
        self.pkg("a")
        self.market(self.entry("a"), self.entry("a"))
        v = gt.marketplace_crosscheck()
        self.assertTrue(any("дважды" in m for m in v["mismatches"]), v)


class TestDeclaredFieldShadowsTheDirectory(Tree):
    """Движок берёт дефолтный каталог ТОЛЬКО когда поля нет. Файл, лежащий
    рядом и не названный, не грузится никогда, и ошибки при этом нет —
    так уже потерялся агент слепой приёмки: построен, протестирован, мёртв."""

    def test_unnamed_agent_is_red(self):
        d = self.pkg("a", agents=["./agents/one.md"])
        (d / "agents").mkdir()
        (d / "agents" / "one.md").write_text("x", encoding="utf-8")
        (d / "agents" / "two.md").write_text("x", encoding="utf-8")
        self.market(self.entry("a"))
        v = gt.marketplace_crosscheck()
        self.assertTrue(any("незаявленное" in m for m in v["mismatches"]), v)

    def test_naming_them_all_passes(self):
        d = self.pkg("a", agents=["./agents/one.md", "./agents/two.md"])
        (d / "agents").mkdir()
        for n in ("one.md", "two.md"):
            (d / "agents" / n).write_text("x", encoding="utf-8")
        self.market(self.entry("a"))
        self.assertEqual(gt.marketplace_crosscheck()["status"], "pass")

    def test_no_field_at_all_is_fine(self):
        """Поля нет — движок берёт каталог сам, и это законно."""
        d = self.pkg("a")
        (d / "agents").mkdir()
        (d / "agents" / "one.md").write_text("x", encoding="utf-8")
        self.market(self.entry("a"))
        self.assertEqual(gt.marketplace_crosscheck()["status"], "pass")


class TestTheRealRepositoryAgrees(unittest.TestCase):
    """Не цель мутаций, а утверждение о продукте: он устанавливаем сегодня."""

    def test_current_marketplace_is_consistent(self):
        v = gt.marketplace_crosscheck()
        self.assertEqual(v["status"], "pass", v)

    def test_no_manifest_declares_dependencies(self):
        """Движок 2.1.42 отвергает этот ключ, и с ним НИ ОДИН пакет не проходит
        валидацию — то есть продукт не ставится вовсе. Проверено запуском
        `claude plugin validate`, а не чтением документации."""
        for p in sorted((REPO / "plugins").glob("*/.claude-plugin/plugin.json")):
            with self.subTest(pkg=p.parent.parent.name):
                self.assertNotIn("dependencies",
                                 json.loads(p.read_text("utf-8")))

    def test_no_tool_resolves_siblings_through_the_manifest(self):
        """Проводка соседей через `dependencies` умерла вместе с полем. Тот,
        кто её вернёт, сломает импорт молча — как это и случилось."""
        for p in sorted((REPO / "plugins").glob("*/tools/*.py")):
            with self.subTest(tool=p.name):
                self.assertNotIn('get("dependencies"', p.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
