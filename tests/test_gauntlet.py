#!/usr/bin/env python3
"""Тесты ПЛАНКИ. Контролёр контролёров: кто проверяет того, кто считает останов.

Зачем этот файл существует.

Планка сама была ничем не заперта: `tools/gauntlet.py` не упоминался ни в одном
тесте и ни в одной мутации, а его улика в плане — подстрока «def gate_hermetic».
Значит правка вида «ворота мутаций возвращают pass, ничего не запуская»
сохраняла подстроку, набор оставался зелёным, и все шесть ворот превращались в
декорацию — незаметно ни для тестов, ни для мутаций, ни для плана. Инструмент,
который решает «планка взята», обязан быть заперт не слабее остальных.

Что здесь держится:

  · частичный прогон (--gate, --quick) НИКОГДА не даёт «планка взята»;
  · ворота, которое не удалось выполнить (unknown), не считается пройденным;
  · ворота мутаций ДЕЙСТВИТЕЛЬНО запускают набор и действительно отличают
    пойманную поломку от выжившей — проверяется на подставном плагине, где
    ответ известен заранее;
  · мутация восстанавливается байт-в-байт: пережившая прогон правка отравила бы
    все следующие ворота;
  · ворота плана читают файлы, а не докладывают по памяти.

Герметичность: настоящий ~/.claude, сеть, время и содержимое этого репозитория
здесь не читаются — ворота подставляются списком, а plugin-каталог собирается в
temp-дереве.
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
from paths import REPO, at  # noqa: E402

ROOT = REPO
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gt = _load("ss_gauntlet", at("tools", "gauntlet.py"))


def _gate(status: str, detail: str = "деталь"):
    return lambda: {"status": status, "detail": detail}


class FakeGates(unittest.TestCase):
    """Ворота подставляются: проверяется СБОРКА вердикта, а не их содержимое."""

    def setUp(self):
        self.real = gt.GATES
        gt.GATES = [("первые", _gate("pass")), ("вторые", _gate("pass")),
                    ("третьи", _gate("pass"))]

    def tearDown(self):
        gt.GATES = self.real


class TestPartialRunIsNeverTheWholeBar(FakeGates):
    def test_single_gate_does_not_take_the_bar(self):
        """`--gate <самые дешёвые ворота>` возвращал done:true и код 0 за
        секунду: в стенограмме это неотличимо от полного прогона, то есть
        планка проходилась словами."""
        v = gt.run(only="первые")
        self.assertFalse(v["done"], v)
        self.assertEqual(len(v["gates"]), 3, "незапущенные ворота исчезли из отчёта")
        self.assertEqual([g["status"] for g in v["gates"]],
                         ["pass", "skipped", "skipped"])
        self.assertNotEqual(v["next"], "планка взята")

    def test_quick_run_does_not_take_the_bar(self):
        gt.GATES = [("набор", _gate("pass")), ("мутации", _gate("pass"))]
        v = gt.run(quick=True)
        self.assertFalse(v["done"], v)

    def test_full_green_run_does_take_the_bar(self):
        """Обратный контроль: планка, которую нельзя взять, бесполезна так же,
        как планка, которая берётся словами."""
        v = gt.run()
        self.assertTrue(v["done"], v)
        self.assertEqual(v["next"], "планка взята")

    def test_unknown_gate_is_not_a_pass(self):
        gt.GATES = [("первые", _gate("pass")), ("вторые", _gate("unknown", "нечем"))]
        v = gt.run()
        self.assertFalse(v["done"], v)
        self.assertIn("не проверено", v["next"])

    def test_red_gate_names_the_next_step(self):
        gt.GATES = [("первые", _gate("fail", "упало 3 из 9"))]
        v = gt.run()
        self.assertFalse(v["done"])
        self.assertIn("починить", v["next"])


class TestExitCodeOfPartialRun(unittest.TestCase):
    """Код возврата — единственное, что читает вызывающий скрипт."""

    def test_single_gate_never_exits_zero(self):
        r = subprocess.run([sys.executable, str(at("tools", "gauntlet.py")),
                            "--json", "--gate", "план"],
                           capture_output=True, text=True, timeout=300, env=ENV)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        v = json.loads(r.stdout)
        self.assertFalse(v["done"], v)
        self.assertEqual(len(v["gates"]), len(gt.GATES),
                         "часть ворот не названа в отчёте одиночного прогона")


class FakePlugin(unittest.TestCase):
    """Подставной плагин: маленькое дерево, где ответ известен заранее.

    Гонять настоящие ворота по настоящему репозиторию нельзя — тест стал бы
    зависеть от того, что в нём сейчас лежит, и от прогона всего набора внутри
    прогона набора.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plug = Path(self.tmp.name)
        self.real_plug = gt.PLUG
        gt.PLUG = self.plug

    def tearDown(self):
        gt.PLUG = self.real_plug
        self.tmp.cleanup()

    def write(self, rel: str, text: str) -> Path:
        f = self.plug / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        return f


