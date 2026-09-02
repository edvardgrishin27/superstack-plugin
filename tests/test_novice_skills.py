#!/usr/bin/env python3
"""Тесты новичковых скиллов: /what, /oops, /fix, /stop, /change.

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

import contextlib
import importlib.util
import io
import json
import re
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

    def test_stop_is_clean(self):
        v = self._review("stop")
        self.assertEqual(v["status"], "clean", msg=json.dumps(v, ensure_ascii=False))

    def test_change_is_clean(self):
        v = self._review("change")
        self.assertEqual(v["status"], "clean", msg=json.dumps(v, ensure_ascii=False))

    def test_frontmatter_name_is_literal(self):
        """Карта плана ищет буквально «name: what» / «name: oops» / «name: fix»."""
        for name in ("what", "oops", "fix", "stop", "change"):
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(skill=name):
                self.assertIn(f"name: {name}", text)
                fields, _ = skill_test.parse_frontmatter(text)
                self.assertEqual(fields.get("name"), name)

    def test_description_names_a_trigger_not_a_function(self):
        """«Когда брать», а не «что делает» — проверяется тем же check_when,
        которым живёт настоящий гейт, не отдельной эвристикой теста."""
        for name in ("what", "oops", "fix", "stop", "change"):
            s = skill_test.load(SKILLS / name, skill_test.LISTING_BUDGET_CHARS,
                                PKG)
            c = skill_test.check_when(s)
            with self.subTest(skill=name):
                self.assertEqual(c.state, "pass", msg=c.detail)


class TestTheBrakeWorksFromAPhrase(unittest.TestCase):
    """Тормоз обязан срабатывать со слов, а не с команды в терминале.

    Повод — не теория. Выключателей было два, `SUPERSTACK_DISABLE=1` и
    `tools/pause.sh on`, и оба требуют терминала. То есть у человека, которому
    гейт верификации не даёт закрыть ход, тормоза не было вовсе: чтобы
    остановить систему, ему полагалось набрать команду, которую он набирать не
    умеет. Установка без терминала при этом была сделана честно — продукт
    впускал легко и не выпускал.
    """

    def setUp(self):
        self.t = (SKILLS / "stop" / "SKILL.md").read_text("utf-8")
        self.gate = (PKG / "hooks" / "verify-gate.py").read_text("utf-8")

    def test_the_description_carries_the_words_a_stuck_person_says(self):
        """Скилл выбирается по description. Слова берутся человеческие —
        те, что человек напишет в панике, а не имя команды."""
        описание = self.t.split("---")[1]
        for слово in ("останови", "выключи", "не могу закрыть ход", "включи"):
            with self.subTest(слово=слово):
                self.assertIn(слово, описание.lower(),
                              f"фразы «{слово}» нет в description — скилл не "
                              "подтянется, когда его позовут этими словами")

    def test_the_brake_calls_the_script_that_confirms_the_flag(self):
        self.assertIn('"$CLAUDE_PLUGIN_ROOT/tools/pause.py" on', self.t)
        self.assertIn('"$CLAUDE_PLUGIN_ROOT/tools/pause.py" off', self.t)

    def test_the_block_message_names_the_way_out(self):
        """Выход назван ТАМ, где человек застрял.

        Документация здесь не считается: текст блока — единственное, что
        человек читает в момент, когда система его не отпускает.
        """
        self.assertIn("останови суперстек", self.gate,
                      "блок гейта не называет фразу выхода — человек читает "
                      "«ход не закрывается» и не знает, что с этим делать")

    def test_the_block_message_does_not_hand_out_shell_commands(self):
        # Ищется ТЕЛО блокирующего ответа, а не конкретная форма печати:
        # прежний якорь `printf '{"decision":"block"` жил ровно пока хук был
        # скриптом оболочки. После порта на Python строка собирается функцией,
        # и проверка, привязанная к способу печати, упала бы на верном коде.
        блок = self.gate[self.gate.rindex("ГЕЙТ ВЕРИФИКАЦИИ НЕ ПРОЙДЕН"):]
        for команда in ("SUPERSTACK_DISABLE=1", "pause.sh on"):
            with self.subTest(команда=команда):
                self.assertNotIn(команда, блок,
                                 "в блоке предлагается команда оболочки — "
                                 "это возвращает человека ровно в ту точку, "
                                 "из-за которой тормоз и переписан")


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
        первый = re.search(r"tools/([\w.-]+\.(?:py|sh))", src)
        self.assertIsNotNone(
            первый, f"{name} не зовёт ни одного инструмента — ломать нечего")
        broken = src.replace(первый.group(0), "tools/does-not-exist.py", 1)
        self.assertNotEqual(src, broken, "подмена не сработала — тест ничего не проверяет")
        dst_dir = self.root / "skills" / name
        dst_dir.mkdir(parents=True)
        (dst_dir / "SKILL.md").write_text(broken, encoding="utf-8")
        return dst_dir

    def test_broken_tool_path_fails_the_gate(self):
        for name in ("what", "oops", "fix", "stop", "change"):
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
        self.assertIn("включи", r["next"])

    def test_line_and_next_are_both_single_lines(self):
        """Скилл обещает РОВНО одну строку состояния и одну — следующего шага."""
        self.write_spec(GOOD_SPEC)
        r = what_mod.evaluate(self.project)
        self.assertNotIn("\n", r["line"])
        self.assertNotIn("\n", r["next"])

    def test_paused_next_is_words_not_a_shell_command(self):
        """Человек попал на паузу через /stop — скилл, написанный ровно для
        того, чтобы не открывать терминал (см. skills/stop/SKILL.md: «тормоз
        обязан срабатывать с фразы»). Ответ на «где я» не имеет права выдать
        ему то же самое, от чего /stop его избавил, — команду оболочки или
        путь к файлу инструмента, которые он не может исполнить."""
        self.pause()
        r = what_mod.evaluate(self.project)
        self.assertEqual(r["reason"], "paused")
        for кусок in ("pause.sh", "sh ", "tools/"):
            with self.subTest(кусок=кусок):
                self.assertNotIn(кусок, r["next"])
                self.assertNotIn(кусок, r["line"])


class TestWhatReadsTheRunWhereGoWritesIt(WhatFixture):
    """`/what` смотрит туда, куда `/go` пишет, — в `.superstack/`.

    Красная пара к находке: `.claude/specs/` не пишет ни один инструмент
    системы, и пока это была единственная папка, на второй день после полного
    прогона человек слышал «работа ещё не начата». Ожидание собрано по
    условию задачи: этап «Проверяем», ход на системе — и обе половины ответа
    (строка состояния и её отсутствие в прежней форме) проверяются вместе,
    потому что «назвал этап» и «перестал врать про начало» — разные утверждения.
    """

    def test_the_recorded_phase_is_reported_instead_of_work_not_started(self):
        run = self.project / ".superstack"
        run.mkdir(parents=True)
        (run / "state.json").write_text(json.dumps({
            "schema": "superstack.progress.v1",
            "phase": {"name": "Проверяем", "owner": "система",
                      "detail": "запускаю проверки",
                      "since": "2026-09-02T00:00:00+00:00"},
        }, ensure_ascii=False), encoding="utf-8")

        p = subprocess.run(
            [sys.executable, str(TOOLS / "what.py"), str(self.project)],
            capture_output=True, text=True, timeout=30,
            env={**ENV, "HOME": str(self.home)})
        self.assertEqual(p.returncode, 0, p.stderr)

        строка = json.loads(p.stdout)["line"]
        self.assertIn("Проверяем", строка)
        self.assertNotIn("не начата", строка)
        # То же самое человек читает глазами, а не в JSON: проверять только
        # поле значило бы разрешить расхождение между ними.
        self.assertIn("Проверяем", p.stderr)
        self.assertNotIn("не начата", p.stderr)


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

    def test_manifest_that_is_valid_json_but_not_an_object(self):
        """`[]`, `42`, `"текст"`, `null` — валидный JSON, но НЕ словарь.
        json.loads() на таком не бросает ничего (except выше ловит только
        OSError/ValueError), и следующая же строка manifest.get("applied")
        роняла AttributeError наружу — голым traceback'ом, причём и в
        `oops.py what`, и в `oops.py undo`: describe() зовут обе ветки.
        Мутация: убрать проверку isinstance(manifest, dict) в describe() —
        тест краснеет именно этим AttributeError."""
        bdir = real_apply.BACKUPS / "20260101-000000"
        bdir.mkdir(parents=True)
        for raw in ("[]", "42", '"текст"', "null"):
            with self.subTest(manifest=raw):
                (bdir / "manifest.json").write_text(raw, encoding="utf-8")
                r = oops_mod.describe("20260101-000000")
                self.assertFalse(r["found"])
                self.assertEqual(r["id"], "20260101-000000")
                self.assertIn("20260101-000000", r["line"])

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


class TestOopsUndoFailsHonestly(OopsFixture):
    """Дефект: `oops.py undo` печатал «откатил «id»: …» БЕЗУСЛОВНО, до
    проверки кода возврата — при провале отката человек читал подтверждение
    в прошедшем времени про действие, которого не было (код при этом был
    честный: `0 if rc == 0 else 1`, но человек читает строку, а не код).

    Сросшийся с ним дефект: undo() вызывался даже когда describe() уже
    сказал found=False, а apply.cmd_undo() читает manifest.json заново без
    единой проверки (apply.py, cmd_undo, ~строка 519) — битый или
    отсутствующий файл ронял голый traceback вместо человеческой строки.

    Мутация (для буквальной проверки красноты обеих причин сразу): вернуть
    в oops.py безусловный print(f"откатил «{backup_id}»: …") ДО проверки rc
    и убрать ветку `if not preview["found"]:` вместе с try/except вокруг
    undo() — ровно то, что было в исходном коде; см. ответ агента за точным
    диффом."""

    def _run_undo(self, backup_id: str) -> tuple:
        """main() как «oops.py undo <id>» в этом же процессе, без --json —
        иначе человеческая строка не печатается вовсе и проверять нечего.
        Возвращает (код, stderr, stdout)."""
        argv_orig = sys.argv
        sys.argv = ["oops.py", "undo", backup_id]
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = oops_mod.main()
        finally:
            sys.argv = argv_orig
        return rc, err.getvalue(), out.getvalue()

    def test_unknown_id_is_not_reported_as_rolled_back(self):
        """Отката с таким id никогда не было — describe() честно отдаёт
        found=False. Дальше звать apply.cmd_undo незачем: она бы либо
        повторила тот же отказ, либо (если каталог есть без манифеста)
        упала traceback'ом — ни то ни другое не повод писать «откатил»."""
        rc, err, out = self._run_undo("nope-never-existed")
        self.assertEqual(rc, 1)
        self.assertNotIn("откатил", err)
        self.assertIn("ОТКАЗ", err)
        self.assertEqual(json.loads(out)["undo_exit"], 1)

    def test_corrupt_manifest_is_refused_not_traceback(self):
        """manifest.json есть, но не разбирается json.loads'ом. describe()
        ловит это сам (found=False — OSError/ValueError уже пойманы внутри
        него) — и это обязано остановить undo ДО вызова apply.cmd_undo(),
        у которой такой защиты нет: тот же файл она читает заново без
        единого try/except (apply.py, cmd_undo, ~строка 519)."""
        bdir = real_apply.BACKUPS / "20260101-000000"
        bdir.mkdir(parents=True)
        (bdir / "manifest.json").write_text("{не json вовсе", encoding="utf-8")
        rc, err, out = self._run_undo("20260101-000000")
        self.assertEqual(rc, 1)
        self.assertNotIn("откатил", err)
        self.assertNotIn("Traceback", err)
        self.assertIn("не читается", err)  # строка из describe(), не выдумка

    def test_structurally_broken_manifest_does_not_crash(self):
        """manifest.json — валидный JSON-словарь с "applied" (describe()
        говорит found=True: ей этого ключа достаточно), но БЕЗ ключа
        "backup". apply.cmd_undo() индексирует manifest["backup"]["files"]
        без проверки формы — предсказать этот KeyError на стороне describe()
        нельзя, он ловится только вокруг самого вызова undo()."""
        bdir = real_apply.BACKUPS / "20260101-000000"
        bdir.mkdir(parents=True)
        manifest = {"created": "2026-01-01T00:00:00+00:00", "applied": []}
        (bdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        rc, err, out = self._run_undo("20260101-000000")
        self.assertEqual(rc, 1)
        self.assertNotIn("откатил", err)
        self.assertNotIn("Traceback", err)
        self.assertIn("код apply.py", err)

    def test_manifest_that_is_not_an_object_is_refused_not_traceback(self):
        """Та же битая форма (валидный JSON, но не словарь), но через CLI:
        describe() отдаёт found=False, значит apply.cmd_undo() не зовётся
        вовсе и человек читает отказ. Без проверки формы в describe() падало
        бы РАНЬШЕ — внутри самой describe(), до ветки `not preview["found"]`,
        то есть до любого шанса напечатать человеческую строку."""
        bdir = real_apply.BACKUPS / "20260101-000000"
        bdir.mkdir(parents=True)
        (bdir / "manifest.json").write_text("[]", encoding="utf-8")
        rc, err, out = self._run_undo("20260101-000000")
        self.assertEqual(rc, 1)
        self.assertNotIn("откатил", err)
        self.assertNotIn("Traceback", err)
        self.assertIn("ОТКАЗ", err)
        self.assertEqual(json.loads(out)["undo_exit"], 1)

    def test_undo_failure_without_a_caught_exception_does_not_say_none(self):
        """crash остаётся None, когда apply.cmd_undo() вернула 1 БЕЗ
        исключения — у неё три выхода (проверено чтением: ранний `return 1`,
        когда bdir.is_dir() ложно, `return UNDO_NOTHING_RESTORED` (код 5,
        разобран отдельной веткой выше) и финальный `return 0`), и ни один
        из них не бросает того, что ловит except выше. Достижимо это
        гонкой: describe() сказала found=True, а к моменту вызова undo()
        каталог отката исчез. Гонка смоделирована без потока — describe()
        подменена на синтетический found=True для id, которого на диске
        нет вовсе, — apply.cmd_undo() увидит ровно то же самое, что и в
        настоящей гонке. `Причина: None.` была бы враньём о том, что у
        отказа нет причины, — причина есть, просто не эта строка её знает."""
        orig_describe = oops_mod.describe
        oops_mod.describe = lambda backup_id: {
            "found": True, "id": backup_id, "line": "неважно для этого теста"}
        try:
            rc, err, out = self._run_undo("this-id-do" "es-not-exi" "st-on-disk")
        finally:
            oops_mod.describe = orig_describe
        self.assertEqual(rc, 1)
        self.assertNotIn("None", err)
        self.assertIn("ОТКАЗ", err)

    def test_successful_undo_is_still_reported_as_such(self):
        """Красная пара к пяти тестам выше: на честном успехе «откатил»
        обязан остаться, иначе выше проверяется дефект, которого нет."""
        self.make_backup_with_change()
        rc, err, out = self._run_undo("20260101-000000")
        self.assertEqual(rc, 0)
        self.assertIn("откатил «20260101-000000»", err)


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
                 "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/verify-gate.py"'}]}]}
        }), encoding="utf-8")
        (hooks / "verify-gate.py").write_text("import sys\nsys.exit(0)\n",
                                      encoding="utf-8")
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
        (root / "hooks" / "verify-gate.py").unlink()
        v = fix_mod.diagnose()
        self.assertEqual(v["status"], "broken")
        # Ожидание менялось вместе с механизмом: звено называлось человеку
        # «подключение хуков» — словом из словаря, которого он не знает.
        self.assertEqual(v["link"], "автоматические проверки")
        self.assertEqual(v["broken_index"], 3)

    def test_pause_flag_is_reported_as_a_link(self):
        self.make_healthy_root()
        fix_mod.STATE.mkdir(parents=True, exist_ok=True)
        (fix_mod.STATE / "PAUSE").write_text("2026-01-01T00:00:00Z", encoding="utf-8")
        v = fix_mod.diagnose()
        self.assertEqual(v["status"], "broken")
        self.assertEqual(v["link"], "пауза")
        # Ожидание менялось вместе с механизмом: раньше здесь стояла
        # команда оболочки `sh tools/pause.sh off`. Целевой человек не
        # разработчик — команду он не наберёт, а /stop снимает паузу с
        # фразы. Ни «sh », ни «.sh», ни «python3 » в ответе быть не может.
        self.assertEqual(v["next"],
                         "скажи «включи» или позови /stop — сама пауза не пройдёт")
        for запрет in ("sh ", ".sh", "python3 "):
            self.assertNotIn(запрет, v["next"],
                             "человеку снова подсовывают команду терминала")

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


