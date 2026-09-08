#!/usr/bin/env python3
"""Снимок не уносит чужой индекс и не берёт в себя новый ключ.

Два сценария пришли из внешней проверки и переписаны здесь регрессионными
тестами ПРАВИЛЬНОГО поведения.

ПЕРВЫЙ. Снимок снимался из подпапки продукта, лежащей внутри хранилища
человека. `git status --porcelain` печатает пути от КОРНЯ хранилища, а не от
папки, где его позвали, — и эти пути присоединялись к самой папке. Выходило
`product/product/.env`: файла по такому адресу нет, проверка на ключи молча
пропускала его, и снимок отчитывался «ключей нет», забрав ключ внутрь. Второй
половиной того же сценария `git add` ограничивался областью, а `git commit`
фиксировал ВЕСЬ индекс — и в снимок уезжал соседний файл, который человек
подготовил к своему коммиту, а его индекс оставался пустым.

Цена обеих половин — не точность отчёта, а данные человека: ключ, попавший в
снимок, оттуда уже не убрать, а подготовленная работа уходит в чужую историю.

ВТОРОЙ. Про файл окружения спрашивалось «попадал ли он в снимки раньше», а
решение принималось про ЗНАЧЕНИЕ. Отслеживаемость файла не доказывает, что
именно ЭТОТ ключ уже в истории: `.env` с безобидным `MODE=development` живёт в
снимках месяцами, в него ВПЕРВЫЕ дописывают токен — и токен уезжает в снимок,
а человеку говорят «ключи здесь попадали в снимки и раньше». Утверждение про
прошлое, которого никто не проверял.

Что здесь заперто:
  · снимок из подпапки видит ключ в своём файле окружения и не берёт файл;
  · соседний подготовленный файл не попадает в снимок, а индекс человека
    остаётся в том же состоянии относительно снимка, что и до;
  · записи «намерен добавить» (`git add -N`) переживают снимок и не
    превращаются в чужой коммит;
  · первый ключ в давно отслеживаемом файле ОСТАНАВЛИВАЕТ снимок, а рабочий
    файл и прежние снимки остаются нетронутыми;
  · строка про «попадали и раньше» звучит только тогда, когда это проверено;
  · имя файла с кириллицей и пробелом доезжает до снимка целым;
  · обратный контроль: обычный снимок из корня работает как прежде.

Git настоящий, во временной папке и со своими настройками: заранее собранный
вывод доказывал бы лишь то, что автор теста и автор разбора одинаково его себе
представляют.

Ключей в фикстурах нет. Там, где нужна находка, лежит выдуманное значение,
которое читается как «пароль-из-теста»: этого достаточно, чтобы сборщик фактов
его опознал, и недостаточно, чтобы принять за чей-то настоящий.

Тест самодостаточен: только стандартная библиотека и сам инструмент.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

РЕПО = Path(__file__).resolve().parents[2]
ИНСТРУМЕНТ = РЕПО / "plugins" / "superstack" / "tools" / "checkpoint.py"

#: Выдуманное значение, которое сборщик фактов опознаёт по имени поля и форме.
#: На настоящий ключ не похоже нарочно — оно читается словами.
ВЫДУМКА = "ADMIN_PASSWORD=пароль-из-теста!7"
ВТОРАЯ_ВЫДУМКА = "ADMIN_PASSWORD=другой-пароль-из-теста!9"
БЕЗОБИДНОЕ = "MODE=development"

#: Git слушается трёх файлов настроек сразу, и на чужой машине любой из них
#: может оказаться с уже отключённым кодированием имён или с чужим автором —
#: тогда проверялся бы не тот случай, ради которого всё написано.
СРЕДА = {**os.environ,
         "GIT_CONFIG_GLOBAL": os.devnull,
         "GIT_CONFIG_SYSTEM": os.devnull,
         "GIT_CONFIG_NOSYSTEM": "1",
         "GIT_AUTHOR_NAME": "тест", "GIT_AUTHOR_EMAIL": "т@т",
         "GIT_COMMITTER_NAME": "тест", "GIT_COMMITTER_EMAIL": "т@т",
         "GIT_TERMINAL_PROMPT": "0",
         "SUPERSTACK_IGNORE_PAUSE": "1"}


@unittest.skipUnless(shutil.which("git"), "git не установлен — проверять нечем")
class НастоящийGit(unittest.TestCase):
    """Хранилище во временной папке. Никаких заранее собранных строк."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)
        self.git("init", "-q", "-b", "основная")

    # ---- git ----------------------------------------------------------
    def git(self, *арг: str, где: Path = None) -> str:
        p = subprocess.run(["git", *арг], cwd=str(где or self.корень),
                           env=СРЕДА, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, f"git {' '.join(арг)}: {p.stderr}")
        return p.stdout

    def файл(self, имя: str, текст: str) -> Path:
        p = self.корень / имя
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(текст, encoding="utf-8")
        return p

    def первый_коммит(self, файлы: dict) -> None:
        for имя, текст in файлы.items():
            self.файл(имя, текст)
        self.git("add", "-A")
        self.git("commit", "-qm", "начало")

    def в_снимке(self, ссылка: str = "HEAD") -> list:
        """Что лежит в снимке — путями от корня хранилища."""
        out = self.git("ls-tree", "-r", "--name-only", "-z", ссылка)
        return sorted(x for x in out.split("\0") if x)

    def индекс_против_снимка(self) -> str:
        """Что человек подготовил сам — его состояние индекса относительно HEAD."""
        return self.git("diff", "--cached", "--name-status")

    def запись_индекса(self, путь: str) -> str:
        return self.git("ls-files", "--stage", "--", путь)

    def голова(self) -> str:
        p = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"],
                           cwd=str(self.корень), env=СРЕДА,
                           capture_output=True, text=True, timeout=120)
        return (p.stdout or "").strip()

    # ---- инструмент ---------------------------------------------------
    def снимок(self, папка: Path, *арг: str) -> tuple:
        p = subprocess.run([sys.executable, str(ИНСТРУМЕНТ), str(папка),
                            *арг, "--json"], env=СРЕДА,
                           capture_output=True, text=True, timeout=180)
        return p.returncode, json.loads(p.stdout)


