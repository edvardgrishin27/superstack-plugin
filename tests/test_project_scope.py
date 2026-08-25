#!/usr/bin/env python3
"""SUPERSTACK работает только там, где его позвали.

Плагины Claude Code ставятся глобально: `Scope: user`. Их хуки объявляются без
привязки к проекту, поэтому установленный продукт начинал работать во ВСЕХ
проектах на машине. Найдено заказчиком на живом случае: в соседнем каталоге он
пишет обучающие материалы, а система спрашивала там про уроки и запускала гейт
верификации.

Цена разная по хукам, и самый дорогой — гейт верификации: в чужом проекте он
может НЕ ДАТЬ ЗАКРЫТЬ ХОД, потому что ни тестов, ни плана там нет и не
предполагалось.

Выключатели у продукта были только глобальные (`SUPERSTACK_DISABLE=1`, `PAUSE`)
— то есть «выключить везде». Нужно обратное: «включить там, где позвали».
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO, skill_text  # noqa: E402

ENABLE = PKG / "tools" / "enable.py"
_s = importlib.util.spec_from_file_location("ss_enable", ENABLE)
en = importlib.util.module_from_spec(_s)
_s.loader.exec_module(en)

#: Хуки, которые срабатывают САМИ. Приветствие в этот список не входит
#: намеренно: оно ограничено счётчиком на машине (три раза за всё время), и
#: человеку, поставившему плагин, полезно узнать об этом хотя бы раз.
AUTO_HOOKS = (
    PKG / "hooks" / "session-lesson.py",
    PKG / "hooks" / "precompact.py",
    PKG / "hooks" / "verify-gate.py",
)


class TestTheRegistry(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        os.environ["SUPERSTACK_STATE_DIR"] = str(self.state)
        self.addCleanup(os.environ.pop, "SUPERSTACK_STATE_DIR", None)
        self.proj = Path(self.tmp.name) / "проект"
        self.proj.mkdir()

    def test_a_fresh_machine_has_nothing_enabled(self):
        self.assertIsNone(en.enabled_for(self.proj))

    def test_enabling_is_idempotent(self):
        en.enable(self.proj)
        en.enable(self.proj)
        self.assertEqual(len(en.known()), 1)

    def test_a_subdirectory_counts_as_the_same_project(self):
        """Работа в подкаталоге репозитория — тот же проект. Иначе человек
        завёл корень, спустился в src/ и система замолчала без причины."""
        en.enable(self.proj)
        sub = self.proj / "src" / "глубоко"
        sub.mkdir(parents=True)
        self.assertIsNotNone(en.enabled_for(sub))

    def test_a_sibling_with_a_shared_prefix_is_not_enabled(self):
        """`/a/bc` не заводится записью `/a/b`: строкой одна начинается с
        другой, а каталогами это разные проекты."""
        en.enable(self.proj)
        other = Path(str(self.proj) + "-обучение")
        other.mkdir()
        self.assertIsNone(en.enabled_for(other))

    def test_forget_removes_it(self):
        en.enable(self.proj)
        self.assertTrue(en.forget(self.proj))
        self.assertIsNone(en.enabled_for(self.proj))

    def test_check_exits_one_when_not_enabled(self):
        p = subprocess.run([sys.executable, str(ENABLE), str(self.proj), "--check"],
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "SUPERSTACK_STATE_DIR": str(self.state)})
        self.assertEqual(p.returncode, 1)

    def test_check_exits_zero_after_enabling(self):
        en.enable(self.proj)
        p = subprocess.run([sys.executable, str(ENABLE), str(self.proj), "--check"],
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "SUPERSTACK_STATE_DIR": str(self.state)})
        self.assertEqual(p.returncode, 0)


class TestHooksAreSilentInForeignProjects(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        self.foreign = Path(self.tmp.name) / "чужой-проект"
        self.foreign.mkdir()
        (self.foreign / "заметка.md").write_text("обучение", encoding="utf-8")

    def _run(self, hook: Path, project: Path):
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state),
               "SUPERSTACK_PROJECT_DIR": str(project)}
        env.pop("SUPERSTACK_DISABLE", None)
        env.pop("SUPERSTACK_IGNORE_PAUSE", None)
        return subprocess.run([sys.executable, str(hook)],
                              capture_output=True, text=True,
                              timeout=60, env=env, cwd=str(project),
                              input='{"session_id":"x"}')

    def test_every_automatic_hook_is_silent_in_a_project_never_enabled(self):
        for hook in AUTO_HOOKS:
            with self.subTest(hook=hook.name):
                p = self._run(hook, self.foreign)
                self.assertEqual(p.stdout.strip(), "",
                                 f"{hook.name} подал голос в проекте, где "
                                 "SUPERSTACK не звали")
                self.assertEqual(p.returncode, 0,
                                 f"{hook.name} вернул ненулевой код в чужом "
                                 "проекте — это может не дать закрыть ход")

    def test_the_lesson_hook_speaks_once_the_project_is_enabled(self):
        """Обратный контроль: гейт не должен глушить систему там, где её
        позвали, — иначе он лечит шум ценой продукта."""
        (self.state / "projects").write_text(str(self.foreign) + "\n",
                                             encoding="utf-8")
        p = self._run(PKG / "hooks" / "session-lesson.py",
                      self.foreign)
        self.assertIn("hookSpecificOutput", p.stdout)


class TestTheGateSurvivesASymlinkedPath(unittest.TestCase):
    """Отметка стоит, а гейт её не видит — из-за симлинка в пути.

    Найдено проверкой выкладки с чистого клона: клон лёг в `mktemp -d`, то есть
    под `/var/folders/...`, а `/var` на macOS — симлинк на `/private/var`.
    Одиннадцать тестов покраснели на КОДЕ, который на самом деле верен.

    Дефект при этом настоящий и тяжелее, чем выглядит. `enable.py` пишет
    `path.resolve()` — разрешённый путь; хук берёт `$PWD`, который оболочка
    наследует как есть. У человека, чей проект лежит под симлинком (`/tmp`,
    сетевой том, синхронизированный каталог), отметка проставится, а НИ ОДИН
    хук не сработает: ни гейт верификации, ни страховка памяти, ни вопрос об
    уроке. Молча — и узнать об этом неоткуда.

    На своей машине это не воспроизводится: репозиторий лежит под `/Users`,
    где симлинка нет. Поэтому тест строит симлинк сам.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        база = Path(self.tmp.name)
        self.state = база / "state"
        self.state.mkdir()
        self.настоящий = база / "настоящий"
        self.настоящий.mkdir()
        # Ссылка на тот же каталог другим именем — ровно то, чем является
        # `/var` для `/private/var`.
        self.через_ссылку = база / "ссылка"
        self.через_ссылку.symlink_to(self.настоящий, target_is_directory=True)

    def _след(self, hook: Path, project: Path) -> tuple:
        """Наблюдаемый след запуска: заговорил ли, каким кодом вышел, оставил
        ли снимок. Сравнивать сами тексты нельзя — в них входит путь проекта,
        и он законно разный под разными именами.
        """
        транскрипт = Path(self.tmp.name) / "т.jsonl"
        транскрипт.write_text('{"role":"user"}\n', encoding="utf-8")
        снимки = self.state / "precompact"
        for f in снимки.glob("*.jsonl") if снимки.is_dir() else ():
            f.unlink()
        # Отметка «когда спрашивали в прошлый раз» делает второй запуск подряд
        # молчаливым: хук не спрашивает про урок, если с прошлого раза ничего
        # не менялось. Без сброса сравнение измеряло бы её, а не гейт — первая
        # версия теста так и падала, показывая расхождение там, где его нет.
        (self.state / "last-lesson-ask").unlink(missing_ok=True)
        # То же и у гейта верификации: он помечает сессию, которой уже сказал,
        # и второй раз молчит. Оба следа — законная память хука о собственной
        # работе, но здесь она маскировала бы измеряемое.
        (self.state / "gate-noted.sessions").unlink(missing_ok=True)
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state),
               "SUPERSTACK_PROJECT_DIR": str(project)}
        env.pop("SUPERSTACK_DISABLE", None)
        env.pop("SUPERSTACK_IGNORE_PAUSE", None)
        p = subprocess.run(
            [sys.executable, str(hook)], capture_output=True, text=True, timeout=60,
            env=env, cwd=str(project),
            input='{"session_id":"с1","transcript_path":"%s"}' % транскрипт)
        оставил = снимки.is_dir() and any(снимки.glob("*.jsonl"))
        return (bool(p.stdout.strip()), p.returncode, оставил)

    def test_the_gate_opens_through_a_symlink_exactly_as_it_does_directly(self):
        """Отметка стоит разрешённым путём, путь приходит через ссылку.

        Сигнал — не совпадение текстов (в них входит сам путь), а то, что след
        запуска ОТЛИЧАЕТСЯ от следа при закрытом гейте. Без этого сравнения
        тест зеленел бы на двух одинаково молчащих запусках, то есть на
        полностью выключенной системе.
        """
        for hook in AUTO_HOOKS:
            with self.subTest(hook=hook.name):
                (self.state / "projects").write_text("", encoding="utf-8")
                закрыт = self._след(hook, self.через_ссылку)
                (self.state / "projects").write_text(
                    str(self.настоящий.resolve()) + "\n", encoding="utf-8")
                через_ссылку = self._след(hook, self.через_ссылку)
                напрямую = self._след(hook, self.настоящий)
                self.assertNotEqual(
                    через_ссылку, закрыт,
                    f"{hook.name} не отличил открытый гейт от закрытого — "
                    "отметка стоит разрешённым путём, а путь пришёл через "
                    "симлинк, и они не сопоставились")
                self.assertEqual(
                    через_ссылку, напрямую,
                    f"{hook.name} ведёт себя по-разному под двумя именами "
                    "одного каталога")

    def test_a_mark_written_unresolved_is_still_found(self):
        """Обратная сторона: отметка записана НЕразрешённой.

        Так выглядит файл, правленный руками или оставшийся от прежней версии.
        Сверять только одну сторону значит починить свой случай и сломать
        соседний — что и произошло: первая правка уронила существующий тест.
        """
        (self.state / "projects").write_text(
            str(self.через_ссылку) + "\n", encoding="utf-8")
        сказал, _, _ = self._след(PKG / "hooks" / "session-lesson.py",
                                  self.настоящий)
        self.assertTrue(сказал, "отметка неразрешённым путём перестала "
                                "находиться — починили одну сторону, сломав "
                                "другую")


