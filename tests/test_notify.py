#!/usr/bin/env python3
"""Отчёт догоняет человека там, где его нет за компьютером.

Автономия без исходящего канала ненастоящая: система может работать час, но
если итог лежит в терминале, человек всё равно привязан к столу — он либо
ждёт, либо возвращается проверять.

Здесь заперты четыре отказа:

  · канал не объявлен, а доставка «работает» — молчание неотличимо от успеха
    ровно до того дня, когда оно понадобится;
  · секрет уезжает наружу: исходящий канал — самый дешёвый способ вынести
    ключ с машины;
  · «отправлено» без ответа сервера — это «вызвали функцию»;
  · сбой доставки отменяет сделанное.

Сеть здесь не трогается: отправитель подаётся снаружи. Проверка, стучащаяся
в интернет, измеряет чужой сервер, а не наш код.
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
from paths import at  # noqa: E402

NOTIFY = at("tools", "notify.py")
_s = importlib.util.spec_from_file_location("ss_notify", NOTIFY)
nt = importlib.util.module_from_spec(_s)
_s.loader.exec_module(nt)


class Канал(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".superstack").mkdir()
        self.ушло = []

    def _спека(self, d: dict) -> None:
        (self.root / ".superstack" / "notify.json").write_text(
            json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def _отправитель(self, код: int = 200, ошибка: str = ""):
        def шлём(url, тело):
            self.ушло.append((url, тело))
            return (код, ошибка)
        return шлём


class НетКанала(Канал):

    def test_не_объявлен_это_не_тихо_ок(self):
        v = nt.send(self.root, "итог", self._отправитель())
        self.assertEqual(v["status"], "unknown")
        self.assertEqual(self.ушло, [])

    def test_http_не_принимается(self):
        """По http итог уедет открытым текстом."""
        self._спека({"webhook": "http://пример.рф/хук"})
        v = nt.send(self.root, "итог", self._отправитель())
        self.assertEqual(v["status"], "unknown")
        self.assertIn("https", v["detail"])

    def test_выключен_намеренно_это_успех(self):
        """Обратный контроль: человек имеет право не хотеть уведомлений."""
        self._спека({"quiet": True})
        v = nt.send(self.root, "итог", self._отправитель())
        self.assertEqual(v["status"], "pass")
        self.assertFalse(v["delivered"])
        self.assertEqual(self.ушло, [])


class СекретыНеУезжают(Канал):

    def test_ключ_в_тексте_вырезается(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        секрет = "ghp_" + "a" * 36
        v = nt.send(self.root, f"готово, токен {секрет} записан",
                    self._отправитель())
        self.assertEqual(v["status"], "pass")
        ушедший = json.dumps(self.ушло[0][1], ensure_ascii=False)
        self.assertNotIn(секрет, ушедший)
        self.assertIn("вырезано", ушедший)
        self.assertEqual(v["redacted"], 1)

    def test_пароль_в_команде_вырезается(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "запускал sshpass -p 'тайна' ssh root@х",
                    self._отправитель())
        self.assertNotIn("тайна", json.dumps(self.ушло[0][1], ensure_ascii=False))
        self.assertGreaterEqual(v["redacted"], 1)

    def test_обычный_текст_не_портится(self):
        """Редактура, режущая всё подряд, делает отчёт нечитаемым."""
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "собрал форму записи, тесты зелёные",
                    self._отправитель())
        self.assertEqual(v["redacted"], 0)
        self.assertIn("тесты зелёные", json.dumps(self.ушло[0][1],
                                                  ensure_ascii=False))


class ДоставкаПодтверждается(Канал):

    def test_ошибка_канала_это_не_отправлено(self):
        """«Отправлено» без ответа сервера — это «вызвали функцию»."""
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "итог", self._отправитель(500, "канал ответил 500"))
        self.assertEqual(v["status"], "fail")

    def test_недоступный_канал_не_отменяет_работу(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "итог", lambda u, t: (None, "канал недоступен"))
        self.assertEqual(v["status"], "fail")
        self.assertIn("не отменяется", v["next"])

    def test_успех_несёт_код_ответа(self):
        self._спека({"webhook": "https://пример.рф/хук"})
        v = nt.send(self.root, "итог", self._отправитель(204))
        self.assertEqual(v["status"], "pass")
        self.assertEqual(v["code"], 204)


class ЧеловекНеСоставитФайлСам(unittest.TestCase):
    """Формулировка отказа — до файловой системы и до подпроцесса.

    Не-разработчик даст то, что скопировал, а не то, что примет парсер URL.
    `link_problem` ловит три реальных промаха и называет их не техническим
    словом, а тем, что человек сам мог увидеть в своей ссылке.
    """

    def test_пустая_ссылка_названа_пустой(self):
        self.assertIn("пусто", nt.link_problem(""))

    def test_фраза_вместо_ссылки_названа_фразой(self):
        self.assertIn("фраз", nt.link_problem("вот сюда, наверное, пиши мне"))

    def test_http_назван_без_защиты(self):
        беда = nt.link_problem("http://пример.рф/хук")
        self.assertIn("http", беда)

    def test_годная_ссылка_проблем_не_имеет(self):
        self.assertEqual(nt.link_problem("https://пример.рф/хук12345"), "")


class КомандаSetИOff(Канал):
    """`--set`/`--off` — единственный способ для не-разработчика завести канал:
    файл `{"webhook": "..."}` руками он не составит."""

    def _run(self, *args):
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(
            [sys.executable, str(NOTIFY), str(self.root), *args],
            capture_output=True, text=True, timeout=30, env=env)

    def test_валидная_ссылка_пишет_файл_настроек(self):
        p = self._run("--set", "https://hooks.example.com/abc123")
        self.assertEqual(p.returncode, 0, p.stderr)
        спека = json.loads((self.root / ".superstack" / "notify.json")
                           .read_text("utf-8"))
        self.assertEqual(спека, {"webhook": "https://hooks.example.com/abc123"})

    def test_отказ_пишет_quiet(self):
        """«Не нужно» — тоже ответ, и он тоже записывается инструментом."""
        p = self._run("--off")
        self.assertEqual(p.returncode, 0, p.stderr)
        спека = json.loads((self.root / ".superstack" / "notify.json")
                           .read_text("utf-8"))
        self.assertEqual(спека, {"quiet": True})

    def test_записанный_через_set_канал_реально_работает(self):
        """Не только файл появился — send() читает именно то, что записал --set."""
        p = self._run("--set", "https://hooks.example.com/abc123")
        self.assertEqual(p.returncode, 0, p.stderr)
        v = nt.send(self.root, "итог", self._отправитель(200))
        self.assertEqual(v["status"], "pass")
        self.assertEqual(self.ушло[0][0], "https://hooks.example.com/abc123")

    def test_фраза_вместо_ссылки_отклонена_словами_а_не_молча(self):
        p = self._run("--set", "не знаю что тебе дать")
        self.assertEqual(p.returncode, 3)
        self.assertIn("не на ссылку", p.stderr)
        self.assertFalse((self.root / ".superstack" / "notify.json").exists())

    def test_http_без_защиты_отклонён(self):
        p = self._run("--set", "http://hooks.example.com/abc")
        self.assertEqual(p.returncode, 3)
        self.assertIn("защиты", p.stderr)

    def test_пустая_ссылка_отклонена(self):
        p = self._run("--set", "")
        self.assertEqual(p.returncode, 3)
        self.assertIn("пусто", p.stderr)


class Фаза0СпрашиваетПроКанал(unittest.TestCase):
    """Дефект: не-разработчик никогда не создаст `.superstack/notify.json`
    руками — значит, фаза, где заводится всё остальное, обязана сама спросить
    человека и записать ответ инструментом, а не оставить это ему."""

    def setUp(self):
        путь = at("skills", "go", "phases", "0-завести.md")
        self.текст = путь.read_text("utf-8")

    def test_фаза_спрашивает_про_канал_простыми_словами(self):
        """Один тест, а не два: и присутствие вопроса, и его словарь — часть
        одного и того же требования. Раздельные тесты позволили бы «словарь
        чист» пройти и на старом файле, где вопроса нет вовсе — пройти можно
        было бы пустотой, а не соблюдением правила.

        «вебхук»/«JSON»/«эндпоинт» — слова, которых нет в словаре plain_ru,
        поэтому автоматическая проверка их не ловит; здесь их отсутствие
        проверяется буквально. Имя файла `notify.json` в примере команды не
        в счёт — это часть технического пути в bash-блоке, а не слово,
        которое произносят человеку, поэтому «json» ищем как отдельное слово,
        а не как хвост имени файла.
        """
        self.assertIn("notify.py", self.текст)
        self.assertIn("--set", self.текст)
        self.assertIn("--off", self.текст)
        # Отказ — тоже допустимый ответ, а не тупик диалога.
        self.assertIn("не нужно", self.текст)

        нижний = self.текст.lower()
        for слово in ("вебхук", "эндпоинт"):
            self.assertNotIn(слово, нижний,
                             f"«{слово}» непонятно человеку без разработки")
        self.assertNotRegex(нижний, r"(?<!\.)\bjson\b",
                            "«json» как отдельное слово непонятно человеку "
                            "без разработки (имена файлов вроде notify.json "
                            "сюда не относятся)")


class ФазыЗовутЧеловекаПриПередачеХода(unittest.TestCase):
    """Единственным сигналом, что ход перешёл к человеку, была подсветка на
    панели, которую отошедший от экрана не видит. Фазы, где это происходит
    посреди работы, обязаны сами послать короткий внешний сигнал."""

    def test_фаза4_зовёт_когда_нужен_ответ_человека(self):
        текст = at("skills", "go", "phases", "4-работа.md").read_text("utf-8")
        self.assertIn("notify.py", текст)
        self.assertIn("Ход переходит к человеку", текст)

    def test_фаза6_зовёт_при_расхождении_приёмки(self):
        текст = at("skills", "go", "phases", "6-приёмка.md").read_text("utf-8")
        self.assertIn("notify.py", текст)
        self.assertIn("тоже повод его позвать", текст)


if __name__ == "__main__":
    unittest.main()
