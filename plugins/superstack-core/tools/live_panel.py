#!/usr/bin/env python3
"""SUPERSTACK — живая панель хода: где мы сейчас и на ком ход.

Зачем отдельно от отчёта.

Отчёт отвечает на вопрос «что получилось» и собирается один раз. Панель
отвечает на другой — «где я и чего от меня ждут», — и нужна ПОСТОЯННО. Половина
ожиданий в прогоне это не «долго считает», а «каждый ждёт другого»: человек
ждёт систему, система ждёт человека, и оба молчат. На панели это видно сразу:
ход стоит на человеке сорок минут.

Как устроено обновление, и почему именно так.

Страница читает `state.json` сама, каждые несколько секунд. Значит обновление
состояния стоит ОДНОЙ ЗАПИСИ JSON — тем же `progress.py`, который его и ведёт;
HTML не перегенерируется и в контекст модели не попадает. Перерисовывать
страницу на каждое изменение значило бы платить за неё токенами по десять раз
за фазу — и её перестали бы обновлять, то есть она врала бы про «сейчас».

Разметка строится методами DOM, а не склейкой строк. Данные тут свои, но
правило дешевле соблюдать всегда, чем помнить, где можно нарушить: в состояние
попадают имена задач и деталей, которые пишет модель, и однажды туда приедет
угловая скобка.

Файл открывается в браузерной панели рядом с разговором и живёт там весь
прогон. Ему нужен http, а не file:// — иначе браузер запретит читать соседний
JSON; отдаётся любым статическим сервером из каталога прогона.

  python3 live_panel.py <каталог .superstack>     -> пишет panel.html
  python3 live_panel.py <каталог> --serve [порт]  -> и поднимает сервер

  код 0 — панель записана, 2 — нечего показывать, 3 — ошибка вызова
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Дизайн-система берётся из ОДНОГО места — того же, что рисует отчёт и план.
# Своя палитра в панели уже случилась: фиолетовый акцент, зелёная полоса,
# жёлтое предупреждение — при том что в системе цветных акцентов НЕТ ВОВСЕ, и
# это записано в ней явно. Панель выглядела чужой страницей того же продукта.
_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
from render_html import CSS as SYSTEM_CSS, BRAND  # noqa: E402
import plain_ru  # noqa: E402
import derive_phase  # noqa: E402

PANEL = "panel.html"
DEFAULT_PORT = 8787

#: Фазы прогона в порядке, в котором они идут. Панель показывает ВСЕ, а не
#: только текущую: человек, видящий одну строку «идёт фаза 3», не знает ни
#: сколько позади, ни сколько впереди, и любая пауза читается как поломка.
#: Фазы прогона в порядке, в котором они идут — НАЗВАННЫЕ ТАК, КАК ИХ ВИДИТ
#: ЧЕЛОВЕК. Первая редакция звалась «завести · понять · спека · направление»:
#: это слова конструкции, а не работы, и человеку они не говорят ничего.
#: «Направление» вдобавок врало — на этой фазе делают дизайн-систему и дизайн,
#: а слово обещало выбор из списка.
#:
#: Панель показывает ВСЕ фазы, а не только текущую: человек, видящий одну
#: строку, не знает ни сколько позади, ни сколько впереди, и любая пауза
#: читается как поломка.
PHASES = (
    "Записали просьбу",
    "Разобрали задачу",
    "Описали, что строим",
    "Дизайн-система",
    "Дизайн экранов",
    "План работ",
    "Пишем код",
    "Проверяем",
    "Приёмка",
    "Отчёт",
)

#: После скольких минут ожидание на человеке перестаёт быть «идёт» и начинает
#: быть «забыли». Двадцать — не измерение, а выбор: меньше даёт ложную тревогу
#: на обычной паузе, больше не спасает от вечера, потерянного на взаимном
#: ожидании.
STALE_MINUTES = 20

#: Этап, на котором пишут код. Названо константой, потому что на него ссылается
#: и `progress.py`: переход задачи в работу обязан сам двигать этап сюда.
#: Совпадение двух написаний проверяется тестом — разошлись бы они молча.
BUILD_PHASE = "Пишем код"

_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Ход работы — __BRAND__</title>
<style>__CSS__
/* --- панель хода -------------------------------------------------------- */
/* Состояние передаётся ВЕСОМ и ПРОЗРАЧНОСТЬЮ, как во всей системе: цветных
   акцентов в ней нет вовсе. Здесь это помогает — монохром не даёт покрасить
   «заявлено» и «доказано» одинаково зелёным. */
.panel { padding: 1.6rem 1.4rem 2.4rem; max-width: 30rem; margin: 0 auto; }
.now { border: 1px solid var(--border); border-radius: var(--radius);
       background: var(--glass); padding: 1.2rem 1.3rem; margin: .9rem 0 1.6rem;
       box-shadow: var(--shadow-subtle); }
.who { display: inline-block; padding: .18rem .7rem; border-radius: 999px;
       font-family: var(--font-mono); font-size: .62rem; letter-spacing: .16em;
       text-transform: uppercase; border: 1px solid var(--border);
       color: var(--muted); }
/* Ход на человеке — единственное состояние, которое переворачивает блок:
   белым по чёрному нельзя не заметить, и это дешевле любого цвета. */
.who[data-owner="человек"] { background: var(--primary); color: #000;
                             border-color: var(--primary); font-weight: 600; }
.phase-name { font-family: var(--font-head); font-size: 1.35rem;
              letter-spacing: -.02em; margin: .7rem 0 .2rem; }
/* Подстрочник — «что это значит», человеческими словами. Без него название
   этапа сообщает ровно столько, сколько человек уже знает: «Пишем код» и
   «Приёмка» одинаково непрозрачны тому, кто не пишет код. */
.means { color: var(--secondary); font-size: var(--step--1); margin: .1rem 0 .5rem; }
.detail { color: var(--muted); font-size: var(--step--1); }
.since { font-family: var(--font-mono); font-size: .68rem; letter-spacing: .08em;
         color: var(--muted); margin-top: .5rem; }
.since.stale { color: var(--primary); }
.since.stale::after { content: "__WAIT_LONG__"; color: var(--muted); }
ol.phases { list-style: none; margin: 0; padding: 0; }
ol.phases li { display: flex; align-items: center; gap: .75rem;
               padding: .42rem 0; color: var(--faint);
               font-size: .95rem; }
ol.phases li[data-state="done"] { color: var(--secondary); }
ol.phases li[data-state="now"] { color: var(--primary); font-weight: 600; }
/* Отметки крупные намеренно: их читают краем глаза, не вчитываясь, и мелкая
   галочка в 0.7rem проигрывала соседнему тексту — взгляд считывал строки, а не
   пройденность. Ширина фиксирована, чтобы названия стояли в одну колонку. */
.tick { font-family: var(--font-mono); font-size: 1.05rem; line-height: 1;
        width: 1.6rem; flex: none; text-align: center; color: var(--faint); }
li[data-state="done"] .tick { color: var(--secondary); }
li[data-state="now"] .tick { color: var(--primary); font-size: 1.3rem; }
.tasks { margin-top: 1.8rem; }
.tasks .rub { font-family: var(--font-mono); font-size: .62rem;
              letter-spacing: .18em; text-transform: uppercase;
              color: var(--muted); }
.track { height: 1.6rem; border-radius: .5rem; overflow: hidden; margin: .5rem 0;
         background: rgba(255,255,255,.04);
         border: 1px solid var(--border-subtle); position: relative; }
.track i { position: absolute; inset: 0 auto 0 0; background: var(--primary);
           width: 0; }
.track b { position: absolute; inset: 0; display: flex; align-items: center;
           padding: 0 .6rem; font-family: var(--font-mono); font-size: .65rem;
           letter-spacing: .1em; color: var(--secondary); mix-blend-mode: difference; }
.foot { color: var(--muted); font-size: var(--step--1); margin-top: 1.4rem; }
.eta { color: var(--secondary); font-size: .95rem; margin: .5rem 0 0; }
/* Расхождение панели с работой. Показывается словами, а не прячется: панель,
   молча показывающая устаревший этап, хуже отсутствующей — ей верят. */
.warn { border: 1px solid var(--border); border-left-width: 3px;
        border-radius: var(--radius); padding: .7rem .9rem; margin: 1rem 0 0;
        color: var(--secondary); font-size: var(--step--1); }
</style></head>
<body>
<div class="panel">
  <p class="eyebrow">__BRAND__ · __TITLE__</p>
  <div class="now">
    <span class="who" id="who"></span>
    <div class="phase-name" id="phase"></div>
    <div class="means" id="means"></div>
    <div class="detail" id="detail"></div>
    <div class="detail" id="why"></div>
    <div class="since" id="since"></div>
  </div>
  <ol class="phases" id="phases"></ol>
  <p class="warn" id="mismatch" hidden></p>
  <div class="tasks">
    <p class="rub" id="tasksrub"></p>
    <div class="track"><i id="bar"></i><b id="tasksline"></b></div>
    <p class="foot" id="barnote"></p>
  </div>
  <div class="tasks">
    <p class="rub" id="covrub"></p>
    <p class="eta" id="cov"></p>
    <p class="foot" id="covnote"></p>
  </div>
  <div class="tasks">
    <p class="rub" id="debtrub"></p>
    <p class="eta" id="debt"></p>
  </div>
  <div class="tasks">
    <p class="rub" id="etarub"></p>
    <p class="eta" id="eta"></p>
    <p class="foot" id="etanote"></p>
  </div>
  <p class="foot" id="updated"></p>
</div>
<script>
const PHASES = __PHASES__;
const STALE_MS = __STALE__ * 60000;
// Слова для человека приезжают из общего словаря, а не пишутся здесь. Иначе
// правка формулировки становится правкой кода, и человеку приходится просить
// о ней отдельно — а он и так уже попросил дважды.
const W = __WORDS__;
const BUILD_PHASE = __BUILD_PHASE__;
const $ = (id) => document.getElementById(id);

function ago(iso) {
  if (!iso) return '';
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  // «меньше минуты», а не «только что»: строка склеивается и с «идёт уже», и с
  // «обновилось … назад», и «обновилось только что назад» читается как брак.
  if (m < 1) return 'меньше минуты';
  if (m < 60) return m + ' мин';
  return Math.floor(m / 60) + ' ч ' + (m % 60) + ' мин';
}

function minutes(ms) {
  const m = Math.round(ms / 60000);
  if (m < 60) return m + ' мин';
  return Math.floor(m / 60) + ' ч ' + (m % 60 ? (m % 60) + ' мин' : '');
}

// «Сколько ещё» считается ТОЛЬКО по замеренному: сколько заняли части, которые
// уже закончились. Середина ряда, а не среднее — одна застрявшая часть не
// должна растягивать оценку на всё остальное. Замеров нет — оценки нет, и об
// этом говорится прямо: выдуманный срок хуже честного «не знаю», потому что
// ему верят.
function drawEta(tasks) {
  $('etarub').textContent = W.copy.eta_rubric;
  $('etanote').textContent = '';
  const spans = tasks
    .filter(t => t.started && t.finished)
    .map(t => new Date(t.finished) - new Date(t.started))
    .filter(ms => ms > 0)
    .sort((a, b) => a - b);
  const left = tasks.filter(t => t.status === 'running' ||
                                 t.status === 'waiting').length;
  if (!left) { $('eta').textContent = W.copy.eta_done; return; }
  if (!spans.length) { $('eta').textContent = W.copy.eta_none; return; }
  const typical = spans[Math.floor(spans.length / 2)];
  $('eta').textContent = W.copy.eta_known
    .replace('{typical}', minutes(typical))
    .replace('{left}', left)
    .replace('{rest}', minutes(typical * left));
  $('etanote').textContent = W.copy.eta_note;
}

// Покрытие просьбы — ДРУГОЕ число, чем полоса частей работы, и показывать одно
// без другого опасно: части можно закрыть все и при этом упустить требование.
// Ровно это и случилось сегодня — страница вышла точно по системе, а цена и
// «что входит» пропали. Взято из autopilot (nick-vels), где покрытие брифа
// стоит отдельной карточкой; у нас оно считается давно, но панель молчала.
function drawCoverage(s) {
  $('covrub').textContent = W.copy.coverage_rubric;
  const R = s.requirements || {};
  if (R.total === null || R.total === undefined ||
      R.covered === null || R.covered === undefined) {
    $('cov').textContent = W.copy.coverage_none;
    $('covnote').textContent = '';
    return;
  }
  const live = Math.max(0, (R.total || 0) - (R.dropped || 0));
  $('cov').textContent = W.copy.coverage_known
    .replace('{done}', R.covered).replace('{live}', live);
  $('covnote').textContent = W.copy.coverage_note;
}

// Долг — единственный блок, отвечающий на вопрос «что нужно ОТ МЕНЯ». Он
// считался и не показывался: заглушки и переменные окружения копятся молча,
// а всплывают в тот день, когда человек пытается этим пользоваться.
function drawDebt(s) {
  $('debtrub').textContent = W.copy.debt_rubric;
  const d = s.debt || {}, seen = s.debt_reviewed || {};
  const n = (k) => (d[k] || []).length;
  const total = n('stub') + n('assumption') + n('env');
  const checked = ['stub', 'assumption', 'env'].every(k => seen[k]);
  if (!total) {
    $('debt').textContent = checked ? W.copy.debt_clean : W.copy.debt_unchecked;
    return;
  }
  $('debt').textContent = W.copy.debt_line
    .replace('{stub}', n('stub'))
    .replace('{assumption}', n('assumption'))
    .replace('{env}', n('env'));
}

function drawPhases(current) {
  const list = $('phases');
  list.replaceChildren();
  const at = PHASES.indexOf(current || '');
  PHASES.forEach((name, i) => {
    const li = document.createElement('li');
    li.dataset.state = at < 0 ? '' : (i < at ? 'done' : (i === at ? 'now' : ''));
    const tick = document.createElement('span');
    tick.className = 'tick';
    tick.textContent = at < 0 ? '·' : (i < at ? '✓' : (i === at ? '▸' : '·'));
    li.append(tick, document.createTextNode(name));
    list.append(li);
  });
}

async function tick() {
  let s;
  try { s = await (await fetch('state.json?' + Date.now())).json(); }
  catch (e) { $('updated').textContent = W.copy.unreadable; return; }

  const ph = s.phase || {};
  $('who').textContent = ph.owner ? (W.roles[ph.owner] || ph.owner)
                                  : W.copy.turn_unknown;
  $('who').dataset.owner = ph.owner || '';
  $('phase').textContent = ph.name || W.copy.not_started;
  $('means').textContent = W.phases[ph.name] || '';
  $('detail').textContent = ph.detail || '';
  // Основание вывода. Панель, объявляющая этап без причины, — оракул: угадала
  // или нет, проверить нечем. С причиной человек видит, из чего это следует.
  $('why').textContent = ph.why ? W.copy.why_prefix + ' ' + ph.why : '';
  $('since').textContent = ph.since ? W.copy.here_for + ' ' + ago(ph.since) : '';
  // Долгое ожидание на человеке — не «идёт», а «забыли». В монохроме это
  // делается яркостью: строка становится белой вместо приглушённой.
  $('since').className = 'since' + (ph.owner === 'человек' && ph.since &&
    (Date.now() - new Date(ph.since)) > STALE_MS ? ' stale' : '');

  drawPhases(ph.name);

  const tasks = Object.values(s.waves || {}).flat();
  const proven = tasks.filter(t => t.status === 'proven').length;
  const claimed = tasks.filter(t => t.status === 'claimed').length;
  const running = tasks.filter(t => t.status === 'running').length;

  // Панель обязана сама заметить, что разошлась с работой: код пишут, а этап
  // показан другой. Ровно это и случилось 17.08 — этап остался на дизайне,
  // пока помощник уже писал страницу, и панель уверенно показывала неправду.
  const off = running > 0 && ph.name && ph.name !== BUILD_PHASE;
  $('mismatch').hidden = !off;
  $('mismatch').textContent = off ? W.copy.mismatch : '';

  $('tasksrub').textContent = W.copy.tasks_rubric;
  $('barnote').textContent = W.copy.bar_note;
  $('tasksline').textContent = tasks.length
    ? W.copy.counts.replace('{proven}', proven)
        .replace('{claimed}', claimed).replace('{total}', tasks.length)
    : W.copy.no_tasks;
  $('bar').style.width =
    tasks.length ? Math.round(100 * proven / tasks.length) + '%' : '0';
  drawCoverage(s);
  drawDebt(s);
  drawEta(tasks);
  $('updated').textContent =
    s.updated ? W.copy.updated.replace('{ago}', ago(s.updated)) : '';
}
tick(); setInterval(tick, 3000);
</script>
</body></html>
"""


