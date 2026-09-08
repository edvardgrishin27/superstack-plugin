#!/usr/bin/env python3
"""Имя файла кириллицей больше не протаскивает пересказ в слепую приёмку.

Сценарий пришёл из внешней проверки и переписан здесь регрессионным тестом
ПРАВИЛЬНОГО поведения. Границы файлов в изменениях искались одной регуляркой
по форме `diff --git a/<путь> b/<путь>`. Git печатает путь иначе, как только
в имени есть не-ASCII буква: имя берётся в кавычки, а байты — восьмеричными
escape-последовательностями (`"a/\\320\\242\\320\\227.md"`). Такую границу
разбор не узнавал, второй файл не начинался — и его текст доставался
предыдущему.

Цена ровно в том, ради чего приёмка заведена. В диффе шли `a.py` и `ТЗ.md`:
показанным числился один `a.py`, скрытых не было вовсе, пакет объявлялся
готовым — а текст спецификации лежал внутри него. Судья, который обязан
видеть только просьбу и результат, читал наш же пересказ просьбы и сверял
пересказ с пересказом. Приёмка переставала быть слепой, отчитываясь, что она
слепая, — худший из возможных исходов, потому что выглядит как успех.

Бьёт это сильнее среднего: кириллическое имя здесь норма и у нас, и у людей,
для которых продукт написан, — `ТЗ.md`, `Спека.md`, `Требования.md`.

Что здесь заперто:
  · имя разбирается во всех формах, которыми его печатает git: в кавычках,
    восьмеричными escape, с пробелом и с кавычкой внутри имени;
  · переименование проверяется по ОБЕИМ сторонам заголовка — спека,
    переехавшая под нейтральным именем, остаётся пересказом;
  · двоичное изменение не приклеивает чужой текст к разрешённому файлу;
  · неопознанная граница — не «файлов нет» и не «всё показано»: пакет не
    собирается, готовность не выставляется, причина называется;
  · обратный контроль: обычный латинский дифф режется как прежде.

Дифф берётся у НАСТОЯЩЕГО git во временной папке и с настройками по
умолчанию — заранее собранная строка доказывала бы только то, что автор
теста и автор разбора одинаково представляют себе вывод git.

Тест самодостаточен: только стандартная библиотека и сам инструмент.
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

РЕПО = Path(__file__).resolve().parents[2]
ИНСТРУМЕНТ = РЕПО / "plugins" / "superstack" / "tools" / "blind_accept.py"

_спек = importlib.util.spec_from_file_location("ss_blind_boundaries", ИНСТРУМЕНТ)
ba = importlib.util.module_from_spec(_спек)
_спек.loader.exec_module(ba)

ПРОСЬБА = "сделай вход по коду из письма"

#: Текст, которого судья не имеет права увидеть. Нарочно без слов-маркеров
#: пересказа: их ловит ДРУГОЙ механизм (следы в теле пакета), и тест, который
#: проходит из-за него, ничего не говорит о границах файлов.
СПЕКА = "Требование: пользователь входит по коду из письма.\n"

#: Git слушается сразу трёх файлов настроек, и любой из них может оказаться
#: с уже отключённым кодированием имён — тогда на этой машине разбирался бы
#: не тот случай, ради которого всё написано. Отсюда полная изоляция: своя
#: настройка, свой автор, никакого чтения чужих файлов.
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
    """Репозиторий во временной папке. Никаких заранее собранных строк."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)
        self.git("init", "-q", "-b", "основная")

    def git(self, *арг: str) -> str:
        p = subprocess.run(["git", *арг], cwd=str(self.корень), env=СРЕДА,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, f"git {' '.join(арг)}: {p.stderr}")
        return p.stdout

    def файл(self, имя: str, текст: str) -> None:
        p = self.корень / имя
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(текст, encoding="utf-8")

    def первый_коммит(self, **файлы: str) -> None:
        for имя, текст in файлы.items():
            self.файл(имя, текст)
        self.git("add", "-A")
        self.git("commit", "-qm", "начало")

    def дифф(self, *арг: str) -> str:
        """Изменения глазами git с настройками ПО УМОЛЧАНИЮ."""
        return self.git("diff", *арг)

    def пакет(self, дифф: str) -> dict:
        return ba.verdict(ba.build_packet(ПРОСЬБА, дифф))


