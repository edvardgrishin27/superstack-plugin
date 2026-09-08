#!/usr/bin/env python3
"""Упавший набор остаётся упавшим при любых словах в его выводе.

Сценарий пришёл из внешней проверки и переписан здесь регрессионным тестом
ПРАВИЛЬНОГО поведения. Признаки пустого набора искались во ВСЁМ выводе
проверки, при любом коде возврата, — а в этот вывод попадает тело чужого
теста. Достаточно было, чтобы в сообщении assert встретилась фраза про
ненайденные тесты, и честно упавший прогон читался как «проверять было
нечего»: гейт отдавал код 2 вместо 1, а Stop-хук разрешал закрыть ход
«без доказательства» вместо «красное».

Починка не в том, чтобы читать код возврата вместо вывода: это уже пробовали,
и красный `make` (он выходит двойкой) перестал блокировать ход. Разделены два
ИСТОЧНИКА слов: то, что печатает о своём наборе сам прогонщик (итоговая
строка pytest, число собранных тестов), и то, что печатает содержимое чужого
теста. Первое перевешивает второе.

Обратный контроль стоит здесь ровно столько же, сколько прямой: по-настоящему
пустой набор обязан по-прежнему читаться как «проверять нечего», а зелёный —
как зелёный. Иначе починка «объявить красным вообще всё» прошла бы этот файл
незамеченной.

Тест самодостаточен: только стандартная библиотека и сам инструмент.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

РЕПО = Path(__file__).resolve().parents[2]

_спек = importlib.util.spec_from_file_location(
    "ss_verif" "y_runner" "_verdict",
    РЕПО / "plugins" / "superstack" / "tools" / "verify.py")
vf = importlib.util.module_from_spec(_спек)
# Регистрация ДО исполнения: dataclass ищет свой модуль в sys.modules и без
# этого падает на разборе аннотаций.
sys.modules[_спек.name] = vf
_спек.loader.exec_module(vf)

#: Прогонщик — состояние МАШИНЫ, а не свойство кода. Спрашивается один раз:
#: ответ одинаков для всего файла, а каждый вопрос стоит запуска процесса.
ПРОГОНЩИК_ЕСТЬ = vf._pytest_available()

#: Тело теста, который честно падает и при этом печатает в вывод прогона две
#: фразы из списка признаков пустого набора. Обе приходят из ТЕЛА теста —
#: прогонщик о своём наборе такого не говорил.
КРАСНЫЙ_С_ФРАЗАМИ = (
    "def test_сломан():\n"
    '    assert "No tests found" == "Unexpected error", "no tests ran"\n'
)


@unittest.skipUnless(ПРОГОНЩИК_ЕСТЬ, "нет pytest: живой прогон не выполнен")
class Проект(unittest.TestCase):
    """Временное дерево, в котором крутится НАСТОЯЩИЙ pytest.

    Подставной вывод тут не годится: проверяется ровно стык «что печатает
    живой прогонщик» — «как это прочитал гейт», и подмена вывода проверяла бы
    представление автора теста о pytest, а не сам pytest.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.корень = Path(tmp.name)
        (self.корень / "tests").mkdir()

    def набор(self, тело: str) -> None:
        (self.корень / "tests" / "test_набор.py").write_text(
            тело, encoding="utf-8")

    def вердикт(self) -> dict:
        проверки = vf.detect_checks(self.корень)
        self.assertEqual([c.id for c in проверки], ["py:test"],
                         "объявленная проверка проекта опознана не та")
        return vf.verdict([vf._run(c, self.корень) for c in проверки],
                          self.корень)


class КрасныйНаборОстаётсяКрасным(Проект):
    """Ровно тот случай из внешней проверки."""

    def test_фразы_из_тела_теста_не_гасят_падение(self):
        self.набор(КРАСНЫЙ_С_ФРАЗАМИ)
        v = self.вердикт()
        # Позитивный контроль сценария: если завтрашний pytest перестанет
        # печатать тело assert, тест обязан сказать об этом, а не тихо
        # проходить на выводе, в котором проверять уже нечего.
        хвост = v["evidence"][0]["output_tail"]
        for фраза in ("No tests found", "no tests ran"):
            self.assertIn(фраза, хвост,
                          f"сценарий выродился: «{фраза}» в выводе прогона нет")
        self.assertEqual(v["status"], "fail", v)
        self.assertEqual(vf.EXIT[v["status"]], 1,
                         "красный набор закрывает ход кодом «нечего проверять»")


