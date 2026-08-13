#!/usr/bin/env python3
"""Тесты пачки починок из триажа 68 дефектов.

Общая нить у всех: продукт получает кривой вход — от человека, от другого
инструмента или от собственной пробы — и обязан сказать, что именно не так,
а не показать стектрейс и не выдать успокоительный вердикт.
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

ROOT = REPO
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


adj = _load("hd_adj", at("tools", "adjudicate.py"))
doctor = _load("hd_doctor", at("tools", "doctor.py"))
collect = _load("hd_collect", at("tools", "probe", "collect.py"))


def run(tool: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(at(*tool.split("/")))] + list(args),
                          capture_output=True, text=True, timeout=120,
                          cwd=str(REPO), env=ENV)


def facts_file(values: dict) -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({k: {"value": v, "probe": "t", "evidence": None,
                   "provenance": "EXTRACTED"} for k, v in values.items()},
              fh, ensure_ascii=False)
    fh.close()
    return fh.name


class TestRuleEngineEdges(unittest.TestCase):
    def test_empty_fact_key_does_not_kill_every_rule(self):
        """Один мусорный ключ обнулял весь аудит: 22 правила из 22 в skipped."""
        self.assertTrue(adj.evaluate("a.count > 0", {"": 1, "a.count": 3}))

    def test_whitespace_key_is_also_ignored(self):
        self.assertTrue(adj.evaluate("a.count > 0", {"   ": 1, "a.count": 3}))

    def test_normal_keys_still_substitute(self):
        """Обратный контроль: пропуск не должен съесть настоящие ключи."""
        self.assertTrue(adj.evaluate("cc.default_mode == 'plan'",
                                     {"cc.default_mode": "plan"}))

    def test_overlapping_globs_do_not_duplicate_findings(self):
        r = run("tools/adjudicate.py", facts_file({"host.git": False, "host.gh": False}),
                str(plug("superstack-core") / "rules" / "core.rules.json"), str(plug("superstack-core") / "rules" / "*.json"))
        data = json.loads(r.stdout)
        ids = [f["id"] for f in data["findings"]]
        self.assertEqual(len(ids), len(set(ids)), f"находки продублированы: {ids}")
        self.assertEqual(len(data["rule_files"]), len(set(data["rule_files"])))

    def test_broken_value_does_not_kill_the_whole_run(self):
        """Падение при сборке находки давало ноль отчёта вместо частичного."""
        r = run("tools/adjudicate.py",
                facts_file({"sec.secret_matches": "не-список", "cc.allow_count": 1}),
                str(plug("superstack-core") / "rules" / "*.json"))
        self.assertEqual(r.returncode, 0, r.stderr[:400])
        self.assertTrue(r.stdout, "отчёт пуст — прогон умер целиком")
        json.loads(r.stdout)


class TestMalformedInputIsNamed(unittest.TestCase):
    def test_missing_args_is_a_message_not_a_traceback(self):
        for tool in ("tools/adjudicate.py", "tools/render.py"):
            with self.subTest(tool=tool):
                r = run(tool)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("нужен", r.stderr)

    def test_missing_file_is_a_message(self):
        for tool, args in (("tools/adjudicate.py", ("/нет.json", str(plug("superstack-core") / "rules" / "*.json"))),
                           ("tools/render.py", ("/нет.json", "beginner"))):
            with self.subTest(tool=tool):
                r = run(tool, *args)
                self.assertNotIn("Traceback", r.stderr)
                self.assertIn("нет такого", r.stderr)

    def test_wrong_shape_is_named(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"а": 1}, fh)
        fh.close()
        r = run("tools/render.py", fh.name, "beginner")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("findings", r.stderr)

    def _full_facts(self) -> dict:
        """Полная фикстура: все правила вычисляются, охват чистый."""
        return json.loads((at("tests", "fixtures", "brownfield.json"))
                          .read_text(encoding="utf-8"))

    def _wrap(self, values: dict) -> dict:
        return {k: {"value": v, "probe": "t", "evidence": None,
                    "provenance": "EXTRACTED"} for k, v in values.items()}

    def _adjudicate(self, wrapped: dict) -> dict:
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        json.dump(wrapped, fh, ensure_ascii=False)
        fh.close()
        return json.loads(run("tools/adjudicate.py", fh.name, str(plug("superstack-core") / "rules" / "*.json")).stdout)

    def test_full_facts_are_trustworthy(self):
        """Контроль: без него следующий тест ничего не доказывает —
        недоверие могло бы браться от пропущенных правил, а не от кривого факта."""
        data = self._adjudicate(self._wrap(self._full_facts()))
        self.assertEqual(data["coverage"]["rules_skipped"], 0)
        self.assertTrue(data["coverage"]["trustworthy"])

    def test_one_malformed_fact_alone_breaks_trust(self):
        wrapped = self._wrap(self._full_facts())
        wrapped["кривой"] = {"нет_value": 1}
        data = self._adjudicate(wrapped)
        self.assertEqual(data["coverage"]["rules_skipped"], 0,
                         "правила пропущены — тест мерил бы не то")
        self.assertEqual(data["coverage"]["malformed_facts"], 1)
        self.assertFalse(data["coverage"]["trustworthy"],
                         "кривой факт не погасил доверие")

    def test_malformed_facts_reach_the_reader(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"кривой": {"нет_value": 1},
                   "host.os": {"value": "darwin", "provenance": "EXTRACTED"}}, fh)
        fh.close()
        r = run("tools/adjudicate.py", fh.name, str(plug("superstack-core") / "rules" / "*.json"))
        fh2 = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        fh2.write(r.stdout)
        fh2.close()
        out = run("tools/render.py", fh2.name, "beginner").stdout
        self.assertIn("НЕПОЛНЫЙ", out)
        self.assertIn("без значения", out)


class TestUnknownIsNotHealthy(unittest.TestCase):
    """«Не разобрал» и «здоров» обязаны выглядеть по-разному."""

    def test_unparseable_date_is_unknown(self):
        self.assertIsNone(doctor.days_since("2019-13-45 странно"))

    #: подставной маркетплейс — состав машины не участвует
    MK = {"тестовый": {"source": {"source": "github", "repo": "owner/repo"}}}

    def test_repo_with_bad_date_is_not_current(self):
        orig = doctor.gh
        doctor.gh = lambda p: {"pushed_at": "2019-13-45 странно",
                               "archived": False, "stargazers_count": 7}
        try:
            res = doctor.axis_upstream(self.MK)
            self.assertTrue(res)
            for r in res:
                self.assertNotEqual(r["state"], "current",
                                    f"нераспознанная дата показана здоровой: {r}")
        finally:
            doctor.gh = orig

    def test_fresh_repo_is_still_current(self):
        """Обратный контроль: проверка, никогда не говорящая «здоров», бесполезна."""
        from datetime import datetime, timezone
        orig = doctor.gh
        doctor.gh = lambda p: {"pushed_at": datetime.now(timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "archived": False, "stargazers_count": 7}
        try:
            res = doctor.axis_upstream(self.MK)
            self.assertTrue(res, "подставной маркетплейс не сработал — тест пуст")
            self.assertTrue(any(r["state"] == "current" for r in res))
        finally:
            doctor.gh = orig

    def test_incomplete_ledger_entry_does_not_crash(self):
        orig = doctor.read_json
        doctor.read_json = lambda p: ({"entries": [{"id": "битая"}, {}]}
                                      if str(p).endswith("supersession.json") else orig(p))
        try:
            res = doctor.axis_supersession("2.1.222", {"skills": [], "commands": [],
                                                       "mcp": [], "files": [], "dirs": []})
            self.assertTrue(any(r.get("state") == "error" for r in res))
        finally:
            doctor.read_json = orig


class TestTopTierCoversTheCommonCase(unittest.TestCase):
    """Агент без поля model наследует модель сессии — это верхний тир."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = Path(self.tmp.name) / ".claude"
        (self.claude / "agents").mkdir(parents=True)
        self._orig = collect.CLAUDE, collect.HOME
        collect.CLAUDE = self.claude
        collect.HOME = Path(self.tmp.name)
        collect.facts.clear()

    def tearDown(self):
        collect.CLAUDE, collect.HOME = self._orig
        self.tmp.cleanup()

    def _agent(self, name: str, fm: str) -> None:
        (self.claude / "agents" / f"{name}.md").write_text(
            f"---\n{fm}---\n\nТело.\n", encoding="utf-8")

    def test_fleet_without_model_field_is_flagged(self):
        for i in range(4):
            self._agent(f"w{i}", f"name: w{i}\ntools: Read\n")
        collect.probe_discipline()
        self.assertTrue(collect.facts["disc.all_on_top_tier"]["value"],
                        "флот без поля model — самый дорогой случай — не замечен")

    def test_cheap_tier_present_is_not_flagged(self):
        self._agent("a", "name: a\ntools: Read\n")
        self._agent("b", "name: b\nmodel: haiku\ntools: Read\n")
        self._agent("c", "name: c\ntools: Read\n")
        collect.probe_discipline()
        self.assertFalse(collect.facts["disc.all_on_top_tier"]["value"])


