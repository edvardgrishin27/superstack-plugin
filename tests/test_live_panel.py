#!/usr/bin/env python3
"""Живая панель хода: где мы сейчас и на ком ход.

Панель — самая опасная поверхность продукта, и это не оговорка: текст можно не
дочитать, полоску нельзя не увидеть. Зелёная шкала читается как факт, даже
когда за ней стоит одно лишь слово агента.

Поэтому здесь заперты три вещи:

  · полоса растёт ТОЛЬКО от доказанного — заявленное на неё не влияет;
  · держатель хода назван всегда, иначе панель показывает занятость вместо
    состояния: половина ожиданий в прогоне это «каждый ждёт другого»;
  · разметка строится методами DOM, а не склейкой строк — в состояние попадают
    имена задач, которые пишет модель.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO  # noqa: E402

TOOL = PKG / "tools" / "live_panel.py"
_s = importlib.util.spec_from_file_location("ss_live_panel", TOOL)
lp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(lp)

_p = importlib.util.spec_from_file_location(
    "ss_progress_panel", PKG / "tools" / "progress.py")
pr = importlib.util.module_from_spec(_p)
_p.loader.exec_module(pr)

_r = importlib.util.spec_from_file_location(
    "ss_plain_ru_panel", PKG / "tools" / "plain_ru.py")
plain_ru = importlib.util.module_from_spec(_r)
_r.loader.exec_module(plain_ru)


class TestTheHolderOfTheTurnIsNamed(unittest.TestCase):
    """«Идёт фаза 3» не говорит, ждут ли человека. Он ждёт систему, система
    ждёт его, и оба молчат — это и есть половина потерянного времени."""

    def test_an_unknown_holder_is_refused(self):
        with self.assertRaises(ValueError):
            pr.set_phase(dict(pr.EMPTY), "спека", "кто-то")

    def test_every_known_holder_is_accepted(self):
        for owner in pr.OWNERS:
            with self.subTest(owner=owner):
                d = pr.set_phase(dict(pr.EMPTY), "спека", owner)
                self.assertEqual(d["phase"]["owner"], owner)

    def test_the_phase_carries_when_it_started(self):
        """Без отметки времени «идёт» неотличимо от «застряло»: фаза, висящая
        сорок минут, и начатая минуту назад выглядят одинаково."""
        d = pr.set_phase(dict(pr.EMPTY), "спека", "человек")
        self.assertIn("since", d["phase"])
        datetime.fromisoformat(d["phase"]["since"])


class TestThePanelIsBuiltSafely(unittest.TestCase):
    """Данные тут свои, но правило дешевле соблюдать всегда, чем помнить, где
    можно нарушить: в состояние попадают имена задач и деталей, написанные
    моделью, и однажды туда приедет угловая скобка."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        self.html = lp.write_panel(self.d).read_text("utf-8")

    def test_no_innerhtml_anywhere(self):
        self.assertNotIn("innerHTML", self.html)

    def test_values_go_through_text_content(self):
        self.assertIn("textContent", self.html)

    def test_the_phase_list_is_built_by_dom_methods(self):
        self.assertIn("createElement", self.html)


class TestTheBarGrowsOnlyFromProof(unittest.TestCase):
    """Разрешив полосе расти от заявленного, мы получили бы дашборд, на котором
    «агент сказал» неотличимо от «гейт вернул ноль»."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        self.html = lp.write_panel(self.d).read_text("utf-8")

    def test_the_width_is_computed_from_proven_only(self):
        m = re.search(r"\$\('bar'\)\.style\.width\s*=\s*(.+?);", self.html, re.S)
        self.assertIsNotNone(m, "полоса не считается вовсе")
        self.assertIn("proven", m.group(1))
        self.assertNotIn("claimed", m.group(1),
                         "заявленное влияет на полосу — это зелёное за слова")

    def test_the_basis_is_stated_on_the_panel(self):
        """Полоса без подписи о том, что её двигает, читается как обычный
        прогресс — и «заявлено» молча становится достижением.

        Проверяется смысл, а не буква: первая редакция объясняла шкалу словами
        «гейт вернул ноль» и «слово агента» — точная формулировка, которую
        человек без опыта в коде не понимает. Тест, требовавший её дословно,
        держал панель на языке автора.
        """
        note = plain_ru.copy("bar_note")
        self.assertIn(note, self.html, "подписи под полосой нет вовсе")
        self.assertIn("проверила машина", note)
        self.assertIn("готово", note, "не сказано, что слова помощника не в счёт")

    def test_no_visible_wording_is_frozen_in_the_markup(self):
        """Тексты живут в словаре, иначе правка формулировки — правка кода, и
        человеку приходится просить о ней отдельно каждый раз. Он уже просил
        трижды за час: про подпись этапа, про держателя хода и про эту сноску.

        Проверяется РАЗМЕТКА, а не весь файл: в комментариях кода служебные
        слова законны — их читает тот, кто правит панель, а не тот, кто ждёт
        работу.
        """
        body = re.search(r"<body>(.+)</body>", self.html, re.S).group(1)
        body = re.sub(r"<script>.+?</script>", "", body, flags=re.S)
        texts = [t.strip() for t in re.sub(r"<[^>]+>", "\n", body).split("\n")]
        words = plain_ru.load()["copy"]["map"]
        allowed = (set(words.values()) | {lp.BRAND, "",
                                          f"{lp.BRAND} · {words['title']}"})
        for t in texts:
            if not t or t in allowed:
                continue
            # Бренд приезжает подстановкой и стоит в строке с разделителем.
            self.assertNotIn(t, self.html.split("<body>")[1],
                             f"надпись «{t}» вшита в разметку мимо словаря")


class TestThePanelAndTheWorkCannotDrift(unittest.TestCase):
    """Панель, молча показывающая устаревший этап, хуже отсутствующей: ей
    верят. Поэтому этап на экране обязан следовать из работы, а расхождение —
    называться словами, а не прятаться."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        self.html = lp.write_panel(self.d).read_text("utf-8")

    def test_both_sides_name_the_same_phase(self):
        """Две строки в двух пакетах разошлись бы молча, и панель начала бы
        показывать этап, которого нет в её же списке."""
        self.assertEqual(lp.BUILD_PHASE, pr.BUILD_PHASE)
        self.assertIn(lp.BUILD_PHASE, lp.PHASES)

    def test_the_panel_notices_the_drift_itself(self):
        m = re.search(r"const off\s*=\s*(.+?);", self.html, re.S)
        self.assertIsNotNone(m, "расхождение не проверяется вовсе")
        self.assertIn("running", m.group(1))
        self.assertIn("BUILD_PHASE", m.group(1))

    def test_the_drift_is_explained_in_plain_words(self):
        said = plain_ru.copy("mismatch")
        self.assertIn(said, self.html)
        self.assertEqual(plain_ru.find_jargon(said), [])


