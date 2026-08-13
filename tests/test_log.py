#!/usr/bin/env python3
"""Тесты журнала (tools/log.py).

Что эти тесты обязаны держать — и почему именно это.

До этого файла в системе не было НИ ОДНОГО инструмента с журналом: отказ
записи ронял бы вызвавший инструмент так же, как любая другая необработанная
ошибка, а секрет, случайно попавший в поле события, лёг бы на диск открытым
текстом и остался там навсегда (журнал не ротируется на прочих основаниях,
кроме размера). Оба свойства — не мелочи, поэтому оба здесь проверяются
поведением, а не чтением кода:

  1. ПОЗИТИВНЫЙ КОНТРОЛЬ: событие реально попадает в файл. Без этого теста
     остальные проверки «секрет не утёк» проходили бы и на инструменте,
     который вообще ничего не пишет.
  2. СЕКРЕТ НЕ ПОПАДАЕТ В ФАЙЛ КАК ЗНАЧЕНИЕ — ни по форме, ни по имени поля.
     Проверяется байтовым поиском по содержимому файла, а не вызовом redact()
     напрямую: сверка «функция что-то возвращает» ничего не говорит о том,
     что именно попало на диск.
  3. РОТАЦИЯ РЕАЛЬНО СРЕЗАЕТ. Файл, растущий без предела, — отказ (забьёт
     диск), а не мелочь; порог задаётся переменной окружения, чтобы не писать
     мегабайты ради одной проверки.
  4. ОТКАЗ ЗАПИСИ НЕ РОНЯЕТ ВЫЗВАВШИЙ ИНСТРУМЕНТ. Каталог журнала намеренно
     делается недоступным для записи; event() обязан вернуть False, а не
     бросить исключение, и предупредить в stderr ровно один раз за процесс.
  5. ВЫКЛЮЧАТЕЛИ гасят запись: SUPERSTACK_DISABLE=1 и флаг паузы — так же, как
     гасят всё остальное в системе.
  6. ПУТЬ ЖУРНАЛА БЕРЁТСЯ ИЗ ПЕРЕМЕННОЙ ОКРУЖЕНИЯ, а не жёстко из HOME —
     иначе этот же набор тестов писал бы в настоящий ~/.claude машины, на
     которой запущен.

Герметичность: HOME и SUPERSTACK_LOG_DIR — временные каталоги на каждый тест,
настоящий ~/.claude не читается и не пишется. Каждый тест грузит СВОЙ
экземпляр модуля (уникальное имя в sys.modules): у log.py есть модульный флаг
«предупреждение уже показано», и без изоляции тест из середины набора получал
бы состояние, оставленное соседним тестом, — набор становился бы зависим от
порядка запуска.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at, plug  # noqa: E402

ROOT = REPO
TOOL = at("tools", "log.py")


def _load_fresh():
    """Свежий экземпляр модуля с уникальным именем — изолирует модульные
    глобали (_warned) между тестами, чтобы порядок запуска не влиял на исход."""
    name = f"ss_log_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class LogFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(parents=True)
        self.logdir = Path(self.tmp.name) / "logs"

        # Снимок окружения, а не мутация os.environ — тест не должен утекать
        # в соседние тесты через процесс-глобальное состояние.
        self._old_env = dict(os.environ)
        for k in ("SUPERSTACK_DISABLE", "SUPERSTACK_IGNORE_PAUSE",
                  "SUPERSTA" "CK_LOG_M" "AX_BYTES"):
            os.environ.pop(k, None)
        os.environ["HOME"] = str(self.home)
        os.environ["SUPERSTACK_LOG_DIR"] = str(self.logdir)

        self.log = _load_fresh()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        self.tmp.cleanup()

    @property
    def logfile(self) -> Path:
        return self.logdir / "events.jsonl"

    def read_lines(self) -> list:
        if not self.logfile.exists():
            return []
        return [json.loads(l) for l in
                self.logfile.read_text("utf-8").splitlines() if l.strip()]


class TestPositiveControl(LogFixture):
    """Без этого теста весь остальной файл ничего не значит: остальные
    проверки «секрет не утёк» прошли бы и на инструменте, который молчит."""

    def test_event_actually_lands_on_disk(self):
        ok = self.log.event("gauntlet", "запуск", "успех",
                            duration_ms=42.5, exit_code=0)
        self.assertTrue(ok, "event() сказал, что не записал")
        self.assertTrue(self.logfile.is_file(), "файла журнала нет на диске")

        rows = self.read_lines()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["tool"], "gauntlet")
        self.assertEqual(row["action"], "запуск")
        self.assertEqual(row["outcome"], "успех")
        self.assertEqual(row["duration_ms"], 42.5)
        self.assertEqual(row["exit_code"], 0)
        self.assertIn("ts", row)

    def test_directory_gets_0700(self):
        self.log.event("t", "a", "o")
        if os.geteuid() == 0:
            self.skipTest("под root биты доступа не проверяют реальный запрет")
        mode = self.logdir.stat().st_mode & 0o777
        self.assertEqual(mode, 0o700, f"каталог журнала не заперт: {oct(mode)}")

    def test_one_line_is_one_json_object(self):
        """Журнал читает не только человек — построчный JSON обязан
        разбираться без знания о соседних строках."""
        self.log.event("a", "x", "ok")
        self.log.event("b", "y", "fail", exit_code=1)
        lines = self.logfile.read_text("utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        for l in lines:
            json.loads(l)  # не бросает — валидный JSON построчно


class TestSecretsNeverHitDisk(LogFixture):
    """Проверка идёт по СОДЕРЖИМОМУ ФАЙЛА, не по возвращаемому значению
    redact(): важно, что именно легло на диск, а не что вычислила функция."""

    def test_secret_shaped_value_is_not_written_raw(self):
        # Имя поля НЕЙТРАЛЬНОЕ («note»), чтобы проверка держала именно
        # срабатывание по ФОРМЕ значения, а не подстраховывалась именем поля —
        # иначе отключение проверки формы осталось бы незамеченным этим тестом.
        secret = "ghp_" + "A1b2C3d4E5" "f6G7h8I9j0" "K1l2M3n4"
        self.log.event("probe", "скан", "ok", note=secret)
        raw = self.logfile.read_bytes()
        self.assertNotIn(secret.encode("utf-8"), raw,
                         "секрет по форме значения попал в файл открытым текстом")
        row = self.read_lines()[0]
        self.assertTrue(row["note"]["redacted"])
        self.assertEqual(row["note"]["len"], len(secret))
        self.assertNotEqual(row["note"], secret)

    def test_secret_named_field_is_not_written_raw(self):
        # Значение само по себе бесформенное — находится только по имени поля.
        value = "correct horse battery staple 42"
        self.log.event("probe", "скан", "ok", api_password=value)
        raw = self.logfile.read_bytes()
        self.assertNotIn(value.encode("utf-8"), raw,
                         "значение секретного поля попало в файл открытым текстом")
        row = self.read_lines()[0]
        self.assertTrue(row["api_password"]["redacted"])

    def test_non_secret_fields_pass_through_untouched(self):
        """redact — не цензор всего подряд: обычные поля обязаны доходить до
        файла как есть, иначе журнал станет бесполезен для разбора инцидентов."""
        self.log.event("verify", "прогон", "ok", project="demo", files=3)
        row = self.read_lines()[0]
        self.assertEqual(row["project"], "demo")
        self.assertEqual(row["files"], 3)

    def test_same_secret_gives_same_fingerprint(self):
        """Отпечаток обязан узнавать один и тот же секрет в двух событиях —
        иначе по журналу нельзя понять, что утечка одна, а не две разных."""
        secret = "ghp_" + "Z9y8X7w6V5" "u4T3s2R1q0" "P9o8N7m6"
        self.log.event("a", "x", "ok", token=secret)
        self.log.event("b", "y", "ok", token=secret)
        rows = self.read_lines()
        self.assertEqual(rows[0]["token"]["fingerprint"],
                         rows[1]["token"]["fingerprint"])


class TestRedactIsPure(LogFixture):
    """redact() — новый объект, исходный не трогается: правило проекта об
    иммутабельности распространяется и на журнал."""

    def test_input_dict_is_not_mutated(self):
        original = {"token": "sk-abcdefghijklmnopqrst"}
        snapshot = dict(original)
        self.log.redact(original)
        self.assertEqual(original, snapshot)


class TestRotation(LogFixture):
    """Ротация — реальный отказ по размеру, а не по документации: набор
    записывает события мимо порога и проверяет, что старое содержимое
    переехало в бэкап, а не легло третьей копией рядом."""

    def test_rotation_actually_caps_the_file(self):
        os.environ["SUPERSTA" "CK_LOG_M" "AX_BYTES"] = "200"
        log = _load_fresh()  # модуль читает порог при обращении — но грузим
                              # заново, чтобы точно не унаследовать кеш прошлого теста

        for i in range(40):
            log.event("t", "событие", "ok", n=i, filler="x" * 20)

        self.assertTrue(self.logfile.exists())
        backup = self.logdir / "events.jsonl.1"
        self.assertTrue(backup.is_file(),
                        "бэкап не создан — ротация не сработала вовсе")
        # Основной файл не растёт неограниченно: он не может быть больше
        # нескольких новых строк поверх порога.
        self.assertLess(self.logfile.stat().st_size, 2000,
                        "основной файл журнала не был срезан ротацией")

    def test_rotation_keeps_exactly_one_backup(self):
        """Бэкап — один и затирается, а не копится: иначе «ротация» на деле
        просто переносит проблему на второй файл вместо первого."""
        os.environ["SUPERSTA" "CK_LOG_M" "AX_BYTES"] = "150"
        log = _load_fresh()
        for i in range(80):
            log.event("t", "событие", "ok", n=i, filler="y" * 15)
        entries = sorted(p.name for p in self.logdir.iterdir())
        self.assertEqual(entries, ["events.jsonl", "events.jsonl.1"],
                         f"на диске накопилось лишнее: {entries}")


class TestWriteFailureNeverCrashesCaller(LogFixture):
    def test_unwritable_directory_returns_false_and_warns_once(self):
        if os.geteuid() == 0:
            self.skipTest("под root права на каталог не ограничивают запись")
        # Запрещается запись в РОДИТЕЛЯ, а не в сам каталог журнала: _ensure_dir
        # сама выставляет 700 на каталог журнала при каждом обращении (это
        # осознанное поведение — каталог с отпечатками секретов обязан быть
        # заперт), и чинить чужую порчу этого же каталога было бы неверно
        # проверять. Здесь каталог журнала вовсе не может быть СОЗДАН.
        parent = self.logdir.parent
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o500)  # каталог есть, создавать в нём нечего нельзя
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                ok1 = self.log.event("t", "a", "ok")
                ok2 = self.log.event("t", "b", "ok")
        finally:
            parent.chmod(0o700)

        self.assertFalse(ok1, "event() отчитался успехом при недоступном каталоге")
        self.assertFalse(ok2)
        stderr = buf.getvalue()
        self.assertEqual(stderr.count("журнал недоступен"), 1,
                         "предупреждение обязано печататься ровно один раз за процесс")

    def test_event_never_raises_even_on_bad_input(self):
        """Отказ гасится ЛЮБОЙ, не только «нет прав» — вызывающий код не
        обязан оборачивать event() в try/except."""
        try:
            ok = self.log.event("t", "a", "ok", weird=object())
        except Exception as e:  # noqa: BLE001
            self.fail(f"event() бросил исключение наружу: {e}")
        self.assertIsInstance(ok, bool)


class TestSwitchesGateTheLog(LogFixture):
    def test_disable_flag_skips_writing(self):
        os.environ["SUPERSTACK_DISABLE"] = "1"
        ok = self.log.event("t", "a", "ok")
        self.assertFalse(ok)
        self.assertFalse(self.logfile.exists(),
                         "SUPERSTACK_DISABLE=1 не остановил запись")

    def test_pause_flag_skips_writing(self):
        pause = self.home / ".claude" / "superstack" / "PAUSE"
        pause.parent.mkdir(parents=True)
        pause.write_text("2026-01-01T00:00:00Z", encoding="utf-8")

        ok = self.log.event("t", "a", "ok")
        self.assertFalse(ok, "флаг паузы не остановил запись")
        self.assertFalse(self.logfile.exists())

    def test_ignore_pause_env_bypasses_the_flag(self):
        """Планка выставляет SUPERSTACK_IGNORE_PAUSE=1 на себя — журнал внутри
        самопроверки обязан продолжать писать, иначе о прогоне под паузой не
        останется следа."""
        pause = self.home / ".claude" / "superstack" / "PAUSE"
        pause.parent.mkdir(parents=True)
        pause.write_text("2026-01-01T00:00:00Z", encoding="utf-8")
        os.environ["SUPERSTACK_IGNORE_PAUSE"] = "1"

        ok = self.log.event("t", "a", "ok")
        self.assertTrue(ok, "SUPERSTACK_IGNORE_PAUSE=1 не пробил паузу")
        self.assertTrue(self.logfile.exists())


class TestPathComesFromEnvVar(LogFixture):
    def test_default_path_is_under_home_when_env_unset(self):
        del os.environ["SUPERSTACK_LOG_DIR"]
        log = _load_fresh()
        expected = self.home / ".claude" / "superstack" / "logs"
        self.assertEqual(log._log_dir(), expected)

    def test_env_var_overrides_default(self):
        custom = Path(self.tmp.name) / "elsewhere"
        os.environ["SUPERSTACK_LOG_DIR"] = str(custom)
        log = _load_fresh()
        self.assertEqual(log._log_dir(), custom)
        log.event("t", "a", "ok")
        self.assertTrue((custom / "events.jsonl").is_file(),
                        "запись ушла не туда, куда указывает переменная окружения")


if __name__ == "__main__":
    unittest.main()


class TestSecretByNameInsideAList(LogFixture):
    """Секрет, опознаваемый по ИМЕНИ поля, а не по форме, внутри списка.

    Единственный путь редакции, который не был закрыт ни одним тестом: обход
    списка обязан протаскивать имя родительского ключа вниз. Внешняя проверка
    сняла эту передачу мутацией — набор остался зелёным, то есть механизм можно
    было удалить, и никто бы не заметил.

    Форма значения здесь намеренно НЕЙТРАЛЬНА (не sk-, не ghp_): иначе секрет
    ловился бы вторым, независимым правилом, и тест доказывал бы не то.
    """

    SECRET = "SuperSecretDeployPw2026"

    def setUp(self):
        super().setUp()
        self.lg = _load_fresh()

    def test_named_secret_in_a_list_is_redacted(self):
        out = json.dumps(self.lg.redact({"passwords": [self.SECRET]}), ensure_ascii=False)
        self.assertNotIn(self.SECRET, out)
        self.assertIn("redacted", out)

    def test_named_secret_nested_deeper_is_redacted(self):
        out = json.dumps(self.lg.redact({"creds": [{"password": [self.SECRET]}]}),
                         ensure_ascii=False)
        self.assertNotIn(self.SECRET, out)

    def test_harmless_list_under_a_harmless_key_survives(self):
        """Обратный контроль: редакция, вырезающая всё подряд, делает журнал
        нечитаемым, и его перестают вести."""
        out = self.lg.redact({"files": ["README.md", "tools/log.py"]})
        self.assertEqual(out, {"files": ["README.md", "tools/log.py"]})

    def test_the_value_actually_reaches_the_file_redacted(self):
        """Позитивный контроль на настоящей записи, а не на функции: между
        redact() и диском есть код, и он тоже может потерять защиту."""
        os.environ["SUPERSTACK_LOG_DIR"] = str(self.logdir)
        self.lg.event("тест", "запись", "ок", passwords=[self.SECRET])
        written = "".join(p.read_text("utf-8") for p in self.logdir.glob("*.jsonl"))
        self.assertTrue(written, "событие не записалось — тест ничего не проверяет")
        self.assertNotIn(self.SECRET, written)