class TestJournalSurvivesBadEntry(unittest.TestCase):
    def test_list_does_not_die_on_incomplete_entry(self):
        tmp = tempfile.TemporaryDirectory()
        store = Path(tmp.name) / ".claude" / "superstack" / "learned"
        store.mkdir(parents=True)
        (store / "deadbeef0000.json").write_text(
            json.dumps({"id": "deadbeef0000", "title": "без части полей"}),
            encoding="utf-8")
        # Запись вообще без title — самый частый вид повреждения при
        # прерванной записи. Раньше она роняла весь список.
        (store / "deadbeef0001.json").write_text(
            json.dumps({"id": "deadbeef0001"}), encoding="utf-8")
        r = subprocess.run([sys.executable, str(at("tools", "learn.py")),
                            "list", "--scope", "local"],
                           capture_output=True, text=True, timeout=60,
                           env={**ENV, "HOME": tmp.name})
        tmp.cleanup()
        self.assertEqual(r.returncode, 0, r.stderr[:300])
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("неполна", r.stdout)


class TestHumanReadableSubstitution(unittest.TestCase):
    def test_list_of_dicts_is_not_a_python_repr(self):
        out = adj.substitute("{disc.verifier_theater}",
                             {"disc.verifier_theater": [
                                 {"agent": "code-reviewer", "why": "запись"},
                                 {"agent": "qa-engineer", "why": "MCP"}]}, None)
        self.assertNotIn("{'agent'", out)
        self.assertIn("code-reviewer", out)
        self.assertIn("qa-engineer", out)

    def test_long_list_is_capped(self):
        out = adj.substitute("{x}", {"x": [{"agent": f"a{i}"} for i in range(20)]}, None)
        self.assertIn("и ещё", out)

    def test_empty_list_reads_as_nothing(self):
        self.assertEqual(adj.substitute("{x}", {"x": []}, None), "—")

    def test_plain_values_unchanged(self):
        self.assertEqual(adj.substitute("{x}", {"x": 42}, None), "42")


