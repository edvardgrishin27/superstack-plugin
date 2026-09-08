#!/usr/bin/env python3
"""Тесты гейта верификации.

Гейт существует ради одного: чтобы «готово» нельзя было объявить словами.
Поэтому проверяется не форма вывода, а поведение на живых проектах —
падающем, зелёном, пустом и вовсе без проверок.

Главная ловушка, которую эти тесты обязаны держать: **отсутствие тестов не
есть успех**. Если пустой проект проходит гейт, гейт мотивирует удалять тесты.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at, skill_text  # noqa: E402

ROOT = REPO
# Счётчик попыток (tools/verify.py::apply_attempt_ceiling) пишет на диск —
# без переопределения пути он писал бы в настоящий ~/.claude пользователя,
# запускающего тесты. Один общий tmp-каталог на весь модуль безопасен: ключ
# счётчика — абсолютный путь проекта, а каждый ProjectFixture.setUp даёт
# новый случайный tempdir, так что разные тесты никогда не делят один ключ.
_STATE_DIR = tempfile.mkdtemp(prefix="ss-verify-state-")
_STATE_PATH = str(Path(_STATE_DIR) / "attempts.json")
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
       "SUPERSTACK_VERIFY_STATE": _STATE_PATH}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Регистрация до исполнения обязательна: dataclass ищет свой модуль в
    # sys.modules, и без этого падает на разборе аннотаций.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vf = _load("ss_verify", at("tools", "verify.py"))


def run_gate(project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(at("tools", "verify.py")), "--json", str(project)],
        capture_output=True, text=True, timeout=300, env=ENV)


class ProjectFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel: str, text: str) -> None:
        f = self.p / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")

    def force_pytest_available(self, value: bool) -> None:
        """Установлен ли pytest — состояние МАШИНЫ, а не свойство кода.

        Обнаружение проверок обязано давать один и тот же ответ и там, где
        pytest стоит, и там, где его нет, поэтому оно подаётся параметром.
        """
        real = vf._pytest_available
        vf._pytest_available = lambda: value
        self.addCleanup(setattr, vf, "_pytest_available", real)


class TestGateActuallyFires(ProjectFixture):
    """Позитивный контроль: гейт обязан РЕАЛЬНО останавливать. Тест, который
    проверяет только «зелёное остаётся зелёным», не держит ничего."""

    def test_failing_test_blocks_the_turn(self):
        self.write("tests/test_x.py", "def test_broken():\n    assert 1 == 2\n")
        r = run_gate(self.p)
        self.assertEqual(r.returncode, 1, r.stdout)
        v = json.loads(r.stdout)
        self.assertEqual(v["status"], "fail")
        self.assertTrue(v["blockers"], "провал без единого названного блокера")

    def test_passing_test_opens_the_turn(self):
        """Обратный контроль: гейт, который не пропускает ничего, бесполезен
        так же, как гейт, который пропускает всё."""
        self.write("tests/test_x.py", "def test_ok():\n    assert 1 == 1\n")
        r = run_gate(self.p)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout)["status"], "pass")

    def test_no_tests_at_all_is_not_success(self):
        """Пустой каталог обязан дать «проверять нечем», а не «прошло».
        Иначе гейт поощряет удалять тесты, а не писать их."""
        self.write("README.md", "проект без единой проверки\n")
        r = run_gate(self.p)
        self.assertEqual(r.returncode, 2, r.stdout)
        v = json.loads(r.stdout)
        self.assertEqual(v["status"], "absent")
        self.assertNotEqual(v["status"], "pass")

    def test_test_file_without_a_single_test_is_not_success(self):
        """Самая тихая форма ложного зелёного: прогон был, тестов ноль,
        код возврата успешный. pytest отдаёт 5 — это не успех."""
        self.write("tests/test_empty.py", "# тестов тут нет\n")
        r = run_gate(self.p)
        v = json.loads(r.stdout)
        # Именно absent, а не fail: лечится это по-разному. «Красный тест» —
        # чинить код; «тестов нет» — заводить тест. Слепить их в одно значит
        # отправить человека чинить то, чего не существует.
        self.assertEqual(v["status"], "absent", v)
        self.assertEqual(r.returncode, 2, "прогон без тестов не опознан как пустой")


class TestDetection(ProjectFixture):
    """Запускается только то, что проект объявил сам."""

    def test_nothing_is_invented(self):
        self.write("package.json", json.dumps({"name": "x", "scripts": {}}))
        self.assertEqual(vf.detect_checks(self.p), [])

    def test_only_declared_scripts_are_run(self):
        self.write("package.json", json.dumps(
            {"name": "x", "scripts": {"test": "jest", "deploy": "./ship.sh"}}))
        ids = [c.id for c in vf.detect_checks(self.p)]
        self.assertIn("node:test", ids)
        self.assertNotIn("node:deploy", ids,
                         "гейт запустил деплой, который никто не объявлял проверкой")

    def test_package_manager_comes_from_the_lockfile(self):
        """Угадывание тут дорого: npm в проекте на pnpm переустановит дерево."""
        self.write("package.json", json.dumps({"scripts": {"test": "jest"}}))
        self.write("pnpm-lock.yaml", "lockfileVersion: 9\n")
        cmds = [c.cmd[0] for c in vf.detect_checks(self.p)]
        self.assertEqual(cmds, ["pnpm"] * len(cmds))

    def test_python_tests_are_found_by_layout(self):
        self.force_pytest_available(True)
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.assertIn("py:test", [c.id for c in vf.detect_checks(self.p)])

    def test_missing_binary_is_not_offered(self):
        """Проверка, которую нечем запустить, не должна попадать в план:
        иначе гейт валится по причине, к коду отношения не имеющей."""
        self.write("Cargo.toml", "[package]\nname='x'\n")
        ids = [c.id for c in vf.detect_checks(self.p)]
        import shutil
        self.assertEqual("rust:test" in ids, shutil.which("cargo") is not None)


class TestVerdict(unittest.TestCase):
    """Вердикт считается из результатов, а не из настроения."""

    def _res(self, code: int, out: str = "", empty: bool = False):
        c = vf.Check("t", "тесты", ("true",), "почему")
        return vf.Result(c, code, out, empty)

    def test_absent_when_nothing_to_run(self):
        v = vf.verdict([], Path("/x"))
        self.assertEqual(v["status"], "absent")
        self.assertEqual(vf.EXIT[v["status"]], 2)

    def test_zero_code_with_empty_run_is_not_pass(self):
        v = vf.verdict([self._res(0, "collected 0 items", empty=True)], Path("/x"))
        self.assertNotEqual(v["status"], "pass")

    def test_one_failure_blocks_even_among_greens(self):
        good = self._res(0)
        bad = vf.Result(vf.Check("l", "линт", ("true",), "w"), 1, "boom", False)
        v = vf.verdict([good, bad], Path("/x"))
        self.assertEqual(v["status"], "fail")
        self.assertEqual(len(v["blockers"]), 1)
        self.assertIn("линт", v["next"])

    def test_all_green_passes(self):
        v = vf.verdict([self._res(0), self._res(0)], Path("/x"))
        self.assertEqual(v["status"], "pass")
        self.assertEqual(v["blockers"], [])

    def test_exit_codes_are_distinct(self):
        """Ноль/один/два обязаны различаться: на них строится решение скрипта."""
        self.assertEqual(sorted(vf.EXIT.values()), [0, 1, 2])

    def test_exit_two_from_the_project_means_unmeasured_not_broken(self):
        """Код 2 — «не смог проверить» во всей системе, и того же она просит от
        проверок проекта. Пока гейт читал 2 как провал, он наказывал проект за
        то, чего сам требовал: исполнителю было велено вернуть 2 на пустом
        наборе, чтобы «пусто» не выглядело «зелено», — и гейт объявил НЕ ПРОШЛО.
        Проект, отдающий на нуле тестов ноль, проходил бы."""
        self.assertTrue(vf._nothing_was_checked(2, "тестов не найдено"))
        self.assertFalse(vf._nothing_was_checked(1, "1 failed"),
                         "код 1 с падением — провал, и он обязан им остаться")
        # Первая версия починки читала САМ код 2 как пустоту, и красный make
        # (он выходит двойкой) перестал блокировать ход. Пустота узнаётся по
        # выводу прогонщика, а не по коду: код принадлежит соглашению, которого
        # у каждого инструмента своё.
        self.assertFalse(vf._nothing_was_checked(2, "make: *** [test] Error 1"),
                         "чужая двойка принята за пустоту")

    def test_an_empty_check_among_greens_is_unmeasured_not_a_failure(self):
        """Разница не в строгости, а в диагнозе: провал означает «чини код»,
        пустота — «заведи тест». Ход при этом одинаково не закрывается: код 2
        зелёным не является."""
        v = vf.verdict([self._res(2, "no tests", empty=True), self._res(0)],
                       Path("/x"))
        self.assertEqual(v["status"], "absent", v)
        self.assertEqual(vf.EXIT[v["status"]], 2)
        self.assertNotIn("починить", v["next"])

    def test_a_real_failure_next_to_an_empty_one_is_still_a_failure(self):
        """Обратный контроль: смягчение пустоты не должно прятать красное."""
        bad = vf.Result(vf.Check("l", "линт", ("true",), "w"), 1, "boom", False)
        v = vf.verdict([self._res(2, "no tests", empty=True), bad], Path("/x"))
        self.assertEqual(v["status"], "fail", v)

    def test_empty_markers_catch_the_common_runners(self):
        for out in ("No tests found, exiting", "Tests:       0 total",
                    "collected 0 items", "?   pkg  [no test files]"):
            with self.subTest(out=out):
                self.assertTrue(any(rx.search(out) for rx in vf.EMPTY_MARKERS),
                                f"пустой прогон не опознан: {out}")


class TestFailureSignature(unittest.TestCase):
    """Признак поломки для ключа серии (apply_attempt_ceiling). Числа и
    похожее на путь обязаны вычищаться — иначе один и тот же провал с другим
    кодом возврата или другой временной директорией читался бы как новая
    поломка, и серия обнулялась бы там, где обязана продолжаться."""

    def test_different_return_codes_are_the_same_signature(self):
        a = vf._failure_signature("тесты: код возврата 1")
        b = vf._failure_signature("тесты: код возврата 2")
        self.assertEqual(a, b, "разные коды возврата дали разные подписи")

    def test_different_temp_paths_are_the_same_signature(self):
        a = vf._failure_signature("линт: не смог проверить — /tmp/run-abc123/log")
        b = vf._failure_signature("линт: не смог проверить — /tmp/run-xyz987/log")
        self.assertEqual(a, b, "разные временные пути дали разные подписи")

    def test_different_labels_are_different_signatures(self):
        """Обратный контроль: нормализация не должна ослеплять подпись
        целиком — сборка и линт обязаны остаться разными поломками."""
        a = vf._failure_signature("сборка: код возврата 1")
        b = vf._failure_signature("линт: код возврата 1")
        self.assertNotEqual(a, b, "разные поломки схлопнулись в одну подпись")


class TestEmptyIsNotConfusedWithReal(ProjectFixture):
    """Граница между «прогон был, тестов ноль» и «тесты есть» проверяется на
    выводе настоящих прогонщиков. Ошибка в любую сторону одинаково дорога:
    зелёный набор объявляется пустым, либо пустой — зелёным."""

    def _run_printing(self, text: str, code: int = 0):
        """Проверка с ОБЪЯВЛЕННОЙ командой, которая печатает заданный вывод.

        Прогонщик подставной намеренно: настоящие mocha/jest на машине могут
        отсутствовать, а тест обязан давать один и тот же ответ везде.
        """
        script = f"import sys; print({text!r}); sys.exit({code})"
        c = vf.Check("x:test", "тесты", (sys.executable, "-c", script), "почему")
        return vf._run(c, self.p)

    def test_ten_passing_is_not_an_empty_run(self):
        """«0 passing» находилось внутри «10 passing»: зелёный mocha-прогон на
        10, 20, 100 тестах объявлялся пустым, и гейт для такого проекта не
        зеленел никогда."""
        r = self._run_printing("  10 passing (12ms)")
        self.assertFalse(r.empty, "прогон с 10 прошедшими тестами объявлен пустым")
        self.assertEqual(vf.verdict([r], self.p)["status"], "pass")

    def test_zero_passing_is_still_an_empty_run(self):
        """Обратный контроль: граница не должна ослепить маркер целиком."""
        r = self._run_printing("  0 passing (2ms)")
        self.assertTrue(r.empty, "настоящий пустой прогон перестал опознаваться")
        self.assertEqual(vf.verdict([r], self.p)["status"], "absent")

    def test_npm_init_stub_is_absent_not_failure(self):
        """`npm init` кладёт "test": echo "Error: no test specified" && exit 1.
        Это «тестов нет», а не «тесты красные»: блокировать ход требованием
        починить несуществующие тесты — то самое смешение диагнозов, которое
        инструмент обязан не допускать."""
        r = self._run_printing("Error: no test specified", code=1)
        self.assertTrue(r.empty, "заглушка npm init прочитана как красные тесты")
        v = vf.verdict([r], self.p)
        self.assertEqual(v["status"], "absent", v)
        self.assertEqual(vf.EXIT[v["status"]], 2, "ход блокируется заглушкой npm init")


class TestPythonTestsAreNotInvented(ProjectFixture):
    """Правило 3 инструмента: запускается только то, что проект объявил."""

    def test_directory_named_test_without_python_declares_nothing(self):
        """Каталог test/ с золотыми файлами есть в Go- и JS-репозиториях
        сплошь и рядом. Раньше одно его имя выдумывало прогон pytest —
        на машине без pytest это блокировало каждое закрытие хода."""
        self.force_pytest_available(True)   # даже когда запускать ЕСТЬ чем
        self.write("main.go", "package main\nfunc main(){}\n")
        self.write("test/golden.txt", "золотой файл\n")
        self.assertEqual([c.id for c in vf.detect_checks(self.p)], [],
                         "выдумана проверка, которой проект не объявлял")
        r = run_gate(self.p)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(json.loads(r.stdout)["status"], "absent")

    def test_python_tests_deeper_than_one_level_are_still_found(self):
        """Обратный контроль: требование .py не должно ослепить обнаружение —
        tests/unit/test_a.py это обычная раскладка."""
        self.force_pytest_available(True)
        self.write("tests/unit/test_a.py", "def test_a():\n    pass\n")
        self.assertIn("py:test", [c.id for c in vf.detect_checks(self.p)])


class TestUnrunnableIsNamed(ProjectFixture):
    """«Не нашёл» и «не смог проверить» — разные утверждения.

    Наличие интерпретатора не означает наличия прогонщика: pytest ставится
    отдельно, и на системном python3 его обычно нет.
    """

    def setUp(self):
        super().setUp()
        self.force_pytest_available(False)

    def test_declared_but_unrunnable_check_is_not_silently_dropped(self):
        self.write("tests/test_a.py", "def test_a():\n    pass\n")
        self.assertEqual(detect := [c.id for c in vf.detect_checks(self.p)], [], detect)
        un = vf.unrunnable_checks(self.p)
        self.assertEqual([u.check.id for u in un], ["py:test"])
        v = vf.verdict([], self.p, tuple(un))
        self.assertEqual(v["status"], "absent")
        self.assertTrue(any("pytest" in b for b in v["blockers"]),
                        f"нечем запустить, а сказано только про отсутствие тестов: {v}")
        self.assertNotIn("не объявил", " ".join(v["blockers"]),
                         "«не смог проверить» подано как «проект не объявил проверок»")

    def test_green_next_to_unverified_is_not_a_pass(self):
        """Одна прошедшая проверка не закрывает ход за ту, которую никто не
        запускал: непроверенное гасит доверие, а не тонет в зелёном."""
        good = vf.Result(vf.Check("go:test", "тесты", ("go", "test"), "w"), 0, "ok", False)
        un = (vf.Unrunnable(vf.Check("py:test", "тесты", ("python3", "-m", "pytest"), "w"),
                            "нечем запустить: модуля pytest нет"),)
        v = vf.verdict([good], self.p, un)
        self.assertNotEqual(v["status"], "pass", v)
        self.assertNotEqual(vf.EXIT[v["status"]], 0)


class TestAttemptCeilingUnit(ProjectFixture):
    """Потолок попыток (apply_attempt_ceiling) — на функции напрямую, без
    сабпроцесса: быстрее и точнее ловит границу MAX_ATTEMPTS."""

    def setUp(self):
        super().setUp()
        self.state_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.state_dir.name) / "attempts.json"
        self.addCleanup(self.state_dir.cleanup)

    def _fail_verdict(self, next_text: str = "почини x") -> dict:
        return {"gate": "verify", "status": "fail", "project": str(self.p),
                "blockers": ["тесты: код возврата 1"], "evidence": [],
                "next": next_text}

    def _pass_verdict(self) -> dict:
        return {"gate": "verify", "status": "pass", "project": str(self.p),
                "blockers": [], "evidence": [], "next": "гейт пройден"}

    def _absent_verdict(self) -> dict:
        return {"gate": "verify", "status": "absent", "project": str(self.p),
                "blockers": ["проект не объявил ни одной проверки"], "evidence": [],
                "next": "завести хотя бы один тест"}

    def _fail_verdict_with(self, blockers: list, next_text: str) -> dict:
        """Провал с ЗАДАННЫМ блокером — нужен, чтобы отличить два РАЗНЫХ
        провала друг от друга (_fail_verdict всегда даёт один и тот же)."""
        return {"gate": "verify", "status": "fail", "project": str(self.p),
                "blockers": blockers, "evidence": [], "next": next_text}

    def test_below_ceiling_keeps_the_original_next(self):
        v1 = vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        self.assertEqual(v1["attempt"], 1)
        self.assertFalse(v1["gave_up"])
        self.assertEqual(v1["next"], "почини x")
        v2 = vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        self.assertEqual(v2["attempt"], 2)
        self.assertFalse(v2["gave_up"], "сдача наступила раньше потолка")

    def test_max_attempts_flips_to_surrender(self):
        for _ in range(vf.MAX_ATTEMPTS - 1):
            vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        v = vf.apply_attempt_ceiling(self._fail_verdict("почини x"), self.p, self.state_path)
        self.assertEqual(v["attempt"], vf.MAX_ATTEMPTS)
        self.assertTrue(v["gave_up"], "потолок не сработал на MAX_ATTEMPTS-м заходе")
        self.assertNotEqual(v["next"], "почини x",
                            "гейт всё ещё просит чинить тем же текстом после сдачи")
        self.assertTrue(any(w in v["next"] for w in ("подход", "уточн")),
                        f"сдача не требует сменить подход, а не повторить: {v['next']}")
        # Потолок меняет только текст next и служебные поля — контракт
        # 0/1/2 (см. test_exit_codes_are_distinct) остаётся тем же самым.
        self.assertEqual(v["status"], "fail")
        self.assertEqual(vf.EXIT[v["status"]], 1)

    def test_state_persists_across_separate_calls(self):
        """Счётчик обязан жить МЕЖДУ вызовами: гейт перезапускается заново
        (новый процесс) на каждый прогон, единственный носитель памяти —
        файл на диске, а не переменная внутри процесса.

        Запись на диске — вложенный словарь `{"attempt": N, "sig": ...}` на
        проект, не голое число: `on_disk[str(self.p)] == 1` было верно, пока
        ключ серии был только проектом. С ключом (проект, признак поломки)
        рядом со счётчиком обязан жить и признак — иначе после перезапуска
        процесса не по чем понять, та же это серия или уже другая, — то
        утверждение это и проверяло не полностью, оно проверяло только то,
        что число вообще что-то пишет на диск.
        """
        vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        on_disk = json.loads(self.state_path.read_text("utf-8"))
        self.assertEqual(on_disk[str(self.p)]["attempt"], 1,
                         "счётчик не записан на диск")
        v2 = vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        self.assertEqual(v2["attempt"], 2, "счётчик не пережил отдельный вызов")

    def test_pass_resets_the_counter(self):
        for _ in range(vf.MAX_ATTEMPTS - 1):
            vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        vf.apply_attempt_ceiling(self._pass_verdict(), self.p, self.state_path)
        v = vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        self.assertEqual(v["attempt"], 1, "зелёный прогон не сбросил серию провалов")
        self.assertFalse(v["gave_up"])

    def test_absent_does_not_move_the_counter(self):
        """«Проверять нечем» — не починка и не новый провал: серия не
        должна ни расти, ни обнуляться, иначе временно недоступный
        прогонщик (pytest пропал на минуту) прячет реальные провалы."""
        for _ in range(vf.MAX_ATTEMPTS - 1):
            vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        vf.apply_attempt_ceiling(self._absent_verdict(), self.p, self.state_path)
        v = vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        self.assertEqual(v["attempt"], vf.MAX_ATTEMPTS,
                         "«проверять нечем» между провалами сдвинуло или сбросило серию")
        self.assertTrue(v["gave_up"])

    def test_different_projects_have_independent_counters(self):
        other = self.p.parent / (self.p.name + "-other")
        vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        vf.apply_attempt_ceiling(self._fail_verdict(), self.p, self.state_path)
        v_other = vf.apply_attempt_ceiling(self._fail_verdict(), other, self.state_path)
        self.assertEqual(v_other["attempt"], 1, "счётчик утёк из чужого проекта")

    def test_failure_signature_change_starts_a_new_series(self):
        """Ключ серии — ПАРА (проект, признак поломки), а не один проект.

        Раньше счётчик был общим на ВСЕ провалы проекта: сборка падала
        дважды, потом начинал падать линт — и это читалось как третий заход
        одной и той же серии, хотя линт провалился впервые. Смена подписи
        обязана обнулить счёт, а не продолжить чужую серию.
        """
        build = self._fail_verdict_with(["сборка: код возврата 1"], "почини сборку")
        self.assertEqual(
            vf.apply_attempt_ceiling(build, self.p, self.state_path)["attempt"], 1)
        self.assertEqual(
            vf.apply_attempt_ceiling(build, self.p, self.state_path)["attempt"], 2)

        lint = self._fail_verdict_with(["линт: код возврата 1"], "почини линт")
        v = vf.apply_attempt_ceiling(lint, self.p, self.state_path)
        self.assertEqual(v["attempt"], 1,
                         "новая поломка унаследовала счёт от чужой серии")
        self.assertFalse(v["gave_up"])

    def test_surrender_names_the_failure_that_actually_repeated(self):
        """Текст сдачи обязан называть поломку, на которой серия РЕАЛЬНО
        застряла, и число заходов именно по ней — не поломку из чужой,
        уже закрытой серии, которая просто попалась первой на глаза."""
        build = self._fail_verdict_with(["сборка: код возврата 1"], "почини сборку")
        vf.apply_attempt_ceiling(build, self.p, self.state_path)      # серия сборки: 1

        lint = self._fail_verdict_with(["линт: код возврата 1"], "почини линт")
        v = None
        for _ in range(vf.MAX_ATTEMPTS):
            v = vf.apply_attempt_ceiling(lint, self.p, self.state_path)
        self.assertEqual(v["attempt"], vf.MAX_ATTEMPTS,
                         "серия линта унаследовала счёт серии сборки")
        self.assertTrue(v["gave_up"])
        self.assertIn("линт", v["next"], f"сдача назвала не ту поломку: {v['next']}")
        self.assertNotIn("сборка", v["next"],
                         "сдача унаследовала поломку из чужой, уже закрытой серии")

    def test_same_fingerprint_does_not_count_as_a_new_attempt(self):
        """Тот же провал, тот же отпечаток дерева — не новый заход починки,
        а тот же прогон, спрошенный другим вызывающим: bar.py зовёт
        verify.py тем же подпроцессом, что и Stop-хук, и фаза 5 — на одном и
        том же непочиненном дереве без дедупликации это утраивало счёт без
        единой починки (см. докстринг apply_attempt_ceiling)."""
        fail = self._fail_verdict()
        v1 = vf.apply_attempt_ceiling(fail, self.p, self.state_path, "fp-A")
        self.assertEqual(v1["attempt"], 1)
        v2 = vf.apply_attempt_ceiling(fail, self.p, self.state_path, "fp-A")
        self.assertEqual(v2["attempt"], 1,
                         "повтор без изменений в дереве засчитан новым заходом")
        v3 = vf.apply_attempt_ceiling(fail, self.p, self.state_path, "fp-B")
        self.assertEqual(v3["attempt"], 2,
                         "изменение в дереве не засчитано новым заходом")

    def test_unknown_fingerprint_falls_back_to_the_old_behaviour(self):
        """fingerprint=None ("сигнала нет", например git недоступен) обязан
        вести себя как ДО этого механизма: каждый провал — новый заход.
        Иначе неспособность снять отпечаток тихо выключила бы весь потолок
        попыток, а не только его дедуп-часть."""
        fail = self._fail_verdict()
        v1 = vf.apply_attempt_ceiling(fail, self.p, self.state_path)
        v2 = vf.apply_attempt_ceiling(fail, self.p, self.state_path)
        self.assertEqual((v1["attempt"], v2["attempt"]), (1, 2))


class TestTreeFingerprint(ProjectFixture):
    """_tree_fingerprint — сигнал для дедупликации из TestAttemptCeilingUnit,
    проверенный на настоящем git, а не на подставных данных: это ровно то же
    дерево, что видит verify.py в реальном проекте."""

    def _git(self, *args) -> None:
        # Свои -c на каждый вызов, а не глобальный git config: тест обязан
        # быть герметичным к чужому ~/.gitconfig (в т.ч. commit.gpgsign=true
        # на машине разработчика) так же, как STATE_ENV — к настоящему
        # ~/.claude пользователя.
        subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
             "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
             *args],
            cwd=str(self.p), check=True, capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        if not shutil.which("git"):
            self.skipTest("git не установлен — отпечаток снять нечем")
        self._git("init", "-q")
        self.write("a.txt", "начало\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "initial")

    def test_unchanged_tree_gives_the_same_fingerprint(self):
        fp1 = vf._tree_fingerprint(self.p)
        fp2 = vf._tree_fingerprint(self.p)
        self.assertIsNotNone(fp1, "в свежем git-репозитории отпечаток не снялся")
        self.assertEqual(fp1, fp2, "одно и то же дерево дало разные отпечатки")

    def test_editing_a_tracked_file_changes_the_fingerprint(self):
        fp1 = vf._tree_fingerprint(self.p)
        self.write("a.txt", "правка\n")
        fp2 = vf._tree_fingerprint(self.p)
        self.assertNotEqual(fp1, fp2, "правка отслеживаемого файла не замечена")

    def test_adding_an_untracked_file_changes_the_fingerprint(self):
        fp1 = vf._tree_fingerprint(self.p)
        self.write("b.txt", "новый файл\n")
        fp2 = vf._tree_fingerprint(self.p)
        self.assertNotEqual(fp1, fp2, "новый неотслеживаемый файл не замечен")

    def test_editing_an_untracked_file_changes_the_fingerprint(self):
        """Список неотслеживаемых сам по себе ловит НОВЫЙ файл, но не правку
        его содержимого — git его не видит вовсе, пока не добавлен. Отпечаток
        обязан ловить это через размер и mtime, не только через имя."""
        self.write("b.txt", "черновик\n")
        fp1 = vf._tree_fingerprint(self.p)
        self.write("b.txt", "черновик значительно длиннее прежнего\n")
        fp2 = vf._tree_fingerprint(self.p)
        self.assertNotEqual(fp1, fp2, "правка неотслеживаемого файла не замечена")

    def test_not_a_git_repo_gives_no_fingerprint(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(vf._tree_fingerprint(Path(other)),
                              "не git-репозиторий, а отпечаток снялся")


class TestAttemptCeilingIntegration(ProjectFixture):
    """Сквозной прогон через сам verify.py целиком, не только через функцию:
    подтверждает, что main() и вправду подключает потолок, а не только
    модуль, который его определяет."""

    def test_three_consecutive_fails_end_in_surrender(self):
        self.write("tests/test_x.py", "def test_broken():\n    assert 1 == 2\n")
        results = [run_gate(self.p) for _ in range(vf.MAX_ATTEMPTS - 1)]
        for r in results:
            v = json.loads(r.stdout)
            self.assertFalse(v.get("gave_up", False), v)
        # Последний заход — БЕЗ --json, чтобы заодно проверить, что сдача
        # видна и человеку в тексте на stderr, не только в служебном поле.
        last = subprocess.run(
            [sys.executable, str(at("tools", "verify.py")), str(self.p)],
            capture_output=True, text=True, timeout=300, env=ENV)
        last_json = json.loads(last.stdout)
        self.assertEqual(last_json["status"], "fail")
        self.assertEqual(last.returncode, 1,
                         "потолок попыток не должен менять код возврата гейта")
        self.assertTrue(last_json.get("gave_up"), last_json)
        self.assertEqual(last_json.get("attempt"), vf.MAX_ATTEMPTS, last_json)
        self.assertIn("СТОП", last.stderr)

    def test_a_pass_in_between_resets_the_ceiling(self):
        self.write("tests/test_x.py", "def test_broken():\n    assert 1 == 2\n")
        for _ in range(vf.MAX_ATTEMPTS - 1):
            run_gate(self.p)
        self.write("tests/test_x.py", "def test_ok():\n    assert 1 == 1\n")
        r_pass = run_gate(self.p)
        self.assertEqual(json.loads(r_pass.stdout)["status"], "pass")
        self.write("tests/test_x.py", "def test_broken():\n    assert 1 == 2\n")
        r_fail = run_gate(self.p)
        v = json.loads(r_fail.stdout)
        self.assertEqual(v.get("attempt"), 1,
                         "зелёный прогон между провалами не сбросил счётчик попыток")
        self.assertFalse(v.get("gave_up", False))

    def test_repeated_calls_with_no_tree_change_do_not_double_count(self):
        """Прямое воспроизведение дефекта №3: bar.py (builtin verify) и
        Stop-хук verify-gate.py зовут этот же verify.py подпроцессом на ОДНО
        и то же красное дерево, что и фаза 5. Без дедупликации по отпечатку
        два вызова подряд без единой починки уже дважды увеличивали
        счётчик — потолок брался раньше, чем человек успевал что-то
        изменить."""
        if not shutil.which("git"):
            self.skipTest("git не установлен — отпечаток снять нечем")
        git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
               "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]
        subprocess.run(git + ["init", "-q"], cwd=str(self.p), check=True)
        self.write("tests/test_x.py", "def test_broken():\n    assert 1 == 2\n")
        subprocess.run(git + ["add", "-A"], cwd=str(self.p), check=True)
        subprocess.run(git + ["commit", "-q", "-m", "x"], cwd=str(self.p), check=True)

        first = json.loads(run_gate(self.p).stdout)
        second = json.loads(run_gate(self.p).stdout)   # тот же провал, другой вызывающий
        self.assertEqual(first["attempt"], 1)
        self.assertEqual(second["attempt"], 1,
                         "повторный вызов на непочиненном дереве увеличил счётчик")

        # Починка — и следующий вызов ОБЯЗАН засчитаться новым заходом:
        # дедупликация не должна прятать настоящий следующий заход.
        self.write("tests/test_x.py", "def test_broken():\n    assert 1 == 3\n")
        third = json.loads(run_gate(self.p).stdout)
        self.assertEqual(third["attempt"], 2,
                         "правка в дереве не засчитана новым заходом")


class TestOutputIsMachineReadable(ProjectFixture):
    """Гейт читает скрипт, а не человек: форма ответа — часть контракта."""

    def test_json_carries_the_decision_fields(self):
        self.write("tests/test_x.py", "def test_ok():\n    assert True\n")
        v = json.loads(run_gate(self.p).stdout)
        for key in ("gate", "status", "blockers", "evidence", "next"):
            self.assertIn(key, v)

    def test_human_text_never_pollutes_the_json(self):
        self.write("tests/test_x.py", "def test_ok():\n    assert True\n")
        r = subprocess.run(
            [sys.executable, str(at("tools", "verify.py")), str(self.p)],
            capture_output=True, text=True, timeout=300, env=ENV)
        json.loads(r.stdout)          # упадёт, если человеческий текст ушёл в stdout
        self.assertIn("ПРОШЛО", r.stderr)

    def test_bad_arguments_are_named_not_crashed(self):
        r = subprocess.run(
            [sys.executable, str(at("tools", "verify.py")), "/нет/такого/пути"],
            capture_output=True, text=True, timeout=60, env=ENV)
        self.assertEqual(r.returncode, 3)
        self.assertIn("НЕ УДАЛОСЬ", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class TestSkillContract(unittest.TestCase):
    def setUp(self):
        self.text = skill_text("go")

    def test_skill_calls_the_gate(self):
        """Проверяется ВЫЗОВ, а не буквальная строка пути.

        Версия до разделения требовала `tools/verify.py` и тем закрепляла
        сломанный путь: скилл жил в пакете `superstack-build`, а verify.py — в
        `superstack-guard`, так что `$CLAUDE_PLUGIN_ROOT/tools/verify.py`
        указывал в пустоту. Тест был зелёным всё это время. Версия после
        разделения требовала вызов через резолвер. Пакет теперь один, и
        буквальная строка снова верна — но верна она не сама по себе, а
        потому, что ворота проводки сверяют её с диском.
        """
        self.assertRegex(self.text,
                         r'"\$CLAUDE_PLUGIN_ROOT/tools/verify\.py"')

    def test_skill_does_not_build_sibling_paths_from_its_own_root(self):
        """Единственная форма, которой здесь быть не должно: она выглядит
        подключённой и падает «нет такого файла» — то есть как поломка
        окружения, и человек идёт чинить установку."""
        import re
        own = {p.name for p in
               (at("skills", "go", "SKILL.md").parent.parent.parent
                / "tools").glob("*.py")}
        for m in re.finditer(r"\$\{?CLAUDE_PLUGIN_ROOT\}?/tools/([\w.]+\.py)",
                             self.text):
            with self.subTest(tool=m.group(1)):
                self.assertIn(m.group(1), own,
                              f"{m.group(1)} нет в этом пакете — звать надо "
                              "через резолвер")

    def test_skill_names_every_exit_code(self):
        for code in ("0", "1", "2"):
            self.assertRegex(self.text, rf"\|\s*{code}\s*\|")

    def test_skill_forbids_treating_absent_as_success(self):
        flat = " ".join(self.text.split())
        self.assertIn("это НЕ успех", flat)

    def test_skill_forbids_editing_the_test_to_go_green(self):
        self.assertIn("Чинится код", " ".join(self.text.split()))


class TestVerifierIdentityCoversEveryPart(unittest.TestCase):
    """Подпись проверяющего обязана покрывать КАЖДЫЙ его файл.

    Гейт перестал быть одним файлом: разбор отчёта прогонщика и потолок
    попыток живут рядом. Подпись, снятая с одного файла, после такого
    разрезания молча перестаёт замечать правку в двух третях кода, которым
    считается вердикт, — и прошлое зелёное переживает смену проверяющего.
    Механизм при этом остаётся на месте и выглядит работающим: ровно тот
    отказ, который здесь ловится, и ровно та цена, ради которой ловится.
    """

    ИНСТРУМЕНТЫ = at("tools", "verify.py").parent

    def свои_соседи(self) -> set:
        """Модули ПАКЕТА, которые гейт импортирует у себя же в каталоге."""
        import re
        текст = at("tools", "verify.py").read_text("utf-8")
        имена = set(re.findall(r"(?m)^(?:import|from)\s+(\w+)", текст))
        return {и for и in имена if (self.ИНСТРУМЕНТЫ / f"{и}.py").is_file()}

    def test_every_part_is_named_in_the_signature(self):
        self.assertEqual({ч.__name__ for ч in vf._ЧАСТИ}, self.свои_соседи(),
                         "часть проверяющего не входит в его подпись: правка "
                         "в ней не обесценит прошлое зелёное")

    def test_a_named_part_really_enters_the_signature(self):
        """Список без чтения — украшение: проверяется, что байты частей
        действительно попадают в подпись, а не только перечислены рядом."""
        целиком = vf._verifier_identity()
        self.assertIsNotNone(целиком, "подпись не снялась вовсе")
        прежние = vf._ЧАСТИ
        vf._ЧАСТИ = ()
        try:
            только_гейт = vf._verifier_identity()
        finally:
            vf._ЧАСТИ = прежние
        self.assertNotEqual(целиком, только_гейт,
                            "части проверяющего в подпись не входят")


if __name__ == "__main__":
    unittest.main()