class TestTheGateIsInEveryAutomaticHook(unittest.TestCase):
    """Новый автоматический хук без этого гейта вернёт ровно ту болезнь, и
    заметят её опять только по жалобе из чужого проекта."""

    def test_each_hook_reads_the_registry(self):
        """Проверяется ТО, ЧТО РЕАЛЬНО ЗАПУСКАЕТСЯ, а не всё, что похоже на хук.

        Раньше проверка обходила `hooks/*.sh` — и это работало, пока хуки были
        скриптами. После переезда на Python `.sh` остался тонкой обёрткой без
        логики: обход по маске нашёл бы её и отчитался о гейте, которого в ней
        нет. Поэтому список берётся из `hooks.json`: запускается именно то, что
        там названо.
        """
        import json as _json
        реестр = _json.loads((PKG / "hooks" / "hooks.json").read_text("utf-8"))

        команды = []

        def обойти(узел):
            if isinstance(узел, dict):
                if isinstance(узел.get("command"), str):
                    команды.append(узел["command"])
                for v in узел.values():
                    обойти(v)
            elif isinstance(узел, list):
                for v in узел:
                    обойти(v)

        обойти(реестр)
        self.assertTrue(команды, "в hooks.json не объявлено ни одной команды")

        # `first-run` исключён НАМЕРЕННО и по существу: это хук, который
        # ПРЕДЛАГАЕТ позвать систему. Требовать от него отметку значит требовать
        # молчать ровно там, где он один и нужен — в проекте, где SUPERSTACK
        # ещё не звали. Его собственный потолок предложений проверяется отдельно.
        ПРЕДЛАГАЕТ = "first-run"
        for команда in команды:
            имя = команда.split("/hooks/")[-1].strip('"')
            if ПРЕДЛАГАЕТ in имя:
                continue
            файл = PKG / "hooks" / имя
            with self.subTest(hook=имя):
                self.assertTrue(файл.is_file(), f"{имя} объявлен и не существует")
                t = файл.read_text("utf-8")
                self.assertTrue(
                    "projects" in t and ("enabled_here" in t or "позвали_здесь" in t),
                    f"{имя} не спрашивает, звали ли его здесь")

    def test_the_starting_skills_write_the_mark(self):
        """Отметку ставят скиллы, НАЧИНАЮЩИЕ работу. `/what`, `/fix`, `/oops`
        её не ставят намеренно: они диагностика и откат, включать ими систему
        в чужом проекте не за что."""
        for имя in ("go", "superstack"):
            with self.subTest(skill=имя):
                self.assertIn("enable.py", skill_text(имя),
                              f"{имя} не отмечает проект — хуки останутся "
                              "немыми там, где систему как раз позвали")


if __name__ == "__main__":
    unittest.main()
