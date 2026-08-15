#!/usr/bin/env python3
"""Четыре дефекта, найденные ПЕРВЫМ живым прогоном `/go`.

Все четыре пережили 1147 тестов и 259 мутаций, потому что каждый инструмент
проверялся отдельно и по фикстурам, которые писал тот же, кто писал инструмент.
Прогон целиком нашёл их за двадцать минут.

  1. Движок подставляет аргументы слэш-команды в ТЕЛО скилла: `"$1"` внутри
     bash-функции стал словом из просьбы человека, и резолвер пошёл искать
     инструмент с именем «брифа».
  2. `--addition` знал только `--parent`, `--discovered` только `--serves`, а
     сообщение об ошибке не называло ни один флаг.
  3. Гейт G2 требует `coverage`, а команды записи для него не существовало —
     поле заполнялось правкой JSON руками.
  4. `progress.py` при обновлении таска без `--wave` клал КОПИЮ в первую волну,
     оставляя исходную запись: два субагента получили бы один таск.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, plug  # noqa: E402


def _load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


pr = _load("ss_progress", plug("superstack-build") / "tools" / "progress.py")
mf = _load("ss_manifest", plug("superstack-spec") / "tools" / "manifest.py")
MANIFEST = plug("superstack-spec") / "tools" / "manifest.py"


class TestSkillsHaveNoPositionalParameters(unittest.TestCase):
    """Позиционный параметр в теле скилла — не стиль, а поломка.

    Движок подставляет `$1`, `$2`, `$ARGUMENTS` в текст скилла перед тем, как
    отдать его модели. Bash-функция `T() { ... "$1"; }` после подстановки
    вызывает резолвер со словом из просьбы человека. Отказ тихий: команда
    формально исполняется, просто ищет несуществующий инструмент — и так все
    27 вызовов файла.
    """

    def test_no_shell_block_in_a_skill_uses_a_positional_parameter(self):
        """Проверяются ИСПОЛНЯЕМЫЕ блоки, а не проза.

        Первая версия теста читала файл построчно и краснела на объяснении,
        где `$1` упомянут словами. Тест, запрещающий описывать собственный
        дефект, заставил бы удалить объяснение вместо поломки.
        """
        bad = []
        for s in sorted(REPO.glob("plugins/*/skills/**/*.md")):
            inside = False
            for n, line in enumerate(s.read_text("utf-8").splitlines(), 1):
                if line.strip().startswith("```"):
                    inside = line.strip().startswith("```bash")
                    continue
                # Комментарий внутри блока не исполняется: подстановка в него
                # безвредна, а объяснять дефект в том же блоке, где он был, —
                # ровно то место, где объяснение прочитают.
                if line.lstrip().startswith("#"):
                    continue
                if inside and re.search(r'"\$\{?[1-9]\}?"', line):
                    bad.append(f"{s.relative_to(REPO)}:{n}: {line.strip()[:60]}")
        self.assertEqual(bad, [], "позиционный параметр в исполняемом блоке — "
                                  "движок подставит туда аргумент слэш-команды: "
                                  + "; ".join(bad))

    def test_the_go_skill_still_resolves_tools(self):
        """Обратный контроль: убрав функцию, вызовы не должны остаться битыми."""
        t = (REPO / "plugins/superstack-build/skills/go/SKILL.md").read_text("utf-8")
        self.assertNotIn('$(T ', t, "остался вызов через удалённую функцию")
        self.assertGreaterEqual(t.count('$(python3 "$W" '), 20)


class TestBothParentFlagsWork(unittest.TestCase):
    """Один смысл — два флага, и ошибка не называла ни один."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "brief.md").write_text("хочу сайт студии", encoding="utf-8")
        self.man = self.d / "manifest.json"
        self._run("init", str(self.man), str(self.d / "brief.md"))
        self._run("add", str(self.man), "R01", "--quote", "хочу сайт студии")

    def _run(self, *args):
        return subprocess.run([sys.executable, str(MANIFEST), *args],
                              capture_output=True, text=True, timeout=60)

    def test_discovered_accepts_parent_too(self):
        p = self._run("add", str(self.man), "D01", "--discovered", "код доказал",
                      "--parent", "R01")
        self.assertEqual(p.returncode, 0, p.stderr[-200:])

    def test_addition_accepts_serves_too(self):
        p = self._run("add", str(self.man), "A01", "--addition", "углубление",
                      "--serves", "R01")
        self.assertEqual(p.returncode, 0, p.stderr[-200:])

    def test_the_refusal_names_the_flag_to_use(self):
        """«Обязана назвать требование» верно и бесполезно: на живом прогоне
        это дало четыре отказа подряд и мусорную строку в манифесте."""
        p = self._run("add", str(self.man), "D02", "--discovered", "без родителя")
        # Код 1 (нарушено) или 3 (ошибка вызова) — оба честны; важно, что не 0
        # и что в тексте назван флаг, которым это чинится.
        self.assertNotEqual(p.returncode, 0, p.stderr[-200:])
        self.assertIn("--serves", p.stderr)
        p2 = self._run("add", str(self.man), "A02", "--addition", "без родителя")
        self.assertIn("--parent", p2.stderr)


