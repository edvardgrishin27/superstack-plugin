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
     но не вернёт секрет.
  3. Удаления нет. Есть карантин: файл переезжает и может вернуться.
  4. Каждое действие пишется в манифест. Откат читает манифест, а не гадает.
  5. Класс находки решает, можно ли применять без человека:
       AUTO   — можно
       GATE   — только с явного согласия
       ASK    — только человек, инструмент лишь показывает, что сделать
       BLOCK  — не применяется никогда
     Секрет в настройках имеет класс BLOCK/ASK именно поэтому: удалять
     чужой пароль автоматически нельзя, а «починить» его молча — тем более.

  python3 apply.py plan findings.json          показать, что будет сделано
  python3 apply.py run  findings.json [--yes]  сделать
  python3 apply.py undo <id>                   откатить
  python3 apply.py list                        какие откаты доступны
  python3 apply.py base [facts.json]            что из базового набора уже есть
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
STATE = CLAUDE / "superstack"
BACKUPS = STATE / "backups"
QUARANTINE = STATE / "quarantine"

#: что инструменту вообще разрешено трогать. Всё остальное — не его дело.
WRITABLE = ("settings.json", "settings.local.json")

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
              f"  флаг: {flag}\n  снять: tools/pause.sh off", file=sys.stderr)
        raise SystemExit(10)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------
# редактирование секретов в бэкапе
# --------------------------------------------------------------------------
def redact(obj, hits: list[dict], path: str = ""):
    """Убрать из копии значения, помеченные пробой как секреты.

    Совпадения приходят из находок и содержат ПУТЬ, а не значение. Здесь
    путь используется, чтобы заменить значение меткой. Отпечаток остаётся,
    поэтому по бэкапу можно понять, тот ли это был секрет, — но не какой.
    """
    locations = {h.get("location") for h in hits if h.get("location")}
    return _redact_walk(obj, "", locations)


def _redact_walk(obj, path: str, locations: set):
    if isinstance(obj, dict):
        return {k: _redact_walk(v, f"{path}.{k}" if path else str(k), locations)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_walk(v, f"{path}[{i}]", locations)
                for i, v in enumerate(obj)]
    if path in locations and isinstance(obj, str):
        fp = hashlib.sha256(obj.encode("utf-8")).hexdigest()[:12]
        return f"<ВЫРЕЗАНО-SUPERSTACK len={len(obj)} fp={fp}>"
    return obj


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


def make_backup(secret_hits: list[dict]) -> dict:
    """Полная копия конфигурации ДО изменений, с вырезанными секретами.

    Возвращает описание бэкапа. Копия сразу перечитывается: бэкап, который
    не прочитали, доказывает только то, что вызов записи не бросил исключение.
    """
    bdir = BACKUPS / stamp()
    bdir.mkdir(parents=True, exist_ok=True)
    saved, redacted_files = [], []

    for name in WRITABLE:
        src = CLAUDE / name
        if not src.is_file():
            continue
        dst = bdir / name
        try:
            data = json.loads(src.read_text(encoding="utf-8-sig"))
        except Exception:
            # Не разбирается — копируем как есть, вырезать нечего и незачем.
            shutil.copy2(src, dst)
            saved.append(name)
            continue
        hits = [h for h in secret_hits if h.get("file") in (name, "user", "user-local")]
        clean = redact(data, hits) if hits else data
        dst.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        saved.append(name)
        if hits:
            redacted_files.append({"file": name, "count": len(hits)})

    verify_backup(bdir, saved)
    verified = saved

    return {"dir": str(bdir), "files": verified, "redacted": redacted_files,
            "created": now()}


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


def act_set_default_mode(dry: bool) -> dict:
    """Задать permissions.defaultMode явно."""
    path = CLAUDE / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    perms = data.setdefault("permissions", {})
    before = perms.get("defaultMode")
    if before:
        return {"changed": False, "why": f"уже задан: {before}"}
    if not dry:
        perms["defaultMode"] = "plan"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return {"changed": True, "field": "permissions.defaultMode",
            "before": before, "after": "plan"}


#: реестр действий. Ключ — id правила. Всё, чего здесь нет, человек делает сам.
ACTIONS: dict[str, dict] = {
    "ctx.default-mode-unset": {
        "kind": "edit-settings",
        "describe": "задам permissions.defaultMode = plan в ~/.claude/settings.json",
        "run": act_set_default_mode,
    },
}


