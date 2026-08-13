#!/usr/bin/env python3
"""Тесты линта памяти.

Что эти тесты обязаны держать — и почему именно это.

Линт памяти существует ради отказов, которые НЕ ВИДНЫ изнутри сессии: индекс
обрезался, ссылка ведёт в никуда, у записи нет источника. Ни один из них не
даёт сигнала сам по себе, поэтому единственная защита — проверка, которая
краснеет вместо человека.

Отсюда три правила этого файла:

  1. КАЖДЫЙ ТЕСТ ДЕРЖИТ МЕХАНИЗМ, А НЕ ФОРМУЛИРОВКУ. Проверяется поведение на
     собранном каталоге: подан индекс за потолком — обязано быть замечание
     именно этого вида и именно на этом файле.
  2. НИЧЕГО НЕ БЕРЁТСЯ С МАШИНЫ. Каталог памяти собирается в temp, дата подаётся
     параметром --today, HOME в подпроцессе подставной. Тест, читающий настоящий
     ~/.claude или системные часы, описывает машину, а не код: завтра он
     покраснеет сам по себе, а через месяц его отключат как «мигающий».
  3. ОЖИДАЕМОЕ НЕ БЕРЁТСЯ ИЗ ПРОВЕРЯЕМОГО КОДА. Пороги, коды возврата и имена
     проверок написаны в тесте литералами. Сверка `assert x == module.LIMIT`
     проходит при любом значении LIMIT и не доказывает ничего.

Отдельно закрыты два свойства, без которых линт вреден:
  · пустая память — НЕ «чисто» (иначе выгоднее удалить память, чем вести её);
  · нечитаемый файл — НЕ «чисто» («не нашёл» и «не смог проверить» — разное).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at, plug  # noqa: E402

ROOT = REPO
TOOL = at("tools", "memory_lint.py")

#: Окружение подпроцесса собирается, а не наследуется: через наследование в
#: тест протекает и настоящий ~/.claude (флаг паузы), и локаль машины.
BASE_ENV = {
    "SUPERSTACK_IGNORE_PAUSE": "1",
    "PATH": "",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "NO_COLOR": "1",
}

#: Опорная дата. Всё, что связано с устареванием, считается от неё, а не от
#: системных часов, иначе набор начнёт краснеть от того, что наступило завтра.
TODAY = "2026-08-09"
FRESH = "2026-08-01"     # свежее порога
ANCIENT = "2019-01-01"   # старше любого разумного порога


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # Регистрация до исполнения обязательна: dataclass ищет свой модуль в
    # sys.modules и без этого падает на разборе аннотаций.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ml = _load("ss_memory_lint", TOOL)


class MemoryFixture(unittest.TestCase):
    """Каталог памяти СОБИРАЕТСЯ в temp. Наблюдать чужую память нельзя:
    её содержимое меняется без участия кода, и вердикт перестаёт повторяться."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir(parents=True)
        self.mem = Path(self.tmp.name) / "memory"
        self.mem.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    # ---- сборка каталога --------------------------------------------------
    def write(self, rel: str, text: str) -> Path:
        path = self.mem / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def note(self, rel: str, *, title: str = "Тема", source: str = "сессия 04.01",
             updated: str = FRESH, body: str = "текст\n", extra: str = "") -> Path:
        """Заметка по схеме. Дефекты вносятся точечно, по одному на тест:
        иначе непонятно, какое именно замечание держит проверку."""
        head = ["---", f"title: {title}"]
        if source is not None:
            head.append(f"source: {source}")
        if updated is not None:
            head.append(f"updated: {updated}")
        if extra:
            head.append(extra)
        head.append("---")
        return self.write(rel, "\n".join(head) + f"\n\n# {title}\n\n{body}")

    def raw_doc(self, rel: str, body: str, *, checksum: str = None,
                title: str = "Источник", source: str = "захват",
                updated: str = FRESH) -> str:
        """raw/-документ по схеме. `checksum` подаётся явно (не вычисляется
        здесь автоматически) — так тест волен подать и верную сумму, и
        заведомо неверную, не завися от того, как именно линт её считает."""
        head = ["---", f"title: {title}", f"source: {source}",
                f"updated: {updated}"]
        if checksum is not None:
            head.append(f"checksum: {checksum}")
        head.append("---")
        text = "\n".join(head) + "\n" + body
        self.write(rel, text)
        return text

    def index(self, *targets: str, extra: str = "") -> Path:
        lines = ["# индекс памяти", ""] + [f"- [[{t}]]" for t in targets]
        if extra:
            lines.append(extra)
        return self.write("MEMORY.md", "\n".join(lines) + "\n")

    # ---- запуск -----------------------------------------------------------
    def run_lint(self, *args: str, home: Path = None) -> subprocess.CompletedProcess:
        env = dict(BASE_ENV)
        env["HOME"] = str(home or self.home)
        return subprocess.run(
            [sys.executable, str(TOOL), str(self.mem), "--json", "--today", TODAY,
             *args],
            capture_output=True, text=True, timeout=120, env=env)

    def report(self, *args: str) -> dict:
        r = self.run_lint(*args)
        self.assertNotIn("Traceback", r.stderr, "линт упал вместо вердикта")
        return json.loads(r.stdout)

    def checks_on(self, report: dict, file: str) -> list:
        return sorted(f["check"] for f in report["findings"] if f["file"] == file)

    def all_checks(self, report: dict) -> list:
        return sorted(f["check"] for f in report["findings"])


