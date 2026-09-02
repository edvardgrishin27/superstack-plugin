#!/usr/bin/env python3
"""SUPERSTACK — /oops. Человеческое описание отката, ДО того как он случится.

Зачем это отдельный инструмент, а не прямой вызов apply.py.

tools/apply.py уже умеет откатывать (`undo`) и перечислять доступное
(`list`) — это переиспользуется, а не переписывается заново. Чего apply.py
не делает: не говорит человеку простыми словами, ЧТО ИМЕННО вернётся. Его
`list` печатает техническое «изменений: 1  2026-08-12T…», а `undo`
восстанавливает файл целиком без пересказа — то есть подтверждение
«согласен откатить» на практике означает «согласен на что-то непонятное».

Порядок в /oops обязан быть: сначала `what` (только чтение, ничего не
меняет), человек соглашается, потом `undo` (переиспользует apply.cmd_undo
буква в букву). Развернуть его нельзя — describe() ничего не пишет на диск.

Секреты: `what` называет, ЧТО не восстановится, а не какое было значение —
apply.make_backup уже вырезал его до этого инструмента, здесь просто
не потерять сам факт вырезания при пересказе человеку.

  python3 oops.py what [--json] [<id-отката>]   что вернёт откат
  python3 oops.py undo [--json] [<id-отката>]   собственно откатить
  python3 oops.py list [--json]                 какие откаты есть

  без <id-отката> берётся последний. код 0 — есть что показать/откатили,
  1 — откатов нет либо откат не состоялся, 3 — ошибка вызова,
  10 — система на паузе
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Соседние инструменты лежат в этом же каталоге, но при импорте из теста
# (spec_from_file_location) каталог скрипта в sys.path не попадает — тогда
# «import verify» упал бы там, где при обычном запуске работает.
sys.path.insert(0, str(Path(__file__).resolve().parent))


import apply  # noqa: E402  — бэкапы/undo уже реализованы там, здесь не дублируются


# --------------------------------------------------------------------------
# поиск последнего отката
# --------------------------------------------------------------------------
def last_backup_id() -> str | None:
    """Самый свежий откат по имени каталога (это временная метка, сортировка
    строкой корректна: формат `apply.stamp()` фиксирован и лексикографичен)."""
    if not apply.BACKUPS.is_dir():
        return None
    dirs = [d for d in apply.BACKUPS.iterdir()
            if d.is_dir() and (d / "manifest.json").is_file()]
    if not dirs:
        return None
    return sorted(dirs, key=lambda d: d.name, reverse=True)[0].name


# --------------------------------------------------------------------------
# человеческое описание — ДО отката
# --------------------------------------------------------------------------
def describe(backup_id: str) -> dict:
    """Что именно вернёт `undo <backup_id>` — читается, ничего не меняет."""
    bdir = apply.BACKUPS / backup_id
    manifest_path = bdir / "manifest.json"
    if not manifest_path.is_file():
        return {"found": False, "id": backup_id,
                "line": f"отката «{backup_id}» не нашлось — нечего вернуть."}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"found": False, "id": backup_id,
                "line": f"запись отката «{backup_id}» не читается: {e}."}
    if not isinstance(manifest, dict):
        # Валидный JSON — ещё не запись отката: `[]`, `42`, `"текст"`, `null`
        # разбираются БЕЗ исключения, и except выше их не ловит по сути дела —
        # это не ошибка чтения и не ошибка разбора, а не та форма данных
        # (расширять его под AttributeError значило бы записать ошибку формы
        # в ошибки чтения). Без этой проверки следующая же строка
        # manifest.get(...) роняла AttributeError голым traceback'ом — и в
        # `oops.py what`, и в `oops.py undo`: describe() зовут обе ветки.
        return {"found": False, "id": backup_id,
                "line": f"запись отката «{backup_id}» повреждена — внутри "
                        f"не запись отката, а {type(manifest).__name__}."}

    applied = manifest.get("applied", [])
    changed = [a for a in applied if a.get("changed")]
    redacted = manifest.get("backup", {}).get("redacted", [])

    changes = []
    for a in changed:
        field_name = a.get("field") or a.get("action") or "?"
        before = a.get("before")
        text = (f"«{field_name}» вернётся к значению «{before}»" if before is not None
                else f"«{field_name}» вернётся к отсутствию значения")
        changes.append({"field": field_name, "restores_to": before, "text": text})

    blocked_secrets = [r["file"] for r in redacted]

    if not changes:
        line = "этот откат ничего не менял — восстанавливать нечего."
    else:
        line = "; ".join(c["text"] for c in changes) + "."
    if blocked_secrets:
        # Секрет НИКОГДА не печатается — ни здесь, ни где-либо ещё: только
        # факт, что именно в этих файлах откат его не тронет.
        line += (" Секреты в " + ", ".join(blocked_secrets) +
                 " откат НЕ восстановит — они не хранятся даже в копии.")

    return {"found": True, "id": backup_id, "created": manifest.get("created"),
            "changes": changes, "changed_count": len(changes),
            "blocked_secrets": blocked_secrets, "line": line}


# --------------------------------------------------------------------------
# собственно откат
# --------------------------------------------------------------------------
def undo(backup_id: str) -> int:
    """Тонкая обёртка над apply.cmd_undo — с тем же тормозом на паузе.

    apply.cmd_undo() САМА по себе паузу не проверяет — её проверяет только
    apply.main() перед тем, как выбрать подкоманду. Вызов cmd_undo() в обход
    main() (как делает этот инструмент) без явной проверки здесь пропускал бы
    паузу молча: откат прошёл бы, пока человек попросил систему остановиться.
    """
    apply.halt_if_paused()
    return apply.cmd_undo([backup_id])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _resolve_id(rest: list) -> tuple:
    """id из аргумента или последний доступный. Второе — причина отсутствия."""
    if rest:
        return rest[0], None
    found = last_backup_id()
    if found is None:
        return None, "откатов пока нет — менять нечего"
    return found, None


#: Куда посмотреть, когда откат не состоялся. Само по себе «не откатил»
#: оставляет человека там же, где раньше оставляло враньё «откатил»: без
#: следующего шага.
_UNDO_FAILED_HINT = "Проверь список откатов: python3 oops.py list."

#: Что ЛОВИМ вокруг вызова undo() ниже — поимённо, а не Exception целиком.
#: apply.cmd_undo() читает manifest.json ЗАНОВО своим json.loads() —
#: describe() выше его не подменяет и не защищает — и не проверяет, что
#: распарсенное имеет форму словаря с ключами "backup"/"files". Ровно эти
#: четыре типа она и бросает на битых входных данных (apply.py, cmd_undo,
#: ~строка 519):
#:   OSError    — файла нет, нет прав, диск отвалился (FileNotFoundError,
#:                PermissionError — его подклассы);
#:   ValueError — JSON битый (json.JSONDecodeError — подкласс) или файл не
#:                в utf-8 (UnicodeDecodeError — тоже подкласс ValueError);
#:   KeyError   — словарь разобрался, но без ключа "backup" или "files";
#:   TypeError  — то, что лежит в manifest.json, — не словарь вовсе.
#: Exception целиком здесь нарочно не ловится: широкий except вокруг чужого
#: модуля в этом проекте уже маскировал однажды настоящую поломку под «всё
#: чисто» — здесь ловится поимённо, и что случилось — говорится вслух.
_UNDO_PARSE_ERRORS = (OSError, ValueError, KeyError, TypeError)


def main() -> int:
    args = sys.argv[1:]
    quiet = "--json" in args
    rest = [a for a in args if a != "--json"]
    if not rest or rest[0] not in ("what", "undo", "list"):
        print("вызов: oops.py [--json] what|undo|list [<id-отката>]",
              file=sys.stderr)
        return 3
    cmd, rest = rest[0], rest[1:]
    if len(rest) > 1:
        print("вызов: oops.py [--json] what|undo|list [<id-отката>]",
              file=sys.stderr)
        return 3

    if cmd == "list":
        apply.halt_if_paused()
        return apply.cmd_list([])

    backup_id, why_not = _resolve_id(rest)
    if backup_id is None:
        result = {"found": False, "id": None, "line": why_not}
        if not quiet:
            print(result["line"], file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 1

    if cmd == "what":
        result = describe(backup_id)
        if not quiet:
            print(result["line"], file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result["found"] else 1

    # cmd == "undo"
    preview = describe(backup_id)
    if not preview["found"]:
        # apply.cmd_undo() не знает того, что уже знает describe(): свой
        # bdir.is_dir() она бы прошла (каталог есть), а на manifest.json —
        # битом или отсутствующем — упала бы голым traceback'ом, потому что
        # такой защиты у неё нет (apply.py, cmd_undo, ~строка 519). Раз
        # здесь уже известно, что возвращать нечего, — не зовём её вовсе:
        # спрашивать инструмент без защиты о том, что мы и так знаем, —
        # риск без пользы. Тормоз на паузе всё равно нужен явно: раньше он
        # срабатывал внутри undo(), а теперь она в этой ветке не вызывается.
        apply.halt_if_paused()
        result = {"id": backup_id, "preview": preview, "undo_exit": 1}
        if not quiet:
            print(f"ОТКАЗ: откат «{backup_id}» не сделан, ничего не менял "
                  f"— {preview['line']} {_UNDO_FAILED_HINT}", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 1

    try:
        rc = undo(backup_id)
        crash = None
    except _UNDO_PARSE_ERRORS as e:
        # preview["found"] был True — manifest.json разобрался в словарь с
        # "applied". Но apply.cmd_undo() ждёт от того же файла ещё и
        # "backup"/"files" и заново читает с диска каждый файл бэкапа — не
        # совпала форма или потерялся отдельный файл копии, и падает ИМЕННО
        # здесь, а не в describe(). Цикл внутри копирует файлы по одному, так
        # что часть могла обновиться до падения, а часть нет — ниже это не
        # выдаётся за «ничего не тронуто», выдаётся только сам факт ошибки.
        rc, crash = 1, str(e)

    result = {"id": backup_id, "preview": preview, "undo_exit": rc}
    if not quiet:
        if rc == 0:
            print(f"откатил «{backup_id}»: {preview['line']}", file=sys.stderr)
        elif rc == apply.UNDO_NOTHING_RESTORED:
            # Отдельная ветка, а не общий отказ ниже: здесь причина известна
            # ТОЧНО (её знает apply.py и назвала кодом), и человеку нужен не
            # «код apply.py: 5», а что именно не вернулось и что теперь делать
            # руками. Настройки при этом не тронуты — этот код означает ровно
            # «ни одного файла не вернули», и обещать «проверь, что уже
            # обновилось» тут не нужно.
            print(f"НЕ откатил «{backup_id}»: ни один файл не вернулся — в "
                  f"копии вырезаны секреты, и возврат затёр бы живое значение "
                  f"меткой. Копия: {apply.BACKUPS / backup_id} — нужные поля "
                  f"верни руками. {_UNDO_FAILED_HINT}", file=sys.stderr)
        else:
            # crash бывает None: у apply.cmd_undo() три выхода без исключения —
            # ранний return 1 (bdir.is_dir() ложно), UNDO_NOTHING_RESTORED
            # (разобран веткой выше) и финальный return 0, — и ни один из них
            # не бросает исключения, которое ловит except выше. Сюда доводит
            # гонка: describe() успела увидеть found=True, а каталог отката
            # исчез до вызова undo(). «Причина: None.» было бы враньём о том,
            # что у отказа нет причины, — причина есть, просто не этот слой
            # её знает; apply.py уже написал её сам чуть выше по stderr.
            reason = crash or ("apply.py отказал, не бросив исключения, — "
                                "смотри его вывод выше")
            print(f"ОТКАЗ: откат «{backup_id}» не сделан (код apply.py: {rc}). "
                  f"Причина: {reason}. Что из настроек уже обновилось — не "
                  f"считай известным, проверь руками. {_UNDO_FAILED_HINT}",
                  file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
