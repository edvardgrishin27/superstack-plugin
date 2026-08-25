#!/usr/bin/env python3
"""SUPERSTACK — страховка памяти перед сжатием контекста.

Компакция необратима: то, что не сохранено до неё, не восстановит никто. Хук
кладёт рядом копию транскрипта и строку метаданных в журнал — этого достаточно,
чтобы вернуться к разговору, которого в контексте больше нет.

ЧЕГО ХУК НЕ ДЕЛАЕТ:
  · не читает содержимое разговора и не печатает его никуда — копия остаётся
    в каталоге состояния, за границу доверия ничего не выносится;
  · не блокирует компакцию и не может её замедлить: хук, который думает,
    превращает сжатие контекста в зависание;
  · ВСЕГДА выходит кодом 0. PreCompact, уронивший компакцию, хуже
    отсутствующего: человек потерял бы не только память, но и саму компакцию.

Почему на Python. В прежней версии стояло: «ни python3, ни jq не предполагаются
установленными в момент компакции — разбор JSON держится на sed по плоской
строке», и рядом честно названо, чего этот разбор НЕ УМЕЕТ: экранированные
кавычки и обратные слэши внутри значения сбивают извлечение. Порт убирает и
зависимость от оболочки, и это ограничение разом: `json.loads` разбирает то,
что пришло, а не то, что похоже на JSON.

Гейт области намеренно повторён в каждом хуке, а не вынесен в общий модуль:
хук не должен зависеть от соседнего файла в момент, когда от него зависит
сохранность памяти.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

#: Сколько последних снимков держим. Без потолка каталог растёт вечно: у
#: активного человека компакции идут постоянно, и год работы оставил бы сотни
#: забытых копий транскриптов.
СНИМКОВ = 20

#: Сколько строк держим в журнале — тот же довод, только для журнала: он растёт
#: по строке на каждую компакцию бесконечно.
СТРОК_ЖУРНАЛА = 500


def _число(имя: str, по_умолчанию: int) -> int:
    v = os.environ.get(имя, "")
    return int(v) if v.isdigit() else по_умолчанию


def состояние() -> Path:
    return Path(os.environ.get("SUPERSTACK_STATE_DIR")
                or Path.home() / ".claude" / "superstack")


def проект() -> Path:
    p = (os.environ.get("SUPERSTACK_PROJECT_DIR")
         or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    try:
        # Симлинки разрешаются ОБЯЗАТЕЛЬНО: отметку пишет `enable.py` уже
        # разрешённой, а сюда путь приходит как есть. На macOS `/var` — симлинк
        # на `/private/var`, и одного этого хватает, чтобы строки не совпали:
        # отметка стоит, гейт её не видит, и хук молчит. Молча.
        return Path(p).resolve()
    except OSError:
        return Path(p)


def позвали_здесь(корень: Path) -> bool:
    """SUPERSTACK работает ТОЛЬКО там, где его позвали.

    Плагины ставятся глобально, хуки объявлены без привязки к проекту — без
    этого гейта хук срабатывает в КАЖДОМ проекте на машине и сохраняет чужие
    транскрипты.
    """
    список = состояние() / "projects"
    if not список.is_file():
        return False
    try:
        строки = список.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return False
    свой = str(корень).rstrip("/") + "/"
    for строка in строки:
        строка = строка.strip()
        if not строка:
            continue
        if свой.startswith(строка.rstrip("/") + "/"):
            return True
        try:
            реальный = str(Path(строка).resolve()).rstrip("/") + "/"
        except OSError:
            continue
        if свой.startswith(реальный):
            return True
    return False


def поля(сырое: str) -> dict:
    """Разбор входа. Сначала как JSON — он им и является.

    Прежний разбор на `sed` спотыкался об экранированную кавычку внутри
    значения, и это было записано в скрипте как осознанный пропуск. Здесь
    пропуска нет: разбирается то, что пришло. Запасной путь на регулярках
    остаётся ровно для случая «пришло не то» — чтобы хук не онемел.
    """
    try:
        d = json.loads(сырое)
        if isinstance(d, dict):
            return {k: str(v) for k, v in d.items() if isinstance(v, (str, int))}
    except ValueError:
        pass
    плоское = сырое.replace("\n", " ")
    из_регулярок = {}
    for имя in ("session_id", "transcript_path", "trigger"):
        m = re.search(r'"%s"\s*:\s*"([^"]*)"' % имя, плоское)
        if m:
            из_регулярок[имя] = m.group(1)
    return из_регулярок


def безопасное_имя(session_id: str) -> str:
    """Имя файла собирается из значения, пришедшего СНАРУЖИ.

    Без очистки `../../etc/passwd` вывел бы запись за пределы каталога
    состояния. Оставляем буквы, цифры, дефис и подчёркивание.
    """
    чистое = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")
    return чистое or "unknown"


def сохранить(транскрипт: str, куда: Path) -> str:
    if not транскрипт:
        return "no-transcript"
    src = Path(транскрипт)
    if not (src.is_file() and os.access(src, os.R_OK)):
        return "no-transcript"
    # Временный файл и переименование: копия, оборванная посреди записи (диск
    # кончился, процесс убили), не должна лечь на место прежнего валидного
    # снимка и выглядеть целой.
    tmp = куда.with_suffix(куда.suffix + f".tmp.{os.getpid()}")
    try:
        shutil.copyfile(src, tmp)
    except OSError:
        tmp.unlink(missing_ok=True)
        return "copy-failed"
    try:
        os.replace(tmp, куда)
    except OSError:
        tmp.unlink(missing_ok=True)
        return "write-failed"
    return "saved"


def подрезать_журнал(журнал: Path, сколько: int) -> None:
    if not журнал.is_file():
        return
    try:
        строки = журнал.read_text("utf-8", errors="replace").splitlines(True)
    except OSError:
        return
    if len(строки) <= сколько:
        return
    tmp = журнал.with_suffix(f".tmp.{os.getpid()}")
    try:
        tmp.write_text("".join(строки[-сколько:]), encoding="utf-8")
        os.replace(tmp, журнал)
    except OSError:
        tmp.unlink(missing_ok=True)


def вычистить_старые(каталог: Path, сколько: int) -> None:
    """Остаются последние по времени изменения. Остальные — прошлые сессии,
    восстанавливать которые всё равно уже некому."""
    try:
        снимки = sorted(каталог.glob("*.jsonl"),
                        key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return
    for старый in снимки[сколько:]:
        try:
            старый.unlink()
        except OSError:
            pass


def main() -> int:
    # Аварийный выключатель — до stdin, до диска, до всего.
    if os.environ.get("SUPERSTACK_DISABLE") == "1":
        return 0
    корень = проект()
    if not позвали_здесь(корень):
        return 0

    # stdin читаем, только если это не терминал: при ручном запуске без
    # перенаправления чтение повисло бы навсегда, выглядя как зависший хук.
    сырое = "" if sys.stdin.isatty() else sys.stdin.read()
    поле = поля(сырое)
    сессия = безопасное_имя(поле.get("session_id", ""))
    повод = поле.get("trigger") or "unknown"

    каталог = состояние() / "precompact"
    try:
        каталог.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Каталог недоступен для записи — молчим и выходим нулём. Уронить
        # компакцию из-за диска, которым мы не управляем, хуже всего.
        return 0

    итог = сохранить(поле.get("transcript_path", ""), каталог / f"{сессия}.jsonl")
    когда = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Журнал — ТОЛЬКО метаданные, без содержимого разговора: сам разговор уже
    # лежит рядом копией, и дублировать его текст значит держать один и тот же
    # секрет в двух местах вместо одного, без всякой пользы.
    журнал = каталог / "log"
    try:
        with журнал.open("a", encoding="utf-8") as f:
            f.write(f"{когда} {сессия} {повод} {итог}\n")
    except OSError:
        pass

    подрезать_журнал(журнал, _число("SUPERSTACK_PRECOMPACT_LOG_KEEP",
                                    СТРОК_ЖУРНАЛА))
    вычистить_старые(каталог, _число("SUPERSTACK_PRECOMPACT_KEEP", СНИМКОВ))
    return 0


if __name__ == "__main__":
    # Хук ВСЕГДА выходит нулём: любая наша ошибка дешевле уроненной компакции.
    try:
        sys.exit(main())
    except Exception:                                        # noqa: BLE001
        sys.exit(0)