# ---------------------------------------------------------------------------
class TestIndexIsTruncatedSilently(MemoryFixture):
    """Главный отказ: индекс грузится не целиком, и об этом никто не сообщает.

    Тест обязан ловить именно превышение потолка, а не «файл большой»."""

    def test_index_over_byte_ceiling_is_named(self):
        self.index("тема")
        self.note("тема.md", body="x" * 400)
        self.write("MEMORY.md", "# индекс\n" + "- [[тема]]\n" * 400)  # ~5 КБ
        r = self.report("--limit-bytes", "1000")
        self.assertIn("index-over-limit-bytes", self.checks_on(r, "MEMORY.md"))

    def test_index_under_ceiling_is_not_named(self):
        """Обратный контроль: линт, который ругается всегда, не отличим от шума."""
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        r = self.report("--limit-bytes", "100000")
        self.assertNotIn("index-over-limit-bytes", self.all_checks(r))

    def test_default_ceiling_is_the_load_limit_not_infinity(self):
        """Порог по умолчанию обязан существовать: индекс на 30 КБ не должен
        проходить только потому, что порог не подали руками. 25 КБ — предел
        загрузки MEMORY.md, число здесь написано в тесте, а не взято из кода."""
        self.write("MEMORY.md", "# индекс\n" + "- [[тема]]\n" * 3000)  # ~36 КБ
        self.note("тема.md")
        r = self.report()
        self.assertIn("index-over-limit-bytes", self.checks_on(r, "MEMORY.md"))

    def test_line_ceiling_is_separate_from_byte_ceiling(self):
        """Короткие строки влезают в байты и всё равно рубятся по числу строк."""
        self.write("MEMORY.md", "# индекс\n" + "- [[тема]]\n" * 40)
        self.note("тема.md")
        r = self.report("--limit-bytes", "1000000", "--limit-lines", "10")
        self.assertIn("index-over-limit-lines", self.checks_on(r, "MEMORY.md"))

    def test_missing_index_is_named_not_ignored(self):
        self.note("тема.md")
        r = self.report()
        self.assertIn("index-missing", self.all_checks(r))


