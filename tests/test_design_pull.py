#!/usr/bin/env python3
"""Дизайн подтягивается из холста, а не пересказывается.

Дефект, ради которого инструмент существует. Фаза 2.5 требовала: человек
приносит дизайн, агент переводит его в токены. Последний шаг держался на
честности агента — он читал дизайн глазами и писал четыре цвета в спеку, и
отличить цвет, ВЗЯТЫЙ из дизайна, от подобранного «примерно такого же» не мог
никто, включая человека. Один раз палитру целиком выбрал строитель, и заметили
это случайно.

Здесь заперто ровно одно свойство: значение, объявленное в спеке, обязано
буквально встречаться в артборде, который человек видел. Всё остальное —
контраст, семейства, ступени — считается из тех же файлов, а не с его слов.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import PKG, skill_text  # noqa: E402

TOOL = PKG / "tools" / "design_pull.py"
_s = importlib.util.spec_from_file_location("ss_design_pull", TOOL)
dp = importlib.util.module_from_spec(_s)
_s.loader.exec_module(dp)

ДОСКА = """<div style="background:{фон};color:{текст};font-family:'Söhne', sans-serif">
  <h1 style="font-size:48px;padding:24px">Заголовок</h1>
  <p style="font-size:16px;color:{приглушённый}">Подпись</p>
  <button style="background:{акцент};border-radius:6px;padding:12px">Действие</button>
