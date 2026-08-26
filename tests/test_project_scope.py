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

    def test_forget_falls_back_to_a_covering_entry(self):
        """Дефект 2: `forget()` требовал ТОЧНОГО совпадения. Человек стоит в
        подкаталоге заведённого дерева и говорит «убери отсюда» — точной
        записи на подкаталог никогда не было, есть только запись на корень
        дерева. Раньше это отвечало «не был заведён» и не убирало ничего,
        хотя `--check` в том же каталоге отвечает «да, заведён»: путь назад
        был недоступен именно оттуда, где человек находится."""
        en.enable(self.proj)
        sub = self.proj / "src" / "глубоко"
        sub.mkdir(parents=True)
        removed = en.forget(sub)
        self.assertEqual(removed, str(self.proj.resolve()))
        self.assertIsNone(en.enabled_for(self.proj))
        self.assertIsNone(en.enabled_for(sub))

    def test_forget_reports_which_covering_entry_it_removed(self):
        """Человек просил «убери ОТСЮДА», а убралась запись выше по дереву —
        это обязано быть видно в выводе, а не молчаливо подразумеваться."""
        en.enable(self.proj)
        sub = self.proj / "src" / "глубоко"
        sub.mkdir(parents=True)
        p = subprocess.run([sys.executable, str(ENABLE), str(sub), "--forget"],
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "SUPERSTACK_STATE_DIR": str(self.state)})
        self.assertIn(str(self.proj.resolve()), p.stderr,
                     "в выводе не названа покрывающая запись, которую реально убрали")

    def test_forget_states_the_consequence_in_plain_words(self):
        """Мало назвать механизм («убрана более широкая отметка») — нужно
        сказать ПОСЛЕДСТВИЕ: система теперь молчит не только здесь, а везде
        внутри убранного. Без этой фразы человек узнает о выключенных
        проверках в другом своём проекте и не свяжет это с сегодняшней
        просьбой «убери отсюда». Термины устройства («покрывающая запись»,
        «выше по дереву») в выводе для человека не место."""
        en.enable(self.proj)
        sub = self.proj / "src" / "глубоко"
        sub.mkdir(parents=True)
        p = subprocess.run([sys.executable, str(ENABLE), str(sub), "--forget"],
                           capture_output=True, text=True, timeout=30,
                           env={**os.environ, "SUPERSTACK_STATE_DIR": str(self.state)})
        self.assertIn("не только здесь", p.stderr,
                     "не сказано, что молчание не ограничено этим каталогом")
        for термин in ("покрывающая запись", "выше по дереву"):
            self.assertNotIn(термин, p.stderr,
                             f"в выводе для человека остался термин устройства «{термин}»")

    def test_forget_without_any_matching_entry_still_reports_absence(self):
        """Обратный контроль: поведение при полном отсутствии записи не
        должно было измениться правкой — иначе почини одно, сломай другое."""
        self.assertIsNone(en.forget(self.proj))

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


