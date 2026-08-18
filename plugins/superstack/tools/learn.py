#!/usr/bin/env python3
"""SUPERSTACK — журнал находок с планкой доказательности.

Сохраняет то, что система узнала, — но НЕ всё подряд.

Почему с планкой. Индекс механизмов формулирует это прямо: самоулучшение без
потолка и без отбора — это не улучшение, а накопление, и оно делает систему
хуже. Доказательство лежало на диске автора: 254 скилла, превышение бюджета
маршрутизации в 5,6 раза, из них реально вызывался 33.

Планка заимствована из self-learning-skills. Три условия, ВСЕ обязательны:
  1. пройденная проверка — тест, прогон или наблюдаемый эффект, не «мне кажется»
  2. названный паттерн отказа — что именно ломалось
  3. минимум один отброшенный тупик — что пробовали и не сработало
Плюс обязательный раздел «что не сработало» и провенанс: откуда факт.
Не прошло все три — уходит в inbox, а не в знание.

Два хранилища, намеренно разделённые:
  общее  — data/knowledge/learned/   едет в репозиторий, версионируется, для всех
  личное — ~/.claude/superstack/learned/   остаётся на машине, не уезжает никуда

Личное не синхронизируется по умолчанию: находка может содержать имена клиентов,
пути и куски кода. Продвижение в общее — только явной командой и после редакции.

Маршрутизация: три адресата, а не один. Без неё триаж ведёт в никуда — всё
подряд оседает в одном плоском журнале, и конвенция про три файла неотличима
от факта про архитектуру, а от инструкции, которую стоило вынести в скилл, тем
более. `route()` решает по заявленному виду знания:
  ФАКТ про проект                   -> память  (журнал находок, как раньше)
  КОНВЕНЦИЯ для конкретных файлов   -> правило (--paths — это и есть заявление
                                        «это про эти файлы», а не про факт)
  ПРОЦЕДУРА, повторённая ≥3 раза    -> скилл   (разовое — ещё не паттерн;
                                        одно и то же наблюдение с первого раза
                                        не отличить от случайности)
Плюс слияние вместо добавления: новая находка сравнивается с уже записанными
по заголовку (не только по точному совпадению четырёх полей планки — то была
бы дословность). Схожесть выше порога (посимвольная, difflib — не понимание
смысла) считается той же находкой в чуть другой формулировке — опечатка,
регистр, число/падеж, одно переставленное слово — и правит существующую
запись, а не плодит вторую с другим id. Полный пересказ другими словами этот
порог не ловит осознанно: посимвольное сравнение не отличит две ПОХОЖИЕ по
словам, но РАЗНЫЕ по теме находки от настоящего пересказа, и слияние двух
разных уроков в один было бы хуже, чем оставленный дубль.

  learn.py add --title … --check … --failure … --deadend … [--scope local|shared]
                [--kind fact|procedure] [--paths a,b,c]
  learn.py list [--scope …] [--since N]
  learn.py promote <id>        личное -> общее, с проверкой на секреты
  learn.py stats
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SHARED = HERE / "data" / "knowledge" / "learned"
LOCAL = Path.home() / ".claude" / "superstack" / "learned"
SCHEMA = "superstack.learned.v1"

# Ёмкость журнала. Потолок существует, чтобы журнал не превратился в свалку:
# при переполнении вытесняется самое старое из НЕподтверждённых повторно.
MAX_ENTRIES = 500

# Сколько раз процедура обязана повториться, прежде чем её вообще стоит
# рассматривать как кандидата в скилл. Меньше — и одно случайное совпадение
# формулировок выглядело бы как «паттерн».
SKILL_CONFIRMATIONS = 3

# Порог схожести заголовков для слияния (0..1, difflib.SequenceMatcher.ratio).
# Ниже 0.8 разные по смыслу находки начинают склеиваться в одну; число не
# выведено математически, это компромисс между «плодит дубли» и «путает
# соседние темы» — при желании подвинуть, тест на инвариант рядом.
MERGE_TITLE_THRESHOLD = 0.84

# Формы токенов ищутся ОТДЕЛЬНО от шаблона «имя=значение». Проба в collect.py
# сверяет значение конфига целиком; здесь текст свободный, и токен обычно
# вставлен в фразу — без якорей ^$ и без имени поля рядом. Раньше этих форм
# тут не было вовсе: голый ghp_… , вставленный в описание находки, проходил
# гейт и уезжал в ОБЩЕЕ хранилище, которое коммитится в репозиторий.
SECRET_PATTERNS = [
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "токен GitHub"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "токен GitHub (fine-grained)"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"), "ключ вида sk-"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "токен Slack"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "ключ AWS"),
    # JWT знал сборщик фактов и не знал журнал. Два независимых списка форм
    # расходятся ровно так же, как два независимых списка мест.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "JWT"),
    (re.compile(r"sshpass\s+-p\s+'[^']+'"), "пароль в командной строке"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S{8,}"), "похоже на секрет"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "приватный ключ"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "адрес почты"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "IP-адрес"),
]


def store(scope: str) -> Path:
    return SHARED if scope == "shared" else LOCAL


def version() -> str:
    """Версия находки = дата + короткий хеш HEAD, если репозиторий есть."""
    day = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=HERE, capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return f"{day}+{r.stdout.strip()}"
    except Exception:
        pass
    return day


def scan_secrets(text: str) -> list[str]:
    return [why for rx, why in SECRET_PATTERNS if rx.search(text)]


def load_all(scope: str) -> list[dict]:
    d = store(scope)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def route(kind: str, paths: list[str] | None, confirmations: int) -> str:
    """Куда едет знание: "memory" | "rule" | "skill". Три адресата, а не один.

    Порядок проверок фиксирован и это не случайность:
      1. paths — явное заявление автора «это конвенция для этих файлов»,
         оно сильнее счётчика повторений: конвенцию не обязательно видеть
         трижды, чтобы записать её как конвенцию.
      2. kind == "procedure" при confirmations >= SKILL_CONFIRMATIONS —
         процедура, которая повторилась достаточно раз, чтобы не быть
         случайностью одной сессии.
      3. иначе — память: одиночный факт про проект, ни то ни другое.

    Без маршрутизации триаж ведёт в никуда: факт, конвенция и процедура
    неотличимы друг от друга в одном плоском журнале находок.
    """
    if paths:
        return "rule"
    if kind == "procedure" and confirmations >= SKILL_CONFIRMATIONS:
        return "skill"
    return "memory"


def _norm_title(title: str) -> str:
    """Нормализация заголовка перед сравнением схожести: регистр и лишние
    пробелы смыслом не являются. Синонимы не трогаем — это уже не форма,
    а значение, и его различать риском ложных слияний не стоит."""
    return " ".join(str(title).lower().split())


def _same_evidence(entry: dict, a) -> bool:
    """Совпадает ли ДОКАЗАТЕЛЬНАЯ часть находки, а не только её название.

    Планка находки — четыре поля, и id хеширует все четыре. Две записи могут
    называться одинаково и быть разными: тот же симптом, другая пройденная
    проверка, другой отброшенный тупик. Слить их по одному заголовку значит
    выбросить чужие проверку и тупик, накрутив счётчик подтверждений на
    потерянных данных. Такой отказ здесь уже случался и назван по имени;
    возвращать его нельзя.
    """
    gate = entry.get("gate") or {}
    return (_norm_title(gate.get("check", "")) == _norm_title(a.check)
            and _norm_title(gate.get("failure_pattern", "")) == _norm_title(a.failure)
            and _norm_title(gate.get("dead_end", "")) == _norm_title(a.deadend))


def find_similar_entry(entries: list[dict], title: str,
                        threshold: float = MERGE_TITLE_THRESHOLD) -> dict | None:
    """Найти в списке запись, чей заголовок похож на новый ВЫШЕ порога.

    Существующее слияние (см. cmd_add) ловит только ДОСЛОВНЫЙ повтор: id
    считается по точным строкам title/check/failure/dead_end, и другая
    формулировка того же урока — «Клавиатура перекрывает поле ввода в
    чатах» вместо «клавиатура перекрывает поле ввода в чате» (падеж, точка,
    регистр) — получает другой id и плодит вторую запись. difflib —
    посимвольное сравнение без внешних зависимостей, не понимание смысла:
    оно ловит опечатку и мелкую правку формулировки, но не полный пересказ
    другими словами — порог (см. MERGE_TITLE_THRESHOLD) подобран так, чтобы
    не путать разные темы с похожими словами.
    """
    target = _norm_title(title)
    best, best_ratio = None, 0.0
    for e in entries:
        ratio = difflib.SequenceMatcher(None, target, _norm_title(e.get("title", ""))).ratio()
        if ratio > best_ratio:
            best, best_ratio = e, ratio
    if best is not None and best_ratio >= threshold:
        return best
    return None


def cmd_add(a) -> int:
    """Записать находку. Все три условия планки обязательны."""
    missing = [name for name, val in
               (("--check (пройденная проверка)", a.check),
                ("--failure (паттерн отказа)", a.failure),
                ("--deadend (отброшенный тупик)", a.deadend))
               if not val or not val.strip()]
    if missing:
        print("ОТКЛОНЕНО планкой доказательности. Не хватает:")
        for m in missing:
            print(f"  · {m}")
        print("\nЭто не бюрократия. Факт без проверки — догадка; без названного отказа —")
        print("непонятно, что чинили; без отброшенного тупика — непонятно, почему так,")
        print("а не иначе. Через месяц такая запись бесполезна.")
        print("\nЕсли доказательств пока нет — это ещё не находка, а наблюдение.")
        print("Держи его в заметках сессии и вернись, когда появится проверка.")
        return 2

    # Сканируется ВСЯ запись целиком, а не список полей: прежняя версия
    # перечисляла поля вручную и забыла --source и --tags. Секрет, вписанный
    # в источник, уезжал в ОБЩЕЕ хранилище — то, что коммитится в репозиторий —
    # с отметкой «секретов нет». Перечисление полей руками рано или поздно
    # отстаёт от структуры; сериализация — нет.
    # Прямая запись в ОБЩЕЕ хранилище запрещена. Гейт продвижения требует
    # минимум двух подтверждений — но рядом была вторая, негейтированная
    # дверь: «add --scope shared» писал туда же с confirmations=1. Правило
    # отбора, которое обходится одной командой, — не правило.
    if a.scope == "shared":
        print("ОТКЛОНЕНО: писать напрямую в общее хранилище нельзя.")
        print("  Находка входит туда только через подтверждение:")
        print("    1) learn.py add --scope local …")
        print("    2) встретил повторно — add ещё раз, счётчик вырастет")
        print("    3) learn.py promote <id>")
        print("  Иначе в общее уедет частный случай одной машины.")
        return 5

    paths_list = [p.strip() for p in (a.paths or "").split(",") if p.strip()]

    body = "\n".join(filter(None, [
        a.title, a.check, a.failure, a.deadend, a.notes or "",
        a.source or "", a.tags or "", a.paths or "",
    ]))
    leaks = scan_secrets(body)
    if leaks and a.scope == "shared":
        print("ОТКЛОНЕНО: в общее хранилище нельзя, найдено — " + ", ".join(sorted(set(leaks))))
        print("Запиши в личное (--scope local) или отредактируй текст.")
        return 3

    entry = {
        "schema": SCHEMA,
        "version": version(),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": a.scope,
        "title": a.title,
        "kind": a.kind,
        "paths": paths_list,
        "gate": {
            "check": a.check,          # чем подтверждено
            "failure_pattern": a.failure,  # что именно ломалось
            "dead_end": a.deadend,     # что пробовали и не сработало
        },
        "what_did_not_work": a.deadend,
        "provenance": a.source or "сессия без указания источника",
        "tags": [t.strip() for t in (a.tags or "").split(",") if t.strip()],
        "notes": a.notes or "",
        "confirmations": 1,
        "secrets_flagged": sorted(set(leaks)),
    }
    # Идентификатор считается по ВСЕМ полям планки, а не по двум. Раньше две
    # разные находки с совпавшими заголовком и паттерном отказа схлопывались:
    # вторая не записывалась вовсе, её содержимое терялось, а системе
    # печаталось «ПОДТВЕРЖДЕНО повторно» — счётчик подтверждений накручивался
    # на потере данных. Подтверждение обязано означать ту же находку, а не
    # похожее название.
    entry["id"] = hashlib.sha256("\x00".join([
        entry["title"],
        entry["gate"]["failure_pattern"],
        entry["gate"]["check"],
        entry["gate"]["dead_end"],
    ]).encode("utf-8")).hexdigest()[:12]

    d = store(a.scope)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{entry['id']}.json"
    entries = load_all(a.scope)

    if path.exists():
        # Повторная встреча — не дубль, а подтверждение. Это сильнее новой записи.
        old = json.loads(path.read_text(encoding="utf-8"))
        old["confirmations"] = old.get("confirmations", 1) + 1
        old["last_seen"] = entry["created"]
        old["destination"] = route(a.kind, paths_list, old["confirmations"])
        write_atomic(path, old)
        print(f"ПОДТВЕРЖДЕНО повторно ({old['confirmations']}×): {old['title']}")
        print(f"  {path}")
        print("  Повторная встреча — сильнейший сигнал, что находка настоящая.")
        _announce_route(old["destination"], paths_list)
        return 0

    # Слияние по схожести заголовка — ОТДЕЛЬНЫЙ шаг от точного совпадения id
    # выше. Точный id ловит дословный повтор; этот шаг — перефразированный:
    # без него вторая формулировка того же урока получает другой id и
    # записывается как новая находка, хотя по сути это подтверждение первой.
    # НО схожести ЗАГОЛОВКА для слияния недостаточно. Планка находки состоит из
    # четырёх полей — заголовок, пройденная проверка, паттерн отказа, отброшенный
    # тупик, — и id хеширует все четыре именно потому, что две находки могут
    # называться одинаково и при этом быть разными: тот же симптом, другая
    # проверка, другой тупик. Слияние по одному заголовку выбрасывало бы чужие
    # проверку и тупик, накручивая счётчик подтверждений НА ПОТЕРЕ ДАННЫХ, —
    # именно этот отказ уже случался здесь раньше и был назван по имени.
    # Поэтому сливаем, только когда доказательная часть совпадает, а расходится
    # одна формулировка.
    similar = find_similar_entry(entries, a.title)
    if similar is not None and not _same_evidence(similar, a):
        similar = None
    if similar is not None:
        similar_path = entry_path(d, similar["id"])
        similar["confirmations"] = similar.get("confirmations", 1) + 1
        similar["last_seen"] = entry["created"]
        similar["destination"] = route(a.kind, paths_list, similar["confirmations"])
        write_atomic(similar_path, similar)
        print(f"СОЧТЕНО ПОВТОРОМ по схожести заголовка ({similar['confirmations']}×): "
              f"«{similar['title']}»")
        print(f"  новая формулировка: «{a.title}»")
        print(f"  {similar_path}")
        print("  Другая формулировка той же находки правит существующую запись, "
              "а не плодит вторую.")
        _announce_route(similar["destination"], paths_list)
        return 0

    if len(entries) >= MAX_ENTRIES:
        weakest = min(entries, key=lambda e: (e.get("confirmations", 1), e.get("created", "")))
        print(f"⚠ Потолок журнала ({MAX_ENTRIES}). Слабейшая запись:")
        print(f"  «{weakest['title'][:60]}» подтверждений {weakest.get('confirmations',1)}")
        print("  Новое входит, только вытесняя старое. Удали её или подними потолок.")
        return 4

    entry["destination"] = route(a.kind, paths_list, entry["confirmations"])
    write_atomic(path, entry)
    if leaks:
        print("⚠ помечено как возможно чувствительное: " + ", ".join(sorted(set(leaks))))
    print(f"ЗАПИСАНО [{entry['scope']}] {entry['id']}  версия {entry['version']}")
    print(f"  {path}")
    _announce_route(entry["destination"], paths_list)
    return 0


def _announce_route(destination: str, paths_list: list[str]) -> None:
    """Сказать вслух, куда по мнению route() едет находка — когда это не
    журнал по умолчанию. Инструмент не пишет rules/*.md или skills/*/SKILL.md
    сам: перенос туда — решение человека, а не автоправка. Молчание здесь
    было бы хуже неудобства: находка осталась бы в журнале навсегда, и никто
    не узнал бы, что она годится для правила или скилла."""
    if destination == "memory":
        return
    label = {
        "rule": f"ПРАВИЛО (конвенция для путей: {', '.join(paths_list) or '—'})",
        "skill": "СКИЛЛ (процедура, повторившаяся ≥3 раза — кандидат на выделение)",
    }.get(destination, destination)
    print(f"МАРШРУТ: {label}")
    print("  запись сохранена в журнал находок с меткой destination; "
          "перенос в rules/ или skills/ — вручную, этот инструмент файл сам не пишет")


def cmd_list(a) -> int:
    entries = load_all(a.scope)
    entries.sort(key=lambda e: (-e.get("confirmations", 1), e.get("created", "")))
    if not entries:
        print(f"[{a.scope}] пусто")
        return 0
    print(f"[{a.scope}] записей: {len(entries)}\n")
    for e in entries[: a.limit]:
        # Одна запись без полей роняла ВЕСЬ список: журнал становился
        # нечитаем целиком из-за одного повреждённого файла. Запись, которую
        # не удалось разобрать, обязана быть видна как повреждённая, а не
        # утаскивать за собой остальные.
        mark = "!" if e.get("secrets_flagged") else " "
        eid = e.get("id", "<без id>")
        title = str(e.get("title") or "<без заголовка>")[:56]
        version = e.get("version", "?")
        print(f"{mark} {eid}  ×{e.get('confirmations', 1)}  {version}  {title}")
        gate = e.get("gate") or {}
        failure = str(gate.get("failure_pattern") or "<не указан>")[:70]
        print(f"      отказ: {failure}")
        missing = [k for k in ("version", "gate", "title") if k not in e]
        if missing:
            print(f"      ⚠ запись неполна, нет полей: {', '.join(missing)}")
    return 0


def write_atomic(path: Path, data: dict) -> None:
    """Запись через временный файл и переименование.

    Прямая запись поверх существующего файла оставляет окно, в котором на
    диске лежит обрезанный JSON: прерывание в этот момент уничтожает запись
    целиком. Для журнала находок это потеря именно того, ради чего он есть.
    Переименование внутри одного каталога атомарно.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def entry_path(store_dir: Path, entry_id: str) -> Path:
    """Путь к записи с проверкой, что он не уводит из хранилища.

    id приходит из аргумента командной строки. Без проверки «../../» читает
    и пишет произвольный JSON вне хранилища — продвижение находки становится
    произвольной записью файла.
    """
    if not re.fullmatch(r"[0-9a-f]{6,64}", entry_id or ""):
        raise ValueError(f"недопустимый идентификатор записи: {entry_id!r}")
    return store_dir / f"{entry_id}.json"