class TestMutationGateReallyRunsTheSuite(FakePlugin):
    """Единственные ворота, доказывающие, что тесты вообще что-то держат.

    Если они начнут отвечать «pass», не запуская набор, все остальные ворота
    теряют смысл, а поймать это можно только так: подсунуть заведомо выживающую
    мутацию и потребовать, чтобы её назвали.
    """

    def _build(self, mutations: list) -> None:
        self.write("src.py", "МЕХАНИЗМ = 'живой'  # опорный комментарий\n")
        self.write("tests/test_src.py",
                   "from pathlib import Path\n\n\n"
                   "def test_mechanism_is_alive():\n"
                   "    text = (Path(__file__).resolve().parent.parent / 'src.py').read_text()\n"
                   "    assert \"МЕХАНИЗМ = 'живой'\" in text\n")
        self.write("tests/mutations.json",
                   json.dumps({"mutations": mutations}, ensure_ascii=False))

    def test_survivor_is_named_and_the_gate_goes_red(self):
        self._build([{"id": "decoy.comment", "file": "src.py",
                      "find": "# опорный комментарий",
                      "replace": "# другой комментарий",
                      "why": "правка, которую не держит ни один тест"}])
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "fail", r)
        self.assertEqual([s["id"] for s in r["survived"]], ["decoy.comment"])

    def test_caught_mutation_passes_the_gate(self):
        self._build([{"id": "real.mechanism", "file": "src.py",
                      "find": "МЕХАНИЗМ = 'живой'",
                      "replace": "МЕХАНИЗМ = 'мёртвый'",
                      "why": "удалён механизм, который держит тест"}])
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "pass", r)

    def test_mutation_is_restored_byte_for_byte(self):
        """Мутация, пережившая прогон в файле, отравляет все следующие ворота
        и весь дальнейший рабочий день: планка начинает мерить сломанный код."""
        self._build([{"id": "real.mechanism", "file": "src.py",
                      "find": "МЕХАНИЗМ = 'живой'",
                      "replace": "МЕХАНИЗМ = 'мёртвый'",
                      "why": "удалён механизм"}])
        before = (self.plug / "src.py").read_bytes()
        gt.gate_mutations()
        self.assertEqual((self.plug / "src.py").read_bytes(), before,
                         "файл не восстановлен после мутации")

    def test_unapplied_mutation_is_unknown_not_pass(self):
        """Якорь, которого нет в файле, означает «не смог проверить».
        Засчитать это за «все мутации пойманы» — выдать непроверенное за
        результат."""
        self._build([{"id": "stale.anchor", "file": "src.py",
                      "find": "СТРОКИ ТАКОЙ НЕТ", "replace": "x",
                      "why": "якорь устарел"}])
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "unknown", r)

    def test_missing_registry_is_unknown_not_pass(self):
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "unknown", r)