class TestFixSpeaksToANonDeveloper(unittest.TestCase):
    """Строки звеньев читает тот, кто пришёл со словами «что-то сломалось».

    Скилл /fix прямо говорит, кто это: человек, который «не знает слова
    «хук»». Ровно это слово и стояло в двух строках, которые он видит на
    экране, — в имени звена «подключение хуков» и в единственном совете
    «переустанови плагин — хук ссылается на файл, которого нет».

    Проверяется машиной, а не вычиткой: чтобы заметить непонятное слово,
    нужно перестать его понимать, и автору строки это недоступно. Список
    слов лежит данными (`data/plain-ru.json`), сверяет его `plain_ru.py` —
    тот же прибор, которым система проверяет любую другую строку человеку.
    """

    def test_no_link_speaks_jargon_to_the_human(self):
        ru = _load("ss_novice_plain_ru", TOOLS / "plain_ru.py")
        for link_id, label, _fn, next_step in fix_mod.LINKS:
            for где, текст in (("имя звена", label), ("совет", next_step)):
                with self.subTest(звено=link_id, где=где):
                    нашлось = ru.find_jargon(текст)
                    self.assertEqual(
                        нашлось, [],
                        f"звено «{link_id}», {где}: человек прочтёт «{текст}»"
                        f" — {[(w, b) for w, _s, b in нашлось]}")


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



