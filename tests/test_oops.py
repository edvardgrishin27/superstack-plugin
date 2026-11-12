#!/usr/bin/env python3
"""/oops не выдаёт несостоявшийся откат за состоявшийся.

Проверка кода возврата в oops.py уже стоит: «откатил» печатается только при
нуле. Но ноль приходил снизу и был неправдой — apply.cmd_undo() возвращала
его даже тогда, когда не вернула ни одного файла: все файлы копии помечены
вырезанным секретом, возврат затёр бы живое значение меткой, поэтому они
пропускаются — и это правильно, неправильным был только код.

Итог для человека: он сказал «верни как было», не вернулось ничего — и он
прочитал «откатил». Здесь заперт весь путь целиком, от кода снизу до строки,
которую человек увидит: инструмент честен ровно настолько, насколько честен
самый нижний его слой.

  python3 -m pytest tests/test_oops.py -q
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG  # noqa: E402

#: Имя намеренно своё: соседние файлы набора грузят те же инструменты под
#: своими именами, и совпадение перезаписало бы чужую запись в sys.modules
#: при совместном прогоне.
_spec = importlib.util.spec_from_file_location(
    "ss_oops_" "nothing_" "restored", PKG / "tools" / "oops.py")
oops_mod = importlib.util.module_from_spec(_spec)
sys.modules["ss_oops_" "nothing_" "restored"] = oops_mod
_spec.loader.exec_module(oops_mod)

#: Тот самый объект модуля apply, которым oops.py пользуется изнутри, — иначе
#: подмена путей била бы мимо, по отдельно загруженной копии.
real_apply = oops_mod.apply

#: Метка, которую apply.make_backup() ставит на месте секрета.
REDACTED = '{"permissions": {"allow": ["<ВЫРЕЗАНО-SUPERSTACK fp=ab12 len=24>"]}}'


class TestUndoThatRestoredNothing(unittest.TestCase):

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

    def _backup_with_only_a_redacted_file(self, bid: str = "20260101-000000") -> Path:
        bdir = real_apply.BACKUPS / bid
        bdir.mkdir(parents=True)
        manifest = {
            "created": "2026-01-01T00:00:00+00:00",
            "backup": {"dir": str(bdir), "files": ["settings.json"],
                       "redacted": [{"file": "settings.json", "count": 1}]},
            "applied": [{"id": "ctx.default-mode-unset",
                         "action": "задам permissions.defaultMode = plan",
                         "changed": True, "field": "permissions.defaultMode",
                         "before": "plan", "after": "acceptEdits"}],
        }
        (bdir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False),
                                            encoding="utf-8")
        (bdir / "settings.json").write_text(REDACTED, encoding="utf-8")
        return bdir

    def _run_undo(self, bid: str) -> tuple:
        """main() как «oops.py undo <id>» — без --json, иначе человеческая
        строка не печатается вовсе и проверять нечего."""
        argv_orig = sys.argv
        sys.argv = ["oops.py", "undo", bid]
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = oops_mod.main()
        finally:
            sys.argv = argv_orig
        return rc, err.getvalue(), out.getvalue()

    def test_nothing_restored_is_told_as_not_rolled_back(self):
        bdir = self._backup_with_only_a_redacted_file()
        rc, err, out = self._run_undo(bdir.name)

        self.assertEqual(rc, 1, "несостоявшийся откат не может дать ноль")
        # В stdout сначала идут строки самой apply.cmd_undo(), и только потом
        # JSON — берём его от первой скобки, а не весь поток целиком.
        ответ = json.loads(out[out.index("{"):])
        self.assertEqual(ответ["undo_exit"], real_apply.UNDO_NOTHING_RESTORED,
                         "код снизу обязан отличать «не вернул ничего» от успеха")

        строки = [s for s in err.splitlines() if s.strip()]
        self.assertFalse([s for s in строки if s.startswith("откатил")],
                         f"подтверждение в прошедшем времени: {err}")
        вердикт = строки[-1]
        self.assertTrue(вердикт.startswith("НЕ откатил"),
                        f"последнее слово человеку — не отказ: {вердикт}")
        self.assertIn("секрет", вердикт.lower(), "причина не названа")
        self.assertIn(str(bdir), вердикт, "не сказано, где лежит копия")
        self.assertIn("руками", вердикт, "не назван следующий шаг человека")


class TestOopsSkillTakesTheTargetFromTheTool(unittest.TestCase):
    """Навык не выбирает снимок по месту в списке: цель называет инструмент."""

    def setUp(self):
        self.текст = (PKG / "skills" / "oops" / "SKILL.md").read_text("utf-8")

    def test_no_pick_by_the_word_latest_and_names_the_target_field(self):
        self.assertNotIn("`последний`", self.текст,
                         "навык берёт самый свежий снимок — после правки это "
                         "состояние, в котором человек уже стоит")
        self.assertIn("цель_отката", self.текст)
        self.assertIn("возвращать некуда", self.текст)


if __name__ == "__main__":
    unittest.main()
