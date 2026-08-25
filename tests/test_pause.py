#!/usr/bin/env python3
"""Тесты аварийного тормоза (tools/pause.sh).

Зачем этот файл существует.

Механизм L0 «kill-switch» засчитывался ПРИСУТСТВИЕМ файла: улика в плане —
подстрока «PAUSE», которой хватало и комментария. Сам скрипт не запускал никто:
остальные тесты создавали флаг паузы руками, минуя его. То есть pause.sh мог
писать не туда, куда смотрят инструменты, или всегда отвечать «ок» — и планка
этого бы не увидела. Тормоз, про который известно только что файл лежит на
диске, тормозом не является.

Здесь скрипт ЗАПУСКАЕТСЯ, и держится главное: после `pause.sh on` настоящий
инструмент действительно останавливается, а после `off` — действительно
продолжает работать.

Герметичность: HOME подставной, настоящий ~/.claude не читается и не пишется.
SUPERSTACK_IGNORE_PAUSE снимается намеренно — иначе проверялось бы обратное.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at  # noqa: E402

ROOT = REPO
PAUSE = at("tools", "pause.sh")
VERIFY = at("tools", "verify.py")

#: Код, которым и скрипт, и инструменты сообщают «система на паузе».
PAUSED_CODE = 10


class PauseFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(parents=True)
        self.project = Path(self.tmp.name) / "project"
        (self.project / "tests").mkdir(parents=True)
        (self.project / "tests" / "test_ok.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8")
        # HOME подставной, а признак «игнорировать паузу» снят: под планкой он
        # выставлен в 1 на весь набор, и с ним тест проверял бы, что тормоз НЕ
        # работает.
        self.env = {k: v for k, v in os.environ.items()
                    if k != "SUPERSTACK_IGNORE_PAUSE"}
        self.env["HOME"] = str(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def pause(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(PAUSE)] + list(args),
                              capture_output=True, text=True, timeout=60,
                              env=self.env)

    def verify(self) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(VERIFY), "--json",
                               str(self.project)],
                              capture_output=True, text=True, timeout=300,
                              env=self.env)

    @property
    def flag(self) -> Path:
        return self.home / ".claude" / "superstack" / "PAUSE"


class TestBrakeActuallyBrakes(PauseFixture):
    """Единственная проверка, которая что-то значит: останавливается ли
    инструмент после того, как тормоз дёрнули СКРИПТОМ, а не руками."""

    def test_on_stops_a_real_tool(self):
        r = self.pause("on")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.flag.is_file(), "флаг не там, где его ищут инструменты")

        v = self.verify()
        self.assertEqual(v.returncode, PAUSED_CODE,
                         f"инструмент не остановился на паузе: {v.stdout}{v.stderr}")
        self.assertIn("ОСТАНОВЛЕНО", v.stderr)
        self.assertEqual(v.stdout, "", "остановленный инструмент всё равно отчитался")

    def test_off_releases_the_brake(self):
        """Тормоз, который нельзя отпустить, — не тормоз, а поломка."""
        self.pause("on")
        r = self.pause("off")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.flag.exists())

        v = self.verify()
        self.assertNotEqual(v.returncode, PAUSED_CODE, v.stderr)
        # Проверяем, что инструмент СНОВА РАБОТАЕТ, а не какой он вынес вердикт:
        # вердикт зависел бы от того, установлен ли pytest на этой машине.
        self.assertEqual(json.loads(v.stdout)["gate"], "verify", v.stdout)

    def test_status_reports_paused_with_its_own_code(self):
        """Состояние читается скриптом, а не глазами: код возврата 10 — то, на
        чём строит решение вызывающий."""
        self.assertEqual(self.pause().returncode, 0,
                         "чистая машина названа остановленной")
        self.pause("on")
        r = self.pause()
        self.assertEqual(r.returncode, PAUSED_CODE, r.stdout + r.stderr)
        self.assertIn("НА ПАУЗЕ", r.stdout)

    def test_confirmation_is_printed_only_after_the_flag_is_on_disk(self):
        """Раньше «ПАУЗА» печаталась безусловно: человек в аварийной ситуации
        читал «остановлено», а система продолжала работать."""
        if os.geteuid() == 0:
            self.skipTest("под root права на каталог не ограничивают запись")
        state = self.home / ".claude" / "superstack"
        state.mkdir(parents=True)
        state.chmod(0o500)                    # каталог есть, писать в него нельзя
        try:
            r = self.pause("on")
        finally:
            state.chmod(0o700)
        self.assertNotEqual(r.returncode, 0,
                            "неудавшаяся пауза отчиталась успехом")
        self.assertNotIn("ПАУЗА подтверждена", r.stdout,
                         "напечатано «остановлено», хотя флага на диске нет")
        self.assertFalse(self.flag.exists())


    def test_removing_a_pause_that_was_not_there_says_so(self):
        """«Снято» и «нечего было снимать» — разные события.

        Одинаковый ответ на оба означает, что подтверждение ничего не
        подтверждает: человек, промахнувшийся файлом или снявший чужую паузу,
        читает тот же успех, что и снявший свою.
        """
        r = self.pause("off")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("паузы не было", r.stdout)

    def test_removing_a_real_pause_says_it_was_removed(self):
        self.pause("on")
        r = self.pause("off")
        self.assertIn("пауза снята", r.stdout)
        self.assertFalse(self.flag.exists())

    def test_the_hint_is_copyable_when_the_path_has_spaces(self):
        """Путь к скрипту лежит в каталоге с пробелом и кириллицей. Строка без
        кавычек копируется в неработающую команду — ровно тогда, когда человек
        торопится больше всего."""
        r = self.pause("on")
        подсказка = [s for s in r.stdout.splitlines() if s.startswith("Снять:")]
        self.assertTrue(подсказка, r.stdout)
        self.assertIn('"', подсказка[0],
                      "путь в подсказке не в кавычках — команда не скопируется")


if __name__ == "__main__":
    unittest.main()
