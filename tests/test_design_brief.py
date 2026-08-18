#!/usr/bin/env python3
"""Промпт для внешнего дизайн-инструмента.

Зачем этот шаг существует — из живого прогона, а не из теории.

Направление выбрали одним словом («тёмная галерея»), перевели в восемь
проверяемых величин и отдали строителю. Он сверстал аккуратно, по всем числам,
и человек увидел ГОТОВЫЙ САЙТ, ни разу не увидев дизайна. Палитру выбрал
строитель, ни с кем не согласовав; она случайно вышла похожей на ориентир — и
это удача, а не работа.

Восемь величин — это критерии приёмки, а не дизайн: они говорят «не больше
четырёх цветов» и молчат о том, какие это цвета и почему.

Поэтому дизайн делает ЧЕЛОВЕК во внешнем инструменте, а система собирает ему
промпт и ждёт результата. Здесь заперто то, без чего заход туда возвращает
бесполезное.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, skill_text  # noqa: E402

TOOL = PKG / "tools" / "design_brief.py"
_s = importlib.util.spec_from_file_location("ss_design_brief", TOOL)
db = importlib.util.module_from_spec(_s)
_s.loader.exec_module(db)

from datetime import datetime, timezone, timedelta

FRESH = {"checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "sources": ["https://example/docs"]}

FULL = {"docs": FRESH, "audience": "люди из инстаграма",
        "product": "сайт студии керамики",
        "reference": "как linear.app — вот прям такой уровень",
        "direction": "тёмная галерея",
        "screens": ["начало", "работы", "запись"],
        "feeling": "работы видно первыми, текста мало, одно действие на экране",
        "avoid": ["карусели"], "constraints": ["тёмный фон"]}


class TestAnIncompleteBriefIsRefused(unittest.TestCase):
    """Пропуск любой части стоит целого захода во внешний инструмент.

    Без ролей цветов вернётся палитра без ролей; без списка экранов — красивый
    одинокий экран; без «чего не надо» — карусели и градиенты. Заметить это
    можно только вернувшись, то есть потратив час.
    """

    def test_every_required_part_is_named_when_missing(self):
        for key in db.REQUIRED:
            with self.subTest(part=key):
                data = {**db.EMPTY, **FULL}
                data[key] = [] if isinstance(data[key], list) else ""
                self.assertTrue(any(m.startswith(key) for m in db.missing(data)),
                                f"пропуск «{key}» не назван")

    def test_a_full_brief_has_nothing_missing(self):
        self.assertEqual(db.missing({**db.EMPTY, **FULL}), [])


class TestThePromptCarriesWhatMustComeBack(unittest.TestCase):
    """Что принести обратно — ЧАСТЬ промпта, а не устная договорённость.

    Иначе человек возвращается со скриншотом, из которого нельзя достать ни
    одного значения, и всё приходится домысливать — то есть ровно то, ради
    отказа от чего шаг и заведён.
    """

    def setUp(self):
        self.text = db.render({**db.EMPTY, **FULL})

    def test_it_asks_for_four_colours_with_roles(self):
        self.assertIn("Палитра — фиксированная", self.text)
        for role in ("фон", "приглушённый", "акцент"):
            with self.subTest(role=role):
                self.assertIn(role, self.text)

    def test_it_asks_for_the_scale_and_the_rhythm(self):
        # Перенос строки внутри промпта делает точную фразу непроверяемой —
        # ищем то, что несёт смысл, а не форматирование.
        self.assertIn("пяти ступеней", self.text)
        self.assertIn("базовый шаг отступов", self.text)

    def test_it_asks_for_states_and_empty_states(self):
        self.assertIn("наведение, фокус, нажатие", self.text)
        self.assertIn("пустые состояния", self.text.lower())

    def test_the_reference_is_quoted_verbatim(self):
        """Пересказ «хочет чего-то строгого» теряет единственное, что известно
        о вкусе человека."""
        self.assertIn(FULL["reference"], self.text)

    def test_it_is_written_in_the_humans_voice(self):
        """Промпт от третьего лица («заказчик хочет…») человек начинает
        переписывать с первой строки и теряет остальное."""
        self.assertTrue(self.text.startswith("Собери мне"), self.text[:40])

    def test_every_named_screen_is_listed(self):
        for s in FULL["screens"]:
            with self.subTest(screen=s):
                self.assertIn(s, self.text)


class TestThePromptIsRefusedUntilTheDocsAreChecked(unittest.TestCase):
    """Промпт, собранный по памяти модели, выглядит точно так же, как собранный
    по документации, — и это единственная причина, по которой требование живёт
    в КОДЕ, а не в инструкции агенту.

    Цена ошибки несимметрична: человек уносит промпт во внешний инструмент,
    работает там и возвращается с результатом по устаревшему представлению.
    Обнаружить это можно только вернувшись.
    """

    def test_no_check_at_all_is_refused(self):
        self.assertIsNotNone(db.docs_stale({**db.EMPTY, **FULL, "docs": {}}))

    def test_a_stale_check_is_refused(self):
        old = (datetime.now(timezone.utc)
               - timedelta(days=db.DOCS_FRESH_DAYS + 1)).isoformat()
        v = db.docs_stale({**db.EMPTY, **FULL,
                           "docs": {"checked": old, "sources": ["x"]}})
        self.assertIn("дней назад", v)

    def test_a_check_without_sources_is_refused(self):
        """«Сверялся» без ссылок — то же «агент сказал», проверить нечем."""
        v = db.docs_stale({**db.EMPTY, **FULL,
                           "docs": {"checked": FRESH["checked"], "sources": []}})
        self.assertIn("источники не названы", v)

    def test_a_fresh_check_passes(self):
        self.assertIsNone(db.docs_stale({**db.EMPTY, **FULL}))


class TestTwoStagesNotOne(unittest.TestCase):
    """Дизайн-инструмент строит систему из того, что дали в начале, и дальше
    все проекты наследуют её. Начав с экрана, человек получает красивый экран и
    никакой системы: второй приедет другим, и сшивать их придётся руками."""

    def test_the_system_prompt_asks_for_the_system(self):
        t = db.render_system({**db.EMPTY, **FULL})
        self.assertIn("дизайн-систему", t)
        # Набор компонентов из чек-листа, а не «кнопка и карточка»: чего в
        # наборе нет, то будет придумано заново и по-разному.
        for part in ("Имя у каждого компонента", "скелетоны", "alert",
                     "Layout", "числами"):
            with self.subTest(part=part):
                self.assertIn(part, t)

    def test_the_screens_prompt_builds_on_it(self):
        t = db.render_screens({**db.EMPTY, **FULL})
        self.assertIn("по этой системе", t)
        for s in FULL["screens"]:
            with self.subTest(screen=s):
                self.assertIn(s, t)

    def test_the_audience_is_carried(self):
        """Формат описания требует аудиторию наравне с целью: «сделай страницу
        цен» работает хуже, чем «страница цен для малых агентств»."""
        self.assertIn(FULL["audience"], db.render_system({**db.EMPTY, **FULL}))


class TestCommandLine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "brief.json"

    def _run(self, *args):
        return subprocess.run([sys.executable, str(TOOL), str(self.f), *args],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_showing_an_incomplete_prompt_fails(self):
        r = self._run("--show")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "", "неполный промпт всё равно напечатан")

    def test_collecting_returns_zero_even_while_incomplete(self):
        """Код записи отделён от вердикта о полноте: сборка идёт по частям, и
        ненулевой код на каждом шаге приучил бы его не читать."""
        r = self._run("--product", "сайт")
        self.assertEqual(r.returncode, 0, r.stderr[-200:])
        self.assertFalse(json.loads(r.stdout)["ready"])

    def test_a_complete_prompt_prints(self):
        self._run("--product", FULL["product"], "--reference", FULL["reference"],
                  "--direction", FULL["direction"], "--feeling", FULL["feeling"],
                  "--audience", FULL["audience"],
                  "--screen", "начало", "--screen", "работы",
                  # Без записанной сверки промпт не покажется — и это проверяет
                  # соседний класс; здесь она часть нормального пути.
                  "--audience", "малые агентства",
                  "--source", "https://example/docs")
        r = self._run("--show")
        self.assertEqual(r.returncode, 0, r.stderr[-200:])
        self.assertIn("Собери мне", r.stdout)


class TestTheSkillSendsThePersonOutAndWaits(unittest.TestCase):
    """Строитель, получивший восемь чисел без дизайна, выберет палитру сам —
    и человек увидит её впервые уже работающей."""

    def setUp(self):
        from paths import REPO
        self.t = skill_text("go")

    def test_the_skill_calls_the_tool(self):
        self.assertIn("design_brief.py", self.t)

    def test_the_skill_says_code_waits(self):
        self.assertIn("Код в это время не пишется", self.t)

    def test_the_skill_forbids_drawing_instead_of_the_human(self):
        self.assertIn("не рисует дизайн вместо человека", self.t)

    def test_the_brought_design_is_read_back_before_building(self):
        """Расхождение до сборки стоит одной реплики, после — стоит волны."""
        self.assertIn("перескажи ему своими словами", self.t)


if __name__ == "__main__":
    unittest.main()
