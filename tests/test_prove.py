#!/usr/bin/env python3
"""Тесты фазы доказательства (tools/prove.py).

Зачем этот файл существует.

Подсчёт файлов доказывает, что файл существует. Работоспособность доказывает
ТОЛЬКО заблокированная попытка — и этот файл проверяет именно блокировку, а
не «текст на экране похож на успех». Здесь держится три вещи, каждая — от
своего способа соврать:

  · planted_failure «pass» ТОЛЬКО когда pytest реально заметил подложенное
    падение и реально перестал его видеть после починки. Harness, который
    всегда отвечает «зелено» независимо от кода (сломанный сбор тестов,
    молча проглоченное исключение), обязан быть назван fail, а не pass —
    тестируется тем, что _pytest() подменяется на лгущую заглушку;
  · каждый из трёх негативных контролей действительно СЧИТАЕТ по содержимому
    (секрет в файле, поднятое исключение), а не всегда отвечает «rejected» —
    тестируется тем, что защита каждого механизма (log.redact / state.py)
    подменяется на сломанную заглушку, и тогда контроль обязан сообщить
    «unblocked»;
  · непойманный (unblocked) контроль красит ВЕСЬ прогон в fail, а не в
    «2 из 3 пройдено» — вплоть до итогового run().

Герметичность: planted_failure работает в temp-каталоге, а не в tests/ этого
репозитория. Негативные контроли направлены на временный install_state и
временный журнал — оригинальные атрибуты `state`/`log`-модулей, которые
использует prove.py, сохраняются и восстанавливаются в каждом тесте, который
их трогает. Настоящий ~/.claude эти тесты не читают и не пишут.
"""
from __future__ import annotations

import contextlib
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
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pv = _load("ss_prove", at("tools", "prove.py"))