class TestTheWaitIsEstimatedOnlyFromMeasurement(unittest.TestCase):
    """«Сколько ещё?» — первый вопрос ожидающего. Ответ на него можно только
    посчитать по замеренному: выдуманный срок хуже честного «не знаю», потому
    что ему верят и по нему планируют."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        self.html = lp.write_panel(self.d).read_text("utf-8")

    def test_the_estimate_uses_finished_parts_only(self):
        m = re.search(r"function drawEta\(tasks\) \{(.+?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "оценки нет вовсе")
        body = m.group(1)
        self.assertIn("t.finished", body,
                      "оценка не опирается на замер конца")
        self.assertIn("t.started", body)

    def test_without_measurements_it_says_so(self):
        self.assertIn(plain_ru.copy("eta_none"), self.html)

    def test_the_estimate_is_called_a_recount_not_a_promise(self):
        note = plain_ru.copy("eta_note")
        self.assertIn("не обещание", note)
        self.assertIn(note, self.html)

    def test_the_middle_is_used_not_the_average(self):
        """Одна застрявшая часть не должна растягивать оценку на все
        остальные — среднее это делает, середина ряда нет."""
        m = re.search(r"const typical\s*=\s*(.+?);", self.html)
        self.assertIsNotNone(m)
        self.assertIn("spans[Math.floor(spans.length / 2)]", m.group(1))


class TestTheStaleWaitIsVisible(unittest.TestCase):
    """Двадцать минут — не измерение, а выбор: меньше даёт ложную тревогу на
    обычной паузе, больше не спасает от вечера, потерянного на ожидании."""

    def test_the_threshold_is_a_named_constant(self):
        self.assertIsInstance(lp.STALE_MINUTES, int)
        self.assertGreater(lp.STALE_MINUTES, 0)

    def test_it_only_marks_the_wait_on_the_human(self):
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        html = lp.write_panel(Path(tmp)).read_text("utf-8")
        m = re.search(r"\$\('since'\)\.className\s*=\s*(.+?);", html, re.S)
        self.assertIsNotNone(m)
        self.assertIn("человек", m.group(1),
                      "жёлтым красится любое ожидание, а не ожидание человека")


class TestCommandLine(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def _run(self, *args):
        return subprocess.run([sys.executable, str(TOOL), str(self.d), *args],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_without_state_it_says_so_instead_of_showing_zeros(self):
        """Пустые полосы читаются как «ничего не происходит», а не как
        «нечего показывать». Это разные утверждения."""
        r = self._run()
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.d / lp.PANEL).exists())

    def test_with_state_it_writes_the_panel(self):
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr[-200:])
        self.assertTrue((self.d / lp.PANEL).is_file())
        self.assertIn("url", json.loads(r.stdout))


class TestTheSkillOpensThePanel(unittest.TestCase):
    """Панель, которую никто не открывает, — это файл на диске, а не панель."""

    def test_the_go_skill_starts_it(self):
        t = (PKG / "skills" / "go"
             / "SKILL.md").read_text("utf-8")
        self.assertIn("live_panel.py", t)

    def test_the_skill_records_the_phase(self):
        t = (PKG / "skills" / "go"
             / "SKILL.md").read_text("utf-8")
        self.assertIn('progress.py" phase', t,
                      "фаза нигде не записывается — панель покажет «не начато» "
                      "весь прогон")


if __name__ == "__main__":
    unittest.main()
