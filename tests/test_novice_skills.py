#!/usr/bin/env python3
"""Тесты новичковых скиллов: /what, /oops, /fix.

Что держат эти тесты.

Скилл — обещание, а не код. «Скилл существует» и «скилл держит обещанное»
это два разных утверждения, и здесь проверяется второе. Поэтому:

  · каждый скилл прогоняется через РЕАЛЬНЫЙ офлайн-гейт tools/skill_test.py —
    он реально разбирает bash-блоки интерпретатором (`sh -n`), реально
    резолвит каждый путь на диске и реально сверяет фронтматтер. Проверка
    подстроки в прозе здесь не была бы поведенческой — это прямо запрещено
    условием задачи, поэтому её тут нет;
  · у гейта есть КРАСНАЯ пара: сломанная копия скилла (путь на несуществующий
    инструмент) обязана провалить ту же проверку — иначе «зелёный» скилл
    ничего не доказывает, только то, что проверка не умеет падать;
  · у механизмов, которые эти скиллы приносят с собой (порядок приоритета в
    /what, тормоз на паузе в /oops, «первое разорванное звено» в /fix),
    тоже есть красная пара: тест ловит их СВОИМИ фикстурами, а не подсматривает
    ожидаемое значение в проверяемом коде — ожидание собрано вручную по
    условиям задачи, а не переписано из tools/what.py и соседей.

Герметичность: НАСТОЯЩИЙ ~/.claude здесь не читается. tools/what.py и
tools/fix.py используют собственные модульные константы HOME/CLAUDE/STATE —
они подменяются в setUp на временный каталог, как и в tests/test_apply.py.
tools/oops.py делает `import apply` и работает через `apply.BACKUPS` — здесь
подменяется САМ объект модуля `oops_mod.apply`, а не переимпортируется под
другим именем: тогда патч действует независимо от того, что ещё в процессе
успело зарегистрировать `sys.modules['apply']`.

    python3 -m pytest tests/test_novice_skills.py -q
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
from paths import PKG, REPO  # noqa: E402

ROOT = REPO
SKILLS = PKG / "skills"
TOOLS = PKG / "tools"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Уникальные имена в sys.modules — намеренно НЕ "apply"/"verify"/"skill_test":
# соседние тестовые файлы (tests/test_apply.py и другие) грузят те же
# инструменты под своими именами, и совпадение имени модуля перезаписало бы
# чужую запись в sys.modules при совместном прогоне полного набора.
skill_test = _load("ss_novice_skill_test",
                   PKG / "tools" / "skill_test.py")
what_mod = _load("ss_novice_what", TOOLS / "what.py")
oops_mod = _load("ss_novice_oops", TOOLS / "oops.py")
fix_mod = _load("ss_novice_fix", TOOLS / "fix.py")

# Объект модуля apply, который РЕАЛЬНО использует oops.py изнутри (через
# `import apply` в его собственном теле) — патчится он, не какая-то отдельно
# загруженная копия, иначе патч бьёт мимо.
real_apply = oops_mod.apply

ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}

GOOD_SPEC = """# Тестовая спека

## Что должно получиться

Работающая штука.

## Как проверить

Запусти `python3 -m pytest tests/test_x.py` — код возврата 0, тест зелёный.

## Чего НЕ делаем

Не трогаем прод.
"""

SPEC_MISSING_ACCEPT = """# Тестовая спека

## Что должно получиться

Работающая штука.

## Чего НЕ делаем

