#!/usr/bin/env python3
"""SUPERSTACK — состояние стройки: что доказано, что заявлено, что не начато.

Зачем это отдельный инструмент, а не поле в отчёте.

Панель хода работ — самая опасная поверхность продукта. Текст можно не дочитать,
полоску нельзя не увидеть: зелёная шкала читается как факт, даже когда за ней
стоит одно лишь слово агента «готово». Красивый дашборд поверх недоказанного
даёт уверенность ровно там, где её быть не должно.

Поэтому здесь «готово» — не статус, а СЛЕДСТВИЕ. Задача переходит в `proven`
только вместе с кодом возврата гейта, и записать иначе нельзя: функция требует
доказательство отдельным аргументом и отказывается без него. Сказанное агентом
записывается как `claimed` и на экране выглядит иначе — не потому, что мы не
доверяем, а потому, что это другое утверждение.

Три величины, которые обычно копятся молча, здесь считаются наравне с прогрессом:

  · ЗАГЛУШКИ    — что осталось ненаписанным под видом написанного;
  · ДОПУЩЕНИЯ   — что решили за человека, не спросив;
  · НЕИЗВЕСТНЫЕ — что не выяснили и понесли дальше.

Прогресс без этих трёх — половина картины, и именно та половина, которую
приятно показывать.

  python3 progress.py init <файл> <название>   [--fresh]
  python3 progress.py phase <файл> <имя> <держатель> [--detail X]
                            держатель: человек|система|исполнитель|внешний-инструмент
  python3 progress.py stage <файл> <имя> <статус> [--detail X]
  python3 progress.py task  <файл> <id> <имя> --wave N --status X [--exit-code N]
                            [--requirements R01,R02] [--zone src/a/] [--blocked-by 01]
                            [--started ISO]   отметка старта; при --status running
                                              проставляется сама
                            [--finished ISO]  отметка конца; при уходе из running
                                              проставляется сама. По ней панель
                                              отвечает «сколько ещё ждать»
                            [--goal "..."] [--acceptance "a;b"] [--quotes "..."]
                            [--spec-sections "..."]   — без них таск не передать
                            [--holdout "a;b"]  проверки, скрытые от исполнителя
                            [--criterion "что|где|высоко|низко[|мимо]"] — можно
                                              несколько раз; критерий с адресом
  python3 progress.py lang  <файл> ru|en                на каком языке говорить
                            с человеком: пишется один раз в фазе 0 по его
                            первой фразе, читают все инструменты
  python3 progress.py debt  <файл> <вид> <текст>        вид: stub|assumption|env
  python3 progress.py debt  <файл> <вид> --none         смотрели, закрывать нечего
  python3 progress.py criteria <файл> строгие|свободные
                            строгие: критерий без адреса дальше не передаётся
  python3 progress.py source <файл> <путь>              где лежат требования и задачи
  python3 progress.py req   <файл> --total N --covered N [--dropped N] [--deferred N]
  python3 progress.py show  <файл>

  код 0 — прочитано, 2 — состояние неполно, 3 — ошибка вызова,
  4 — файл уже есть и не пуст: ничего не тронуто (см. --fresh)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Замок и запись — общие для всех инструментов, которые пишут одни и те же
# файлы: размноженная копипастом ошибка чинится по одному файлу и остаётся в
# остальных. Каталог скрипта при импорте из теста в sys.path не попадает,
# поэтому добавляется явно — как у соседей.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_store  # noqa: E402

# Словарь состояний работы и всё, что по нему считается, — рядом, в
# `panel_facts.py`. Ввоз односторонний: числа панели не знают про запись
# состояния, а запись берёт словарь оттуда — четыре слова, объявленные в
# двух местах, разошлись бы молча, и «доказано» стало бы двумя разными
# «доказано». Имена остаются достижимы отсюда: их зовут снаружи.
from panel_facts import (  # noqa: E402,F401
    CLAIMED, PROVEN, RUNNING, TASK_STATES, WAITING, панель_факты)

STAGE_STATES = (PROVEN, CLAIMED, RUNNING, WAITING)

#: На ком сейчас ход. Половина ожиданий в прогоне — не «долго считает», а
#: «каждый ждёт другого»: человек ждёт систему, система ждёт человека, и оба
#: молчат. Панель обязана называть держателя, иначе она показывает занятость,
#: а не состояние.
OWNERS = ("человек", "система", "исполнитель", "внешний-инструмент")

#: Этап, на котором пишут код. Он же назван в панели; совпадение написаний
#: проверяется тестом, иначе они разойдутся молча и панель начнёт показывать
#: этап, которого нет в её же списке.
BUILD_PHASE = "Пишем код"

DEBT_KINDS = ("stub", "assumption", "env")

#: Кому и что делать. Долг — не бухгалтерия, а список того, что нужно ОТ
#: ЧЕЛОВЕКА: «сколько долга» и «что от меня требуется» — разные вопросы,
#: и второй единственный, ради которого этот блок стоит смотреть.
DEBT_ASKS = {
    "stub": "заглушки — нужны твои данные",
    "assumption": "решения, принятые за тебя",
    "env": "переменные окружения — нужны твои значения",
}

EMPTY = {
    "schema": "superstack.progress.v1",
    "project": "",
    "stages": [],
    # Где мы сейчас и на ком ход. Одно на прогон, в отличие от истории этапов.
    "phase": None,
    "waves": {},
    "debt": {k: [] for k in DEBT_KINDS},
    "requirements": {"total": None, "covered": None,
                     "dropped": 0, "deferred": 0, "stubbed": 0},
    # Отметка «смотрели и закрывать нечего». Без неё пустой список неотличим
    # от «никто не записывал», а это разные утверждения: первое — результат
    # проверки, второе — её отсутствие. Показывать их одинаково значит выдать
    # неведение за порядок.
    "debt_reviewed": {},
    "updated": None,
    "source": None,
    # Язык, на котором с человеком разговаривают инструменты. Лежит рядом с
    # этапом нарочно: это такой же факт прогона, и читается он оттуда же,
    # откуда панель берёт, на чём мы остановились. None — язык не называли,
    # и инструменты остаются на русском (tools/lang.py).
    "lang": None,
}


def load(path: Path) -> dict:
    if not path.is_file():
        return json.loads(json.dumps(EMPTY))
    try:
        d = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(EMPTY))
    for k, v in EMPTY.items():
        d.setdefault(k, json.loads(json.dumps(v)))
    return d


#: Замок переехал в общий модуль записи вместе с веткой для Windows: тот же
#: механизм понадобился всем, кто пишет одни и те же файлы. Имя здесь оставлено
#: прежним — это по-прежнему замок на весь цикл «прочитал, изменил, записал»,
#: и держится он всю операцию, а не одну запись.
_Lock = atomic_store.Lock


def save(path: Path, data: dict, now: "str | None" = None) -> None:
    """Записать состояние, проставив отметку времени.

    Панель без «когда обновлено» выглядит живой, даже когда данные недельной
    давности: число без отметки — то же утверждение без провенанса, которое
    система запрещает везде. Часы подаются параметром, иначе тест перестаёт
    давать один и тот же ответ завтра.
    """
    from datetime import datetime, timezone
    data["updated"] = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    atomic_store.записать_json(path, data, indent=1)


def снимок_на_панель(path: Path, data: dict) -> bool:
    """Вписать снимок состояния в страницу панели рядом с файлом состояния.

    Зачем это делает запись состояния, а не панель.

    Страницу нельзя научить читать соседний файл с диска — браузер запрещает.
    Значит панель без живого сервера показывала бы пустые полосы: «ничего не
    происходит» вместо «нечего показывать». Она умирала вместе с процессом,
    и назавтра по той же ссылке было пусто.

    Поэтому состояние едет ВНУТРИ страницы, и вписывается ровно тогда, когда
    меняется, — здесь. Открытый двойным кликом файл показывает последнюю
    запись сразу, через месяц и на машине, где сервера не было вовсе; живой
    сервер кладёт свежее поверх.

    Отказ не молчит и не останавливает работу: ход работ записан — это
    главное, а не вписанный снимок значит только, что страница отстала, и об
    этом говорится вслух.
    """
    import importlib.util
    tool = Path(__file__).resolve().parent / "live_panel.py"
    try:
        spec = importlib.util.spec_from_file_location("ss_live_panel_snap", tool)
        if spec is None or spec.loader is None:
            raise ImportError(f"не загружается: {tool}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.embed_state(path.parent, data)
        return True
    except (OSError, ValueError, ImportError, AttributeError, KeyError) as e:
        print(f"НЕ ВПИСАЛ снимок в панель ({tool.name}: {e}) — ход работ "
              "записан, но страница на диске осталась прежней", file=sys.stderr)
        return False


#: Код отказа «здесь уже начата работа». Ноль здесь означал бы «завёл», а
#: завести поверх начатого — это стереть ход вчерашней стройки молча. Число то
#: же, что у manifest.py: для человека это одно событие, и разными кодами оно
#: быть не может.
ЗАНЯТО = 4


def занято(path: Path) -> bool:
    """Лежит ли по этому пути уже чья-то работа."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def отложить(path: Path) -> Path:
    """Копия рядом, со временем в имени, ДО перезаписи.

    Перезапись по явной просьбе остаётся возможной, а вот безвозвратной быть
    не должна: человек, сказавший «начнём заново», отвечает за сегодняшний
    выбор, а не за то, что вчерашний ход работ стёрся без следа.
    """
    from datetime import datetime, timezone
    метка = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    копия = path.with_name(f"{path.name}.{метка}.bak")
    n = 0
    while копия.exists():
        n += 1
        копия = path.with_name(f"{path.name}.{метка}-{n}.bak")
    копия.write_bytes(path.read_bytes())
    return копия


