#!/usr/bin/env python3
"""Застрявшая мутация: находится, чинится и не превращается в ложное зелёное.

Обнаружение существовало и не спасло — потому что набор гоняют напрямую,
`pytest tests/`, а туда сторож планки не доставал. Прерванный прогон оставил
в `log.py` одну строку `if False:`, и набор показал ВОСЕМЬ падений в четырёх
файлах, ни одно из которых не было дефектом кода.

Здесь заперты три вещи, каждая из которых уже подводила:

  1. ПОЧИНКА, а не только диагноз. Раньше чинили `git checkout` — сработало
     только потому, что поломка не была закоммичена. В чужом проекте, куда
     ворота и предназначены, дерево обычно грязное, и откат файла целиком снёс
     бы вместе с мутацией живую работу человека.
  2. ПОЧИНКА ПРОВЕРЯЕТСЯ. Записал и объявил — то же «агент сказал», против
     которого написана система.
  3. СТОРОЖ МОЛЧИТ В ВОРОТАХ МУТАЦИЙ. Это самый дорогой из возможных отказов:
     сорвав набор до единого теста, сторож вернул бы ненулевой код, ворота
     прочли бы его как «мутация поймана», и весь набор мутаций отчитался бы
     пойманным, не проверив ни одной.
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

from paths import REPO

_spec = importlib.util.spec_from_file_location("gauntlet_unstick",
                                               REPO / "tools" / "gauntlet.py")
gt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gt)


class TestRestorePutsTheCodeBack(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plug = Path(self.tmp.name)
        (self.plug / "tests").mkdir(parents=True)
        (self.plug / "tools").mkdir(parents=True)
        self._orig = gt.PLUG
        gt.PLUG = self.plug
        self.addCleanup(setattr, gt, "PLUG", self._orig)
        self.addCleanup(self.tmp.cleanup)

    def _write(self, body: str, find: str = "GOOD", replace: str = "BROKEN"):
        (self.plug / "tools" / "t.py").write_text(body, encoding="utf-8")
        (self.plug / "tests" / "mutations.json").write_text(json.dumps(
            {"mutations": [{"id": "m1", "file": "tools/t.py", "find": find,
                            "replace": replace, "why": "поломка"}]},
            ensure_ascii=False), encoding="utf-8")

    def test_stuck_mutation_is_put_back(self):
        self._write("x = BROKEN\n")
        r = gt.restore_stuck()
        self.assertEqual(r["status"], "pass", r)
        self.assertEqual([x["id"] for x in r["restored"]], ["m1"])
        self.assertEqual((self.plug / "tools" / "t.py").read_text("utf-8"),
                         "x = GOOD\n")

    def test_restore_is_byte_exact(self):
        """Возврат обязан дать РОВНО прежний файл, а не похожий: лишний
        перевод строки или потерянный отступ — это уже другая правка, и
        человек будет искать её в своём дифе как собственную."""
        before = "# шапка\nx = GOOD\n\n\ny = 2  # хвост без перевода"
        self._write(before.replace("GOOD", "BROKEN"))
        gt.restore_stuck()
        self.assertEqual((self.plug / "tools" / "t.py").read_text("utf-8"), before)

    def test_only_the_mutation_is_touched(self):
        """Соседняя работа человека остаётся нетронутой — иначе починка
        поломки стирала бы незакоммиченный код, и её перестанут запускать."""
        self._write("x = BROKEN\nмоя_правка = 'не трогать'\n")
        gt.restore_stuck()
        self.assertIn("моя_правка = 'не трогать'",
                      (self.plug / "tools" / "t.py").read_text("utf-8"))

    def test_clean_tree_changes_nothing(self):
        self._write("x = GOOD\n")
        r = gt.restore_stuck()
        self.assertEqual(r["restored"], [])
        self.assertEqual(r["status"], "pass")
        self.assertEqual((self.plug / "tools" / "t.py").read_text("utf-8"), "x = GOOD\n")

    def test_an_ambiguous_revert_is_refused_not_guessed(self):
        """Несколько вхождений строки замены — угадывать место НЕЛЬЗЯ.

        Прежняя версия возвращала код в первое вхождение и была заперта тестом
        на это. Оно и сломало исходник: поломка заменяла блок на строку
        `continue`, такая строка в файле не одна, и блок вернулся в чужую
        ветку. Файл остался валидным, `--unstick` отчитался «вернул 1», а
        десять тестов упали часом позже — уже как «непонятный регресс».

        Отказ здесь стоит одной ручной правки. Угадывание стоило часа поиска
        дефекта, которого не было.
        """
        self._write("x = BROKEN\n# слово BROKEN в комментарии\n")
        before = (self.plug / "tools" / "t.py").read_text("utf-8")
        r = gt.restore_stuck()
        self.assertEqual(r["status"], "fail", r)
        self.assertEqual((self.plug / "tools" / "t.py").read_text("utf-8"), before,
                         "файл тронут при неоднозначном возврате")
        self.assertIn("2 раз", r["failed"][0]["why"])

    def test_stashed_bytes_restore_exactly_even_when_ambiguous(self):
        """С отложенными байтами неоднозначность перестаёт существовать: искать
        нечего, возвращается ровно то, что было."""
        before = "if a:\n    continue\nif b:\n    БЛОК\n    continue\n"
        (self.plug / "tools" / "t.py").write_text(before, encoding="utf-8")
        (self.plug / "tests" / "mutations.json").write_text(json.dumps(
            {"mutations": [{"id": "m.block", "file": "tools/t.py",
                            "find": "    БЛОК\n    continue",
                            "replace": "    continue", "why": "убран блок"}]},
            ensure_ascii=False), encoding="utf-8")
        gt.stash("m.block", self.plug / "tools" / "t.py", before.encode("utf-8"))
        (self.plug / "tools" / "t.py").write_text(
            before.replace("    БЛОК\n    continue", "    continue", 1),
            encoding="utf-8")
        r = gt.restore_stuck()
        self.assertEqual(r["status"], "pass", r)
        self.assertEqual((self.plug / "tools" / "t.py").read_text("utf-8"), before)

    def test_the_stash_is_dropped_only_after_a_verified_restore(self):
        """Пока файл не восстановлен, отложенная копия — единственная целая."""
        before = "x = GOOD\n"
        (self.plug / "tools" / "t.py").write_text("x = BROKEN\n", encoding="utf-8")
        (self.plug / "tests" / "mutations.json").write_text(json.dumps(
            {"mutations": [{"id": "m1", "file": "tools/t.py", "find": "GOOD",
                            "replace": "BROKEN", "why": "поломка"}]},
            ensure_ascii=False), encoding="utf-8")
        gt.stash("m1", self.plug / "tools" / "t.py", before.encode("utf-8"))
        self.assertIsNotNone(gt._stashed("m1"))
        gt.restore_stuck()
        self.assertIsNone(gt._stashed("m1"), "копия снята, а файл не проверен")

    def test_unwritable_file_is_reported_not_swallowed(self):
        """Починка, которая не смогла записать, ОБЯЗАНА быть провалом.

        Молчаливое «вернул» на невосстановленном файле хуже отсутствия починки:
        человек уходит с уверенностью, что дерево чистое, и следующий красный
        прогон снова отправит его искать несуществующий дефект. Случай не
        выдуманный — файл без права записи бывает в чужом дереве постоянно.
        """
        self._write("x = BROKEN\n")
        f = self.plug / "tools" / "t.py"
        f.chmod(0o444)
        self.addCleanup(f.chmod, 0o644)
        r = gt.restore_stuck()
        self.assertEqual(r["status"], "fail", r)
        self.assertEqual([x["id"] for x in r["failed"]], ["m1"])
        self.assertEqual(f.read_text("utf-8"), "x = BROKEN\n")

    def test_missing_mutation_set_is_unknown_not_pass(self):
        """«Не нашёл» и «не смог проверить» — разные ответы. Пустой pass на
        отсутствующем наборе сказал бы, что дерево чистое, ничего не проверив."""
        r = gt.restore_stuck()
        self.assertEqual(r["status"], "unknown", r)


#: Одиночный тест для подпроцесса. Целить подпроцесс в ВЕСЬ этот файл нельзя:
#: внутри него есть тесты, запускающие pytest, — прогон породил бы прогон, и
#: первая же попытка ушла в рекурсию и висела десять минут до таймаута.
_LEAF = ("tests/test_unstick.py::TestRestorePutsTheCodeBack"
         "::test_clean_tree_changes_nothing")


class TestGuardFiresAndStaysQuiet(unittest.TestCase):
    """Сторож стоит на входе в НАБОР, а не только в планку: набор гоняют
    напрямую, и именно так восемь ложных падений и появились.

    Срабатывание проверяется вызовом функции, а не отравлением живого дерева:
    тест, вносящий поломку в рабочий код, при своём падении оставил бы её там.
    """

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "superstac" "k_conftes" "t_probe", REPO / "tests" / "conftest.py")
        self.cf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.cf)
        self._env = os.environ.get("SUPERSTACK_MUTATION_RUN")
        os.environ.pop("SUPERSTACK_MUTATION_RUN", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._env is None:
            os.environ.pop("SUPERSTACK_MUTATION_RUN", None)
        else:
            os.environ["SUPERSTACK_MUTATION_RUN"] = self._env

    def _with_stuck(self, stuck: list):
        self.cf._stuck = lambda: stuck

    def test_guard_stops_the_session_and_names_the_fix(self):
        """Одна строка с причиной вместо россыпи падений — весь смысл сторожа.
        Диагноз без команды починки заставляет искать её заново каждый раз."""
        self._with_stuck([{"id": "m1", "file": "tools/t.py", "why": "поломка"}])
        # Класс исключения ловится по имени: `pytest.exit` бросает
        # `_pytest.outcomes.Exit` — приватный путь, который уже переезжал
        # между версиями. Имя стабильнее пути.
        with self.assertRaises(BaseException) as cm:
            self.cf.pytest_sessionstart(None)
        self.assertEqual(type(cm.exception).__name__, "Exit")
        msg = str(cm.exception)
        self.assertIn("ЗАСТРЯЛА МУТАЦИЯ", msg)
        self.assertIn("m1", msg)
        self.assertIn("--unstick", msg)

    def test_guard_is_silent_under_the_mutation_flag(self):
        """Главная развилка: под флагом ворот мутаций сторож обязан молчать,
        иначе он сам станет источником ложного зелёного (см. класс ниже)."""
        self._with_stuck([{"id": "m1", "file": "tools/t.py", "why": "поломка"}])
        os.environ["SUPERSTACK_MUTATION_RUN"] = "1"
        self.cf.pytest_sessionstart(None)  # молча

    def test_clean_tree_passes_through(self):
        """Обратный контроль: сторож, кричащий на здоровое дерево, останавливал
        бы каждый прогон и был бы отключён на второй день."""
        self._with_stuck([])
        self.cf.pytest_sessionstart(None)

    def test_guard_is_actually_wired_into_a_real_run(self):
        """Проверка функции доказывает логику, но не подключение. Настоящий
        прогон на чистом дереве обязан пройти — сторож не должен ломать набор."""
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
               "SUPERSTACK_IGNORE_PAUSE": "1", "NO_COLOR": "1"}
        env.pop("SUPERSTACK_MUTATION_RUN", None)
        p = subprocess.run([sys.executable, "-m", "pytest", _LEAF, "-q"],
                           cwd=str(REPO), capture_output=True, text=True,
                           timeout=120, env=env)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-600:])
        self.assertNotIn("ЗАСТРЯЛА МУТАЦИЯ", p.stdout + p.stderr)


class TestMutationGateSilencesTheGuard(unittest.TestCase):
    """Самое дорогое место во всей починке.

    Ворота мутаций применяют поломку НАМЕРЕННО и запускают тот же набор.
    Сработай там сторож — pytest вышел бы с ненулевым кодом до единого теста,
    а ворота читают ненулевой код как «мутация поймана». Итог: все мутации
    отчитались бы пойманными, не проверив ни одной, и главный механизм системы
    против ложного зелёного сам стал бы его источником.
    """

    def test_gate_passes_the_bypass_flag_to_every_run(self):
        seen = []
        orig = gt._sh

        def fake(cmd, timeout, env=None, cwd=None):
            seen.append(env or {})
            return 1, "1 failed"

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        plug = Path(tmp.name)
        (plug / "tests").mkdir(parents=True)
        (plug / "tools").mkdir(parents=True)
        (plug / "tools" / "t.py").write_text("x = GOOD\n", encoding="utf-8")
        (plug / "tests" / "mutations.json").write_text(json.dumps(
            {"mutations": [{"id": "m1", "file": "tools/t.py", "find": "GOOD",
                            "replace": "BROKEN", "why": "поломка"}]},
            ensure_ascii=False), encoding="utf-8")

        gt._sh, gt.PLUG, keep = fake, plug, gt.PLUG
        try:
            gt.gate_mutations()
        finally:
            gt._sh, gt.PLUG = orig, keep

        self.assertTrue(seen, "ворота не запустили ни одного прогона")
        for env in seen:
            self.assertEqual(env.get("SUPERSTACK_MUTATION_RUN"), "1",
                             "прогон мутации пошёл без флага обхода — сторож "
                             "оборвал бы набор, и мутация ложно считалась бы "
                             "пойманной")


if __name__ == "__main__":
    unittest.main()
