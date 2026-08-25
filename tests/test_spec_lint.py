#!/usr/bin/env python3
"""Тесты линта спеки (`tools/spec_lint.py`).

Что здесь держится. Линт существует ради одного отказа: спека выглядит как
спека, а критерий приёмки в ней проверить нечем. Поэтому набор проверяет не
«скрипт запустился», а ровно то, что скрипт обязан ловить: исчезнувший раздел,
заголовок без содержания и «Как проверить», в котором написано «работает».

Дисциплина, без которой эти тесты ничего не стоили бы:

  * спека подаётся ФИКСТУРОЙ — текст пишется прямо здесь и во временный
    каталог. Настоящий ~/.claude, сеть, время и текущий каталог не читаются,
    HOME подставляется, поэтому результат на другой машине тот же;
  * ожидаемые коды возврата и коды замечаний записаны буквально (0/1/2/3/10,
    "accept.no_check" и прочие), а не взяты из самого инструмента: значение,
    полученное из проверяемого кода, доказывает лишь, что код равен себе;
  * есть обратный контроль — спека с командой и словом «готово» обязана
    остаться ЧИСТОЙ. Без него проверку пустых формулировок можно было бы
    «починить», объявив пустым вообще всё.
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
TOOL = at("tools", "spec_lint.py")

# --- эталонные куски спеки -------------------------------------------------
# Разделы держатся врозь, чтобы тест мог убрать РОВНО ОДИН и остальное не
# трогать: иначе «раздела нет» доказывалось бы заодно поломкой соседей.
OUTCOME = """## Что должно получиться
Человек открывает страницу входа и попадает внутрь по своей паре логин-пароль,
а по чужой видит понятную ошибку и остаётся на месте.
"""

# Пример действия с ожидаемым результатом — дословно из skills/go/SKILL.md,
# фаза 2. Если линт забракует его, забракован формат, объявленный скиллом.
ACCEPT_ACTION = """## Как проверить
Открыть /login, ввести неверный пароль, увидеть ошибку и остаться на странице.
"""

ACCEPT_COMMAND = """## Как проверить
```bash
python3 -m pytest tests/test_login.py -q
```
Ожидается код возврата 0.
"""

SCOPE = """## Чего НЕ делаем
Не делаем восстановление пароля и вход через сторонние сервисы.
"""

HEADER = "# Спека: вход по паролю\n\n"


def spec(*parts: str) -> str:
    return HEADER + "\n".join(parts)


def _env(home: Path, ignore_pause: str = "1") -> dict:
    """Окружение прогона. Наследовать окружение целиком нельзя: через него
    на инструмент протекает и настоящий HOME, и флаг паузы хозяина машины."""
    env = {
        "HOME": str(home),
        "PATH": "",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if ignore_pause is not None:
        env["SUPERSTACK_IGNORE_PAUSE"] = ignore_pause
    return env


class SpecLintCase(unittest.TestCase):
    """Общая обвязка: временный каталог на каждый тест, ничего снаружи."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="superstack-spec-lint-")
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def write(self, text, name: str = "spec.md") -> Path:
        path = self.tmp / name
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def run_tool(self, *args: str, ignore_pause: str = "1", home: Path = None):
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True, text=True, timeout=60,
            cwd=str(self.tmp), env=_env(home or self.home, ignore_pause))

    def lint(self, text, *flags: str):
        """Прогон на готовом тексте: (код возврата, разобранный JSON, stderr)."""
        path = self.write(text)
        r = self.run_tool(*flags, str(path))
        try:
            data = json.loads(r.stdout)
        except ValueError as e:
            raise AssertionError(
                f"stdout не разбирается как JSON ({e}):\n{r.stdout[:2000]}\n"
                f"stderr:\n{r.stderr[:2000]}")
        return r.returncode, data, r.stderr

    def codes(self, data: dict) -> list:
        return [p["code"] for p in data["problems"]]


class TestToolExists(SpecLintCase):
    def test_tool_file_is_present(self):
        """Улика для планки: инструмент существует по объявленному пути."""
        self.assertTrue(TOOL.is_file(), f"нет файла инструмента: {TOOL}")


class TestWholeSpec(SpecLintCase):
    def test_full_spec_with_action_is_clean(self):
        code, data, _ = self.lint(spec(OUTCOME, ACCEPT_ACTION, SCOPE))
        self.assertEqual(code, 0, f"чистая спека дала код {code}: {data['problems']}")
        self.assertEqual(data["status"], "clean")
        self.assertEqual(data["problems"], [])

    def test_full_spec_with_command_is_clean(self):
        """Команда в кодовом блоке — второй объявленный вид доказательства."""
        code, data, _ = self.lint(spec(OUTCOME, ACCEPT_COMMAND, SCOPE))
        self.assertEqual(code, 0, f"код {code}: {data['problems']}")
        self.assertEqual(data["sections"]["accept"]["claim"], "command")

    def test_command_plus_word_gotovo_stays_clean(self):
        """Обратный контроль против перегиба.

        «Готово» рядом с настоящей командой — не пустая формулировка, а
        обычное слово. Линт, который валит и такую спеку, люди отключают,
        и тогда он не ловит уже ничего.
        """
        accept = ("## Как проверить\n"
                  "Запустить `python3 -m pytest -q`, увидеть код возврата 0. Готово.\n")
        code, data, _ = self.lint(spec(OUTCOME, accept, SCOPE))
        self.assertEqual(code, 0, f"ложное срабатывание: {data['problems']}")