def уже_начато(path: Path, data: dict) -> str:
    """Что сказать вместо заведения: что здесь лежит и какие есть два выхода."""
    проект = data.get("project") or "без названия"
    когда = (data.get("updated") or "").split("T")[0] or "неизвестно когда"
    частей = sum(len(v) for v in (data.get("waves") or {}).values())
    return ("НЕ ЗАВЁЛ: здесь уже начата работа, ничего не тронуто.\n"
            f"  файл: {path}\n"
            f"  проект: {проект}\n"
            f"  частей работы в нём: {частей}, последняя запись: {когда}\n"
            "  продолжить начатое — заводить заново не нужно, работай с этим "
            "файлом;\n"
            "  начать всё сначала — повтори с --fresh: прежнее сперва ляжет "
            "копией рядом.")


def _wave_of(data: dict, tid: str) -> int:
    """В какой волне таск лежит сейчас. Новому — первая."""
    for w, lst in data["waves"].items():
        if any(t["id"] == tid for t in lst):
            try:
                return int(w)
            except ValueError:
                return 1
    return 1


def _прежняя_запись(data: dict, tid: str) -> "dict | None":
    """Как этот таск записан сейчас — или None, если такого ещё нет.

    Спрашивают об этом дважды и по разным поводам: за отметкой старта и за
    полями, которые нельзя потерять при перезаписи. Два одинаковых поиска в
    одной функции расходятся при первой же правке одного из них.
    """
    return next((x for w in data["waves"].values() for x in w
                 if x["id"] == tid), None)