class TestCoverageHasACommand(unittest.TestCase):
    """Гейт требовал поле, которое нечем было записать.

    Ровно та болезнь, что ловят ворота проводки, этажом выше: механизм есть,
    запустить его нечем. Обходной путь — правка JSON руками — не проверяется
    ничем и молча принимает любую чушь.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "brief.md").write_text("хочу сайт", encoding="utf-8")
        self.man = self.d / "manifest.json"
        self._run("init", str(self.man), str(self.d / "brief.md"))

    def _run(self, *args):
        return subprocess.run([sys.executable, str(MANIFEST), *args],
                              capture_output=True, text=True, timeout=60)

    def _cov(self):
        return json.loads(self.man.read_text("utf-8")).get("coverage")

    def test_coverage_is_written_by_a_command(self):
        p = self._run("coverage", str(self.man), "--found", "23", "--fixed", "21",
                      "--deferred", "2", "--by", "субагент, только бриф и спека",
                      "--deferred-what", "повтор уведомления; просмотр заявок")
        self.assertEqual(p.returncode, 0, p.stderr[-200:])
        c = self._cov()
        self.assertEqual((c["found"], c["fixed"], c["deferred"]), (23, 21, 2))
        self.assertEqual(len(c["deferred_what"]), 2)

    def test_closing_more_than_found_is_refused(self):
        """Не щедрость отчёта, а признак того, что считали разные вещи."""
        p = self._run("coverage", str(self.man), "--found", "3", "--fixed", "5",
                      "--by", "кто-то")
        self.assertEqual(p.returncode, 3, p.stderr[-200:])
        self.assertIsNone(self._cov())

    def test_a_review_without_a_reviewer_is_refused(self):
        """Сверка без имени проверяющего неотличима от самопроверки, а весь
        смысл G2 в том, что спеку прочитал НЕ её автор."""
        p = self._run("coverage", str(self.man), "--found", "5", "--fixed", "5")
        self.assertEqual(p.returncode, 3, p.stderr[-200:])
        self.assertIn("--by", p.stderr)

    def test_deferred_must_be_named(self):
        p = self._run("coverage", str(self.man), "--found", "5", "--fixed", "3",
                      "--deferred", "2", "--by", "субагент")
        self.assertEqual(p.returncode, 3, p.stderr[-200:])
        self.assertIn("--deferred-what", p.stderr)


class TestUpdatingATaskDoesNotCloneIt(unittest.TestCase):
    """Задвоение таска — не грязный файл, а два субагента в одной зоне.

    Волна раздаётся по вызову агента на таск. Копия таска в другой волне
    означает, что один и тот же кусок работы уйдёт дважды, и оба исполнителя
    будут писать в одну территорию — ровно та потеря, которую ловит расчёт зон,
    только созданная самим инструментом планирования.
    """

    def _state(self):
        return {"schema": "superstack.progress.v1", "waves": {}, "stages": [],
                "updated": ""}

    def test_update_without_a_wave_keeps_the_task_where_it_is(self):
        d = pr.set_task(self._state(), "02", "галерея", 2, "waiting")
        d = pr.set_task(d, "02", "галерея", None, "waiting", blocked_by=["01"])
        ids = {w: [t["id"] for t in lst] for w, lst in d["waves"].items()}
        self.assertEqual(ids, {"2": ["02"]}, f"таск задвоился: {ids}")
        self.assertEqual(d["waves"]["2"][0]["blockedBy"], ["01"])

    def test_moving_a_task_moves_it_instead_of_copying(self):
        d = pr.set_task(self._state(), "03", "запись", 1, "waiting")
        d = pr.set_task(d, "03", "запись", 3, "waiting")
        places = [w for w, lst in d["waves"].items()
                  if any(t["id"] == "03" for t in lst)]
        self.assertEqual(places, ["3"], f"таск оказался в {places}")

    def test_an_emptied_wave_disappears(self):
        """Пустой ключ волны читается как «волна есть, в ней никого» — и
        считается за волну при разборе яруса."""
        d = pr.set_task(self._state(), "01", "каркас", 1, "waiting")
        d = pr.set_task(d, "01", "каркас", 2, "waiting")
        self.assertNotIn("1", d["waves"])

    def test_the_position_inside_a_wave_survives_an_update(self):
        """План читают глазами: таск, уходящий в конец списка при каждой правке
        статуса, перетасовывает страницу без единой смысловой причины."""
        d = self._state()
        for i in ("01", "02", "03"):
            d = pr.set_task(d, i, f"таск {i}", 1, "waiting")
        d = pr.set_task(d, "02", "таск 02", None, "running")
        self.assertEqual([t["id"] for t in d["waves"]["1"]], ["01", "02", "03"])

    def test_the_first_wave_is_still_the_default_for_a_new_task(self):
        d = pr.set_task(self._state(), "01", "каркас", None, "waiting")
        self.assertEqual(list(d["waves"]), ["1"])


if __name__ == "__main__":
    unittest.main()


class TestWritingIsNotAVerdict(unittest.TestCase):
    """Код записи и вердикт о состоянии — разные утверждения.

    `review.py find` возвращал 1 сразу после УСПЕШНОЙ записи находки: находка
    легла в файл, а код сообщал «ревью не пройдено». Первая же находка выглядела
    отказом инструмента; скрипт, смотрящий на код, бросает работу на середине
    ревью, а человек видит красное там, где механизм отработал как задуман.

    Тот же класс уже чинился в манифесте и в состязательном проходе — здесь он
    пережил обе починки, потому что живёт в третьем инструменте.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "review.json"
        self.tool = plug("superstack-guard") / "tools" / "review.py"

    def _run(self, *args):
        return subprocess.run([sys.executable, str(self.tool), *args],
                              capture_output=True, text=True, timeout=60)

    def test_recording_a_finding_returns_zero(self):
        p = self._run("find", str(self.f), "--axis", "craft",
                      "--where", "src/a.js:1", "--what", "что-то",
                      "--must", "как должно быть")
        self.assertEqual(p.returncode, 0, p.stderr[-200:])

    def test_the_verdict_still_reports_blockers(self):
        """Обратный контроль: смягчив код записи, нельзя потерять вердикт."""
        self._run("find", str(self.f), "--axis", "craft", "--where", "src/a.js:1",
                  "--what", "что-то", "--must", "как должно быть")
        self.assertEqual(self._run("route", str(self.f)).returncode, 1)


class TestTheLessonWatchdogIgnoresTheSystemsOwnNoise(unittest.TestCase):
    """«Файл изменился» не значит «работал агент».

    Прогон мутаций правит и восстанавливает файлы в том же дереве десятки минут
    подряд. Критерий «изменился ли хоть один файл» давал «да» на каждом
    разговорном ходе — шесть подряд, ни в одном не было правки. Сторож считал
    собственный шум системы работой человека и снова превращал разговор в петлю
    коротких реплик, ради выхода из которой критерий и вводился.
    """

    HOOK = plug("superstack-brain") / "hooks" / "session-lesson.sh"

    def test_the_hook_is_silent_while_mutations_run(self):
        t = self.HOOK.read_text("utf-8")
        self.assertIn(".mutation-lock", t,
                      "хук не знает признака «система работает сама»")
        self.assertIn(".mutation-backup", t,
                      "служебные копии считаются работой человека")