</div>"""

ПАЛИТРА = {"фон": "#0a0a0c", "текст": "#f2f2f4",
           "приглушённый": "#86868f", "акцент": "#6355d9"}

ОТМЕТКА = {"откуда": "extract", "когда": "2026-08-24T10:00:00Z",
           "холст": "https://claude.ai/public/artifacts/пример"}


class Холст(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def холст(self, палитра: dict = None, токены: dict = None,
              доска: str = None, имя: str = "Main.dc.html",
              отметка: dict = None) -> Path:
        палитра = палитра or ПАЛИТРА
        (self.d / имя).write_text(доска if доска is not None
                                  else ДОСКА.format(**палитра), encoding="utf-8")
        if токены is not False:
            (self.d / "tokens.json").write_text(
                json.dumps(токены if токены is not None else палитра,
                           ensure_ascii=False), encoding="utf-8")
        if отметка is not False:
            (self.d / "pulled.json").write_text(
                json.dumps(отметка or ОТМЕТКА, ensure_ascii=False),
                encoding="utf-8")
        return self.d

    def запуск(self, *флаги: str):
        p = subprocess.run([sys.executable, str(TOOL), str(self.d), *флаги],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, p.stdout + p.stderr


class ПридуманногоЦветаНеБывает(Холст):
    """Сердце инструмента: спека не может назвать цвет, которого нет в дизайне."""

    def test_взятый_из_дизайна_проходит(self):
        self.холст()
        код, _ = self.запуск("--check")
        self.assertEqual(код, 0)

    def test_придуманный_валит(self):
        подмена = dict(ПАЛИТРА, акцент="#ff0055")
        self.холст(токены=подмена)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("которого в дизайне НЕТ", текст)
        self.assertIn("#ff0055", текст)

    def test_регистр_не_расхождение(self):
        """`#0A0A0C` и `#0a0a0c` — одно значение. Иначе ворота валят верное."""
        self.холст(токены={k: v.upper() for k, v in ПАЛИТРА.items()})
        код, _ = self.запуск("--check")
        self.assertEqual(код, 0)

    def test_короткая_запись_равна_длинной(self):
        палитра = dict(ПАЛИТРА, акцент="#63d")
        self.холст(палитра=dict(ПАЛИТРА, акцент="#6633dd"),
                   токены=палитра)
        код, _ = self.запуск("--check")
        self.assertEqual(код, 0)

    def test_запятые_в_rgb_не_расхождение(self):
        палитра = dict(ПАЛИТРА, акцент="rgb(99 85 217)")
        self.холст(палитра=dict(ПАЛИТРА, акцент="rgb(99, 85, 217)"),
                   токены=палитра)
        код, _ = self.запуск("--check")
        self.assertEqual(код, 0)


class ПроверятьНечемНеЗначитПрошло(Холст):

    def test_без_артбордов_код_два(self):
        код, текст = self.запуск("--check")
        self.assertEqual(код, 2, "пустой каталог принят за пройденную проверку")
        self.assertIn("ПРОВЕРЯТЬ НЕЧЕМ", текст)

    def test_сказано_на_чём_держатся_числа(self):
        _, текст = self.запуск("--check")
        self.assertIn("на слове агента", текст)

    def test_без_токенов_отказ(self):
        self.холст(токены=False)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("tokens.json", текст)


class РолиИКонтраст(Холст):

    def test_нет_главной_доски(self):
        self.холст(имя="Pricing.dc.html")
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("Main.dc.html", текст)

    def test_пятая_роль_валит(self):
        self.холст(токены=dict(ПАЛИТРА, успех="#22c55e"))
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("роли не те", текст)

    def test_контраст_ниже_порога_валит(self):
        тёмный = dict(ПАЛИТРА, текст="#1a1a1e")
        self.холст(палитра=тёмный, токены=тёмный)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("контраст текст", текст)

    def test_невычислимый_контраст_назван_а_не_замолчан(self):
        """Непонятая запись — это «не проверено», и так оно и должно звучать."""
        экзотика = dict(ПАЛИТРА, приглушённый="oklab(0.6 0.02 0.01)")
        self.холст(палитра=экзотика, токены=экзотика)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 0)
        self.assertIn("проверено НЕ БЫЛО", текст)

    def test_oklch_считается_а_не_пропускается(self):
        палитра = {"фон": "oklch(98% 0 0)", "текст": "oklch(0.2 0 0)",
                   "приглушённый": "oklch(0.5 0 0)", "акцент": "oklch(0.6 0.2 260)"}
        self.холст(палитра=палитра, токены=палитра)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 0, текст)
        self.assertNotIn("проверено НЕ БЫЛО", текст)


class СчётныеВеличины(Холст):

    def test_три_семейства_валят(self):
        доска = ДОСКА.format(**ПАЛИТРА) + (
            "<p style=\"font-family:Inter, sans-serif\">a</p>"
            "<p style=\"font-family:'Fira Code', monospace\">b</p>")
        self.холст(доска=доска)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("семейств шрифта 3", текст)

    def test_много_ступеней_кегля_это_наблюдение_а_не_отказ(self):
        """С утверждённым дизайном ворота не спорят — они его пересказывают."""
        доска = ДОСКА.format(**ПАЛИТРА) + "".join(
            f"<p style=\"font-size:{n}px\">x</p>" for n in (11, 13, 20, 28, 36))
        self.холст(доска=доска)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 0)
        self.assertIn("ступеней кегля", текст)

    def test_rem_приводится_к_пикселям(self):
        доска = ДОСКА.format(**ПАЛИТРА) + "<p style=\"font-size:1rem\">x</p>"
        self.холст(доска=доска)
        _, текст = self.запуск()
        self.assertIn("16", текст)

    def test_ритм_и_картинки_названы(self):
        доска = (ДОСКА.format(**ПАЛИТРА)
                 + "<div style=\"padding:13px\"><img src=\"a.png\"></div>")
        self.холст(доска=доска)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 0)
        self.assertIn("мимо шага 4px", текст)
        self.assertIn("без width/height", текст)


class ЗелёныйНеЗначитПровереноВсё(Холст):

    def test_непроверенное_названо_и_на_зелёном(self):
        self.холст()
        код, текст = self.запуск("--check")
        self.assertEqual(код, 0)
        self.assertIn("НЕ ПРОВЕРЕНО здесь", текст)
        self.assertIn("состояния", текст)

    def test_показ_без_check_не_гейт(self):
        """`--check` — это гейт. Без него инструмент показывает, а не судит."""
        self.холст(токены=dict(ПАЛИТРА, акцент="#ff0055"))
        код, _ = self.запуск()
        self.assertEqual(код, 0)
        код_гейта, _ = self.запуск("--check")
        self.assertEqual(код_гейта, 1)

    def test_json_машинный(self):
        self.холст()
        код, текст = self.запуск("--check", "--json")
        self.assertEqual(код, 0)
        d = json.loads(текст)
        self.assertEqual(d["код"], 0)
        self.assertEqual(len(d["величины"]["цвета"]), 4)


class ОткудаВзялисьДоски(Холст):
    """Черновик агента и утверждённый человеком дизайн на диске неразличимы.

    Гейт, не спрашивающий об этом, подтверждает работу агента его же руками:
    он рисует доски, он же их проверяет, и «дизайн подтянут» становится
    правдой про файлы и ложью про человека.
    """

    def test_без_отметки_отказ(self):
        self.холст(отметка=False)
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("выглядят одинаково", текст)

    def test_выдуманное_происхождение_отказ(self):
        self.холст(отметка={"откуда": "черновик", "когда": "2026-08-24T10:00:00Z",
                            "холст": "https://x"})
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("так не бывает", текст)

    def test_без_ссылки_на_холст_отказ(self):
        """«Человек видел» без ссылки нельзя проверить даже вручную."""
        self.холст(отметка={"откуда": "утверждён", "когда": "2026-08-24T10:00:00Z",
                            "холст": "  "})
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("не назван холст", текст)

    def test_правка_после_отметки_отказ(self):
        """Агент поправил доску после одобрения — человек утвердил не это."""
        import os
        self.холст()
        д = self.d / "Main.dc.html"
        os.utime(д, (д.stat().st_atime + 600, д.stat().st_mtime + 600))
        код, текст = self.запуск("--check")
        self.assertEqual(код, 1)
        self.assertIn("ПОСЛЕ отметки", текст)

    def test_происхождение_печатается_и_на_зелёном(self):
        self.холст()
        код, текст = self.запуск("--check")
        self.assertEqual(код, 0)
        self.assertIn("откуда: extract", текст)

    def test_утверждён_как_есть_тоже_годится(self):
        """Человек посмотрел и ничего не менял — это законный путь, не отказ."""
        self.холст(отметка={"откуда": "утверждён", "когда": "2026-08-24T10:00:00Z",
                            "холст": "https://claude.ai/public/artifacts/x"})
        код, _ = self.запуск("--check")
        self.assertEqual(код, 0)


class ОтметкаСтавитсяКодом(Холст):

    def test_record_пишет_отметку(self):
        self.холст(отметка=False)
        код, _ = self.запуск("--record", "extract", "--canvas", "https://x/y")
        self.assertEqual(код, 0)
        d = json.loads((self.d / "pulled.json").read_text("utf-8"))
        self.assertEqual(d["откуда"], "extract")
        self.assertEqual(d["холст"], "https://x/y")
        self.assertTrue(d["когда"].endswith("Z"))

    def test_record_без_холста_отказ(self):
        self.холст(отметка=False)
        код, текст = self.запуск("--record", "extract")
        self.assertEqual(код, 3)
        self.assertIn("не назван холст", текст)
        self.assertFalse((self.d / "pulled.json").exists())

    def test_record_чужого_слова_отказ(self):
        self.холст(отметка=False)
        код, текст = self.запуск("--record", "нормально", "--canvas", "https://x")
        self.assertEqual(код, 3)
        self.assertIn("принимает только", текст)


class Цвет(unittest.TestCase):
    """Арифметика проверяется на известных величинах, а не на глаз."""

    def test_яркость_белого_и_чёрного(self):
        self.assertAlmostEqual(dp.яркость("#ffffff"), 1.0, places=4)
        self.assertAlmostEqual(dp.яркость("#000000"), 0.0, places=4)

    def test_контраст_чёрного_к_белому(self):
        self.assertAlmostEqual(dp.контраст("#000", "#fff"), 21.0, places=2)

    def test_hsl_совпадает_с_hex(self):
        self.assertAlmostEqual(dp.яркость("hsl(0 100% 50%)"),
                               dp.яркость("#ff0000"), places=4)

    def test_непонятое_не_чёрное(self):
        """None вместо нуля: ноль дал бы непонятому цвету контраст 21:1."""
        self.assertIsNone(dp.яркость("var(--brand)"))
        self.assertIsNone(dp.контраст("var(--brand)", "#fff"))


class ФазаВедётЧерезХолст(unittest.TestCase):
    """Механизм, до которого не дотягивается скилл, неотличим от отсутствующего."""

    @classmethod
    def setUpClass(cls):
        cls.текст = skill_text("go")

    def test_дизайн_делается_на_холсте_внутри(self):
        self.assertIn("ДИЗАЙН ДЕЛАЕТСЯ ЗДЕСЬ, НА ХОЛСТЕ", self.текст)

    def test_правит_и_утверждает_человек(self):
        """Черновик рисует агент — но увидеть и поправить обязан человек, до кода."""
        self.assertIn("Код в это время не пишется", self.текст)

    def test_фаза_зовёт_гейт_подтягивания(self):
        self.assertIn("design_pull.py", self.текст)
        self.assertIn("--check", self.текст)

    def test_дизайн_объявляется_в_состоянии_проекта(self):
        """Без объявления гейт остаётся необязательным — то есть невыполняемым."""
        self.assertIn("design_dir", self.текст)

    def test_запасной_путь_назван_вместе_с_его_ценой(self):
        self.assertIn("design_brief.py", self.текст)
        self.assertIn("держатся на твоём слове", self.текст)


if __name__ == "__main__":
    unittest.main()