def cmd_promote(a) -> int:
    """Личное -> общее. Только после проверки на чувствительное."""
    try:
        src = entry_path(LOCAL, a.id)
    except ValueError as e:
        print(f"ОТКАЗ: {e}")
        return 2
    if not src.exists():
        print(f"нет такой записи в личном: {a.id}")
        return 1
    e = json.loads(src.read_text(encoding="utf-8"))
    leaks = scan_secrets(json.dumps(e, ensure_ascii=False))
    if leaks:
        print("НЕЛЬЗЯ продвигать: найдено — " + ", ".join(sorted(set(leaks))))
        print("Отредактируй запись, потом повтори.")
        return 3
    if e.get("confirmations", 1) < 2:
        print("НЕЛЬЗЯ продвигать: находка встречалась один раз.")
        print("В общее хранилище идёт то, что подтвердилось минимум дважды —")
        print("иначе туда уедет частный случай одной машины.")
        return 4
    SHARED.mkdir(parents=True, exist_ok=True)
    e["scope"] = "shared"
    e["promoted"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_atomic(entry_path(SHARED, a.id), e)
    print(f"продвинуто в общее: {e['title']}")
    return 0


def cmd_stats(_a) -> int:
    for scope in ("local", "shared"):
        e = load_all(scope)
        conf = sum(1 for x in e if x.get("confirmations", 1) >= 2)
        flagged = sum(1 for x in e if x.get("secrets_flagged"))
        print(f"[{scope:<6}] записей {len(e):>4}  подтверждено ≥2× {conf:>4}  "
              f"с пометкой о чувствительном {flagged:>3}")
    print(f"\nпотолок журнала: {MAX_ENTRIES} записей на хранилище")
    print(f"личное:  {LOCAL}")
    print(f"общее:   {SHARED}")
    return 0


def halt_if_paused() -> None:
    """Тормоз обязан быть общим. Прежде его соблюдали collect, adjudicate,
    doctor и линтер — но НЕ этот инструмент, хотя докстринги трёх других
    утверждали «каждый инструмент проверяет флаг первым действием».
    Для learn.py это особенно тяжело: он единственный ПИШЕТ на диск."""
    import os
    from pathlib import Path as _P
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    flag = _P.home() / ".claude" / "superstack" / "PAUSE"
    if flag.exists():
        try:
            since = flag.read_text(encoding="utf-8").strip()
        except Exception:
            since = "?"
        import sys as _s
        print(f"ОСТАНОВЛЕНО: система на паузе с {since}\n"
              f"  флаг: {flag}\n  снять: tools/pause.sh off", file=_s.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    p = argparse.ArgumentParser(description="журнал находок с планкой доказательности")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="записать находку")
    a.add_argument("--title", required=True)
    a.add_argument("--check", help="чем подтверждено: тест, прогон, наблюдаемый эффект")
    a.add_argument("--failure", help="что именно ломалось — паттерн отказа")
    a.add_argument("--deadend", help="что пробовали и не сработало")
    a.add_argument("--source", help="откуда: сессия, файл, коммит")
    a.add_argument("--tags")
    a.add_argument("--notes")
    a.add_argument("--scope", choices=["local", "shared"], default="local")
    a.add_argument("--kind", choices=["fact", "procedure"], default="fact",
                    help="ФАКТ про проект или ПРОЦЕДУРА — влияет на маршрут (route)")
    a.add_argument("--paths",
                    help="файлы/маски через запятую — заявление «это конвенция "
                         "для этих файлов», маршрутизирует находку в правило")
    a.set_defaults(fn=cmd_add)

    l = sub.add_parser("list")
    l.add_argument("--scope", choices=["local", "shared"], default="local")
    l.add_argument("--limit", type=int, default=30)
    l.set_defaults(fn=cmd_list)

    pr = sub.add_parser("promote", help="личное -> общее")
    pr.add_argument("id")
    pr.set_defaults(fn=cmd_promote)

    st = sub.add_parser("stats")
    st.set_defaults(fn=cmd_stats)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