class TestNamedMutationsAreASubsetNotTheBar(FakePlugin):
    """Проверка поимённо (`--mutation`).

    Заведена после того, как проверка трёх свежих поломок обошлась в прогон
    всех 241: незнакомый флаг молча игнорировался. Но выборка опаснее длинного
    прогона — три пойманные мутации выглядят точно как взятая планка, и разница
    видна только если отчёт назовёт её сам.
    """

    def _build(self) -> None:
        self.write("src.py", "A = 1\nB = 2\n")
        self.write("tests/test_src.py",
                   "from pathlib import Path\n\n\n"
                   "def test_a():\n"
                   "    assert 'A = 1' in (Path(__file__).resolve().parent.parent"
                   " / 'src.py').read_text()\n\n\n"
                   "def test_b():\n"
                   "    assert 'B = 2' in (Path(__file__).resolve().parent.parent"
                   " / 'src.py').read_text()\n")
        self.write("tests/mutations.json", json.dumps({"mutations": [
            {"id": "m.a", "file": "src.py", "find": "A = 1", "replace": "A = 9",
             "why": "убран механизм A"},
            {"id": "m.b", "file": "src.py", "find": "B = 2", "replace": "B = 9",
             "why": "убран механизм B"}]}, ensure_ascii=False))

    def tearDown(self):
        gt.ONLY_MUTATIONS = set()
        super().tearDown()

    def test_only_the_named_mutation_runs(self):
        self._build()
        gt.ONLY_MUTATIONS = {"m.a"}
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "pass", r)
        self.assertIn("1 из 2", r["detail"])

    def test_a_subset_says_so_in_the_report(self):
        """Без пометки «проверено 3 из 241» читается как «проверено всё» —
        ровно то умолчание, против которого написана вся планка."""
        self._build()
        gt.ONLY_MUTATIONS = {"m.a"}
        r = gt.gate_mutations()
        self.assertTrue(r.get("subset"), r)
        self.assertIn("остальные не проверялись", r["detail"])

    def test_the_whole_set_is_not_marked_a_subset(self):
        self._build()
        r = gt.gate_mutations()
        self.assertFalse(r.get("subset"), r)
        self.assertNotIn("ВЫБОРКА", r["detail"])

    def test_an_unknown_id_is_refused_not_silently_empty(self):
        """Опечатка в имени иначе даёт «все пойманы», не проверив ничего —
        самый убедительный из возможных зелёных отчётов."""
        self._build()
        gt.ONLY_MUTATIONS = {"m.опечатка"}
        r = gt.gate_mutations()
        self.assertEqual(r["status"], "unknown", r)
        self.assertIn("m.опечатка", r["detail"])


