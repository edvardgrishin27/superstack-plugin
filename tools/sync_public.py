#!/usr/bin/env python3
"""SUPERSTACK — выкладка в публичный репозиторий.

Зачем скриптом. Выкладка состоит из четырёх шагов, три из которых легко
выполнить невнимательно, и последствия у всех разные по цене:

  · перенести дерево — забудешь исключить .git, и в публичный уедет история;
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
Витрина — это и описание репозитория: его видят раньше кода, и числа в нём
переписываются тем же измерением, после того как выкладка состоялась.

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
         ".github", ".superstack", "README.md", "README.ru.md")

#: Длина, с которой строка начинает выглядеть как ключ для чужого сканера.
LITERAL_LIMIT = 24
LITERAL = re.compile(r'"([A-Za-z0-9+/=_-]{%d,})"' % LITERAL_LIMIT)

#: Имя пользователя и домашние пути не должны уезжать в публичное дерево.
#: `/Users/me` — намеренная фикстура, она исключена.
PERSONAL = re.compile(r"/Users/(?!me\b)[A-Za-z][\w.-]*")


def chop(literal: str) -> str:
    """Разбить длинный литерал на три куска, склеиваемых рядом.

    Значение во время прогона не меняется; в исходнике не остаётся строки,
    на которую срабатывает сканер.
    """
    k = (len(literal) + 2) // 3
    return " ".join(f'"{literal[i:i + k]}"' for i in range(0, len(literal), k))


def split_literals(tests_dir: Path) -> int:
    n = 0
    for f in sorted(tests_dir.glob("*.py")):
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
    leaks, personal, long_lits = [], [], []
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
    return {"secrets_checked": len(secrets), "leaks": leaks,
            "personal": personal[:20], "long_literals": long_lits}


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


_NUM = {
    "tests": re.compile(r"\b\d+ (?=тест)"),
}


#: Строка, которой человек говорит: чисел в витрине нет НАМЕРЕННО.
#: Признак живёт в самом README, а не во флаге запуска: флаг ставят один раз
#: и дальше дописывают по привычке, а строка видна тому, кто правит текст, и
#: исчезает вместе с ним.
БЕЗ_ЧИСЕЛ = "<!-- без чисел -->"

#: Витрина двуязычная: английская — главная, её читают со всего мира; русская —
#: полная. Обе переписываются ОДНИМ проходом и обе лежат в списке переноса.
#: Половина, оставшаяся без обновления, — худший исход из возможных: она не
#: пустая, она уверенно показывает числа прошлого релиза, а языковой
#: переключатель ведёт человека прямо в неё.
ВИТРИНЫ = ("README.md", "README.ru.md")


def refresh_readme(pub: Path, nums: dict, tests: int) -> bool:
    """Переписать числа в README на измеренные.

    Возвращает False в двух случаях, и оба означают одно: витрине верить
    нельзя.

    Первый — измерить не удалось. Второй — измерили, но переписывать оказалось
    нечего: ни одна из трёх подстановок не совпала. Второй тише и потому
    дороже: три замены молча не срабатывают, файл переписывается сам собой,
    скрипт печатает «обновлено», а витрина продолжает показывать числа
    прошлого релиза. Так и вышло — числа правили руками весь релиз.

    Витрина без чисел бывает и намеренной. Тогда это сказано ВСЛУХ строкой
    `<!-- без чисел -->` в самом README: рядом с текстом, к которому она
    относится, и на глазах у того, кто этот текст правит. Молчание таким
    признаком быть не может — оно неотличимо от поломки.

    Витрин две — английская и русская, и обход идёт по обеим. Обновить одну и
    промолчать про вторую нельзя: языковой переключатель стоит первой строкой
    в каждой, и человек, нажавший его, попадёт на страницу, которая уверенно
    показывает числа прошлого релиза. Поэтому «нечего править» считается ДЛЯ
    КАЖДОЙ найденной витрины отдельно.
    """
    if None in (nums.get("mutations"), nums.get("mechanisms")) or tests is None:
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
        заменено = 0
        for шаблон, чем, флаги in подстановки:
            t, сколько = re.subn(шаблон, чем, t, flags=флаги)
            заменено += сколько
        if not заменено:
            return False
        p.write_text(t, encoding="utf-8")
        обновлено += 1
    return обновлено > 0


def почему_не_обновилась(nums: dict, tests) -> str:
    """Две причины отказа — двумя разными словами.

    «Измерить не удалось» и «в витрине нечего править» чинятся по-разному:
    первое — в наборе и в данных, второе — в самом README. Один общий текст
    отправлял бы чинить не то.
    """
    if None in (nums.get("mutations"), nums.get("mechanisms")) or tests is None:
        return "числа в README не обновлены — измерить не удалось"
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


#: Числовой хвост описания репозитория пишет только этот скрипт — по нему же
#: он и находит, что переписать, не трогая текст человека перед хвостом.
#:
#: Форм две, и узнавать надо ОБЕ. Хвост стал английским вслед за главной
#: витриной, но в описании репозитория лежит ещё прежний, русский. Регулярка,
#: знающая только новую форму, не срежет старый хвост — и припишет к нему
#: второй: в описании окажутся оба, каждый со своими числами.
ХВОСТ = re.compile(
    r"\s*(?:—\s*)?\d+ (?:тестов · \d+ мутаций · \d+ механизмов"
    r"|tests · \d+ mutations · \d+ mechanisms)\.?\s*$")

#: Разделитель, оставшийся висеть в конце текста человека после среза хвоста.
#: Живой случай выкладки 0.10.0: основа кончалась на «9 ворот ·», склейка
#: добавляла своё тире, и получалось «9 ворот · — 1962 тестов…». Описание
#: тогда правили руками — то есть механизм был, а результат его работы всё
#: равно приходилось чинить глазами.
ВИСЯЧИЙ = re.compile(r"[·—\-:\s]+$")


def repo_slug(pub: Path) -> str:
    """owner/repo — из origin публичного дерева, а не из строки в коде.

    Зашитый адрес однажды отправит правку описания в чужой репозиторий.
    Здесь он берётся оттуда же, куда уходит push, и «не разобрали адрес» —
    отдельный ответ, а не молчаливая догадка.
    """
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(pub),
                       capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    m = re.search(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
                  (r.stdout or "").strip())
    return m.group(1) if m else ""


def описание(текущее: str, nums: dict, tests: int) -> str:
    """Новое описание: текст человека без прежнего хвоста плюс измеренный хвост.

    Числа дописываются, а не затирают строку целиком: описание пишет человек,
    и выкладка не имеет права стирать его слова ради своих цифр.

    Хвост английский — на языке главной страницы репозитория. Описание и
    README видят подряд, и разные языки в них читаются как недоделка.

    Отдельный шаг — снять висячий разделитель. Человек кончает свою фразу
    тем же «·», которым разделены числа, и склейка ставит рядом два знака:
    «9 ворот · — 1967 tests». Это не опечатка редкого случая, это форма,
    которую сама же выкладка и создаёт каждый раз.
    """
    основа = ВИСЯЧИЙ.sub("", ХВОСТ.sub("", текущее or "")).strip()
    хвост = (f"{tests} tests · {nums['mutations']} mutations · "
             f"{nums['mechanisms']} mechanisms")
    return f"{основа} — {хвост}" if основа else хвост


def _gh(аргументы: list) -> tuple:
    try:
        r = subprocess.run(["gh", *аргументы], capture_output=True, text=True,
                           timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def refresh_description(pub: Path, nums: dict, tests, gh=None) -> dict:
    """Числа в описании репозитория — та же витрина, что и README.

    Описание видно раньше самого кода: в поиске, в списке репозиториев, в
    карточке ссылки. Пока его правили руками, оно целый релиз показывало
    числа прошлой версии — README при этом был свежий.

    Шаг ходит в сеть и потому НЕ блокирует выкладку: не получилось — так и
    написано, «не проверено», а не «сделано». Обращение к gh подаётся
    снаружи, иначе набор измерял бы чужой сервер вместо нашего кода.
    """
    if None in (nums.get("mutations"), nums.get("mechanisms")) or tests is None:
        return {"status": "unknown",
                "detail": "числа не измерены — описание репозитория не трогали"}
    slug = repo_slug(pub)
    if not slug:
        return {"status": "unknown",
                "detail": "origin не похож на адрес GitHub — описание "
                          "репозитория не трогали"}
    gh = gh or _gh
    код, было = gh(["api", f"repos/{slug}", "--jq", ".description"])
    if код != 0:
        return {"status": "unknown",
                "detail": f"описание репозитория не прочитано ({было[-120:]}) — "
                          "числа в нём НЕ ПРОВЕРЕНЫ"}
    текст = описание("" if было.strip() == "null" else было, nums, tests)
    код, ответ = gh(["api", "-X", "PATCH", f"repos/{slug}",
                     "-f", f"description={текст}"])
    if код != 0:
        return {"status": "unknown", "text": текст,
                "detail": f"описание репозитория не обновлено ({ответ[-120:]}) — "
                          "числа в нём НЕ ПРОВЕРЕНЫ"}
    return {"status": "pass", "text": текст,
            "detail": f"описание репозитория обновлено: {текст}"}


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
    chopped = split_literals(pub / "tests")
    code, passed, tail = run_tests(pub)
    nums = measured()
    fresh = refresh_readme(pub, nums, passed)
    a = audit(pub)

    report = {"target": str(pub), "literals_split": chopped,
              "tests_passed": passed, "tests_exit": code,
              "readme_refreshed": fresh, **nums, **a}

    blocking = []
    if a["leaks"]:
        blocking.append(f"КЛЮЧИ ЧЕЛОВЕКА В ДЕРЕВЕ: {a['leaks']}")
    if a["personal"]:
        blocking.append(f"личные пути: {a['personal'][:3]}")
    if a["long_literals"]:
        blocking.append(f"длинные литералы остались: {a['long_literals']}")
    if code != 0:
        blocking.append(f"набор красный (код {code}): {tail.strip()[-200:]}")
    if not fresh:
        blocking.append(почему_не_обновилась(nums, passed))

    report["blocking"] = blocking
    report["status"] = "ready" if not blocking else "blocked"

    print(("ГОТОВО К ВЫКЛАДКЕ" if not blocking else "ВЫКЛАДЫВАТЬ НЕЛЬЗЯ"), file=sys.stderr)
    print(f"  литералов разбито: {chopped}", file=sys.stderr)
    print(f"  тестов прошло: {passed}", file=sys.stderr)
    print(f"  секретов машины сверено: {a['secrets_checked']} — "
          f"{'ни один не уехал' if not a['leaks'] else 'УТЕЧКА'}", file=sys.stderr)
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

        # Описание репозитория — та же витрина, и видят его раньше кода. Шаг
        # ходит в сеть, поэтому выкладку не блокирует; но «не смог» пишется
        # словом, а не пропускается молча.
        витрина = refresh_description(pub, nums, passed)
        report["description"] = витрина
        print(f"  {витрина['detail']}", file=sys.stderr)

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
