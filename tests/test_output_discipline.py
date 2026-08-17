#!/usr/bin/env python3
"""Сколько система говорит человеку — и как она спрашивает.

Почему это заперто тестом, а не оставлено на вкус.

Прогон, который работал ПРАВИЛЬНО и объяснял себя на каждом шаге, живой
пользователь описал как «невозможно переварить, давит». Дефекта в работе не
было — был объём речи. Для новичка это отказ такой же тяжести, как красный
гейт: он перестаёт читать, а значит перестаёт видеть вопросы, на которые может
ответить только он.

Правило можно записать в скилл и потерять при следующей правке — молча, потому
что ни один прогон от этого не покраснеет. Здесь оно держится.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import REPO  # noqa: E402

GO = REPO / "plugins" / "superstack-build" / "skills" / "go" / "SKILL.md"
INSTALL = REPO / "plugins" / "superstack-core" / "skills" / "superstack" / "SKILL.md"

#: Скиллы, ведущие ДИАЛОГ с человеком. `/what`, `/fix`, `/oops` отвечают на
#: конкретный вопрос и разговора не ведут — бюджеты им не нужны.
CONVERSATIONAL = (GO, INSTALL)


class TestTheOutputBudgetIsWrittenDown(unittest.TestCase):

    def test_each_conversational_skill_carries_the_budget(self):
        for s in CONVERSATIONAL:
            with self.subTest(skill=s.name):
                t = s.read_text("utf-8")
                self.assertIn("Сколько говорить", t,
                              f"{s.parent.name}: нет раздела о бюджете речи")
                self.assertIn("Бюджет", t)

    def test_the_deletion_test_is_stated(self):
        """Единственная проверка, которую можно применить к любому сообщению:
        останется ли оно верным, если его удалить."""
        for s in CONVERSATIONAL:
            with self.subTest(skill=s.name):
                self.assertIn("если удалить", s.read_text("utf-8").replace(
                    "если я это удалю", "если удалить"))

    def test_command_output_is_never_pasted(self):
        for s in CONVERSATIONAL:
            with self.subTest(skill=s.name):
                self.assertIn("Никогда не вставляй", s.read_text("utf-8"))


class TestHowQuestionsAreAsked(unittest.TestCase):
    """Четыре правила, каждое из которых уже стоило прогона.

    Умолчание вместо допроса — потому что «где будем хостить?» новичок не может
    ответить правильно, и вопрос перекладывает на него работу системы.

    Вопрос про уже случившееся — потому что «опишите сценарий приёмки» это
    задание, которого он не просил; превратить воспоминание в критерии обязан
    тот, кто умеет.

    Вариант «объясни сначала» — потому что человек, не желающий признаться, что
    слышит слово впервые, угадает, и его догадка будет выглядеть как решение.
    Живой случай: выбор календаря со слотами вместо простой заявки был сделан
    без объяснения, чего он стоит.

    Рекомендация в каждом вопросе — потому что пустой выбор перед новичком это
    не свобода, а тупик.
    """

    def setUp(self):
        self.t = GO.read_text("utf-8")

    def test_three_rounds_not_a_questionnaire(self):
        self.assertIn("Три раунда", self.t)

    def test_defaults_are_proposed_not_interrogated(self):
        self.assertIn("Предлагай умолчание", self.t)

    def test_questions_ask_about_what_already_happened(self):
        self.assertIn("что уже случилось", self.t)

    def test_hard_words_offer_an_explanation_first(self):
        self.assertIn("объясни сначала", self.t)

    def test_every_question_carries_a_recommendation(self):
        self.assertIn("Recommended", self.t)



class TestDesignDirectionIsAPhase(unittest.TestCase):
    """Вкус, не переведённый в числа, не переживает первого таска.

    Он остаётся мнением, а мнение проигрывает любому доводу исполнителя: тот
    работает по критериям приёмки, и «красиво» в них не входит.

    Живой случай: единственным требованием про внешний вид было «как
    linear.app — вот прям такой уровень», и в первой редакции спеки тёмная тема
    была вычеркнута как лишняя — при том что единственный названный ориентир
    тёмный. Поймала это слепая сверка, а не механизм: механизма не было.
    """

    def setUp(self):
        self.t = GO.read_text("utf-8")

    def test_the_phase_exists_and_is_conditional(self):
        self.assertIn("НАПРАВЛЕНИЕ", self.t)
        self.assertIn("Пропускается молча", self.t,
                      "фаза обязана пропускаться там, где интерфейса нет")

    def test_taste_is_turned_into_numbers(self):
        for measure in ("цветовых токенов", "ступеней кегля", "контраст",
                        "сдвиг вёрстки"):
            with self.subTest(measure=measure):
                self.assertIn(measure, self.t)

    def test_the_humans_reference_cannot_be_dropped(self):
        self.assertIn("Вычеркнуть\nназванный ориентир нельзя", self.t)

    def test_it_names_which_skill_to_use(self):
        for skill in ("frontend-design", "impeccable", "refactoring-ui-skills"):
            with self.subTest(skill=skill):
                self.assertIn(skill, self.t,
                              "фаза не говорит, каким скиллом исполнять")

    def test_clean_minimal_is_refused_as_a_direction(self):
        """«Чисто и минималистично» — это отсутствие выбора, а не выбор."""
        self.assertIn("вариантом не является", self.t)

if __name__ == "__main__":
    unittest.main()


class TestConfirmationLevelIsTheHumansChoice(unittest.TestCase):
    """Сколько раз система останавливается — решение человека, а не умолчание.

    Взято из шкалы автономии, но переписано под то, чем этот продукт является:
    у нас нет автомержа и не может быть, зато есть три места, где работа встаёт
    и ждёт. Одно умолчание на всех означает, что новичка либо дёргают на каждом
    таске (и он перестаёт читать к третьему), либо не спрашивают там, где он
    хотел бы посмотреть.

    Главное здесь — не сама шкала, а список того, чего она НЕ отключает:
    гейты, подтверждение плана на первых двух ступенях, слепую приёмку и G4.
    Шкала, которой можно выключить проверку, — это не шкала доверия, а тумблер
    «не проверяй».
    """

    def setUp(self):
        self.t = GO.read_text("utf-8")

    def test_the_ladder_exists_with_three_rungs(self):
        self.assertIn("Ступень подтверждений", self.t)
        for rung in ("| 1 |", "| 2 |", "| 3 |"):
            with self.subTest(rung=rung):
                self.assertIn(rung, self.t)

    def test_it_is_asked_not_assumed(self):
        self.assertIn("сколько подтверждений он хочет", self.t)

    def test_gates_survive_every_rung(self):
        self.assertIn("гейты остаются гейтами на всех трёх", self.t)

    def test_blind_acceptance_cannot_be_switched_off(self):
        self.assertIn("обязательны всегда", self.t)

    def test_the_choice_is_recorded(self):
        """Незаписанная договорённость к третьему таску забыта, и система
        тихо съезжает к своему умолчанию."""
        self.assertIn("записывается в манифест", self.t)


class TestTheRunLeavesASummaryInTheProject(unittest.TestCase):
    """Отчёт человек читает один раз, и он исчезает вместе с окном.

    Следующий заход — другой сессией, с чистым контекстом — смотрит в память
    проекта раньше, чем в исходники. Всё, что прогон понял и не записал туда,
    он будет выяснять заново: те же вопросы человеку, те же находки, та же
    цена. Сводка в чате этого не решает — её там уже нет.
    """

    def setUp(self):
        self.t = GO.read_text("utf-8")

    def test_the_summary_goes_to_project_memory(self):
        self.assertIn("Сводка остаётся в проекте", self.t)
        self.assertIn('memory_file.py)" set .', self.t)

    def test_it_names_what_belongs_in_it(self):
        for part in ("что теперь существует", "что ждёт человека",
                     "что решено и почему", "обо что споткнулись"):
            with self.subTest(part=part):
                self.assertIn(part, self.t)

    def test_it_forbids_praise_and_unmeasured_numbers(self):
        """Сводка, повторяющая отчёт и хвалящая себя, не читается никем — и
        первым перестаёт читать её следующий прогон."""
        self.assertIn("Чего в сводке не бывает", self.t)