def state_with_phase(run_dir: Path) -> dict:
    """Состояние прогона с этапом, вычисленным по следам на диске.

    Записанный этап остаётся, но уходит на второй план: из него берётся только
    подпись, и только когда записанное и вычисленное согласны. Подпись от
    ДРУГОГО этапа — самая тихая форма вранья: заголовок верный, объяснение
    под ним от прошлого часа, и заметить это нельзя.
    """
    state = derive_phase.read_state(run_dir)
    got = derive_phase.derive(run_dir)
    was = state.get("phase") or {}
    same = was.get("name") == got["name"]
    state["phase"] = {
        "name": got["name"],
        "owner": got["owner"],
        "detail": was.get("detail", "") if same else "",
        "why": got["why"],
        # Отметка времени сохраняется, пока этап тот же: иначе «идёт уже»
        # обнулялось бы каждые три секунды и застрявший этап выглядел бы
        # вечно свежим — то есть ровно наоборот.
        "since": was.get("since") if same else None,
    }
    return state


def write_panel(run_dir: Path) -> Path:
    """Собрать страницу. Слова — из словаря, ни одного текста здесь.

    Словарь вшивается при сборке, а состояние читается на лету: правка
    формулировки — это правка одного JSON и одна пересборка, а не выпуск новой
    версии продукта.
    """
    words = plain_ru.load()
    p = run_dir / PANEL
    p.write_text(_HTML
                 .replace("__CSS__", SYSTEM_CSS)
                 .replace("__BRAND__", BRAND)
                 .replace("__PHASES__", json.dumps(PHASES, ensure_ascii=False))
                 .replace("__STALE__", str(STALE_MINUTES))
                 .replace("__WAIT_LONG__", words["copy"]["map"]["waiting_long"])
                 .replace("__TITLE__", words["copy"]["map"]["title"])
                 .replace("__BUILD_PHASE__",
                          json.dumps(BUILD_PHASE, ensure_ascii=False))
                 .replace("__WORDS__", json.dumps({
                     "roles": words["roles"]["map"],
                     "phases": words["phases"]["map"],
                     "statuses": words["statuses"]["map"],
                     "copy": words["copy"]["map"],
                 }, ensure_ascii=False)),
                 encoding="utf-8")
    return p


