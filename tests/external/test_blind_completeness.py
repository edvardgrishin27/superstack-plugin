#!/usr/bin/env python3
"""Приёмка, которая чего-то не оценила, — не «чистая», а неполная.

Сценарий пришёл из внешней проверки и переписан здесь регрессионным тестом
ПРАВИЛЬНОГО поведения. Гейт G4 сверял манифест со слепой приёмкой только по
тем строкам, которые приёмка вообще упомянула: `seen.get(r["id"])` вернул
`None` — и требование молча пропускалось. Требование, объявленное готовым и НЕ
ОЦЕНЁННОЕ никем со стороны, проходило все четыре гейта, `passed=true`, код
возврата 0. Тем же способом исчезала запись без `id` (её выбрасывал фильтр) и
запись с вердиктом, которого не существует: ни одна ветка не совпадала, и
результат не считался расхождением.

Цена ровно в том, ради чего приёмка заведена: главный аргумент продукта —
«проверял тот, кто не видел ни спеки, ни манифеста». Пропущенная строка
означает, что он её не видел тоже, а отчёт человеку об этом молчит.

Что здесь заперто:
  · требование `done` без вердикта — расхождение, а не пропуск;
  · вердикт вне набора «реализовано / частично / нет» — не нейтральный,
    а неразобранный: гейт гаснет целиком;
  · запись без `id`, повторённый `id`, приёмка не списком — то же самое;
  · граница: строку НЕ `done` приёмка оценивать не обязана;
  · обратный контроль: полная и правильная приёмка остаётся зелёной.

Тест самодостаточен: только стандартная библиотека и сам инструмент.
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

РЕПО = Path(__file__).resolve().parents[2]
ГЕЙТЫ = РЕПО / "plugins" / "superstack" / "tools" / "gates.py"

_спек = importlib.util.spec_from_file_location("ss_gates_completeness", ГЕЙТЫ)
gt = importlib.util.module_from_spec(_спек)
_спек.loader.exec_module(gt)
mf = gt.mf

БРИФ = "принимает заявки на ремонт техники и складывает их в Google-таблицу\n"
ЦИТАТЫ = ("принимает заявки на ремонт техники", "складывает их в Google-таблицу")


class Приёмка(unittest.TestCase):
    """Манифест на два требования и брифом, в котором обе цитаты находятся."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)
        (self.корень / "brief.md").write_text(БРИФ, encoding="utf-8")
        self.путь = self.корень / "manifest.json"

    def манифест(self, статусы=("done", "done"), **ещё) -> dict:
        d = json.loads(json.dumps(mf.EMPTY))
        d["brief"] = "brief.md"
        d["brief_sha"] = mf.sha(self.корень / "brief.md")
        for i, (цитата, статус) in enumerate(zip(ЦИТАТЫ, статусы), 1):
            d["requirements"].append(
                {"id": f"R0{i}", "kind": mf.EXPLICIT, "quote": цитата,
                 "status": статус, "basis": "", "parent": "", "where": "",
                 "said": ""})
        d.update(ещё)
        return d

    def стройка(self) -> Path:
        p = self.корень / "progress.json"
        p.write_text(json.dumps({"schema": "superstack.progress.v1", "waves": {
            "1": [{"id": "01", "name": "всё", "status": "waiting",
                   "requirements": ["R01", "R02"]}]}}), encoding="utf-8")
        return p

    def прогон(self, данные: dict, *флаги) -> subprocess.CompletedProcess:
        """Полный прогон инструментом — ради КОДА ВОЗВРАТА, а не вердикта в уме."""
        self.путь.write_text(json.dumps(данные, ensure_ascii=False),
                             encoding="utf-8")
        env = {**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"}
        return subprocess.run([sys.executable, str(ГЕЙТЫ), str(self.путь), *флаги],
                              cwd=str(self.корень), capture_output=True,
                              text=True, timeout=180, env=env)


class НеоценённоеНеПроходит(Приёмка):
    """Тот самый сценарий внешней проверки: R01 и R02 готовы, оценён один."""

    def test_готовое_без_вердикта_это_расхождение(self):
        d = self.манифест(blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT}]})
        v = gt.gate_g4(d)
        self.assertEqual(v["status"], gt.FAIL, v)
        self.assertEqual([x["id"] for x in v["drift"]], ["R02"], v)
        self.assertIn("не выносила", v["drift"][0]["why"], v)

    def test_прогон_целиком_отдаёт_единицу_а_не_ноль(self):
        """Раньше здесь были четыре зелёных гейта и код возврата 0."""
        d = self.манифест(coverage={"found": 0, "fixed": 0, "deferred": 0},
                          blind={"checked": [{"id": "R01", "verdict": gt.BUILT}]})
        p = self.прогон(d, "--tasks", str(self.стройка()), "--json")
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-600:])
        v = json.loads(p.stdout)
        g4 = [g for g in v["gates"] if g["gate"] == "G4"][0]
        self.assertEqual(g4["status"], gt.FAIL, v)
        self.assertFalse(v["passed"], v)

    def test_человеку_названо_что_именно_не_оценено(self):
        d = self.манифест(blind={"checked": [{"id": "R01", "verdict": gt.BUILT}]})
        self.assertIn("R02", gt.gate_g4(d)["detail"])

    def test_приёмка_пустым_списком_не_считается_запущенной(self):
        """`checked: []` — приёмка, которая не вынесла ни одного вердикта."""
        self.assertEqual(gt.gate_g4(self.манифест(blind={"checked": []}))["status"],
                         gt.UNKNOWN)


