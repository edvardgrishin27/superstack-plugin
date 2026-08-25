#!/usr/bin/env python3
"""SUPERSTACK — журнал.

Зачем это отдельный файл, а не print() россыпью по инструментам.

До этого файла в системе не было НИ ОДНОГО вхождения logging: инструмент падал
или сообщал «готово» — и никакого следа между этими двумя состояниями не
оставалось. Заказчик, разбирающий инцидент постфактум, не может ответить на
вопрос «что вообще делал инструмент до отказа» — а на глаз это неотличимо от
«инструмент ничего не делал».

Здесь запись — ОДНА точка входа (event) и одна дисциплина для секретов
(redact), которую подключают в каждый инструмент двумя вызовами: на входе и
на выходе. Всё остальное намеренно не сделано:

  · журнал НИКОГДА не роняет вызвавший инструмент. Любой отказ записи
    (нет прав, диск полон, каталог не создать) гасится здесь и попадает в
    stderr ОДИН раз за процесс — инструмент, упавший из-за собственного
    журналирования, хуже инструмента без журнала вовсе;
  · секрет никогда не попадает в файл как значение. Поле, чьё имя похоже на
    секретное, и значение, чья ФОРМА похожа на реальный ключ, заменяются на
    место+длину+sha256-отпечаток — та же дисциплина, что в
    tools/probe/collect.py: секрет не сохраняется и не печатается нигде;
  · журнал растущий без предела — это отказ (забьёт диск), а не мелочь:
    файл ротируется по размеру, старый бэкап один и затирается, а не копится;
  · SUPERSTACK_DISABLE=1 и флаг паузы гасят запись так же, как гасят всё
    остальное в системе — молча по СМЫСЛУ (это осознанное выключение, а не
    отказ), но событие просто не пишется, ошибки тут нет;
  · путь журнала — ТОЛЬКО из переменной окружения SUPERSTACK_LOG_DIR (с
    дефолтом в ~/.claude/superstack/logs). Без этого тесты неизбежно писали
    бы в настоящий ~/.claude пользователя — герметичность потребовала бы
    подмены HOME целиком, а не только каталога журнала.

Формат — JSON Lines: одна строка = одно событие (инструмент, действие, итог,
длительность, код возврата), чтобы журнал читал не только человек, но и
скрипт построчно, без разбора многострочного блока.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# где лежит журнал
# --------------------------------------------------------------------------

LOG_DIR_ENV = "SUPERSTACK_LOG_DIR"
LOG_FILE_NAME = "events.jsonl"

# Ротация по размеру. Переопределяема через SUPERSTACK_LOG_MAX_BYTES —
# исключительно для тестов: без этого проверка ротации писала бы мегабайты
# ради одной строки, которая переполняет счётчик.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024


def _log_dir() -> Path:
    """Каталог журнала. Переменная окружения обязательна для герметичности:
    без неё тесты и любой сторонний вызов молча писали бы в настоящий
    ~/.claude пользователя, минуя подставной HOME."""
    raw = os.environ.get(LOG_DIR_ENV)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "superstack" / "logs"


def _max_bytes() -> int:
    raw = os.environ.get("SUPERSTACK_LOG_MAX_BYTES")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except ValueError:
            pass
    return DEFAULT_MAX_BYTES


# --------------------------------------------------------------------------
# выключатели: тот же смысл, что и везде в системе
# --------------------------------------------------------------------------

def _gated() -> bool:
    """True — событие писать не нужно. Это не отказ, а осознанное молчание:
    SUPERSTACK_DISABLE=1 гасит систему целиком, а флаг паузы — до снятия.
    SUPERSTACK_IGNORE_PAUSE=1 использует сама планка при самопроверке, чтобы
    не запираться собственной паузой во время прогона."""
    if os.environ.get("SUPERSTACK_DISABLE") == "1":
        return True
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return False
    return (Path.home() / ".claude" / "superstack" / "PAUSE").exists()


# --------------------------------------------------------------------------
# redact — секрет не попадает в журнал как значение
# --------------------------------------------------------------------------

# Имя поля, само по себе намекающее на секрет. Совпадения по имени слабее
# совпадений по форме — они срабатывают только при значении заметной длины,
# иначе «token_type: bearer» превращалось бы в ложную находку.
_KEY_NAME = re.compile(
    r"(?i)(secret|token|password|passwd|api[_-]?key|credential|private[_-]?key|auth)")

# Форма самого значения — независимо от имени поля. Список короче, чем в
# tools/probe/collect.py: журналу это НЕ полноценный сканер конфигов, а
# последний рубеж перед записью того, что уже сформировал вызывающий код.
_SECRET_SHAPE = re.compile(
    r"sk-[A-Za-z0-9]{10,}"
    r"|sk-ant-[A-Za-z0-9\-]{10,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

_MIN_NAME_MATCH_LEN = 8  # короче — это «pin: 1234», а не ключ


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _redacted_marker(value: str, why: str) -> dict:
    """То, что уходит в файл ВМЕСТО секрета: место (задаёт вызывающий через
    ключ словаря), причина, длина, отпечаток. Само значение — нигде."""
    return {"redacted": True, "why": why, "len": len(value),
            "fingerprint": _fingerprint(value)}


def redact(obj, _key: "str | None" = None):
    """Новый объект с секретами, замененными на отпечаток. Ничего не мутирует
    на месте (obj остаётся как был) — вызывающий код может безопасно
    использовать исходные данные и после вызова.

    Рекурсивно обходит dict/list; строковый лист проверяется и по форме
    значения, и по имени поля, в котором он лежит (для списков — по имени
    родительского ключа, форма всё равно ловит основные случаи)."""
    if isinstance(obj, dict):
        return {k: redact(v, _key=k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, _key=_key) for v in obj]
    if isinstance(obj, str):
        if _SECRET_SHAPE.search(obj):
            return _redacted_marker(obj, "форма значения похожа на ключ")
        if _key and _KEY_NAME.search(_key) and len(obj) >= _MIN_NAME_MATCH_LEN:
            return _redacted_marker(obj, "имя поля указывает на секрет")
        return obj
    return obj


# --------------------------------------------------------------------------
# запись
# --------------------------------------------------------------------------

_warned = False  # предупреждение об отказе печатается один раз за процесс


def _warn_once(err: Exception) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    print(f"[superstack] журнал недоступен, работа продолжается без него: {err}",
          file=sys.stderr)


def _ensure_dir(d: Path) -> None:
    """Каталог журнала с правами 700 — в нём могут лежать отпечатки секретов,
    и даже отпечаток не должен читаться посторонним пользователем машины."""
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        # Права не выставились (например, каталог не наш) — писать всё равно
        # пробуем: это не повод терять событие, а отдельная проблема доступа.
        pass


def _rotate_if_needed(path: Path) -> None:
    """Один бэкап, который каждый раз затирается новым. Не архив истории —
    гарантия постоянного потолка места на диске, что и требовалось: журнал
    без ротации однажды забивает диск, и это отказ, а не мелочь."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size < _max_bytes():
        return
    backup = path.with_name(path.name + ".1")
    if backup.exists():
        backup.unlink()
    path.rename(backup)


def event(tool: str, action: str, outcome: str, *,
          duration_ms: "float | None" = None,
          exit_code: "int | None" = None,
          **fields) -> bool:
    """Записать одно событие. Возвращает True, если строка реально легла на
    диск, False — если событие пропущено (выключатель) или запись не удалась.

    Пять обязательных полей — инструмент, действие, итог, длительность, код
    возврата — плюс любые доп.поля вызывающего кода, каждое пропущенное через
    redact() перед сериализацией. НИКОГДА не бросает исключение: вызывающий
    инструмент не обязан оборачивать вызов в try/except."""
    if _gated():
        return False
    try:
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "tool": str(tool),
            "action": str(action),
            "outcome": str(outcome),
            "duration_ms": duration_ms,
            "exit_code": exit_code,
        }
        if fields:
            record.update(redact(fields))
        line = json.dumps(record, ensure_ascii=False)

        d = _log_dir()
        _ensure_dir(d)
        path = d / LOG_FILE_NAME
        _rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception as e:  # noqa: BLE001 — журнал обязан гаситься целиком, причина неважна
        _warn_once(e)
        return False
