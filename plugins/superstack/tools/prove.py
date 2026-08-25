#!/usr/bin/env python3
"""SUPERSTACK — доказательство установщика.

Зачем это отдельный инструмент, а не «gauntlet сказал done:true, значит всё
доказано».

Подсчёт файлов доказывает, что файл существует. Работоспособность доказывает
ТОЛЬКО заблокированная попытка. Планка (gauntlet.py) считает зелёные тесты —
но зелёный набор можно нарисовать самому себе: написать тест, который всегда
проходит, и объявить механизм доказанным. Этот файл существует, чтобы
проверить сам измерительный прибор, а не то, что он измеряет:

  1. planted_failure — система САМА подкладывает падающий тест в одноразовую
     песочницу (не в настоящий репозиторий) и чинит его. Если pytest не
     заметил подложенное падение — вся остальная зелень в системе ничего не
     стоит: значит harness рисует «зелёное» себе сам, независимо от кода.

  2. negative_control — три попытки, каждая ОБЯЗАНА быть отклонена:
       · secret_write   — записать секрет через журнал в открытом виде;
       · self_approval  — продвинуть install_state в proven-local БЕЗ улики;
       · close_with_red — продвинуть install_state в proven, пока планка
                           красная.
     Непойманный (unblocked) негативный контроль — это не «частичный успех».
     Это хуже отсутствия доказательства вовсе: он создаёт уверенность там,
     где её быть не должно. Поэтому unblocked контроль всегда красит весь
     прогон в fail, а не в «2 из 3».

Оба механизма выполняются в ОДНОРАЗОВЫХ песочницах (tempfile): подложенный
тест живёт в temp-каталоге, а не в tests/ этого репозитория; попытки
негативных контролей направлены на ВРЕМЕННЫЙ install_state и временный
журнал (state.STATE_DIR / SUPERSTACK_LOG_DIR подменяются на время трёх
попыток и восстанавливаются в finally). Настоящий ~/.claude эта проверка
не трогает никогда — ни один из трёх негативных контролей не должен иметь
шанс испортить реальную установку самим фактом своей проверки.

Только если ОБА механизма прошли, prove.py вызывает НАСТОящий
state.mark_proven_local() — с уликой, которую он сам только что получил
(id подложенного теста в красном/зелёном выводе, результаты трёх попыток).
state.py эту улику перепроверяет заново по содержанию, а не по факту
наличия ключей — так что даже испорченный вызов отсюда ничего не купит.

  python3 prove.py           -> человеку в stderr + JSON в stdout
  python3 prove.py --json    -> только JSON

  код 0 — оба механизма прошли (и install_state продвинут, если был applied)
  код 1 — что-то красное (в т.ч. непойманный негативный контроль)
  код 2 — не удалось проверить (инфраструктура: pytest/subprocess отказал)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Соседние инструменты лежат в этом же каталоге, но при импорте из теста
# (spec_from_file_location) каталог скрипта в sys.path не попадает — тогда
# «import verify» упал бы там, где при обычном запуске работает.
sys.path.insert(0, str(Path(__file__).resolve().parent))




import log as _log_mod  # noqa: E402 — соседний модуль, не пакет
import state as _state_mod  # noqa: E402

PLUG = Path(__file__).resolve().parent.parent
PYTEST_TIMEOUT = 120

#: уникальный маркер подложенного падения — не название реального теста
#: проекта, чтобы прогон в чужом дереве случайно не совпал с ним.
PLANTED_ID = "test_planted_failure"
PLANTED_MARKER = "SUPERSTACK_PLANTED_FAILURE_9f3c"

#: форма ключа Anthropic, СОБРАННАЯ конкатенацией, а не литералом — тот же
#: приём, что в tests/fixtures/fake_machine.py: файл не должен выглядеть как
#: утечка ни для человека, ни для сканера секретов. Значение никому не
#: принадлежит и никогда не сохраняется — оно существует только затем,
#: чтобы проверить, что redact() его НЕ сохранит тоже.
_FAKE_SECRET = "sk-ant-" + "a1b2c3d4e5f6g7h8i9j0"


# ==========================================================================
# механизм 1: planted_failure
# ==========================================================================
def _pytest(cwd: Path) -> tuple:
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                           cwd=str(cwd), capture_output=True, text=True,
                           timeout=PYTEST_TIMEOUT,
                           env={**os.environ, "SUPERSTACK_IGNORE_PAUSE": "1"})
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"не завершилось за {PYTEST_TIMEOUT} с"
    except OSError as e:
        return 127, str(e)


def _check_planted_failure() -> dict:
    """Красное -> починка -> зелёное, в temp-дереве, которое не переживёт
    эту функцию. Ответ известен заранее (это и есть смысл проверки), а не
    подглядывается из результата — что pytest СКАЖЕТ, то и проверяется."""
    with tempfile.TemporaryDirectory() as td:
        sandbox = Path(td)
        (sandbox / "tests").mkdir()
        (sandbox / "src.py").write_text("МЕХАНИЗМ = 'живой'\n", encoding="utf-8")
        (sandbox / "tests" / "test_src.py").write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
            "from src import МЕХАНИЗМ\n\n\n"
            "def test_baseline():\n"
            "    assert МЕХАНИЗМ == 'живой'\n",
            encoding="utf-8")

        # шаг 1: подложить падение
        (sandbox / "tests" / "test_planted.py").write_text(
            f"def {PLANTED_ID}():\n"
            f"    assert False, {PLANTED_MARKER!r}\n",
            encoding="utf-8")
        rc_red, red_out = _pytest(sandbox)

        # шаг 2: починить — убрать подложенный файл, не трогая остальное
        (sandbox / "tests" / "test_planted.py").unlink()
        rc_green, green_out = _pytest(sandbox)

    infra_failed = rc_red in (124, 127) or rc_green in (124, 127)
    caught = (not infra_failed) and rc_red != 0 and PLANTED_ID in red_out and PLANTED_MARKER in red_out
    fixed = (not infra_failed) and rc_green == 0 and PLANTED_ID not in green_out

    if infra_failed:
        status = "unknown"
    elif caught and fixed:
        status = "pass"
    else:
        status = "fail"

    return {"mechanism": "planted_failure", "status": status,
            "planted_id": PLANTED_ID, "caught": caught, "fixed": fixed,
            "red_output": red_out[-2000:], "green_output": green_out[-2000:]}


# ==========================================================================
# механизм 2: negative_control
# ==========================================================================
def _attempt_secret_write(log_dir: Path) -> dict:
    """Попытка №1: провести секрет через журнал. journal.event() обязан
    его вырезать (см. tools/log.py:redact) — если открытый текст долетит
    до диска, это утечка, а не «пройденный» негативный контроль."""
    try:
        _log_mod.event("prove", "negative_control", "попытка записать секрет",
                       secret=_FAKE_SECRET)
    except Exception as e:  # событие обязано гаситься само; регрессия — не повод падать
        return {"id": "secret_write", "outcome": "unknown",
                "detail": f"log.event выбросил исключение вместо тихого гашения: {e}"}

    path = log_dir / _log_mod.LOG_FILE_NAME
    if not path.is_file():
        return {"id": "secret_write", "outcome": "unknown",
                "detail": "журнал не появился на диске — нечем подтвердить редактирование"}
    text = path.read_text(encoding="utf-8")
    if _FAKE_SECRET in text:
        return {"id": "secret_write", "outcome": "unblocked",
                "detail": "секрет найден в журнале открытым текстом"}
    if '"redacted": true' not in text:
        return {"id": "secret_write", "outcome": "unknown",
                "detail": "секрета в журнале нет, но и метки редактирования тоже — "
                          "непонятно, записалось ли событие вообще"}
    return {"id": "secret_write", "outcome": "rejected",
            "detail": "секрет заменён меткой редактирования, открытым текстом не найден"}


def _attempt_self_approval() -> dict:
    """Попытка №2: продвинуть install_state в proven-local БЕЗ улики.

    Пишет напрямую в state._write — приватную точку записи — только затем,
    чтобы ПОСЕЯТЬ состояние applied для самой попытки: это тестовая
    подготовка, а не заявление «я применил», и она никогда не касается
    настоящего install_state (STATE_DIR на этот момент уже подменён вызывающей
    функцией на временный каталог)."""
    _state_mod._write("applied", {"bootstrap": "prove.py: подготовка к негативному контролю, не реальное применение"})
    try:
        _state_mod.mark_proven_local({})
    except _state_mod.Rejected as e:
        return {"id": "self_approval", "outcome": "rejected", "detail": str(e)[:200]}
    return {"id": "self_approval", "outcome": "unblocked",
            "detail": "mark_proven_local({}) прошёл без единой улики"}


def _attempt_close_with_red() -> dict:
    """Попытка №3: продвинуть install_state в proven, пока планка красная.

    Как и в попытке №2, install_state сеется напрямую в proven-local — это
    подготовка сцены для атаки, а не факт «доказано локально»."""
    _state_mod._write("proven-local", {"bootstrap": "prove.py: подготовка к негативному контролю"})
    fake_red_gauntlet = {"done": False, "gates": [
        {"gate": "набор", "status": "fail", "detail": "3 упало из 9"},
        {"gate": "герметичность", "status": "pass", "detail": "ok"},
        {"gate": "мутации", "status": "pass", "detail": "ok"},
        {"gate": "правила", "status": "pass", "detail": "ok"},
        {"gate": "манифест", "status": "pass", "detail": "ok"},
        {"gate": "план", "status": "pass", "detail": "ok"},
    ]}
    try:
        _state_mod.mark_proven(runner=lambda: fake_red_gauntlet)
    except _state_mod.Rejected as e:
        return {"id": "close_with_red", "outcome": "rejected", "detail": str(e)[:200]}
    return {"id": "close_with_red", "outcome": "unblocked",
            "detail": "mark_proven прошёл при красных воротах планки"}


def _check_negative_controls() -> dict:
    """Все три попытки — во ВРЕМЕННЫХ install_state и журнале. Настоящие
    пути state.py и настоящий SUPERSTACK_LOG_DIR подменяются здесь и
    гарантированно восстанавливаются в finally, даже если попытка бросит
    неожиданное исключение."""
    orig_paths = (_state_mod.STATE_DIR, _state_mod.STATE_FILE, _state_mod.BACKUPS_DIR)
    orig_log_dir = os.environ.get(_log_mod.LOG_DIR_ENV)
    orig_ignore_pause = os.environ.get("SUPERSTACK_IGNORE_PAUSE")

    with tempfile.TemporaryDirectory() as state_td, tempfile.TemporaryDirectory() as log_td:
        sdir = Path(state_td)
        _state_mod.STATE_DIR = sdir
        _state_mod.STATE_FILE = sdir / "install_state.json"
        _state_mod.BACKUPS_DIR = sdir / "backups"
        os.environ[_log_mod.LOG_DIR_ENV] = log_td
        # SUPERSTACK_IGNORE_PAUSE=1 — та же дисциплина, что в gauntlet._sh:
        # без него реальный флаг паузы на машине заставил бы log.event()
        # молча ничего не писать, и попытка №1 стала бы "unknown" не по
        # существу дела, а по не относящейся к делу случайности окружения.
        os.environ["SUPERSTACK_IGNORE_PAUSE"] = "1"
        try:
            attempts = [
                _attempt_secret_write(Path(log_td)),
                _attempt_self_approval(),
                _attempt_close_with_red(),
            ]
        except Exception as e:  # noqa: BLE001 — неожиданный отказ ≠ «unblocked», это «unknown»
            attempts = [{"id": "infrastructure", "outcome": "unknown",
                        "detail": f"негативные контроли не удалось выполнить: {e}"}]
        finally:
            (_state_mod.STATE_DIR, _state_mod.STATE_FILE, _state_mod.BACKUPS_DIR) = orig_paths
            if orig_log_dir is None:
                os.environ.pop(_log_mod.LOG_DIR_ENV, None)
            else:
                os.environ[_log_mod.LOG_DIR_ENV] = orig_log_dir
            if orig_ignore_pause is None:
                os.environ.pop("SUPERSTACK_IGNORE_PAUSE", None)
            else:
                os.environ["SUPERSTACK_IGNORE_PAUSE"] = orig_ignore_pause

    unblocked = [a for a in attempts if a["outcome"] == "unblocked"]
    unknown = [a for a in attempts if a["outcome"] == "unknown"]
    if unblocked:
        # Хуже отсутствия доказательства — красное, а не «2 из 3 пройдены».
        status = "fail"
    elif unknown:
        status = "unknown"
    else:
        status = "pass"
    return {"mechanism": "negative_control", "status": status, "attempts": attempts}


# ==========================================================================
# сводка
# ==========================================================================
def run() -> dict:
    t0 = time.monotonic()
    checks = [_check_planted_failure(), _check_negative_controls()]

    if any(c["status"] == "fail" for c in checks):
        overall = "fail"
    elif any(c["status"] == "unknown" for c in checks):
        overall = "unknown"
    else:
        overall = "pass"

    result = {"tool": "prove", "status": overall, "checks": checks,
              "duration_ms": (time.monotonic() - t0) * 1000}

    if overall == "pass":
        pf = next(c for c in checks if c["mechanism"] == "planted_failure")
        nc = next(c for c in checks if c["mechanism"] == "negative_control")
        evidence = {
            "planted_failure": {"planted_id": pf["planted_id"],
                                 "red_output": pf["red_output"],
                                 "green_output": pf["green_output"]},
            "negative_control": {"attempts": nc["attempts"]},
        }
        try:
            advanced = _state_mod.mark_proven_local(evidence)
            result["install_state"] = advanced["state"]
        except _state_mod.Rejected as e:
            # Оба механизма честно прошли, но install_state отклонил переход
            # по собственной причине (например, реальная установка ещё не
            # в «applied»). Это конкретная, названная причина — не провал
            # самих проверок, поэтому статус проверок не переписывается,
            # но общий итог обязан это отразить.
            result["status"] = "fail"
            result["install_state_error"] = str(e)
    return result


MARK = {"pass": "+", "fail": "x", "unknown": "?"}


def human(v: dict) -> str:
    lines = [{"pass": "ДОКАЗАНО", "fail": "НЕ ДОКАЗАНО", "unknown": "НЕ УДАЛОСЬ ПРОВЕРИТЬ"}[v["status"]]]
    for c in v["checks"]:
        lines.append(f"  {MARK.get(c['status'], '?')} {c['mechanism']:<18} {c.get('detail', '')}".rstrip())
        for a in c.get("attempts", []):
            lines.append(f"      {a['id']:<16} {a['outcome']:<10} {a['detail']}")
    if "install_state" in v:
        lines.append(f"  install_state -> {v['install_state']}")
    if "install_state_error" in v:
        lines.append(f"  install_state НЕ продвинут: {v['install_state_error']}")
    return "\n".join(lines)


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    t0 = time.monotonic()
    _log_mod.event("prove", "запуск", "начало", **{"argv": " ".join(sys.argv[1:])})

    halt_if_paused()
    quiet = "--json" in sys.argv[1:]

    v = run()
    if not quiet:
        print(human(v), file=sys.stderr)
    print(json.dumps(v, ensure_ascii=False, indent=1))

    code = {"pass": 0, "fail": 1, "unknown": 2}[v["status"]]
    _log_mod.event("prove", "запуск", v["status"],
                   duration_ms=(time.monotonic() - t0) * 1000, exit_code=code)
    return code


if __name__ == "__main__":
    sys.exit(main())