class ЧужойИндексИКлючВПодпапке(НастоящийGit):
    """Первый сценарий внешней проверки, дословно."""

    def setUp(self):
        super().setUp()
        self.первый_коммит({"readme.txt": "начало\n"})
        # Человек подготовил СВОЙ файл к СВОЕМУ коммиту — рядом, но не у нас.
        self.файл("other.txt", "моя работа\n")
        self.git("add", "other.txt")
        self.индекс_до = self.индекс_против_снимка()
        self.запись_до = self.запись_индекса("other.txt")
        # Наша работа — в подпапке продукта.
        self.файл("product/app.txt", "код\n")
        self.файл("product/.env", ВЫДУМКА + "\n")
        self.код, self.v = self.снимок(self.корень / "product", "snap", "этап")

    def test_снимок_состоялся(self):
        """Позитивный контроль: строгость, которую нельзя пройти, — не
        строгость. Работа в папке есть, снимок обязан сняться."""
        self.assertEqual(self.код, 0, self.v)
        self.assertTrue(self.v["снято"], self.v)

    def test_ключ_в_подпапке_замечен(self):
        имена = [н["файл"] for н in self.v.get("ключи", [])]
        self.assertEqual(имена, [".env"],
                         f"ключ в подпапке не увиден: {self.v}")

    def test_файл_с_ключом_в_снимок_не_взят(self):
        self.assertNotIn("product/.env", self.в_снимке(),
                         "ключ уехал в снимок, откуда его уже не убрать")

    def test_работа_в_снимок_взята(self):
        self.assertIn("product/app.txt", self.в_снимке(), self.v)

    def test_чужой_подготовленный_файл_не_уехал_в_снимок(self):
        self.assertNotIn("other.txt", self.в_снимке(),
                         "снимок унёс в свою историю чужую подготовленную работу")

    def test_индекс_человека_остался_прежним(self):
        self.assertEqual(self.индекс_против_снимка(), self.индекс_до,
                         "состояние подготовленного человеком изменилось")
        self.assertEqual(self.запись_индекса("other.txt"), self.запись_до,
                         "запись в индексе человека подменена")

    def test_рабочий_файл_с_ключом_остался_на_месте(self):
        self.assertEqual((self.корень / "product" / ".env").read_text("utf-8"),
                         ВЫДУМКА + "\n",
                         "рабочий файл человека переписан автоматически")