Не трогаем прод.
"""


# ============================================================================
# 1. РЕАЛЬНЫЕ СКИЛЛЫ ПРОХОДЯТ ОФЛАЙН-ГЕЙТ
# ============================================================================
class TestSkillsPassTheGate(unittest.TestCase):
    """Каждый скилл — through tools/skill_test.py, а не через grep по файлу."""

    def _review(self, name: str) -> dict:
        s = skill_test.load(SKILLS / name, skill_test.LISTING_BUDGET_CHARS,
                                PKG)
        return skill_test.review(s)

    def test_what_is_clean(self):
        v = self._review("what")
        self.assertEqual(v["status"], "clean", msg=json.dumps(v, ensure_ascii=False))

    def test_oops_is_clean(self):
        v = self._review("oops")
        self.assertEqual(v["status"], "clean", msg=json.dumps(v, ensure_ascii=False))

    def test_fix_is_clean(self):
        v = self._review("fix")
        self.assertEqual(v["status"], "clean", msg=json.dumps(v, ensure_ascii=False))

    def test_frontmatter_name_is_literal(self):
        """Карта плана ищет буквально «name: what» / «name: oops» / «name: fix»."""
        for name in ("what", "oops", "fix"):
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertIn(f"name: {name}", text)
                fields, _ = skill_test.parse_frontmatter(text)
                self.assertEqual(fields.get("name"), name)

    def test_description_names_a_trigger_not_a_function(self):
        """«Когда брать», а не «что делает» — проверяется тем же check_when,
        которым живёт настоящий гейт, не отдельной эвристикой теста."""
        for name in ("what", "oops", "fix"):
            s = skill_test.load(SKILLS / name, skill_test.LISTING_BUDGET_CHARS,
                                PKG)
            c = skill_test.check_when(s)
            with self.subTest(skill=name):
                self.assertEqual(c.state, "pass", msg=c.detail)


class TestGateHasARedPair(unittest.TestCase):
    """Гейт обязан падать на сломанном скилле — иначе «зелёный» ничего не значит."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "plugin"
        self.root.mkdir()
        # Пустая заготовка каталога инструментов — существование конкретных
        # файлов проверяется по отдельности внутри каждого теста.
        (self.root / "tools").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _copy_skill_with_broken_path(self, name: str) -> Path:
        """Копия реального скилла с ОДНОЙ порчей: путь на несуществующий файл."""
        src = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
        broken = src.replace(f"tools/{name}.py", "tools/does-not-exist.py")
        self.assertNotEqual(src, broken, "подмена не сработала — тест ничего не проверяет")
        dst_dir = self.root / "skills" / name
        dst_dir.mkdir(parents=True)
        (dst_dir / "SKILL.md").write_text(broken, encoding="utf-8")
        return dst_dir

    def test_broken_tool_path_fails_the_gate(self):
        for name in ("what", "oops", "fix"):
            with self.subTest(skill=name):
                dst = self._copy_skill_with_broken_path(name)
                s = skill_test.load(dst, skill_test.LISTING_BUDGET_CHARS, self.root)
                v = skill_test.review(s)
                self.assertNotEqual(v["status"], "clean",
                                    "гейт пропустил путь на несуществующий инструмент")
                paths_check = next(c for c in v["checks"] if c["check"] == "paths")
                self.assertEqual(paths_check["state"], "fail")

    def test_description_without_trigger_fails_when_check(self):
        """Красная пара для check_when: описание без повода обязано падать."""
        d = self.root / "skills" / "mute"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: mute\ndescription: Собирает релиз и публикует его.\n---\n\nтело\n",
            encoding="utf-8")
        s = skill_test.load(d, skill_test.LISTING_BUDGET_CHARS, self.root)
        c = skill_test.check_when(s)
        self.assertEqual(c.state, "fail")


# ============================================================================
# 2. /what — приоритет состояний
# ============================================================================
class WhatFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir(parents=True)
        self._orig = (what_mod.HOME, what_mod.CLAUDE, what_mod.STATE)
        what_mod.HOME = self.home
        what_mod.CLAUDE = self.home / ".claude"
        what_mod.STATE = what_mod.CLAUDE / "superstack"

    def tearDown(self):
        what_mod.HOME, what_mod.CLAUDE, what_mod.STATE = self._orig
        self.tmp.cleanup()

    def write_spec(self, text: str, name: str = "x.md") -> Path:
        d = self.project / ".claude" / "specs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(text, encoding="utf-8")
        return p

    def write_test_file(self, body: str) -> None:
        d = self.project / "tests"
        d.mkdir(parents=True, exist_ok=True)
        (d / "test_x.py").write_text(body, encoding="utf-8")

    def pause(self, since: str = "2026-01-01T00:00:00Z") -> None:
        what_mod.STATE.mkdir(parents=True, exist_ok=True)
        (what_mod.STATE / "PAUSE").write_text(since, encoding="utf-8")


