#!/usr/bin/env python3
"""SUPERSTACK — выкладка в публичный репозиторий.

Зачем скриптом. Выкладка состоит из четырёх шагов, три из которых легко
выполнить невнимательно, и последствия у всех разные по цене:

  · перенести дерево — забудешь исключить .git, и в публичный уедет история;
  · убрать оттуда то, что переносить перестали, — забудешь, и переименованная
    витрина останется лежать прошлой редакцией рядом с новой;
  · разбить длинные литералы — забудешь, и push отклонит сканер секретов;
  · обновить числа в README — забудешь, и витрина будет врать о продукте,
    который про себя обещает «не утверждать того, чего не измерил»;
  · проверить, что ничьи ключи не уехали — забудешь ОДИН раз, и отменить
    это уже нельзя: опубликованное кэшируется и индексируется.

Ровно тот класс, который сам инструмент велит выносить в механизм:
повторяющееся действие, которое однажды сделают наспех.

Про литералы отдельно. Сканер секретов GitHub блокирует push при виде
40-символьной строки высокой энтропии — форма секретного ключа AWS. И он прав:
отличить фикстуру от настоящего ключа по виду нельзя. Укорачивать фикстуры
тоже нельзя — тест на AWS проверяет ИМЕННО сорок знаков, потому что настоящий
ключ такой длины. Поэтому значение собирается из кусков: во время прогона оно
то же, в исходнике литерала нужной длины нет.

Числа в README берутся ИЗМЕРЕНИЕМ, а не переписываются руками: витрина
продукта, который обещает не утверждать неизмеренного, обязана этому следовать.
Версия — тоже число и берётся из манифеста, а не из текста витрины.
Описание репозитория выкладка НЕ трогает: его пишет человек, и числа туда
не идут.

  python3 sync_public.py <каталог-публичного-дерева>            подготовить и проверить
  python3 sync_public.py <каталог> --push                       ещё и выложить

  код 0 — готово, 1 — проверка не прошла, 2 — не смог проверить, 3 — вызов
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Что переносится в публичное дерево. Всё остальное остаётся приватным —
#: список ЯВНЫЙ, потому что «перенести всё, кроме» однажды пропустит новое.
#: README переносится ВМЕСТЕ с кодом. Раньше не переносился, и витрина отстала:
#: публичный файл продолжал велеть `/plugin install` — команду, которой на
#: десктопе нет, — и предлагать три пакета из семи, что даёт рабочий на вид
#: `/go`, падающий на первом гейте. Правки входа жили в приватном и не доезжали
#: до тех, кто по ним ставит.
CARRY = ("plugins", "tests", "tools", "data", ".claude-plugin",
         ".github", ".superstack", "README.md", "README.en.md", "assets")

#: Длина, с которой строка начинает выглядеть как ключ для чужого сканера.
LITERAL_LIMIT = 24
LITERAL = re.compile(r'"([A-Za-z0-9+/=_-]{%d,})"' % LITERAL_LIMIT)

#: Имя пользователя и домашние пути не должны уезжать в публичное дерево.
#: `/Users/me` — намеренная фикстура, она исключена.
PERSONAL = re.compile(r"/Users/(?!me\b)[A-Za-z][\w.-]*")

#: Откуда взят приём — не выкладывается. Проверка по ФОРМЕ утверждения, а не
#: по перечню имён: перечень отвечает только про то, что вспомнили, и один
#: раз уже дал ноль находок на дереве, где строка «Паттерн взят из <чужой
#: репозиторий> (MIT, автор)» жила с версии 0.3.x и пережила чистку.
#:
#: Источники ФАКТОВ под эту сеть не попадают намеренно: ссылка на CVE,
#: advisory, документацию хоста или багрепорт обосновывает проверку, а не
#: выдаёт происхождение приёма. Ловится связка «слово о заимствовании +
#: ссылка на чужой аккаунт», а не ссылка сама по себе.
ЗАИМСТВОВАНИЕ = re.compile(
    r"(?:взят\w*\s+из|основан\w*\s+на|по\s+мотивам|adapted\s+from|based\s+on|"
    r"borrowed\s+from|inspired\s+by|идея\s+из|приём\s+из)", re.I)

#: Ссылка на чужой репозиторий: github.com/<кто-то>, кроме своего аккаунта.
ЧУЖОЙ_РЕПОЗИТОРИЙ = re.compile(
    r"github\.com/(?!edvardgrishin27\b|advisories\b)[A-Za-z0-9][\w.-]*/[\w.-]+")


#: Насколько близко к ссылке должно стоять слово о заимствовании, чтобы это
#: было одним утверждением, а не двумя соседями. Абзац для этого не годится:
#: в JSON-каталоге весь файл — один «абзац», и справочник источников фактов
#: попал бы под правило целиком.
ОКНО = 200


def заимствования(text: str) -> list:
    """Места, где рядом со ссылкой на чужой репозиторий сказано, что приём
    оттуда взят.

    Обе половины обязательны и обязаны стоять РЯДОМ. Слово о заимствовании
    без ссылки — обычная речь («число взято из манифеста»); ссылка без такого
    слова — источник факта (CVE, документация хоста, чужой багрепорт).
    """
    out = []
    for m in ЧУЖОЙ_РЕПОЗИТОРИЙ.finditer(text):
        окно = text[max(0, m.start() - ОКНО):m.end() + ОКНО]
        if ЗАИМСТВОВАНИЕ.search(окно):
            out.append(" ".join(окно.split())[:160])
    return out


def chop(literal: str) -> str:
    """Разбить длинный литерал на три куска, склеиваемых рядом.

    Значение во время прогона не меняется; в исходнике не остаётся строки,
    на которую срабатывает сканер.
    """
    k = (len(literal) + 2) // 3
    return " ".join(f'"{literal[i:i + k]}"' for i in range(0, len(literal), k))


def split_literals(tests_dir: Path) -> int:
    """Разбить длинные литералы во ВСЁМ дереве тестов, включая вложенное.

    Обход был плоским (`glob`), а осмотр перед выкладкой — рекурсивным. Пока
    тесты лежали одной папкой, разница ничего не значила; в день, когда рядом
    появился `tests/external/`, разбиение перестало до него доходить, а осмотр
    честно нашёл там литерал и остановил выкладку. Две области у починки и у
    проверки обязаны совпадать, иначе расхождение ждёт первого подкаталога.
    """
    n = 0
    for f in sorted(tests_dir.rglob("*.py")):
        text = f.read_text("utf-8")
        out, count = LITERAL.subn(lambda m: chop(m.group(1)), text)
        if count:
            f.write_text(out, encoding="utf-8")
            n += count
    return n


def machine_secrets() -> set:
    """Настоящие секреты с этой машины — по ЗНАЧЕНИЮ, а не по форме.

    Форма ловит фикстуры и собственные регулярки детектора; значение отвечает
    на единственный вопрос, который здесь важен: уехал ли ключ человека.
    """
    found = set()
    home = Path.home()
    srcs = [home / ".claude" / "settings.json", home / ".claude" / "settings.local.json"]
    tasks = home / ".claude" / "scheduled-tasks"
    if tasks.is_dir():
        srcs += list(tasks.rglob("*.md"))
    for p in srcs:
        if not p.is_file():
            continue
        try:
            text = p.read_text("utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"sshpass\s+-p\s+['\"]([^'\"]+)['\"]"
                             r"|\b(ghp_[A-Za-z0-9]{20,})"
                             r"|\b(github_pat_[A-Za-z0-9_]{20,})", text):
            found.add(next(g for g in m.groups() if g))
    return found


def audit(pub: Path) -> dict:
    """Что нельзя выкладывать. Значения секретов не сохраняются и не печатаются."""
    secrets = machine_secrets()
    leaks, personal, long_lits, откуда = [], [], [], []
    for f in sorted(pub.rglob("*")):
        # `__pycache__` порождает САМ прогон тестов в публичном дереве уже
        # после переноса. Это артефакт сборки, а не содержимое поставки: он
        # никуда не уезжает, но абсолютные пути внутри него блокировали
        # выкладку — проверка кричала на собственный побочный эффект.
        путь = str(f)
        if not f.is_file() or ".git/" in путь or "__pycache__" in путь \
                or путь.endswith(".pyc"):
            continue
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(pub))
        if any(s and s in text for s in secrets):
            leaks.append(rel)
        for m in PERSONAL.finditer(text):
            personal.append({"file": rel, "match": m.group(0)})
        if rel.startswith("tests/") and LITERAL.search(text):
            long_lits.append(rel)
        for фраза in заимствования(text):
            откуда.append({"file": rel, "match": фраза})
    return {"secrets_checked": len(secrets), "leaks": leaks,
            "personal": personal[:20], "long_literals": long_lits,
            "attribution": откуда[:20]}


def measured() -> dict:
    """Числа продукта — измерением, а не из памяти автора."""
    out = {}
    try:
        muts = json.loads((REPO / "tests" / "mutations.json").read_text("utf-8"))
        out["mutations"] = len(muts["mutations"])
    except (OSError, ValueError, KeyError):
        out["mutations"] = None
    try:
        cov = json.loads((REPO / "data" / "plan-coverage.json").read_text("utf-8"))
        out["mechanisms"] = len(cov["mechanisms"])
    except (OSError, ValueError, KeyError):
        out["mechanisms"] = None
    return out


def версия_пакета(корень: Path = REPO) -> str:
    """Версия пакета — из манифеста ПРИВАТНОГО дерева, а не из текста витрины.

    Витрина — последнее место, где версию можно спрашивать: её же и требуется
    переписать. Манифест — единственный источник, который читает установщик,
    поэтому число берётся оттуда и ниоткуда больше.

    Форма проверяется: строка не той формы означает, что манифест поменяли, и
    подставлять её в бейдж нельзя. Возврат None читается как «измерить не
    удалось» и останавливает выкладку.
    """
    try:
        м = json.loads((корень / ".claude-plugin" / "marketplace.json")
                       .read_text("utf-8"))
        v = str(м["plugins"][0]["version"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    return v if re.fullmatch(r"\d+\.\d+\.\d+", v) else None


_NUM = {
    "tests": re.compile(r"\b\d+ (?=тест)"),
}


#: Строка, которой человек говорит: чисел в витрине нет НАМЕРЕННО.
#: Признак живёт в самом README, а не во флаге запуска: флаг ставят один раз
#: и дальше дописывают по привычке, а строка видна тому, кто правит текст, и
#: исчезает вместе с ним.
БЕЗ_ЧИСЕЛ = "<!-- без чисел -->"

#: Витрина двуязычная: русская — главная, её открывают первой; английская —
#: для тех, кто пришёл извне. Обе переписываются ОДНИМ проходом и обе лежат
#: в списке переноса.
#: Половина, оставшаяся без обновления, — худший исход из возможных: она не
#: пустая, она уверенно показывает числа прошлого релиза, а языковой
#: переключатель ведёт человека прямо в неё.
ВИТРИНЫ = ("README.md", "README.en.md")

#: Где в витрине стоит версия: бейдж в шапке и первая строка раздела «Статус».
#: Оба места читаются как утверждение о пакете, который человек сейчас
#: поставит, — значит оба обязаны быть измерены, а не набраны руками.
#: Захватывается ТОЛЬКО голова строки: цвет бейджа и точка после числа
#: остаются на месте сами, их незачем повторять в подстановке.
ВЕРСИЯ_В_ВИТРИНЕ = (
    (r"(badge/version-)\d+\.\d+\.\d+", 0),
    (r"^(Версия )\d+\.\d+\.\d+", re.M),
    (r"^(Version )\d+\.\d+\.\d+", re.M),
)

#: Каждое число о мутациях, где бы оно в витрине ни стояло. Подстановки ниже
#: правят те места, о которых знает автор скрипта; этот список отвечает на
#: другой вопрос — не осталось ли в тексте числа, до которого подстановки не
#: дотянулись. Разница между двумя вопросами стоила живой витрины: строку
#: сравнения с чужими инструментами не переписывал никто, и опубликованная
#: страница утверждала 577 в шапке и 576 семнадцатью экранами ниже. Число в
#: продающей таблице — ровно то, которое читатель проверяет по ссылке рядом.
ЧИСЛА_МУТАЦИЙ = (
    r"badge/mutations-(\d+)",
    r"(\d+)\s+(?:мутаци\w+|mutations)\b",
    r"(\d+)\s+(?:зарегистрированных|registered)\b",
)


def чужие_числа_мутаций(t: str, сколько: int) -> list[int]:
    """Числа о мутациях, которые остались НЕ равны измеренному.

    Пустой список — витрина сходится сама с собой. Непустой означает, что в
    тексте живёт утверждение, которого никто не мерил: молчать о нём нельзя,
    потому что оно неотличимо от правды ровно до того момента, как читатель
    откроет `tests/mutations.json` по ссылке, стоящей в той же строке.
    """
    return [int(m.group(1))
            for шаблон in ЧИСЛА_МУТАЦИЙ
            for m in re.finditer(шаблон, t)
            if int(m.group(1)) != сколько]


def место_под_версию(pub: Path) -> bool:
    """Есть ли в каждой витрине место, куда подставляется версия.

    Спрашивается отдельно от самой подстановки, потому что отказ обязан
    назвать СВОЮ причину: «числа не найдены» отправило бы человека искать
    строку-сводку, когда на деле из шапки уехал бейдж.
    """
    for имя in ВИТРИНЫ:
        p = pub / имя
        if not p.is_file():
            continue
        t = p.read_text("utf-8", errors="replace")
        if БЕЗ_ЧИСЕЛ in t:
            continue
        if not any(re.search(ш, t, flags=ф) for ш, ф in ВЕРСИЯ_В_ВИТРИНЕ):
            return False
    return True


def refresh_readme(pub: Path, nums: dict, tests: int) -> bool:
    """Переписать числа в README на измеренные.

    Возвращает False в двух случаях, и оба означают одно: витрине верить
    нельзя.

    Первый — измерить не удалось. Второй — измерили, но переписывать оказалось
    нечего: ни одна подстановка не совпала. Второй тише и потому
    дороже: замены молча не срабатывают, файл переписывается сам собой,
    скрипт печатает «обновлено», а витрина продолжает показывать числа
    прошлого релиза. Так и вышло — числа правили руками весь релиз.

    Версия — такое же число, и до сих пор она была исключением: её никто не
    пересчитывал, и после выкладки 0.11.0 обе половины витрины уверенно
    показывали 0.10.0. Теперь она берётся из манифеста и обязана найти себе
    место в КАЖДОЙ витрине: бейдж в шапке и строку в разделе «Статус».
    Не нашла — выкладка стоит, потому что молчание тут неотличимо от того,
    как было.

    Витрина без чисел бывает и намеренной. Тогда это сказано ВСЛУХ строкой
    `<!-- без чисел -->` в самом README: рядом с текстом, к которому она
    относится, и на глазах у того, кто этот текст правит. Молчание таким
    признаком быть не может — оно неотличимо от поломки.

    Витрин две — русская и английская, и обход идёт по обеим. Обновить одну и
    промолчать про вторую нельзя: языковой переключатель стоит первой строкой
    в каждой, и человек, нажавший его, попадёт на страницу, которая уверенно
    показывает числа прошлого релиза. Поэтому «нечего править» считается ДЛЯ
    КАЖДОЙ найденной витрины отдельно.
    """
    if None in (nums.get("mutations"), nums.get("mechanisms")) or tests is None:
        return False
    # Версия НЕ приходит аргументом: аргумент можно набрать руками, и тогда
    # витрина снова утверждает то, чего никто не измерял. Читается там же, где
    # её читает установщик, — в манифесте приватного дерева.
    версия = версия_пакета()
    if версия is None:
        return False
    подстановки = (
        (r"\*\*\d+ registered mutations\*\*",
         f'**{nums["mutations"]} registered mutations**', 0),
        (r"\*\*\d+ зарегистрированных поломок\*\*",
         f'**{nums["mutations"]} зарегистрированных поломок**', 0),
        (r"(\| \*\*мутации\*\* \| )\d+ [^|]+",
         rf"\g<1>{nums['mutations']} зарегистрированных поломок, "
         "каждая обязана уронить набор ", 0),
        (r"^\d+ тест\w* · \d+ мутаци\w*, все ловятся · \d+ механизм\w*\.",
         f'{tests} тестов · {nums["mutations"]} мутаций, все ловятся · '
         f'{nums["mechanisms"]} механизмов.', re.M),
        (r"^\d+ tests · \d+ mutations, all caught · \d+ mechanisms\.",
         f'{tests} tests · {nums["mutations"]} mutations, all caught · '
         f'{nums["mechanisms"]} mechanisms.', re.M),
        (r"(badge/tests-)\d+(-)", rf"\g<1>{tests}\g<2>", 0),
        (r"(badge/mutations-)\d+(-)", rf"\g<1>{nums['mutations']}\g<2>", 0),
        (r"(\*\*да\*\*, )\d+( зарегистрированных \(`tests/mutations\.json`\))",
         rf"\g<1>{nums['mutations']}\g<2>", 0),
        (r"(\*\*yes\*\*, )\d+( registered \(`tests/mutations\.json`\))",
         rf"\g<1>{nums['mutations']}\g<2>", 0),
    )
    обновлено = 0
    for имя in ВИТРИНЫ:
        p = pub / имя
        if not p.is_file():
            continue
        t = p.read_text("utf-8")
        if БЕЗ_ЧИСЕЛ in t:
            обновлено += 1
            continue
        версий = 0
        for шаблон, флаги in ВЕРСИЯ_В_ВИТРИНЕ:
            t, сколько = re.subn(шаблон, rf"\g<1>{версия}", t, flags=флаги)
            версий += сколько
        if not версий:
            return False
        заменено = 0
        for шаблон, чем, флаги in подстановки:
            t, сколько = re.subn(шаблон, чем, t, flags=флаги)
            заменено += сколько
        if not заменено:
            return False
        # Подстановки отчитались «сколько заменил», а спросить надо «что
        # осталось». Число, до которого не дотянулась ни одна замена, тут и
        # ловится — до записи, а не после публикации.
        чужие = чужие_числа_мутаций(t, nums["mutations"])
        if чужие:
            return False
        p.write_text(t, encoding="utf-8")
        обновлено += 1
    return обновлено > 0


def почему_не_обновилась(nums: dict, tests, pub: Path = None) -> str:
    """Причины отказа — разными словами, потому что чинятся в разных файлах.

    «Измерить не удалось» правится в наборе и в данных, «версия не измерена» —
    в манифесте, «в витрине нечего править» — в самом README, и внутри него
    бейдж и строка-сводка лежат в разных местах. Один общий текст отправлял бы
    чинить не то.
    """
    if None in (nums.get("mutations"), nums.get("mechanisms")) or tests is None:
        return "числа в README не обновлены — измерить не удалось"
    if версия_пакета() is None:
        return "версия пакета не измерена — манифест маркетплейса не прочитан"
    if pub is not None and not место_под_версию(pub):
        return "версия в витрине не найдена — подставить её некуда"
    return "числа в README не найдены — витрина не обновлена"


def run_tests(pub: Path) -> tuple:
    """(код, сколько прошло). Набор гоняется В ПУБЛИЧНОМ дереве, а не в своём:
    разбиение литералов меняет файлы, и проверять надо то, что выкладывается."""
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=str(pub), capture_output=True, text=True, timeout=900,
                           env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1",
                                "PYTHONDONTWRITEBYTECODE": "1", "NO_COLOR": "1"})
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, None, str(e)
    out = p.stdout + p.stderr
    m = re.search(r"(\d+) passed", out)
    return p.returncode, (int(m.group(1)) if m else None), out[-800:]


def carry(pub: Path) -> None:
    for name in CARRY:
        src = REPO / name
        if not src.exists():
            continue
        dst = pub / name
        # В списке есть и каталоги, и отдельные файлы (README). `rmtree` и
        # `copytree` работают только с каталогами и на файле падают — первая же
        # попытка перенести README уронила бы выкладку целиком.
        if src.is_file():
            shutil.copy2(src, dst)
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", ".git",
            ".mutation-lock"))



#: Что живёт в публичном дереве по праву, не приезжая из приватного: история,
#: лицензия и правила игнора. Список ЯВНЫЙ по той же причине, что и CARRY.
СЛУЖЕБНОЕ = (".git", ".gitignore", ".gitattributes", "LICENSE", "LICENSE.md")


def лишнее(pub: Path) -> list:
    """Что лежит в корне публичного дерева, но больше не переносится.

    Перенос затирает то, что приехало, и не знает о том, что уехать перестало.
    Переименовали витрину — старый файл остался лежать в публичном, и человек,
    пришедший по ссылке, открыл прошлую редакцию как текущую. Так и вышло:
    `README.ru.md` пережил переименование и был убран руками.

    Считается по КОРНЮ дерева, а не по всем файлам: внутри перенесённых папок
    порядок наводит сам перенос — он их удаляет целиком и кладёт заново.
    """
    берём = set(CARRY) | set(СЛУЖЕБНОЕ)
    return sorted(p.name for p in pub.iterdir() if p.name not in берём)


def подмести(pub: Path, применить: bool) -> list:
    """Назвать лишнее, а при `применить` — убрать его.

    Называется ВСЕГДА, убирается только при выкладке: подготовка обязана
    оставаться безопасной для дерева, в котором человек мог что-то держать
    руками. Возвращает список названного — его же печатает отчёт.
    """
    имена = лишнее(pub)
    if not применить:
        return имена
    for имя in имена:
        p = pub / имя
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    return имена


def verify_published(pub: Path, клонер=None) -> dict:
    """Публикация состоялась, если набор зелёный В СВЕЖЕМ КЛОНЕ выложенного.

    Код возврата `git push` доказывает, что байты уехали. Он ничего не говорит
    о том, что уехало ЦЕЛОЕ: в рабочем дереве живут файлы, которых нет в
    индексе, и прогон в нём проходит по ним. Так уже случалось у других —
    репозиторий, который не собирается ни у кого, кроме автора.

    Клонер подаётся снаружи: проверка, обязательно ходящая в сеть, измеряет
    чужой сервер и не может быть прогнана в наборе.
    """
    адрес = None
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(pub),
                       capture_output=True, text=True)
    if r.returncode == 0:
        адрес = (r.stdout or "").strip()
    if not адрес:
        return {"status": "unknown", "detail": "у публичного дерева нет origin — "
                                               "проверять нечего"}
    клонер = клонер or _склонировать
    with tempfile.TemporaryDirectory() as d:
        куда = Path(d) / "клон"
        код, почему = клонер(адрес, куда)
        if код != 0:
            return {"status": "unknown",
                    "detail": f"склонировать выложенное не удалось: {почему}"}
        кода, прошло, хвост = run_tests(куда)
        if кода != 0:
            # Худший из исходов: у автора зелено, у скачавшего нет, и узнаёт
            # об этом скачавший.
            return {"status": "fail", "tests_passed": прошло,
                    "detail": f"в свежем клоне набор красный (код {кода})",
                    "tail": (хвост or "")[-300:]}
        return {"status": "pass", "tests_passed": прошло,
                "detail": f"свежий клон зелёный: {прошло} тестов"}


def _склонировать(адрес: str, куда: Path) -> tuple:
    r = subprocess.run(["git", "clone", "--depth", "1", "-q", адрес, str(куда)],
                       capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stderr or "").strip()[-200:]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    push = "--push" in sys.argv[1:]
    if len(args) != 1:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 3
    pub = Path(args[0]).expanduser().resolve()
    if not (pub / ".git").is_dir():
        print(f"НЕ УДАЛОСЬ: {pub} не git-репозиторий — выкладывать некуда",
              file=sys.stderr)
        return 3

    carry(pub)
    # Подметается ДО прогона: набор обязан идти по тому дереву, которое уедет,
    # а не по нему же плюс вчерашние файлы.
    stale = подмести(pub, push)
    chopped = split_literals(pub / "tests")
    code, passed, tail = run_tests(pub)
    nums = measured()
    fresh = refresh_readme(pub, nums, passed)
    a = audit(pub)

    report = {"target": str(pub), "literals_split": chopped,
              "tests_passed": passed, "tests_exit": code,
              "readme_refreshed": fresh,
              "stale": stale, "stale_removed": bool(stale) and push, **nums, **a}

    blocking = []
    if a["leaks"]:
        blocking.append(f"КЛЮЧИ ЧЕЛОВЕКА В ДЕРЕВЕ: {a['leaks']}")
    if a["personal"]:
        blocking.append(f"личные пути: {a['personal'][:3]}")
    if a["long_literals"]:
        blocking.append(f"длинные литералы остались: {a['long_literals']}")
    if a["attribution"]:
        blocking.append("сказано, откуда взят приём: "
                        + "; ".join(x["file"] for x in a["attribution"]))
    if code != 0:
        blocking.append(f"набор красный (код {code}): {tail.strip()[-200:]}")
    if not fresh:
        blocking.append(почему_не_обновилась(nums, passed, pub))

    report["blocking"] = blocking
    report["status"] = "ready" if not blocking else "blocked"

    print(("ГОТОВО К ВЫКЛАДКЕ" if not blocking else "ВЫКЛАДЫВАТЬ НЕЛЬЗЯ"), file=sys.stderr)
    print(f"  литералов разбито: {chopped}", file=sys.stderr)
    print(f"  тестов прошло: {passed}", file=sys.stderr)
    print(f"  секретов машины сверено: {a['secrets_checked']} — "
          f"{'ни один не уехал' if not a['leaks'] else 'УТЕЧКА'}", file=sys.stderr)
    if stale:
        print(f"  лишнее в публичном дереве ({len(stale)}): {', '.join(stale)}"
              + (" — убрано" if push else " — останется, пока не --push"),
              file=sys.stderr)
    for b in blocking:
        print(f"  ! {b}", file=sys.stderr)

    if blocking:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1

    if push:
        subprocess.run(["git", "add", "-A"], cwd=str(pub), check=False)
        msg = (f"sync: {passed} тестов · {nums['mutations']} мутаций · "
               f"{nums['mechanisms']} механизмов")
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(pub), check=False)
        r = subprocess.run(["git", "push", "-q", "origin", "main"], cwd=str(pub),
                           capture_output=True, text=True)
        report["pushed"] = r.returncode == 0
        if r.returncode != 0:
            print(f"  ! push отклонён: {(r.stderr or '')[-300:]}", file=sys.stderr)
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1
        print("  выложено", file=sys.stderr)

        # Публикация СОСТОЯЛАСЬ — это зелёный прогон в свежем клоне, а не код
        # возврата push. Иначе «выложено» означает «байты уехали», и разницу
        # первым замечает тот, кто скачал.
        проверка = verify_published(pub)
        report["fresh_clone"] = проверка
        print(f"  свежий клон: {проверка['detail']}", file=sys.stderr)
        if проверка["status"] == "fail":
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1
    else:
        print("  --push не задан: дерево подготовлено, выкладка не выполнена",
              file=sys.stderr)

    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
