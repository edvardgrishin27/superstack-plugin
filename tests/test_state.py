#!/usr/bin/env python3
"""Тесты состояния установки (tools/state.py).

Зачем этот файл существует.

Подсчёт файлов доказывает, что файл существует. Он не доказывает, что
install_state вообще что-то не пускает. Если `mark_proven_local({})` тихо
проходит — весь смысл фазы доказательства испаряется: любой вызывающий код
(в том числе сама модель в чате) может написать «готово» и получить
proven-local без единой реальной проверки. Поэтому здесь держится ИМЕННО
это: каждый переход отклоняется без содержательной улики, и отклоняется
конкретно ПО СОДЕРЖАНИЮ (подстрока в выводе, статус ворот), а не по факту
присутствия ключей в словаре — пустая строка в поле проходит проверку «ключ
есть», но не должна проходить проверку «в красном выводе есть id теста».

Герметичность: каждый тест подменяет STATE_DIR/STATE_FILE/BACKUPS_DIR на
временный каталог и восстанавливает исходные значения в tearDown. Настоящий
~/.claude здесь не читается и не пишется ни разу.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at, plug  # noqa: E402

ROOT = REPO
def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load("ss_state", at("tools", "state.py"))

GOOD_EVIDENCE = {
    "planted_failure": {
        "planted_id": "test_planted_failure",
        "red_output": "1 failed\ntest_planted_failure FAILED - AssertionError",
        "green_output": "1 passed",
    },
    "negative_control": {"attempts": [
        {"id": "secret_write", "outcome": "rejected"},
        {"id": "self_approval", "outcome": "rejected"},
        {"id": "close_with_red", "outcome": "rejected"},
    ]},
}

GREEN_GAUNTLET = {"done": True, "gates": [
    {"gate": n, "status": "pass"} for n in
    ("набор", "герметичность", "мутации", "правила", "манифест", "план")
]}


def _red_gauntlet(bad_gate: str = "набор") -> dict:
    gates = [{"gate": n, "status": "pass"} for n in
             ("набор", "герметичность", "мутации", "правила", "манифест", "план")]
    for g in gates:
        if g["gate"] == bad_gate:
            g["status"] = "fail"
    return {"done": False, "gates": gates}


class StateFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        sdir = Path(self.tmp.name)
        self._orig = (st.STATE_DIR, st.STATE_FILE, st.BACKUPS_DIR)
        st.STATE_DIR = sdir
        st.STATE_FILE = sdir / "install_state.json"
        st.BACKUPS_DIR = sdir / "backups"

    def tearDown(self):
        st.STATE_DIR, st.STATE_FILE, st.BACKUPS_DIR = self._orig
        self.tmp.cleanup()

    def seed_manifest(self, applied: list = None) -> Path:
        bdir = st.BACKUPS_DIR / "20260101-000000"
        bdir.mkdir(parents=True)
        m = bdir / "manifest.json"
        m.write_text(json.dumps({"applied": applied if applied is not None else [{"id": "x"}]},
                                ensure_ascii=False), encoding="utf-8")
        return m


class TestReadIsHonestAboutAbsence(StateFixture):
    def test_no_file_means_absent_not_error(self):
        self.assertEqual(st.read(), {"state": "absent", "history": []})

    def test_corrupt_file_is_rejected_not_silently_absent(self):
        """Файл ЕСТЬ, но не читается — это не «absent» (пусто, потому что не
        начинали), это отдельная ошибка. Спутать их значит показать «ничего
        не сделано» там, где на самом деле «сделано, но сломано»."""
        st.STATE_DIR.mkdir(parents=True, exist_ok=True)
        st.STATE_FILE.write_text("{не json", encoding="utf-8")
        with self.assertRaises(st.Rejected):
            st.read()

    def test_unknown_state_value_is_rejected(self):
        st.STATE_DIR.mkdir(parents=True, exist_ok=True)
        st.STATE_FILE.write_text(json.dumps({"state": "definitely-done", "history": []}),
                                 encoding="utf-8")
        with self.assertRaises(st.Rejected):
            st.read()


class TestMarkApplied(StateFixture):
    def test_rejected_without_any_manifest(self):
        with self.assertRaises(st.Rejected):
            st.mark_applied()
        self.assertEqual(st.read()["state"], "absent", "состояние сдвинулось без манифеста")

    def test_rejected_with_empty_applied_list(self):
        self.seed_manifest(applied=[])
        with self.assertRaises(st.Rejected):
            st.mark_applied()

    def test_succeeds_with_real_manifest(self):
        self.seed_manifest(applied=[{"id": "a"}, {"id": "b"}])
        r = st.mark_applied()
        self.assertEqual(r["state"], "applied")
        self.assertEqual(st.read()["state"], "applied", "переход не сохранился на диск")

    def test_rejected_from_proven_local_state(self):
        """Нельзя «применить снова» после того, как ушли вперёд по цепочке —
        applied обязан идти сразу за absent, не откуда угодно."""
        self.seed_manifest()
        st.mark_applied()
        st._write("proven-local", {"bootstrap": "тест"})
        with self.assertRaises(st.Rejected):
            st.mark_applied()


class TestMarkProvenLocalRejectsSelfApproval(StateFixture):
    """Это и есть негативный контроль №2, проверенный напрямую на самом
    механизме: без улики переход обязан отклониться. Если этот тест когда-то
    начнёт падать, значит self_approval в prove.py больше ничего не значит."""

    def setUp(self):
        super().setUp()
        self.seed_manifest()
        st.mark_applied()

    def test_empty_evidence_is_rejected(self):
        with self.assertRaises(st.Rejected):
            st.mark_proven_local({})
        self.assertEqual(st.read()["state"], "applied", "proven-local выставилось без улики")

    def test_none_evidence_is_rejected(self):
        with self.assertRaises(st.Rejected):
            st.mark_proven_local(None)

    def test_missing_planted_id_in_red_output_is_rejected(self):
        """Улика заявляет id, но его нет в самом выводе — заявление
        разошлось с содержимым, и это обязано провалить проверку."""
        ev = json.loads(json.dumps(GOOD_EVIDENCE))
        ev["planted_failure"]["red_output"] = "1 failed\nsome_other_test FAILED"
        with self.assertRaises(st.Rejected):
            st.mark_proven_local(ev)

    def test_red_output_without_failure_wording_is_rejected(self):
        ev = json.loads(json.dumps(GOOD_EVIDENCE))
        ev["planted_failure"]["red_output"] = "test_planted_failure ran fine"
        with self.assertRaises(st.Rejected):
            st.mark_proven_local(ev)

    def test_planted_id_still_in_green_output_is_rejected(self):
        """«Починка» не убрала подложенный тест — значит починки не было."""
        ev = json.loads(json.dumps(GOOD_EVIDENCE))
        ev["planted_failure"]["green_output"] = "1 passed\ntest_planted_failure passed"
        with self.assertRaises(st.Rejected):
            st.mark_proven_local(ev)

    def test_missing_negative_control_is_rejected(self):
        ev = json.loads(json.dumps(GOOD_EVIDENCE))
        ev["negative_control"]["attempts"] = [
            a for a in ev["negative_control"]["attempts"] if a["id"] != "secret_write"]
        with self.assertRaises(st.Rejected):
            st.mark_proven_local(ev)

    def test_unblocked_negative_control_is_rejected(self):
        """Непойманный контроль — это провал перехода целиком, не «2 из 3»."""
        ev = json.loads(json.dumps(GOOD_EVIDENCE))
        ev["negative_control"]["attempts"][1]["outcome"] = "unblocked"
        with self.assertRaises(st.Rejected):
            st.mark_proven_local(ev)

    def test_real_evidence_succeeds(self):
        r = st.mark_proven_local(GOOD_EVIDENCE)
        self.assertEqual(r["state"], "proven-local")
        self.assertEqual(st.read()["state"], "proven-local")

    def test_rejected_when_prior_state_is_not_applied(self):
        """После первого успешного перехода состояние уже proven-local —
        повторный вызов обязан отклониться по стадии, а не тихо повторить
        запись."""
        st.mark_proven_local(GOOD_EVIDENCE)
        with self.assertRaises(st.Rejected):
            st.mark_proven_local(GOOD_EVIDENCE)


class TestMarkProvenRejectsCloseWithRed(StateFixture):
    """Негативный контроль №3 на самом механизме: proven не выставляется,
    пока хоть одни ворота планки красные."""

    def setUp(self):
        super().setUp()
        self.seed_manifest()
        st.mark_applied()
        st.mark_proven_local(GOOD_EVIDENCE)

    def test_rejected_from_wrong_prior_state(self):
        # откатим искусственно на applied и попробуем proven напрямую
        st._write("applied", {"bootstrap": "тест"})
        with self.assertRaises(st.Rejected):
            st.mark_proven(runner=lambda: GREEN_GAUNTLET)

    def test_rejected_when_any_gate_is_red(self):
        with self.assertRaises(st.Rejected):
            st.mark_proven(runner=lambda: _red_gauntlet())
        self.assertEqual(st.read()["state"], "proven-local", "proven выставилось при красных воротах")

    def test_rejected_when_done_is_false_even_if_gates_all_pass(self):
        """Дефолт-в-глубину: даже если КАЖДЫЕ ворота помечены pass, а поле
        done соврало (false), переход обязан отклониться — не доверяем
        одному полю больше, чем содержимому массива ворот."""
        faked = {"done": False, "gates": GREEN_GAUNTLET["gates"]}
        with self.assertRaises(st.Rejected):
            st.mark_proven(runner=lambda: faked)

    def test_rejected_when_fewer_than_six_gates(self):
        with self.assertRaises(st.Rejected):
            st.mark_proven(runner=lambda: {"done": True, "gates": [{"gate": "набор", "status": "pass"}]})

    def test_succeeds_with_real_green_run(self):
        r = st.mark_proven(runner=lambda: GREEN_GAUNTLET)
        self.assertEqual(r["state"], "proven")
        self.assertEqual(st.read()["state"], "proven")

    def test_history_is_append_only_and_ordered(self):
        st.mark_proven(runner=lambda: GREEN_GAUNTLET)
        hist = st.read()["history"]
        self.assertEqual([h["to"] for h in hist], ["applied", "proven-local", "proven"])
        self.assertEqual([h["from"] for h in hist], ["absent", "applied", "proven-local"])


class TestCliHasNoSetCommand(unittest.TestCase):
    """Модель не имеет способа продвинуть состояние иначе, чем через код.
    Если когда-нибудь в CLI добавят `state.py set X`, вот тест, который на
    это отреагирует."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"HOME": self.tmp.name, "PATH": "/usr/bin:/bin"}

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, args: list) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(at("tools", "state.py")), *args],
                              capture_output=True, text=True, timeout=30, env=self.env)

    def test_set_is_not_a_recognized_command(self):
        r = self._run(["set", "proven"])
        self.assertNotEqual(r.returncode, 0)
        state_file = Path(self.tmp.name) / ".claude" / "superstack" / "install_state.json"
        self.assertFalse(state_file.exists(), "состояние продвинулось через голый CLI-вызов")

    def test_show_on_fresh_home_reports_absent(self):
        r = self._run(["show"])
        self.assertEqual(r.returncode, 0, r.stderr)
        v = json.loads(r.stdout)
        self.assertEqual(v["state"], "absent")

    def test_no_args_is_a_usage_error_not_a_silent_success(self):
        r = self._run([])
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