# ---------------------------------------------------------------------------
class TestLinksThatLeadNowhere(MemoryFixture):
    def test_link_to_absent_file_is_a_finding(self):
        self.index("живая", "мёртвая")
        self.note("живая.md")
        r = self.report()
        broken = [f for f in r["findings"] if f["check"] == "broken-link"]
        self.assertEqual(len(broken), 1, r["findings"])
        self.assertIn("мёртвая", broken[0]["detail"])

    def test_existing_target_is_not_broken(self):
        self.index("живая")
        self.note("живая.md", body="[[MEMORY]]\n")
        self.assertNotIn("broken-link", self.all_checks(self.report()))

    def test_alias_anchor_and_extension_resolve_to_the_same_file(self):
        """Ссылку пишут руками: [[тема|как звать]], [[тема#раздел]], [[тема.md]]
        — три записи одной цели. Ложное «битая ссылка» приучает не читать вывод."""
        for link in ("тема|как звать", "тема#раздел", "тема.md", "ТЕМА"):
            with self.subTest(link=link):
                self.index(link)
                self.note("тема.md")
                self.assertNotIn("broken-link", self.all_checks(self.report()))

    def test_link_inside_code_fence_is_not_a_link(self):
        """В блоке кода [[имя]] — показ синтаксиса. Иначе описание формата
        памяти обвиняет само себя, и настоящие битые ссылки тонут в шуме."""
        self.index("тема")
        self.note("тема.md",
                  body="Формат:\n\n```\n- [[имя-темы]]\n```\n\n[[MEMORY]]\n")
        self.assertNotIn("broken-link", self.all_checks(self.report()))


# ---------------------------------------------------------------------------
class TestProvenance(MemoryFixture):
    """Факт без источника — слух: перепроверить его нечем, а влияет он наравне
    с измеренным."""

    def test_note_without_source_is_a_finding(self):
        self.index("тема")
        self.note("тема.md", source=None, body="[[MEMORY]]\n")
        self.assertIn("no-provenance", self.checks_on(self.report(), "тема.md"))

    def test_note_with_source_is_clean(self):
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        r = self.report()
        self.assertEqual(r["findings"], [], r["findings"])
        self.assertEqual(r["status"], "clean")

    def test_note_without_frontmatter_is_a_finding(self):
        self.index("тема")
        self.write("тема.md", "# Тема\n\nтекст [[MEMORY]]\n")
        self.assertIn("frontmatter-missing", self.checks_on(self.report(), "тема.md"))

    def test_index_itself_is_not_asked_for_provenance(self):
        """Индекс — оглавление, а не факт. Требовать у него источник значит
        выдавать замечание, которое нечем закрыть, и обучать их игнорировать."""
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        self.assertEqual(self.checks_on(self.report(), "MEMORY.md"), [])


# ---------------------------------------------------------------------------
class TestFrontmatterSchema(MemoryFixture):
    def test_unknown_key_is_named(self):
        self.index("тема")
        self.note("тема.md", extra="автор: кто-то", body="[[MEMORY]]\n")
        self.assertIn("frontmatter-unknown-key", self.checks_on(self.report(), "тема.md"))

    def test_date_not_in_iso_form_is_named(self):
        self.index("тема")
        self.note("тема.md", updated="04.01.2026", body="[[MEMORY]]\n")
        self.assertIn("frontmatter-bad-date", self.checks_on(self.report(), "тема.md"))

    def test_impossible_date_is_named(self):
        self.index("тема")
        self.note("тема.md", updated="2026-02-31", body="[[MEMORY]]\n")
        self.assertIn("frontmatter-bad-date", self.checks_on(self.report(), "тема.md"))

    def test_unclosed_frontmatter_is_named(self):
        """Незакрытая шапка опаснее отсутствующей: половина заметки утекает
        в неё и не показывается там, где её собирались прочитать."""
        self.index("тема")
        self.write("тема.md", "---\ntitle: Тема\nsource: с\n\n# Тема\n[[MEMORY]]\n")
        self.assertIn("frontmatter-unparsed", self.checks_on(self.report(), "тема.md"))

    def test_missing_updated_is_named(self):
        self.index("тема")
        self.note("тема.md", updated=None, body="[[MEMORY]]\n")
        self.assertIn("frontmatter-missing-key", self.checks_on(self.report(), "тема.md"))


