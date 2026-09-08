#!/usr/bin/env python3
"""SUPERSTACK — применение изменений.

Единственная часть системы, которая ПИШЕТ в конфигурацию человека. Поэтому
здесь всё построено вокруг одного вопроса: что произойдёт, если я ошибусь.

Устройство:

  1. Бэкап делается ДО первого изменения, целиком, и проверяется чтением.
     Не проверенный бэкап — это надежда, а не бэкап.
  2. Секреты в бэкап НЕ копируются. Иначе «критическая находка про пароль»
     превращала бы одну копию пароля в две. На их месте метка-отпечаток,
     и человеку это говорится ЗАРАНЕЕ, до действия: откат вернёт файл,
     но не вернёт секрет. Ищутся они в том содержимом, которое СЕЙЧАС ляжет
     в копию, а не в списке, собранном раньше: пустой список означает «сканер
     ничего не увидел», а не «в файле ничего нет». Файл, который не
     разобрался, — отдельное состояние «не проверено»: он не копируется вовсе,
     не меняется ничего, и человеку названы файл и причина.
  3. Удаления нет. Есть карантин: файл переезжает и может вернуться.
  4. Каждое действие пишется в журнал: какое поле, было ли оно, что было и
     что стало. Откат возвращает СВОИ поля — и только те, в которых до сих
     пор лежит записанное им. Всё остальное человек правил сам после нас:
     оно остаётся, а ему говорится, что именно не откатили и почему.
     Отсутствие файла — тоже состояние, оно записывается до первой записи:
     созданный здесь файл убирается, если в нём не появилось чужого.
     Имена в журнале проверяются при чтении: своя же запись, сделанная
     вчера, — внешние данные.
  5. Класс находки решает, можно ли применять без человека:
       AUTO   — можно
       GATE   — только с явного согласия
       ASK    — только человек, инструмент лишь показывает, что сделать
       BLOCK  — не применяется никогда
     Секрет в настройках имеет класс BLOCK/ASK именно поэтому: удалять
     чужой пароль автоматически нельзя, а «починить» его молча — тем более.

Раскладка. Здесь — профиль, копия, действия и команды: всё, что ПИШЕТ.
Рядом три соседа, каждый со своим контрактом и без единого пути к профилю:

  tools/base_kit.py      — перечень базового набора: данные, не решение
  tools/settings_copy.py — чтение настроек и очистка их копии от секретов
  tools/undo_fields.py   — формат `superstack.undo.v2`: единица отката — поле

  python3 apply.py plan findings.json          показать, что будет сделано
  python3 apply.py run  findings.json [--yes]  сделать
  python3 apply.py undo <id>                   откатить
  python3 apply.py list                        какие откаты доступны
  python3 apply.py base [facts.json]            что из базового набора уже есть
  коды: 0 — сделано; 1 — нет такого отката, имя не то, формат копии
  незнаком, базовый набор неполон или копия не снята (файл настроек не
  разбирается, и вслепую его не сохраняем); 2 — ошибка вызова; 4 — факты не
  прочитались; 5 — откат не вернул ни одного поля; 6 — нужно явное
  согласие; 10 — система на паузе
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# Замок и запись — общие для всех инструментов, которые пишут одни и те же
# файлы: одинаковая ошибка, размноженная копипастом, чинится по одному файлу и
# возвращается в остальных. Каталог скрипта при импорте из теста в sys.path не
# попадает, поэтому добавляется явно — как у соседей.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_store  # noqa: E402
from base_kit import BASE_KIT, _kit_row  # noqa: E402
# Имена перечислены полностью, включая те, что зовутся не отсюда: apply.py
# остаётся ЕДИНЫМ адресом для тех, кто ходит к нему снаружи (tools/oops.py,
# тесты), и переезд куска в соседний модуль не имеет права менять этот адрес.
from undo_fields import (  # noqa: E402,F401
    UnsafeName, _drop_created_file, _restore_one_field, _взять, _звенья,
    _коротко, _положить, _секрет_вырезан, _убрать, _undo_journal,
    _undo_legacy, _формат_копии,
)
from settings_copy import (  # noqa: E402,F401
    REDACTION_MARK, WRITABLE, _отказ_непроверенное, _отказ_остатки,
    _проба, _прочитать_конфиг, _redact_journal, _redact_walk,
    _свежие_находки, _что_вырезать, _остатки_в_тексте, redact,
)


HOME = Path.home()

#: Каталог настроек, который читает ХОСТ, и то, откуда взят этот путь.
#:
#: Здесь путь собирался литералом `Path.home() / ".claude"`, а находки —
#: сборщиком, который уважает `CLAUDE_CONFIG_DIR`. На машине с назначенным
#: профилем это означало правку одного каталога по диагнозу о другом, с нулевым
#: кодом возврата. Из всех троих, кто так делал, этот — единственный, кто ПИШЕТ.
CLAUDE, CLAUDE_SOURCE = _проба().config_dir()

#: Свой каталог системы — тормоз. За назначенным профилем он НЕ едет: флаг
#: паузы кладёт `tools/pause.py` по домашнему адресу, и там же его ищут
#: остальные инструменты. Пишущий инструмент, который смотрит в другое место,
#: не «не нашёл паузу» — он продолжает править настройки остановленной системы.
STATE = HOME / ".claude" / "superstack"

#: Копия и карантин — В ТОМ ЖЕ профиле, который правится. Копия в одном
#: профиле и правка в другом — это потеря данных с успешным кодом возврата:
#: откат ищет копию рядом с изменённым файлом и не находит её там никогда.
BACKUPS = CLAUDE / "superstack" / "backups"
QUARANTINE = CLAUDE / "superstack" / "quarantine"


def профиль_строкой() -> str:
    """Профиль, в который пойдёт правка, и то, откуда взят его путь.

    Инструмент, который ПИШЕТ, обязан сказать КУДА, прежде чем это сделает.
    Согласие на изменение «настроек» — это согласие на изменение конкретного
    файла, и без имени каталога человек соглашается вслепую. Причина стоит
    рядом с путём: имя без причины нечем перепроверить, а «в твоём профиле» и
    «в чужом профиле» — разные утверждения.
    """
    return f"Профиль настроек: {CLAUDE}   путь взят: {CLAUDE_SOURCE}"

#: Код `undo`, когда вернуть не удалось НИ ОДНОГО поля. Причин к этому три, и
#: все три названы человеку словами: на месте поля в копии вырезан секрет и
#: возврат затёр бы живое значение меткой; поле изменилось после применения, и
#: чужое не трогаем; возвращать было нечего с самого начала. Код отдельный от 1
#: намеренно: 1 — «отката с таким именем нет», 5 — «откат есть, и он ничего не
#: вернул». Слой выше говорит на них РАЗНОЕ, а на одном коде сказал бы одно и
#: то же — то есть соврал бы в одном из случаев.
UNDO_NOTHING_RESTORED = 5

#: Метка формата копии. Копия, снятая ПРЕЖНЕЙ версией, её не имеет: полей в
#: ней не записано ни одного, и откатывается она прежним способом — файлом
#: целиком. Разобрать старую копию как новую значило бы не вернуть из неё
#: ничего и сказать об этом кодом успеха, то есть молча её испортить.
UNDO_FORMAT = "superstack.undo.v2"

#: классы находок и право на автоматическое применение
AUTO_APPLICABLE = {"AUTO"}
NEEDS_CONSENT = {"GATE"}
HUMAN_ONLY = {"ASK", "BLOCK"}


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = STATE / "PAUSE"
    if flag.exists():
        try:
            since = flag.read_text(encoding="utf-8").strip()
        except Exception:
            since = "?"
        print(f"ОСТАНОВЛЕНО: система на паузе с {since}\n"
              f"  флаг: {flag}\n  снять: скажи «включи» или позови /stop — сама пауза не пройдёт", file=sys.stderr)
        raise SystemExit(10)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------
# чтение настроек: сам разбор файла — в tools/settings_copy.py
# --------------------------------------------------------------------------
def прочитать_настройки() -> tuple:
    """Все файлы настроек ОДНИМ проходом: разобранное и непроверенное.

    Возвращает (имя -> содержимое, список непроверенных). Непроверенное — это
    отдельное состояние, а не пустота: неудача разбора говорит о разборщике и
    не сообщает о содержимом ничего. Прежде из неё выводилось «вырезать нечего
    и незачем», и файл уезжал в копию сырым, вместе с тем, что в нём лежало.

    Проход именно один. Два прохода означали бы два чтения одного файла и,
    между ними, две разные правды о нём — ровно тот разрыв, из-за которого
    секрет и попадал в копию: там расходились кодировки, здесь разошлось бы
    время чтения.
    """
    прочитано: dict = {}
    непроверенные: list[dict] = []
    for name in WRITABLE:
        src = CLAUDE / name
        if not src.is_file():
            continue
        данные, беда = _прочитать_конфиг(src)
        if беда:
            непроверенные.append({"file": name, "why": беда})
            continue
        прочитано[name] = данные
    return прочитано, непроверенные


# --------------------------------------------------------------------------
# бэкап
# --------------------------------------------------------------------------
def verify_backup(bdir: Path, names: list[str]) -> None:
    """Перечитать копию и убедиться, что она есть и разбирается.

    Без этого «бэкап сделан» доказывает лишь то, что вызов записи не бросил
    исключение. Пустой или обрезанный файл выглядел бы как успешный бэкап,
    и человек узнал бы об этом только в момент отката — когда возвращать
    уже нечего.
    """
    for name in names:
        f = bdir / name
        if not f.is_file() or f.stat().st_size == 0:
            raise SystemExit(f"ОТКАЗ: копия {name} пуста или отсутствует. "
                             f"Ничего не менял.")
        try:
            f.read_text(encoding="utf-8")
        except Exception as e:
            raise SystemExit(f"ОТКАЗ: копия {name} не перечитывается ({e}). "
                             f"Ничего не менял.")


def new_backup_dir() -> Path:
    """Каталог копии с собственным именем, созданный ЭКСКЛЮЗИВНО.

    Имя было меткой времени с точностью до секунды, а каталог создавался
    «можно поверх существующего». Два настоящих применения в одну секунду
    писали в один каталог: второе клало туда уже изменённое состояние,
    исходное терялось — и откат отчитывался успехом, оставив настройку на
    месте. Потеря исходного состояния выглядела как удачный откат.

    Поэтому создание эксклюзивное: занятое имя — отказ файловой системы, а не
    молчаливое слияние. Отказ гасится не согласием писать в чужой каталог, а
    следующим именем. Порядок имён при этом сохраняется — слой выше находит
    последнюю копию сортировкой строк, и суффикс идёт после метки времени.
    """
    BACKUPS.mkdir(parents=True, exist_ok=True)
    основа = stamp()
    for n in range(1, 1000):
        имя = основа if n == 1 else f"{основа}-{n:03d}"
        try:
            (BACKUPS / имя).mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return BACKUPS / имя
    raise SystemExit(f"ОТКАЗ: свободного имени для копии не нашлось ({основа}). "
                     f"Ничего не менял.")


def _record_before_state(name: str) -> dict:
    """Состояние файла ДО первой записи — включая его ОТСУТСТВИЕ.

    Отсутствующий файл прежде просто пропускался, и в копии о нём не
    оставалось ни строки. Инструмент создавал файл настроек с нуля, а откат
    не знал, что до него файла не было: возвращать нечего, файл оставался
    навсегда. Отсутствие — такое же состояние, как содержимое, и записывается
    ровно так же, до первой записи, а не выводится потом по догадке.
    """
    return {"before_exists": (CLAUDE / name).is_file()}


def make_backup(secret_hits: list[dict]) -> dict:
    """Полная копия конфигурации ДО изменений, с вырезанными секретами.

    Возвращает описание бэкапа. Копия сразу перечитывается: бэкап, который
    не прочитали, доказывает только то, что вызов записи не бросил исключение.

    Порядок здесь — часть механизма. Сначала разбирается ВСЁ, потом готовится
    и проверяется весь текст копии, и только потом появляется каталог. Отказ
    на любом шаге не оставляет ни половины копии, ни пустого каталога, и слова
    «ничего не менял» остаются правдой буквально.
    """
    прочитано, непроверенные = прочитать_настройки()
    if непроверенные:
        raise SystemExit(_отказ_непроверенное(непроверенные))

    готово: list = []
    for name, data in прочитано.items():
        hits = _что_вырезать(name, data, secret_hits)
        текст = json.dumps(redact(data, hits) if hits else data,
                           ensure_ascii=False, indent=2)
        остатки = _остатки_в_тексте(текст)
        if остатки:
            raise SystemExit(_отказ_остатки(name, остатки))
        готово.append((name, текст, hits))

    bdir = new_backup_dir()
    saved, redacted_files = [], []
    состояние = {name: _record_before_state(name) for name in WRITABLE}

    for name, текст, hits in готово:
        (bdir / name).write_text(текст, encoding="utf-8")
        saved.append(name)
        if hits:
            redacted_files.append({"file": name, "count": len(hits)})

    verify_backup(bdir, saved)
    verified = saved

    return {"dir": str(bdir), "files": verified, "redacted": redacted_files,
            "created": now(), "format": UNDO_FORMAT, "before": состояние}


# --------------------------------------------------------------------------
# действия
# --------------------------------------------------------------------------
def plan_actions(findings: list[dict]) -> tuple[list[dict], list[dict]]:
    """Разложить находки на «сделаю сам» и «только человек».

    Никаких догадок: действие берётся из правила. Находка, для которой
    действия не описано, попадает в «только человек» — умолчанием является
    бездействие, а не самодеятельность.
    """
    doable, human = [], []
    for f in findings:
        cls, verdict, rid = f.get("class"), f.get("verdict"), f.get("id")
        action = ACTIONS.get(rid)
        if cls in HUMAN_ONLY or action is None:
            human.append({"id": rid, "class": cls, "verdict": verdict,
                          "headline": f.get("headline", ""),
                          "why_human": ("правило требует решения человека"
                                        if cls in HUMAN_ONLY
                                        else "автоматического действия не описано")})
            continue
        doable.append({"id": rid, "class": cls, "verdict": verdict,
                       "headline": f.get("headline", ""),
                       "action": action["describe"], "kind": action["kind"]})
    return doable, human


def _write_json(path: Path, data) -> None:
    """Запись настроек одним движением — общим модулем, а не своей копией.

    Своя копия здесь была, и в ней нашлись три дефекта сразу: общее на всех имя
    временного файла, потеря режима доступа при замене и отсутствие замка на
    цикл. Все три жили одинаково ещё в девяти инструментах — ровно то, ради
    чего запись съехалась в один модуль (tools/atomic_store.py).

    Отступ два: файл настроек читает человек, и переформатировать его заодно с
    правкой одного поля значит выдать её за правку всего файла.
    """
    atomic_store.записать_json(path, data, indent=2)


def act_set_default_mode(dry: bool) -> dict:
    """Задать permissions.defaultMode явно.

    Возвращает запись для журнала: какое поле, было ли оно, что было и что
    стало. Откат читает именно её, а не файл целиком, — поэтому здесь важно
    не только сделать правку, но и назвать её точно.
    """
    path = CLAUDE / "settings.json"
    # Замок держится на ВЕСЬ цикл, а не на запись: между чтением и записью и
    # находится дыра. Stop-хук, фаза и планка ходят одновременно; оба процесса
    # читают старые настройки, каждый меняет своё поле и пишет файл целиком —
    # записавший вторым затирает чужую правку, и оба выходят нулём.
    with atomic_store.Lock(path):
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        родитель_был = isinstance(data.get("permissions"), dict)
        perms = data.setdefault("permissions", {})
        before = perms.get("defaultMode")
        if before:
            return {"changed": False, "why": f"уже задан: {before}"}
        поле_было = "defaultMode" in perms
        if not dry:
            perms["defaultMode"] = "plan"
            _write_json(path, data)
    return {"changed": True, "file": "settings.json",
            "field": "permissions.defaultMode",
            "before_present": поле_было, "before": before, "after": "plan",
            "created_parents": [] if родитель_был else ["permissions"]}


def act_base_item(item: dict, dry: bool) -> dict:
    """Применить ОДИН пункт базового набора.

    Зачем это существует. Диагностика без применения оставляет человека ровно
    там, где нашла: со списком проблем и без единого решения. Ради этого списка
    ничего не строилось — а до появления этой функции набор именно им и был,
    и улика в карте плана засчитывала наличие ТАБЛИЦЫ за наличие механизма.

    Граница проходит по полю `touches`, а не по классу:

      · «settings» — правка файлов настроек. Инструмент имеет на это право:
        файл его, копия сделана до правки, откат существует.
      · «machine» — установка чужого софта. Инструмент НЕ ставит это сам
        никогда, даже с подтверждения: он готовит точную команду и отдаёт её
        человеку. Установщик, ставящий пакеты молча, — это тот же захват, что
        и хук, запускающий проверку без спроса.

    Возвращает то же, что остальные действия: что изменилось и почему нет,
    если нет.
    """
    if item["touches"] == "machine":
        return {"changed": False, "human_only": True,
                "why": "ставит софт на машину — команду готовлю, запускаешь ты",
                "command": item.get("command", "команда не описана")}
    fn = _BASE_ACTIONS.get(item["id"])
    if fn is None:
        return {"changed": False, "why": "автоматического действия не описано"}
    return fn(dry)


def act_set_statusline(dry: bool) -> dict:
    """Показать расход в строке состояния.

    Без него бюджетные пороги считаются от самооценки модели, которую никто
    не сверял: человек не видит цену собственной сессии.
    """
    return _set_setting("statusLine", {"type": "command",
                                       "command": "npx -y ccusage statusline"}, dry)


def act_wire_stop_gate(dry: bool) -> dict:
    """Подключить Stop-гейт в пользовательские настройки.

    Плагин приносит свой hooks.json, но человек мог поставить пакеты выборочно.
    Явная запись в settings.json переживает выборочную установку.
    """
    return _set_setting("_superstackStopGate", True, dry)


def _set_setting(key: str, value, dry: bool) -> dict:
    """Одна правка settings.json: атомарная запись, ничего не затирающая.

    Существующее значение НЕ перезаписывается: человек мог настроить своё,
    и «поставлю как лучше» здесь означает потерю его решения.
    """
    path = CLAUDE / "settings.json"
    # Замок на весь цикл — по той же причине, что и в соседней правке: два
    # процесса, читающие одно и то же старое состояние, пишут файл целиком.
    with atomic_store.Lock(path):
        data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        before = data.get(key)
        if before is not None:
            return {"changed": False, "why": f"уже задано: {key}"}
        поле_было = key in data
        if not dry:
            data[key] = value
            _write_json(path, data)
    return {"changed": True, "file": "settings.json", "field": key,
            "before_present": поле_было, "before": before, "after": value,
            "created_parents": []}


_BASE_ACTIONS = {
    "base.plan-mode": act_set_default_mode,
    "base.cost-visible": act_set_statusline,
    "base.stop-gate": act_wire_stop_gate,
    # base.kill-switch приносит сам пакет (tools/pause.sh) — ставить нечего,
    # поэтому действия нет и «применение» для него означало бы ложную работу.
}


#: реестр действий. Ключ — id правила. Всё, чего здесь нет, человек делает сам.
ACTIONS: dict[str, dict] = {
    "ctx.default-mode-unset": {
        "kind": "edit-settings",
        # Каталог здесь НЕ называется: он один на весь запуск и печатается
        # отдельной строкой из `профиль_строкой()`. Прежде тут стоял литерал
        # `~/.claude/settings.json` — и на машине с назначенным профилем это
        # было прямое враньё в единственном месте, которое человек читает
        # перед тем, как согласиться.
        "describe": "задам permissions.defaultMode = plan в settings.json",
        "run": act_set_default_mode,
    },
}


# --------------------------------------------------------------------------
# базовый набор: сам перечень — в tools/base_kit.py, здесь только применение
# --------------------------------------------------------------------------
def base_kit_status(facts: dict) -> dict:
    """Что из базового набора уже есть, чего нет и кто это ставит.

    Факт, которого проба не вернула, — это НЕ «отсутствует». Проверка,
    не отработавшая на этой машине, называется отдельно: иначе отчёт
    предложит поставить то, что уже стоит, и потеряет доверие с первого пункта.
    """
    present, missing, unknown = [], [], []
    for item in BASE_KIT:
        try:
            got = item["present"](facts)
        except Exception as e:                       # noqa: BLE001
            unknown.append({**_kit_row(item), "why_unknown": str(e)})
            continue
        (present if got else missing).append(_kit_row(item))
    return {
        "kit": "base", "present": present, "missing": missing, "unknown": unknown,
        "auto": [r for r in missing if r["class"] in AUTO_APPLICABLE],
        "needs_consent": [r for r in missing if r["class"] in NEEDS_CONSENT],
        "complete": not missing and not unknown,
    }



def cmd_base(argv: list[str]) -> int:
    """Показать состояние базового набора. Показать, а не поставить.

    Отдельная команда без побочных действий: человек обязан увидеть список
    до того, как что-то произойдёт. Применение идёт через `run` и только
    по подтверждению.
    """
    facts = {}
    if argv:
        try:
            raw = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"НЕ УДАЛОСЬ прочитать факты: {e}", file=sys.stderr)
            return 4
        facts = {k: (v.get("value") if isinstance(v, dict) else v)
                 for k, v in raw.items()}
    st = base_kit_status(facts)
    print(профиль_строкой())
    for row in st["present"]:
        print(f"  + {row['what']}")
    for row in st["missing"]:
        who = "поставлю сам" if row["class"] in AUTO_APPLICABLE else "спрошу тебя"
        print(f"  - {row['what']}  ({who})")
        print(f"      {row['why']}")
    for row in st["unknown"]:
        print(f"  ? {row['what']} — не смог проверить: {row['why_unknown']}")
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0 if st["complete"] else 1


# --------------------------------------------------------------------------
# карантин
# --------------------------------------------------------------------------
def quarantine(path: Path, reason: str) -> dict:
    """Переместить, а не удалить. Удаление необратимо, перемещение — нет."""
    if not path.exists():
        return {"moved": False, "why": "нет такого пути"}
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    dst = QUARANTINE / f"{stamp()}-{path.name}"
    shutil.move(str(path), str(dst))
    return {"moved": True, "from": str(path), "to": str(dst), "reason": reason}


# --------------------------------------------------------------------------
# команды
# --------------------------------------------------------------------------
def load_findings(arg: str) -> tuple[list[dict], list[dict]]:
    data = json.loads(Path(arg).read_text(encoding="utf-8"))
    findings = data.get("findings", [])
    hits = []
    for f in findings:
        ev = f.get("evidence") or {}
        for v in ev.values():
            if isinstance(v, list):
                hits += [h for h in v if isinstance(h, dict) and "location" in h]
    return findings, hits


def cmd_plan(argv: list[str]) -> int:
    findings, _ = load_findings(argv[0])
    doable, human = plan_actions(findings)
    print(профиль_строкой())
    print("ЧТО Я СДЕЛАЮ САМ")
    if not doable:
        print("  ничего — все находки требуют решения человека")
    for a in doable:
        print(f"  · {a['headline']}\n      -> {a['action']}")
    print("\nЧТО СДЕЛАЕШЬ ТОЛЬКО ТЫ")
    for h in human:
        print(f"  · {h['headline']}\n      причина: {h['why_human']}")
    print("\nПеред первым изменением будет полный бэкап настроек.")
    print("Секреты в бэкап НЕ копируются: откат вернёт файл, но не вернёт")
    print("значение секрета — на его месте будет метка с отпечатком.")
    # О непроверенном человек узнаёт ЗДЕСЬ, а не в момент отказа: план
    # существует ровно затем, чтобы неожиданностей на шаге «run» не было.
    непроверенные = прочитать_настройки()[1]
    if непроверенные:
        print("\nЧТО Я НЕ ПРОВЕРИЛ")
        for э in непроверенные:
            print(f"  · {э['file']}: {э['why']}")
        print("  Эти файлы не разбираются, поэтому о содержимом я не знаю "
              "ничего:\n  в них могут лежать незакрытые значения. Копию с них "
              "не снимаю и\n  менять ничего не буду, пока они такие.")
    return 0


def cmd_run(argv: list[str]) -> int:
    consent = "--yes" in argv
    # Куда пойдёт правка — ПЕРВОЙ строкой и до любого решения об изменении:
    # отказ по согласию, «применять нечего» и сама правка одинаково относятся
    # к конкретному каталогу, и человек обязан видеть, к какому.
    print(профиль_строкой())
    findings, hits = load_findings(argv[0])
    doable, human = plan_actions(findings)

    needs = [a for a in doable if a["class"] in NEEDS_CONSENT]
    if needs and not consent:
        print("ОСТАНОВЛЕНО: часть изменений требует явного согласия.")
        for a in needs:
            print(f"  · {a['headline']} -> {a['action']}")
        print("\nПовтори с --yes, если согласен. Ничего не менял.")
        return 6

    if not doable:
        print("Применять нечего: все находки требуют решения человека.")
        return 0

    backup = make_backup(hits)
    print(f"Бэкап: {backup['dir']}  файлов: {len(backup['files'])}")
    if backup["redacted"]:
        print("  секреты вырезаны из копии — откат вернёт файл, но не значение")

    manifest = {"created": now(), "backup": backup, "applied": []}
    for a in doable:
        result = ACTIONS[a["id"]]["run"](dry=False)
        manifest["applied"].append(_redact_journal(
            {"id": a["id"], "action": a["action"], **result}, hits))
        mark = "изменено" if result.get("changed") else "без изменений"
        print(f"  {mark}: {a['action']}")

    mpath = Path(backup["dir"]) / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОткатить: python3 tools/apply.py undo {Path(backup['dir']).name}")
    if human:
        print(f"Осталось тебе: {len(human)} — см. plan")
    return 0


def safe_target(имя) -> Path:
    """Файл, который откату разрешено трогать. Имя ПРОВЕРЯЕТСЯ, а не берётся.

    Откат брал имена из записи и присоединял их к каталогу настроек без
    единой проверки. Имя, поднимающееся на уровень выше, переписывало файл
    СНАРУЖИ каталога настроек. Своя же запись, сделанная вчера, — внешние
    данные: между записью и чтением она лежала на диске, где её мог править
    кто угодно, и «мы её сами писали» ничего не доказывает.

    Проверок две, и каждая ловит своё. Список имён отсекает файл, к которому
    инструмент не имеет отношения, даже когда тот лежит ровно в каталоге
    настроек. Разбор пути отсекает то, что список пропустил бы по форме:
    разрешённое имя, оказавшееся ссылкой наружу.
    """
    if not isinstance(имя, str) or имя not in WRITABLE:
        raise UnsafeName(f"«{имя}» не из тех файлов, что откату разрешено "
                         f"трогать ({', '.join(WRITABLE)})")
    цель = (CLAUDE / имя).resolve()
    корень = CLAUDE.resolve()
    if not цель.is_relative_to(корень):
        raise UnsafeName(f"«{имя}» после разбора пути уводит за каталог "
                         f"настроек: {цель}")
    return CLAUDE / имя


def cmd_undo(argv: list[str]) -> int:
    ид = argv[0]
    if not isinstance(ид, str) or ид in ("", ".", "..") or "/" in ид or "\\" in ид:
        # Имя копии — тоже путь, и оно тоже приходит снаружи.
        print(f"нет такого отката: {ид}", file=sys.stderr)
        return 1
    bdir = BACKUPS / ид
    if not bdir.is_dir():
        # Где искали — часть ответа. Копия, снятая в другом профиле, здесь
        # выглядит как «отката не было»: без имени каталога человек ищет
        # причину в списке откатов, а она в том, какой профиль сейчас активен.
        print(f"нет такого отката: {ид}\n"
              f"  искал здесь: {BACKUPS}\n"
              f"  {профиль_строкой()}", file=sys.stderr)
        return 1
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    формат = _формат_копии(manifest)
    if формат is not None and формат != UNDO_FORMAT:
        print(f"ОТКАЗ: копия «{ид}» записана форматом «{формат}» — этот "
              f"инструмент его не знает и разбирать по догадке не будет. "
              f"Ничего не менял. Копия лежит здесь: {bdir}", file=sys.stderr)
        return 1
    if формат == UNDO_FORMAT:
        # Куда писать и чем писать — отсюда: модуль отката знает формат
        # журнала и не знает профиля человека (см. его докстринг).
        restored, отказы = _undo_journal(manifest, safe_target, _write_json)
    else:
        restored, отказы = _undo_legacy(bdir, manifest, safe_target)

    print(f"Восстановлено: {', '.join(restored) if restored else '—'}")
    for о in отказы:
        print(f"НЕ восстановлено — {о['что']}: {о['почему']}", file=sys.stderr)
    if not restored:
        # Ноль здесь означал бы «сделано» — и слой выше (oops.py) печатал бы на
        # нём «откатил» человеку, у которого не вернулось НИ ОДНОГО поля. Это то
        # самое подтверждение раньше результата, только этажом ниже: там код
        # честно проверяется, но врёт сам код.
        причина = ("; ".join(о["почему"] for о in отказы[:3]) if отказы
                   else "в копии не оказалось ничего, что можно вернуть")
        print(f"НЕ восстановлено ничего: {причина}. Копия лежит здесь: {bdir} "
              f"— нужные поля верни руками, из копии они не вернутся.",
              file=sys.stderr)
        return UNDO_NOTHING_RESTORED
    if отказы:
        # Рядом с самими отказами, а не в общем выводе: подсказка, оторванная
        # от того, к чему она относится, читается как общая фраза и теряется.
        print(f"  Копия лежит здесь: {bdir}\n"
              "  Что не откатилось — верни руками: чужое я не трогаю.",
              file=sys.stderr)
    return 0


def cmd_list(_argv: list[str]) -> int:
    print(профиль_строкой())
    if not BACKUPS.is_dir():
        print(f"откатов пока нет  (искал здесь: {BACKUPS})")
        return 0
    for d in sorted(BACKUPS.iterdir(), reverse=True):
        m = d / "manifest.json"
        if not m.is_file():
            continue
        data = json.loads(m.read_text(encoding="utf-8"))
        n = len(data.get("applied", []))
        print(f"  {d.name}  изменений: {n}  {data.get('created', '?')}")
    return 0


def main() -> int:
    halt_if_paused()
    if len(sys.argv) < 2:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 2
    cmd, argv = sys.argv[1], sys.argv[2:]
    table = {"plan": cmd_plan, "run": cmd_run, "undo": cmd_undo,
             "list": cmd_list, "base": cmd_base}
    if cmd not in table:
        print(f"неизвестная команда: {cmd}", file=sys.stderr)
        return 2
    if cmd in ("plan", "run", "undo") and not argv:
        print(f"команде {cmd} нужен аргумент", file=sys.stderr)
        return 2
    return table[cmd](argv)


if __name__ == "__main__":
    sys.exit(main())