def halt_if_paused() -> None:
    if os.environ.get("SUPERSTACK_IGNORE_PAUSE") == "1":
        return
    if (Path.home() / ".claude" / "superstack" / "PAUSE").exists():
        print("ОСТАНОВЛЕНО: система на паузе", file=sys.stderr)
        raise SystemExit(10)


def main() -> int:
    halt_if_paused()
    argv = sys.argv[1:]
    plain = [a for a in argv if not a.startswith("--")]
    if not plain:
        print("вызов: live_panel.py <каталог .superstack> [--serve [порт]]",
              file=sys.stderr)
        return 3
    run_dir = Path(plain[0]).resolve()
    if not run_dir.is_dir():
        print(f"НЕ УДАЛОСЬ: нет каталога {run_dir}", file=sys.stderr)
        return 3
    if not (run_dir / "state.json").is_file():
        # Панель без состояния показала бы пустые полосы — то есть «ничего не
        # происходит» вместо «нечего показывать». Это разные утверждения.
        print("НЕ УДАЛОСЬ: нет state.json — панели нечего читать", file=sys.stderr)
        return 2

    p = write_panel(run_dir)
    port = int(plain[1]) if len(plain) > 1 else DEFAULT_PORT
    print(f"панель: {p}", file=sys.stderr)
    print(f"открыть: http://localhost:{port}/{PANEL}", file=sys.stderr)
    print(json.dumps({"panel": str(p), "url": f"http://localhost:{port}/{PANEL}"},
                     ensure_ascii=False))

    if "--serve" in argv:
        import functools
        import http.server
        import socketserver

        class Fresh(http.server.SimpleHTTPRequestHandler):
            """Отдаёт всегда свежее — и этап считает сам.

            Две вещи, каждая из живого прогона.

            Первая: обычный статический сервер отвечает «не менялось» по дате
            файла, и браузер оставляет прежнюю страницу. Проверено вживую —
            панель на диске уже новая, сервер отдаёт новую, а на экране
            прежняя. Человек смотрит на устаревшее и не может об этом узнать.

            Вторая, и она важнее: этап раньше приходил из записи, которую
            делают отдельной командой. Команду забывали — за один вечер
            дважды, — и панель уверенно показывала не тот этап. Здесь состояние
            отдаётся с этапом, ВЫЧИСЛЕННЫМ по следам на диске: панель
            переключается сама, без чьей-либо дисциплины.
            """

            def end_headers(self):
                self.send_header("Cache-Control", "no-store, must-revalidate")
                super().end_headers()

            def do_GET(self):                                  # noqa: N802
                if self.path.split("?")[0].rstrip("/") not in ("/state.json",):
                    return super().do_GET()
                body = json.dumps(state_with_phase(run_dir),
                                  ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # тишина: опрос идёт раз в 3 секунды
                pass

        handler = functools.partial(Fresh, directory=str(run_dir))
        # allow_reuse_address: панель перезапускают часто, и «порт занят» после
        # каждого перезапуска сделало бы её неудобной ровно настолько, чтобы
        # ею перестали пользоваться.
        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
            httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
