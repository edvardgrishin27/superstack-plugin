#!/usr/bin/env python3
"""Манифест требований: «дословно» обязано быть проверяемым.

Механизм взят из AutoPilot, где он описан прозой и целиком держится на том,
что модель послушается. Здесь заперты те же правила, но кодом: на каждое
«обязано» есть отказ записи и тест на этот отказ.

Разница, ради которой всё это писалось: у него цитата «дословная», потому что
так сказано в инструкции. Здесь она дословная, потому что строка ищется в
файле брифа и требование без совпадения не записывается вовсе. Пересказ,
выданный за цитату, — это тот же дрейф, только заверенный: строка выглядит
уликой и уликой не является.
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

from paths import at

MANIFEST = at("tools", "manifest.py")
_spec = importlib.util.spec_from_file_location("superstack_manifest", MANIFEST)
mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mf)

BRIEF = """# Изначальная задача

Хочу телеграм-бота, который принимает заявки на ремонт техники
и складывает их в Google-таблицу. Чтобы клиент видел статус.
И дублировать на SMS.
"""


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.brief = self.dir / "brief.md"
        self.brief.write_text(BRIEF, encoding="utf-8")
        self.path = self.dir / "manifest.json"
        self.data = json.loads(json.dumps(mf.EMPTY))
        self.data["brief"] = "brief.md"
        self.data["brief_sha"] = mf.sha(self.brief)

    def add_quote(self, rid: str, quote: str):
        return mf.add(self.data, rid, mf.EXPLICIT, quote=quote, brief_text=BRIEF)


class TestQuoteMustExistInTheBrief(Base):
    """Главное отличие от прозаической версии: «дословно» здесь проверяется."""

    def test_verbatim_quote_is_accepted(self):
        self.add_quote("R01", "принимает заявки на ремонт техники")
        self.assertEqual(mf.find(self.data, "R01")["status"], mf.OPEN)

    def test_paraphrase_is_refused(self):
        """Пересказ, записанный как цитата, — это дрейф с печатью.

        Строка выглядит уликой из брифа, ею не является, и все проверки ниже
        по течению будут честно сверяться с ней вместо слов человека.
        """
        with self.assertRaises(ValueError) as cm:
            self.add_quote("R01", "бот умеет принимать заявки от клиентов")
        self.assertIn("цитаты нет в брифе", str(cm.exception))

    def test_refusal_names_the_nearest_line(self):
        """Строгость без подсказки выключают на второй день: непонятно, что
        чинить. Отказ обязан показать ближайшее место брифа."""
        with self.assertRaises(ValueError) as cm:
            self.add_quote("R01", "бот умеет принимать заявки от клиентов")
        self.assertIn("ближайшее в брифе", str(cm.exception))

    def test_typography_does_not_break_a_real_quote(self):
        """Кавычки, тире и лишние пробелы перенабираются по-разному в одном и
        том же тексте. Ловить на этом — сделать проверку невыполнимой, а не
        строгой: её тогда просто снимут."""
        self.add_quote("R01", "складывает   их\nв Google-таблицу")
        self.assertTrue(mf.find(self.data, "R01"))

    def test_empty_quote_is_refused(self):
        with self.assertRaises(ValueError):
            self.add_quote("R01", "")

    def test_requirement_from_brief_without_quote_is_refused(self):
        with self.assertRaises(ValueError):
            mf.add(self.data, "R01", mf.EXPLICIT, brief_text=BRIEF)


class TestBriefIsTheStandardAndDoesNotMove(Base):

    def test_edited_brief_is_detected(self):
        self.add_quote("R01", "принимает заявки на ремонт техники")
        self.brief.write_text(BRIEF + "\nи ещё тёмную тему\n", encoding="utf-8")
        a = mf.audit(self.data, self.path)
        self.assertTrue(any("бриф изменился" in u for u in a["unmeasured"]), a)

    def test_edited_brief_is_unmeasured_not_broken(self):
        """«Не смог проверить» и «нарушено» — разные ответы, и смешивать их
        нельзя именно здесь: подменённый эталон не доказывает дефекта, он
        отнимает возможность его увидеть."""
        self.add_quote("R01", "принимает заявки на ремонт техники")
        self.brief.write_text("совсем другой текст", encoding="utf-8")
        a = mf.audit(self.data, self.path)
        self.assertTrue(a["unmeasured"])
        self.assertFalse([b for b in a["broken"] if "цитаты нет" in b],
                         "при подменённом эталоне цитаты не судятся — сверять не с чем")

    def test_missing_brief_is_unmeasured(self):
        self.brief.unlink()
        a = mf.audit(self.data, self.path)
        self.assertTrue(any("не найден" in u for u in a["unmeasured"]), a)


class TestOnlyTheHumanRemovesARequirement(Base):

    def test_drop_without_the_humans_words_is_refused(self):
        self.add_quote("R01", "И дублировать на SMS")
        with self.assertRaises(ValueError):
            mf.drop(self.data, "R01", "   ")

    def test_drop_keeps_the_words(self):
        self.add_quote("R01", "И дублировать на SMS")
        mf.drop(self.data, "R01", "SMS не надо, только телега")
        r = mf.find(self.data, "R01")
        self.assertEqual(r["status"], mf.DROPPED)
        self.assertEqual(r["said"], "SMS не надо, только телега")

    def test_set_cannot_reach_dropped(self):
        """Единственная дверь к «отменено» — узкая намеренно. Статус, который
        агент выставляет сам, не защищает требование ни от чего: «мне
        показалось неважным» и «человек передумал» дают одну строку."""
        self.add_quote("R01", "И дублировать на SMS")
        with self.assertRaises(ValueError) as cm:
            mf.set_status(self.data, "R01", mf.DROPPED)
        self.assertIn("только командой drop", str(cm.exception))

    def test_there_is_no_delete_command(self):
        """Строка, которую можно стереть, ничего не держит. Молчание не
        отменяет: забыл и решил не должны выглядеть одинаково."""
        src = MANIFEST.read_text("utf-8")
        for word in ('cmd == "delete"', 'cmd == "remove"', 'cmd == "rm"'):
            self.assertNotIn(word, src)
        self.assertFalse(hasattr(mf, "delete"))

    def test_hand_edited_drop_without_words_is_caught(self):
        """Файл манифеста правят руками. Проверка, работающая только на входе,
        держит ровно до первого такого раза."""
        self.add_quote("R01", "И дублировать на SMS")
        mf.find(self.data, "R01")["status"] = mf.DROPPED
        a = mf.audit(self.data, self.path)
        self.assertTrue(any("без слов человека" in b for b in a["broken"]), a)

    def test_hand_edited_quote_is_rechecked_against_the_brief(self):
        self.add_quote("R01", "принимает заявки на ремонт техники")
        mf.find(self.data, "R01")["quote"] = "принимает заявки любого вида"
        a = mf.audit(self.data, self.path)
        self.assertTrue(any("цитаты нет в брифе" in b for b in a["broken"]), a)


class TestAdditionsKnowTheirParentAndTheirLimit(Base):

    def test_addition_without_parent_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            mf.add(self.data, "A01", mf.ADDITION, basis="тёмная тема")
        self.assertIn("родительское требование", str(cm.exception))

    def test_addition_with_unknown_parent_is_refused(self):
        with self.assertRaises(ValueError):
            mf.add(self.data, "A01", mf.ADDITION, basis="тёмная тема", parent="R99")

    def test_additions_may_not_outnumber_the_humans_requirements(self):
        """Мера — единственное, что отделяет углубление заказанного от другого
        проекта, выросшего рядом."""
        self.add_quote("R01", "принимает заявки на ремонт техники")
        mf.add(self.data, "A01", mf.ADDITION, basis="номер заявки", parent="R01")
        with self.assertRaises(ValueError) as cm:
            mf.add(self.data, "A02", mf.ADDITION, basis="экспорт", parent="R01")
        self.assertIn("мера нарушена", str(cm.exception))

    def test_hand_edited_excess_is_caught(self):
        self.add_quote("R01", "принимает заявки на ремонт техники")
        mf.add(self.data, "A01", mf.ADDITION, basis="номер заявки", parent="R01")
        self.data["requirements"].append(
            {"id": "A02", "kind": mf.ADDITION, "quote": "", "status": mf.OPEN,
             "basis": "экспорт", "parent": "R01", "where": "", "said": ""})
        a = mf.audit(self.data, self.path)
        self.assertTrue(any("мера нарушена" in b for b in a["broken"]), a)


class TestEachKindProvesItself(Base):

    def test_implied_needs_a_basis(self):
        with self.assertRaises(ValueError):
            mf.add(self.data, "R06i", mf.IMPLIED)

    def test_implied_must_carry_the_i_mark(self):
        """Подразумеваемое — самое опасное в манифесте: слишком очевидно,
        чтобы записать, слишком крупно, чтобы пропустить. Метка — то, что
        отправляет его в брифинг вместо тихой выдумки."""
        with self.assertRaises(ValueError) as cm:
            mf.add(self.data, "R06", mf.IMPLIED, basis="заявки кто-то читает")
        self.assertIn("хвостом i", str(cm.exception))

    def test_discovered_needs_both_basis_and_the_requirement_it_serves(self):
        self.add_quote("R01", "принимает заявки на ремонт техники")
        with self.assertRaises(ValueError):
            mf.add(self.data, "D01", mf.DISCOVERED, basis="схема не держит два адреса")
        with self.assertRaises(ValueError):
            mf.add(self.data, "D01", mf.DISCOVERED, parent="R01")
        mf.add(self.data, "D01", mf.DISCOVERED,
               basis="схема не держит два адреса", parent="R01")
        self.assertTrue(mf.find(self.data, "D01"))

    def test_duplicate_id_is_refused(self):
        self.add_quote("R01", "принимает заявки на ремонт техники")
        with self.assertRaises(ValueError):
            self.add_quote("R01", "складывает их в Google-таблицу")

    def test_malformed_id_is_refused(self):
        for bad in ("R1", "X01", "r01", "R01x", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.add_quote(bad, "принимает заявки на ремонт техники")


class TestNullIsNotAPass(Base):
    """Гейт, у которого пустота читается как успех, не гейт."""

    def test_coverage_and_blind_start_as_unrun(self):
        self.assertIsNone(self.data["coverage"])
        self.assertIsNone(self.data["blind"])

    def test_empty_manifest_is_unmeasured_not_clean(self):
        r = mf.report(self.data, self.path)
        self.assertFalse(r["trustworthy"])
        self.assertTrue(r["unmeasured"])
        self.assertFalse(r["broken"])


class TestExitCodesSeparateTheThreeAnswers(Base):
    """Прошло · нарушено · не смог проверить — три разных кода, а не два."""

    def _run(self, *args):
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(MANIFEST), *args],
                              cwd=str(self.dir), capture_output=True,
                              text=True, timeout=120, env=env)

    def test_clean_manifest_exits_zero(self):
        self._run("init", "m.json", "brief.md")
        p = self._run("add", "m.json", "R01",
                      "--quote", "принимает заявки на ремонт техники")
        self.assertEqual(p.returncode, 0, p.stderr[-400:])

    def test_broken_manifest_exits_one(self):
        self._run("init", "m.json", "brief.md")
        self._run("add", "m.json", "R01",
                  "--quote", "принимает заявки на ремонт техники")
        d = json.loads((self.dir / "m.json").read_text("utf-8"))
        d["requirements"][0]["quote"] = "что-то, чего человек не говорил"
        (self.dir / "m.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self._run("show", "m.json").returncode, 1)

    def test_unmeasurable_manifest_exits_two(self):
        self._run("init", "m.json", "brief.md")
        self._run("add", "m.json", "R01",
                  "--quote", "принимает заявки на ремонт техники")
        (self.dir / "brief.md").write_text("подменённый эталон", encoding="utf-8")
        self.assertEqual(self._run("show", "m.json").returncode, 2)

    def test_bad_call_exits_three(self):
        self.assertEqual(self._run("нетакой", "m.json").returncode, 3)


class TestMarkdownIsForTheHuman(Base):

    def test_dropped_row_shows_the_humans_words(self):
        self.add_quote("R01", "И дублировать на SMS")
        mf.drop(self.data, "R01", "SMS не надо, только телега")
        md = mf.to_md(self.data)
        self.assertIn("SMS не надо, только телега", md)
        self.assertIn("только человек", md)


if __name__ == "__main__":
    unittest.main()