# ---------------------------------------------------------------------------
class TestStaleness(MemoryFixture):
    def test_old_note_is_named_as_stale(self):
        self.index("тема")
        self.note("тема.md", updated=ANCIENT, body="[[MEMORY]]\n")
        self.assertIn("stale", self.checks_on(self.report(), "тема.md"))

    def test_fresh_note_is_not_stale(self):
        self.index("тема")
        self.note("тема.md", updated=FRESH, body="[[MEMORY]]\n")
        self.assertNotIn("stale", self.all_checks(self.report()))

    def test_the_clock_is_a_parameter_not_the_machine(self):
        """Один и тот же каталог обязан менять вердикт от ПОДАННОЙ даты, а не
        от системной. Иначе набор краснеет сам по себе, когда наступит завтра."""
        self.index("тема")
        self.note("тема.md", updated="2026-01-01", body="[[MEMORY]]\n")
        near = self.run_lint("--stale-days", "30")
        far = subprocess.run(
            [sys.executable, str(TOOL), str(self.mem), "--json",
             "--today", "2026-01-10", "--stale-days", "30"],
            capture_output=True, text=True, timeout=120,
            env={**BASE_ENV, "HOME": str(self.home)})
        self.assertIn("stale", self.all_checks(json.loads(near.stdout)))
        self.assertNotIn("stale", self.all_checks(json.loads(far.stdout)))

    def test_threshold_is_adjustable(self):
        self.index("тема")
        self.note("тема.md", updated="2026-07-01", body="[[MEMORY]]\n")
        self.assertIn("stale", self.all_checks(self.report("--stale-days", "7")))
        self.assertNotIn("stale", self.all_checks(self.report("--stale-days", "3650")))


# ---------------------------------------------------------------------------
class TestDuplicatesByMeaning(MemoryFixture):
    def test_same_topic_in_two_files_is_named(self):
        """Расходящиеся дубли дают разный ответ в зависимости от того, какой
        попался первым — это хуже, чем отсутствие записи."""
        self.index("а", "б")
        self.note("а.md", title="Тон общения", body="[[MEMORY]]\n")
        self.note("б.md", title="общения  тон!", body="[[MEMORY]]\n")
        dupes = [f for f in self.report()["findings"] if f["check"] == "duplicate-title"]
        self.assertEqual(len(dupes), 1, dupes)
        self.assertIn("а.md", dupes[0]["detail"])
        self.assertIn("б.md", dupes[0]["detail"])

    def test_different_topics_are_not_duplicates(self):
        self.index("а", "б")
        self.note("а.md", title="Тон общения", body="[[MEMORY]]\n")
        self.note("б.md", title="Пороги оплаты", body="[[MEMORY]]\n")
        self.assertNotIn("duplicate-title", self.all_checks(self.report()))

    def test_title_comes_from_the_heading_when_frontmatter_is_silent(self):
        """Поле title заполняют не всегда; название темы стоит строкой «# …».
        Дубль обязан находиться и по нему."""
        self.index("а", "б")
        self.write("а.md", "---\nsource: с\nupdated: %s\n---\n\n# Тон общения\n[[MEMORY]]\n" % FRESH)
        self.write("б.md", "---\nsource: с\nupdated: %s\n---\n\n# тон общения\n[[MEMORY]]\n" % FRESH)
        self.assertIn("duplicate-title", self.all_checks(self.report()))