class КириллицаВИмени(НастоящийGit):
    """Тот самый сценарий внешней проверки: в диффе `a.py`, затем `ТЗ.md`."""

    def setUp(self):
        super().setUp()
        self.первый_коммит(**{"a.py": "print(1)\n", "ТЗ.md": СПЕКА})
        self.файл("a.py", "print(1)\nprint(2)\n")
        self.файл("ТЗ.md", СПЕКА + "Требование: коды живут пять минут.\n")
        self.сырой = self.дифф()
        self.v = self.пакет(self.сырой)

    def test_git_печатает_имя_восьмеричными_escape(self):
        """Условие сценария, а не вывод из него.

        Если бы git на этой машине печатал имя как есть, дальше проверялся бы
        другой случай, и весь набор был бы зелёным мимо цели.
        """
        self.assertIn(r'"a/\320\242\320\227.md"', self.сырой,
                      "git не закодировал имя — проверяется не тот случай")

    def test_спека_попадает_в_скрытые(self):
        self.assertIn("ТЗ.md", [h["path"] for h in self.v["hidden_files"]],
                      f"пересказ не опознан как файл: {self.v['hidden_files']}")

    def test_текста_спеки_нет_в_теле_пакета(self):
        self.assertNotIn("Требование:", self.v["changes"],
                         "спека уехала судье внутри чужого файла")

    def test_судье_показан_только_код(self):
        self.assertEqual(self.v["shown_files"], ["a.py"], self.v)

    def test_пакет_собирается(self):
        """Позитивный контроль: строгость, которую нельзя пройти, — не
        строгость. Работа в диффе есть, и пакет обязан собраться."""
        self.assertEqual(self.v["status"], "ready", self.v)

    def test_прогон_целиком_отдаёт_ноль_и_не_показывает_спеку(self):
        """Через код возврата и stdout — а не через вердикт в уме."""
        (self.корень / "просьба.txt").write_text(ПРОСЬБА, encoding="utf-8")
        (self.корень / "изменения.diff").write_text(self.сырой, encoding="utf-8")
        p = subprocess.run([sys.executable, str(ИНСТРУМЕНТ), "pack", "--json",
                            str(self.корень / "просьба.txt"),
                            str(self.корень / "изменения.diff")],
                           capture_output=True, text=True, timeout=120, env=СРЕДА)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-600:])
        v = json.loads(p.stdout)
        self.assertEqual([h["path"] for h in v["hidden_files"]], ["ТЗ.md"], v)
        self.assertNotIn("Требование:", v["changes"])


class ЛатинскийДиффРежетсяКакПрежде(НастоящийGit):
    """Обратный контроль: чинили разбор кавычек, а не переписывали правила."""

    def test_обычные_имена_делятся_на_показанное_и_скрытое(self):
        self.первый_коммит(**{"a.py": "print(1)\n", "SPEC.md": СПЕКА})
        self.файл("a.py", "print(1)\nprint(2)\n")
        self.файл("SPEC.md", СПЕКА + "ещё строка\n")
        v = self.пакет(self.дифф())
        self.assertEqual(v["shown_files"], ["a.py"], v)
        self.assertEqual([h["path"] for h in v["hidden_files"]], ["SPEC.md"], v)
        self.assertNotIn("Требование:", v["changes"])

    def test_обычный_дифф_не_даёт_неразобранных_границ(self):
        self.первый_коммит(**{"a.py": "print(1)\n"})
        self.файл("a.py", "print(1)\nprint(2)\n")
        v = self.пакет(self.дифф())
        self.assertEqual(v["unparsed"], [], v)
        self.assertEqual(v["status"], "ready", v)


class ФормыИмениКоторыеПечатаетGit(НастоящийGit):
    """Кавычки git ставит не только вокруг кириллицы.

    Пробел он не экранирует вовсе, кавычку внутри имени экранирует всегда —
    независимо от кодирования не-ASCII. Разбор, знающий одну форму из трёх,
    зелен ровно до первого настоящего проекта.
    """

    def _показан(self, имя: str) -> None:
        self.первый_коммит(**{имя: "было\n"})
        self.файл(имя, "было\nстало\n")
        v = self.пакет(self.дифф())
        self.assertEqual(v["unparsed"], [], f"{имя}: граница не разобрана")
        self.assertEqual(v["shown_files"], [имя], v)

    def test_пробел_в_имени(self):
        self._показан("мой файл.py")

    def test_пробел_в_латинском_имени(self):
        self._показан("my file.py")

    def test_кавычка_в_имени(self):
        self._показан('quo"te.py')

    def test_кириллица_и_пробел_вместе(self):
        self._показан("исходный код.py")

    def test_вложенный_путь_кириллицей(self):
        self._показан("модуль/вход.py")


class ПереименованиеПроверяетсяПоОбеимСторонам(НастоящийGit):
    """Спека, переехавшая под нейтральным именем, остаётся пересказом.

    Заголовок переименования git печатает половинами НЕЗАВИСИМО: старое имя
    в кавычках, новое — как есть. Разбор, знающий только «обе в кавычках»
    или только «обе без», такую строку не узнаёт.
    """

    def setUp(self):
        super().setUp()
        self.первый_коммит(**{"a.py": "print(1)\n", "ТЗ.md": СПЕКА})
        self.git("mv", "ТЗ.md", "notes.md")
        self.файл("notes.md", СПЕКА + "Требование: коды живут пять минут.\n")
        self.файл("a.py", "print(1)\nprint(2)\n")
        self.git("add", "-A")
        self.сырой = self.дифф("--cached")
        self.v = self.пакет(self.сырой)

    def test_git_печатает_половины_по_разному(self):
        self.assertIn(r'"a/\320\242\320\227.md" b/notes.md', self.сырой,
                      "проверяется не смешанная форма заголовка")

    def test_старое_имя_решает_судьбу_куска(self):
        self.assertIn("notes.md", [h["path"] for h in self.v["hidden_files"]],
                      f"спека под новым именем поехала судье: {self.v}")

    def test_текст_спеки_не_приклеился_к_коду(self):
        self.assertNotIn("Требование:", self.v["changes"], self.v)

    def test_код_по_прежнему_показан(self):
        self.assertEqual(self.v["shown_files"], ["a.py"], self.v)


