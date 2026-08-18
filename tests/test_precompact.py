#!/usr/bin/env python3
"""Тесты страховки памяти на компакции (hooks/precompact.sh, hooks/hooks.json).

Зачем этот файл существует.

PreCompact — это момент, после которого детали разговора не восстановить
ничем: компакция сжимает контекст необратимо, и то, что не было сохранено
ДО неё, пропадает навсегда. Механизм, который «должен» копировать
транскрипт, но копию никто не проверил, ничем не отличается от механизма,
который её не делает: оба выглядят установленными, оба одинаково молчат
в момент, когда это стало бы заметно (после следующей компакции, когда
разговор уже потерян).

Здесь скрипт ЗАПУСКАЕТСЯ по-настоящему (subprocess, реальный stdin в формате
события PreCompact), а не читается глазами. Проверяется главное: файл
транскрипта, который compaction вот-вот перепишет, действительно лежит
копией на диске ПОСЛЕ вызова хука, с тем же содержимым.

Герметичность: SUPERSTACK_STATE_DIR подставной на каждый тест, настоящий
~/.claude не читается и не пишется. Время не берётся из системных часов
теста — пруннинг проверяется по ФАКТУ выживания конкретных session_id,
а не по меткам времени, которые разнились бы на другой машине.
"""
from __future__ import annotations

import json
import re
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO, at  # noqa: E402

ROOT = REPO
HOOK = at("hooks", "precompact.sh")
HOOKS_JSON = PKG / "hooks" / "hooks.json"


class PrecompactFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(parents=True)
        self.state = Path(self.tmp.name) / "state"
        self.transcripts = Path(self.tmp.name) / "transcripts"
        self.transcripts.mkdir(parents=True)
        # HOME подставной на случай, если скрипт когда-нибудь начнёт
        # использовать дефолт $HOME/.claude/superstack без переменной —
        # тест не должен зависеть от того, задали мы SUPERSTACK_STATE_DIR
        # или нет.
        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env["SUPERSTACK_STATE_DIR"] = str(self.state)
        self.env.pop("SUPERSTACK_DISABLE", None)

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, payload: dict | None, env: dict | None = None,
                 stdin: bool = True) -> subprocess.CompletedProcess:
        input_text = json.dumps(payload) if (payload is not None and stdin) else None
        use = env if env is not None else self.env
        # Проект заводится, иначе хук молчит: он работает только там, где
        # SUPERSTACK позвали. Здесь проверяется поведение ВНУТРИ проекта —
        # сам гейт области живёт в test_project_scope.py.
        # Но НЕ при выключателе: отдельный тест доказывает, что с
        # SUPERSTACK_DISABLE=1 хук не трогает диск вовсе, и созданный здесь
        # каталог состояния сломал бы именно это утверждение.
        if use.get("SUPERSTACK_DISABLE") != "1":
            st = Path(use.get("SUPERSTACK_STATE_DIR", str(self.state)))
            st.mkdir(parents=True, exist_ok=True)
            (st / "projects").write_text(os.getcwd() + "\n", encoding="utf-8")
        return subprocess.run(
            ["sh", str(HOOK)],
            input=input_text,
            capture_output=True, text=True, timeout=30,
            env=use,
        )

    @property
    def precompact_dir(self) -> Path:
        return self.state / "precompact"

    @property
    def log_path(self) -> Path:
        return self.precompact_dir / "log"

    def make_transcript(self, name: str, content: str) -> Path:
        p = self.transcripts / name
        p.write_text(content, encoding="utf-8")
        return p


