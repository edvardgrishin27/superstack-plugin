#!/usr/bin/env python3
"""Тесты проверки скиллов (tools/skill_test.py).

Что эти тесты обязаны держать. Шлюз над скиллами ценен ровно одним: он ловит
обещание, которое скилл не выполняет. Поэтому у каждой проверки здесь стоит
ПАРА: сломанный скилл обязан покраснеть, целый — обязан пройти. Тест, который
проверяет только «целое остаётся целым», не держит ничего: с обезвреженной
проверкой он останется зелёным.

Как обеспечена повторяемость на другой машине:

  · скиллы СОБИРАЮТСЯ в temp-каталоге, а не берутся из репозитория, — иначе
    правка чужого SKILL.md красит мой набор;
  · корень (`--root`) подаётся параметром, автоопределение по хосту не
    используется;
  · бюджет подаётся параметром, а ожидаемое число считается из строк, которые
    написал сам тест, — не из константы инструмента (иначе это тавтология:
    код сверяют с ним же);
  · настоящий ~/.claude не читается; отдельный тест это доказывает, сравнивая
    два прогона с разными HOME;
  · синтаксис в блоках оболочки взят такой, который ломается одинаково в sh и
    в bash, — исход не зависит от того, какой интерпретатор нашёлся.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at, plug  # noqa: E402

ROOT = REPO
TOOL = at("tools", "skill_test.py")
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Регистрация до исполнения: dataclass ищет свой модуль в sys.modules.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load("ss_skill_test", TOOL)


# Описание с поводом. Вынесено в константу, чтобы тесты про ПУТИ и ОБОЛОЧКУ
# не краснели заодно из-за описания: одна проверка — один отказ.
GOOD_DESC = "Собрать релиз. Использовать, когда человек просит собрать сборку."


class SkillBed(unittest.TestCase):
    """Собранная машина: корень плагина, каталог скиллов, инструменты."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "plugin"
        (self.root / "tools").mkdir(parents=True)
        (self.root / "skills").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def skill(self, name: str, frontmatter: str, body: str = "") -> Path:
        d = self.root / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n",
                                    encoding="utf-8")
        return d

    def tool(self, rel: str, source: str) -> Path:
        f = self.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(source, encoding="utf-8")
        return f

    def run_tool(self, skill_dir: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(TOOL), str(skill_dir), "--json",
             "--root", str(self.root), *extra],
            capture_output=True, text=True, timeout=120, env=ENV)

    def verdict(self, skill_dir: Path, *extra: str) -> dict:
        r = self.run_tool(skill_dir, *extra)
        self.assertNotIn("Traceback", r.stderr, "инструмент упал вместо вердикта")
        return json.loads(r.stdout)

    def state_of(self, verdict: dict, check_id: str) -> str:
        for c in verdict["checks"]:
            if c["check"] == check_id:
                return c["state"]
        self.fail(f"в вердикте нет проверки {check_id}")