class TestEnableRefusesTooBroadDirectories(unittest.TestCase):
    """Дефект 1: `enable()` заводил ЛЮБОЙ каталог, включая $HOME. В десктопном
    приложении новый чат нередко открывается прямо в $HOME — это не крайний
    случай, а обычный. Заведённым по префиксу дерева (`enabled_for()`)
    становилось бы вообще всё на машине, включая проекты, где систему никто
    не звал, — ровно та болезнь, ради лечения которой сам реестр придуман.

    $HOME здесь ПОДСТАВНОЙ, задаётся переменной окружения: иначе тест либо не
    воспроизводил бы дефект на «безопасной» машине, либо и правда пытался бы
    завести настоящий $HOME при падении проверки.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.state.mkdir()
        os.environ["SUPERSTACK_STATE_DIR"] = str(self.state)
        self.addCleanup(os.environ.pop, "SUPERSTACK_STATE_DIR", None)

        self._real_home = os.environ.get("HOME")
        self.home_parent = Path(self.tmp.name) / "домашние"
        self.home = self.home_parent / "пользователь"
        self.home.mkdir(parents=True)
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)

        self.proj = self.home / "проекты" / "мой-проект"
        self.proj.mkdir(parents=True)

    def _restore_home(self):
        if self._real_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._real_home

    def test_enabling_home_itself_is_refused(self):
        """Обычный случай, не крайний: новый чат в десктопном приложении,
        если человек ещё не зашёл ни в один проект, живёт прямо в $HOME."""
        with self.assertRaises(en.TooBroad):
            en.enable(self.home)
        self.assertEqual(en.known(), [], "отказ не должен заводить наполовину")

    def test_enabling_an_ancestor_of_home_is_refused(self):
        """Каталог НАД домом (аналог /Users) заводить нельзя: под ним лежат
        домашние каталоги всех, кто пользуется машиной, а не только автора."""
        with self.assertRaises(en.TooBroad):
            en.enable(self.home_parent)
        self.assertEqual(en.known(), [])

    def test_enabling_the_filesystem_root_is_refused(self):
        """Тот же случай в пределе: корень — предок вообще всего."""
        with self.assertRaises(en.TooBroad):
            en.enable(Path("/"))
        self.assertEqual(en.known(), [])

    def test_an_ordinary_project_under_home_still_enables_normally(self):
        """Обратный контроль: правило обязано ловить ровно три широких
        случая и не задевать обычный проект внутри дома — иначе чинили бы
        одну беду, создав другую."""
        p = en.enable(self.proj)
        self.assertEqual(p, str(self.proj.resolve()))
        self.assertIn(p, en.known())

    def test_the_cli_exits_with_a_dedicated_code_and_names_the_next_step(self):
        """`main()`: отдельный код возврата (не общий код ошибки вызова) и
        человеческая причина с шагом дальше — не молчаливый провал."""
        env = {**os.environ, "SUPERSTACK_STATE_DIR": str(self.state),
               "HOME": str(self.home)}
        p = subprocess.run([sys.executable, str(ENABLE), str(self.home)],
                           capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(p.returncode, en.EXIT_TOO_BROAD)
        self.assertIn("перейди", p.stderr,
                     "не назван следующий шаг — что делать вместо этого")
        self.assertEqual(en.known(), [],
                         "CLI не должен был завести каталог наполовину")


class TestPhaseOneStopsClaimingItChangesNothing(unittest.TestCase):
    """Дефект 3: фаза 1 называла себя «только чтение, ничего не меняет» и
    заставляла агента сказать вслух «ничего менять не буду», хотя тут же по
    ходу фазы вызывается enable.py, который пишет отметку на диск. Ложь
    именно здесь особенно вредна: она произносится ЧЕЛОВЕКУ раньше, чем
    становится неправдой."""

    def test_the_heading_no_longer_claims_the_phase_changes_nothing(self):
        текст = skill_text("superstack")
        self.assertNotIn("только чтение, ничего не меняет", текст,
                         "заголовок фазы 1 всё ещё обещает, что ничего не "
                         "меняется, хотя следом идёт запись на диск")
        self.assertIn("не меняет проект", текст,
                      "новый заголовок обязан прямо сказать, что не "
                      "меняется, — иначе неясно, что вообще поправили")

    def test_the_spoken_line_no_longer_promises_nothing_will_change(self):
        текст = skill_text("superstack")
        self.assertNotIn("Ничего менять не буду", текст,
                         "фраза, которую агент произносит вслух, всё ещё "
                         "обещает не менять ничего")
        self.assertIn("не поменяю", текст,
                      "честная замена не найдена — человеку обязаны сказать "
                      "правду о том, что проект не меняется")


class TestPhaseOneReactsWhenEnablingFails(unittest.TestCase):
    """`enable.py` умеет отказывать широким каталогам (см.
    `TestEnableRefusesTooBroadDirectories`) и возвращает ненулевой код, но до
    этой правки фаза 1 никак не разбирала исход: `enable.py` и сбор проб
    стояли в одном bash-блоке подряд, без разбора кода между ними, — команда
    отказывала, вывод уходил в stderr, а следующая строка всё равно
    запускалась и своим успехом маскировала отказ. Агент по старой
    инструкции шёл дальше, а человек так и не узнавал, что система здесь
    работать не будет: механизм построен, а сообщить о нём некому."""

    def test_the_bash_block_stops_before_running_probes_on_refusal(self):
        """Без `|| exit $?` блок продолжает выполняться после отказа:
        `enable.py` мог вернуть ненулевой код, а сбор проб всё равно
        запускался бы следующей строкой и скрывал бы отказ своим успехом."""
        текст = skill_text("superstack")
        self.assertIn('enable.py" . || exit $?', текст,
                     "bash-блок фазы 1 не останавливается на отказе "
                     "заведения — следующая команда маскирует его код")

    def test_the_skill_tells_the_agent_not_to_continue_silently_on_refusal(self):
        текст = skill_text("superstack")
        self.assertIn("Если заведение отказало", текст,
                      "фаза 1 не называет случай, когда enable.py отказал")
        self.assertIn("в какой папке лежит его проект", текст,
                      "фаза 1 не велит спросить человека, где его проект")
        self.assertIn("молча продолжать", текст,
                      "фаза 1 не запрещает молча продолжать после отказа")


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
