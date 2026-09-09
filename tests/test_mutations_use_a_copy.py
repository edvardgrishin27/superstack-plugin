#!/usr/bin/env python3
"""Мутации ломают одноразовую копию, а не рабочее дерево человека.

Проверка «держатся ли тесты» устроена так: сломать нарочно и посмотреть,
покраснеет ли набор. Пока ломалось ЖИВОЕ дерево, у этого была цена, и она
дважды предъявлялась: прерванный прогон оставлял в файле `if False:`, и потом
восемь красных тестов в четырёх файлах выглядели настоящими дефектами —
чинить шли работающее. Плюс всё время прогона нельзя было ни редактировать,
ни выкладывать: дерево то и дело сломано.

Копия стоит секунду и двадцать пять мегабайт. Здесь заперто, что она
действительно используется, — иначе правка тихо вернётся к прежнему поведению,
и узнается это только на следующем обрыве.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO, at  # noqa: E402

_s = importlib.util.spec_from_file_location("ss_gauntlet_copy",
                                            at("tools", "gauntlet.py"))
gt = importlib.util.module_from_spec(_s)
_s.loader.exec_module(gt)

#: Служебное, что ворота вправе создать и убрать за собой. В слепок дерева не
#: входит: замок живёт ровно столько, сколько идёт измерение.
СЛУЖЕБНОЕ = {".mutation-lock", "__pycache__", ".pytest_cache"}


class ЛомаетсяУказанныйКорень(unittest.TestCase):
    """Поломка уходит туда, куда велено, а не в рабочее дерево."""

    def test_живой_файл_не_трогается(self):
        живой = REPO / "README.md"
        было = живой.read_bytes()

        with tempfile.TemporaryDirectory() as d:
            копия = Path(d)
            (копия / "README.md").write_bytes(было)
            (копия / "tests").mkdir()
            # Набор в копии заведомо красный: важно не это, а то, ГДЕ
            # оказалась поломка.
            (копия / "tests" / "test_ничего.py").write_text(
                "def test_ничего():\n    assert False\n", encoding="utf-8")
            мутация = {"id": "проверочная", "file": "README.md",
                       "find": было.decode("utf-8", "replace")[:12],
                       "replace": "СЛОМАНО", "why": "проверка адреса поломки"}
            gt._mutate_all([мутация], копия)

        self.assertEqual(живой.read_bytes(), было,
                         "поломка ушла в рабочее дерево вместо копии")


class ВоротаБерутКопию(unittest.TestCase):
    """Ворота обязаны работать в копии, и это видно по поведению, а не по коду.

    Признак точный: в копии отложенные копии файлов не нужны и не делаются.
    Если ворота снова начнут ломать дерево, `stash` будет вызван — и этот тест
    покраснеет раньше, чем следующий обрыв прогона.

    Корень здесь СВОЙ, и это не удобство, а починка. Пока тест звал ворота на
    рабочем дереве, он занимал замок этого дерева на минуту — а планку в это
    время могли гонять ещё раз. Второй прогон набора получал отказ по замку,
    падал ровно этот тест, и ворота герметичности объявляли разницу в один
    тест зависимостью набора от HOME. Проверка ценная, а цена оказалась
    чужая: подменённый смысл красного у соседних ворот.

    Своё дерево ничего не отнимает. Утверждение то же самое — «ворота не
    трогают дерево, которое им дали», — и проверено оно теперь сильнее: не
    только по вызову `stash`, но и слепком всех файлов до и после.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)
        (self.корень / "tests").mkdir()
        (self.корень / "src.py").write_text("МЕХАНИЗМ = 'живой'\n",
                                            encoding="utf-8")
        (self.корень / "tests" / "test_src.py").write_text(
            "from pathlib import Path\n\n\n"
            "def test_механизм_на_месте():\n"
            "    t = (Path(__file__).resolve().parent.parent / 'src.py')"
            ".read_text()\n"
            "    assert \"МЕХАНИЗМ = 'живой'\" in t\n", encoding="utf-8")
        (self.корень / "tests" / "mutations.json").write_text(
            json.dumps({"mutations": [
                {"id": "проба.механизм", "file": "src.py",
                 "find": "МЕХАНИЗМ = 'живой'", "replace": "МЕХАНИЗМ = 'мёртвый'",
                 "why": "удалён механизм, который держит тест"}]},
                ensure_ascii=False), encoding="utf-8")
        self.addCleanup(setattr, gt, "PLUG", gt.PLUG)
        gt.PLUG = self.корень

    def _слепок(self) -> dict:
        return {str(f.relative_to(self.корень)): f.read_bytes()
                for f in sorted(self.корень.rglob("*"))
                if f.is_file() and not (СЛУЖЕБНОЕ & set(f.parts))}

    def test_отложенных_копий_не_делается(self):
        звали = []
        self.addCleanup(setattr, gt, "stash", gt.stash)
        gt.stash = lambda *a, **k: звали.append(a)
        было = self._слепок()

        v = gt.gate_mutations()

        if v.get("lock"):
            # Отказ по замку и провал механизма — разные вещи. Считать первое
            # вторым значит чинить работающее; считать его успехом — молча
            # выдать непроверенное за проверенное. Поэтому пропуск, и с
            # НАЗВАННОЙ причиной.
            self.skipTest("дерево держит чужой прогон: " + v["detail"])
        self.assertEqual(v["status"], "pass", v)
        self.assertEqual(звали, [], "ворота отложили копию файла — значит "
                                    "ломали дерево, которое им дали")
        self.assertFalse((self.корень / ".mutation-backup").exists(),
                         "ворота завели каталог отложенных копий в дереве")
        self.assertEqual(self._слепок(), было,
                         "ворота изменили дерево, которое им дали")

    def test_занятое_дерево_названо_полем_а_не_только_прозой(self):
        """«Дерево держит чужой прогон» и «механизм сломан» — оба unknown.

        Пока разница жила только в формулировке, вызывающий обязан был
        разбирать прозу, чтобы отличить одно от другого, — то есть
        договариваться о словах вместо поля. Здесь заперто поле.
        """
        (self.корень / ".mutation-lock").write_text(
            "1 набор: tests/test_чужой.py::Класс::тест\n", encoding="utf-8")

        v = gt.gate_mutations()

        self.assertEqual(v["status"], "unknown", v)
        self.assertEqual(v["lock"]["pid"], 1, v)
        self.assertIn("tests/test_чужой.py", v["lock"]["кто"], v)


if __name__ == "__main__":
    unittest.main()
