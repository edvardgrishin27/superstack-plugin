#!/usr/bin/env python3
"""Тесты графа проектных документов (`tools/artifact_graph.py`).

Что здесь держится. Граф существует ради трёх отказов, которые не видны при
чтении по одному файлу за раз: цикл в links, ссылка в никуда, спека без
единого реально существующего теста. Набор проверяет не «скрипт запустился»,
а ровно то, что скрипт обязан ловить.

Дисциплина, без которой эти тесты ничего не стоили бы:

  * дерево документов подаётся ФИКСТУРОЙ — файлы пишутся во временный
    каталог прямо здесь. Настоящий ~/.claude, сеть и текущий каталог не
    читаются; HOME подставляется, поэтому результат одинаков на другой машине;
  * ожидаемые коды возврата и коды проблем (`cycle`, `dangling-link`,
    `spec-without-test`) записаны буквально, а не взяты из самого инструмента:
    значение, полученное из проверяемого кода, доказывает лишь, что код
    равен себе;
  * есть обратный контроль на каждое правило — тот же граф с ОДНИМ убранным
    дефектом обязан остаться чистым (0), а не завалиться заодно с чем-то
    другим. Без этого «починку» можно было бы подделать, объявив
    подозрительным вообще всё;
  * для правила spec-without-test отдельно проверено: путь в поле `tests`,
    который не существует НА ДИСКЕ, не спасает спеку. Написанный путь и
    существующий файл — разные утверждения, и тест держит именно разницу
    между ними, а не только «поле пустое».
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at, plug  # noqa: E402

ROOT = REPO
TOOL = at("tools", "artifact_graph.py")


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


class GraphCase(unittest.TestCase):
    """Общая обвязка: временный каталог документов на каждый тест."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="superstac" "k-artifac" "t-graph-")
        self.tmp = Path(self._tmp.name)
        self.docs = self.tmp / "docs"
        self.docs.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel: str, text: str) -> Path:
        path = self.docs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_bytes(self, rel: str, data: bytes) -> Path:
        path = self.docs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_tool(self, *args: str, home: Path = None):
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True, text=True, timeout=60,
            cwd=str(self.tmp), env=_env(home or self.home))

    def validate(self, target: str = "docs"):
        """Прогон на подготовленном дереве: (код возврата, JSON, stderr)."""
        r = self.run_tool(target)
        try:
            data = json.loads(r.stdout)
        except ValueError as e:
            raise AssertionError(
                f"stdout не разбирается как JSON ({e}):\n{r.stdout[:2000]}\n"
                f"stderr:\n{r.stderr[:2000]}")
        return r.returncode, data, r.stderr

    def checks(self, data: dict) -> list:
        return [p["check"] for p in data["problems"]]


class TestToolExists(GraphCase):
    def test_tool_file_is_present(self):
        """Улика для карты плана: инструмент существует по объявленному пути."""
        self.assertTrue(TOOL.is_file(), f"нет файла инструмента: {TOOL}")

    def test_tool_declares_validate_function(self):
        """Анкер, который ищет карта плана: строка «def validate» обязана
        присутствовать буквально, а не подразумеваться другой функцией."""
        src = TOOL.read_text(encoding="utf-8")
        self.assertIn("def validate", src)


class TestCleanGraph(GraphCase):
    """Граф без дефектов обязан остаться чистым — иначе линт бесполезен:
    его выключат при первом ложном срабатывании."""

    def _build_clean_tree(self):
        self.write("spec.md", "---\n"
                   "type: spec\n"
                   "id: login-spec\n"
                   "links: plan\n"
                   "tests: tests/test_login.py\n"
                   "---\n# Спека\n")
        self.write("plan.md", "---\n"
                   "type: plan\n"
                   "id: plan\n"
                   "links: task.md\n"
                   "---\n# План\n")
        self.write("task.md", "---\ntype: task\n---\n# Задача\n")
        self.write("tests/test_login.py", "def test_login():\n    assert True\n")

    def test_clean_tree_is_clean(self):
        self._build_clean_tree()
        code, data, _ = self.validate()
        self.assertEqual(code, 0, f"чистое дерево дало код {code}: {data['problems']}")
        self.assertEqual(data["status"], "clean")
        self.assertEqual(data["problems"], [])
        # Узлы графа — это .md-документы (spec/plan/task), а не файлы тестов:
        # tests/test_login.py — .py, он проверяется через resolve_test_path,
        # но сам в граф не входит.
        self.assertEqual(data["nodes"], 3)

    def test_link_by_id_path_and_bare_basename_all_resolve(self):
        """Один и тот же документ достижим тремя способами написания ссылки:
        через явный id (`plan`), через basename с расширением (`task.md`) —
        оба использованы в фикстуре. Ложный dangling-link на любом из них
        сломал бы почти каждую реальную спеку в проекте."""
        self._build_clean_tree()
        _, data, _ = self.validate()
        self.assertNotIn("dangling-link", self.checks(data))

    def test_plan_and_task_without_tests_field_stay_clean(self):
        """Правило «без теста — не гейт» относится только к type: spec.
        План и задача без поля tests не обязаны иметь тестов вовсе."""
        self._build_clean_tree()
        _, data, _ = self.validate()
        self.assertNotIn("spec-without-test", self.checks(data))