class TestMissingSections(SpecLintCase):
    """Раздел исчезает молча — это и есть тот отказ, ради которого линт есть."""

    def test_each_required_section_is_missed_when_removed(self):
        whole = {"outcome": OUTCOME, "accept": ACCEPT_ACTION, "scope": SCOPE}
        for dropped in ("outcome", "accept", "scope"):
            with self.subTest(раздел=dropped):
                parts = [v for k, v in whole.items() if k != dropped]
                code, data, _ = self.lint(spec(*parts))
                self.assertEqual(code, 1, f"пропажа раздела не дала код 1: {data}")
                missing = [p for p in data["problems"]
                           if p["code"] == "section.missing"]
                self.assertEqual([p["section"] for p in missing], [dropped],
                                 f"замечание не про {dropped}: {data['problems']}")
                self.assertFalse(data["sections"][dropped]["present"])

    def test_section_name_in_prose_is_not_a_section(self):
        """Упоминание «как проверить» в абзаце разделом не является.

        Иначе линт превращается в поиск подстроки в прозе, а спеку можно
        «починить», написав нужные слова в любом месте.
        """
        prose = ("## Что должно получиться\n"
                 "Форма входа. Как проверить — решим позже.\n")
        code, data, _ = self.lint(spec(prose, SCOPE))
        self.assertEqual(code, 1)
        self.assertIn("accept", [p["section"] for p in data["problems"]
                                 if p["code"] == "section.missing"])


class TestEmptySection(SpecLintCase):
    def test_heading_without_body_is_reported(self):
        """Заголовок без содержания выглядит как заполненный раздел."""
        code, data, _ = self.lint(spec(OUTCOME, "## Как проверить\n\n", SCOPE))
        self.assertEqual(code, 1)
        self.assertIn("section.empty", self.codes(data))

    def test_dash_placeholder_is_still_empty(self):
        code, data, _ = self.lint(spec(OUTCOME, ACCEPT_ACTION,
                                       "## Чего НЕ делаем\n—\n"))
        self.assertEqual(code, 1)
        empty = [p for p in data["problems"] if p["code"] == "section.empty"]
        self.assertEqual([p["section"] for p in empty], ["scope"])


class TestAcceptanceIsCheckable(SpecLintCase):
    """Сердце инструмента: критерий приёмки обязан быть проверяемым."""

    def test_vacuous_wordings_fail_the_gate(self):
        for wording in ("Работает.",
                        "Должно работать.",
                        "Выглядит правильно.",
                        "Готово.",
                        "Всё ок."):
            with self.subTest(формулировка=wording):
                accept = f"## Как проверить\n{wording}\n"
                code, data, _ = self.lint(spec(OUTCOME, accept, SCOPE))
                self.assertEqual(code, 1, f"«{wording}» прошло как приёмка: {data}")
                self.assertIn("accept.no_check", self.codes(data))

    def test_vacuous_phrase_is_named_to_the_human(self):
        """Замечание обязано называть виновника, иначе его нечем закрыть.

        ЧЕСТНО О СИЛЕ ТЕСТА: поведенческая часть здесь — код замечания
        «accept.vacuous» и код возврата 1. Проверка слова в тексте сообщения —
        это подстрока в прозе, она держит формулировку, а не поведение, и
        поведенческим доказательством не является.
        """
        accept = "## Как проверить\nДолжно работать.\n"
        code, data, _ = self.lint(spec(OUTCOME, accept, SCOPE))
        self.assertEqual(code, 1)
        named = [p for p in data["problems"] if p["code"] == "accept.vacuous"]
        self.assertTrue(named, f"пустая формулировка не названа: {data['problems']}")
        self.assertIn("работать", named[0]["message"])

    def test_action_without_expected_result_is_not_a_check(self):
        """Шаг без ожидаемого результата проверкой не является: по нему
        нельзя сказать «сошлось» или «не сошлось»."""
        accept = "## Как проверить\nОткрыть страницу входа и попробовать войти.\n"
        code, data, _ = self.lint(spec(OUTCOME, accept, SCOPE))
        self.assertEqual(code, 1, f"шаг без результата прошёл: {data['problems']}")
        self.assertIn("accept.no_check", self.codes(data))

    def test_inline_command_counts_as_evidence(self):
        accept = "## Как проверить\nПрогнать `npm test` — код возврата 0.\n"
        code, data, _ = self.lint(spec(OUTCOME, accept, SCOPE))
        self.assertEqual(code, 0, f"команда не засчитана: {data['problems']}")
        self.assertEqual(data["sections"]["accept"]["claim"], "command")

    def test_broken_acceptance_does_not_hide_missing_scope(self):
        """Замечания не глушат друг друга: обе поломки видны за один прогон."""
        code, data, _ = self.lint(spec(OUTCOME, "## Как проверить\nРаботает.\n"))
        self.assertEqual(code, 1)
        self.assertIn("accept.no_check", self.codes(data))
        self.assertIn("scope", [p["section"] for p in data["problems"]
                                if p["code"] == "section.missing"])