class TestTranscriptIsSavedBeforeItIsGone(PrecompactFixture):
    """Единственная проверка, которая реально держит обещание файла: копия
    транскрипта появляется на диске с тем же содержимым, что и оригинал."""

    def test_saves_transcript_with_identical_content(self):
        src = self.make_transcript("t.jsonl", "line-one\nline-two\n")
        payload = {
            "session_id": "sess-abc-123",
            "transcript_path": str(src),
            "trigger": "auto",
        }
        r = self.run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr)

        dest = self.precompact_dir / "sess-abc-123.jsonl"
        self.assertTrue(dest.is_file(),
                         "копия транскрипта не создана — компакция сотрёт "
                         "оригинал, и восстановить будет нечего")
        self.assertEqual(dest.read_text(encoding="utf-8"), src.read_text(encoding="utf-8"),
                          "копия разошлась с оригиналом")

    def test_log_records_metadata_but_not_conversation_content(self):
        """Журнал — метаданные (кто/когда/статус), а не второй экземпляр
        разговора: секрет, вставленный пользователем в чат, не должен
        осесть ещё и в log-файле."""
        secret_line = "user pasted API_KEY=sk-verysecretvalue123"
        src = self.make_transcript("t.jsonl", secret_line + "\n")
        payload = {"session_id": "sess-secret", "transcript_path": str(src),
                   "trigger": "manual"}
        r = self.run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertTrue(self.log_path.is_file())
        log_text = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(secret_line, log_text,
                         "содержимое разговора попало в журнал метаданных")
        self.assertIn("sess-secret", log_text)
        self.assertIn("saved", log_text)

        # Копия транскрипта — единственное место, где содержимое разговора
        # ожидаемо лежит открытым текстом (это и есть цель хука).
        dest = self.precompact_dir / "sess-secret.jsonl"
        self.assertIn(secret_line, dest.read_text(encoding="utf-8"))