class ЗаписиНамеренДобавить(НастоящийGit):
    """`git add -N .` фазы приёмки: снимок обязан их пережить.

    Записи «намерен добавить» ставит сама сборка — иначе новые файлы не видны
    в изменениях. Снимок не имеет права ни потерять их, ни превратить в чужой
    коммит.
    """

    def setUp(self):
        super().setUp()
        self.первый_коммит({"readme.txt": "начало\n"})
        self.файл("other.txt", "моя работа\n")
        self.файл("product/app.txt", "код\n")
        self.git("add", "-N", ".")
        self.статус_до = self.git("status", "--porcelain", "--", "other.txt")
        self.код, self.v = self.снимок(self.корень / "product", "snap", "этап")

    def test_снимок_состоялся(self):
        self.assertEqual(self.код, 0, self.v)
        self.assertTrue(self.v["снято"], self.v)

    def test_запись_вне_области_цела(self):
        self.assertEqual(self.git("status", "--porcelain", "--", "other.txt"),
                         self.статус_до,
                         "намерение добавить чужой файл снимок потерял")

    def test_чужой_файл_в_снимок_не_попал(self):
        self.assertNotIn("other.txt", self.в_снимке(), self.v)
        self.assertIn("product/app.txt", self.в_снимке(), self.v)


class ПервыйКлючВОтслеживаемомФайле(НастоящийGit):
    """Второй сценарий внешней проверки, дословно."""

    def setUp(self):
        super().setUp()
        self.первый_коммит({".env": БЕЗОБИДНОЕ + "\n"})
        self.голова_до = self.голова()
        # В давно отслеживаемый файл ВПЕРВЫЕ дописывают ключ.
        self.файл(".env", БЕЗОБИДНОЕ + "\n" + ВЫДУМКА + "\n")
        self.код, self.v = self.снимок(self.корень, "snap", "этап")

    def test_снимок_остановлен(self):
        self.assertEqual(self.код, 1, self.v)
        self.assertFalse(self.v["снято"], self.v)

    def test_снимка_не_появилось(self):
        self.assertEqual(self.голова(), self.голова_до,
                         "снимок всё-таки записан, и ключ теперь в истории")

    def test_ключа_в_истории_нет(self):
        self.assertNotIn("пароль-из-теста", self.git("show", "HEAD:.env"))

    def test_рабочий_файл_не_переписан(self):
        self.assertEqual((self.корень / ".env").read_text("utf-8"),
                         БЕЗОБИДНОЕ + "\n" + ВЫДУМКА + "\n",
                         "рабочий файл человека переписан автоматически")

    def test_отказ_объяснён_и_называет_файл(self):
        линия = self.v["линия"]
        self.assertIn(".env", линия, линия)
        self.assertNotIn("попадали в снимки и раньше", линия,
                         "сказано про прошлое то, чего никто не проверял")

    def test_в_отказе_нет_самого_значения(self):
        весь = json.dumps(self.v, ensure_ascii=False)
        self.assertNotIn("пароль-из-теста", весь,
                         "инструмент напечатал найденный ключ, "
                         "предупреждая о ключе")


class ТотЖеКлючУжеВСнимке(НастоящийGit):
    """Обратный контроль ко второму сценарию: НЕ новое значение не тормозит.

    Иначе «останавливаем всё подряд» выглядело бы как работающая проверка:
    файл, чей ключ уже лежит в прошлом снимке, из снимков этим не убрать, и
    отказываться от точки возврата тут не за что.
    """

    def setUp(self):
        super().setUp()
        self.первый_коммит({".env": ВЫДУМКА + "\n", "app.txt": "код\n"})
        self.файл("app.txt", "код\nещё\n")
        # Файл окружения ПРАВЯТ, но ключ в нём тот же самый: иначе он просто не
        # попал бы в изменения, и проверялось бы отсутствие вопроса, а не ответ.
        self.файл(".env", ВЫДУМКА + "\n" + БЕЗОБИДНОЕ + "\n")
        self.код, self.v = self.снимок(self.корень, "snap", "этап")

    def test_снимок_состоялся(self):
        self.assertEqual(self.код, 0, self.v)
        self.assertTrue(self.v["снято"], self.v)

    def test_сказано_что_ключ_был_и_раньше(self):
        self.assertIn("прошлом снимке", self.v["линия"], self.v["линия"])

    def test_второй_ключ_в_том_же_файле_уже_останавливает(self):
        self.файл(".env", ВЫДУМКА + "\n" + ВТОРАЯ_ВЫДУМКА + "\n")
        код, v = self.снимок(self.корень, "snap", "следующий этап")
        self.assertEqual(код, 1, v)
        self.assertFalse(v["снято"], v)


