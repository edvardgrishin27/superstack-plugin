#!/usr/bin/env python3
"""SUPERSTACK — состояние установки.

Зачем это отдельный файл, а не поле в манифесте apply.py.

Подсчёт файлов доказывает, что файл существует. Он НЕ доказывает, что
установка работает: `apply.py` мог записать settings.json и тут же сломать
его синтаксис, `prove.py` мог никогда не запускаться, а модель в чате может
просто НАПИСАТЬ «планка взята» — и это неотличимо от правды для любого, кто
читает стенограмму, а не код возврата. Состояние — это последовательность
absent -> applied -> proven-local -> proven, и КАЖДЫЙ переход обязан быть
результатом вычисления, а не заявления:

  · absent -> applied       — только если на диске есть настоящий манифест
                               apply.py с непустым applied[] (не текст «применил»,
                               а артефакт, который apply.py оставляет ТОЛЬКО
                               после реальной записи);
  · applied -> proven-local — только если evidence содержит СЛЕДЫ реального
                               прогона: id подложенного теста в красном выводе
                               pytest, его отсутствие в зелёном, и все три
                               негативных контроля с outcome=="rejected".
                               Эти поля не «доверяются» — их СОДЕРЖАНИЕ
                               проверяется здесь (подстрока, а не факт наличия
                               ключа), потому что пустой ключ проходит любую
                               проверку на присутствие и не проходит ни одну
                               проверку по смыслу;
  · proven-local -> proven   — только если планка (gauntlet.py) ПЕРЕЗАПУЩЕНА
                               прямо отсюда и вернула done=true по всем
                               воротам. Чужой JSON с исхода не принимается —
                               подписанный самим вызывающим отчёт о планке
                               был бы негативным контролем №2 (самоодобрение)
                               в чистом виде, и он обязан быть отклонён.

Это НЕ криптографическая защита: у кого есть доступ к файловой системе, тот
может дописать любую строку в «красный вывод» руками. От этого файл не
защищает — так же как gauntlet.py не защищает от правки самого gauntlet.py.
Он защищает от куда более частого отказа: агент, который ПОВЕРИЛ, что сделал
работу, и вызвал mark_proven_local() с честной, но недостаточной уликой —
такую попытку это здесь ловит структурной проверкой, не только по имени поля.

Каждая функция перехода — это АССЕРТ скрипта, а не команда. У модели нет
способа продвинуть состояние иначе, чем через одну из трёх функций ниже, и
каждая из них сама пересчитывает предпосылку. CLI внизу файла умышленно НЕ
даёт команды «poставь состояние X» — только чтение (`show`) и настоящие
переходы, каждый со своей проверкой.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# --------------------------------------------------------------------------
# расположение — тот же слой, что и apply.py (HOME/.claude/superstack/...),
# но БЕЗ импорта apply.py: параллельная правка соседнего файла не должна
# ронять этот модуль на импорте, а совпадение путей — вопрос конвенции,
# не связанного кода.
# --------------------------------------------------------------------------
HOME = Path.home()
CLAUDE = HOME / ".claude"
STATE_DIR = CLAUDE / "superstack"
STATE_FILE = STATE_DIR / "install_state.json"
BACKUPS_DIR = STATE_DIR / "backups"  # сюда apply.py кладёт manifest.json

STATES = ("absent", "applied", "proven-local", "proven")
_ORDER = {s: i for i, s in enumerate(STATES)}

#: чьи именно три контроля обязаны быть в evidence, прежде чем proven-local
#: будет выдано. Отсутствие любого — недоказанность, не «наверное ок».
REQUIRED_CONTROLS = ("secret_write", "self_approval", "close_with_red")


class Rejected(Exception):
    """Переход отклонён: предпосылка не подтверждена вычислением.

    Это НЕ ошибка программы — это единственный способ, которым состояние
    отказывается двигаться без реального основания. Вызывающий код обязан
    ловить её и показать причину, а не подавлять.
    """


# --------------------------------------------------------------------------
# чтение / запись
# --------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read() -> dict:
    """Текущее состояние. Отсутствие файла — законное состояние `absent`,
    а не ошибка: до первого запуска apply.py файла никогда не было."""
    if not STATE_FILE.is_file():
        return {"state": "absent", "history": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # Повреждённый файл состояния — это НЕ «absent» (файл-то есть, что-то
        # в нём было) и не текущее состояние (прочитать не удалось). Отдельная
        # ошибка не даёт спутать «пусто, потому что не начинали» с «сломано».
        raise Rejected(f"файл состояния повреждён, не читается: {e}") from e
    if data.get("state") not in STATES:
        raise Rejected(f"файл состояния содержит неизвестное значение: {data.get('state')!r}")
    return data


def _write(new_state: str, evidence: dict) -> dict:
    """Единственное место, которое кладёт state.json на диск. Публичные
    функции ниже — единственные вызыватели; прямого экспорта `_write` из
    CLI нет ровно затем, чтобы «поставить состояние X одной командой» было
    физически невозможно."""
    cur = read()
    record = {"from": cur["state"], "to": new_state, "at": _now(), "evidence": evidence}
    history = [*cur.get("history", []), record]
    data = {"state": new_state, "history": history}
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)  # атомарная замена: половина записи не читается никем
    return data


def _require_state(cur: dict, expected: str) -> None:
    if cur["state"] != expected:
        raise Rejected(
            f"нужен переход из «{expected}», а сейчас «{cur['state']}» — "
            f"нельзя перепрыгнуть звено absent->applied->proven-local->proven")


# --------------------------------------------------------------------------
# absent -> applied
# --------------------------------------------------------------------------
def mark_applied() -> dict:
    """Требует настоящий manifest.json от apply.py с непустым applied[].

    Ни один аргумент не принимается: заявление «я применил» ничего не
    доказывает, artefact на диске — доказывает. Если apply.py ещё не писал
    ни разу, этот переход отклоняется, а не выполняется «на слово»."""
    cur = read()
    if cur["state"] not in ("absent", "applied"):
        raise Rejected(f"applied ожидается после absent, а не после «{cur['state']}»")
    if not BACKUPS_DIR.is_dir():
        raise Rejected(f"нет каталога бэкапов apply.py: {BACKUPS_DIR}")
    manifests = sorted(BACKUPS_DIR.glob("*/manifest.json"),
                        key=lambda p: p.stat().st_mtime)
    if not manifests:
        raise Rejected("apply.py ни разу не оставил manifest.json — применения не было")
    latest = manifests[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Rejected(f"манифест {latest} не разобран: {e}") from e
    applied = data.get("applied") or []
    if not applied:
        raise Rejected(f"манифест {latest} пуст — ни одно действие не применено")
    return _write("applied", {"manifest": str(latest), "actions": len(applied)})


# --------------------------------------------------------------------------
# applied -> proven-local
# --------------------------------------------------------------------------
def mark_proven_local(evidence: Optional[dict] = None) -> dict:
    """Требует СОДЕРЖАТЕЛЬНУЮ улику, не факт наличия ключей.

    evidence = {
      "planted_failure": {"planted_id": str, "red_output": str, "green_output": str},
      "negative_control": {"attempts": [{"id": ..., "outcome": "rejected", ...}, x3]},
    }

    Пустой evidence (или evidence={}) — это ровно негативный контроль №2:
    попытка продвинуть состояние без реальной работы. Она ОБЯЗАНА быть
    отклонена, и тест этого файла проверяет именно это: mark_proven_local({})
    поднимает Rejected, а не тихо ставит proven-local.
    """
    cur = read()
    _require_state(cur, "applied")
    evidence = evidence or {}

    pf = evidence.get("planted_failure") or {}
    planted_id = pf.get("planted_id")
    red = pf.get("red_output", "")
    green = pf.get("green_output", "")
    if not planted_id:
        raise Rejected("нет planted_id — подложенный провал не назван")
    if planted_id not in red:
        raise Rejected("id подложенного теста отсутствует в красном выводе — "
                        "нельзя доказать, что он вообще запускался")
    if not any(w in red.lower() for w in ("failed", "error")):
        raise Rejected("красный вывод не похож на падение — planted_failure не подтверждён")
    if planted_id in green:
        raise Rejected("подложенный тест всё ещё присутствует после «починки»")
    if "failed" in green.lower():
        raise Rejected("зелёный вывод не зелёный — есть падения после починки")

    nc = evidence.get("negative_control") or {}
    attempts = nc.get("attempts") or []
    got = {a.get("id") for a in attempts}
    missing = set(REQUIRED_CONTROLS) - got
    if missing:
        raise Rejected(f"не хватает негативных контролей: {sorted(missing)}")
    unblocked = [a["id"] for a in attempts if a.get("outcome") != "rejected"]
    if unblocked:
        # Непойманный негативный контроль хуже отсутствия доказательства:
        # он создаёт ложную уверенность. Поэтому это не «частичный успех» —
        # это провал всего перехода.
        raise Rejected(f"негативный контроль НЕ был отклонён: {unblocked} — "
                        f"это опаснее отсутствия доказательства")

    return _write("proven-local", {"planted_id": planted_id,
                                    "controls": sorted(got)})


# --------------------------------------------------------------------------
# proven-local -> proven
# --------------------------------------------------------------------------
def _find_gauntlet() -> "Path | None":
    """Где планка. Вверх до корня репозитория, а не рядом с собой."""
    here = Path(__file__).resolve()
    for up in here.parents:
        cand = up / "tools" / "gauntlet.py"
        if cand.is_file() and cand != here:
            return cand
    return None


def _run_gauntlet() -> dict:
    """Планка запускается ОТСЮДА, а не читается из чужого отчёта. Отчёт,
    который принёс вызывающий, — это именно то, от чего защищается
    негативный контроль №2: самоодобрение по своему же тексту."""
    # Планка живёт в КОРНЕ репозитория (`tools/gauntlet.py`) и не входит ни в
    # один плагин — значит на машине человека её нет и не будет. Прежний путь
    # (`.../superstack-guard/tools/gauntlet.py`) не существовал нигде и никогда:
    # переход в `proven` не мог произойти ни у кого, а тесты этого не видели,
    # потому что подставляли `runner=`.
    #
    # Ищем по имени тем же резолвером, что и скиллы; не нашли — говорим прямо.
    gauntlet_py = _find_gauntlet()
    if gauntlet_py is None:
        raise Rejected(
            "планки нет на этой машине: `tools/gauntlet.py` живёт в корне "
            "репозитория разработки и не входит ни в один плагин. Ступень "
            "`proven` — состояние дерева разработки, а не установки; на "
            "машине человека доказательством служит `prove.py` с негативными "
            "контролями, и она даёт `proven-local`")
    p = subprocess.run([sys.executable, str(gauntlet_py), "--json"],
                       capture_output=True, text=True, timeout=1200,
                       cwd=str(gauntlet_py.parent.parent),
                       env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
    try:
        return json.loads(p.stdout)
    except ValueError as e:
        raise Rejected(f"планка не вернула разбираемый JSON: {e}") from e


def _expected_gates() -> int:
    """Сколько ворот у планки СЕЙЧАС. Число, продублированное руками, устаревает
    молча: так уже случилось, когда ворот стало семь."""
    g = _find_gauntlet()
    if g is None:
        return 6
    try:
        import re
        m = re.search(r"^GATES = \[(.*?)^\]", g.read_text("utf-8"), re.S | re.M)
        return max(6, len(re.findall(r'\("', m.group(1)))) if m else 6
    except OSError:
        return 6


def mark_proven(*, runner=None) -> dict:
    """runner — только для тестов (подставной прогон планки без реального
    pytest на весь репозиторий, который занимает минуты). В проде параметр
    не передаётся, и планка запускается по-настоящему через _run_gauntlet()."""
    cur = read()
    _require_state(cur, "proven-local")
    result = (runner or _run_gauntlet)()
    gates = result.get("gates") or []
    # Порог читается из САМОЙ планки, а не переписывается здесь: он уже
    # устарел однажды (ворот стало семь, а тут стояло шесть), и это тот
    # случай, когда число в двух местах расходится молча.
    if len(gates) < _expected_gates():
        raise Rejected(f"планка вернула {len(gates)} ворот вместо "
                       f"{_expected_gates()} — это не полный прогон")
    bad = [g["gate"] for g in gates if g.get("status") != "pass"]
    if bad or not result.get("done"):
        # «Закрыть ход с красными тестами» — третий негативный контроль.
        # Хотя бы одни красные/непроверенные ворота обязаны блокировать
        # переход, а не только влиять на текст отчёта.
        raise Rejected(f"планка не взята целиком, не прошли: {bad or ['done=false']}")
    return _write("proven", {"gates": len(gates)})


# --------------------------------------------------------------------------
# CLI — только чтение. Команды «poставь состояние X» здесь нет НАМЕРЕННО.
# --------------------------------------------------------------------------
def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] != "show":
        print("вызов: state.py show", file=sys.stderr)
        print("(переходы состояния — только из кода: mark_applied / "
              "mark_proven_local / mark_proven, не через CLI)", file=sys.stderr)
        return 3
    try:
        data = read()
    except Rejected as e:
        print(f"НЕ УДАЛОСЬ: {e}", file=sys.stderr)
        print(json.dumps({"state": "unknown", "error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