class ПустойНаборОстаётсяПустым(Проект):
    """Обратный контроль: сужение не имеет права красить всё подряд."""

    def test_ни_одного_теста_читается_как_проверять_нечего(self):
        self.набор("# в этом файле нет ни одного теста\n")
        v = self.вердикт()
        self.assertEqual(v["status"], "absent", v)
        self.assertEqual(vf.EXIT[v["status"]], 2,
                         "«тестов нет» подменилось на «тесты красные»")

    def test_зелёный_набор_остаётся_зелёным(self):
        self.набор("def test_ок():\n    assert True\n")
        v = self.вердикт()
        self.assertEqual(v["status"], "pass", v)
        self.assertEqual(vf.EXIT[v["status"]], 0, v)


class ОтчётПрогонщикаЧитаетсяЧислами(unittest.TestCase):
    """Итоговая строка pytest разбирается машинно, а не совпадением подстроки.

    Живого прогона тут нет намеренно: это разбор ФОРМЫ отчёта, и каждая форма
    стоит одной строки вместо одного процесса.
    """

    def test_итоговая_строка_перевешивает_фразу_из_тела_теста(self):
        вывод = ("E       AssertionError: assert 'No tests found' == 'Unexpected error'\n"
                 "1 failed in 0.01s\n")
        self.assertEqual(vf._pytest_report(вывод), vf.REPORT_FAILED)

    def test_пустоту_прогонщик_называет_сам(self):
        for строка in ("no tests ran in 0.00s",
                       "=========== no tests ran in 0.00s ============"):
            with self.subTest(строка=строка):
                self.assertEqual(vf._pytest_report(строка + "\n"),
                                 vf.REPORT_EMPTY)

    def test_собранный_ноль_читается_числом(self):
        """Число собранных тестов — второй машинный ответ прогонщика: он есть
        и там, где итоговой строки в выводе не осталось."""
        self.assertEqual(vf._pytest_report("collected 0 items\n"),
                         vf.REPORT_EMPTY)

    def test_зелёная_сводка_не_пустая(self):
        self.assertEqual(vf._pytest_report("2 passed, 1 warning in 0.03s\n"),
                         vf.REPORT_RAN)

    def test_чужой_вывод_не_выдаётся_за_отчёт_pytest(self):
        """None — «это не его вывод», и решать по нему нечего."""
        self.assertIsNone(vf._pytest_report("собрано и запущено\n"))


class НеразобранныйОтчётНеЗелёный(unittest.TestCase):
    """«Не смог разобрать отчёт» — отдельное состояние.

    Молча превратить его в «пусто» значит вернуть ту самую дыру; молча
    превратить в «зелено» — выдать непрочитанное за доказательство. Ход при
    этом закрывается кодом 2, как и всякое непроверенное.
    """

    def test_незнакомые_слова_сводки_не_читаются_ни_пусто_ни_зелено(self):
        итог = vf._pytest_report("2 rerun in 0.10s\n")
        self.assertEqual(итог, vf.REPORT_UNCLEAR)
        self.assertNotEqual(итог, vf.REPORT_EMPTY)
        self.assertNotEqual(итог, vf.REPORT_RAN)

    def test_вердикт_гаснет_а_не_зеленеет(self):
        c = vf.Check("py:test", "тесты",
                     ("python3", "-m", "pytest", "-q"), "почему")
        v = vf.verdict([vf.Result(c, 0, "2 rerun in 0.10s", False, True)],
                       Path("/нет-такого"))
        self.assertEqual(v["status"], "absent", v)
        self.assertEqual(vf.EXIT[v["status"]], 2, v)
        self.assertTrue(v["blockers"], "непрочитанный отчёт не назван блокером")


if __name__ == "__main__":
    unittest.main()
