#!/usr/bin/env python3
"""Тесты спускового крючка памяти.

Журнал находок был построен целиком — планка из трёх условий, маршрутизация,
слияние — и НИКТО его не вызывал. Одна запись за всю разработку, вписанная
руками. Механизм без спускового крючка не работает так же, как отсутствующий,
но выглядит построенным.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import plug  # noqa: E402

HOOK = plug("superstack-brain") / "hooks" / "session-lesson.sh"
LEARN = plug("superstack-brain") / "tools" / "learn.py"


def run(state: Path, script: Path = HOOK, disable: str = "") -> subprocess.CompletedProcess:
    # Проект заводится, иначе хук молчит: он работает только там, где SUPERSTACK
    # позвали. Здесь проверяется САМ вопрос про урок — гейт области проверяется
    # отдельно, в test_project_scope.py.
    state.mkdir(parents=True, exist_ok=True)
    (state / "projects").write_text(os.getcwd() + "\n", encoding="utf-8")
    env = {**os.environ, "SUPERSTACK_STATE_DIR": str(state)}
    env.pop("SUPERSTACK_DISABLE", None)
    # Пауза проверяется только когда её не отключили извне. Набор гоняется с
    # SUPERSTACK_IGNORE_PAUSE=1, и без явного сброса тест доказывал бы
    # настройку прогона, а не поведение хука.
    env.pop("SUPERSTACK_IGNORE_PAUSE", None)
    if disable:
        env["SUPERSTACK_DISABLE"] = disable
    return subprocess.run(["sh", str(script)], capture_output=True, text=True,
                          timeout=30, env=env)


class TestTheQuestionIsAsked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def ctx(self) -> str:
        out = run(self.state).stdout
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def test_output_is_valid_json_for_claude_code(self):
        d = json.loads(run(self.state).stdout)
        self.assertEqual(d["hookSpecificOutput"]["hookEventName"], "Stop")

    def test_all_three_bar_conditions_are_named(self):
        """Урок без отброшенного тупика бесполезен: в следующий раз в этот
        тупик зайдут снова."""
        c = self.ctx()
        for word in ("проверка", "паттерн отказа", "тупик"):
            self.assertIn(word, c, f"условие планки не названо: {word}")

    def test_the_command_is_complete_enough_to_run(self):
        """Строка, начинающаяся с «--», молча съедалась printf: синтаксис
        верен, текст урезан, и агент получал команду без половины ключей."""
        c = self.ctx()
        for key in ("--title", "--check", "--failure", "--deadend"):
            self.assertIn(key, c, f"ключ потерян в выводе: {key}")

    def test_silence_is_named_as_the_usual_answer(self):
        """Хук, вынуждающий что-нибудь записать, наполняет память шумом,
        а шумную память перестают читать — она умирает тише, чем пустая."""
        self.assertIn("НЕ ЗАПИСЫВАЙ", self.ctx())

    def test_path_points_at_a_real_tool(self):
        c = self.ctx()
        import re
        # Путь взят в одиночные кавычки И содержит пробел: разбиение по
        # пробелам даёт обрывок. Достаём ровно то, что заключено в кавычки.
        m = re.search(r"'([^']*learn\.py)'", c)
        self.assertIsNotNone(m, f"путь к инструменту не назван: {c[:120]}")
        self.assertTrue(Path(m.group(1)).is_file(),
                        f"путь ведёт в никуда: {m.group(1)}")

    def test_kill_switch_silences_it(self):
        self.assertEqual(run(self.state, disable="1").stdout.strip(), "")

    def test_pause_silences_it(self):
        (self.state / "PAUSE").write_text("test", encoding="utf-8")
        self.assertEqual(run(self.state).stdout.strip(), "")

    def test_always_exits_zero(self):
        """Stop-хук не имеет права уронить закрытие хода."""
        self.assertEqual(run(self.state).returncode, 0)

    def test_silent_when_the_tool_is_missing(self):
        """Вопрос без адреса бесполезен: человек прочитает просьбу записать
        урок и не найдёт куда."""
        d = Path(self.tmp.name) / "копия"
        (d / "hooks").mkdir(parents=True)
        shutil.copy2(HOOK, d / "hooks" / "session-lesson.sh")
        self.assertEqual(run(self.state, script=d / "hooks" / "session-lesson.sh")
                         .stdout.strip(), "")

    def test_path_with_a_space_survives(self):
        d = Path(self.tmp.name) / "каталог с пробелом"
        (d / "hooks").mkdir(parents=True)
        (d / "tools").mkdir(parents=True)
        shutil.copy2(HOOK, d / "hooks" / "session-lesson.sh")
        shutil.copy2(LEARN, d / "tools" / "learn.py")
        out = run(self.state, script=d / "hooks" / "session-lesson.sh").stdout
        c = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("каталог с пробелом", c)


class TestAQuietTurnIsNotAsked(unittest.TestCase):
    """Ход, в котором ничего не изменилось, не может содержать урок.

    Планка требует пройденную проверку — проверять нечего. Но вопрос стоит
    ровно один лишний ход: агент обязан ответить, ответ порождает новый Stop,
    и разговор встаёт в петлю коротких реплик. Наблюдалось живьём: четыре хода
    подряд вида «готово к перезапуску», каждый вызванный предыдущим.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        (self.work / "файл.txt").write_text("до", encoding="utf-8")

    def _run(self):
        (self.state / "projects").write_text(str(self.work) + "\n",
                                             encoding="utf-8")
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state),
               "SUPERSTACK_PROJECT_DIR": str(self.work)}
        env.pop("SUPERSTACK_DISABLE", None)
        env.pop("SUPERSTACK_IGNORE_PAUSE", None)
        return subprocess.run(["sh", str(HOOK)], capture_output=True, text=True,
                              timeout=30, env=env, cwd=str(self.work))

    def test_the_first_turn_is_always_asked(self):
        self.assertIn("hookSpecificOutput", self._run().stdout)

    def test_a_turn_that_changed_nothing_is_silent(self):
        self.assertIn("hookSpecificOutput", self._run().stdout)
        self.assertEqual(self._run().stdout.strip(), "",
                         "вопрос задан на ходу без единого изменения — "
                         "агент обязан ответить, и разговор уходит в петлю")

    def test_a_turn_that_changed_a_file_is_asked_again(self):
        self._run()
        time.sleep(1.1)  # разрешение mtime на некоторых ФС — секунда
        (self.work / "файл.txt").write_text("после", encoding="utf-8")
        self.assertIn("hookSpecificOutput", self._run().stdout,
                      "работа сделана, а урок не спросили — журнал снова "
                      "останется пустым")

    def test_a_disabled_hook_does_not_move_the_mark(self):
        """Иначе выключенный или приостановленный хук сдвигал бы отсчёт и
        терял работу, сделанную за это время."""
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state),
               "SUPERSTACK_DISABLE": "1"}
        subprocess.run(["sh", str(HOOK)], capture_output=True, text=True,
                       timeout=30, env=env, cwd=str(self.work))
        self.assertFalse((self.state / "last-lesson-ask").exists())


class TestHookIsWired(unittest.TestCase):
    def test_stop_hook_is_declared_by_its_owner(self):
        """Механизм, не подключённый в hooks.json, — файл, который никто
        никогда не вызовет. Именно так журнал и простоял пустым."""
        cfg = json.loads((plug("superstack-brain") / "hooks" / "hooks.json")
                         .read_text("utf-8"))["hooks"]
        cmds = [h["command"] for e in cfg.get("Stop", []) for h in e["hooks"]]
        self.assertTrue(any("session-lesson.sh" in c for c in cmds),
                        f"крючок не подключён: {cmds}")
        self.assertTrue(all("CLAUDE_PLUGIN_ROOT" in c for c in cmds))


if __name__ == "__main__":
    unittest.main()
