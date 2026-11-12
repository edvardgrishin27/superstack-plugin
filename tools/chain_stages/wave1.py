"""Стадии цепочки волны 1: заведение, снимки, проверка, автономия, «доказано»,
отчёт, возврат, хук на красном тесте в подпапке.

Каждая стадия получает Ctx и возвращает None (итог ожидаемый) или одну фразу
о том, что пошло не так.
"""
from __future__ import annotations

import json

KIND = "проверено машиной"


def _bad(r, what: str) -> str:
    tail = (r.stderr or r.stdout or "").strip().splitlines()
    return f"{what} (код {r.returncode}): {tail[-1][:160] if tail else 'пусто'}"


def stage_enable(c):
    r = c.tool("enable", str(c.proj))
    if r.returncode != 0:
        return _bad(r, "заведение не удалось")
    r = c.tool("enable", str(c.proj), "--check")
    return None if r.returncode == 0 else _bad(r, "проект не числится заведённым")


def stage_snapshots(c):
    for args in (("init",), ("snap", "до правки: app")):
        r = c.tool("checkpoint", str(c.proj), *args)
        if r.returncode != 0:
            return _bad(r, f"снимок «{args[0]}» не снят")
    (c.proj / "app.py").write_text("x = 2\n", "utf-8")
    r = c.tool("checkpoint", str(c.proj), "snap", "после правки: app")
    if r.returncode != 0:
        return _bad(r, "снимок после правки не снят")
    r = c.tool("checkpoint", str(c.proj), "list", "--json")
    цель = (json.loads(r.stdout).get("цель_отката") if r.returncode == 0 else None)
    return None if цель else "список снимков не назвал цель возврата"


def stage_tasks_recorded(c):
    state = str(c.proj / ".superstack" / "state.json")
    for tid, name, st in (("T1", "Часть один", "waiting"),
                          ("T2", "Часть два", "claimed")):
        r = c.tool("progress", "task", state, tid, name, "--status", st)
        if r.returncode != 0:
            return _bad(r, f"задача {tid} не записана")
    return None


def stage_verify_writes_record(c):
    r = c.tool("verify", "--json", str(c.proj))
    return None if r.returncode == 0 else _bad(r, "проверка не зелёная")


def stage_autonomy_reads_record(c):
    r = c.tool("autonomy", str(c.proj), "--can", "2")
    return None if r.returncode == 0 else _bad(
        r, "ступень 2 не открылась после зелёной проверки")


def _statuses(c) -> dict:
    state = c.proj / ".superstack" / "state.json"
    data = json.loads(state.read_text("utf-8"))
    return {t["id"]: t["status"] for w in data["waves"].values() for t in w}


def stage_promote_waiting_to_proven(c):
    state = str(c.proj / ".superstack" / "state.json")
    r = c.tool("state_fields", "promote", state)
    if r.returncode != 0:
        return _bad(r, "перевод в «доказано» отказал при свежей зелёной записи")
    got = _statuses(c)
    return None if got == {"T1": "proven", "T2": "proven"} else f"после перевода: {got}"


def stage_report_machine_word_only_for_proven(c):
    state = str(c.proj / ".superstack" / "state.json")
    r = c.tool("progress", "task", state, "T3", "Часть три", "--status", "claimed")
    if r.returncode != 0:
        return _bad(r, "заявленная задача не записана")
    r = c.tool("report", str(c.proj / ".superstack"))
    if r.returncode != 0:
        return _bad(r, "отчёт не собран")
    строки = {s.strip() for s in r.stdout.splitlines()}
    for name, ждём in (("Часть один", True), ("Часть два", True),
                       ("Часть три", False)):
        есть = any(s.startswith(f"✓ {name}") and KIND in s for s in строки)
        if есть != ждём:
            return f"«{name}»: слово «{KIND}» {'нет' if ждём else 'есть'}, а должно быть наоборот"
    return None


def stage_oops_restores_file(c):
    r = c.tool("checkpoint", str(c.proj), "list", "--json")
    цель = json.loads(r.stdout).get("цель_отката") if r.returncode == 0 else None
    if not цель:
        return "цель возврата не названа"
    r = c.tool("checkpoint", str(c.proj), "back", цель["sha"])
    if r.returncode != 0:
        return _bad(r, "возврат не удался")
    now = (c.proj / "app.py").read_text("utf-8")
    return None if now == "x = 1\n" else f"файл не вернулся: {now!r}"


def _hook(c, cwd):
    вход = json.dumps({"cwd": str(cwd), "session_id": "chain"})
    return c.run(c.hooks / "verify-gate.py", stdin=вход, cwd=c.home,
                 extra_env={"CLAUDE_PROJECT_DIR": str(c.home)})


def stage_gate_blocks_red_test_in_subfolder(c):
    sub = c.proj / "sub"
    (sub / "tests").mkdir(parents=True)
    (sub / "tests" / "conftest.py").write_text("", "utf-8")
    (sub / "tests" / "test_red.py").write_text(
        "def test_red():\n    assert 1 == 2\n", "utf-8")
    r = _hook(c, c.home)
    if r.stdout.strip():
        return "хук заговорил в папке, которую никто не заводил"
    r = _hook(c, sub)
    try:
        ответ = json.loads(r.stdout)
    except ValueError:
        return f"хук не заблокировал красный тест в подпапке: {r.stdout[:80]!r}"
    return None if ответ.get("decision") == "block" else "ответ хука — не блок"


STAGES = [
    ("заведение проекта", stage_enable),
    ("снимки до и после правки", stage_snapshots),
    ("ход работ записан", stage_tasks_recorded),
    ("проверка пишет запись", stage_verify_writes_record),
    ("автономия читает запись", stage_autonomy_reads_record),
    ("ждущее становится доказанным", stage_promote_waiting_to_proven),
    ("отчёт: слово только у доказанного",
     stage_report_machine_word_only_for_proven),
    ("возврат вернул файл", stage_oops_restores_file),
    ("хук блокирует красный тест в подпапке",
     stage_gate_blocks_red_test_in_subfolder),
]
