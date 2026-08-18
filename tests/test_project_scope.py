#!/usr/bin/env python3
"""SUPERSTACK работает только там, где его позвали.

Плагины Claude Code ставятся глобально: `Scope: user`. Их хуки объявляются без
привязки к проекту, поэтому установленный продукт начинал работать во ВСЕХ
проектах на машине. Найдено заказчиком на живом случае: в соседнем каталоге он
пишет обучающие материалы, а система спрашивала там про уроки и запускала гейт
верификации.

Цена разная по хукам, и самый дорогой — гейт верификации: в чужом проекте он
может НЕ ДАТЬ ЗАКРЫТЬ ХОД, потому что ни тестов, ни плана там нет и не
предполагалось.

Выключатели у продукта были только глобальные (`SUPERSTACK_DISABLE=1`, `PAUSE`)
— то есть «выключить везде». Нужно обратное: «включить там, где позвали».
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO  # noqa: E402

ENABLE = PKG / "tools" / "enable.py"
_s = importlib.util.spec_from_file_location("ss_enable", ENABLE)
en = importlib.util.module_from_spec(_s)
_s.loader.exec_module(en)

#: Хуки, которые срабатывают САМИ. Приветствие в этот список не входит
#: намеренно: оно ограничено счётчиком на машине (три раза за всё время), и
#: человеку, поставившему плагин, полезно узнать об этом хотя бы раз.
AUTO_HOOKS = (
    PKG / "hooks" / "session-lesson.sh",
    PKG / "hooks" / "precompact.sh",
    PKG / "hooks" / "verify-gate.sh",
)


class TestTheRegistry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        os.environ["SUPERSTACK_STATE_DIR"] = str(self.state)
        self.addCleanup(os.environ.pop, "SUPERSTACK_STATE_DIR", None)
        self.proj = Path(self.tmp.name) / "проект"
        self.proj.mkdir()

    def test_a_fresh_machine_has_nothing_enabled(self):
        self.assertIsNone(en.enabled_for(self.proj))

    def test_enabling_is_idempotent(self):
        en.enable(self.proj)
        en.enable(self.proj)
        self.assertEqual(len(en.known()), 1)

    def test_a_subdirectory_counts_as_the_same_project(self):
        """Работа в подкаталоге репозитория — тот же проект. Иначе человек
        завёл корень, спустился в src/ и система замолчала без причины."""
        en.enable(self.proj)
        sub = self.proj / "src" / "глубоко"
        sub.mkdir(parents=True)
        self.assertIsNotNone(en.enabled_for(sub))

    def test_a_sibling_with_a_shared_prefix_is_not_enabled(self):
        """`/a/bc` не заводится записью `/a/b`: строкой одна начинается с
        другой, а каталогами это разные проекты."""
        en.enable(self.proj)
        other = Path(str(self.proj) + "-обучение")
        other.mkdir()
        self.assertIsNone(en.enabled_for(other))

    def test_forget_removes_it(self):
        en.enable(self.proj)
        self.assertTrue(en.forget(self.proj))
        self.assertIsNone(en.enabled_for(self.proj))

    def test_check_exits_one_when_not_enabled(self):
        p = subprocess.run([sys.executable, str(ENABLE), str(self.proj), "--check"],
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "SUPERSTACK_STATE_DIR": str(self.state)})
        self.assertEqual(p.returncode, 1)

    def test_check_exits_zero_after_enabling(self):
        en.enable(self.proj)
        p = subprocess.run([sys.executable, str(ENABLE), str(self.proj), "--check"],
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "SUPERSTACK_STATE_DIR": str(self.state)})
        self.assertEqual(p.returncode, 0)


class TestHooksAreSilentInForeignProjects(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.foreign = Path(self.tmp.name) / "чужой-проект"
        self.foreign.mkdir()
        (self.foreign / "заметка.md").write_text("обучение", encoding="utf-8")

    def _run(self, hook: Path, project: Path):
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state),
               "SUPERSTACK_PROJECT_DIR": str(project)}
        env.pop("SUPERSTACK_DISABLE", None)
        env.pop("SUPERSTACK_IGNORE_PAUSE", None)
        return subprocess.run(["sh", str(hook)], capture_output=True, text=True,
                              timeout=60, env=env, cwd=str(project),
                              input='{"session_id":"x"}')

    def test_every_automatic_hook_is_silent_in_a_project_never_enabled(self):
        for hook in AUTO_HOOKS:
            with self.subTest(hook=hook.name):
                p = self._run(hook, self.foreign)
                self.assertEqual(p.stdout.strip(), "",
                                 f"{hook.name} подал голос в проекте, где "
                                 "SUPERSTACK не звали")
                self.assertEqual(p.returncode, 0,
                                 f"{hook.name} вернул ненулевой код в чужом "
                                 "проекте — это может не дать закрыть ход")

    def test_the_lesson_hook_speaks_once_the_project_is_enabled(self):
        """Обратный контроль: гейт не должен глушить систему там, где её
        позвали, — иначе он лечит шум ценой продукта."""
        (self.state / "projects").write_text(str(self.foreign) + "\n",
                                             encoding="utf-8")
        p = self._run(PKG / "hooks" / "session-lesson.sh",
                      self.foreign)
        self.assertIn("hookSpecificOutput", p.stdout)


class TestTheGateIsInEveryAutomaticHook(unittest.TestCase):
    """Новый автоматический хук без этого гейта вернёт ровно ту болезнь, и
    заметят её опять только по жалобе из чужого проекта."""

    def test_each_hook_reads_the_registry(self):
        for hook in AUTO_HOOKS:
            with self.subTest(hook=hook.name):
                t = hook.read_text("utf-8")
                self.assertIn("$STATE/projects", t,
                              f"{hook.name} не спрашивает, звали ли его здесь")
                self.assertIn("enabled_here", t)

    def test_the_starting_skills_write_the_mark(self):
        """Отметку ставят скиллы, НАЧИНАЮЩИЕ работу. `/what`, `/fix`, `/oops`
        её не ставят намеренно: они диагностика и откат, включать ими систему
        в чужом проекте не за что."""
        for rel in ("plugins/superstack/skills/go/SKILL.md",
                    "plugins/superstack/skills/superstack/SKILL.md"):
            with self.subTest(skill=rel):
                self.assertIn("enable.py", (REPO / rel).read_text("utf-8"),
                              f"{rel} не отмечает проект — хуки останутся "
                              "немыми там, где систему как раз позвали")


if __name__ == "__main__":
    unittest.main()