# --------------------------------------------------------------------------
# базовый набор
# --------------------------------------------------------------------------
#: Что получает КАЖДЫЙ, без вопросов и без решателя.
#:
#: Зачем отдельно от ACTIONS. Реестр выше чинит найденное — он реагирует. Этот
#: реестр ставит то, чего человек не просил и о чём не знает, что оно бывает:
#: новичок не может попросить TDD-гейт, потому что не слышал слова «гейт».
#: Диагностика без применения оставляет его ровно там, где нашла, — со списком
#: проблем и без единого решения. Ради этого списка ничего и не строилось.
#:
#: Правило, которое здесь нельзя ослабить: НИЧЕГО НЕ СТАВИТСЯ МОЛЧА. Каждый
#: пункт называет себя вслух, а всё, что трогает чужую машину дальше файлов
#: настроек, идёт классом GATE и ждёт явного согласия. Установщик, который
#: «сделал как лучше», — это тот же захват, что и хук, запускающий проверку
#: без спроса.
BASE_KIT: tuple = (
    {
        "id": "base.plan-mode",
        "touches": "settings",
        "what": "режим прав задан явно",
        "why": "с 14 августа 2026 значение по умолчанию меняет сам вендор; "
               "не задав своё, человек однажды получает чужое",
        "class": "AUTO",
        "present": lambda facts: facts.get("cc.default_mode") is not None,
    },
    {
        "id": "base.tdd-gate",
        "touches": "machine",
        "what": "PreToolUse-гейт TDD (probity)",
        "why": "единственный гейт, срабатывающий РАНЬШЕ проверки прав: "
               "его не обходит даже режим, разрешающий всё",
        "class": "GATE",
        "present": lambda facts: bool(facts.get("hooks.wired.count")),
    },
    {
        "id": "base.stop-gate",
        "touches": "settings",
        "what": "Stop-гейт «тесты, линт, типы»",
        "why": "без него ход закрывается словами: «должно работать» "
               "означает, что работа не сделана",
        "class": "AUTO",
        "present": lambda facts: bool(facts.get("hooks.wired.count")),
    },
    {
        "id": "base.secret-scan",
        "touches": "machine",
        "what": "офлайн-сканер секретов до коммита",
        "why": "дешевле любого ревью моделью: секунды, ноль токенов, жёсткий блок",
        "class": "GATE",
        "present": lambda facts: bool(facts.get("host.git")),
    },
    {
        "id": "base.oracle",
        "touches": "machine",
        "what": "браузерный оракул для проверок интерфейса",
        "why": "единственный источник ДОКАЗАТЕЛЬСТВА вместо утверждения: "
               "результат видно, а не пересказано",
        "class": "GATE",
        "present": lambda facts: False,
    },
    {
        "id": "base.cost-visible",
        "touches": "settings",
        "what": "расход виден в строке состояния",
        "why": "без него бюджетные пороги считаются от самооценки модели, "
               "которую никто не сверял",
        "class": "AUTO",
        "present": lambda facts: bool(facts.get("cc.statusline")),
    },
    {
        "id": "base.kill-switch",
        "touches": "settings",
        "what": "аварийный выключатель",
        "why": "тормоз обязан работать, когда тот, кого тормозят, не отвечает",
        "class": "AUTO",
        "present": lambda facts: bool(facts.get("disc.kill_switch_present")),
    },
)


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


def _kit_row(item: dict) -> dict:
    return {k: item[k] for k in ("id", "what", "why", "class", "touches")}


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
    return 0


def cmd_run(argv: list[str]) -> int:
    consent = "--yes" in argv
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
        manifest["applied"].append({"id": a["id"], "action": a["action"], **result})
        mark = "изменено" if result.get("changed") else "без изменений"
        print(f"  {mark}: {a['action']}")

    mpath = Path(backup["dir"]) / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nОткатить: python3 tools/apply.py undo {Path(backup['dir']).name}")
    if human:
        print(f"Осталось тебе: {len(human)} — см. plan")
    return 0


def cmd_undo(argv: list[str]) -> int:
    bdir = BACKUPS / argv[0]
    if not bdir.is_dir():
        print(f"нет такого отката: {argv[0]}", file=sys.stderr)
        return 1
    manifest = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    restored, skipped = [], []
    for name in manifest["backup"]["files"]:
        src, dst = bdir / name, CLAUDE / name
        text = src.read_text(encoding="utf-8")
        if "<ВЫРЕЗАНО-SUPERSTACK" in text:
            # Возврат такого файла затёр бы живой секрет меткой. Это потеря
            # данных под видом отката — поэтому файл не трогаем и говорим об этом.
            skipped.append(name)
            continue
        shutil.copy2(src, dst)
        restored.append(name)
    print(f"Восстановлено: {', '.join(restored) if restored else '—'}")
    if skipped:
        print(f"НЕ восстановлено (в копии вырезаны секреты): {', '.join(skipped)}")
        print(f"  Копия лежит здесь: {bdir}")
        print("  Верни руками те поля, которые нужны, — иначе откат затрёт секрет меткой.")
    return 0


def cmd_list(_argv: list[str]) -> int:
    if not BACKUPS.is_dir():
        print("откатов пока нет")
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
