#!/usr/bin/env python3
"""SUPERSTACK — доктор актуальности.

Отвечает на вопрос: что обновить, что удалить, и что уже есть в самом Claude Code.

Четыре независимые оси, которые НЕ смешиваются между собой — потому что
«устарело», «умерло» и «стало ненужным» лечатся по-разному:

  A. ЖИВОСТЬ АПСТРИМА   репозиторий заархивирован? давно ли трогали? отстали ли мы?
  B. ВЫТЕСНЕНИЕ НАТИВОМ  это уже есть в ядре и стало лишним?
  C. РАСХОЖДЕНИЕ         объявлено одно, установлено другое?
  D. САМОПРОВЕРКА        не устарел ли сам реестр вытеснения?

Ось D существует потому, что ось B честно неполна: сопоставить свежую нативную
возможность со сторонним инструментом может только человек, читающий changelog.
Доктор обязан признаваться в этом сам, а не делать вид, что знает всё.

  python3 doctor.py [--json] [--offline]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
HERE = Path(__file__).resolve().parent.parent
LEDGER = HERE / "data" / "supersession.json"

STALE_DAYS = 180
CACHE = CLAUDE / "superstack" / "cache" / "upstream.json"

OFFLINE = "--offline" in sys.argv
AS_JSON = "--json" in sys.argv


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def gh(path: str) -> dict | None:
    """GitHub API без токена. Оффлайн и лимиты — не ошибка, а «неизвестно»."""
    if OFFLINE:
        return None
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "superstack-doctor"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def days_since(iso: str) -> int | None:
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - then).days
    except Exception:
        return None



def active_version() -> tuple[str | None, str]:
    """Версия движка, исполняющего сессию, и как она получена.

    Без неё ось «уже есть в ядре» даёт советы вслепую: на старом движке
    нативной замены ещё нет, и «удали, это дублирует ядро» — вредный совет.
    """
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if "desktop" in entry:
        asar = Path("/Applications/Claude.app/Contents/Resources/app.asar")
        if asar.is_file():
            try:
                r = subprocess.run(["/usr/bin/strings", str(asar)],
                                   capture_output=True, text=True, timeout=60)
                vs = sorted(set(re.findall(r'"(2\.\d+\.\d{2,3})"', r.stdout)))
                if vs:
                    return vs[-1], "движок десктоп-приложения"
            except Exception:
                pass
    link = HOME / ".local" / "bin" / "claude"
    try:
        if link.exists():
            m = re.search(r"(\d+\.\d+\.\d+)", os.path.basename(os.path.realpath(link)))
            if m:
                return m.group(1), "нативный установщик"
    except Exception:
        pass
    return None, "определить не удалось"


def _vt(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except Exception:
        return (0, 0, 0)


# ---------------------------------------------------------------- ОСЬ A
def axis_upstream(marketplaces: dict | None = None) -> list[dict]:
    """Живость источников, из которых что-то установлено.

    Список маркетплейсов можно подать снаружи. Без этого функция читала
    только реальный ~/.claude, и проверить её логику было нечем: на машине
    без маркетплейсов она возвращала пустой список, и любой тест про неё
    проходил вакуумно — то есть измерял состав чужого компьютера, а не код.
    """
    out = []
    seen: set[str] = set()

    mk = marketplaces if marketplaces is not None else (
        read_json(CLAUDE / "plugins" / "known_marketplaces.json") or {})
    for name, meta in (mk.items() if isinstance(mk, dict) else []):
        # Штатная форма — структурная: {"source":{"source":"github",
        # "repo":"owner/name"}}. Строки github.com в ней НЕТ, и регулярка,
        # требовавшая её, отправляла официальные маркетплейсы в «не удалось
        # определить». То есть ось живости слепла именно на том, что стоит
        # у всех. Вторая форма — git+url — остаётся.
        slug = None
        if isinstance(meta, dict):
            src_meta = meta.get("source")
            if isinstance(src_meta, dict) and src_meta.get("repo"):
                slug = str(src_meta["repo"]).strip("/")
        if not slug:
            src = json.dumps(meta) if not isinstance(meta, str) else meta
            m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git|\"|$)", src)
            if not m:
                out.append({"source": name, "state": "unknown",
                            "why": "не удалось определить репозиторий"})
                continue
            slug = f"{m.group(1)}/{m.group(2)}"
        if slug in seen:
            continue
        seen.add(slug)

        data = gh(f"repos/{slug}")
        if data is None:
            out.append({"source": slug, "state": "unknown",
                        "why": "нет ответа от GitHub (оффлайн или лимит)"})
            continue
        if data.get("message") == "Not Found":
            out.append({"source": slug, "state": "gone",
                        "why": "репозиторий или владелец не существует"})
            continue

        age = days_since(data.get("pushed_at", ""))
        if data.get("archived"):
            state, why = "archived", "репозиторий заархивирован — обновлений не будет"
        elif age is not None and age > STALE_DAYS:
            state, why = "stale", f"последний коммит {age} дней назад"
        elif age is None:
            # Дата не разобралась. «Неизвестно» и «здоров» — разные утверждения,
            # и раньше они рендерились одинаково: маркер «·» и строка
            # «последний коммит None дней назад». Заброшенный источник с
            # нестандартным полем даты выглядел живым.
            state, why = "unknown", "дату последнего коммита разобрать не удалось"
        else:
            state, why = "current", f"последний коммит {age} дней назад"
        out.append({"source": slug, "state": state, "why": why,
                    "stars": data.get("stargazers_count")})

    # GSD ставится не через marketplace — проверяем отдельно.
    if (CLAUDE / "get-shit-done" / "VERSION").is_file():
        ver = (CLAUDE / "get-shit-done" / "VERSION").read_text().strip()
        data = gh("repos/gsd-build/get-shit-done")
        if data and data.get("archived"):
            out.append({"source": "gsd-build/get-shit-done", "state": "archived",
                        "why": f"установлена {ver} из заархивированного репозитория",
                        "replacement": "@opengsd/gsd-core"})
    return out


# ---------------------------------------------------------------- ОСЬ B
def axis_supersession(active: str | None = None,
                      inventory: dict | None = None) -> list[dict]:
    """Что из установленного уже есть в самом Claude Code.

    Каждая запись гейтится по полю since: если активная версия СТАРШЕ той,
    в которой появилась нативная замена, совет «удали» неприменим — у этого
    человека замены ещё нет.

    inventory позволяет подать состав машины извне. Без этого функция читала
    только реальный ~/.claude, и проверить логику гейта было нечем: на машине
    без совпадений она возвращала пустой список, и тест про гейт молча
    превращался в тест про содержимое чужого компьютера.
    """
    ledger = read_json(LEDGER)
    if not ledger:
        return [{"id": "ledger-missing", "state": "error",
                 "why": "реестр вытеснения не найден"}]

    if inventory is None:
        skills = {d.name for d in (CLAUDE / "skills").iterdir()} \
            if (CLAUDE / "skills").is_dir() else set()
        commands = {f.stem for f in (CLAUDE / "commands").glob("*.md")} \
            if (CLAUDE / "commands").is_dir() else set()
        mcp = set((read_json(HOME / ".claude.json") or {}).get("mcpServers", {}))
    else:
        skills = set(inventory.get("skills", []))
        commands = set(inventory.get("commands", []))
        mcp = set(inventory.get("mcp", []))

    out = []
    for e in ledger.get("entries", []):
        # Неполная запись реестра роняла доктора KeyError. Реестр правится
        # руками и приходит через git — то есть кривая запись это норма,
        # а не исключительная ситуация.
        if not isinstance(e, dict) or not e.get("id") or not isinstance(
                e.get("superseded_by"), dict):
            out.append({"id": e.get("id", "<без id>") if isinstance(e, dict) else "?",
                        "state": "error",
                        "why": "запись реестра неполна — пропущена"})
            continue
        det = e.get("detect", {})
        found: list[str] = []
        found += [s for s in det.get("skills", []) if s in skills]
        found += [c for c in det.get("commands", []) if c in commands]
        found += [m for m in det.get("mcp", []) if m in mcp]
        if inventory is None:
            # Файлы и каталоги проверяются на диске только в боевом режиме.
            # При поданном составе обращение к диску сделало бы функцию
            # наполовину герметичной — а это хуже негерметичной целиком:
            # тест выглядит изолированным и молча зависит от машины.
            for f in det.get("files", []):
                if Path(os.path.expanduser(f)).exists():
                    found.append(f)
            if det.get("dir") and Path(os.path.expanduser(det["dir"])).is_dir():
                n = len(list(Path(os.path.expanduser(det["dir"])).glob("*.md")))
                if n:
                    found.append(f"{det['dir']} ({n} файлов)")
        else:
            found += [f for f in det.get("files", []) if f in inventory.get("files", [])]
            if det.get("dir") and det["dir"] in inventory.get("dirs", []):
                found.append(det["dir"])
        if not found:
            continue

        since = e["superseded_by"].get("since")
        applicable, gate_note = True, ""
        if active and since and since != "n/a":
            if _vt(active) < _vt(since):
                applicable = False
                gate_note = (f"НЕ ПРИМЕНИМО: нативная замена появилась в {since}, "
                             f"а работает {active} — у тебя её ещё нет")
        elif not active:
            gate_note = "версия движка не определена — применимость не проверена"

        out.append({
            "id": e["id"],
            "applicable": applicable,
            "gate_note": gate_note,
            "found": found,
            "native": e["superseded_by"]["native"],
            "since": e["superseded_by"].get("since"),
            "overlap": e["overlap"],
            "confidence": e["confidence"],
            "action": e["action"],
            "plain": e["plain"],
            "caveat": e.get("caveat", ""),
            "observable": e["superseded_by"].get("observe", "").startswith("существует"),
        })
    return out


_COLLECT_MOD = None


def _collect():
    """Сборщик фактов как библиотека: одна логика — один ответ.

    Корень пути синхронизируется при каждом обращении. Иначе доктор читал бы
    манифест из своего CLAUDE, а подключённые хуки — из константы, которую
    сборщик связал при импорте: два разных корня внутри одной функции.
    На обычном прогоне они совпадают, и дефект остаётся латентным — ровно
    до первой попытки проверить эту функцию.
    """
    global _COLLECT_MOD
    if _COLLECT_MOD is None:
        import importlib.util
        import json as _json
        _here = Path(__file__).resolve().parent
        _plugins = _here.parent.parent
        _deps = []
        try:
            _deps = _json.loads((_here.parent / ".claude-plugin" / "plugin.json")
                                .read_text("utf-8")).get("dependencies", [])
        except (OSError, ValueError):
            pass
        path = _here / "probe" / "collect.py"
        for _d in _deps:
            _cand = _plugins / _d["name"] / "tools" / "probe" / "collect.py"
            if _cand.is_file():
                path = _cand
                break
        spec = importlib.util.spec_from_file_location("ss_collect_lib", path)
        _COLLECT_MOD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_COLLECT_MOD)
    _COLLECT_MOD.CLAUDE = CLAUDE
    _COLLECT_MOD.HOME = HOME
    return _COLLECT_MOD


# ---------------------------------------------------------------- ОСЬ C
def axis_drift() -> list[dict]:
    """Объявлено против фактически подключено."""
    out = []
    # Счёт берётся у сборщика фактов, а не считается здесь заново. Своя копия
    # логики давала ТРЕТЬЕ число на тот же вопрос «сколько хуков подключено»:
    # она читала один файл настроек и сопоставляла id подстрокой. Два ответа
    # на один вопрос внутри одного продукта — это не расхождение в деталях,
    # это отсутствие источника правды.
    manifest = read_json(CLAUDE / "hooks" / "hooks.json")
    if manifest:
        declared = [e.get("id", "?") for entries in manifest.get("hooks", {}).values()
                    for e in entries]
        blob = _collect().wired_hooks_scoped()[0]
        dormant = [d for d in declared if not _collect().is_hook_wired(d, blob)]
        if dormant:
            out.append({
                "id": "hooks-dormant", "declared": len(declared),
                "wired": len(declared) - len(dormant), "dormant": len(dormant),
                "why": "манифест хуков объявляет больше, чем подключено в настройках",
            })

    if (CLAUDE / "skills" / "continuous-learning-v2").is_dir() \
            and not (CLAUDE / "homunculus").is_dir():
        out.append({"id": "learning-never-ran",
                    "why": "система обучения установлена, но хранилище не создано — она не работала ни разу"})
    return out


# ---------------------------------------------------------------- ОСЬ D
def axis_self(built_for: str | None = None, installed: str | None = None) -> dict:
    """Не устарел ли сам реестр. Доктор обязан сомневаться в себе.

    Версии можно подать снаружи: иначе логику сравнения нечем проверить,
    кроме как ждать, пока апстрим выпустит новую версию.
    """
    ledger = read_json(LEDGER) or {}
    if built_for is None:
        built_for = ledger.get("observed_claude_code", "?")
    age = days_since(ledger.get("generated", "") + "T00:00:00Z")

    if installed is None:
        installed = "?"
        try:
            r = subprocess.run(["npm", "view", "@anthropic-ai/claude-code", "version"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                installed = r.stdout.strip()
        except Exception:
            pass

    def minor(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split(".")[:2])
        except Exception:
            return (0, 0)

    # Разрыв считается по ПАРЕ (мажор, минор). Прежняя версия вычитала только
    # минорные: при смене мажора 2.1 -> 3.0 получалось -1, что «меньше двух», и
    # самопроверка объявляла себя достоверной ровно в момент самого большого
    # изменения. Проверка, слепнущая на главном событии, хуже отсутствующей.
    gap = None
    major_changed = False
    if installed != "?" and built_for != "?":
        mi, mb = minor(installed), minor(built_for)
        major_changed = mi[0] != mb[0]
        gap = (mi[0] - mb[0]) * 1000 + (mi[1] - mb[1])

    # «Не смог определить» и «устарел» — разные утверждения, и звучать они
    # обязаны по-разному. Прежняя версия на машине без npm или без сети
    # выдавала тревогу об устаревании первой строкой, до всего остального:
    # человек видит крик о проблеме, которой нет, и перестаёт читать
    # предупреждения вообще.
    return {
        "ledger_built_for": built_for,
        "ledger_age_days": age,
        "latest_claude_code": installed,
        "minor_gap": gap,
        "major_changed": major_changed,
        "version_unknown": installed == "?",
        "trustworthy": (gap is not None and not major_changed and gap <= 2),
        "why_not_automatic": ledger.get("decay", {}).get("not_solved", ""),
    }


def render(report: dict) -> str:
    L = "─" * 72
    out = [L, "ДОКТОР: что обновить, что убрать, что уже есть в Claude Code", L, ""]

    s = report["self"]
    if s.get("version_unknown"):
        out += ["· Актуальную версию Claude Code проверить не удалось "
                "(нет npm или нет сети).",
                "  Это не признак устаревания реестра — это непроверенная глубина.",
                f"  Реестр собран под {s['ledger_built_for']}, "
                f"возраст {s['ledger_age_days'] if s['ledger_age_days'] is not None else '?'} дн.", ""]
    elif not s["trustworthy"]:
        head = ("⚠ СМЕНИЛАСЬ МАЖОРНАЯ ВЕРСИЯ — реестр заведомо неполон."
                if s.get("major_changed") else "⚠ РЕЕСТР МОГ УСТАРЕТЬ.")
        out += [head,
                f"  Собран под Claude Code {s['ledger_built_for']}, актуальная {s['latest_claude_code']}.",
                f"  Возраст реестра: {s['ledger_age_days']} дн.",
                "  Ось «уже есть в ядре» ниже верна только до этой глубины.",
                f"  Не решается автоматически: {s['why_not_automatic']}", ""]

    out += ["A. ЖИВОСТЬ ИСТОЧНИКОВ", ""]
    for r in report["upstream"] or [{"source": "—", "state": "none", "why": "источников не найдено"}]:
        mark = {"archived": "✗", "gone": "✗", "stale": "!", "current": "·",
                "unknown": "?", "none": " "}.get(r["state"], "?")
        line = f"  {mark} {r['source']:<38} {r['why']}"
        out.append(line)
        if r.get("replacement"):
            out.append(f"      → перейти на {r['replacement']}")
    out.append("")

    out += [f"B. УЖЕ ЕСТЬ В САМОМ CLAUDE CODE   "
            f"(активная версия {report.get('active_version') or '?'}, "
            f"{report.get('active_version_source')})", ""]
    sup = report["supersession"]
    if not sup:
        out.append("  ничего вытесненного не обнаружено")
    for r in sup:
        if not r.get("applicable", True):
            out.append(f"  ○ {r['plain']}")
            out.append(f"      {r['gate_note']}")
            out.append("")
            continue
        # Запись-ошибка («реестр не найден») не имеет полей находки. Прежняя
        # версия обращалась к ним напрямую и падала — то есть штатная ветка
        # отказа сама была сломана и не исполнялась ни разу.
        if r.get("state") == "error":
            out.append(f"  ⚠ {r.get('why', 'ось не отработала')}")
            continue
        conf = "" if r.get("confidence") == "high" else f"  [уверенность {r.get('confidence','?')}]"
        out.append(f"  • {r['plain']}{conf}")
        out.append(f"      нашёл: {', '.join(str(x) for x in r['found'])}")
        out.append(f"      в ядре: {r['native']} (с {r['since']})   действие: {r['action']}")
        if not r["observable"]:
            out.append("      ⚠ проверить наличие в ядре скриптом нельзя — это ОЖИДАНИЕ, а не наблюдение")
        if r["caveat"]:
            out.append(f"      важно: {r['caveat']}")
        out.append("")

    out += ["C. РАСХОЖДЕНИЯ", ""]
    if not report["drift"]:
        out.append("  расхождений нет")
    for r in report["drift"]:
        if r["id"] == "hooks-dormant":
            out.append(f"  ! объявлено хуков {r['declared']}, подключено {r['wired']}, "
                       f"мёртвым грузом {r['dormant']}")
        else:
            out.append(f"  ! {r['why']}")
    out += ["", L,
            "Ничего не изменено. Доктор только смотрит.",
            "Применить: /superstack apply <id>", L]
    return "\n".join(out)


def halt_if_paused() -> None:
    """Тормоз соблюдается, а не только попадает в отчёт.

    Раньше флаг паузы читался лишь как факт для отчёта: человек жал стоп,
    система записывала «paused: true» и продолжала работать. Теперь каждый
    инструмент проверяет флаг ПЕРВЫМ действием и выходит с кодом 10.
    """
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
        print(f"ОСТАНОВЛЕНО: система на паузе с {since}\n"
              f"  флаг: {flag}\n"
              f"  снять: tools/pause.sh off", file=__import__("sys").stderr)
        raise SystemExit(10)


def main() -> None:
    halt_if_paused()
    ver, how = active_version()
    report = {
        "active_version": ver,
        "active_version_source": how,
        "upstream": axis_upstream(),
        "supersession": axis_supersession(ver),
        "drift": axis_drift(),
        "self": axis_self(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2) if AS_JSON else render(report))


if __name__ == "__main__":
    main()
