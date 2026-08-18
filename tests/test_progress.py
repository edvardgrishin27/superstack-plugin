#!/usr/bin/env python3
"""Тесты панели хода стройки.

Панель — самая опасная поверхность продукта: текст можно не дочитать, полоску
нельзя не увидеть. Зелёная шкала читается как факт, даже когда за ней стоит одно
слово агента «готово». Поэтому здесь проверяется не отрисовка, а отказ:
записать «доказано» без доказательства должно быть НЕВОЗМОЖНО.
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
from paths import PKG  # noqa: E402

TOOL = PKG / "tools" / "progress.py"
RENDER = PKG / "tools" / "render_html.py"
ENV = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pr = _load("ss_progress", TOOL)
rh = _load("ss_render_progress", RENDER)


def fresh() -> dict:
    return json.loads(json.dumps(pr.EMPTY))


class TestDoneRequiresProof(unittest.TestCase):
    """Единственное место, где решается, будет ли на экране сплошная полоса."""

    def test_proven_without_exit_code_is_refused(self):
        with self.assertRaises(ValueError) as e:
            pr.set_task(fresh(), "01", "задача", 1, pr.PROVEN)
        self.assertIn("код", str(e.exception).lower())

    def test_proven_with_nonzero_exit_code_is_refused(self):
        with self.assertRaises(ValueError):
            pr.set_task(fresh(), "01", "задача", 1, pr.PROVEN, exit_code=1)

    def test_proven_with_zero_is_accepted(self):
        """Обратный контроль: механизм, не пропускающий ничего, бесполезен
        так же, как пропускающий всё."""
        d = pr.set_task(fresh(), "01", "задача", 1, pr.PROVEN, exit_code=0)
        self.assertEqual(d["waves"]["1"][0]["status"], pr.PROVEN)

    def test_claimed_needs_no_proof_and_says_so(self):
        d = pr.set_task(fresh(), "01", "задача", 1, pr.CLAIMED)
        self.assertEqual(d["waves"]["1"][0]["status"], pr.CLAIMED)
        self.assertNotIn("exit_code", d["waves"]["1"][0])


class TestScaleMovesOnlyOnProof(unittest.TestCase):
    """Полоса, растущая от слов, показывает движение там, где его нет."""

    def _two(self) -> dict:
        d = pr.set_task(fresh(), "01", "a", 1, pr.PROVEN, exit_code=0)
        return pr.set_task(d, "02", "b", 1, pr.CLAIMED)

    def test_claimed_does_not_move_the_scale(self):
        s = pr.summary(self._two())
        self.assertEqual(s["progress"], 50, s)
        self.assertEqual(s["by_status"]["claimed"], 1)

    def test_basis_is_stated_on_the_number(self):
        """Доля без указания, от чего она считается, — утверждение без опоры."""
        self.assertIn("доказан", pr.summary(self._two())["progress_basis"])


class TestEmptyIsNotCleanliness(unittest.TestCase):
    """«Проверено, нечего» и «никто не смотрел» — разные утверждения.
    Показать их одинаково значит выдать неведение за порядок."""

    def test_untouched_debt_is_unreviewed(self):
        s = pr.summary(fresh())
        self.assertEqual(set(s["debt_unreviewed"]), set(pr.DEBT_KINDS))
        self.assertFalse(s["trustworthy"])

    def test_explicit_review_clears_it_without_entries(self):
        d = fresh()
        for k in pr.DEBT_KINDS:
            d = pr.review_debt(d, k)
        s = pr.summary(d)
        self.assertEqual(s["debt_unreviewed"], [])
        self.assertEqual(s["debt_total"], 0)

    def test_adding_an_entry_counts_as_having_looked(self):
        d = pr.add_debt(fresh(), "stub", "оплата заглушена")
        self.assertNotIn("stub", pr.summary(d)["debt_unreviewed"])

    def test_unreviewed_kind_is_named_in_gaps(self):
        gaps = pr.summary(fresh())["unmeasured"]
        self.assertTrue(any("никто не проверял" in g for g in gaps), gaps)

    def test_unknown_debt_kind_is_refused(self):
        with self.assertRaises(ValueError):
            pr.add_debt(fresh(), "выдуманный", "текст")


class TestClockIsAParameter(unittest.TestCase):
    """Часы внутри функции делают вердикт зависящим от дня прогона."""

    def test_timestamp_can_be_injected(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "p.json"
            pr.save(f, fresh(), now="2026-01-01T00:00:00+00:00")
            self.assertEqual(json.loads(f.read_text("utf-8"))["updated"],
                             "2026-01-01T00:00:00+00:00")

    def test_timestamp_is_written_at_all(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "p.json"
            pr.save(f, fresh())
            self.assertTrue(json.loads(f.read_text("utf-8"))["updated"],
                            "панель без отметки времени выглядит живой на старых данных")


class TestRenderSeparatesProofFromClaim(unittest.TestCase):
    """На экране два состояния обязаны быть различимы — цвета для этого нет."""

    def _page(self, data: dict) -> str:
        return rh.build_progress({**data, "summary": pr.summary(data)})

    def test_proven_and_claimed_get_different_classes(self):
        d = pr.set_task(fresh(), "01", "доказанная", 1, pr.PROVEN, exit_code=0)
        d = pr.set_task(d, "02", "заявленная", 1, pr.CLAIMED)
        page = self._page(d)
        self.assertIn('class="bar proven"', page)
        self.assertIn('class="bar claimed"', page)

    def test_proof_is_shown_as_the_exit_code(self):
        d = pr.set_task(fresh(), "01", "задача", 1, pr.PROVEN, exit_code=0)
        self.assertIn("гейт вернул 0", self._page(d))

    def test_claim_is_marked_as_hearsay(self):
        d = pr.set_task(fresh(), "01", "задача", 1, pr.CLAIMED)
        self.assertIn("со слов", self._page(d))

    def test_unreviewed_debt_reads_differently_from_clean(self):
        untouched = self._page(fresh())
        self.assertIn("никто не проверял", untouched)
        reviewed = fresh()
        for k in pr.DEBT_KINDS:
            reviewed = pr.review_debt(reviewed, k)
        self.assertIn("закрывать нечего", self._page(reviewed))
        self.assertNotIn("никто не проверял", self._page(reviewed))

    def test_missing_timestamp_is_named_not_hidden(self):
        self.assertIn("панель могла устареть", self._page(fresh()))

    #: Палитра системы целиком. Не эвристика «сколько разных символов в коде»,
    #: а список: #070612 — законный «фон-альтернатива с еле заметным холодным
    #: уходом», и правило, которое его запрещает, строже самой системы.
    PALETTE = {"000000", "070612", "0f0f14", "ffffff", "fff", "000"}

    def test_no_colour_outside_the_system_palette(self):
        """В дизайн-системе цветных акцентов не бывает: состояние передаётся
        весом и прозрачностью. Один оттенок вне палитры — начало цветовой
        иерархии, и дальше её не остановить."""
        import re
        d = pr.set_task(fresh(), "01", "задача", 1, pr.PROVEN, exit_code=0)
        page = self._page(d)
        stray = {m.group(1).lower() for m in re.finditer(r"#([0-9a-fA-F]{3,8})\b", page)}
        self.assertEqual(stray - self.PALETTE, set(),
                         "оттенок вне палитры системы")

    def test_the_palette_check_would_notice_a_colour(self):
        """Обратный контроль: правило, ничего не запрещающее, не является
        правилом. Зелёный из чужого дашборда обязан быть пойман."""
        self.assertNotIn("2f855a", self.PALETTE)


class TestCommandLine(unittest.TestCase):
    def test_show_returns_two_when_picture_is_incomplete(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "p.json"
            subprocess.run([sys.executable, str(TOOL), "init", str(f), "проект"],
                           capture_output=True, env=ENV, timeout=60)
            r = subprocess.run([sys.executable, str(TOOL), "show", str(f)],
                               capture_output=True, text=True, env=ENV, timeout=60)
            self.assertEqual(r.returncode, 2, "неполная картина выдана за полную")

    def test_bad_status_is_named_not_crashed(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "p.json"
            r = subprocess.run([sys.executable, str(TOOL), "task", str(f), "01", "имя",
                                "--wave", "1", "--status", "готово"],
                               capture_output=True, text=True, env=ENV, timeout=60)
            self.assertEqual(r.returncode, 3)
            self.assertIn("НЕ УДАЛОСЬ", r.stderr)
            self.assertNotIn("Traceback", r.stderr)


class TestThePhaseFollowsTheWork(unittest.TestCase):
    """Этап писала одна команда, задачу запускала другая, и ничто их не
    связывало.

    17.08 это выстрелило вживую: этап остался на «Дизайн-система», пока
    помощник уже писал страницу. Панель показывала записанное — то есть
    неправду, — и человек увидел на экране одно, а в разговоре другое.
    Дефект не в подписи, а в том, что подпись зависела от памяти автора.
    """

    def test_starting_work_moves_the_phase(self):
        d = pr.set_phase(fresh(), "Дизайн-система", "система")
        d = pr.set_task(d, "06", "страница", 1, pr.RUNNING)
        self.assertEqual(d["phase"]["name"], pr.BUILD_PHASE)
        self.assertEqual(d["phase"]["owner"], "исполнитель")

    def test_the_moved_phase_names_the_task(self):
        """«Пишем код» без имени задачи не отличает работу от зависания."""
        d = pr.set_task(fresh(), "06", "страница по системе", 1, pr.RUNNING)
        self.assertIn("06", d["phase"]["detail"])
        self.assertIn("страница", d["phase"]["detail"])

    def test_several_running_tasks_are_all_named(self):
        d = pr.set_task(fresh(), "02", "галерея", 2, pr.RUNNING)
        d = pr.set_task(d, "03", "слоты", 2, pr.RUNNING)
        for tid in ("02", "03"):
            self.assertIn(tid, d["phase"]["detail"])

    def test_the_clock_is_not_reset_by_a_later_edit(self):
        """Иначе каждая правка задачи обнуляет счётчик, и застрявший этап
        выглядит вечно свежим — то есть ровно наоборот."""
        d = pr.set_task(fresh(), "06", "страница", 1, pr.RUNNING)
        first = d["phase"]["since"]
        d = pr.set_task(d, "06", "страница", 1, pr.RUNNING,
                        acceptance=["добавили критерий"])
        self.assertEqual(d["phase"]["since"], first)

    def test_finishing_work_leaves_the_phase_alone(self):
        """Двигаем только вперёд и только по факту: что идёт после кода —
        решает тот, кто ведёт прогон, а не эта функция."""
        d = pr.set_task(fresh(), "06", "страница", 1, pr.RUNNING)
        d = pr.set_task(d, "06", "страница", 1, pr.CLAIMED)
        self.assertEqual(d["phase"]["name"], pr.BUILD_PHASE)


class TestHowLongItTookIsMeasured(unittest.TestCase):
    """«Сколько ещё ждать» — первый вопрос ожидающего человека, и до сих пор
    ответа на него не было ни у кого: длительность частей никто не мерил.

    Обещать срок нельзя — никто его не знает. Но сказать «прошлые части
    занимали столько-то» можно, если это замерено. Здесь заперт сам замер.
    """

    def test_leaving_work_stamps_the_end(self):
        d = pr.set_task(fresh(), "01", "каркас", 1, pr.RUNNING,
                        started="2026-08-17T10:00:00+00:00")
        d = pr.set_task(d, "01", "каркас", 1, pr.CLAIMED,
                        finished="2026-08-17T10:12:00+00:00")
        t = d["waves"]["1"][0]
        self.assertEqual(t["finished"], "2026-08-17T10:12:00+00:00")
        self.assertEqual(t["started"], "2026-08-17T10:00:00+00:00")

    def test_work_in_progress_has_no_end_yet(self):
        d = pr.set_task(fresh(), "01", "каркас", 1, pr.RUNNING)
        self.assertNotIn("finished", d["waves"]["1"][0])

    def test_reopening_clears_the_stale_measurement(self):
        """Часть вернули в работу — прежний замер стал неправдой. Оставленный,
        он попал бы в расчёт «сколько ещё» как законченный."""
        d = pr.set_task(fresh(), "01", "каркас", 1, pr.RUNNING,
                        started="2026-08-17T10:00:00+00:00")
        d = pr.set_task(d, "01", "каркас", 1, pr.CLAIMED,
                        finished="2026-08-17T10:12:00+00:00")
        d = pr.set_task(d, "01", "каркас", 1, pr.RUNNING)
        self.assertNotIn("finished", d["waves"]["1"][0])

    def test_the_measurement_survives_an_unrelated_edit(self):
        d = pr.set_task(fresh(), "01", "каркас", 1, pr.RUNNING,
                        started="2026-08-17T10:00:00+00:00")
        d = pr.set_task(d, "01", "каркас", 1, pr.CLAIMED,
                        finished="2026-08-17T10:12:00+00:00")
        d = pr.set_task(d, "01", "каркас", 1, pr.CLAIMED, goal="уточнили цель")
        self.assertEqual(d["waves"]["1"][0]["finished"],
                         "2026-08-17T10:12:00+00:00")


if __name__ == "__main__":
    unittest.main()


class TestParallelWritersDoNotEraseEachOther(unittest.TestCase):
    """Волна из двух помощников — это два процесса, пишущих один файл.

    Каждый читает состояние, меняет СВОЮ часть и записывает ЦЕЛИКОМ. Тот, кто
    записал вторым, затирает чужую правку, и оба выходят с кодом 0. Атомарной
    замены файла здесь мало: дыра находится между чтением и записью.

    18.08.2026 так исчезла отметка «проверено» у части 10: гейт вернул ноль, я
    записал результат, а параллельный процесс вернул строке «в работе». Молча,
    без единой ошибки — заметить можно было только глазами.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Path(self.tmp.name) / "state.json"
        subprocess.run([sys.executable, str(TOOL), "init", str(self.f), "проба"],
                       capture_output=True, env=ENV, timeout=60)
        for tid in ("01", "02"):
            subprocess.run([sys.executable, str(TOOL), "task", str(self.f), tid,
                            f"часть {tid}", "--wave", "1", "--status", "running"],
                           capture_output=True, env=ENV, timeout=60)

    def test_two_writers_both_survive(self):
        import threading
        done = []

        def close(tid):
            r = subprocess.run(
                [sys.executable, str(TOOL), "task", str(self.f), tid, f"часть {tid}",
                 "--status", "proven", "--exit-code", "0"],
                capture_output=True, env=ENV, timeout=60)
            done.append(r.returncode)

        threads = [threading.Thread(target=close, args=(t,)) for t in ("01", "02")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(done, [0, 0], "запись вернула не ноль")
        data = json.loads(self.f.read_text("utf-8"))
        got = {t["id"]: t["status"] for w in data["waves"].values() for t in w}
        self.assertEqual(got.get("01"), "proven", "правка первой части затёрта")
        self.assertEqual(got.get("02"), "proven", "правка второй части затёрта")

    def test_the_lock_is_released_even_on_a_refused_call(self):
        """Замок, не снятый после отказа, вешает следующую запись навсегда —
        и прогон встаёт молча, без ошибки."""
        subprocess.run([sys.executable, str(TOOL), "task", str(self.f), "01",
                        "часть 01", "--status", "готово"],
                       capture_output=True, env=ENV, timeout=60)
        r = subprocess.run([sys.executable, str(TOOL), "task", str(self.f), "01",
                            "часть 01", "--status", "claimed"],
                           capture_output=True, env=ENV, timeout=30)
        self.assertEqual(r.returncode, 0, "после отказа замок остался взятым")