class TestHostToolIsActuallyUsable(unittest.TestCase):
    def test_present_but_broken_tool_is_reported_absent(self):
        """Сломанный git лежит по своему пути и падает на любой команде."""
        orig = collect.sh

        def fake(cmd, timeout=10):
            if cmd and cmd[0] == "which":
                return "/usr/bin/" + cmd[1]
            return None          # любая попытка запустить — провал

        collect.sh = fake
        try:
            collect.facts.clear()
            collect.probe_host()
            self.assertFalse(collect.facts["host.git"]["value"],
                             "наличие пути выдано за работоспособность")
        finally:
            collect.sh = orig

    def test_working_tool_is_reported_present(self):
        """Обратный контроль: проверка, отвергающая всё, бесполезна."""
        collect.facts.clear()
        collect.probe_host()
        self.assertTrue(collect.facts["host.python3"]["value"])


class TestModelTierParsing(unittest.TestCase):
    """Полный идентификатор модели — документированный способ записи."""

    CASES = [("model: claude-opus-4-5-20250929", "opus"),
             ("model: opus", "opus"),
             ("model: claude-haiku-4-5", "haiku"),
             ("model: 'claude-sonnet-5'", "sonnet"),
             ("model: inherit", "none"),
             ("name: x\ntools: Read", "none"),
             ("model: gpt-5", "other")]

    def test_every_form(self):
        for fm, expected in self.CASES:
            with self.subTest(form=fm):
                self.assertEqual(collect._model_tier(fm + "\n"), expected)

    def test_cheap_fleet_with_full_ids_is_not_called_expensive(self):
        """Регрессия, которую внёс предыдущий фикс: haiku падал в none,
        а none считается верхним тиром — флот на дешёвой модели помечался
        дорогим. Проверка врала в обе стороны сразу."""
        tmp = tempfile.TemporaryDirectory()
        claude = Path(tmp.name) / ".claude"
        (claude / "agents").mkdir(parents=True)
        for i in range(4):
            (claude / "agents" / f"a{i}.md").write_text(
                f"---\nname: a{i}\nmodel: claude-haiku-4-5\ntools: Read\n---\n\nx\n",
                encoding="utf-8")
        orig = collect.CLAUDE, collect.HOME
        collect.CLAUDE, collect.HOME = claude, Path(tmp.name)
        try:
            collect.facts.clear()
            collect.probe_discipline()
            self.assertEqual(collect.facts["disc.model_tiers"]["value"]["haiku"], 4)
            self.assertFalse(collect.facts["disc.all_on_top_tier"]["value"])
        finally:
            collect.CLAUDE, collect.HOME = orig
            tmp.cleanup()