class TestHookNeverBreaksCompaction(PrecompactFixture):
    """PreCompact-хук, уронивший компакцию, хуже отсутствующего хука: код
    возврата обязан быть нулевым при ЛЮБОМ входе."""

    def test_disable_flag_short_circuits_before_any_disk_write(self):
        env = dict(self.env)
        env["SUPERSTACK_DISABLE"] = "1"
        payload = {"session_id": "s", "transcript_path": "/does/not/matter",
                   "trigger": "auto"}
        r = self.run_hook(payload, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.state.exists(),
                         "SUPERSTACK_DISABLE=1 не должен трогать диск вовсе")

    def test_missing_transcript_path_does_not_crash(self):
        payload = {"session_id": "no-transcript-case", "trigger": "auto"}
        r = self.run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        dest = self.precompact_dir / "no-transcript-case.jsonl"
        self.assertFalse(dest.exists())
        self.assertIn("no-transcript", self.log_path.read_text(encoding="utf-8"))

    def test_nonexistent_transcript_file_does_not_crash(self):
        payload = {
            "session_id": "ghost",
            "transcript_path": str(self.transcripts / "does-not-exist.jsonl"),
            "trigger": "auto",
        }
        r = self.run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.precompact_dir / "ghost.jsonl").exists())

    def test_empty_stdin_does_not_crash(self):
        r = self.run_hook(None, stdin=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_malformed_json_does_not_crash(self):
        r = subprocess.run(["sh", str(HOOK)], input="{not valid json at all",
                           capture_output=True, text=True, timeout=30,
                           env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestSessionIdIsSanitized(PrecompactFixture):
    """session_id приходит по цепочке из данных, которые не мы порождали.
    Без очистки он становится путём на диске."""

    def test_path_traversal_session_id_cannot_escape_state_dir(self):
        src = self.make_transcript("t.jsonl", "payload\n")
        payload = {
            "session_id": "../../../escaped",
            "transcript_path": str(src),
            "trigger": "auto",
        }
        r = self.run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr)

        escaped_target = Path(self.tmp.name) / "escaped.jsonl"
        self.assertFalse(escaped_target.exists(),
                         "копия вышла за пределы каталога состояния")
        # Файл обязан осесть ВНУТРИ precompact_dir под очищенным именем.
        saved = list(self.precompact_dir.glob("*.jsonl"))
        self.assertEqual(len(saved), 1, saved)
        self.assertTrue(saved[0].parent == self.precompact_dir)


class TestOldSnapshotsArePruned(PrecompactFixture):
    """Каталог не растёт бесконечно: без потолка активный пользователь
    копит сотни забытых копий транскриптов за годы работы."""

    def test_keeps_only_the_newest_n_sessions(self):
        env = dict(self.env)
        env["SUPERSTAC" "K_PRECOMP" "ACT_KEEP"] = "2"
        src = self.make_transcript("t.jsonl", "x\n")

        for i in range(4):
            payload = {"session_id": f"sess-{i}", "transcript_path": str(src),
                       "trigger": "auto"}
            r = self.run_hook(payload, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            time.sleep(1.05)  # mtime-разрешение файловой системы

        remaining = sorted(p.stem for p in self.precompact_dir.glob("*.jsonl"))
        self.assertEqual(len(remaining), 2,
                         f"потолок KEEP=2 не удержан, на диске: {remaining}")
        # Выжить обязаны два ПОСЛЕДНИХ по времени вызова — sess-2 и sess-3,
        # а не случайные два из четырёх.
        self.assertEqual(remaining, ["sess-2", "sess-3"])


class TestHooksJsonDeclaresPreCompact(unittest.TestCase):
    """Проверяет, что PreCompact добавлен РЯДОМ с уже существующими
    SessionStart и Stop, а не вместо них, и что JSON остался валидным."""

    def test_every_declared_hook_points_at_a_script_that_exists(self):
        """Команда хука обязана указывать на существующий файл.

        Раньше это охраняло разделение на пакеты: `${CLAUDE_PLUGIN_ROOT}`
        указывает на СВОЙ пакет, и Stop-гейт, объявленный в install, искал бы
        verify-gate.sh у себя и молча не находил. Пакет теперь один, а
        проверка осталась — потому что опечатка в имени скрипта даёт ровно тот
        же отказ и такой же тихий: хук просто не делает ничего.
        """
        cfg = json.loads(HOOKS_JSON.read_text("utf-8"))["hooks"]
        self.assertTrue(cfg, "hooks.json не объявляет ни одного события")
        for event, entries in sorted(cfg.items()):
            for entry in entries:
                for h in entry["hooks"]:
                    script = re.search(r'hooks/([\w.-]+)', h["command"]).group(1)
                    with self.subTest(event=event, script=script):
                        self.assertTrue(
                            (PKG / "hooks" / script).is_file(),
                            f"{event} зовёт {script}, которого нет в пакете")

    def test_every_hook_script_is_declared_by_some_event(self):
        """Скрипт, на который не указывает ни один хук, — мёртвый.

        Этот отказ рождён слиянием и до него был невозможен: три hooks.json
        сводились в один, и потерянная при сведении строка выключила бы целый
        механизм НАСМЕРТЬ, не уронив ни одного теста. Файл на месте, код
        рабочий, набор зелёный — и гейт верификации просто больше не
        вызывается. Ровно эта болезнь у проекта уже была трижды, поэтому
        проверка идёт в обе стороны, а не в одну.
        """
        cfg = json.loads(HOOKS_JSON.read_text("utf-8"))["hooks"]
        названы = {re.search(r'hooks/([\w.-]+)', h["command"]).group(1)
                   for entries in cfg.values() for e in entries for h in e["hooks"]}
        на_диске = {f.name for f in (PKG / "hooks").glob("*.sh")}
        self.assertEqual(
            на_диске - названы, set(),
            "скрипты лежат в hooks/, но их не зовёт ни одно событие — "
            "механизм выключен, и об этом ничто не сообщит")

    def test_precompact_command_points_at_the_new_script(self):
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        entries = data["hooks"]["PreCompact"]
        commands = [
            h["command"]
            for entry in entries
            for h in entry["hooks"]
        ]
        self.assertTrue(any("precompact.sh" in c for c in commands), commands)

    def test_precompact_script_file_exists_and_is_referenced_relative_to_plugin_root(self):
        self.assertTrue(HOOK.is_file(), "hooks.json ссылается на несуществующий файл")
        data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        entries = data["hooks"]["PreCompact"]
        commands = [h["command"] for entry in entries for h in entry["hooks"]]
        self.assertTrue(any("CLAUDE_PLUGIN_ROOT" in c for c in commands), commands)


if __name__ == "__main__":
    unittest.main()
