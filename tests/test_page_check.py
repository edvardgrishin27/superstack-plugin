#!/usr/bin/env python3
"""Можно ли показывать страницу человеку — вопрос отдельный от «сходятся ли числа».

Откуда взялся этот набор.

Страница прошла всё: четыре токена системы совпали значение в значение, теней
нет, отступы кратны четырём, контраст выше нормы, 87 тестов зелёные, три
линтера вернули ноль. Человек открыл её и сказал: «выглядит убого, словно
вообще не сделали».

Обе стороны были правы. Проверки считали СООТВЕТСТВИЕ ПРАВИЛАМ, человек смотрел
на РЕЗУЛЬТАТ, и между ними помещался целый класс провалов: страница, где каждый
элемент по системе, а смотреть не на что — семь подписей «[ВПИШИ: ...]» и пять
пустых блоков вместо содержимого.

Заглушки при этом правильны: выдумывать за человека цену и адрес запрещено.
Ошибка не в них, а в том, что каркас показали как результат вместо того, чтобы
спросить факты.
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
from paths import PKG  # noqa: E402

TOOL = PKG / "tools" / "page_check.py"
_s = importlib.util.spec_from_file_location("ss_page_check", TOOL)
pc = importlib.util.module_from_spec(_s)
_s.loader.exec_module(pc)


def project(tmp: str, page: str, extra: dict = None) -> Path:
    root = Path(tmp)
    (root / "src").mkdir(exist_ok=True)
    (root / "index.html").write_text(page, encoding="utf-8")
    for name, text in (extra or {}).items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


class TestAFrameIsNotAPage(unittest.TestCase):
    """Грубое различение намеренно: тонкие суждения о красоте машине не по
    силам, а отличить наполненную страницу от решётки заглушек — по силам."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_a_page_full_of_placeholders_is_refused(self):
        root = project(self.tmp.name, """
          <h1>[ВПИШИ: название студии]</h1>
          <p>[ВПИШИ: адрес студии]</p>
          <p>[ВПИШИ: телефон]</p>
          <p>[ВПИШИ: цена мастер-класса]</p>
        """)
        v = pc.scan(root)
        self.assertEqual(v["status"], "fail")
        self.assertGreater(v["placeholder_count"], pc.PLACEHOLDER_LIMIT)

    def test_a_filled_page_passes(self):
        root = project(self.tmp.name, """
          <h1>Студия Глина</h1>
          <p>улица Гончарная, 12</p>
          <p>+7 900 000-00-00</p>
          <img src="work.webp" width="800" height="600" alt="чаша">
        """)
        self.assertEqual(pc.scan(root)["status"], "pass")

    def test_one_or_two_gaps_are_normal(self):
        """Незаполненное место — обычное состояние работы. Каркасом страницу
        делает их количество, а не сам факт."""
        root = project(self.tmp.name, """
          <h1>Студия Глина</h1>
          <p>[ВПИШИ: телефон]</p>
        """)
        self.assertEqual(pc.scan(root)["status"], "pass")

    def test_the_example_in_a_comment_is_not_a_gap(self):
        """«[ВПИШИ: ...]» в комментарии — образец записи, а не пустое место:
        считать его значило бы просить человека заполнить многоточие."""
        root = project(self.tmp.name, "<h1>Студия Глина</h1>",
                       {"src/content.ts": "// вида «[ВПИШИ: ...]»\n"
                                          "export const A = '[ВПИШИ: телефон]';"})
        v = pc.scan(root)
        self.assertNotIn("...", v["placeholders"])

    def test_tests_do_not_count_as_the_page(self):
        """Тестовый файл отражает страницу, но не является ею: его вхождения
        удвоили бы счёт и превратили нормальную страницу в каркас."""
        root = project(self.tmp.name, "<h1>Студия Глина</h1>",
                       {"src/page.test.ts": "'[ВПИШИ: а]' '[ВПИШИ: б]' '[ВПИШИ: в]'"})
        self.assertEqual(pc.scan(root)["status"], "pass")


class TestItSaysWhatToAsk(unittest.TestCase):
    """Вердикт «каркас» без списка вопросов оставляет человека там же, где он
    был: он видит отказ и не знает, что от него нужно."""

    def test_every_gap_becomes_a_question(self):
        with tempfile.TemporaryDirectory() as t:
            root = project(t, """
              <h1>[ВПИШИ: название студии]</h1>
              <p>[ВПИШИ: цена мастер-класса]</p>
              <p>[ВПИШИ: адрес студии]</p>
            """)
            ask = pc.what_to_ask(pc.scan(root))
            self.assertIn("как называется студия", ask)
            self.assertIn("сколько стоит занятие", ask)

    def test_questions_are_asked_in_plain_russian(self):
        import importlib.util as iu
        s = iu.spec_from_file_location(
            "ss_plain_page", PKG / "tools" / "plain_ru.py")
        ru = iu.module_from_spec(s)
        s.loader.exec_module(ru)
        with tempfile.TemporaryDirectory() as t:
            root = project(t, "<h1>[ВПИШИ: название студии]</h1>")
            for q in pc.what_to_ask(pc.scan(root)):
                self.assertEqual(ru.find_jargon(q), [])