def _донести_прежние_поля(entry: dict, keep: "dict | None") -> None:
    """Достроить новую запись тем, чего в этот раз не передали."""
    if keep:
        # Перезапись таска не должна терять то, чего не передали в этот раз:
        # статус меняют чаще, чем критерии, и молчаливая потеря критериев
        # сделала бы передачу невозможной со второго вызова.
        for key in ("goal", "acceptance", "quotes", "spec_sections",
                    "requirements", "zone", "blockedBy", "started", "holdout",
                    "finished"):
            if key not in entry and key in keep:
                entry[key] = keep[key]


def _отметить_конец(entry: dict, keep: "dict | None", status: str,
                    finished: "str | None") -> None:
    """Проставить отметку конца работы над частью — или снять её."""
    # Отметка конца — единственный способ ответить человеку «сколько ещё».
    # Обещать срок нельзя: никто его не знает. Но сказать «прошлые части
    # занимали столько-то» можно — если кто-то это мерил. Раньше не мерил
    # никто, и панель честно молчала о времени, оставляя человека гадать.
    if status == RUNNING:
        entry.pop("finished", None)   # снова в работе — прежний замер неверен
    elif finished is not None:
        entry["finished"] = finished  # часы подаются извне — иначе тест протухнет
    elif keep and keep.get("status") == RUNNING and "finished" not in entry:
        from datetime import datetime, timezone
        entry["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _положить_в_волну(data: dict, tid: str, entry: dict,
                      wave: "int | None") -> None:
    """Убрать таск отовсюду и положить в его волну — ровно в одном месте."""
    # Волна не передана — таск остаётся там, где лежит. Раньше умолчание было
    # «первая», и обновление существующего таска клало КОПИЮ в первую волну,
    # оставляя исходную запись на месте. Найдено сквозным прогоном: после
    # `task 02 --blocked-by 01` в файле оказались волна 1 с 01,02,03,04 и волна
    # 2 с 02,03,04, а `crew.py` доложил «02 и 02 делят территорию».
    #
    # Цена дефекта — не грязный файл: волна раздаётся субагентам по одному
    # вызову на таск, и два из них получили бы ОДИН таск, то есть писали бы в
    # одну зону одновременно. Ровно та потеря, которую ловит расчёт зон, — с
    # той разницей, что здесь её создаёт сам инструмент планирования.
    where = wave if wave is not None else _wave_of(data, tid)
    # Удаление из ВСЕХ волн до вставки: перенос обязан быть переносом, а не
    # копированием. Один и тот же id в двух волнах — не состояние, а мусор.
    # Позиция внутри волны запоминается: план читают глазами, и таск, уходящий
    # в конец списка при каждой правке статуса, перетасовывает страницу без
    # единой смысловой причины.
    at = None
    for w, lst in data["waves"].items():
        for i in range(len(lst) - 1, -1, -1):
            if lst[i]["id"] == tid:
                if w == str(where):
                    at = i
                del lst[i]
    target = data["waves"].setdefault(str(where), [])
    if at is None:
        target.append(entry)
    else:
        target.insert(at, entry)
    # Опустевшая волна удаляется: пустой ключ «2» читается как «волна есть, в
    # ней никого», и расчёт ярусов считал бы её за волну.
    for w in [k for k, v in data["waves"].items() if not v]:
        del data["waves"][w]


