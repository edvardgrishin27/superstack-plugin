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
    Если ворота снова начнут ломать живое дерево, `stash` будет вызван — и
    этот тест покраснеет раньше, чем следующий обрыв прогона.
    """

    def test_отложенных_копий_не_делается(self):
        звали = []
        orig_stash, orig_only = gt.stash, gt.ONLY_MUTATIONS
        gt.stash = lambda *a, **k: звали.append(a)
        # Одна дешёвая мутация: она падает на первом же тесте, и ворота
        # закрываются за секунды.
        gt.ONLY_MUTATIONS = {"hook.no-lock"}
        try:
            v = gt.gate_mutations()
        finally:
            gt.stash, gt.ONLY_MUTATIONS = orig_stash, orig_only

        self.assertIn(v["status"], ("pass", "fail"), v)
        self.assertEqual(звали, [], "ворота отложили копию файла — значит "
                                    "ломали рабочее дерево")


if __name__ == "__main__":
    unittest.main()