class TestUncheckedIsNotClean(SpecLintCase):
    """«Не смог проверить» обязано отличаться от «замечаний нет»."""

    def test_undecodable_file_gives_code_2(self):
        path = self.write(b"\xff\xfe\x00\x00# \xd0\xa1\xd0\xbf", name="broken.md")
        r = self.run_tool(str(path))
        self.assertEqual(r.returncode, 2,
                         f"нечитаемая спека дала код {r.returncode}")
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "unchecked")
        self.assertTrue(data.get("reason"), "причина отказа не названа")
        self.assertEqual(data["problems"], [])


class TestCallErrors(SpecLintCase):
    def test_no_argument_is_call_error(self):
        r = self.run_tool()
        self.assertEqual(r.returncode, 3)

    def test_missing_path_is_call_error(self):
        r = self.run_tool(str(self.tmp / "нет-такого.md"))
        self.assertEqual(r.returncode, 3)

    def test_directory_instead_of_file_is_call_error(self):
        r = self.run_tool(str(self.tmp))
        self.assertEqual(r.returncode, 3)

    def test_two_paths_is_call_error(self):
        a = self.write(spec(OUTCOME, ACCEPT_ACTION, SCOPE), name="a.md")
        b = self.write(spec(OUTCOME, ACCEPT_ACTION, SCOPE), name="b.md")
        r = self.run_tool(str(a), str(b))
        self.assertEqual(r.returncode, 3)


class TestOutputContract(SpecLintCase):
    """Машина читает stdout, человек — stderr. Их нельзя смешивать."""

    def test_json_flag_keeps_stderr_silent(self):
        path = self.write(spec(OUTCOME, ACCEPT_ACTION, SCOPE))
        r = self.run_tool("--json", str(path))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "", f"с --json в stderr попало: {r.stderr!r}")
        self.assertEqual(json.loads(r.stdout)["status"], "clean")

    def test_human_text_goes_to_stderr(self):
        """Поведенческое здесь — РАЗДЕЛЕНИЕ потоков: JSON в stdout, текст в
        stderr. Совпадение слов в человеческом тексте — подстрока в прозе и
        держит только формулировку."""
        path = self.write(spec(OUTCOME, "## Как проверить\nРаботает.\n", SCOPE))
        r = self.run_tool(str(path))
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.strip(), "человеку не сказано ничего")
        json.loads(r.stdout)          # stdout остаётся машиночитаемым
        self.assertIn("Как проверить", r.stderr)

    def test_tool_does_not_touch_the_spec(self):
        """Линт ничего не правит: спека — решение человека, а не поле автозамены."""
        path = self.write(spec(OUTCOME, "## Как проверить\nРаботает.\n", SCOPE))
        before = path.read_bytes()
        stat_before = path.stat().st_mtime_ns
        self.run_tool(str(path))
        self.assertEqual(path.read_bytes(), before, "файл спеки изменён")
        self.assertEqual(path.stat().st_mtime_ns, stat_before,
                         "у файла спеки поехало время правки")


class TestPauseIsObeyed(SpecLintCase):
    def test_pause_flag_stops_the_tool(self):
        """Тормоз соблюдается, а не попадает в отчёт: код 10 и ничего в stdout."""
        flag = self.home / ".claude" / "superstack" / "PAUSE"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("2026-08-09\n", encoding="utf-8")
        path = self.write(spec(OUTCOME, ACCEPT_ACTION, SCOPE))
        r = self.run_tool(str(path), ignore_pause=None)
        self.assertEqual(r.returncode, 10, f"пауза не сработала: {r.stderr[:500]}")
        self.assertEqual(r.stdout.strip(), "")


class TestFormatTolerance(SpecLintCase):
    """Оформление заголовка — не содержание: спека не должна падать из-за него."""

    def test_numbered_bold_headings_are_recognized(self):
        text = ("# Спека\n\n"
                "1. **Что должно получиться** — форма входа с понятной ошибкой.\n"
                "2. **Как проверить** — открыть /login, ввести неверный пароль, "
                "увидеть ошибку.\n"
                "3. **Чего НЕ делаем** — восстановление пароля не трогаем.\n")
        code, data, _ = self.lint(text)
        self.assertEqual(code, 0, f"нумерованный формат не разобран: {data}")

    def test_yo_and_colon_in_heading_do_not_break_matching(self):
        text = ("# Спека\n\n"
                "## Что должно получиться:\nФорма входа.\n\n"
                "## Как проверить:\nОткрыть /login и увидеть ошибку.\n\n"
                "## Чего НЕ делаем:\nРегистрацию не трогаем.\n")
        code, data, _ = self.lint(text)
        self.assertEqual(code, 0, f"двоеточие сломало сопоставление: {data}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