class TestExitCodes(unittest.TestCase):

    def _run(self, path):
        return subprocess.run([sys.executable, str(TOOL), str(path)],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})

    def test_a_frame_returns_one(self):
        with tempfile.TemporaryDirectory() as t:
            project(t, "[ВПИШИ: а] [ВПИШИ: б] [ВПИШИ: в] [ВПИШИ: г]")
            self.assertEqual(self._run(t).returncode, 1)

    def test_nothing_to_look_at_returns_two(self):
        """«Страницы нет» и «страница плоха» — разные утверждения."""
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(self._run(t).returncode, 2)

    def test_a_missing_directory_returns_three(self):
        self.assertEqual(self._run("/нет/такого/каталога").returncode, 3)


def bare(tmp: str, files: dict) -> Path:
    """Проект без index.html: только то, что названо."""
    root = Path(tmp)
    for name, text in files.items():
        f = root / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return root


FIVE = " ".join(f"[ВПИШИ: пункт {i}]" for i in range(5))


class TestAllInterfaceFilesAreRead(unittest.TestCase):
    """Проект на .vue с пятью заглушками раньше получал «можно показывать»:
    инструмент не открывал файлы, где лежит содержимое."""

    def test_other_interface_formats_are_read(self):
        for ext in ("vue", "jsx", "svelte", "astro", "md"):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as t:
                root = bare(t, {f"components/A.{ext}": FIVE})
                v = pc.scan(root)
                self.assertEqual(v["status"], "fail", msg=str(v))

    def test_nested_page_outside_src_is_read(self):
        with tempfile.TemporaryDirectory() as t:
            root = bare(t, {"pages/x.html": "[ВПИШИ: а] [ВПИШИ: б] [ВПИШИ: в]"})
            self.assertEqual(pc.scan(root)["status"], "fail")

    def test_trash_directories_are_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            root = bare(t, {"node_modules/p/a.js": FIVE, "index.html": "<h1>Привет</h1>"})
            self.assertEqual(pc.scan(root)["status"], "pass")

    def test_the_cli_returns_one_for_a_vue_frame(self):
        with tempfile.TemporaryDirectory() as t:
            bare(t, {"components/A.vue": FIVE})
            r = subprocess.run([sys.executable, str(TOOL), t],
                               capture_output=True, text=True, timeout=60,
                               env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
            self.assertEqual(r.returncode, 1)

    def test_an_ordinary_src_project_with_one_placeholder_passes(self):
        with tempfile.TemporaryDirectory() as t:
            root = bare(t, {"src/App.tsx": "<p>[ВПИШИ: телефон]</p>"})
            self.assertEqual(pc.scan(root)["status"], "pass")


class TestUnreadIsNotGreen(unittest.TestCase):
    """Файл, который не удалось прочитать, не даёт сказать «можно показывать»."""

    def test_an_oversized_file_gives_unknown(self):
        with tempfile.TemporaryDirectory() as t:
            root = bare(t, {"src/Big.tsx": "x" * 200, "src/Ok.tsx": "<p>ok</p>"})
            old = pc.MAX_READ_BYTES
            pc.MAX_READ_BYTES = 100
            try:
                v = pc.scan(root)
            finally:
                pc.MAX_READ_BYTES = old
            self.assertEqual(v["status"], "unknown", msg=str(v))
            self.assertIn("посмотреть не смог", v["detail"])
            self.assertIn("Big.tsx", v["detail"])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root читает всё")
    def test_an_unreadable_file_gives_unknown_and_exit_two(self):
        with tempfile.TemporaryDirectory() as t:
            root = bare(t, {"src/A.tsx": "<p>ok</p>"})
            f = root / "src" / "A.tsx"
            f.chmod(0)
            try:
                v = pc.scan(root)
                r = subprocess.run([sys.executable, str(TOOL), t],
                                   capture_output=True, text=True, timeout=60,
                                   env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
            finally:
                f.chmod(0o644)
            self.assertEqual(v["status"], "unknown", msg=str(v))
            self.assertEqual(r.returncode, 2)

    def test_over_the_limit_is_still_fail_even_with_unread_files(self):
        with tempfile.TemporaryDirectory() as t:
            root = bare(t, {"src/Big.tsx": "x" * 5000, "src/A.vue": FIVE})
            old = pc.MAX_READ_BYTES
            pc.MAX_READ_BYTES = 1000
            try:
                v = pc.scan(root)
            finally:
                pc.MAX_READ_BYTES = old
            self.assertEqual(v["status"], "fail")


if __name__ == "__main__":
    unittest.main()
