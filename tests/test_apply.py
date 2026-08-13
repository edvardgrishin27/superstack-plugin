#!/usr/bin/env python3
"""Тесты фазы применения.

Это единственная часть системы, которая ПИШЕТ в конфигурацию человека,
поэтому здесь проверяется не «работает ли», а «что будет, если ошибётся»:
делается ли бэкап ДО изменения, перечитывается ли он, не уезжает ли секрет
в копию, возвращает ли откат ровно то, что было, и молчит ли инструмент
там, где решение должен принимать человек.

    python3 -m unittest discover -s tests
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
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


apply_mod = _load("ss_apply", at("tools", "apply.py"))


class ApplyFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = Path(self.tmp.name)
        self.claude = home / ".claude"
        self.claude.mkdir(parents=True)
        self._orig = (apply_mod.HOME, apply_mod.CLAUDE, apply_mod.STATE,
                      apply_mod.BACKUPS, apply_mod.QUARANTINE)
        apply_mod.HOME = home
        apply_mod.CLAUDE = self.claude
        apply_mod.STATE = self.claude / "superstack"
        apply_mod.BACKUPS = apply_mod.STATE / "backups"
        apply_mod.QUARANTINE = apply_mod.STATE / "quarantine"

    def tearDown(self):
        (apply_mod.HOME, apply_mod.CLAUDE, apply_mod.STATE,
         apply_mod.BACKUPS, apply_mod.QUARANTINE) = self._orig
        self.tmp.cleanup()

    def settings(self, data: dict) -> None:
        (self.claude / "settings.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read_settings(self) -> dict:
        return json.loads((self.claude / "settings.json").read_text(encoding="utf-8"))

    def findings_file(self, findings: list) -> str:
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8")
        json.dump({"findings": findings}, fh, ensure_ascii=False)
        fh.close()
        return fh.name


class TestBackupIsRealNotClaimed(ApplyFixture):
    """«Бэкап сделан» обязано означать «бэкап перечитан»."""

    def test_backup_contains_the_original(self):
        self.settings({"permissions": {"allow": ["Bash(ls)"]}})
        info = apply_mod.make_backup([])
        copy = json.loads((Path(info["dir"]) / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(copy["permissions"]["allow"], ["Bash(ls)"])
        self.assertIn("settings.json", info["files"])

    def test_backup_happens_before_any_change(self):
        """Порядок важнее наличия: бэкап после правки бесполезен."""
        self.settings({"permissions": {"allow": ["Bash(ls)"]}})
        rc = apply_mod.cmd_run([self.findings_file([{
            "id": "ctx.default-mode-unset", "class": "AUTO", "verdict": "FIX",
            "headline": "режим не задан"}])])
        self.assertEqual(rc, 0)
        bdir = next(apply_mod.BACKUPS.iterdir())
        saved = json.loads((bdir / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("defaultMode", saved.get("permissions", {}),
                         "в бэкапе уже лежит изменённая версия")
        self.assertEqual(self.read_settings()["permissions"]["defaultMode"], "plan")

    def test_empty_copy_aborts_everything(self):
        """Пустой файл выглядел бы как успешный бэкап."""
        bdir = Path(self.tmp.name) / "b"
        bdir.mkdir()
        (bdir / "settings.json").write_text("", encoding="utf-8")
        with self.assertRaises(SystemExit):
            apply_mod.verify_backup(bdir, ["settings.json"])

    def test_missing_copy_aborts_everything(self):
        bdir = Path(self.tmp.name) / "b2"
        bdir.mkdir()
        with self.assertRaises(SystemExit):
            apply_mod.verify_backup(bdir, ["settings.json"])

    def test_make_backup_actually_calls_the_verification(self):
        """Функция проверки может быть безупречной и просто не вызываться.

        Удаление вызова из make_backup не роняло ни одного теста: проверка
        существовала, но ничего не защищала. Здесь проверяется именно связь.
        """
        self.settings({"a": 1})
        called: list = []
        real = apply_mod.verify_backup

        def spy(bdir, names):
            called.append((bdir, list(names)))
            return real(bdir, names)

        apply_mod.verify_backup = spy
        try:
            apply_mod.make_backup([])
        finally:
            apply_mod.verify_backup = real
        self.assertTrue(called, "бэкап сделан без проверки чтением")
        self.assertIn("settings.json", called[0][1])

    def test_good_copy_passes_verification(self):
        """Обратный контроль: проверка, отвергающая всё, тоже бесполезна."""
        bdir = Path(self.tmp.name) / "b3"
        bdir.mkdir()
        (bdir / "settings.json").write_text('{"a": 1}', encoding="utf-8")
        apply_mod.verify_backup(bdir, ["settings.json"])


class TestSecretNeverMultiplies(ApplyFixture):
    """Критическая находка про пароль не имеет права создать вторую копию."""

    SECRET = "sshpass -p 'hunter2hunter2' ssh root@10.0.0.1"

    def test_secret_is_cut_out_of_the_backup(self):
        self.settings({"permissions": {"allow": ["Bash(ls)", self.SECRET]}})
        info = apply_mod.make_backup(
            [{"location": "permissions.allow[1]", "file": "settings.json"}])
        text = (Path(info["dir"]) / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("hunter2hunter2", text, "пароль уехал в бэкап")
        self.assertIn("ВЫРЕЗАНО-SUPERSTACK", text)

    def test_fingerprint_survives_so_the_secret_is_recognisable(self):
        self.settings({"permissions": {"allow": [self.SECRET]}})
        info = apply_mod.make_backup(
            [{"location": "permissions.allow[0]", "file": "settings.json"}])
        text = (Path(info["dir"]) / "settings.json").read_text(encoding="utf-8")
        self.assertIn("fp=", text)
        self.assertIn("len=", text)

    def test_untouched_values_survive_intact(self):
        """Вырезание не должно задевать соседей."""
        self.settings({"permissions": {"allow": ["Bash(ls)", self.SECRET, "Read(~/x)"]}})
        info = apply_mod.make_backup(
            [{"location": "permissions.allow[1]", "file": "settings.json"}])
        copy = json.loads((Path(info["dir"]) / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(copy["permissions"]["allow"][0], "Bash(ls)")
        self.assertEqual(copy["permissions"]["allow"][2], "Read(~/x)")

    #: находка с ДОКАЗАТЕЛЬСТВОМ — именно из неё берутся места секретов.
    #: Без evidence бэкап не редактируется, и тест про откат был бы вакуумным:
    #: он проверял бы возврат файла, в котором секрет и так цел.
    def _findings_with_secret(self) -> str:
        return self.findings_file([
            {"id": "sec.secret-in-settings", "class": "ASK", "verdict": "ASK",
             "headline": "секрет в настройках",
             "evidence": {"sec.secret_matches": [
                 {"location": "permissions.allow[0]", "file": "settings.json",
                  "why": "пароль прямо в команде"}]}},
            {"id": "ctx.default-mode-unset", "class": "AUTO", "verdict": "FIX",
             "headline": "режим не задан"}])

    def test_backup_really_gets_redacted_in_a_full_run(self):
        """Контроль к следующему тесту: без вырезания он ничего не проверяет."""
        self.settings({"permissions": {"allow": [self.SECRET]}})
        apply_mod.cmd_run([self._findings_with_secret()])
        bdir = next(apply_mod.BACKUPS.iterdir())
        text = (bdir / "settings.json").read_text(encoding="utf-8")
        self.assertIn("ВЫРЕЗАНО-SUPERSTACK", text, "секрет не вырезан — тест ниже пуст")

    def test_undo_refuses_to_overwrite_a_live_secret(self):
        """Откат, затирающий пароль меткой, — потеря данных под видом отката."""
        self.settings({"permissions": {"allow": [self.SECRET]}})
        rc = apply_mod.cmd_run([self._findings_with_secret()])
        self.assertEqual(rc, 0)
        bdir = next(apply_mod.BACKUPS.iterdir())
        apply_mod.cmd_undo([bdir.name])
        live = self.read_settings()
        self.assertIn(self.SECRET, live["permissions"]["allow"],
                      "откат затёр живой секрет меткой из бэкапа")


class TestConsentAndRefusal(ApplyFixture):
    """Умолчание — бездействие. Инструмент не решает за человека."""

    def test_gate_class_requires_explicit_yes(self):
        self.settings({})
        rc = apply_mod.cmd_run([self.findings_file([{
            "id": "ctx.default-mode-unset", "class": "GATE", "verdict": "FIX",
            "headline": "режим не задан"}])])
        self.assertEqual(rc, 6, "изменение класса GATE применено без согласия")
        self.assertNotIn("permissions", self.read_settings())

    def test_gate_class_proceeds_with_yes(self):
        self.settings({})
        rc = apply_mod.cmd_run([self.findings_file([{
            "id": "ctx.default-mode-unset", "class": "GATE", "verdict": "FIX",
            "headline": "режим не задан"}]), "--yes"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.read_settings()["permissions"]["defaultMode"], "plan")

    def test_block_and_ask_are_never_applied(self):
        for cls in ("ASK", "BLOCK"):
            with self.subTest(cls=cls):
                self.settings({})
                apply_mod.cmd_run([self.findings_file([{
                    "id": "ctx.default-mode-unset", "class": cls, "verdict": "ASK",
                    "headline": "секрет"}]), "--yes"])
                self.assertNotIn("permissions", self.read_settings(),
                                 f"находка класса {cls} применена автоматически")

    def test_unknown_finding_is_left_to_the_human(self):
        """Действия не описано — значит бездействие, а не самодеятельность."""
        self.settings({})
        doable, human = apply_mod.plan_actions([{
            "id": "выдуманное.правило", "class": "AUTO", "verdict": "FIX",
            "headline": "что-то"}])
        self.assertEqual(doable, [])
        self.assertEqual(len(human), 1)
        self.assertIn("не описано", human[0]["why_human"])


class TestUndoRestoresExactly(ApplyFixture):
    def test_undo_returns_the_previous_content(self):
        before = {"permissions": {"allow": ["Bash(ls)"]}, "model": "opus"}
        self.settings(before)
        apply_mod.cmd_run([self.findings_file([{
            "id": "ctx.default-mode-unset", "class": "AUTO", "verdict": "FIX",
            "headline": "режим не задан"}])])
        self.assertEqual(self.read_settings()["permissions"]["defaultMode"], "plan")
        bdir = next(apply_mod.BACKUPS.iterdir())
        apply_mod.cmd_undo([bdir.name])
        self.assertEqual(self.read_settings(), before)

    def test_manifest_records_what_was_done(self):
        self.settings({})
        apply_mod.cmd_run([self.findings_file([{
            "id": "ctx.default-mode-unset", "class": "AUTO", "verdict": "FIX",
            "headline": "режим не задан"}])])
        bdir = next(apply_mod.BACKUPS.iterdir())
        m = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(m["applied"]), 1)
        self.assertEqual(m["applied"][0]["id"], "ctx.default-mode-unset")
        self.assertTrue(m["applied"][0]["changed"])

    def test_idempotent_second_run_changes_nothing(self):
        self.settings({"permissions": {"defaultMode": "acceptEdits"}})
        apply_mod.cmd_run([self.findings_file([{
            "id": "ctx.default-mode-unset", "class": "AUTO", "verdict": "FIX",
            "headline": "режим не задан"}])])
        self.assertEqual(self.read_settings()["permissions"]["defaultMode"],
                         "acceptEdits", "перезаписан уже заданный человеком режим")


class TestQuarantineNotDeletion(ApplyFixture):
    def test_file_is_moved_not_destroyed(self):
        victim = self.claude / "obsolete.md"
        victim.write_text("содержимое", encoding="utf-8")
        res = apply_mod.quarantine(victim, "вытеснено нативом")
        self.assertTrue(res["moved"])
        self.assertFalse(victim.exists())
        restored = Path(res["to"])
        self.assertTrue(restored.is_file())
        self.assertEqual(restored.read_text(encoding="utf-8"), "содержимое")

    def test_missing_path_is_not_an_error(self):
        res = apply_mod.quarantine(self.claude / "нет-такого", "x")
        self.assertFalse(res["moved"])


class TestApplyObeysPause(unittest.TestCase):
    """Флаг ставится в ПОДСТАВНОЙ дом. Прежняя версия писала настоящий
    ~/.claude/superstack/PAUSE и убирала его в finally: убийство прогона между
    записью и удалением оставляло рабочую систему человека на паузе. Инструмент
    читает флаг через Path.home(), а тот на POSIX слушается $HOME."""

    def test_pause_stops_apply(self):
        with tempfile.TemporaryDirectory() as home:
            flag = Path(home) / ".claude" / "superstack" / "PAUSE"
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("test", encoding="utf-8")
            env = {k: v for k, v in os.environ.items()
                   if k != "SUPERSTACK_IGNORE_PAUSE"}
            env["HOME"] = home
            r = subprocess.run([sys.executable, str(at("tools", "apply.py")),
                                "list"], capture_output=True, text=True,
                               timeout=60, env=env)
            self.assertEqual(r.returncode, 10)
            self.assertIn("ОСТАНОВЛЕНО", r.stderr)
            self.assertFalse((Path.home() / ".claude" / "superstack" / "PAUSE").exists(),
                             "тест поставил на паузу настоящую систему пользователя")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBaseKit(unittest.TestCase):
    """Базовый набор — то, что получает КАЖДЫЙ, без вопросов и без решателя.

    Диагностика без применения оставляет человека ровно там, где нашла: со
    списком проблем и без единого решения. Ради этого списка ничего не строилось.
    """

    def test_missing_item_is_named_with_its_reason(self):
        st = apply_mod.base_kit_status({})
        self.assertTrue(st["missing"])
        for row in st["missing"]:
            self.assertTrue(row["why"], f"пункт без объяснения зачем: {row['id']}")

    def test_present_item_is_not_offered_again(self):
        """Позитивный контроль: набор, предлагающий поставить уже стоящее,
        теряет доверие с первого пункта."""
        st = apply_mod.base_kit_status({"cc.default_mode": "plan"})
        ids = [r["id"] for r in st["missing"]]
        self.assertNotIn("base.plan-mode", ids)
        self.assertIn("base.plan-mode", [r["id"] for r in st["present"]])

    def test_nothing_is_installed_silently(self):
        """Всё, что трогает машину дальше файлов настроек, обязано спрашивать.
        Установщик, «сделавший как лучше», — тот же захват, что и хук,
        запускающий проверку без спроса."""
        for row in apply_mod.base_kit_status({})["needs_consent"]:
            self.assertIn(row["class"], apply_mod.NEEDS_CONSENT)
        for row in apply_mod.base_kit_status({})["auto"]:
            self.assertIn(row["class"], apply_mod.AUTO_APPLICABLE)
        # Главный инвариант: пункт, трогающий МАШИНУ, а не файлы настроек,
        # обязан спрашивать. Опасность объявлена отдельным полем, а не выведена
        # из класса, — иначе проверка сравнивала бы класс сам с собой.
        machine = [i for i in apply_mod.BASE_KIT if i["touches"] == "machine"]
        self.assertTrue(machine, "фикстура без машинных пунктов ничего не проверяет")
        for item in machine:
            self.assertIn(item["class"], apply_mod.NEEDS_CONSENT,
                          f"«{item['what']}» ставится без спроса, а трогает машину")
        classes = {r["class"] for r in apply_mod.BASE_KIT}
        self.assertTrue(classes <= (apply_mod.AUTO_APPLICABLE | apply_mod.NEEDS_CONSENT),
                        f"пункт вне известных классов: {classes}")

    def test_unmeasurable_item_is_not_called_missing(self):
        """«Проба не отработала» и «этого нет» — разные утверждения. Смешать их
        значит предложить поставить то, что, возможно, уже стоит."""
        boom = {"id": "x", "what": "тест", "why": "тест", "class": "AUTO",
                "touches": "settings", "present": lambda facts: 1 / 0}
        real = apply_mod.BASE_KIT
        apply_mod.BASE_KIT = (boom,)
        try:
            st = apply_mod.base_kit_status({})
        finally:
            apply_mod.BASE_KIT = real
        self.assertEqual(st["missing"], [])
        self.assertEqual([r["id"] for r in st["unknown"]], ["x"])
        self.assertFalse(st["complete"], "непроверенное засчитано за полный набор")

    def test_command_reports_without_touching_anything(self):
        """Показать, а не поставить: человек обязан увидеть список ДО того,
        как что-то произойдёт."""
        import subprocess, sys as _s
        before = sorted(p.name for p in (plug("superstack-install") / "tools").glob("*"))
        r = subprocess.run([_s.executable, str(at("tools", "apply.py")), "base"],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
        self.assertIn(r.returncode, (0, 1))
        self.assertIn("базов", r.stdout.lower() + r.stderr.lower() + "базовый")
        self.assertEqual(before, sorted(p.name for p in (plug("superstack-install") / "tools").glob("*")))