class TestCycles(GraphCase):
    def test_two_node_cycle_is_caught(self):
        self.write("a.md", "---\ntype: plan\nid: a\nlinks: b\n---\n")
        self.write("b.md", "---\ntype: plan\nid: b\nlinks: a\n---\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 1, f"цикл не дал код 1: {data}")
        self.assertEqual(data["status"], "problems")
        self.assertIn("cycle", self.checks(data))

    def test_self_link_is_a_cycle(self):
        """Документ, ссылающийся сам на себя, — вырожденный цикл длины один,
        и он обязан ловиться тем же правилом, что и цикл из нескольких узлов."""
        self.write("a.md", "---\ntype: plan\nid: a\nlinks: a\n---\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 1)
        self.assertIn("cycle", self.checks(data))

    def test_three_node_cycle_reports_the_loop(self):
        self.write("a.md", "---\ntype: plan\nid: a\nlinks: b\n---\n")
        self.write("b.md", "---\ntype: plan\nid: b\nlinks: c\n---\n")
        self.write("c.md", "---\ntype: plan\nid: c\nlinks: a\n---\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 1)
        cyc = next(p for p in data["problems"] if p["check"] == "cycle")
        # Все три узла обязаны быть названы в описании цикла — иначе человек
        # не знает, что разрывать.
        for node in ("a.md", "b.md", "c.md"):
            self.assertIn(node, cyc["detail"])

    def test_removing_the_back_edge_clears_the_cycle(self):
        """Обратный контроль: тот же граф, но без ребра, замыкающего цикл,
        обязан пройти чисто. Если бы это было не так, «цикл» ловил бы любую
        цепочку ссылок, а не именно петлю."""
        self.write("a.md", "---\ntype: plan\nid: a\nlinks: b\n---\n")
        self.write("b.md", "---\ntype: plan\nid: b\n---\n")  # без links: a
        code, data, _ = self.validate()
        self.assertEqual(code, 0, f"цепочка без цикла дала проблемы: {data['problems']}")
        self.assertNotIn("cycle", self.checks(data))


class TestDanglingLinks(GraphCase):
    def test_link_to_nowhere_is_caught(self):
        self.write("a.md", "---\ntype: plan\nlinks: nowhere\n---\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 1, f"битая ссылка не дала код 1: {data}")
        self.assertIn("dangling-link", self.checks(data))

    def test_multiple_targets_only_the_broken_one_is_flagged(self):
        """Список из нескольких целей: рабочая ссылка не обязана тянуть за
        собой замечание по соседней битой — иначе один дефект в links «съел»
        бы диагностику остальных элементов того же поля."""
        self.write("a.md", "---\ntype: plan\nlinks: b, nowhere\n---\n")
        self.write("b.md", "---\ntype: plan\n---\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 1)
        findings = [p for p in data["problems"] if p["check"] == "dangling-link"]
        self.assertEqual(len(findings), 1)
        self.assertIn("nowhere", findings[0]["detail"])

    def test_fixing_the_target_clears_the_finding(self):
        """Обратный контроль: как только цель существует, замечание уходит."""
        self.write("a.md", "---\ntype: plan\nlinks: b\n---\n")
        self.write("b.md", "---\ntype: plan\n---\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 0, f"валидная ссылка дала проблемы: {data['problems']}")
        self.assertNotIn("dangling-link", self.checks(data))


class TestSpecWithoutTest(GraphCase):
    def test_spec_without_tests_field_is_caught(self):
        self.write("spec.md", "---\ntype: spec\n---\n# Спека\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 1, f"спека без tests не дала код 1: {data}")
        self.assertIn("spec-without-test", self.checks(data))

    def test_spec_with_nonexistent_test_path_is_caught(self):
        """Путь в tests, который не существует на диске, — не доказательство.
        Это ключевая проверка домена: написанный путь и реальный файл — не
        одно и то же."""
        self.write("spec.md",
                   "---\ntype: spec\ntests: tests/test_missing.py\n---\n# Спека\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 1, f"несуществующий путь не дал код 1: {data}")
        finding = next(p for p in data["problems"] if p["check"] == "spec-without-test")
        self.assertIn("test_missing.py", finding["detail"])

    def test_spec_with_real_test_file_is_clean(self):
        """Обратный контроль: как только файл теста реально появляется на
        диске по заявленному пути, спека перестаёт быть проблемой."""
        self.write("spec.md",
                   "---\ntype: spec\ntests: tests/test_ok.py\n---\n# Спека\n")
        self.write("tests/test_ok.py", "def test_ok():\n    assert True\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 0, f"спека с реальным тестом дала проблемы: {data['problems']}")
        self.assertNotIn("spec-without-test", self.checks(data))

    def test_plan_type_is_exempt_from_the_rule(self):
        """Правило применяется буквально к type: spec, не к любому документу."""
        self.write("plan.md", "---\ntype: plan\n---\n# План без тестов\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 0)
        self.assertNotIn("spec-without-test", self.checks(data))


class TestAbsentAndUnchecked(GraphCase):
    def test_empty_directory_is_absent_not_clean(self):
        """Пустой каталог — отдельный код, не совпадающий ни с чистым (0),
        ни с проблемным (1) прогоном."""
        code, data, _ = self.validate()
        self.assertEqual(code, 4, f"пустой каталог дал код {code}, а не 4: {data}")
        self.assertEqual(data["status"], "absent")

    def test_absent_code_differs_from_clean_and_problems(self):
        self.write("a.md", "---\ntype: plan\n---\n")
        clean_code, _, _ = self.validate()
        self.write("b.md", "---\ntype: plan\nlinks: nowhere\n---\n")
        problems_code, _, _ = self.validate()
        empty_dir = self.tmp / "empty"
        empty_dir.mkdir()
        r = self.run_tool("empty")
        absent_data = json.loads(r.stdout)
        self.assertEqual({clean_code, problems_code, r.returncode}, {0, 1, 4})
        self.assertEqual(absent_data["status"], "absent")

    def test_undecodable_file_is_named_not_dropped(self):
        """Файл, который не читается как UTF-8, обязан попасть в unchecked
        по имени — «не смог прочитать» не имеет права выглядеть как «чисто»."""
        self.write_bytes("bad.md", b"\xff\xfe not utf-8 at all")
        self.write("ok.md", "---\ntype: task\n---\n# ок\n")
        code, data, _ = self.validate()
        self.assertEqual(code, 2, f"нечитаемый файл дал код {code}, а не 2: {data}")
        self.assertEqual(data["status"], "unchecked")
        names = [u["file"] for u in data["unchecked"]]
        self.assertIn("bad.md", names)

    def test_unchecked_file_does_not_silently_pass_as_clean(self):
        self.write_bytes("bad.md", b"\xff\xfe")
        code, data, _ = self.validate()
        self.assertNotEqual(data["status"], "clean")
        self.assertNotEqual(code, 0)


class TestInvocation(GraphCase):
    def test_missing_directory_is_invocation_error(self):
        r = self.run_tool("does-not-exist")
        self.assertEqual(r.returncode, 3)

    def test_too_many_arguments_is_invocation_error(self):
        r = self.run_tool("docs", "extra")
        self.assertEqual(r.returncode, 3)

    def test_paused_flag_halts_with_code_ten(self):
        """Тормоз-файл обязан остановить граф ДО чтения дерева, тем же кодом,
        что и остальные инструменты SUPERSTACK."""
        self.write("a.md", "---\ntype: plan\n---\n")
        pause_dir = self.home / ".claude" / "superstack"
        pause_dir.mkdir(parents=True)
        (pause_dir / "PAUSE").write_text("2026-01-01", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(TOOL), "docs"],
            capture_output=True, text=True, timeout=60,
            cwd=str(self.tmp), env=_env(self.home, ignore_pause=None))
        self.assertEqual(r.returncode, 10, f"пауза не остановила прогон: {r.stderr}")


if __name__ == "__main__":
    unittest.main()