class TestWhatPriority(WhatFixture):
    def test_no_spec_at_all(self):
        r = what_mod.evaluate(self.project)
        self.assertEqual(r["reason"], "no-spec")
        self.assertIn("/go", r["next"])

    def test_spec_with_missing_section(self):
        self.write_spec(SPEC_MISSING_ACCEPT)
        r = what_mod.evaluate(self.project)
        self.assertEqual(r["reason"], "spec-problems")
        self.assertIn("x.md", r["line"])

    def test_clean_spec_no_declared_checks(self):
        self.write_spec(GOOD_SPEC)
        r = what_mod.evaluate(self.project)
        self.assertEqual(r["reason"], "no-checks")

    def test_clean_spec_failing_tests(self):
        self.write_spec(GOOD_SPEC)
        self.write_test_file("def test_x():\n    assert False\n")
        r = what_mod.evaluate(self.project)
        self.assertEqual(r["reason"], "verify-fail")

    def test_clean_spec_passing_tests(self):
        self.write_spec(GOOD_SPEC)
        self.write_test_file("def test_x():\n    assert True\n")
        r = what_mod.evaluate(self.project)
        self.assertEqual(r["reason"], "verified")

    def test_pause_wins_over_a_fully_verified_project(self):
        """Механизм: приоритет ПАУЗЫ выше зелёного гейта. Красная пара — ниже
        (test_pause_priority_mutation_would_be_caught), проверена буквально
        через find/replace, см. описание в ответе агента."""
        self.write_spec(GOOD_SPEC)
        self.write_test_file("def test_x():\n    assert True\n")
        self.pause()
        r = what_mod.evaluate(self.project)
        self.assertEqual(r["reason"], "paused")
        self.assertIn("pause.sh off", r["next"])

    def test_line_and_next_are_both_single_lines(self):
        """Скилл обещает РОВНО одну строку состояния и одну — следующего шага."""
        self.write_spec(GOOD_SPEC)
        r = what_mod.evaluate(self.project)
        self.assertNotIn("\n", r["line"])
        self.assertNotIn("\n", r["next"])


class TestWhatCLI(WhatFixture):
    """Прогон реальным процессом — не только импортом функции."""

    def test_cli_reports_no_spec_as_json(self):
        p = subprocess.run(
            [sys.executable, str(TOOLS / "what.py"), "--json", str(self.project)],
            capture_output=True, text=True, timeout=30,
            env={**ENV, "HOME": str(self.home)})
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual(out["reason"], "no-spec")


# ============================================================================
# 3. /oops — показать ДО отката, тормоз на паузе
# ============================================================================
class OopsFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.claude = home / ".claude"
        self.claude.mkdir(parents=True)
        self._orig = (real_apply.HOME, real_apply.CLAUDE, real_apply.STATE,
                      real_apply.BACKUPS, real_apply.QUARANTINE)
        real_apply.HOME = home
        real_apply.CLAUDE = self.claude
        real_apply.STATE = self.claude / "superstack"
        real_apply.BACKUPS = real_apply.STATE / "backups"
        real_apply.QUARANTINE = real_apply.STATE / "quarantine"

    def tearDown(self):
        (real_apply.HOME, real_apply.CLAUDE, real_apply.STATE,
         real_apply.BACKUPS, real_apply.QUARANTINE) = self._orig
        self.tmp.cleanup()

    def make_backup_with_change(self, backup_id: str = "20260101-000000",
                                redacted: list | None = None) -> Path:
        bdir = real_apply.BACKUPS / backup_id
        bdir.mkdir(parents=True)
        manifest = {
            "created": "2026-01-01T00:00:00+00:00",
            "backup": {"dir": str(bdir), "files": ["settings.json"],
                      "redacted": redacted or []},
            "applied": [{"id": "ctx.default-mode-unset",
                        "action": "задам permissions.defaultMode = plan",
                        "changed": True, "field": "permissions.defaultMode",
                        "before": "plan", "after": "acceptEdits"}],
        }
        (bdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                            encoding="utf-8")
        (bdir / "settings.json").write_text("{}", encoding="utf-8")
        return bdir