# ---------------------------------------------------------------------------
class TestDeadEntries(MemoryFixture):
    def test_entry_nobody_links_and_which_links_nowhere_is_named(self):
        self.index("живая")
        self.note("живая.md", body="[[MEMORY]]\n")
        self.note("мёртвая.md", body="ни на что не ссылается\n")
        self.assertIn("orphan", self.checks_on(self.report(), "мёртвая.md"))

    def test_entry_referenced_from_the_index_is_alive(self):
        self.index("живая")
        self.note("живая.md", body="текст без ссылок\n")
        self.assertNotIn("orphan", self.all_checks(self.report()))

    def test_entry_that_links_out_is_alive(self):
        """На неё не ссылаются, но она сама участвует в работе — «мёртвой»
        считается только запись, оторванная с ОБЕИХ сторон."""
        self.index("живая")
        self.note("живая.md", body="[[MEMORY]]\n")
        self.note("односторонняя.md", body="см. [[живая]]\n")
        self.assertNotIn("orphan", self.all_checks(self.report()))

    def test_without_index_the_orphan_check_is_declared_unchecked(self):
        """Без индекса «на кого никто не ссылается» считать не от чего.
        Молча пропустить проверку нельзя: непроверенное обязано быть названо."""
        self.note("тема.md", body="текст\n")
        r = self.report()
        self.assertNotIn("orphan", self.all_checks(r))
        self.assertTrue(r["unchecked"], "проверка выпала молча")
        self.assertFalse(r["complete"])


# ---------------------------------------------------------------------------
class TestAbsenceIsNotCleanliness(MemoryFixture):
    """Два состояния, которые нельзя выдавать за порядок."""

    def test_empty_memory_is_not_clean(self):
        """Иначе удалить память выгоднее, чем вести её: ровно тот стимул,
        из-за которого в проектах пропадают тесты."""
        r = self.run_lint()
        self.assertEqual(r.returncode, 2, r.stdout)
        v = json.loads(r.stdout)
        self.assertEqual(v["status"], "unknown")
        self.assertNotEqual(v["status"], "clean")

    def test_unreadable_file_is_not_silently_skipped(self):
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        (self.mem / "битый.md").write_bytes(b"\xff\xfe\x00\x00binary")
        r = self.run_lint()
        v = json.loads(r.stdout)
        self.assertEqual([u["file"] for u in v["unchecked"]], ["битый.md"], v)
        self.assertFalse(v["complete"], "вердикт объявлен полным при нечитанном файле")

    def test_no_findings_but_unread_file_is_not_clean(self):
        """«Не нашёл» и «не смог проверить» — разные утверждения. Второе
        обязано гасить доверие, а не подаваться как чистый результат."""
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        (self.mem / "битый.md").write_bytes(b"\xff\xfe\x00\x00binary")
        r = self.run_lint()
        v = json.loads(r.stdout)
        self.assertEqual(v["findings"], [], v["findings"])
        self.assertEqual(v["status"], "unknown")
        self.assertEqual(r.returncode, 2)


# ---------------------------------------------------------------------------
class TestLintTouchesNothing(MemoryFixture):
    """Линт говорит, человек решает. Память — единственное, что нельзя
    пересобрать из исходников: автоправка здесь дороже любой находки."""

    def _snapshot(self) -> dict:
        return {p.relative_to(self.mem).as_posix():
                (p.read_bytes(), p.stat().st_mtime_ns)
                for p in sorted(self.mem.rglob("*")) if p.is_file()}

    def test_nothing_is_written_moved_or_deleted(self):
        self.index("тема", "нет-такого")
        self.note("тема.md", source=None, updated=ANCIENT)
        self.note("дубль.md", title="Тема")
        (self.mem / "битый.md").write_bytes(b"\xff\xfe")
        before = self._snapshot()
        r = self.run_lint()
        self.assertEqual(r.returncode, 1, r.stdout)   # находки были
        self.assertEqual(self._snapshot(), before,
                         "линт изменил каталог памяти — он обязан только читать")