def _этап_за_работой(data: dict, name: str, status: str) -> dict:
    """Двинуть этап туда, куда его двигает сама работа."""
    # Этап следует из ФАКТА, а не из памяти того, кто ведёт состояние.
    #
    # Раньше этап писала одна команда, а задачу запускала другая, и ничто их не
    # связывало. 17.08 это выстрелило: этап остался на «Дизайн-система», пока
    # помощник уже писал страницу. Панель показывала записанное — то есть
    # неправду, — и человек увидел на экране одно, а в разговоре другое.
    #
    # Двигаем только ВПЕРЁД и только по факту работы; отметка «идёт уже» при
    # этом не сбрасывается, иначе каждая правка задачи обнуляла бы таймер и
    # застрявший этап выглядел бы вечно свежим.
    if status == RUNNING:
        busy = sorted(t["id"] for w in data["waves"].values() for t in w
                      if t["status"] == RUNNING)
        detail = (f"задача {busy[0]} — {name}" if len(busy) == 1
                  else "задачи в работе: " + ", ".join(busy))
        was = data.get("phase") or {}
        keep = was.get("since") if was.get("name") == BUILD_PHASE else None
        data = set_phase(data, BUILD_PHASE, "исполнитель", detail, now=keep,
                         plain=False)
    return data