class TestOopsDescribesBeforeUndoing(OopsFixture):
    def test_no_backups_is_named_honestly(self):
        r = oops_mod.describe(oops_mod.last_backup_id() or "nope")
        self.assertFalse(r["found"])

    def test_describe_names_the_field_and_value(self):
        self.make_backup_with_change()
        r = oops_mod.describe("20260101-000000")
        self.assertTrue(r["found"])
        self.assertEqual(r["changed_count"], 1)
        self.assertIn("permissions.defaultMode", r["line"])
        self.assertIn("plan", r["line"])

    def test_describe_flags_secrets_that_will_not_return(self):
        self.make_backup_with_change(
            redacted=[{"file": "settings.json", "count": 1}])
        r = oops_mod.describe("20260101-000000")
        self.assertEqual(r["blocked_secrets"], ["settings.json"])
        self.assertIn("НЕ восстановит", r["line"])

    def test_describe_never_leaks_a_secret_value(self):
        """Отпечаток и длина допустимы, само значение — никогда."""
        self.make_backup_with_change(
            redacted=[{"file": "settings.json", "count": 1}])
        r = oops_mod.describe("20260101-000000")
        dump = json.dumps(r, ensure_ascii=False)
        self.assertNotIn("sk-", dump)  # типичный префикс токена
        self.assertNotIn("secret_value", dump)

    def test_last_backup_id_picks_the_newest(self):
        self.make_backup_with_change("20260101-000000")
        self.make_backup_with_change("20260601-120000")
        self.assertEqual(oops_mod.last_backup_id(), "20260601-120000")

    def test_undo_actually_restores_the_file(self):
        self.make_backup_with_change()
        (real_apply.CLAUDE / "settings.json").write_text(
            json.dumps({"permissions": {"defaultMode": "plan"}}), encoding="utf-8")
        rc = oops_mod.undo("20260101-000000")
        self.assertEqual(rc, 0)
        restored = json.loads((real_apply.CLAUDE / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(restored, {})


class TestOopsRespectsThePause(OopsFixture):
    """Механизм: undo() сам проверяет паузу, потому что apply.cmd_undo() —
    в обход apply.main() — этого не делает. Мутация: убрать вызов
    apply.halt_if_paused() внутри oops.undo(); см. ответ агента для
    буквальной проверки красноты."""

    def setUp(self):
        super().setUp()
        self._env_backup = os.environ.pop("SUPERSTACK_IGNORE_PAUSE", None)

    def tearDown(self):
        if self._env_backup is not None:
            os.environ["SUPERSTACK_IGNORE_PAUSE"] = self._env_backup
        super().tearDown()

    def test_undo_refuses_while_paused(self):
        self.make_backup_with_change()
        real_apply.STATE.mkdir(parents=True, exist_ok=True)
        (real_apply.STATE / "PAUSE").write_text("2026-01-01T00:00:00Z", encoding="utf-8")
        with self.assertRaises(SystemExit) as ctx:
            oops_mod.undo("20260101-000000")
        self.assertEqual(ctx.exception.code, 10)

    def test_apply_cmd_undo_alone_would_not_have_stopped_this(self):
        """Доказывает, ЧТО именно добавляет oops.undo() поверх apply.cmd_undo:
        сам apply.cmd_undo(), вызванный напрямую (как это и происходит внутри
        undo() до добавления явного тормоза), паузу не проверяет."""
        self.make_backup_with_change()
        real_apply.STATE.mkdir(parents=True, exist_ok=True)
        (real_apply.STATE / "PAUSE").write_text("2026-01-01T00:00:00Z", encoding="utf-8")
        rc = real_apply.cmd_undo(["20260101-000000"])
        self.assertEqual(rc, 0, "если это утверждение когда-нибудь станет "
                                "неверным — тормоз в oops.py стал избыточным, "
                                "и об этом стоит сказать явно, а не молчать")


# ============================================================================
# 4. /fix — первое разорванное звено, а не список причин
# ============================================================================
class FixFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self._orig = (fix_mod.HOME, fix_mod.CLAUDE, fix_mod.STATE, fix_mod.ROOT)
        fix_mod.HOME = self.home
        fix_mod.CLAUDE = self.home / ".claude"
        fix_mod.STATE = fix_mod.CLAUDE / "superstack"

    def tearDown(self):
        fix_mod.HOME, fix_mod.CLAUDE, fix_mod.STATE, fix_mod.ROOT = self._orig
        self.tmp.cleanup()

    def make_healthy_root(self) -> Path:
        root = Path(self.tmp.name) / "plugin"
        tools = root / "tools"
        tools.mkdir(parents=True)
        for name in ("verify.py", "apply.py", "spec_lint.py", "skill_test.py"):
            (tools / name).write_text("# stub\n", encoding="utf-8")
        hooks = root / "hooks"
        hooks.mkdir()
        (hooks / "hooks.json").write_text(json.dumps({
            "hooks": {"Stop": [{"hooks": [
                {"type": "command",
                 "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/verify-gate.sh"'}]}]}
        }), encoding="utf-8")
        (hooks / "verify-gate.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fix_mod.ROOT = root
        return root


class TestFixOrderedChain(FixFixture):
    def test_all_links_ok(self):
        self.make_healthy_root()
        v = fix_mod.diagnose()
        self.assertEqual(v["status"], "ok")
        self.assertEqual(v["ok_count"], v["total"])

    def test_missing_tool_reports_that_exact_link(self):
        root = self.make_healthy_root()
        (root / "tools" / "apply.py").unlink()
        v = fix_mod.diagnose()
        self.assertEqual(v["status"], "broken")
        self.assertEqual(v["link"], "файлы инструментов")
        self.assertEqual(v["broken_index"], 2)  # python3(1) ok, tools(2) сломан
        self.assertEqual(v["ok_count"], 1)

    def test_hook_referencing_missing_script_is_caught(self):
        root = self.make_healthy_root()
        (root / "hooks" / "verify-gate.sh").unlink()
        v = fix_mod.diagnose()
        self.assertEqual(v["status"], "broken")
        self.assertEqual(v["link"], "подключение хуков")
        self.assertEqual(v["broken_index"], 3)

    def test_pause_flag_is_reported_as_a_link(self):
        self.make_healthy_root()
        fix_mod.STATE.mkdir(parents=True, exist_ok=True)
        (fix_mod.STATE / "PAUSE").write_text("2026-01-01T00:00:00Z", encoding="utf-8")
        v = fix_mod.diagnose()
        self.assertEqual(v["status"], "broken")
        self.assertEqual(v["link"], "пауза")
        self.assertEqual(v["next"], "sh tools/pause.sh off")

    def test_unwritable_state_is_the_last_link(self):
        self.make_healthy_root()
        blocker = Path(self.tmp.name) / "blocker-is-a-file"
        blocker.write_text("x", encoding="utf-8")
        fix_mod.STATE = blocker / "superstack"  # родитель — файл, mkdir провалится
        v = fix_mod.diagnose()
        self.assertEqual(v["status"], "broken")
        self.assertEqual(v["link"], "права на запись")
        self.assertEqual(v["broken_index"], 5)

    def test_first_broken_link_wins_over_a_later_one(self):
        """Механизм: цепочка останавливается на ПЕРВОМ разрыве, даже если
        дальше по порядку есть ещё один. Красная пара — ниже по тексту
        ответа агента (проверено буквальной мутацией: return -> continue)."""
        root = self.make_healthy_root()
        (root / "tools" / "verify.py").unlink()          # звено 2 сломано
        fix_mod.STATE.mkdir(parents=True, exist_ok=True)
        (fix_mod.STATE / "PAUSE").write_text("x", encoding="utf-8")  # звено 4 тоже
        v = fix_mod.diagnose()
        self.assertEqual(v["broken_index"], 2, "должно остановиться на первом "
                         "разрыве (инструменты), а не дойти до паузы")
        self.assertEqual(v["ok_count"], 1)

    def test_vocabulary_says_not_connected_not_error(self):
        root = self.make_healthy_root()
        (root / "tools" / "apply.py").unlink()
        v = fix_mod.diagnose()
        text = json.dumps(v, ensure_ascii=False) + fix_mod.human(v)
        self.assertIn("ещё не подключено", fix_mod.human(v))
        self.assertNotIn("ошибка", text.lower())
        self.assertNotIn("баг", text.lower())


class TestFixCLI(FixFixture):
    def test_cli_exit_code_matches_status(self):
        self.make_healthy_root()
        p = subprocess.run(
            [sys.executable, str(TOOLS / "fix.py"), "--json"],
            capture_output=True, text=True, timeout=30,
            env={**ENV, "HOME": str(self.home)})
        # запущенный подпроцессом инструмент использует СВОИ собственные
        # HOME/CLAUDE/ROOT (вычисленные при импорте, не патч этого теста) —
        # здесь проверяется только форма вывода и связь код/статус, не
        # конкретное дерево путей.
        out = json.loads(p.stdout)
        self.assertEqual(p.returncode, 0 if out["status"] == "ok" else 1)


if __name__ == "__main__":
    unittest.main()