class TestFrontmatter(SkillBed):
    """Скилл без имени или с чужим именем не вызывается ничем."""

    def test_name_mismatch_is_a_finding(self):
        d = self.skill("alpha", f"name: beta\ndescription: {GOOD_DESC}")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "frontmatter"), "fail")
        self.assertEqual(v["status"], "findings")

    def test_matching_name_passes(self):
        """Обратный контроль: шлюз, который бракует всё, бесполезен так же,
        как шлюз, который пропускает всё."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}")
        self.assertEqual(self.state_of(self.verdict(d), "frontmatter"), "pass")

    def test_empty_description_is_a_finding(self):
        d = self.skill("alpha", "name: alpha\ndescription:")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "frontmatter"), "fail")

    def test_missing_frontmatter_is_a_finding(self):
        d = self.root / "skills" / "alpha"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("# просто заголовок\n", encoding="utf-8")
        self.assertEqual(self.state_of(self.verdict(d), "frontmatter"), "fail")

    def test_missing_skill_file_is_a_finding_not_a_crash(self):
        d = self.root / "skills" / "alpha"
        d.mkdir(parents=True)
        r = self.run_tool(d)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_multiline_description_is_read_whole(self):
        """Описание в несколько строк — норма. Обрезка по первой строке молча
        теряет как раз тот кусок, где написано, когда брать скилл."""
        d = self.skill("alpha",
                       "name: alpha\ndescription: Собрать релиз.\n"
                       "  Использовать, когда человек просит сборку.\n"
                       "argument-hint: \"<что>\"")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "when"), "pass")
        self.assertGreater(v["listing_chars"], 50,
                           "описание прочитано только до перевода строки")


class TestWhenIsHonestlyLabelled(SkillBed):
    """Проверка «повода» — ЭВРИСТИКА ПО ПРОЗЕ, и она обязана называть себя так.

    Поведенческим этот тест не является и не выдаётся за поведенческий: он
    сверяет реакцию на подстроку. Ценность в другом — в том, что вердикт
    помечает такую находку как вывод, а не как измерение.
    """

    def test_description_without_a_trigger_is_flagged(self):
        d = self.skill("alpha", "name: alpha\n"
                                "description: Умеет собирать релизы и писать заметки.")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "when"), "fail")

    def test_description_with_a_trigger_passes(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}")
        self.assertEqual(self.state_of(self.verdict(d), "when"), "pass")

    def test_verdict_marks_the_check_as_inference(self):
        """Если пометка исчезнет, вывод эвристики станет неотличим от замера —
        и человек поверит ему как измерению."""
        d = self.skill("alpha", "name: alpha\ndescription: Умеет собирать релизы.")
        v = self.verdict(d)
        block = next(c for c in v["checks"] if c["check"] == "when")
        self.assertIn("basis", block, "эвристика выдана за измерение")


class TestListingBudget(SkillBed):
    """Описание сверх бюджета обрезается молча — отказ без сообщения об ошибке.

    Ожидаемые числа считаются из строк, которые пишет сам тест, и бюджет
    подаётся параметром: константа инструмента в сравнении не участвует.
    """

    def _bed(self, desc_len: int):
        name = "alpha"
        desc = "Использовать, когда просят: " + "я" * desc_len
        d = self.skill(name, f"name: {name}\ndescription: {desc}")
        return d, len(name) + len(desc)

    def test_over_budget_is_a_finding(self):
        d, cost = self._bed(60)
        v = self.verdict(d, "--budget", str(cost - 1))
        self.assertEqual(self.state_of(v, "budget"), "fail")
        self.assertEqual(v["listing_chars"], cost,
                         "стоимость листинга посчитана не как name+description")

    def test_exactly_at_budget_passes(self):
        """Граница включительна: ровно бюджет ещё влезает. Сдвиг на единицу —
        это уже другой инструмент, и он врёт на каждом пограничном скилле."""
        d, cost = self._bed(60)
        self.assertEqual(self.state_of(self.verdict(d, "--budget", str(cost)),
                                       "budget"), "pass")

    def test_one_char_over_is_already_a_finding(self):
        d, cost = self._bed(60)
        v = self.verdict(d, "--budget", str(cost - 1))
        self.assertIn(str(cost), " ".join(v["problems"]),
                      "замечание не называет фактическую стоимость")


class TestToolPaths(SkillBed):
    """Путь, названный в теле, обязан существовать."""

    def test_missing_tool_is_a_finding(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "Запусти `tools/gone.py`.")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "paths"), "fail")
        self.assertIn("tools/gone.py", " ".join(v["problems"]))

    def test_existing_tool_passes(self):
        self.tool("tools/real.py", "print(1)\n")
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "Запусти `tools/real.py`.")
        self.assertEqual(self.state_of(self.verdict(d), "paths"), "pass")

    def test_plugin_root_variable_is_resolved_against_root(self):
        self.tool("tools/real.py", "print(1)\n")
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       'Запусти `"$CLAUDE_PLUGIN_ROOT/tools/real.py"`.')
        self.assertEqual(self.state_of(self.verdict(d), "paths"), "pass")

    def test_plugin_root_variable_still_catches_a_missing_file(self):
        """Подстановка корня не должна превращаться в амнистию: путь от корня
        проверяем так же строго, как относительный."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       'Запусти `"$CLAUDE_PLUGIN_ROOT/tools/gone.py"`.')
        self.assertEqual(self.state_of(self.verdict(d), "paths"), "fail")

    def test_file_inside_the_skill_directory_is_checked(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "Смотри [шаблон](tools/template.md).")
        self.assertEqual(self.state_of(self.verdict(d), "paths"), "fail")
        (d / "tools").mkdir()
        (d / "tools" / "template.md").write_text("шаблон\n", encoding="utf-8")
        self.assertEqual(self.state_of(self.verdict(d), "paths"), "pass")

    def test_shell_substitution_is_unverified_not_missing(self):
        """«Не смог проверить» и «не нашёл» — разные утверждения. Путь с
        подстановкой оболочки нельзя объявлять несуществующим: инструмент
        начнёт врать про рабочие скиллы, и на него перестанут смотреть."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 x.py \"$W/facts.json\"\n```")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "paths"), "unknown")
        self.assertEqual(v["problems"], [], "непроверенное подано как находка")
        self.assertTrue(any("$W/facts.json" in u for u in v["unverified"]),
                        "непроверенное место не названо")

    def test_unverified_path_lowers_the_exit_code(self):
        """Непроверенное обязано гасить доверие, а не молча растворяться."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "Смотри `~/.claude/settings.json`.")
        r = self.run_tool(d)
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertEqual(json.loads(r.stdout)["status"], "unknown")

    def test_output_redirect_target_is_not_demanded(self):
        """`> отчёт.md` — то, что скилл создаёт. Требовать его заранее значит
        объявлять дефектом нормальную запись результата."""
        self.tool("tools/real.py", "print(1)\n")
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/real.py > out/report.json\n```")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "paths"), "pass", v["problems"])

    def test_urls_are_not_treated_as_files(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "Документация: https://example.com/docs/guide.md")
        self.assertEqual(self.state_of(self.verdict(d), "paths"), "pass")


class TestShellBlocks(SkillBed):
    """Опечатка в команде внутри скилла молчит до первого живого запуска."""

    # Незакрытый `if` — синтаксическая ошибка и в sh, и в bash: исход теста
    # не зависит от того, какой интерпретатор нашёлся на машине.
    BROKEN = "```bash\nif [ 1 -eq 1 ]; then\n  echo да\n```"
    SOUND = "```bash\nif [ 1 -eq 1 ]; then\n  echo да\nfi\n```"

    def test_broken_block_is_a_finding(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}", self.BROKEN)
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "shell"), "fail")
        self.assertIn("блок 1", " ".join(v["problems"]),
                      "замечание не показывает, какой именно блок сломан")

    def test_sound_block_passes(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}", self.SOUND)
        self.assertEqual(self.state_of(self.verdict(d), "shell"), "pass")

    def test_second_block_is_checked_too(self):
        """Проверка, которая смотрит только первый блок, пропускает всё
        остальное — а сломанное обычно не в начале."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       self.SOUND + "\n\nтекст\n\n" + self.BROKEN)
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "shell"), "fail")
        self.assertIn("блок 2", " ".join(v["problems"]))

    def test_no_blocks_is_not_a_finding(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "Скилл без единой команды.")
        self.assertEqual(self.state_of(self.verdict(d), "shell"), "pass")

    def test_non_shell_fence_is_left_alone(self):
        """Блок на другом языке через оболочку не разбирается: иначе валидный
        Python объявляется опечаткой в командах."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```python\nif True:\n    x = [1, 2\n```")
        self.assertEqual(self.state_of(self.verdict(d), "shell"), "pass")

    def test_error_message_carries_no_temp_path(self):
        """Имя временного файла в замечании делает один и тот же дефект разным
        на разных машинах — сравнивать прогоны становится нечем."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}", self.BROKEN)
        v = self.verdict(d)
        self.assertNotIn(tempfile.gettempdir(), " ".join(v["problems"]))


# Инструменты-подделки для сверки кодов возврата. Ожидание в тесте берётся
# отсюда — из текста, который написал тест, а не из разбора, который делает
# проверяемый код.
TOOL_WITH_CODES = (
    "import sys\n"
    "EXIT = {'ok': 0, 'bad': 1}\n"
    "def main():\n"
    "    if len(sys.argv) > 9:\n"
    "        return 3\n"
    "    return EXIT.get('ok', 1)\n"
    "sys.exit(main())\n"
)
TOOL_WITH_UNREADABLE_RETURN = (
    "import sys\n"
    "def choose():\n"
    "    return 7\n"
    "def main():\n"
    "    return choose()\n"
    "sys.exit(main())\n"
)


def codes_table(rows: list) -> str:
    lines = ["| код | что это значит |", "|---|---|"]
    lines += [f"| {c} | пояснение |" for c in rows]
    return "\n".join(lines)


class TestDeclaredExitCodes(SkillBed):
    """Таблица кодов в скилле — обещание. Обещание сверяется с инструментом."""

    def test_code_the_tool_never_returns_is_a_finding(self):
        self.tool("tools/cli.py", TOOL_WITH_CODES)
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/cli.py\n```\n\n"
                       + codes_table([0, 1, 3, 7]))
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "exit_codes"), "fail")
        self.assertIn("7", " ".join(v["problems"]),
                      "не назван код, которого инструмент не отдаёт")

    def test_table_matching_the_tool_passes(self):
        self.tool("tools/cli.py", TOOL_WITH_CODES)
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/cli.py\n```\n\n"
                       + codes_table([0, 1, 3]))
        self.assertEqual(self.state_of(self.verdict(d), "exit_codes"), "pass")

    def test_unreadable_return_gives_unknown_not_a_finding(self):
        """Если сверить нечем — так и сказано. Инструмент, чей возврат не
        разобран, не даёт права объявлять честную таблицу враньём."""
        self.tool("tools/cli.py", TOOL_WITH_UNREADABLE_RETURN)
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/cli.py\n```\n\n"
                       + codes_table([0, 1]))
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "exit_codes"), "unknown")
        self.assertEqual(v["problems"], [])
        self.assertTrue(v["unverified"], "не смог — но об этом никто не узнал")

    def test_table_without_a_named_tool_is_unknown(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       codes_table([0, 1]))
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "exit_codes"), "unknown")

    def test_ordinary_table_is_not_read_as_codes(self):
        """Таблица без колонки кодов не должна попадать под эту проверку:
        ложная находка дороже пропущенной."""
        self.tool("tools/cli.py", TOOL_WITH_CODES)
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/cli.py\n```\n\n"
                       "| шаг | что делать |\n|---|---|\n| 7 | подумать |")
        v = self.verdict(d)
        self.assertEqual(self.state_of(v, "exit_codes"), "pass", v["unverified"])


class TestToolReadsSourceNotBehaviour(unittest.TestCase):
    """Коды снимаются разбором исходника: запускать чужой инструмент ради
    опроса кодов — побочный эффект, которого у проверки быть не должно."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_codes_are_collected_from_dict_and_literals(self):
        f = self.d / "cli.py"
        f.write_text(TOOL_WITH_CODES, encoding="utf-8")
        codes, why = st.tool_exit_codes(f)
        self.assertIsNone(why)
        self.assertEqual(codes, {0, 1, 3})

    def test_unresolved_return_is_reported(self):
        f = self.d / "cli.py"
        f.write_text(TOOL_WITH_UNREADABLE_RETURN, encoding="utf-8")
        codes, why = st.tool_exit_codes(f)
        self.assertIsNotNone(why, "неразобранный возврат выдан за полный разбор")

    def test_boolean_return_is_not_a_code(self):
        """`return True` — не код возврата 1. По типу True и есть единица, и
        без оговорки таблица, обещающая 1, подтверждалась бы совпадением
        типов, а не тем, что инструмент правда так завершается."""
        f = self.d / "cli.py"
        f.write_text("def main():\n    return True\n", encoding="utf-8")
        codes, _ = st.tool_exit_codes(f)
        self.assertNotIn(1, codes)

    def test_side_effects_of_the_examined_tool_never_happen(self):
        """Инструмент с записью на диск при разборе не исполняется."""
        marker = self.d / "исполнено.txt"
        f = self.d / "cli.py"
        f.write_text(f"open({str(marker)!r}, 'w').write('x')\n"
                     "def main():\n    return 0\n", encoding="utf-8")
        st.tool_exit_codes(f)
        self.assertFalse(marker.exists(), "проверяемый инструмент был запущен")


class TestContract(SkillBed):
    """Форма ответа — часть контракта: вердикт читает скрипт, а не человек."""

    def test_clean_skill_returns_zero(self):
        self.tool("tools/real.py", "print(1)\n")
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/real.py\n```")
        r = self.run_tool(d)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout)["status"], "clean")

    def test_finding_returns_one(self):
        d = self.skill("alpha", "name: beta\ndescription: Умеет всё.")
        self.assertEqual(self.run_tool(d).returncode, 1)

    def test_exit_codes_are_distinct(self):
        self.assertEqual(sorted(st.EXIT.values()), [0, 1, 2])

    def test_human_text_never_pollutes_the_json(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}")
        r = subprocess.run([sys.executable, str(TOOL), str(d),
                            "--root", str(self.root)],
                           capture_output=True, text=True, timeout=120, env=ENV)
        json.loads(r.stdout)      # упадёт, если человеческий текст ушёл в stdout
        self.assertIn("СКИЛЛ", r.stderr)

    def test_missing_directory_is_named_not_crashed(self):
        r = subprocess.run([sys.executable, str(TOOL), "/нет/такого/скилла"],
                           capture_output=True, text=True, timeout=60, env=ENV)
        self.assertEqual(r.returncode, 3)
        self.assertIn("НЕ УДАЛОСЬ", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_no_argument_is_a_call_error(self):
        r = subprocess.run([sys.executable, str(TOOL)],
                           capture_output=True, text=True, timeout=60, env=ENV)
        self.assertEqual(r.returncode, 3)
        self.assertIn("вызов:", r.stderr)

    def test_garbage_budget_is_a_call_error_not_a_verdict(self):
        """Кривой аргумент не должен превращаться в вердикт о скилле: иначе
        ошибка вызова читается как находка."""
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}")
        r = self.run_tool(d, "--budget", "много")
        self.assertEqual(r.returncode, 3, r.stdout)

    def test_verdict_carries_the_decision_fields(self):
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}")
        v = self.verdict(d)
        for key in ("gate", "skill", "status", "checks", "problems",
                    "unverified", "next"):
            self.assertIn(key, v)


