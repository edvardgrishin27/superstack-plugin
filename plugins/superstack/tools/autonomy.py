#!/usr/bin/env python3
"""SUPERSTACK — сколько система делает без человека, и чем это заслужено.

Зачем ручка вообще. Сегодня объём самостоятельности задаётся молча: где-то
спросили, где-то нет, и человек узнаёт границу по факту — когда что-то
случилось без него. Явная ступень превращает это в решение, которое принято
один раз и записано.

Зачем ступень нельзя поднять словами. Всё, что здесь автоматизируется, стоит на
доказательствах: пропускать подтверждение шагов осмысленно там, где приёмка
поймает расхождение, а закрывать ход без человека — там, где тесты доказали, что
умеют падать. Согласие «да» на сам план от ступени не зависит. Ступень,
выставленная желанием, снимает подтверждения, не добавив проверок, и первым
же прогоном превращается в «оно само что-то сделало».

Поэтому подъём — это ПРОВЕРКА, а не запись. Каждая ступень называет своё условие
и путь, по которому оно измерено:

  0  ничего сама          человек подтверждает каждый шаг
  1  план по шаблону      есть тесты и команда, которой их гоняют
  2  код без подтверждения каждого таска
                          гейт верификации отвечает кодом 0/1, а не «проверять
                          нечем»: иначе «сделано» опирается на пустоту
  3  закрытие хода без вопроса (план по-прежнему ждёт «да»)
                          зарегистрированы поломки (тесты умеют падать) И слепая
                          приёмка хоть раз выносила вердикт

Выше третьей ступени здесь нет намеренно. Четвёртая означала бы, что система
сама решает, ЧТО строить, а это другое решение, и оно не следует из желания не
отвечать на вопросы по ходу.

Те же ступени, названные словами человека: «полный автомат» — верхняя,
«обычно» — умолчание, «ручной» — нижняя. Второго механизма под это НЕ
заводится: слово остаётся ПРОСЬБОЙ, и порядок прежний — сначала условие,
потом ступень. Не выполнено — остаёмся ниже и говорим, чего не хватает.

Глубина («строго» · «обычно» · «глубоко») — отдельное поле рядом. Она про то,
сколько система додумывает сверх сказанного, и ступени не трогает: спрашивать
реже и додумывать больше — разные решения, и одно из другого не следует.

  python3 autonomy.py <корень> --show
  python3 autonomy.py <корень> --set 2
  python3 autonomy.py <корень> --can 3      можно ли, и чего не хватает
  python3 autonomy.py <корень> --words "полный автомат, продумай глубоко"

  код 0 — сделано/можно, 1 — нельзя (условие не выполнено), 2 — не смог,
  3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plain_ru  # noqa: E402

MAX_LEVEL = 3

LEVELS = {
    0: "ничего сама — человек подтверждает каждый шаг",
    1: "план по шаблону, код только с подтверждения",
    2: "код пишется без подтверждения каждого таска",
    3: "ход закрывается без вопроса к человеку; план по-прежнему ждёт «да»",
}


#: Слово человека -> ступень, которую оно ПРОСИТ. Отдельной шкалы тут нет:
#: «полный автомат» это верхняя ступень со всеми её условиями, «ручной» —
#: нижняя, которой условие не нужно никогда.
MODES = {"auto": MAX_LEVEL, "normal": 2, "manual": 0}
DEFAULT_MODE = "normal"

#: Сколько система додумывает сверх сказанного. Ступень этим не двигается.
DEPTHS = ("strict", "normal", "deep")
DEFAULT_DEPTH = "normal"

#: По каким оборотам режим узнаётся в живой фразе. Списки НАРОЧНО разной
#: широты и порядок НАРОЧНО такой. Ошибка в сторону «ручной» стоит лишнего
#: вопроса, ошибка в сторону «полный автомат» — сделанного без спроса.
#: Поэтому верхний режим узнаётся только по прямым оборотам, нижний — по
#: любому упоминанию, и при совпадении сразу двух побеждает последний, то
#: есть более осторожный.
MODE_WORDS = {
    "auto": ("полный автомат", "полностью автомат", "полном автомате",
             "на автомате", "автопилот", "ничего не спрашивай",
             "не спрашивай меня", "решай сам", "делай сам"),
    "normal": ("как обычно", "обычный режим", "обычном режиме"),
    "manual": ("ручн", "вручную", "спрашивай обо всем", "подтверждай каждый"),
}

#: Та же осторожность, повёрнутая обратно: «строго» и «глубоко» стоят человеку
#: времени, а не неожиданностей, поэтому узнаются широко.
DEPTH_WORDS = {
    "normal": ("как обычно", "обычная глубина"),
    "strict": ("строго", "строгий", "только то, что сказал", "ничего своего",
               "без самодеятельности"),
    "deep": ("глубок", "глубже", "продумай как следует", "все сценарии"),
}


def state_file(root: Path) -> Path:
    return root / ".superstack" / "autonomy.json"


def _blank(why: str) -> dict:
    return {"level": 0, "why": why, "mode": DEFAULT_MODE, "depth": DEFAULT_DEPTH}


def read(root: Path) -> dict:
    try:
        d = json.loads(state_file(root).read_text("utf-8"))
    except (OSError, ValueError):
        return _blank("не задавалась — начинаем с нуля")
    lvl = d.get("level")
    if not isinstance(lvl, int) or not 0 <= lvl <= MAX_LEVEL:
        return _blank("запись повреждена — считаем нулём")
    d["mode"] = d.get("mode") if d.get("mode") in MODES else DEFAULT_MODE
    d["depth"] = d.get("depth") if d.get("depth") in DEPTHS else DEFAULT_DEPTH
    return d


def _has_tests(root: Path) -> bool:
    if (root / "package.json").is_file():
        try:
            if "test" in json.loads(
                    (root / "package.json").read_text("utf-8")).get("scripts", {}):
                return True
        except ValueError:
            pass
    for m in ("pytest.ini", "pyproject.toml", "tox.ini", "Makefile",
              "tests/conftest.py", "go.mod", "Cargo.toml"):
        if (root / m).is_file():
            return True
    return False


def _verify_answers(root: Path) -> bool:
    """Гейт верификации отвечает кодом, а не «проверять нечем».

    Читается ЗАПИСЬ последнего прогона, а не запускается проверка: подъём
    ступени не должен зависеть от того, соберётся ли проект прямо сейчас.
    """
    f = root / ".superstack" / "verify-last.json"
    try:
        return json.loads(f.read_text("utf-8")).get("exit_code") in (0, 1)
    except (OSError, ValueError, AttributeError):
        return False


def _mutations_registered(root: Path) -> bool:
    for rel in (".superstack/mutations.json", "tests/mutations.json"):
        f = root / rel
        try:
            if len(json.loads(f.read_text("utf-8")).get("mutations", [])) > 0:
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def _blind_ran(root: Path) -> bool:
    try:
        return bool(json.loads(
            (root / ".superstack" / "manifest.json").read_text("utf-8")).get("blind"))
    except (OSError, ValueError, AttributeError):
        return False


#: Условие ступени: что обязано быть доказано и где это записано.
GATES = {
    1: [(_has_tests, "нет команды тестов — проверять работу нечем")],
    2: [(_has_tests, "нет команды тестов"),
        (_verify_answers, "гейт верификации ни разу не отвечал кодом 0 или 1: "
                          "«проверять нечем» — не основание писать код без "
                          "подтверждения")],
    3: [(_has_tests, "нет команды тестов"),
        (_verify_answers, "гейт верификации не отвечал кодом"),
        (_mutations_registered, "ни одной зарегистрированной поломки — что тесты "
                                "умеют падать, здесь никем не измерено"),
        (_blind_ran, "слепая приёмка ни разу не выносила вердикт — расхождение "
                     "«сделали не то» ловить нечем")],
}


def can(root: Path, level: int) -> dict:
    if level == 0:
        return {"ok": True, "level": 0, "missing": []}
    missing = [why for check, why in GATES.get(level, []) if not check(root)]
    return {"ok": not missing, "level": level, "missing": missing}


def write(root: Path, level: int, missing: list,
          mode: "str | None" = None, depth: "str | None" = None) -> None:
    """Одна запись на всё: ступень, режим и глубина.

    Прежнее значение режима подхватывается, а не затирается: `--set` меняет
    ступень, и молчаливая потеря выбранного режима означала бы, что система
    вернулась к умолчанию, не сказав об этом ни слова.
    """
    было = read(root)
    f = state_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"level": level, "why": LEVELS[level],
                             "mode": mode or было["mode"],
                             "depth": depth or было["depth"],
                             "checked": [c for c in missing]},
                            ensure_ascii=False, indent=1), encoding="utf-8")


def recognise(text: str) -> dict:
    """Что человек сказал о режиме и о глубине СВОИМИ словами.

    Ничего не нашлось — ничего и не возвращается. Молчание это не выбор
    «обычно», а отсутствие просьбы, и перезаписывать им прежний выбор нельзя:
    человек, сказавший «ручной» вчера, не отменял его сегодняшним «давай
    дальше».
    """
    t = (text or "").lower().replace("ё", "е")
    said = {"mode": None, "depth": None}
    for mode, words in MODE_WORDS.items():
        if any(w in t for w in words):
            said["mode"] = mode
    for depth, words in DEPTH_WORDS.items():
        if any(w in t for w in words):
            said["depth"] = depth
    return said


def ask(root: Path, mode: "str | None" = None,
        depth: "str | None" = None) -> dict:
    """Просьба о режиме. Ступень даёт условие, а не слово.

    Просимая ступень берётся, только если её условие выполнено; иначе берётся
    ближайшая снизу, которая заслужена, и причина возвращается вместе с
    результатом. Тихо выдать верхнюю по слову значило бы снять подтверждения,
    не добавив проверок, — ровно то, ради чего эта ступень и заведена.
    """
    было = read(root)
    mode = mode or было["mode"]
    depth = depth or было["depth"]
    if mode not in MODES:
        raise ValueError(f"неизвестный режим: {mode}")
    if depth not in DEPTHS:
        raise ValueError(f"неизвестная глубина: {depth}")
    хотим = MODES[mode]
    не_хватает = can(root, хотим)["missing"]
    дают = хотим
    while дают > 0 and not can(root, дают)["ok"]:
        дают -= 1
    write(root, дают, не_хватает, mode=mode, depth=depth)
    return {"mode": mode, "depth": depth, "asked": хотим, "level": дают,
            "missing": не_хватает if дают < хотим else []}


def badges(root: Path) -> dict:
    """Две плашки для панели: как работаем и насколько глубоко.

    Отдаются опознавателями, а не словами: слова для человека лежат в
    `data/plain-ru.json`, и правка формулировки не должна быть правкой кода.
    """
    d = read(root)
    return {"mode": d["mode"], "depth": d["depth"], "level": d["level"]}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def _value_arg(argv: list, flag: str) -> "str | None":
    if flag not in argv or argv.index(flag) + 1 >= len(argv):
        return None
    return argv[argv.index(flag) + 1]


def _level_arg(argv: list, flag: str) -> "int | None":
    if flag not in argv or argv.index(flag) + 1 >= len(argv):
        return None
    try:
        return int(argv[argv.index(flag) + 1])
    except ValueError:
        return None


def _сказать(mode: str, depth: str) -> str:
    """Режим и глубина словами человека — из общего словаря, не отсюда."""
    words = plain_ru.load()
    return (f"{words['modes']['map'].get(mode, mode)} · глубина: "
            f"{words['depths']['map'].get(depth, depth)}")


def _по_словам(root: Path, сказано: str) -> int:
    """Режим, названный словами. Печатается ПОСЛЕ записи, а не до неё."""
    услышано = recognise(сказано)
    if not услышано["mode"] and not услышано["depth"]:
        print("НЕ УДАЛОСЬ: слов о режиме в этой фразе нет — оставляю как было",
              file=sys.stderr)
        return 2
    out = ask(root, услышано["mode"], услышано["depth"])
    print(f"РЕЖИМ: {_сказать(out['mode'], out['depth'])}", file=sys.stderr)
    if out["mode"] == "auto":
        # Одна фраза человеку в момент, когда он выбрал верхний режим: то,
        # чего этот выбор НЕ отключает. Сказанное после первого прогона уже
        # поздно — он к тому времени решил, что проверок не осталось.
        print(plain_ru.copy("mode_auto_note"), file=sys.stderr)
    if out["missing"]:
        print(f"СТУПЕНЬ {out['asked']} НЕ ЗАСЛУЖЕНА — работаю на "
              f"{out['level']}: {LEVELS[out['level']]}", file=sys.stderr)
        for x in out["missing"]:
            print(f"  ! {x}", file=sys.stderr)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    # Фраза человека уходит из позиционных ДО подсчёта: она не начинается с
    # «--» и без этого читалась бы как второй корень, то есть команда с
    # режимом падала бы «вызов: …», не дойдя до распознавания слов.
    сказано = _value_arg(argv, "--words")
    остальное = list(argv)
    if сказано is not None:
        остальное.remove(сказано)
    plain = [a for a in остальное
             if not a.startswith("--") and not a.isdigit()]
    if len(plain) != 1:
        print("вызов: autonomy.py <корень> --show | --set N | --can N | "
              "--words «слова человека»", file=sys.stderr)
        return 3
    root = Path(plain[0]).expanduser().resolve()
    if not root.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {root}", file=sys.stderr)
        return 2

    if сказано is not None:
        return _по_словам(root, сказано)

    if "--show" in argv or (not any(f in argv for f in ("--set", "--can"))):
        cur = read(root)
        print(f"СТУПЕНЬ {cur['level']}: {LEVELS[cur['level']]}", file=sys.stderr)
        print(f"РЕЖИМ: {_сказать(cur['mode'], cur['depth'])}", file=sys.stderr)
        print(json.dumps(cur, ensure_ascii=False, indent=1))
        return 0

    want = _level_arg(argv, "--set")
    if want is None:
        want = _level_arg(argv, "--can")
    if want is None or not 0 <= want <= MAX_LEVEL:
        print(f"НЕ УДАЛОСЬ: ступень — целое от 0 до {MAX_LEVEL}", file=sys.stderr)
        return 3

    v = can(root, want)
    if not v["ok"]:
        print(f"СТУПЕНЬ {want} НЕ ЗАСЛУЖЕНА", file=sys.stderr)
        for x in v["missing"]:
            print(f"  ! {x}", file=sys.stderr)
        print("  подтверждения снимаются вместе с добавлением проверок, а не "
              "вместо них", file=sys.stderr)
        print(json.dumps(v, ensure_ascii=False, indent=1))
        return 1

    if "--set" in argv:
        write(root, want, v["missing"])
        print(f"СТУПЕНЬ {want}: {LEVELS[want]}", file=sys.stderr)
    else:
        print(f"СТУПЕНЬ {want} ДОСТУПНА", file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
