#!/usr/bin/env python3
"""SUPERSTACK — доктор актуальности.

Отвечает на вопрос: что обновить, что удалить, и что уже есть в самом Claude Code.

Шесть независимых осей, которые НЕ смешиваются между собой — потому что
«устарело», «умерло», «стало ненужным» и «опасно настроено» лечатся по-разному:

  A. ЖИВОСТЬ АПСТРИМА   репозиторий заархивирован? давно ли трогали? отстали ли мы?
  B. ВЫТЕСНЕНИЕ НАТИВОМ  это уже есть в ядре и стало лишним?
  C. РАСХОЖДЕНИЕ         объявлено одно, установлено другое?
  D. САМОПРОВЕРКА        не устарел ли сам реестр вытеснения?
  E. ЗДОРОВЬЕ            хук указывает в никуда? MCP нечем запустить? два скилла
                         спорят за одну просьбу?
  F. БЕЗОПАСНОСТЬ КОНФИГУРАЦИИ  разрешение шире нужного? MCP со всеми правами?

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
        # Слаг едет в путь URL. Без проверки формы `owner/name` строка вроде
        # "../../user/repos" уводит запрос в другой эндпоинт GitHub — не
        # эскалация, но запрос уже не тот, о котором отчитываются.
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", slug):
            out.append({"source": name, "state": "unknown",
                        "why": f"имя репозитория не похоже на owner/name: {slug}"})
            continue
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
        # Пробы ищутся по РАСКЛАДКЕ, а не по полю `dependencies`: движок
        # Claude Code 2.1.42 отвергает это поле как неизвестный ключ, из-за
        # чего ни один пакет не проходил валидацию. Поле убрано — вместе с ним
        # исчезла бы и эта проводка, молча: doctor нашёл бы свой несуществующий
        # `probe/collect.py` и упал на импорте.
        path = _here / "probe" / "collect.py"
        if not path.is_file():
            for _base in (_plugins, _plugins.parent):
                if not _base.is_dir():
                    continue
                _hits = sorted(_base.glob("*/tools/probe/collect.py")) or \
                        sorted(_base.glob("*/*/tools/probe/collect.py"))
                if _hits:
                    path = _hits[0]
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
    out += ["", "E. ЗДОРОВЬЕ", ""]
    здоровье = report.get("health") or []
    if not здоровье:
        out.append("  · хуки на месте, MCP запускаемы, имена скиллов не спорят")
    for r in здоровье:
        цель = r.get("file") or r.get("server") or r.get("skill") or "?"
        out.append(f"  ! {цель:<38} {r['why']}")
        if r.get("sources"):
            out.append(f"      откуда: {', '.join(r['sources'])}")

    out += ["", "F. БЕЗОПАСНОСТЬ КОНФИГУРАЦИИ", ""]
    безопасность = report.get("config_security") or []
    if not безопасность:
        out.append("  · разрешений шире нужного и MCP со всеми правами не найдено")
    for r in безопасность:
        цель = r.get("rule") or r.get("server") or r.get("skill") or "?"
        out.append(f"  ! {цель:<38} {r['why']}")

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



def _utf8_stdio() -> None:
    """Печать по-русски не должна зависеть от локали.

    В окружении без UTF-8 — минимальный контейнер, cron с урезанным env,
    `PYTHONCOERCECLOCALE=0` — кодировка вывода оказывается ascii, и первый же
    русский символ роняет инструмент целиком. Человек получает не «проверка не
    прошла», а трейсбек вместо любого ответа. На macOS по умолчанию это не
    воспроизводится: интерпретатор сам приводит локаль C к C.UTF-8.
    """
    for поток in (sys.stdout, sys.stderr):
        кодировка = (getattr(поток, "encoding", "") or "").lower().replace("-", "")
        if кодировка != "utf8" and hasattr(поток, "reconfigure"):
            поток.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- ОСЬ E
#: Что означает «MCP нечем запустить»: первое слово команды не находится ни в
#: PATH, ни как файл. Запускать сам сервер здесь нельзя — это минуты на ход и
#: побочные эффекты у чужого софта; отсутствие исполнителя ловится даром.
def _runnable(cmd: str) -> "bool | None":
    if not cmd:
        return None
    from shutil import which
    первое = cmd.split()[0]
    if which(первое):
        return True
    p = Path(первое).expanduser()
    return True if p.exists() else False


def _skill_sources(roots: list | None = None) -> dict:
    """{имя скилла: [откуда]}. Корни можно подать снаружи — иначе проверку
    нечем измерить, кроме как содержимым чужой машины."""
    если_свои = roots if roots is not None else None
    где: dict = {}
    корни = если_свои
    if корни is None:
        корни = [(CLAUDE / "skills", "личный"),
                 (Path.cwd() / ".claude" / "skills", "проектный")]
        кэш = CLAUDE / "plugins" / "cache"
        if кэш.is_dir():
            for пакет in sorted(кэш.glob("*/*/*/skills")):
                имя, версия = пакет.parts[-3], пакет.parts[-2]
                корни.append((пакет, f"плагин {имя}@{версия}"))
    for корень, откуда in корни:
        if not Path(корень).is_dir():
            continue
        for d in sorted(Path(корень).iterdir()):
            if (d / "SKILL.md").is_file():
                где.setdefault(d.name, []).append(откуда)
    return где


def trigger_collisions(roots: list | None = None) -> list[dict]:
    """Два скилла с ОДНИМ именем: выигрывает случайный.

    Проверка намеренно точная — совпадение имени, а не похожесть описаний.
    Похожесть пришлось бы судить моделью, а осмотр, выносящий суждение
    моделью, сам нуждается в осмотре.

    Два случая разведены, потому что чинятся по-разному: одна и та же пачка
    в двух версиях — это мусор в кэше, а разные источники — настоящий спор
    за просьбу.
    """
    out = []
    for имя, места in _skill_sources(roots).items():
        if len(места) < 2:
            continue
        пачки = [м.split("@")[0] for м in места if м.startswith("плагин ")]
        if len(места) == len(пачки) and len(set(пачки)) == 1:
            out.append({"id": "stale-plugin-version", "skill": имя,
                        "sources": места,
                        "why": "в кэше лежит больше одной версии одной пачки — "
                               "какая подхватится, зависит от порядка чтения"})
        else:
            out.append({"id": "skill-name-collision", "skill": имя,
                        "sources": места,
                        "why": "одну просьбу заявляют двое: какой сработает — "
                               "не определено"})
    return out


def axis_health(settings: dict | None = None,
                skill_roots: list | None = None) -> list[dict]:
    """Живо ли то, что установлено: хуки, MCP, непересекающиеся имена скиллов.

    Настройки можно подать снаружи — иначе проверка читала бы только реальный
    ~/.claude и измеряла бы состав чужого компьютера, а не собственный код.
    """
    out = []
    s = settings if settings is not None else (read_json(CLAUDE / "settings.json") or {})

    for событие, записи in (s.get("hooks") or {}).items():
        for запись in (записи if isinstance(записи, list) else []):
            for h in (запись.get("hooks") or []):
                cmd = str(h.get("command", ""))
                файлы = re.findall(r"[\w./$-]+\.(?:sh|py)", cmd)
                for f in файлы:
                    if "$" in f:
                        continue
                    if not Path(f).expanduser().exists():
                        out.append({"id": "hook-points-nowhere", "event": событие,
                                    "file": f,
                                    "why": "хук объявлен, а файла нет — он падает "
                                           "каждый ход и молча"})

    for имя, сервер in (s.get("mcpServers") or {}).items():
        if not isinstance(сервер, dict):
            continue
        ок = _runnable(str(сервер.get("command", "")))
        if ок is False:
            out.append({"id": "mcp-not-runnable", "server": имя,
                        "command": сервер.get("command"),
                        "why": "исполнителя нет ни в PATH, ни на диске — сервер "
                               "не поднимется, а выглядит подключённым"})

    out += trigger_collisions(skill_roots)
    return out


# ---------------------------------------------------------------- ОСЬ F
#: Разрешения, которые звучат узко и означают «исполняй что угодно». Каждое
#: названо вместе с тем, что оно РЕАЛЬНО даёт: `Bash(node *)` читается как
#: «работа с node», а значит «выполни любой код на этой машине».
ШИРОКИЕ = {
    "Bash(node *)": "любой код в node",
    "Bash(python *)": "любой код на python",
    "Bash(python3 *)": "любой код на python",
    "Bash(sh *)": "любую команду оболочки",
    "Bash(bash *)": "любую команду оболочки",
    "Bash(npm *)": "любой скрипт пакета, включая postinstall",
    "Bash(npx *)": "запуск любого пакета из сети",
    "Bash(curl *)": "скачивание чего угодно откуда угодно",
    "Bash(wget *)": "скачивание чего угодно откуда угодно",
    "Bash(docker *)": "контейнер с любыми правами и монтированием диска",
}

#: Признаки «дай мне всё» в аргументах MCP-сервера.
ВСЕ_ПРАВА = ("--caps all", "--allow-all", "--dangerously", "--yolo",
             "--no-sandbox", "--full-access")


def axis_config_security(settings: dict | None = None,
                         skills_root: Path | None = None) -> list[dict]:
    """Опасно ли настроено — сверх секретов, которые ищет отдельная проба."""
    out = []
    s = settings if settings is not None else (read_json(CLAUDE / "settings.json") or {})

    allow = ((s.get("permissions") or {}).get("allow") or [])
    for правило in (allow if isinstance(allow, list) else []):
        что = ШИРОКИЕ.get(str(правило).strip())
        if что:
            out.append({"id": "permission-too-wide", "rule": правило,
                        "grants": что,
                        "why": f"звучит узко, а разрешает {что}"})

    for имя, сервер in (s.get("mcpServers") or {}).items():
        if not isinstance(сервер, dict):
            continue
        строка = " ".join(str(a) for a in (сервер.get("args") or []))
        for признак in ВСЕ_ПРАВА:
            if признак in строка:
                out.append({"id": "mcp-all-caps", "server": имя, "flag": признак,
                            "why": "сервер запрашивает все права разом — сузить "
                                   "до того, что ему нужно"})
                break

    корень = skills_root if skills_root is not None else (CLAUDE / "skills")
    if корень.is_dir():
        for f in sorted(корень.glob("*/SKILL.md")):
            try:
                голова = f.read_text("utf-8", errors="replace")[:800]
            except OSError:
                continue
            if re.search(r"(?m)^(?:command|exec|preload|onLoad|setup):", голова):
                out.append({"id": "skill-runs-shell-on-load", "skill": f.parent.name,
                            "file": str(f),
                            "why": "скилл выполняет команду при загрузке — код "
                                   "исполняется раньше, чем человек прочитал, что это"})
    return out



def main() -> None:
    _utf8_stdio()
    halt_if_paused()
    ver, how = active_version()
    report = {
        "active_version": ver,
        "active_version_source": how,
        "upstream": axis_upstream(),
        "supersession": axis_supersession(ver),
        "drift": axis_drift(),
        "self": axis_self(),
        "health": axis_health(),
        "config_security": axis_config_security(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2) if AS_JSON else render(report))


if __name__ == "__main__":
    main()