class TestChangesNothing(SkillBed):
    """Проверяющий инструмент не правит проверяемое. Иначе шлюз становится
    источником изменений, которых никто не заказывал."""

    def _snapshot(self) -> dict:
        out = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(self.root))] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
        return out

    def test_nothing_on_disk_moves(self):
        self.tool("tools/real.py", "print(1)\n")
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/real.py\n```\n\n"
                       "Ещё `tools/gone.py`.")
        before = self._snapshot()
        self.run_tool(d)
        self.assertEqual(self._snapshot(), before)


class TestHermetic(SkillBed):
    """Вердикт описывает скилл, а не машину, на которой его читают."""

    def test_two_different_homes_give_the_same_verdict(self):
        """Если бы проверка заглядывала в настоящий ~/.claude, npm или сеть,
        два прогона с разными HOME разошлись бы."""
        self.tool("tools/real.py", "print(1)\n")
        d = self.skill("alpha", f"name: alpha\ndescription: {GOOD_DESC}",
                       "```bash\npython3 tools/real.py\n```\n\n"
                       "Смотри `~/.claude/settings.json`.")
        seen = []
        for home in ("home-один", "home-два"):
            h = Path(self.tmp.name) / home
            h.mkdir()
            r = subprocess.run(
                [sys.executable, str(TOOL), str(d), "--json",
                 "--root", str(self.root)],
                capture_output=True, text=True, timeout=120,
                env={**ENV, "HOME": str(h)})
            seen.append(r.stdout)
        self.assertEqual(seen[0], seen[1], "вердикт зависит от домашнего каталога")


class TestShippedSkillsAreParsable(unittest.TestCase):
    """Дымовой прогон по скиллам самого плагина.

    Намеренно НЕ утверждает, что скиллы чисты: их правят в соседнем круге, и
    привязка к их содержимому сделала бы мой набор ложно-красным. Держит
    ровно одно — на настоящем скилле инструмент даёт вердикт, а не стектрейс.
    """

    def test_every_shipped_skill_gets_a_verdict(self):
        skills = sorted(p for p in (plug("superstack-control") / "skills").iterdir() if p.is_dir())
        self.assertTrue(skills, "в плагине не нашлось ни одного скилла")
        for d in skills:
            with self.subTest(skill=d.name):
                r = subprocess.run([sys.executable, str(TOOL), str(d), "--json"],
                                   capture_output=True, text=True, timeout=120,
                                   env=ENV)
                self.assertIn(r.returncode, (0, 1, 2), r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertEqual(json.loads(r.stdout)["skill"], d.name)


if __name__ == "__main__":
    unittest.main()