@contextlib.contextmanager
def _env(**kv):
    """Временно выставить переменные окружения и вернуть как было — те же
    переменные, что и SUPERSTACK_LOG_DIR/SUPERSTACK_IGNORE_PAUSE, которые сам
    prove.py подменяет и восстанавливает вокруг негативных контролей."""
    saved = {k: os.environ.get(k) for k in kv}
    os.environ.update(kv)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class StateSandbox(unittest.TestCase):
    """Подменяет STATE_DIR/STATE_FILE/BACKUPS_DIR модуля state, который
    использует prove.py, на временный каталог — независимо от того, что
    делает test_state.py со своей отдельно загруженной копией того же файла."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        sdir = Path(self.tmp.name)
        self._orig = (pv._state_mod.STATE_DIR, pv._state_mod.STATE_FILE,
                      pv._state_mod.BACKUPS_DIR)
        pv._state_mod.STATE_DIR = sdir
        pv._state_mod.STATE_FILE = sdir / "install_state.json"
        pv._state_mod.BACKUPS_DIR = sdir / "backups"

    def tearDown(self):
        (pv._state_mod.STATE_DIR, pv._state_mod.STATE_FILE,
         pv._state_mod.BACKUPS_DIR) = self._orig
        self.tmp.cleanup()


# ==========================================================================
# planted_failure
# ==========================================================================
class TestPlantedFailureMechanism(unittest.TestCase):
    def test_real_cycle_catches_and_fixes(self):
        r = pv._check_planted_failure()
        self.assertEqual(r["status"], "pass", r)
        self.assertTrue(r["caught"], r)
        self.assertTrue(r["fixed"], r)
        self.assertIn(pv.PLANTED_ID, r["red_output"])
        self.assertIn(pv.PLANTED_MARKER, r["red_output"])
        self.assertNotIn(pv.PLANTED_ID, r["green_output"])

    def test_harness_that_never_fails_is_reported_as_fail(self):
        """Сломанный harness, который всегда отвечает «зелено», обязан быть
        назван fail — иначе вся проверка превращается именно в то
        самонарисованное зелёное, против которого она написана."""
        orig = pv._pytest
        pv._pytest = lambda cwd: (0, "1 passed in 0.00s")
        try:
            r = pv._check_planted_failure()
        finally:
            pv._pytest = orig
        self.assertEqual(r["status"], "fail", r)
        self.assertFalse(r["caught"], r)

    def test_infra_failure_is_unknown_not_fail(self):
        """Таймаут pytest — это «не смог проверить», а не «сломано»."""
        orig = pv._pytest
        pv._pytest = lambda cwd: (124, "не завершилось")
        try:
            r = pv._check_planted_failure()
        finally:
            pv._pytest = orig
        self.assertEqual(r["status"], "unknown", r)

    def test_green_run_that_still_fails_is_reported_as_fail(self):
        """Красное поймано, но починка не дала зелёного — тоже fail, не pass."""
        orig = pv._pytest
        calls = {"n": 0}

        def fake(cwd):
            calls["n"] += 1
            if calls["n"] == 1:
                return 1, f"{pv.PLANTED_ID} FAILED {pv.PLANTED_MARKER}"
            return 1, "1 failed, unrelated_test FAILED"

        pv._pytest = fake
        try:
            r = pv._check_planted_failure()
        finally:
            pv._pytest = orig
        self.assertEqual(r["status"], "fail", r)
        self.assertTrue(r["caught"], r)
        self.assertFalse(r["fixed"], r)


# ==========================================================================
# негативный контроль №1 — секрет
# ==========================================================================
class TestSecretWriteControl(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_redaction_is_rejected(self):
        with _env(**{pv._log_mod.LOG_DIR_ENV: str(self.log_dir),
                     "SUPERSTACK_IGNORE_PAUSE": "1"}):
            r = pv._attempt_secret_write(self.log_dir)
        self.assertEqual(r["id"], "secret_write")
        self.assertEqual(r["outcome"], "rejected", r)
        text = (self.log_dir / pv._log_mod.LOG_FILE_NAME).read_text(encoding="utf-8")
        self.assertNotIn(pv._FAKE_SECRET, text)

    def test_leaking_backend_is_reported_as_unblocked(self):
        """Если сам механизм редактирования сломан и секрет доехал до диска
        открытым текстом, контроль обязан назвать это unblocked, а не
        rejected — иначе негативный контроль сам по себе бесполезен."""
        def leaky_event(tool, action, outcome, **fields):
            path = self.log_dir / pv._log_mod.LOG_FILE_NAME
            path.write_text(json.dumps({"secret": fields.get("secret")}) + "\n",
                            encoding="utf-8")
            return True

        orig = pv._log_mod.event
        pv._log_mod.event = leaky_event
        try:
            with _env(**{pv._log_mod.LOG_DIR_ENV: str(self.log_dir)}):
                r = pv._attempt_secret_write(self.log_dir)
        finally:
            pv._log_mod.event = orig
        self.assertEqual(r["outcome"], "unblocked", r)

    def test_missing_log_file_is_unknown(self):
        def noop_event(tool, action, outcome, **fields):
            return False

        orig = pv._log_mod.event
        pv._log_mod.event = noop_event
        try:
            with _env(**{pv._log_mod.LOG_DIR_ENV: str(self.log_dir)}):
                r = pv._attempt_secret_write(self.log_dir)
        finally:
            pv._log_mod.event = orig
        self.assertEqual(r["outcome"], "unknown", r)


# ==========================================================================
# негативный контроль №2 — самоодобрение
# ==========================================================================
class TestSelfApprovalControl(StateSandbox):
    def test_real_state_module_rejects_it(self):
        r = pv._attempt_self_approval()
        self.assertEqual(r["id"], "self_approval")
        self.assertEqual(r["outcome"], "rejected", r)

    def test_broken_state_module_is_reported_as_unblocked(self):
        """Если бы state.mark_proven_local когда-нибудь перестал проверять
        улику, эта попытка обязана заметить это и назвать unblocked."""
        orig = pv._state_mod.mark_proven_local
        pv._state_mod.mark_proven_local = lambda evidence: {"state": "proven-local"}
        try:
            r = pv._attempt_self_approval()
        finally:
            pv._state_mod.mark_proven_local = orig
        self.assertEqual(r["outcome"], "unblocked", r)


# ==========================================================================
# негативный контроль №3 — закрыть ход с красными тестами
# ==========================================================================
class TestCloseWithRedControl(StateSandbox):
    def test_real_state_module_rejects_it(self):
        r = pv._attempt_close_with_red()
        self.assertEqual(r["id"], "close_with_red")
        self.assertEqual(r["outcome"], "rejected", r)

    def test_broken_state_module_is_reported_as_unblocked(self):
        orig = pv._state_mod.mark_proven
        pv._state_mod.mark_proven = lambda runner=None: {"state": "proven"}
        try:
            r = pv._attempt_close_with_red()
        finally:
            pv._state_mod.mark_proven = orig
        self.assertEqual(r["outcome"], "unblocked", r)


# ==========================================================================
# сборка трёх контролей
# ==========================================================================
class TestCheckNegativeControls(unittest.TestCase):
    def test_all_real_controls_pass_and_environment_is_restored(self):
        orig_state_paths = (pv._state_mod.STATE_DIR, pv._state_mod.STATE_FILE,
                            pv._state_mod.BACKUPS_DIR)
        orig_log_dir = os.environ.get(pv._log_mod.LOG_DIR_ENV)
        orig_ignore_pause = os.environ.get("SUPERSTACK_IGNORE_PAUSE")

        r = pv._check_negative_controls()

        self.assertEqual(r["status"], "pass", r)
        self.assertEqual({a["id"] for a in r["attempts"]},
                         {"secret_write", "self_approval", "close_with_red"})
        self.assertTrue(all(a["outcome"] == "rejected" for a in r["attempts"]), r)
        # окружение и модуль state — байт-в-байт как были, иначе три попытки
        # негативного контроля сами испортили бы реальную установку.
        self.assertEqual((pv._state_mod.STATE_DIR, pv._state_mod.STATE_FILE,
                          pv._state_mod.BACKUPS_DIR), orig_state_paths)
        self.assertEqual(os.environ.get(pv._log_mod.LOG_DIR_ENV), orig_log_dir)
        self.assertEqual(os.environ.get("SUPERSTACK_IGNORE_PAUSE"), orig_ignore_pause)

    def test_any_unblocked_attempt_fails_the_whole_check(self):
        orig = pv._attempt_self_approval
        pv._attempt_self_approval = lambda: {"id": "self_approval", "outcome": "unblocked",
                                             "detail": "подставной провал"}
        try:
            r = pv._check_negative_controls()
        finally:
            pv._attempt_self_approval = orig
        self.assertEqual(r["status"], "fail", r)

    def test_unexpected_exception_is_unknown_not_unblocked(self):
        orig = pv._attempt_close_with_red
        pv._attempt_close_with_red = lambda: (_ for _ in ()).throw(RuntimeError("бум"))
        try:
            r = pv._check_negative_controls()
        finally:
            pv._attempt_close_with_red = orig
        self.assertEqual(r["status"], "unknown", r)
        self.assertFalse(any(a["outcome"] == "unblocked" for a in r["attempts"]),
                         "неожиданный отказ не должен читаться как «непойманный контроль»")


# ==========================================================================
# run() — итог
# ==========================================================================
class TestRunOverallStatus(unittest.TestCase):
    def setUp(self):
        self._orig_pf = pv._check_planted_failure
        self._orig_nc = pv._check_negative_controls
        self._orig_mpl = pv._state_mod.mark_proven_local

    def tearDown(self):
        pv._check_planted_failure = self._orig_pf
        pv._check_negative_controls = self._orig_nc
        pv._state_mod.mark_proven_local = self._orig_mpl

    def _stub(self, pf_status: str, nc_status: str):
        pv._check_planted_failure = lambda: {"mechanism": "planted_failure", "status": pf_status,
                                             "planted_id": "x", "red_output": "x failed",
                                             "green_output": "1 passed"}
        pv._check_negative_controls = lambda: {"mechanism": "negative_control", "status": nc_status,
                                               "attempts": [{"id": i, "outcome": "rejected", "detail": ""}
                                                            for i in pv._state_mod.REQUIRED_CONTROLS]}

    def test_any_fail_makes_overall_fail_without_touching_state(self):
        self._stub("fail", "pass")
        calls = []
        pv._state_mod.mark_proven_local = lambda ev: calls.append(ev) or {"state": "proven-local"}
        v = pv.run()
        self.assertEqual(v["status"], "fail", v)
        self.assertEqual(calls, [], "install_state не должен трогаться, если проверки не прошли целиком")

    def test_any_unknown_without_fail_makes_overall_unknown(self):
        self._stub("unknown", "pass")
        v = pv.run()
        self.assertEqual(v["status"], "unknown", v)

    def test_both_pass_calls_mark_proven_local_and_reports_new_state(self):
        self._stub("pass", "pass")
        pv._state_mod.mark_proven_local = lambda ev: {"state": "proven-local"}
        v = pv.run()
        self.assertEqual(v["status"], "pass", v)
        self.assertEqual(v["install_state"], "proven-local")

    def test_both_pass_but_state_rejects_downgrades_to_fail(self):
        """Обе проверки честно прошли, но install_state отклонил переход по
        своей причине (например, реальная установка не в «applied»).
        Итог обязан стать fail, а не остаться «pass» с забытой ошибкой."""
        self._stub("pass", "pass")

        def rejecting(ev):
            raise pv._state_mod.Rejected("нужен переход из «applied»")

        pv._state_mod.mark_proven_local = rejecting
        v = pv.run()
        self.assertEqual(v["status"], "fail", v)
        self.assertIn("install_state_error", v)


# ==========================================================================
# CLI — сквозной прогон в песочнице
# ==========================================================================
class TestCliSmoke(unittest.TestCase):
    def test_json_output_is_parseable_and_exit_code_matches_status(self):
        with tempfile.TemporaryDirectory() as home:
            env = {"HOME": home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                  "SUPERSTACK_IGNORE_PAUSE": "1"}
            r = subprocess.run([sys.executable, str(at("tools", "prove.py")), "--json"],
                               capture_output=True, text=True, timeout=120, env=env)
        v = json.loads(r.stdout)
        self.assertEqual(v["tool"], "prove")
        self.assertIn(v["status"], ("pass", "fail", "unknown"))
        self.assertEqual(len(v["checks"]), 2)
        self.assertEqual({c["mechanism"] for c in v["checks"]},
                         {"planted_failure", "negative_control"})
        # свежий HOME без апплая: оба внутренних механизма честно проходят,
        # а install_state отклоняет финальный переход по стадии — значит
        # итог fail с названной причиной, а не тихий «pass».
        self.assertEqual(v["status"], "fail", v)
        self.assertIn("install_state_error", v)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