class TestEmptyFileIsNotAMechanism(unittest.TestCase):
    """`touch promotion.yaml` удовлетворял планку отбора."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = Path(self.tmp.name) / ".claude"
        (self.claude / "superstack" / "baseline").mkdir(parents=True)
        self._orig = collect.CLAUDE, collect.HOME
        collect.CLAUDE, collect.HOME = self.claude, Path(self.tmp.name)
        collect.facts.clear()

    def tearDown(self):
        collect.CLAUDE, collect.HOME = self._orig
        self.tmp.cleanup()

    def test_empty_files_do_not_satisfy_the_gates(self):
        for name in ("promotion.yaml", "budget.json"):
            (self.claude / "superstack" / name).write_text("", encoding="utf-8")
        (self.claude / "superstack" / "baseline" / "x.yaml").write_text("", encoding="utf-8")
        collect.probe_evolution()
        self.assertFalse(collect.facts["ev.promotion_gate"]["value"])
        self.assertFalse(collect.facts["ev.budget_ceiling"]["value"])
        self.assertEqual(collect.facts["ev.eval_baseline"]["value"], 0)

    def test_real_content_does_satisfy_them(self):
        """Обратный контроль: гейт, который нельзя удовлетворить, бесполезен."""
        (self.claude / "superstack" / "promotion.yaml").write_text(
            "gate:\n  check: required\n", encoding="utf-8")
        (self.claude / "superstack" / "budget.json").write_text(
            '{"listing_chars": 8000}', encoding="utf-8")
        (self.claude / "superstack" / "baseline" / "x.yaml").write_text(
            "task: собрать лендинг\ncriterion: зелёные тесты\n", encoding="utf-8")
        collect.probe_evolution()
        self.assertTrue(collect.facts["ev.promotion_gate"]["value"])
        self.assertTrue(collect.facts["ev.budget_ceiling"]["value"])
        self.assertEqual(collect.facts["ev.eval_baseline"]["value"], 1)


class TestStringLiteralsAreNotRewritten(unittest.TestCase):
    """Подстановка имён фактов залезала внутрь кавычек."""

    def test_literal_equal_to_a_fact_name(self):
        self.assertTrue(adj.evaluate("a.name == 'a.count'",
                                     {"a.count": 3, "a.name": "a.count"}))

    def test_double_quoted_literal(self):
        self.assertTrue(adj.evaluate('a.name == "a.count"',
                                     {"a.count": 3, "a.name": "a.count"}))

    def test_normal_comparison_still_works(self):
        self.assertTrue(adj.evaluate("cc.default_mode == 'plan'",
                                     {"cc.default_mode": "plan"}))
        self.assertTrue(adj.evaluate("a.count > 2", {"a.count": 3}))

    def test_huge_multiplier_is_refused(self):
        """Правило приходит через git; кода не исполняет, но память выест."""
        with self.assertRaises(adj.RuleError):
            adj.evaluate("len('A' * 100000 * 20000) > 0", {})

    def test_small_multiplier_is_allowed(self):
        self.assertTrue(adj.evaluate("len('A' * 10) > 5", {}))

    def test_unknown_dotted_fact_is_named_as_such(self):
        with self.assertRaises(adj.RuleError) as cm:
            adj.evaluate("nonexistent.key > 0", {"a.count": 1})
        self.assertIn("неизвестный факт: nonexistent.key", str(cm.exception))


class TestProjectFactsDescribeTheProject(unittest.TestCase):
    def test_project_dir_comes_from_env(self):
        tmp = tempfile.TemporaryDirectory()
        r = subprocess.run(
            [sys.executable, str(at("tools", "probe", "collect.py"))],
            capture_output=True, text=True, timeout=180, cwd=str(REPO),
            env={**ENV, "SUPERSTACK_PROJECT_DIR": tmp.name})
        facts = json.loads(r.stdout)
        got = facts["proj.dir"]["value"]
        has_tests = facts["proj.has_tests"]["value"]
        tmp.cleanup()
        self.assertTrue(got.endswith(Path(got).name))
        self.assertFalse(has_tests,
                         "описан каталог плагина, а не проект: tests/ найдены в пустом каталоге")


if __name__ == "__main__":
    unittest.main(verbosity=2)