# ---------------------------------------------------------------------------
class TestVerdictContract(MemoryFixture):
    """Вердикт читает скрипт: коды возврата и форма ответа — часть контракта."""

    def test_clean_memory_returns_zero(self):
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        self.assertEqual(self.run_lint().returncode, 0)

    def test_findings_return_one(self):
        self.index("нет-такого")
        self.note("тема.md", body="[[MEMORY]]\n")
        self.assertEqual(self.run_lint().returncode, 1)

    def test_exit_codes_are_distinct(self):
        """Ноль/один/два обязаны различаться: на них строится решение скрипта."""
        self.assertEqual(sorted(ml.EXIT.values()), [0, 1, 2])

    def test_json_carries_the_decision_fields(self):
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        v = self.report()
        for key in ("lint", "status", "complete", "files", "findings",
                    "unchecked", "next"):
            self.assertIn(key, v)

    def test_every_finding_names_file_check_and_fix(self):
        self.index("нет-такого")
        self.note("тема.md", source=None, updated=ANCIENT)
        for f in self.report()["findings"]:
            with self.subTest(check=f.get("check")):
                for key in ("check", "severity", "file", "detail", "fix"):
                    self.assertTrue(f.get(key), f)

    def test_severe_findings_come_first(self):
        self.index("нет-такого")
        self.note("тема.md", body="ни на что не ссылается\n")
        ranks = [{"critical": 0, "high": 1, "medium": 2, "low": 3}[f["severity"]]
                 for f in self.report()["findings"]]
        self.assertEqual(ranks, sorted(ranks), "порядок находок не по тяжести")

    def test_human_text_never_pollutes_the_json(self):
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        env = {**BASE_ENV, "HOME": str(self.home)}
        r = subprocess.run(
            [sys.executable, str(TOOL), str(self.mem), "--today", TODAY],
            capture_output=True, text=True, timeout=120, env=env)
        json.loads(r.stdout)      # упадёт, если человеческий текст ушёл в stdout
        self.assertIn("ПАМЯТЬ ЦЕЛА", r.stderr)