class ГраницаПолноты(Приёмка):
    """Проверяется полнота ПО ВЫПОЛНЕННЫМ, а не по всем строкам подряд."""

    def test_невыполненное_приёмка_оценивать_не_обязана(self):
        for статус in ("in-spec", "in-ticket", "placeholder", "deferred"):
            with self.subTest(статус=статус):
                d = self.манифест(("done", статус), blind={"checked": [
                    {"id": "R01", "verdict": gt.BUILT}]})
                self.assertEqual(gt.gate_g4(d)["status"], gt.PASS, статус)

    def test_снятое_приёмка_оценивать_не_обязана(self):
        d = self.манифест(("done", "dropped"),
                          blind={"checked": [{"id": "R01", "verdict": gt.BUILT}]})
        d["requirements"][1]["said"] = "это не надо"
        self.assertEqual(gt.gate_g4(d)["status"], gt.PASS)


class НеразобранныйРезультатГаситГейт(Приёмка):
    """Разобранный кусок чужого результата ничего не говорит о неразобранном.

    Поэтому здесь `unknown` целиком, а не «расхождений в остальном нет»:
    тихое выбрасывание записи делает приёмку КОРОЧЕ, чем она была, и по этому
    остатку считались расхождения.
    """

    def test_запись_без_id_не_выбрасывается_молча(self):
        d = self.манифест(blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"verdict": gt.BUILT}]})
        v = gt.gate_g4(d)
        self.assertEqual(v["status"], gt.UNKNOWN, v)
        self.assertIn("без id", " ".join(v["unparsed"]), v)

    def test_вердикт_вне_набора_не_нейтрален(self):
        d = self.манифест(blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": "скорее да"}]})
        v = gt.gate_g4(d)
        self.assertEqual(v["status"], gt.UNKNOWN, v)
        self.assertIn("R02", " ".join(v["unparsed"]), v)
        self.assertIn("скорее да", " ".join(v["unparsed"]), v)

    def test_вердикта_нет_вовсе_это_тоже_неразобрано(self):
        d = self.манифест(blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT}, {"id": "R02"}]})
        self.assertEqual(gt.gate_g4(d)["status"], gt.UNKNOWN)

    def test_повторённый_id_не_разбирается(self):
        d = self.манифест(blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.ABSENT}]})
        v = gt.gate_g4(d)
        self.assertEqual(v["status"], gt.UNKNOWN, v)
        self.assertIn("дважды", " ".join(v["unparsed"]), v)

    def test_приёмка_не_списком_не_разбирается(self):
        d = self.манифест(blind={"checked": {"R01": gt.BUILT, "R02": gt.BUILT}})
        v = gt.gate_g4(d)
        self.assertEqual(v["status"], gt.UNKNOWN, v)
        self.assertIn("не списком", " ".join(v["unparsed"]), v)

    def test_неразобранное_держит_код_возврата_два(self):
        """«Не смог проверить» — не «прошло» и не «красное»."""
        d = self.манифест(coverage={"found": 0, "fixed": 0, "deferred": 0},
                          blind={"checked": [
                              {"id": "R01", "verdict": gt.BUILT},
                              {"id": "R02", "verdict": "скорее да"}]})
        p = self.прогон(d, "--tasks", str(self.стройка()), "--json")
        self.assertEqual(p.returncode, 2, (p.stdout + p.stderr)[-600:])


class ПолнаяПриёмкаОстаётсяЗелёной(Приёмка):
    """Обратный контроль: строгость, которую нельзя пройти, — не строгость."""

    def test_каждое_готовое_оценено_и_сошлось(self):
        d = self.манифест(blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.BUILT}]})
        self.assertEqual(gt.gate_g4(d)["status"], gt.PASS)

    def test_понижение_строки_остаётся_законным_способом_сойтись(self):
        d = self.манифест(("done", "placeholder"), blind={"checked": [
            {"id": "R01", "verdict": gt.BUILT},
            {"id": "R02", "verdict": gt.PARTIAL}]})
        self.assertEqual(gt.gate_g4(d)["status"], gt.PASS)

    def test_полный_прогон_отдаёт_ноль(self):
        d = self.манифест(coverage={"found": 0, "fixed": 0, "deferred": 0},
                          blind={"checked": [{"id": "R01", "verdict": gt.BUILT},
                                             {"id": "R02", "verdict": gt.BUILT}]})
        p = self.прогон(d, "--tasks", str(self.стройка()), "--json")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-600:])


if __name__ == "__main__":
    unittest.main()