class TestUnknownFlagsAreRefused(unittest.TestCase):
    """Незнакомый флаг молча игнорировался: `--only x` не сузил ничего, и
    прогон трёх мутаций превратился в часовой прогон всех, отчитавшись так же."""

    def test_a_stray_flag_returns_three_not_a_full_run(self):
        p = subprocess.run(
            [sys.executable, str(at("tools", "gauntlet.py")), "--выдуманный"],
            capture_output=True, text=True, timeout=120,
            env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
        self.assertEqual(p.returncode, 3, p.stderr[-300:])
        self.assertIn("неизвестный флаг", p.stderr)


class TestPlanGateReadsFiles(FakePlugin):
    def _coverage(self, contains: str) -> None:
        self.write("data/plan-coverage.json", json.dumps({"mechanisms": [
            {"id": "X.mech", "layer": "L0", "mechanism": "механизм",
             "evidence": {"file": "tools/thing.py", "contains": contains}},
        ]}, ensure_ascii=False))

    def test_absent_evidence_is_named_missing(self):
        self._coverage("def механизм")
        self.write("tools/thing.py", "# файл есть, механизма в нём нет\n")
        r = gt.gate_plan()
        self.assertEqual(r["status"], "fail", r)
        self.assertEqual([m["id"] for m in r["missing"]], ["X.mech"])

    def test_present_evidence_passes(self):
        self._coverage("def механизм")
        self.write("tools/thing.py", "def механизм():\n    pass\n")
        self.assertEqual(gt.gate_plan()["status"], "pass")

    def test_missing_file_is_not_a_pass(self):
        self._coverage("def механизм")
        r = gt.gate_plan()
        self.assertEqual(r["status"], "fail", r)


if __name__ == "__main__":
    unittest.main()


class TestStuckMutationPoisonsEverything(unittest.TestCase):
    """Прогон мутаций восстанавливает файл в finally — и этого хватает, пока
    процесс умирает по-человечески. Убитый по SIGKILL прогон оставляет поломку
    в коде навсегда, и следующий замер объявляет провалом ЧУЖУЮ мутацию.

    Так и случилось: четыре падения из пяти были не кодом, а тремя застрявшими
    поломками, и нашлись случайно. Ниже — сторож, чтобы больше не случайно.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plug = Path(self.tmp.name)
        (self.plug / "tests").mkdir(parents=True)
        (self.plug / "tools").mkdir(parents=True)
        self._orig = gt.PLUG
        gt.PLUG = self.plug
        self.addCleanup(setattr, gt, "PLUG", self._orig)
        self.addCleanup(self.tmp.cleanup)

    def _write(self, body: str, find: str = "GOOD", replace: str = "BROKEN") -> None:
        (self.plug / "tools" / "t.py").write_text(body, encoding="utf-8")
        (self.plug / "tests" / "mutations.json").write_text(json.dumps(
            {"mutations": [{"id": "m1", "file": "tools/t.py", "find": find,
                            "replace": replace, "why": "поломка"}]},
            ensure_ascii=False), encoding="utf-8")

    def test_applied_mutation_is_detected(self):
        self._write("x = BROKEN\n")
        self.assertEqual([s["id"] for s in gt.stuck_mutations()], ["m1"])

    def test_clean_tree_is_silent(self):
        """Обратный контроль: сторож, кричащий на здоровое дерево, останавливал
        бы каждый прогон и был бы отключён на второй день."""
        self._write("x = GOOD\n")
        self.assertEqual(gt.stuck_mutations(), [])

    def test_both_present_is_not_stuck(self):
        """Оригинал И замена рядом — это обычный код (например, замена
        встречается в комментарии). Мутацией это не является."""
        self._write("x = GOOD\n# упоминание BROKEN в комментарии\n")
        self.assertEqual(gt.stuck_mutations(), [])

    def test_bar_refuses_to_measure_a_poisoned_tree(self):
        """Главное: планка обязана НЕ мерить, а не мерить и соврать."""
        self._write("x = BROKEN\n")
        v = gt.run()
        self.assertFalse(v["done"])
        self.assertEqual([g["gate"] for g in v["gates"]], ["дерево"])
        self.assertIn("m1", v["next"])

    def test_refusal_names_the_file_to_restore(self):
        self._write("x = BROKEN\n")
        self.assertIn("tools/t.py", gt.human(gt.run()))


class TestBytecodeCacheCannotFakeAMeasurement(unittest.TestCase):
    """Python признаёт кэш устаревшим по паре (mtime, размер). Мутация и её
    восстановление часто СОВПАДАЮТ в размере («GATE» -> «AUTO», `_key=_key` ->
    `_key=None`) и укладываются в одну секунду — тогда пара совпадает, и прогон
    идёт по старому .pyc. Ворота честно докладывают исход чужого кода.
    """

    def test_same_size_edit_within_a_second_is_invisible_to_the_cache(self):
        """Сначала докажем, что опасность настоящая, а не теоретическая."""
        # Запись байткода включается ЯВНО на время теста. Планка гоняет набор
        # с PYTHONDONTWRITEBYTECODE=1 — то есть в её окружении кэша нет вовсе
        # и опасность не воспроизводится. Тест, наследующий условие, которое
        # сам же и проверяет, доказывает не свойство кода, а настройку среды:
        # прямой прогон давал зелёное, прогон из планки — красное.
        was_off = sys.dont_write_bytecode
        sys.dont_write_bytecode = False
        self.addCleanup(setattr, sys, "dont_write_bytecode", was_off)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.py"
            f.write_text('VALUE = "GATE"\n', encoding="utf-8")

            def value():
                spec = importlib.util.spec_from_file_location("pyc_probe", f)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["pyc_probe"] = mod
                spec.loader.exec_module(mod)
                return mod.VALUE

            self.assertEqual(value(), "GATE")
            was = f.stat().st_mtime
            f.write_text('VALUE = "AUTO"\n', encoding="utf-8")   # тот же размер
            # Время выставляется ЯВНО, а не выпрашивается у планировщика.
            # Прежняя версия надеялась, что обе записи попадут в одну секунду:
            # попадали не всегда, и тест падал три прогона подряд, а на четвёртый
            # проходил. Тест, зависящий от того, успел ли часовой тик, —
            # ровно то, что этот проект запрещает.
            os.utime(f, (was, was))
            stale = value() == "GATE"
            self.assertTrue(stale, "кэш вдруг перестал врать — тест утратил смысл")

    def test_purge_removes_the_caches(self):
        with tempfile.TemporaryDirectory() as d:
            plug = Path(d)
            (plug / "tools" / "__pycache__").mkdir(parents=True)
            (plug / "tools" / "__pycache__" / "x.pyc").write_bytes(b"stale")
            orig, gt.PLUG = gt.PLUG, plug
            try:
                killed = gt.purge_bytecode()
            finally:
                gt.PLUG = orig
            self.assertEqual(killed, 1)
            self.assertFalse((plug / "tools" / "__pycache__").exists())

    def test_mutation_runs_never_write_bytecode(self):
        """Того, чего не записали, не существует и устареть не может."""
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw.get("env") or {})
            class R:
                returncode, stdout, stderr = 0, "", ""
            return R()

        real = gt.subprocess.run
        gt.subprocess.run = fake_run
        try:
            gt._sh(["true"], 5)
        finally:
            gt.subprocess.run = real
        self.assertEqual(seen.get("PYTHONDONTWRITEBYTECODE"), "1")


class TestMutationGatePurgesBeforeEachRun(unittest.TestCase):
    """Чистка кэша обязана происходить ВНУТРИ цикла мутаций, а не один раз.

    Без этого теста вызов purge_bytecode() удаляется бесследно: набор остаётся
    зелёным, а ворота начинают измерять прогоны по старому байткоду — то есть
    отчитываться об исходе кода, который в этот момент не исполнялся.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plug = Path(self.tmp.name)
        (self.plug / "tests").mkdir(parents=True)
        (self.plug / "tools").mkdir(parents=True)
        (self.plug / "tools" / "t.py").write_text("V = 'GATE'\n", encoding="utf-8")
        (self.plug / "tests" / "mutations.json").write_text(json.dumps(
            {"mutations": [
                {"id": "a", "file": "tools/t.py", "find": "GATE",
                 "replace": "AUTO", "why": "первая"},
                {"id": "b", "file": "tools/t.py", "find": "V =",
                 "replace": "W =", "why": "вторая"},
            ]}, ensure_ascii=False), encoding="utf-8")
        self._orig = gt.PLUG
        gt.PLUG = self.plug
        self.addCleanup(setattr, gt, "PLUG", self._orig)

    def test_purge_happens_once_per_mutation(self):
        order = []
        real_purge, real_sh = gt.purge_bytecode, gt._sh
        gt.purge_bytecode = lambda: order.append("purge") or 0
        gt._sh = lambda *a, **k: (order.append("run"), (1, ""))[1]
        try:
            gt.gate_mutations()
        finally:
            gt.purge_bytecode, gt._sh = real_purge, real_sh
        self.assertEqual(order, ["purge", "run", "purge", "run"],
                         f"чистка не идёт перед каждым прогоном: {order}")


class TestEvidenceMustLiveInCode(unittest.TestCase):
    """Ворота «план» искали улику простым вхождением подстроки в ФАЙЛ.

    Внешняя проверка выпотрошила четыре механизма, оставив их названия в
    докстрингах, — и ворота отчитались «на месте» по всем четырём. Улика,
    живущая в прозе, доказывает, что кто-то написал ПРО механизм, а не что
    механизм есть.
    """

    def _text(self, body: str, suffix: str = ".py") -> str:
        f = Path(self.d.name) / f"m{suffix}"
        f.write_text(body, encoding="utf-8")
        return gt.executable_text(f)

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def test_anchor_only_in_a_docstring_does_not_count(self):
        self.assertNotIn("EMPTY_MARKERS",
                         self._text('"""про EMPTY_MARKERS написано."""\nX = 1\n'))

    def test_anchor_only_in_a_comment_does_not_count(self):
        self.assertNotIn("MAX_ATTEMPTS", self._text("# MAX_ATTEMPTS упомянут\nX = 1\n"))

    def test_anchor_in_live_code_counts(self):
        """Обратный контроль: вырезав лишнее, ворота объявили бы отсутствующим
        всё подряд и заставили переписать карту вместо кода."""
        self.assertIn("EMPTY_MARKERS", self._text("EMPTY_MARKERS = (1,)\n"))

    def test_ordinary_string_literal_still_counts(self):
        """`"proven-local"` — настоящий код и законная улика; вырезаются только
        тройные кавычки."""
        self.assertIn("proven-local", self._text('STATE = "proven-local"\n'))

    def test_adjacency_survives_blanking(self):
        """Проза затирается ПРОБЕЛАМИ, а не выбрасывается: перестроение файла
        из токенов рвало `def probe_runtime` на `def` и `probe_runtime`, и
        улика переставала находиться в коде, который на месте."""
        self.assertIn("def probe_runtime",
                      self._text('# коммент\ndef probe_runtime():\n    pass\n'))

    def test_shell_comments_are_stripped_too(self):
        self.assertNotIn("SUPERSTACK_DISABLE",
                         self._text("# SUPERSTACK_DISABLE упомянут\nX=1\n", ".sh"))

    def test_markdown_keeps_its_prose(self):
        """В .md текст И ЕСТЬ артефакт: вырезать там нечего, и ограничение
        названо, а не замаскировано."""
        self.assertIn("ГЕЙТ", self._text("# Заголовок\n\nГЕЙТ описан тут.\n", ".md"))

    def test_unparsable_python_still_loses_its_prose(self):
        """Молчаливый возврат сырого текста вернул бы ровно ту дыру, что чиним."""
        broken = '"""про EMPTY_MARKERS."""\ndef (((\n'
        self.assertNotIn("EMPTY_MARKERS", self._text(broken))


class TestStuckDetectionCoversAllThreeShapes(unittest.TestCase):
    """Наивное «замена есть, оригинала нет» ложно молчит, когда искомое —
    ПОДСТРОКА заменяющего.

    Живой случай, стоивший прогона: `tools: Read, Grep, Glob` против
    `tools: Read, Grep, Glob, Task`. В мутированном файле оригинал формально
    присутствует внутри замены, сторож рапортовал «застрявших 0», и `Task`
    пережил убитый прогон. Красным это стало не у сторожа, а у набора тестов —
    то есть поломку нашли на круг позже, чем должны были.
    """

    ADD = ("tools: Read, Grep, Glob", "tools: Read, Grep, Glob, Task")
    SWAP = ("if index.size > limit_bytes:", "if False:")
    DROP = ("    purge_bytecode()\n", "    pass\n")

    def test_addition_shape(self):
        find, repl = self.ADD
        self.assertFalse(gt._looks_applied(f"a\n{find}\nb", find, repl))
        self.assertTrue(gt._looks_applied(f"a\n{repl}\nb", find, repl))

    def test_replacement_shape(self):
        find, repl = self.SWAP
        self.assertFalse(gt._looks_applied(find, find, repl))
        self.assertTrue(gt._looks_applied(repl, find, repl))

    def test_deletion_shape(self):
        find, repl = self.DROP
        self.assertFalse(gt._looks_applied(find, find, repl))
        self.assertTrue(gt._looks_applied(repl, find, repl))

    def test_every_registered_mutation_is_distinguishable(self):
        """Инвариант на весь набор: применив мутацию к живому файлу, сторож
        ОБЯЗАН её увидеть, а на нетронутом — промолчать. Проверяется на всех
        зарегистрированных парах, а не на трёх выбранных: следующая пара
        неудобной формы иначе снова пройдёт незамеченной.
        """
        muts = json.loads((Path(gt.PLUG) / "tests" / "mutations.json")
                          .read_text("utf-8"))["mutations"]
        self.assertGreater(len(muts), 50, "набор подозрительно мал")
        blind = []
        for m in muts:
            f = Path(gt.PLUG) / m["file"]
            if not f.is_file():
                continue
            clean = f.read_text("utf-8", errors="replace")
            self.assertIn(m["find"], clean,
                          f"якорь мутации {m['id']} протух — проверять нечего")
            dirty = clean.replace(m["find"], m["replace"], 1)
            if gt._looks_applied(clean, m["find"], m["replace"]):
                blind.append((m["id"], "молчит на чистом? нет — кричит"))
            if not gt._looks_applied(dirty, m["find"], m["replace"]):
                blind.append((m["id"], "не видит применённую"))
        self.assertEqual(blind, [], f"сторож слеп к парам: {blind}")


class TestEveryMutationAnchorResolves(unittest.TestCase):
    """Якорь мутации обязан находиться в своём файле РОВНО ОДИН раз.

    Рефакторинг переписывает строку — якорь протухает, ворота мутаций говорят
    «не применились», и вся проверка тестов на осмысленность превращается в
    «не проверено». Узнать об этом можно было только через сорок минут полного
    прогона; здесь это стоит доли секунды.

    Отдельно: тест «сторож видит каждую мутацию» пропускал такие записи молча
    (`if find not in clean: continue`) — то есть терпел ровно ту поломку,
    которую обязан ловить. Пропуск и проверка — разные вещи.
    """

    def setUp(self):
        self.muts = json.loads((Path(gt.PLUG) / "tests" / "mutations.json")
                               .read_text("utf-8"))["mutations"]

    def test_each_anchor_appears_exactly_once(self):
        broken = []
        for m in self.muts:
            f = Path(gt.PLUG) / m["file"]
            if not f.is_file():
                broken.append((m["id"], f"нет файла {m['file']}"))
                continue
            n = f.read_text("utf-8", errors="replace").count(m["find"])
            if n != 1:
                broken.append((m["id"], f"вхождений якоря: {n}"))
        self.assertEqual(broken, [], f"протухшие якоря: {broken}")

    def test_ids_are_unique(self):
        ids = [m["id"] for m in self.muts]
        dupes = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(dupes, set(), f"дублирующиеся id мутаций: {dupes}")

    def test_replacement_actually_changes_something(self):
        """Мутация, ничего не меняющая по смыслу, засоряет планку: она всегда
        «выживает» и держит ворота красными по ложной причине."""
        idle = [m["id"] for m in self.muts if m["find"] == m["replace"]]
        self.assertEqual(idle, [], f"мутации без изменения: {idle}")

    def test_every_mutation_states_the_failure_it_restores(self):
        """«Почему» — не украшение: по нему читают, какой именно отказ вернётся,
        когда мутация выживет. Пустое поле превращает отчёт в список идентификаторов."""
        mute = [m["id"] for m in self.muts if len(m.get("why", "")) < 20]
        self.assertEqual(mute, [], f"мутации без объяснения: {mute}")