def set_task(data: dict, tid: str, name: str, wave: "int | None",
             status: str, exit_code: "int | None" = None,
             requirements: "list | None" = None,
             zone: "list | None" = None,
             blocked_by: "list | None" = None,
             started: "str | None" = None,
             finished: "str | None" = None,
             goal: "str | None" = None,
             acceptance: "list | None" = None,
             quotes: "list | None" = None,
             spec_sections: "list | None" = None,
             holdout: "list | None" = None) -> dict:
    """Записать задачу. «Готово» без доказательства записать НЕЛЬЗЯ.

    Единственное место, где решается, будет ли на экране сплошная полоса или
    контурная. Разрешив `proven` по слову, мы получили бы дашборд, на котором
    «агент сказал» неотличимо от «гейт вернул ноль», — то есть ровно тот отказ,
    против которого построена вся система.
    """
    if status not in TASK_STATES:
        raise ValueError(f"неизвестный статус задачи: {status}")
    if status == PROVEN and exit_code != 0:
        raise ValueError(
            "«доказано» требует кода возврата 0 от гейта; без него это «заявлено» "
            f"(получено: {exit_code!r})")
    entry = {"id": tid, "name": name, "status": status}
    if exit_code is not None:
        entry["exit_code"] = exit_code
    # Требования и зона живут НА ТАСКЕ, а не в отдельной таблице соответствий.
    # Отдельная таблица расходится с планом при первой же перенарезке, и
    # расходится молча: обе стороны выглядят правдоподобно. Здесь трассируемость
    # (гейт G3) и непересечение волн (расчёт экипажа) читаются из одного места.
    if requirements is not None:
        entry["requirements"] = list(requirements)
    if zone is not None:
        entry["zone"] = list(zone)
    # Блокеры живут ЗДЕСЬ же, а не в отдельном файле плана. Без них расчёт
    # волн не имеет исходных данных: crew.py видит таски без зависимостей,
    # кладёт все в первую волну и честно докладывает расхождение с
    # объявленными волнами плюс ложные пересечения зон. Найдено сквозным
    # прогоном — поодиночке оба инструмента работали.
    if blocked_by is not None:
        entry["blockedBy"] = list(blocked_by)
    # Отметка старта — единственный источник данных для обнаружения
    # последовательного полёта: товарищи по волне обязаны стартовать в
    # пределах секунд. Без неё механизм, ловящий отказ, который «изнутри
    # выглядит как правильная работа», не имеет что мерить и вечно отвечает
    # «не смог проверить». Часы подаются аргументом, иначе тест перестанет
    # давать один и тот же ответ завтра.
    if started is not None:
        entry["started"] = started
    else:
        prev = _прежняя_запись(data, tid)
        if prev and prev.get("started"):
            entry["started"] = prev["started"]

    # Поля, без которых таск нельзя ПЕРЕДАТЬ. Их отсутствие здесь было
    # структурным разрывом: инструмент, создающий таски, не мог заполнить то,
    # что требует инструмент, их раздающий, — и передача честно отказывала
    # на каждом таске. Поодиночке оба работали; цепочка не складывалась.
    # `holdout` — проверки, которых исполнитель не увидит. Они живут рядом с
    # критериями и намеренно НЕ попадают в промпт: всё, что исполнитель видит,
    # находится внутри его оптимизационной петли, и имея критерий, он со
    # временем удовлетворит именно его. Часть проверок обязана остаться снаружи.
    for key, val in (("goal", goal), ("acceptance", acceptance),
                     ("quotes", quotes), ("spec_sections", spec_sections),
                     ("holdout", holdout)):
        if val is not None:
            entry[key] = list(val) if isinstance(val, (list, tuple)) else val

    keep = _прежняя_запись(data, tid)
    _донести_прежние_поля(entry, keep)
    _отметить_конец(entry, keep, status, finished)
    _положить_в_волну(data, tid, entry, wave)
    return _этап_за_работой(data, name, status)


