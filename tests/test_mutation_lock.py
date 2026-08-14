#!/usr/bin/env python3
"""Замок на мутации: два харнесса не мутируют одно дерево.

Найдено не рассуждением, а трижды за одну сессию.

Первый раз — восемь ложных падений от мутации, оставшейся в `log.py` после
убитого прогона. Второй — «застрявшая» мутация, оказывавшаяся РАЗНОЙ при каждом
замере, потому что фоновая планка мутировала дерево под тестами. Третий — я сам
запустил починку поверх идущей проверки и вырвал файл у неё из-под рук.

Общая причина одна: механизм без замка не защищает даже от собственного автора.
Каждый процесс видит чужую поломку и честно относит её на свой счёт — измерение
при этом остаётся правдоподобным, и в этом вся беда.

Мёртвый замок снимается сам: процесс, убитый по SIGKILL, файл за собой не
уберёт, и вечная блокировка от прошлого прогона — это отказ мерить навсегда.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paths import REPO

_spec = importlib.util.spec_from_file_location("gauntlet_lock",
                                               REPO / "tools" / "gauntlet.py")
gt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gt)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plug = Path(self.tmp.name)
        self._orig = gt.PLUG
        gt.PLUG = self.plug
        self.addCleanup(setattr, gt, "PLUG", self._orig)

    def write_lock(self, pid: int) -> None:
        (self.plug / ".mutation-lock").write_text(f"{pid}\n", encoding="utf-8")

    @staticmethod
    def dead_pid() -> int:
        """Пид процесса, который точно завершился и уже пожат."""
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        return p.pid


class TestTheLockIsTaken(Base):

    def test_free_tree_gives_the_lock(self):
        self.assertIsNone(gt.acquire_lock())
        self.assertTrue((self.plug / ".mutation-lock").is_file())

    def test_lock_records_who_holds_it(self):
        gt.acquire_lock()
        self.assertEqual((self.plug / ".mutation-lock").read_text("utf-8").strip(),
                         str(os.getpid()))

    def test_release_frees_the_tree(self):
        gt.acquire_lock()
        gt.release_lock()
        self.assertFalse((self.plug / ".mutation-lock").exists())

    def test_release_without_a_lock_is_silent(self):
        gt.release_lock()   # не должно бросать

    def test_our_own_lock_is_not_a_blocker(self):
        """Повторный вход того же процесса — не конфликт. Иначе прогон
        заперся бы собственным замком на второй мутации."""
        gt.acquire_lock()
        self.assertIsNone(gt.acquire_lock())


class TestALiveHolderBlocks(Base):

    def test_live_foreign_pid_refuses_the_lock(self):
        # pid 1 существует всегда и точно не наш.
        self.write_lock(1)
        held = gt.acquire_lock()
        self.assertIsNotNone(held)
        self.assertIn("уже мутирует", held)

    def test_dead_holder_does_not_block_forever(self):
        """Процесс, убитый по SIGKILL, замок за собой не снимет. Вечная
        блокировка от прошлого прогона — это отказ мерить навсегда, то есть
        та же поломка с другой стороны."""
        self.write_lock(self.dead_pid())
        self.assertIsNone(gt.acquire_lock())

    def test_unreadable_lock_does_not_block_forever(self):
        (self.plug / ".mutation-lock").write_text("не пид\n", encoding="utf-8")
        self.assertIsNone(gt.acquire_lock())


class TestRepairDoesNotInterruptAMeasurement(Base):
    """Мой собственный третий случай: починка поверх идущей проверки."""

    def test_restore_refuses_while_someone_is_measuring(self):
        (self.plug / "tests").mkdir(parents=True)
        (self.plug / "tests" / "mutations.json").write_text(
            '{"mutations": []}', encoding="utf-8")
        self.write_lock(1)
        r = gt.restore_stuck()
        self.assertEqual(r["status"], "unknown")
        self.assertIn("прямо сейчас мутирует", r["detail"])

    def test_restore_names_why_it_refuses(self):
        """«Не буду» без причины читается как поломка починки. Отказ обязан
        сказать, что застрявшее применено намеренно."""
        (self.plug / "tests").mkdir(parents=True)
        (self.plug / "tests" / "mutations.json").write_text(
            '{"mutations": []}', encoding="utf-8")
        self.write_lock(1)
        self.assertIn("применено намеренно", gt.restore_stuck()["detail"])

    def test_restore_works_when_the_holder_is_dead(self):
        (self.plug / "tests").mkdir(parents=True)
        (self.plug / "tools").mkdir(parents=True)
        (self.plug / "tools" / "t.py").write_text("x = BROKEN\n", encoding="utf-8")
        (self.plug / "tests" / "mutations.json").write_text(
            '{"mutations": [{"id": "m1", "file": "tools/t.py", "find": "GOOD", '
            '"replace": "BROKEN", "why": "поломка"}]}', encoding="utf-8")
        self.write_lock(self.dead_pid())
        r = gt.restore_stuck()
        self.assertEqual(r["status"], "pass", r)
        self.assertEqual((self.plug / "tools" / "t.py").read_text("utf-8"), "x = GOOD\n")


class TestAliveness(Base):

    def test_our_own_process_is_alive(self):
        self.assertTrue(gt._alive(os.getpid()))

    def test_a_reaped_process_is_not(self):
        self.assertFalse(gt._alive(self.dead_pid()))

    def test_doubt_resolves_to_occupied(self):
        """Ошибиться в сторону «жив» стоит одного отказа мерить; в обратную —
        двух харнессов на одном дереве, и это уже было трижды."""
        self.assertTrue(gt._alive(1))


if __name__ == "__main__":
    unittest.main()
