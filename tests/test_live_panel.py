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

И ещё три, добавленные позже той же логикой — каждая ловит случай, когда
панель уверенно врёт, а не молчит:

  · «частей ещё нет» — это не «всё готово»: у обоих tasks.length === 0, и
    первую половину прогона (часы) панель не имеет права путать их;
  · тревога о застревании обязана срабатывать не только на человеке — упавшая
    сессия на «Пишем код» держит ход на исполнителе так же намертво;
  · ссылку на панель нельзя выдавать раньше, чем подтверждено, что сервер
    отвечает — иначе первые минуты знакомства с продуктом человек тратит на
    мёртвую ссылку.
"""
from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import os
import re
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, REPO, skill_text  # noqa: E402

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

_a = importlib.util.spec_from_file_location(
    "ss_autonomy_panel", PKG / "tools" / "autonomy.py")
au = importlib.util.module_from_spec(_a)
_a.loader.exec_module(au)


class TestTheModeIsVisibleOnThePanel(unittest.TestCase):
    """Режим участия человек называет словами — и обязан видеть их на панели.

    Договорённость, которой не видно, к третьей части забывают обе стороны:
    человек уверен, что его спросят, система уверена, что уже не надо. Здесь
    заперт весь путь целиком — фраза, запись в состоянии, плашка на экране:
    любое из трёх звеньев в отдельности зеленеет и при мёртвом соседе.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / ".superstack"
        self.run.mkdir()
        (self.run / "state.json").write_text(
            json.dumps(pr.EMPTY, ensure_ascii=False), encoding="utf-8")

    def test_full_auto_said_in_words_reaches_the_panel(self):
        услышано = au.recognise("давай в полном автомате, я сегодня занят")
        self.assertEqual(услышано["mode"], "auto")
        au.ask(self.root, услышано["mode"], услышано["depth"])
        self.assertEqual(au.read(self.root)["mode"], "auto")
        self.assertEqual(lp.state_with_phase(self.run)["mode"]["mode"], "auto")
        html = lp.write_panel(self.run).read_text("utf-8")
        self.assertIn(plain_ru.load()["modes"]["map"]["auto"], html)


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

    def test_the_dollar_helper_is_defined_before_its_first_caller(self):
        """`let LANG = firstLang()` вызывает `snapshot()`, а та читает
        `$('snapshot')` немедленно, при первой отрисовке — до первого опроса
        сервера. Если `const $ = ...` стоит НИЖЕ этого вызова, `$` в момент
        вызова лежит в мёртвой зоне константы: на чистом профиле браузера
        (пустой localStorage, поэтому кэш языка не спасает) страница падает
        раньше первой цифры, и панель молча остаётся пустой — ничего не
        рисуется, но ни один прежний тест этого не видит, потому что все они
        проверяют ГОТОВЫЙ html, а не порядок исполнения script при загрузке.
        """
        dollar = self.html.find("const $ = (id) => document.getElementById(id);")
        first_lang_call = self.html.find("let LANG = firstLang();")
        self.assertGreater(dollar, -1, "$ нигде не объявлен")
        self.assertGreater(first_lang_call, -1, "firstLang() нигде не вызывается")
        self.assertLess(dollar, first_lang_call,
                        "$ объявлен ПОСЛЕ первого вызова firstLang() — на чистом"
                        " профиле браузера страница падает раньше первой цифры")


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
        работу. По той же причине вырезаются ВСЕ теги скрипта, а не только без
        свойств: снимок состояния лежит в теге со свойствами, и его содержимое
        — данные прогона, а не надпись, которую кто-то сочинил в разметке.
        """
        body = re.search(r"<body>(.+)</body>", self.html, re.S).group(1)
        body = re.sub(r"<script[^>]*>.+?</script>", "", body, flags=re.S)
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

    def test_no_parts_yet_is_not_reported_as_all_done(self):
        """Пока работу не разбили на части — а это вся первая половина
        прогона (просьба, спека, дизайн, план — часы), — tasks == [] и
        `left` внизу тоже 0: то же самое условие, что и «все части
        кончились». Старый код не различал их и печатал «Все части
        закончены» в первую же минуту разговора, не сделав ровно ничего."""
        m = re.search(r"function drawEta\(tasks\) \{(.+?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "оценки нет вовсе")
        body = m.group(1)
        empty_block = re.search(r"if\s*\(\s*!\s*tasks\.length\s*\)\s*\{[^}]*\}",
                                body)
        self.assertIsNotNone(
            empty_block,
            "нет отдельной ветки на случай, когда частей ещё нет вовсе")
        self.assertIn("no_tasks", empty_block.group(0),
                      "случай «частей ещё нет» не назван своими словами")
        self.assertNotIn("eta_done", empty_block.group(0),
                         "пустой список всё ещё читается как «всё сделано»")
        done_check = re.search(r"if\s*\(\s*!\s*left\s*\)", body)
        self.assertIsNotNone(done_check, "проверка «всё сделано» пропала")
        self.assertLess(
            empty_block.start(), done_check.start(),
            "проверка «частей ещё нет» обязана идти РАНЬШЕ «всё сделано» —"
            " иначе до неё просто не доходит выполнение")


class TestTheStaleWaitIsVisible(unittest.TestCase):
    """Двадцать минут — не измерение, а выбор: меньше даёт ложную тревогу на
    обычной паузе, больше не спасает от вечера, потерянного на ожидании."""

    def test_the_threshold_is_a_named_constant(self):
        self.assertIsInstance(lp.STALE_MINUTES, int)
        self.assertGreater(lp.STALE_MINUTES, 0)

    def test_the_human_specific_check_still_names_the_human(self):
        """Основная запись `$('since').className = …` — исходный механизм,
        который красит ожидание ЧЕЛОВЕКА. Он остаётся своим отдельным
        условием и после того, как рядом появилась проверка для «система»/
        «исполнитель» (см. ниже) — оба держателя красятся, но разными
        условиями и разными порогами, а не одним общим."""
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        html = lp.write_panel(Path(tmp)).read_text("utf-8")
        m = re.search(r"\$\('since'\)\.className\s*=\s*(.+?);", html, re.S)
        self.assertIsNotNone(m)
        self.assertIn("человек", m.group(1),
                      "проверка человека потеряла своё условие")

    def test_the_run_threshold_is_its_own_named_constant(self):
        """Тот же порог, что у человека, звонил бы на каждой обычной
        автоматической фазе: «Пишем код» и «Проверяем» в норме идут часами
        без единой новой записи, и это не поломка."""
        self.assertIsInstance(lp.STALE_MINUTES_RUN, int)
        self.assertGreater(lp.STALE_MINUTES_RUN, lp.STALE_MINUTES,
                           "порог для системы/исполнителя обязан быть мягче"
                           " человеческого — иначе обычная долгая"
                           " автоматическая фаза выглядит забытой")

    def test_a_dead_run_is_also_marked_stale(self):
        """Упавшая сессия или уснувший ноутбук на этапе «Пишем код» держит
        ход на «система» или «исполнитель» так же намертво, как на человеке.
        Раньше тревога умела срабатывать только когда ждут человека, и
        застрявший прогон выглядел живым бесконечно долго."""
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        html = lp.write_panel(Path(tmp)).read_text("utf-8")
        m = re.search(
            r"if\s*\(\((.+?)\)\s*\{\s*\$\('since'\)\.className \+= '([^']*)';",
            html, re.S)
        self.assertIsNotNone(
            m, "нет отдельной проверки застревания для «система»/«исполнитель»")
        cond, added = m.group(1), m.group(2)
        self.assertIn("система", cond)
        self.assertIn("исполнитель", cond)
        self.assertIn("STALE_RUN_MS", cond,
                      "проверка не использует отдельный (более мягкий) порог")
        self.assertIn("stale-run", added.split(),
                      "держатель красится, но не своим классом — получит"
                      " человеческий текст вместо адресного")

    def test_the_stale_run_text_is_its_own_key_not_the_human_one(self):
        """Держатели «система»/«исполнитель» не ждут человека — слово «ждём»
        в их тексте было бы не про то. У них свой ключ словаря (`stale_run`),
        а `waiting_long` остаётся только за человеком."""
        said = plain_ru.copy("stale_run")
        human_said = plain_ru.copy("waiting_long")
        self.assertTrue(said, "в словаре нет отдельного текста для «система»/"
                        "«исполнитель» — используется общий человеческий")
        self.assertNotEqual(
            said, human_said,
            "текст застревания «система»/«исполнитель» совпал с человеческим"
            " — адресный текст подменили обратно на общий")

    def test_the_stale_run_text_does_not_claim_death(self):
        """Панель не может отличить упавшую сессию от помощника, который
        просто долго думает — значит, ей нельзя утверждать «умер». Текст
        обязан называть только то, что известно (сколько не менялось), а не
        ставить диагноз."""
        said = plain_ru.copy("stale_run")
        self.assertTrue(said, "в словаре нет текста застревания для «система»/"
                        "«исполнитель»")
        for word in ("умер", "упал", "сдох", "мёртв", "крашн"):
            self.assertNotIn(word, said.lower())

    def test_the_stale_run_text_is_wired_into_the_page(self):
        """Ключ в словаре без подстановки в HTML — мёртвая буква: правку
        текста никто не увидит, а тест на словарь будет зелёным."""
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        html = lp.write_panel(Path(tmp)).read_text("utf-8")
        said = plain_ru.copy("stale_run")
        m = re.search(r"\.since\.stale-run::after\s*\{\s*content:\s*\"([^\"]*)\"",
                      html)
        self.assertIsNotNone(m, "нет отдельного правила оформления для"
                             " .stale-run")
        self.assertEqual(m.group(1), said,
                         "текст в разметке разошёлся со словарём")


class TestTheLinkIsGivenOnlyAfterTheServerAnswers(unittest.TestCase):
    """`--serve` в прогоне всегда запускают отвязанным (`nohup … &`), и весь
    его вывод летит в /dev/null — если порт занят, узнать об этом изнутри
    того же процесса неоткуда. Раньше ссылку печатали ДО попытки привязать
    порт: человек получал рабочую на вид ссылку на мёртвый сервер в первые
    же минуты знакомства с продуктом."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        lp.write_panel(self.d)

    def _run(self, *args):
        return subprocess.run([sys.executable, str(TOOL), str(self.d), *args],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    @staticmethod
    def _free_port() -> int:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((lp.HOST, 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def _spawn_serve(self, port: int):
        proc = subprocess.Popen(
            [sys.executable, str(TOOL), str(self.d), "--serve", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

        def _stop():
            proc.kill()
            proc.wait(timeout=10)
        self.addCleanup(_stop)
        return proc

    def _start_decoy_server(self, content: bytes) -> int:
        """Поднять чужой HTTP-сервер, который отвечает не тем, чем панель."""
        decoy_dir = tempfile.mkdtemp()
        (Path(decoy_dir) / lp.PANEL).write_bytes(content)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                    directory=decoy_dir)
        srv = socketserver.TCPServer((lp.HOST, 0), handler)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        def _stop():
            srv.shutdown()
            thread.join(timeout=5)
            srv.server_close()
        self.addCleanup(_stop)
        return srv.server_address[1]

    def test_a_busy_port_gets_no_url_and_a_named_exit_code(self):
        """Порт занят чужим приложением ДО того, как `--serve` пытается его
        привязать. Раньше ссылку успевали напечатать ДО попытки привязки —
        здесь этого быть не должно вовсе."""
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind((lp.HOST, 0))
        port = blocker.getsockname()[1]
        blocker.listen(1)
        self.addCleanup(blocker.close)

        r = self._run("--serve", str(port))
        self.assertNotIn('"url"', r.stdout,
                         "ссылку напечатали, хотя порт был занят и сервер"
                         " не поднялся")
        self.assertEqual(r.returncode, lp.PORT_BUSY)

    def test_check_refuses_to_confirm_when_nothing_is_actually_serving(self):
        """Раньше `--check` не существовал вовсе: `plain = [a for a in argv
        if not a.startswith('--')]` тихо выбрасывал незнакомый флаг, и вызов
        `<каталог> --check <порт>` откатывался к обычной генерации — код 0 и
        ссылка на порт, где никто не слушает."""
        port = self._free_port()
        r = self._run("--check", str(port))
        self.assertNotEqual(r.returncode, 0,
                            "подтвердили несуществующую панель на пустом порту")
        self.assertNotIn('"url"', r.stdout)

    def test_panel_is_live_reports_absence_when_nothing_listens(self):
        original = (lp.CHECK_BUDGET_SECONDS, lp.CHECK_POLL_SECONDS)

        def _restore():
            lp.CHECK_BUDGET_SECONDS, lp.CHECK_POLL_SECONDS = original
        self.addCleanup(_restore)
        lp.CHECK_BUDGET_SECONDS, lp.CHECK_POLL_SECONDS = 0.3, 0.1

        port = self._free_port()
        ok, why = lp.panel_is_live(self.d, port)
        self.assertFalse(ok)
        self.assertIn(str(port), why)

    def test_panel_is_live_detects_a_different_occupant(self):
        """Порт бывает занят не только тишиной: чужой сервер тоже ответит, но
        не той страницей, которую write_panel только что записал на диск.
        Сверка идёт по байтам, а не по факту хоть какого-то ответа."""
        port = self._start_decoy_server(b"not the real panel")
        ok, why = lp.panel_is_live(self.d, port)
        self.assertFalse(ok)
        self.assertIn("не эта панель", why)

    def test_check_confirms_a_real_serve_and_hands_out_the_matching_link(self):
        port = self._free_port()
        self._spawn_serve(port)
        r = self._run("--check", str(port))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"localhost:{port}/{lp.PANEL}", r.stdout)


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


class TestThePanelLivesWithoutAServer(unittest.TestCase):
    """Панель, живущая только вместе с процессом, — не панель, а окно.

    Сервер умирает вместе с сессией, вкладку закрывают, ноутбук перезагружают,
    и назавтра по той же ссылке пусто. Читать соседний файл с диска браузер не
    даёт, поэтому состояние едет ВНУТРИ страницы: каждая запись хода работ
    вписывает снимок между границами, и файл, открытый двойным кликом,
    показывает последнюю запись без всякого сервера.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / ".superstack"
        self.run.mkdir()
        self.state = self.run / "state.json"

    def _progress(self, *args):
        return subprocess.run(
            [sys.executable, str(PKG / "tools" / "progress.py"), *args],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_a_recorded_phase_lands_in_the_page_as_a_snapshot(self):
        """Ни сервера, ни запроса: только запись хода работ — и страница уже
        знает этап. Проверяется РАЗОБРАННЫЙ снимок, а не вхождение названия в
        файл: имя этапа встречается в разметке и просто как элемент списка."""
        self._progress("init", str(self.state), "проба")
        r = self._progress("phase", str(self.state), "Дизайн-система", "человек",
                           "--detail", "ушёл собирать правила внешнего вида")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])

        html = (self.run / lp.PANEL).read_text("utf-8")
        a = html.find(lp.SNAPSHOT_BEGIN)
        b = html.find(lp.SNAPSHOT_END)
        self.assertGreater(b, a, "в странице нет границ снимка")
        внутри = html[a + len(lp.SNAPSHOT_BEGIN):b]
        снимок = json.loads(re.search(r'id="snapshot">(.*)</script>',
                                      внутри, re.S).group(1))
        self.assertEqual(снимок["phase"]["name"], "Дизайн-система")
        self.assertEqual(снимок["phase"]["owner"], "человек")

    def test_the_page_speaks_both_languages(self):
        """Панель уезжает человеку одним файлом, и кто его откроет — заранее
        не известно. Второй язык обязан ехать вместе с ней: догружать словарь
        с диска странице неоткуда."""
        self.state.write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        html = lp.write_panel(self.run).read_text("utf-8")
        self.assertIn('data-lang="en"', html, "переключателя языка нет")
        self.assertIn('data-lang="ru"', html)
        английские = plain_ru.english()["names"]
        for этап in lp.PHASES:
            with self.subTest(phase=этап):
                self.assertIn(английские[этап], html,
                              f"этап «{этап}» некому назвать по-английски")


class TestOpeningTheFileIsAnExitCode(unittest.TestCase):
    """«Открыл» без проверки — подтверждение раньше результата.

    Открывателя может не быть вовсе: машина без графической оболочки, урезанный
    образ, отсутствующий `xdg-open`. Тогда честный ответ — код и путь словами, а
    не бодрая строка про открытое окно, которого никто не видел.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        self.открыватель = self.d / "открыватель.sh"

    def _run(self, код):
        self.открыватель.write_text(f"#!/bin/sh\nexit {код}\n", encoding="utf-8")
        self.открыватель.chmod(0o755)
        return subprocess.run(
            [sys.executable, str(TOOL), str(self.d), "--open"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
                 lp.OPENER_ENV: str(self.открыватель)})

    def test_a_refusing_opener_is_reported_not_swallowed(self):
        r = self._run(1)
        self.assertEqual(r.returncode, lp.OPEN_FAILED, r.stderr[-300:])
        self.assertIn(str(self.d / lp.PANEL), r.stderr,
                      "путь к файлу не назван — человеку нечего открыть самому")

    def test_a_working_opener_returns_zero(self):
        self.assertEqual(self._run(0).returncode, 0)


class TestTheSkillOpensThePanel(unittest.TestCase):
    """Панель, которую никто не открывает, — это файл на диске, а не панель."""

    def test_the_go_skill_starts_it(self):
        t = skill_text("go")
        self.assertIn("live_panel.py", t)

    def test_the_skill_records_the_phase(self):
        t = skill_text("go")
        self.assertIn('progress.py" phase', t,
                      "фаза нигде не записывается — панель покажет «не начато» "
                      "весь прогон")

    def test_the_skill_verifies_before_handing_out_the_link(self):
        """`--serve` уходит в фон через `nohup … &`, и весь его вывод летит в
        /dev/null — если порт занят, узнать об этом изнутри того же процесса
        неоткуда. Фаза обязана дожидаться `--check` отдельной командой, а не
        печатать ссылку сразу следом за запуском.

        Подстрока `--check` сама по себе ничего не доказывает: она уже
        встречается в других фазах у других инструментов (`criteria.py`,
        `deny_list.py`, `design_pull.py`) — проверяем именно вызов
        `live_panel.py … --check`, а не факт, что где-то в скилле есть флаг с
        таким именем."""
        t = skill_text("go")
        self.assertIn('live_panel.py" .superstack --check', t,
                      "ссылка выдаётся без подтверждения, что сервер отвечает")


if __name__ == "__main__":
    unittest.main()


class ВедомостьИЛентаДоезжаютДоСтраницы(unittest.TestCase):
    """Панель обязана отвечать не «сколько процентов», а «что мне обещали и
    что из этого подтверждено машиной».

    Ведомость двойной записи — слева обещание словами человека, справа код
    возврата — единственное место, где обещание и подтверждение стоят рядом и
    не могут подтвердить друг друга. Лента «что произошло» держит их во
    времени. Оба блока обязаны быть В ФАЙЛЕ: страница уезжает человеку одним
    файлом и открывается без сервера.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / ".superstack"
        self.run.mkdir()
        self.state = self.run / "state.json"

    def _progress(self, *args):
        r = subprocess.run(
            [sys.executable, str(PKG / "tools" / "progress.py"), *args],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        return r

    def test_a_live_run_lands_in_the_page_with_its_blocks_and_its_share(self):
        (self.run / "manifest.json").write_text(json.dumps({
            "schema": "superstack.manifest.v1",
            "requirements": [
                {"id": "R01", "kind": "explicit", "status": "done",
                 "quote": "чтобы клиент выбирал мастера", "basis": ""},
                {"id": "R02", "kind": "explicit", "status": "in-ticket",
                 "quote": "записаться без регистрации", "basis": ""},
            ]}, ensure_ascii=False), encoding="utf-8")
        self._progress("init", str(self.state), "запись к парикмахеру")
        self._progress("phase", str(self.state), "Пишем код", "исполнитель")
        self._progress("task", str(self.state), "01", "Выбор мастера",
                       "--wave", "1", "--status", "proven", "--exit-code", "0",
                       "--requirements", "R01",
                       "--started", "2026-09-03T13:00:00+00:00",
                       "--finished", "2026-09-03T13:29:00+00:00")
        self._progress("task", str(self.state), "02", "Форма записи",
                       "--wave", "1", "--status", "claimed",
                       "--requirements", "R02",
                       "--started", "2026-09-03T13:30:00+00:00",
                       "--finished", "2026-09-03T13:58:00+00:00")

        html = (self.run / lp.PANEL).read_text("utf-8")
        for блок in ("ledger", "feed", "you", "ticks", "saidbar"):
            with self.subTest(block=блок):
                self.assertIn(f'id="{блок}"', html, f"на странице нет «{блок}»")
        for ключ in ("ledger_rubric", "ledger_left", "ledger_right",
                     "feed_rubric", "you_rubric", "fine_note", "ruler_said"):
            with self.subTest(key=ключ):
                self.assertIn(plain_ru.copy(ключ), html,
                              f"надписи «{ключ}» нет в файле")

        внутри = html[html.find(lp.SNAPSHOT_BEGIN):html.find(lp.SNAPSHOT_END)]
        снимок = json.loads(re.search(r'id="snapshot">(.*)</script>',
                                      внутри, re.S).group(1))
        факты = снимок["facts"]
        # Одна часть из двух доказана, вторая названа готовой. Доля — ровно
        # половина: слово помощника не двигает её ни на процент.
        self.assertEqual(факты["parts"]["percent_proven"], 50)
        self.assertEqual(факты["parts"]["gap"], 1)
        # Ведомость: слева слова человека, справа — код возврата и «со слов».
        состояния = {r["id"]: r["state"] for r in факты["ledger"]}
        self.assertEqual(состояния, {"R01": "proven", "R02": "claimed"})
        self.assertEqual(факты["ledger"][0]["code"], 0)
        # У «со слов» на правой стороне не должно быть ни одного числа —
        # цифра рядом со словом читается как измерение.
        сказано = next(r for r in факты["ledger"] if r["state"] == "claimed")
        self.assertNotIn("code", сказано)
        self.assertIn("proven", {e["key"] for e in факты["events"]},
                      "лента не знает о проверке, которая уже прошла")


class TestThePanelAdaptsToTheScreen(unittest.TestCase):
    """Панель уезжает человеку одним файлом и открывается на чём угодно — от
    телефона до широкого монитора. Без точек перелома вёрстка, рассчитанная
    на 1440px, ломается на первом же экране уже. Три точки:

      · ≤600px  — четыре числа в столбик, поля рельса и сцены уже;
      · ≤1000px или ≤1100px — числа 2×2, а рельс станций уходит наверх;
      · ≤720px  — ведомость двойной записи из двух колонок становится
        строкой обещания над строкой подтверждения.

    Проверяется здесь БУКВОЙ: `@media` с этими порогами вообще есть в
    собранной странице. То, что от них ничего не отваливается по ширине, —
    отдельная ручная проверка снимком headless Chrome (см. отчёт), а не
    то, что можно пришпилить как код возврата к строке CSS.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        self.html = lp.write_panel(self.d).read_text("utf-8")

    def test_three_breakpoints_are_present(self):
        for width in ("37.5rem", "45rem"):
            with self.subTest(width=width):
                self.assertIn(f"@media (max-width: {width})", self.html,
                              f"нет точки перелома на {width}")
        self.assertTrue(
            re.search(r"@media \(max-width: (62\.5|68\.75)rem\)", self.html),
            "нет точки перелома у ряда чисел или у рельса станций"
            " (ожидались 1000px или 1100px)")


class TestTheYouBarSaysItOnce(unittest.TestCase):
    """Полоса «от тебя» раньше несла две строки — «ход/ничего не нужно» и
    «долг/ничего» — и в частом случае (не твой ход, долга нет) обе говорили
    одно и то же разными словами: «сейчас от тебя ничего не нужно» плюс
    «пока ничего — заглушки … соберём к сдаче» рядом. Теперь ровно один
    заголовок и одна фраза, а решает, что важнее — ход, долг или честное
    «ничего», — сама drawYou()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)
        (self.d / "state.json").write_text(json.dumps(pr.EMPTY), encoding="utf-8")
        self.html = lp.write_panel(self.d).read_text("utf-8")

    def test_there_is_only_one_value_line_in_the_you_block(self):
        self.assertEqual(self.html.count('id="youv"'), 1,
                         "в полосе «от тебя» больше одной строки значения")
        self.assertNotIn('id="debt"', self.html,
                         "старый второй блок «что нужно от тебя» не убран")
        self.assertNotIn('id="debtrub"', self.html,
                         "старый второй блок «что нужно от тебя» не убран")

    def test_the_function_picks_one_reason_by_priority(self):
        m = re.search(r"function drawYou\(s\) \{(.+?)\n\}", self.html, re.S)
        self.assertIsNotNone(m, "drawYou нет вовсе")
        body = m.group(1)
        # Ход важнее долга, долг важнее «ничего» — и решение принимается ОДИН
        # раз в if/else if/else, а не тремя независимыми присваиваниями,
        # которые могли бы напечататься все разом.
        self.assertIn("you_turn", body)
        self.assertIn("debt.phrase", body)
        self.assertIn("you_nothing", body)
        self.assertRegex(body, r"if\s*\(mine\)[\s\S]+else if\s*\(debt\.total\)[\s\S]+else\b")