def _jargon_in(text: str) -> list:
    """Слова, которых человек не поймёт, — списком.

    Словарь лежит в этом же пакете, рядом. Раньше он лежал в соседнем и
    искался общим резолвером, а весь вызов был обёрнут в `except Exception:
    return []` — на случай, если сосед не установлен. Слияние пакетов удалило
    резолвер, `import where` начал падать, и проверка ВЫКЛЮЧИЛАСЬ ЦЕЛИКОМ,
    не уронив ни одного теста в самом инструменте: пустой список читается как
    «жаргона нет». Поймал её единственный тест, который смотрел на результат,
    а не на код.

    Поэтому отказ здесь больше не молчит. «Не нашёл жаргон» и «не смог
    проверить» — разные ответы, и второй печатается человеку. Прогон при этом
    не останавливается: сломать запись состояния из-за вычитки — цена выше
    пользы, а вот скрыть, что вычитки не было, нельзя.
    """
    if not text:
        return []
    import importlib.util
    p = Path(__file__).resolve().parent / "plain_ru.py"
    try:
        spec = importlib.util.spec_from_file_location("ss_plain_ru", p)
        if spec is None or spec.loader is None:
            raise ImportError(f"не загружается: {p}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.find_jargon(text)
    except (OSError, ValueError, ImportError, AttributeError) as e:
        print(f"НЕ СМОГ ПРОВЕРИТЬ подпись на жаргон ({p.name}: {e}) — "
              "текст уходит человеку невычитанным", file=sys.stderr)
        return []


def set_phase(data: dict, name: str, owner: str, detail: str = "",
              now: str = None, plain: bool = True) -> dict:
    """Текущая фаза и тот, на ком ход.

    Пишется отдельно от этапов: этапов много и они историчны, а «где мы сейчас»
    ровно одно, и читается оно с панели за полсекунды. Отметка времени нужна,
    чтобы «идёт» отличалось от «застряло»: фаза, висящая на человеке сорок
    минут, и фаза, начатая минуту назад, выглядят одинаково без неё.
    """
    if owner not in OWNERS:
        raise ValueError(f"неизвестный держатель хода: {owner} — есть "
                         + ", ".join(OWNERS))
    # Деталь этапа человек читает на панели, а пишет её модель — и пишет своим
    # рабочим языком. «Таск 06 — страница по принесённой системе; я жду
    # возврата» перечитывается автором как понятная строка и не сообщает
    # неразработчику ничего. Вычиткой это не ловится: чтобы заметить непонятное
    # слово, нужно перестать его понимать. Поэтому — отказ на записи.
    #
    # Проверяется ТОЛЬКО подпись, написанная здесь и сейчас. Подпись, собранная
    # из имени части работы, идёт мимо: имя даёт план, а не эта функция, и
    # отказ записать состояние из-за чужого слова остановил бы прогон вместо
    # того, чтобы поправить текст. Имена частей — отдельный рычаг.
    bad = _jargon_in(detail) if plain else []
    if bad:
        raise ValueError(
            "в подписи этапа слова, которых человек не поймёт: "
            + "; ".join(f"«{w}» → {better}" for w, _, better in bad[:4]))
    from datetime import datetime, timezone
    data["phase"] = {
        "name": name, "owner": owner, "detail": detail,
        "since": now or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return data


def set_stage(data: dict, name: str, status: str, detail: str = "") -> dict:
    if status not in STAGE_STATES:
        raise ValueError(f"неизвестный статус этапа: {status}")
    entry = {"name": name, "status": status, "detail": detail}
    for i, s in enumerate(data["stages"]):
        if s["name"] == name:
            data["stages"][i] = entry
            break
    else:
        data["stages"].append(entry)
    return data


def add_debt(data: dict, kind: str, text: str) -> dict:
    if kind not in DEBT_KINDS:
        raise ValueError(f"неизвестный вид долга: {kind}")
    if text not in data["debt"][kind]:
        data["debt"][kind].append(text)
    data["debt_reviewed"][kind] = True
    return data


def review_debt(data: dict, kind: str) -> dict:
    """Отметить: смотрели, закрывать нечего.

    Единственный способ отличить «проверено и чисто» от «никто не смотрел».
    Без явной отметки пустой список честнее показывать как непроверенный.
    """
    if kind not in DEBT_KINDS:
        raise ValueError(f"неизвестный вид долга: {kind}")
    data["debt_reviewed"][kind] = True
    return data


def summary(data: dict) -> dict:
    """Счётная картина — включая то, чего не хватает, чтобы её считать."""
    tasks = [t for w in data["waves"].values() for t in w]
    by = {s: sum(1 for t in tasks if t["status"] == s) for s in TASK_STATES}
    req = data["requirements"]
    debt = data["debt"]

    gaps = []
    if req["total"] is None or req["covered"] is None:
        gaps.append("покрытие требований не измерено")
    if not tasks:
        gaps.append("задач нет")
    unreviewed = [k for k in DEBT_KINDS if not data["debt_reviewed"].get(k)]
    for k in unreviewed:
        gaps.append(f"{DEBT_ASKS[k]}: никто не проверял")

    # Доля считается ТОЛЬКО от доказанного. Заявленное в прогресс не идёт:
    # иначе шкала растёт от слов, и человек видит движение там, где его нет.
    done = by[PROVEN]
    return {
        "tasks_total": len(tasks),
        "by_status": by,
        "progress": (round(100 * done / len(tasks)) if tasks else None),
        "progress_basis": "только доказанные задачи",
        "debt_total": sum(len(v) for v in debt.values()),
        "debt": {k: len(v) for k, v in debt.items()},
        "requirements": req,
        "debt_unreviewed": unreviewed,
        "unmeasured": gaps,
        "trustworthy": not gaps,
    }


def _языки() -> tuple:
    """Языки, на которых продукт говорит с человеком — из lang.py.

    Импорт по пути, как и у соседей: при импорте из теста каталог скрипта в
    sys.path не попадает. Промолчать нельзя — молча принятый неизвестный язык
    записался бы в состояние и показал человеку пустые надписи.
    """
    import importlib.util
    p = Path(__file__).resolve().parent / "lang.py"
    spec = importlib.util.spec_from_file_location("ss_lang_progress", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.LANGS


def _fail(msg: str) -> int:
    print(f"НЕ УДАЛОСЬ: {msg}", file=sys.stderr)
    return 3


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def _flag(argv: list, name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


#: Поля критерия в порядке записи через `|`. Пятое — «мимо» — необязательно.
ПОЛЯ_КРИТЕРИЯ = ("что", "где", "высоко", "низко", "мимо")


def _критерии(rest: list) -> list:
    """Все `--criterion` из строки вызова. Неполный — отказ, а не тихий пропуск.

    Критерий, потерявший поле по дороге, выглядит как обычный: он просто не
    проверяет того, что должен был. Молчание здесь стоило бы ровно того, ради
    чего форма и заведена.
    """
    out = []
    for i, a in enumerate(rest):
        if a != "--criterion" or i + 1 >= len(rest):
            continue
        части = [ч.strip() for ч in rest[i + 1].split("|")]
        if len(части) < 4 or not all(части[:4]):
            raise ValueError(
                "--criterion: нужно «что|где|высоко|низко[|мимо]», "
                f"получено: {rest[i + 1][:60]!r}")
        out.append(dict(zip(ПОЛЯ_КРИТЕРИЯ, части)))
    return out


def _приёмка(acc: "str | None", крит: list) -> "list | None":
    """Строки и адресные критерии в одном списке — старая форма не отменяется.

    None означает «не трогай существующее»: перезапись таска не должна терять
    критерии только потому, что в этот раз меняли статус.
    """
    if acc is None and not крит:
        return None
    строки = [x.strip() for x in (acc or "").split(";") if x.strip()]
    return строки + крит


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    if len(argv) < 2:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 3
    cmd, path = argv[0], Path(argv[1])
    rest = argv[2:]
    # Замок держится до конца операции: читаем, меняем и пишем под ним.
    # Иначе параллельный помощник, начавший читать между нашими чтением и
    # записью, вернёт файл к прежнему виду — молча и с кодом 0.
    lock = _Lock(path)
    lock.__enter__()
    data = load(path)

    try:
        if cmd == "criteria":
            # Строгость объявляется ЯВНО: молчаливое ужесточение сломало бы
            # проект, начатый до этой проверки, на середине волны.
            if not rest or rest[0] not in ("строгие", "свободные"):
                return _fail("вызов: progress.py criteria <файл> строгие|свободные")
            data["criteria"] = rest[0]
        elif cmd == "init":
            if not rest:
                return _fail("нужно название проекта")
            # Заведение поверх начатого — не заведение, а стирание. Повторный
            # запуск в той же папке уносил ход вчерашней стройки целиком и
            # молча: панель показывала пустую шкалу там, где вчера была
            # половина работы.
            if занято(path):
                if "--fresh" not in rest:
                    print(уже_начато(path, data), file=sys.stderr)
                    lock.__exit__()
                    return ЗАНЯТО
                копия = отложить(path)
                print(f"прежний ход работ отложен сюда: {копия}",
                      file=sys.stderr)
            data = json.loads(json.dumps(EMPTY))
            data["project"] = rest[0]
        elif cmd == "lang":
            # Список языков берётся у lang.py, а не переписывается здесь:
            # два своих списка разошлись бы молча, и записанный язык оказался
            # бы тем, надписей для которого нет.
            if not rest or rest[0] not in _языки():
                return _fail("вызов: progress.py lang <файл> "
                             + "|".join(_языки()))
            data["lang"] = rest[0]
        elif cmd == "phase":
            if len(rest) < 2:
                return _fail("нужны имя фазы и держатель хода: "
                             + ", ".join(OWNERS))
            data = set_phase(data, rest[0], rest[1], _flag(rest, "--detail", ""))
        elif cmd == "stage":
            if len(rest) < 2:
                return _fail("нужны имя этапа и статус")
            data = set_stage(data, rest[0], rest[1], _flag(rest, "--detail", ""))
        elif cmd == "task":
            if len(rest) < 2:
                return _fail("нужны id и имя задачи")
            code = _flag(rest, "--exit-code")
            reqs = _flag(rest, "--requirements")
            zone = _flag(rest, "--zone")
            blk = _flag(rest, "--blocked-by")
            started = _flag(rest, "--started")
            goal = _flag(rest, "--goal")
            acc = _flag(rest, "--acceptance")
            # Критерий с адресом. Флаг повторяемый, поля через `|` — писать
            # JSON в командной строке значит воевать с кавычками оболочки, а
            # эта война уже стоила одного молча испорченного файла.
            крит = _критерии(rest)
            quo = _flag(rest, "--quotes")
            sec = _flag(rest, "--spec-sections")
            hold = _flag(rest, "--holdout")
            # Переход в работу без явной отметки штампуется здесь: пропущенная
            # отметка тише неверной, но обходится дороже — она делает
            # проверку параллельности бессильной, ничего об этом не сказав.
            if started is None and _flag(rest, "--status") == RUNNING:
                from datetime import datetime, timezone
                started = datetime.now(timezone.utc).isoformat(timespec="seconds")
            # None, а не «1»: умолчание «первая волна» превращало обновление
            # существующего таска в его копию (см. set_task). Не передали
            # волну — таск остаётся там, где лежит.
            _w = _flag(rest, "--wave")
            data = set_task(data, rest[0], rest[1],
                            int(_w) if _w is not None else None,
                            _flag(rest, "--status", WAITING),
                            int(code) if code is not None else None,
                            [x.strip() for x in reqs.split(",") if x.strip()]
                            if reqs is not None else None,
                            [x.strip() for x in zone.split(",") if x.strip()]
                            if zone is not None else None,
                            [x.strip() for x in blk.split(",") if x.strip()]
                            if blk is not None else None,
                            started, _flag(rest, "--finished"), goal,
                            # Критерии и цитаты режутся по «;», а не по запятой:
                            # запятая внутри критерия — обычное дело, и разбор
                            # по ней порезал бы одно условие на два бессмысленных.
                            _приёмка(acc, крит),
                            [x.strip() for x in quo.split(";") if x.strip()]
                            if quo is not None else None,
                            [x.strip() for x in sec.split(";") if x.strip()]
                            if sec is not None else None,
                            [x.strip() for x in hold.split(";") if x.strip()]
                            if hold is not None else None)
        elif cmd == "debt":
            if not rest:
                return _fail("нужен вид долга")
            if len(rest) >= 2 and rest[1] == "--none":
                data = review_debt(data, rest[0])
            elif len(rest) < 2:
                return _fail("нужен текст долга либо --none")
            else:
                data = add_debt(data, rest[0], " ".join(rest[1:]))
        elif cmd == "source":
            if not rest:
                return _fail("нужен путь к источнику требований и задач")
            data["source"] = rest[0]
        elif cmd == "req":
            for k in ("total", "covered", "dropped", "deferred", "stubbed"):
                v = _flag(rest, f"--{k}")
                if v is not None:
                    data["requirements"][k] = int(v)
        elif cmd == "show":
            out = {**data, "summary": summary(data)}
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return 0 if out["summary"]["trustworthy"] else 2
        else:
            return _fail(f"неизвестная команда: {cmd}")
    except ValueError as e:
        lock.__exit__()
        return _fail(str(e))

    save(path, data)
    # Снимок вписывается ПОД ТЕМ ЖЕ замком, что и запись состояния: иначе два
    # помощника, пишущие рядом, кладут в страницу разные снимки в обратном
    # порядке, и на панели остаётся более старый.
    снимок_на_панель(path, data)
    lock.__exit__()
    s = summary(data)
    print(json.dumps({**data, "summary": s}, ensure_ascii=False, indent=1))
    # Запись состояния — не место для вердикта о полноте: пока идёт стройка,
    # неполнота нормальна, и код 2 на каждой записи приучил бы его не читать.
    return 0


if __name__ == "__main__":
    sys.exit(main())