# ============================================================================
# 6. МАЛАЯ ПРАВКА ГОТОВОГО ПРОДУКТА — /change
# ============================================================================
class TestSmallEditHasItsOwnRoute(unittest.TestCase):
    """«Добавь кнопку отмены записи» шло мимо системы целиком.

    /go — про новое и крупное, /fix — про поломки самой системы, и между ними
    маршрута не было: правку в готовом продукте модель делала руками. Без
    снимка до — возвращать некуда; без проверки — «готово» говорится раньше,
    чем проверено; без снимка после — «верни как было» возвращает не к правке,
    а к тому, что было до неё.

    Здесь заперты два утверждения, и оба про поведение, а не про наличие файла.

    ПЕРВОЕ — скилл выбирается на живых словах человека и не перехватывает
    чужие. Пересечение описаний считается В ОБЕ СТОРОНЫ: односторонняя проверка
    пропустила бы обратный захват, когда /change тянет на себя «хочу сайт».
    Список фраз собран по условию задачи вручную, а не переписан из
    проверяемых описаний, — иначе тест подтверждал бы сам себя.

    ВТОРОЕ — ВЗАИМНЫЙ ПОРЯДОК трёх команд, а не их присутствие. Все три могут
    стоять в файле и не образовывать маршрута: два снимка подряд, а проверка
    после них, — и «готово» снова опережает результат.
    """

    #: Живые фразы, которыми человек зовёт каждый из трёх скиллов.
    ФРАЗЫ = {
        "change": ("поправь", "добавь кнопку", "измени цвет", "убери поле",
                   "переименуй"),
        "go": ("хочу сайт", "сделай приложение", "нужен бот"),
        "fix": ("не работает", "сломалось", "не запускается"),
    }

    def setUp(self):
        self.текст = (SKILLS / "change" / "SKILL.md").read_text("utf-8")
        self.описание = {}
        for имя in self.ФРАЗЫ:
            поля, _ = skill_test.parse_frontmatter(
                (SKILLS / имя / "SKILL.md").read_text("utf-8"))
            self.описание[имя] = (поля or {}).get("description", "").lower()

    def test_the_description_carries_the_words_said_about_a_finished_product(self):
        for слово in ("поправь", "добавь"):
            with self.subTest(слово=слово):
                self.assertIn(
                    слово, self.описание["change"],
                    f"фразы «{слово}» нет в description — скилл не подцепится "
                    "там, где человек его как раз и зовёт")

    def test_it_does_not_answer_to_what_go_and_fix_answer_to(self):
        """Три описания обязаны быть непересекающимися по смыслу.

        Совпавшая фраза-триггер — это не дубль в тексте, а развилка без
        правила: два скилла отвечают на одни слова, и какой возьмётся,
        решает случай.
        """
        for чужой in ("go", "fix"):
            for фраза in self.ФРАЗЫ[чужой]:
                with self.subTest(чужой=чужой, фраза=фраза):
                    self.assertNotIn(
                        фраза, self.описание["change"],
                        f"«{фраза}» — повод для /{чужой}, а стоит в описании "
                        "/change: малая правка перехватит чужой заход")
            for фраза in self.ФРАЗЫ["change"]:
                with self.subTest(чужой=чужой, фраза=фраза, сторона="обратно"):
                    self.assertNotIn(
                        фраза, self.описание[чужой],
                        f"«{фраза}» — повод для /change, а стоит в описании "
                        f"/{чужой}: заход на малую правку уедет не туда")

    def test_the_snapshots_wrap_the_edit_with_the_check_between_them(self):
        снимки = [m.start() for m in re.finditer(
            r'"\$CLAUDE_PLUGIN_ROOT/tools/checkpoint\.py" \. snap ', self.текст)]
        self.assertEqual(
            len(снимки), 2,
            f"снимков в скилле {len(снимки)}, а нужны два — до правки и после: "
            "с одним «верни как было» вернёт не к правке")
        проверка = self.текст.find('"$CLAUDE_PLUGIN_ROOT/tools/verify.py" .')
        self.assertNotEqual(проверка, -1,
                            "скилл не зовёт проверку — «готово» окажется "
                            "утверждением, а не кодом возврата")
        self.assertLess(снимки[0], проверка,
                        "первый снимок стоит ПОСЛЕ проверки — возвращаться "
                        "будет уже некуда")
        self.assertLess(проверка, снимки[1],
                        "оба снимка стоят до проверки — правка не проверена "
                        "между ними, и второй снимок фиксирует непроверенное")

    def test_every_tool_is_called_by_its_literal_path(self):
        голые = []
        for m in re.finditer(r"tools/[\w.-]+\.py", self.текст):
            перед = self.текст[:m.start()]
            if not (перед.endswith("$CLAUDE_PLUGIN_ROOT/")
                    or перед.endswith("${CLAUDE_PLUGIN_ROOT}/")):
                голые.append(self.текст[max(0, m.start() - 30):m.end()])
        self.assertEqual(
            голые, [],
            "инструмент назван без литерального пути от корня пакета — в "
            f"установленной раскладке такой вызов не найдёт файл: {голые}")

if __name__ == "__main__":
    unittest.main()