class ИмяКириллицейИСПробелом(НастоящийGit):
    """Имя, которое git печатает не так, как оно лежит на диске."""

    def setUp(self):
        super().setUp()
        self.первый_коммит({"readme.txt": "начало\n"})
        self.файл("product/ТЗ.md", "требование\n")
        self.файл("product/при вет.txt", "код\n")
        self.файл("product/.env", ВЫДУМКА + "\n")
        self.печатное = self.git("status", "--porcelain", "-uall")
        self.код, self.v = self.снимок(self.корень / "product", "snap", "этап")

    def test_git_печатает_имя_закодированным(self):
        """Условие сценария, а не вывод из него: если бы git на этой машине
        печатал имя как есть, дальше проверялся бы другой случай."""
        self.assertIn(r'"product/\320\242\320\227.md"', self.печатное,
                      "git не закодировал имя — проверяется не тот случай: "
                      + self.печатное)

    def test_снимок_состоялся(self):
        self.assertEqual(self.код, 0, self.v)
        self.assertTrue(self.v["снято"], self.v)

    def test_оба_имени_доехали_до_снимка(self):
        в_снимке = self.в_снимке()
        self.assertIn("product/ТЗ.md", в_снимке, в_снимке)
        self.assertIn("product/при вет.txt", в_снимке, в_снимке)

    def test_ключ_рядом_с_такими_именами_всё_равно_замечен(self):
        self.assertEqual([н["файл"] for н in self.v.get("ключи", [])], [".env"],
                         self.v)
        self.assertNotIn("product/.env", self.в_снимке(), self.v)


class ОбычныйСнимокИзКорня(НастоящийGit):
    """Обратный контроль: чинили область и проверку значения, а не переписывали
    инструмент. Вся цепочка «завести — снять — вернуть» обязана работать как
    прежде."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)          # ХРАНИЛИЩА ЗДЕСЬ ЕЩЁ НЕТ

    def test_завести_снять_вернуть(self):
        страница = self.файл("страница.txt", "было\n")

        код, v = self.снимок(self.корень, "init")
        self.assertEqual(код, 0, v["линия"])
        self.assertTrue((self.корень / ".git").is_dir(), v["линия"])

        страница.write_text("стало\n", encoding="utf-8")
        код, v = self.снимок(self.корень, "snap", "Пишем код")
        self.assertEqual(код, 0, v["линия"])
        self.assertTrue(v["снято"], v["линия"])

        код, v = self.снимок(self.корень, "list")
        self.assertEqual(код, 0, v["линия"])
        self.assertIn("Пишем код", [s["этап"] for s in v["снимки"]], v)

        код, v = self.снимок(self.корень, "back", "как было до начала")
        self.assertEqual(код, 0, v["линия"])
        self.assertEqual(страница.read_text("utf-8"), "было\n", v["линия"])

        код, v = self.снимок(self.корень, "list")
        self.assertIn("Пишем код", [s["этап"] for s in v["снимки"]],
                      "возврат унёс с собой более поздний снимок")

    def test_пустой_снимок_не_делается(self):
        self.файл("страница.txt", "было\n")
        self.снимок(self.корень, "init")
        код, v = self.снимок(self.корень, "snap", "ничего не менялось")
        self.assertEqual(код, 0, v["линия"])
        self.assertFalse(v["снято"], v["линия"])
        self.assertIn("нечего фиксировать", v["линия"])


if __name__ == "__main__":
    unittest.main()