# ---------------------------------------------------------------------------
class TestCallErrorsAreNamed(MemoryFixture):
    """Ошибка вызова — код 3 и внятная строка, а не трейсбек и не тихий дефолт."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, timeout=120,
                              env={**BASE_ENV, "HOME": str(self.home)})

    def test_directory_is_required(self):
        """Каталог не подставляется молча: линт, который сам выбирает, какую
        память смотреть, невозможно ни проверить, ни повторить на другой машине."""
        r = self._run("--json")
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("НЕ УДАЛОСЬ", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_absent_directory_is_named(self):
        r = self._run("/нет/такого/каталога", "--json")
        self.assertEqual(r.returncode, 3)
        self.assertIn("НЕ УДАЛОСЬ", r.stderr)

    def test_broken_today_is_refused_not_guessed(self):
        r = self._run(str(self.mem), "--json", "--today", "вчера")
        self.assertEqual(r.returncode, 3)
        self.assertNotIn("Traceback", r.stderr)

    def test_option_without_value_is_refused_not_defaulted(self):
        """Тихий откат к дефолту дал бы человеку чужой вердикт под видом своего."""
        r = self._run(str(self.mem), "--json", "--limit-bytes")
        self.assertEqual(r.returncode, 3)

    def test_non_numeric_limit_is_refused(self):
        r = self._run(str(self.mem), "--json", "--limit-bytes", "много")
        self.assertEqual(r.returncode, 3)

    def test_unknown_option_is_refused(self):
        r = self._run(str(self.mem), "--json", "--fix-everything")
        self.assertEqual(r.returncode, 3)


# ---------------------------------------------------------------------------
class TestVerdictDoesNotDependOnTheMachine(MemoryFixture):
    def test_same_directory_two_homes_same_verdict(self):
        """Если бы линт читал настоящий ~/.claude, вердикт поехал бы вслед за
        машиной проверяющего — и «зелено» перестало бы быть утверждением о коде."""
        self.index("тема", "нет-такого")
        self.note("тема.md", source=None)
        other = Path(self.tmp.name) / "home2"
        (other / ".claude").mkdir(parents=True)
        (other / ".claude" / "MEMORY.md").write_text("чужая память\n", encoding="utf-8")
        a = self.run_lint()
        b = self.run_lint(home=other)
        self.assertEqual(a.stdout, b.stdout)
        self.assertEqual(a.returncode, b.returncode)

    def test_index_name_is_a_parameter(self):
        """Имя индекса тоже не зашито: у разных установок он называется по-разному,
        а линт, промахнувшийся мимо индекса, объявит всю память сиротской."""
        self.write("ПАМЯТЬ.md", "# индекс\n- [[тема]]\n")
        self.note("тема.md", body="текст\n")
        r = self.report("--index", "ПАМЯТЬ.md")
        self.assertNotIn("index-missing", self.all_checks(r))
        self.assertNotIn("orphan", self.all_checks(r))


# ---------------------------------------------------------------------------
class TestRawSourcesAreImmutable(MemoryFixture):
    """raw/ — неизменяемые источники. Чек-сумма в шапке — единственный
    способ заметить правку задним числом: mtime не годится (его легко
    подделать копированием), git-лог не годится (память может не быть
    репозиторием)."""

    def test_matching_checksum_is_clean(self):
        body = "исходная цитата, слово в слово\n"
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.raw_doc("raw/источник.md", body, checksum=checksum)
        r = self.report()
        checks = self.checks_on(r, "raw/источник.md")
        self.assertNotIn("raw-modified", checks)
        self.assertNotIn("raw-missing-checksum", checks)

    def test_mismatched_checksum_is_flagged_as_modified(self):
        """Тело записано одно, а в шапке — сумма от другого текста: ровно то,
        что происходит, когда источник поправили и забыли (или не смогли)
        пересчитать сумму."""
        body = "источник как он есть сейчас, уже другой\n"
        stale_checksum = hashlib.sha256("исходный текст до правки\n".encode("utf-8")).hexdigest()
        self.raw_doc("raw/источник.md", body, checksum=stale_checksum)
        r = self.report()
        self.assertIn("raw-modified", self.checks_on(r, "raw/источник.md"))

    def test_missing_checksum_is_flagged_not_silently_trusted(self):
        self.raw_doc("raw/источник.md", "текст без чек-суммы\n", checksum=None)
        r = self.report()
        self.assertIn("raw-missing-checksum", self.checks_on(r, "raw/источник.md"))

    def test_note_outside_raw_is_not_checked_for_checksum(self):
        """Обратный контроль: обычная заметка вне raw/ не обязана иметь
        checksum — иначе весь остальной каталог памяти покраснел бы разом."""
        self.index("тема")
        self.note("тема.md", body="[[MEMORY]]\n")
        checks = self.all_checks(self.report())
        self.assertNotIn("raw-missing-checksum", checks)
        self.assertNotIn("raw-modified", checks)


# ---------------------------------------------------------------------------
class TestWikiNotesCiteTheirSource(MemoryFixture):
    """wiki/ — то, что порождает и линтует агент. Поле source в шапке —
    вольный текст и до файла им не дойти; без ссылки [[raw/…]] запись формально
    «с провенансом», а на деле её нечем перепроверить."""

    def test_wiki_note_without_raw_link_is_flagged(self):
        self.note("wiki/заметка.md", source="сессия", body="просто текст, без ссылок\n")
        r = self.report()
        self.assertIn("wiki-missing-source-link", self.checks_on(r, "wiki/заметка.md"))

    def test_wiki_note_linking_to_raw_is_clean(self):
        body = "текст источника\n"
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self.raw_doc("raw/источник.md", body, checksum=checksum)
        self.note("wiki/заметка.md", body="разбор по [[raw/источник]]\n")
        r = self.report()
        self.assertNotIn("wiki-missing-source-link", self.checks_on(r, "wiki/заметка.md"))

    def test_wiki_note_linking_only_to_another_wiki_note_is_still_flagged(self):
        """Ссылка на соседнюю заметку в wiki/ — не источник: она сама всего
        лишь чей-то разбор, и цепочка обязана упираться в raw/, а не гулять
        по кругу внутри wiki/."""
        self.note("wiki/другая.md", source="сессия", body="[[MEMORY]]\n")
        self.note("wiki/заметка.md", source="сессия", body="см. [[wiki/другая]]\n")
        r = self.report()
        self.assertIn("wiki-missing-source-link", self.checks_on(r, "wiki/заметка.md"))


if __name__ == "__main__":
    unittest.main()