class ДвоичноеИзменение(НастоящийGit):
    """У двоичного файла нет тела правки — но граница у него есть.

    Не узнав её, разбор продолжает предыдущий файл и доносит до судьи
    следующий за ней текст под чужим именем.
    """

    def setUp(self):
        super().setUp()
        self.первый_коммит(**{"a.py": "print(1)\n", "ТЗ.md": СПЕКА})
        (self.корень / "лого.png").write_bytes(bytes(range(120)))
        self.git("add", "-A")
        self.git("commit", "-qm", "картинка")
        (self.корень / "лого.png").write_bytes(bytes(range(120, 240)))
        self.файл("a.py", "print(1)\nprint(2)\n")
        self.файл("ТЗ.md", СПЕКА + "Требование: коды живут пять минут.\n")
        self.v = self.пакет(self.дифф())

    def test_все_три_границы_разобраны(self):
        self.assertEqual(self.v["unparsed"], [], self.v)
        self.assertEqual(sorted(self.v["shown_files"]), ["a.py", "лого.png"],
                         self.v)

    def test_спека_осталась_скрытой(self):
        self.assertEqual([h["path"] for h in self.v["hidden_files"]], ["ТЗ.md"],
                         self.v)
        self.assertNotIn("Требование:", self.v["changes"], self.v)


class НеопознаннаяГраницаНеСобираетПакет(НастоящийGit):
    """Неразобранная граница — не «файлов нет» и не «всё показано».

    Где кончается один файл, неизвестно, — значит, нечем подтвердить, что
    пересказ вырезан. Молчаливое склеивание тут худший исход из возможных:
    оно неотличимо от успеха.
    """

    def setUp(self):
        super().setUp()
        self.первый_коммит(**{"a.py": "print(1)\n", "ТЗ.md": СПЕКА})
        self.файл("a.py", "print(1)\nprint(2)\n")
        self.файл("ТЗ.md", СПЕКА + "Требование: коды живут пять минут.\n")

    def test_дифф_без_служебных_приставок_не_собирается(self):
        """`--no-prefix` — настоящая форма вывода git, и в ней у путей нет
        ни `a/`, ни `b/`: отличить один путь от другого нечем."""
        v = self.пакет(self.дифф("--no-prefix"))
        self.assertEqual(v["status"], "unknown", v)
        self.assertTrue(v["unparsed"], v)
        self.assertEqual(ba.EXIT[v["status"]], 2)

    def test_причина_названа_а_не_подменена_словами_про_пустоту(self):
        v = self.пакет(self.дифф("--no-prefix"))
        self.assertIn("границ", v["next"].lower(), v["next"])
        self.assertNotIn("изменений нет вовсе", v["next"])

    def test_неразобранный_кусок_не_достаётся_соседу(self):
        """Половина диффа обычная, половина — неразобранная. Раньше вторая
        приклеивалась к последнему опознанному файлу."""
        обычный = self.дифф("--", "a.py")
        чужой = self.дифф("--no-prefix", "--", "ТЗ.md")
        v = self.пакет(обычный + чужой)
        self.assertEqual(v["status"], "unknown", v)
        self.assertNotIn("Требование:", v["changes"], v)

    def test_готовность_не_выставляется_даже_при_живом_показанном_файле(self):
        обычный = self.дифф("--", "a.py")
        чужой = self.дифф("--no-prefix", "--", "ТЗ.md")
        v = self.пакет(обычный + чужой)
        self.assertNotEqual(v["status"], "ready", v)


class ФазаПриёмкиПроситИменаКакЕсть(unittest.TestCase):
    """Разбор кавычек нужен всегда — дифф может прийти собранным иначе.

    Но там, где дифф собираем МЫ, имена незачем кодировать вовсе: одна
    настройка снимает вопрос у источника.
    """

    def test_фаза_отключает_кодирование_имён(self):
        текст = (РЕПО / "plugins" / "superstack" / "skills" / "go" / "phases"
                 / "6-приёмка.md").read_text("utf-8")
        self.assertIn("core.quotepath=false", текст,
                      "фаза собирает дифф с закодированными именами")


if __name__ == "__main__":
    unittest.main()
