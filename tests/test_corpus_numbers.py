#!/usr/bin/env python3
"""Числа о корпусе сверяются с корпусом, а не переписываются из памяти.

Класс К11 из триажа: «отчёты об охвате не сходятся со своими же данными».
Проверено измерением 20.08: 45 досье против 45 строк таблицы — сошлось,
24 репозитория и 1135 механизмов — сошлось, а диапазон прочитанных файлов был
заявлен как «13–67» при настоящем минимуме 9.

Занижённый минимум — не описка: он преувеличивает глубину чтения там, где она
была самой мелкой, то есть ровно там, где читателю важнее всего знать правду.

Продукт продаёт правило «не утверждай того, чего не измерил». Числа в его
собственных отчётах — первое место, где это правило проверяется.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO  # noqa: E402

#: Корпус — материал разработки: он остаётся в репозитории и не ставится
#: вместе с плагином. В поставке проверять нечем, и это ПРОПУСК с названной
#: причиной, а не зелёная строка: разница между «сошлось» и «нечего сверять»
#: обязана быть видна тому, кто читает отчёт.
КОРПУС = REPO / "research" / "deep-read.json"
ЕСТЬ_КОРПУС = КОРПУС.is_file()
ПРИЧИНА = "корпус research/ не входит в поставку — сверять числа не с чем"


def корпус() -> dict:
    return json.loads((REPO / "research" / "deep-read.json").read_text("utf-8"))


@unittest.skipUnless(ЕСТЬ_КОРПУС, ПРИЧИНА)
class ЧислаСходятсяСДанными(unittest.TestCase):

    def test_диапазон_на_репозиторий_измерен(self):
        """Совокупность обязана быть НАЗВАНА рядом с числом.

        Прежнее «13–67 файлов на 10 CORE-репозиториев» было посчитано по
        проходам, а не по репозиториям: `repos_data` держит 24 ПРОХОДА по
        десяти репозиториям, и поле в данных при этом называется `repos`.
        Правя это, я сам чуть не заменил одно неверное число другим — взял
        минимум по проходам (9) и приписал его репозиториям.
        """
        d = корпус()
        self.assertEqual(d["files_per_repo"],
                         {"min": 30, "max": 100},
                         "числа в данных разошлись с проверкой — пересчитай оба")
        for имя in ("COVERAGE-REPORT.md", "research/README.md"):
            with self.subTest(имя):
                t = (REPO / имя).read_text("utf-8")
                self.assertRegex(
                    t, r"30[–-]100 файлов исходников на репозиторий"
                       r"|30[–-]100 файлов на репозиторий",
                    f"{имя} называет диапазон не по измерению")

    def test_числа_в_данных_пересчитываются_из_данных(self):
        """Сами поля обязаны сходиться со строками, иначе сверять нечего."""
        import collections
        d = корпус()
        по_репо = collections.defaultdict(int)
        for r in d["repos_data"]:
            по_репо[r["repo"].split(" ")[0].split(" —")[0]] += len(r["files_actually_read"])
        за_проход = [len(r["files_actually_read"]) for r in d["repos_data"]]
        self.assertEqual(d["unique_repos"], len(по_репо))
        self.assertEqual(d["passes"], len(d["repos_data"]))
        self.assertEqual(d["files_per_repo"],
                         {"min": min(по_репо.values()), "max": max(по_репо.values())})
        self.assertEqual(d["files_per_pass"],
                         {"min": min(за_проход), "max": max(за_проход)})

    def test_поле_repos_считает_проходы_и_это_сказано(self):
        """Поле названо `repos`, а держит проходы. Пока имя не исправлено,
        расхождение обязано быть записано рядом — иначе следующий читатель
        повторит ту же ошибку, что и я."""
        d = корпус()
        self.assertIn("note_on_repos_field", d)
        self.assertNotEqual(d["repos"], d["unique_repos"],
                            "поля сошлись — оговорку пора убирать")

    def test_механизмов_столько_сколько_строк(self):
        d = корпус()
        по_строкам = sum(len(r["mechanisms"]) for r in d["repos_data"])
        self.assertEqual(d["mechanisms"], по_строкам)

    def test_репозиториев_столько_сколько_строк(self):
        d = корпус()
        self.assertEqual(d["repos"], len(d["repos_data"]))

    def test_досье_столько_сколько_строк(self):
        d = json.loads((REPO / "research" / "dossiers.json").read_text("utf-8"))
        self.assertEqual(d["count"], len(d["dossiers"]))


if __name__ == "__main__":
    unittest.main()
