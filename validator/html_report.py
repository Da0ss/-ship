"""Interactive HTML report: the document on the left, findings as margin notes on the right.

One self-contained file (no server, no build). Built from the JSON export plus the document
lines, so it can be rebuilt from a saved run:

    python -m validator.html_report reports/report.json docs/requirements.md reports/report.html
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .reader import read_document


def _payload(report: dict, lines: list[tuple[int, str, int | None]]) -> str:
    data = {"report": report, "lines": [[n, t, p] for n, t, p in lines]}
    # Safe inside <script>: no "</" sequence can close the tag.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_html(report: dict, lines: list[tuple[int, str, int | None]]) -> str:
    title = (report.get("passport") or {}).get("document_title") or "Отчёт проверки требований"
    return (_TEMPLATE
            .replace("__TITLE__", _esc(title))
            .replace("__DATA__", _payload(report, lines)))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write_html(report: dict, document_path: str, out_path: str) -> None:
    doc = read_document(document_path)
    lines = [(l.no, l.text, l.page) for l in doc.lines]
    Path(out_path).write_text(render_html(report, lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m validator.html_report",
                                description="HTML-отчёт из JSON-отчёта и исходного документа")
    p.add_argument("json_report")
    p.add_argument("document")
    p.add_argument("output")
    a = p.parse_args(argv)
    report = json.loads(Path(a.json_report).read_text(encoding="utf-8"))
    write_html(report, a.document, a.output)
    print(f"HTML: {a.output}", file=sys.stderr)
    return 0


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — проверка требований</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=PT+Sans:wght@400;700&family=PT+Serif:wght@400;700&family=PT+Mono&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#FBFCFD; --desk:#EEF1F5; --ink:#1C2430; --slate:#5B6878; --rule:#D9DEE5;
  --crit:#B4233C; --crit-bg:#FBE9EC; --major:#A86400; --major-bg:#FDF2E0;
  --minor:#3C6E9F; --minor-bg:#E8F0F8; --bank:#0F7B6C; --bank-bg:#E2F3F0;
  --sans:"PT Sans","Segoe UI",system-ui,sans-serif; --serif:"PT Serif",Georgia,serif;
  --mono:"PT Mono",Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font:15px/1.5 var(--sans);color:var(--ink);background:var(--desk);display:flex;flex-direction:column}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--minor);outline-offset:2px}

header.top{background:var(--paper);border-bottom:1px solid var(--rule);padding:14px 24px 0}
.top h1{font:700 22px/1.25 var(--serif);margin:0 0 6px;max-width:60ch}
.verdict{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 18px;margin:0 0 10px}
.verdict .state{font-weight:700;padding:2px 10px;border-radius:3px;color:#fff;background:var(--crit)}
.verdict .state.ok{background:var(--bank)}
.verdict .sum{color:var(--slate);max-width:90ch}
.tally{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 10px;color:var(--slate)}
.tally b{color:var(--ink);font-size:18px}
.tally .c b{color:var(--crit)} .tally .m b{color:var(--major)} .tally .n b{color:var(--minor)} .tally .k b{color:var(--bank)}
.meta{font-size:13px;color:var(--slate);margin-bottom:8px;display:flex;flex-wrap:wrap;gap:2px 16px}
nav.tabs{display:flex;gap:2px;flex-wrap:wrap}
nav.tabs button{background:none;border:0;border-bottom:3px solid transparent;padding:8px 12px;cursor:pointer;color:var(--slate)}
nav.tabs button[aria-selected=true]{color:var(--ink);border-color:var(--ink);font-weight:700}
nav.tabs .cnt{font-size:12px;color:var(--slate);margin-left:4px}

main{flex:1;min-height:0;display:flex}
.view{display:none;flex:1;min-height:0}
.view.on{display:flex}

/* document + notes */
#v-notes{gap:0}
.docwrap{position:relative;flex:1 1 58%;min-width:0;display:flex}
.minimap{width:14px;flex:none;position:relative;background:var(--paper);border-right:1px solid var(--rule);cursor:pointer}
.minimap i{position:absolute;left:2px;right:2px;height:3px;border-radius:1px}
.minimap .eye{position:absolute;left:0;right:0;border:1px solid var(--slate);background:rgba(91,104,120,.08);pointer-events:none}
.doc{flex:1;overflow:auto;background:var(--paper);padding:18px 0 40vh}
.ln{display:grid;grid-template-columns:52px 6px 1fr;align-items:start;font:15px/1.6 var(--serif);border-left:0}
.ln .no{font:12px/2 var(--sans);color:#9AA5B3;text-align:right;padding-right:10px;user-select:none}
.ln .bar{align-self:stretch}
.ln .tx{padding:0 28px 0 12px;white-space:pre-wrap;overflow-wrap:anywhere;max-width:88ch}
.ln.h .tx{font-weight:700;font-size:17px;padding-top:6px}
.ln.h1 .tx{font-size:21px}
.ln.t .tx{font:14px/1.5 var(--sans);color:#303A47;display:flex;flex-wrap:wrap;gap:0}
.ln.t .cell{flex:1 1 0;min-width:90px;padding:3px 10px 3px 0;border-bottom:1px solid #EEF1F5}
.ln.t .cell:first-child{flex:0 1 220px;color:var(--slate)}
.ln.sep .tx{height:1px;background:var(--rule);margin:2px 28px 2px 12px;padding:0}
.ln.sep .no{line-height:1;font-size:10px}
.tx code{font:13px var(--mono);background:var(--desk);padding:0 3px;border-radius:2px}
.ln.mk{cursor:pointer}
.ln.mk.critical .bar{background:var(--crit)} .ln.mk.critical .tx{background:var(--crit-bg)}
.ln.mk.major .bar{background:var(--major)} .ln.mk.major .tx{background:var(--major-bg)}
.ln.mk.minor .bar{background:var(--minor)} .ln.mk.minor .tx{background:var(--minor-bg)}
.ln.mk .no{color:var(--ink);font-weight:700}
.ln.flash .tx{box-shadow:inset 0 0 0 2px var(--ink)}
.ln.pick .tx{box-shadow:inset 0 0 0 2px var(--minor)}

.notes{flex:1 1 42%;min-width:340px;max-width:620px;display:flex;flex-direction:column;border-left:1px solid var(--rule);background:var(--desk)}
.filters{padding:10px 14px;display:flex;flex-wrap:wrap;gap:6px;align-items:center;border-bottom:1px solid var(--rule);background:var(--paper)}
.chip{border:1px solid var(--rule);background:var(--paper);border-radius:14px;padding:2px 10px;font-size:13px;cursor:pointer}
.chip[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
.filters input{flex:1 1 140px;min-width:120px;border:1px solid var(--rule);border-radius:4px;padding:4px 8px;font:inherit;font-size:13px}
.pickinfo{width:100%;font-size:13px;color:var(--slate);display:none}
.pickinfo.on{display:block}
.pickinfo button{border:0;background:none;color:var(--minor);text-decoration:underline;cursor:pointer;padding:0}
.list{overflow:auto;padding:10px 14px 40vh;flex:1}
.note{background:var(--paper);border:1px solid var(--rule);border-left-width:4px;border-radius:3px;padding:10px 12px;margin:0 0 10px;cursor:pointer}
.note.critical{border-left-color:var(--crit)} .note.major{border-left-color:var(--major)} .note.minor{border-left-color:var(--minor)}
.note.sel{outline:2px solid var(--ink)}
.note .hd{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:baseline;font-size:13px;margin-bottom:4px}
.note .sev{font-weight:700}
.note.critical .sev{color:var(--crit)} .note.major .sev{color:var(--major)} .note.minor .sev{color:var(--minor)}
.note .kind{font-weight:700}
.tag{font-size:12px;border-radius:3px;padding:0 6px;background:var(--desk);color:var(--slate)}
.tag.bank{background:var(--bank-bg);color:var(--bank);font-weight:700}
.tag.q{background:#EFE8F7;color:#5B3A8C}
.note .where{margin-left:auto;color:var(--slate)}
.note p{margin:4px 0}
.note q{font-family:var(--serif);quotes:"«" "»";color:#303A47}
.note .fix{border-top:1px dashed var(--rule);margin-top:6px;padding-top:6px}
.note .fix b{font-weight:700}
.note .opts{list-style:none;margin:4px 0 0;padding-left:4px}
.note .opts li{margin:2px 0;padding-left:22px;text-indent:-22px}
.opt{font-weight:700;display:inline-block;width:22px;text-indent:0}
.empty{color:var(--slate);padding:20px 4px}

/* tables & lists in other tabs */
.page{flex:1;overflow:auto;padding:20px 24px 60px;background:var(--paper)}
.page h2{font:700 19px/1.3 var(--serif);margin:0 0 6px}
.page .lead{color:var(--slate);max-width:90ch;margin:0 0 14px}
table.grid{border-collapse:collapse;width:100%;font-size:14px}
table.grid th{text-align:left;font-weight:700;border-bottom:2px solid var(--ink);padding:6px 8px;vertical-align:bottom}
table.grid td{border-bottom:1px solid var(--rule);padding:7px 8px;vertical-align:top}
.st{font-weight:700;white-space:nowrap}
.st.no{color:var(--crit)} .st.yes{color:var(--bank)} .st.na{color:var(--slate)}
a.goto{color:var(--minor);cursor:pointer;text-decoration:underline;white-space:nowrap}
.qlist{max-width:100ch;padding-left:24px}
.qlist li{margin:0 0 14px}
.qlist ul{margin:4px 0 0;padding-left:20px;list-style:none}
.qlist ul li{margin:2px 0}


@media (max-width:600px){
  header.top{padding:10px 14px 0}
  .verdict .sum{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .tally{gap:4px 12px;font-size:13px} .tally b{font-size:15px}
  .meta{display:none}
  nav.tabs{flex-wrap:nowrap;overflow-x:auto}
  nav.tabs button{white-space:nowrap}
}
@media (max-width:900px){
  #v-notes{flex-direction:column}
  .docwrap{flex:none;height:50vh}
  .notes{max-width:none;min-width:0;border-left:0;border-top:1px solid var(--rule)}
}
@media (prefers-reduced-motion:no-preference){
  .ln .tx{transition:box-shadow .25s}
}
</style>
</head>
<body>
<header class="top">
  <h1 id="title"></h1>
  <p class="verdict"><span class="state" id="state"></span><span class="sum" id="vsum"></span></p>
  <div class="tally" id="tally"></div>
  <div class="meta" id="meta"></div>
  <nav class="tabs" role="tablist" id="tabs"></nav>
</header>
<main>
  <section class="view on" id="v-notes" role="tabpanel">
    <div class="docwrap">
      <div class="minimap" id="minimap" title="Где в документе замечания"><div class="eye" id="eye"></div></div>
      <div class="doc" id="doc"></div>
    </div>
    <aside class="notes">
      <div class="filters" id="filters">
        <button class="chip" data-sev="critical" aria-pressed="true">Критичные</button>
        <button class="chip" data-sev="major" aria-pressed="true">Существенные</button>
        <button class="chip" data-sev="minor" aria-pressed="true">Незначительные</button>
        <button class="chip" data-src="bank" aria-pressed="false">Только банк и ИБ</button>
        <button class="chip" data-src="q" aria-pressed="false">Только вопросы</button>
        <input type="search" id="search" placeholder="Поиск по замечаниям" aria-label="Поиск по замечаниям">
        <div class="pickinfo" id="pickinfo"></div>
      </div>
      <div class="list" id="list"></div>
    </aside>
  </section>
  <section class="view" id="v-check" role="tabpanel"><div class="page" id="p-check"></div></section>
  <section class="view" id="v-q" role="tabpanel"><div class="page" id="p-q"></div></section>
  <section class="view" id="v-cov" role="tabpanel"><div class="page" id="p-cov"></div></section>
  <section class="view" id="v-req" role="tabpanel"><div class="page" id="p-req"></div></section>
</main>
<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('data').textContent);
const R = D.report, LINES = D.lines;
const FACT = '[проверка кодом]';
const SEV = {critical:'Критичное', major:'Существенное', minor:'Незначительное'};
const SEV_ORDER = {critical:0, major:1, minor:2};
const DEF = {ambiguity:'Неоднозначность', unverifiable:'Непроверяемость', contradiction:'Противоречие',
  gap:'Пробел', design:'Проектное решение', non_singular:'Составное требование',
  traceability:'Трассируемость', other:'Корректность'};
const $ = s => document.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

// ---- findings ----
const V = [];
(R.checks || []).forEach(c => (c.violations || []).forEach(v => V.push(Object.assign({owner: c.rid}, v))));
((R.set_check || {}).violations || []).forEach(v => V.push(Object.assign({owner: null}, v)));
V.forEach(v => {
  v.code = (v.explanation || '').startsWith(FACT);
  v.text = v.code ? v.explanation.slice(FACT.length).trim() : v.explanation;
  v.lines = [];
  [v.location, v.location2].concat(v.also_at || []).forEach(l => {
    if (l) for (let n = l.line_start; n <= l.line_end; n++) v.lines.push(n);
  });
  v.lines = [...new Set(v.lines)].sort((a, b) => a - b);
  v.ids = [...new Set([v.owner].concat(v.req_ids || []).filter(Boolean))];
});
const grouped = v => (v.also_at || []).length > 3 ? 1 : 0;
V.sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity] || grouped(a) - grouped(b) || (a.lines[0] || 0) - (b.lines[0] || 0));
const byLine = {};
V.forEach(v => v.lines.forEach(n => (byLine[n] = byLine[n] || []).push(v)));

// ---- header ----
const P = R.passport || {}, C = R.counts || {}, VD = R.verdict || {};
$('#title').textContent = P.document_title || 'Отчёт проверки требований';
const ok = VD.ready_for_development;
$('#state').textContent = ok ? 'Можно в разработку' : 'Нельзя в разработку';
if (ok) $('#state').classList.add('ok');
$('#vsum').textContent = VD.summary || '';
const bs = C.by_severity || {};
const bank = V.filter(v => v.tag).length, qs = V.filter(v => v.fix_type === 'question').length;
[['c', bs.critical || 0, 'критичных'], ['m', bs.major || 0, 'существенных'], ['n', bs.minor || 0, 'незначительных'],
 ['k', bank, 'банк и ИБ'], ['', (R.requirements || []).length, 'требований']].forEach(([cls, n, label]) => {
  const s = el('span', cls); s.append(el('b', null, String(n)), document.createTextNode(' ' + label)); $('#tally').append(s);
});
[P.document, P.model && ('Модель: ' + P.model), P.agent_version && ('Версия агента: ' + P.agent_version),
 P.date && ('Проверено: ' + P.date.replace('T', ' ').slice(0, 16))].filter(Boolean).forEach(x => $('#meta').append(el('span', null, x)));

// ---- tabs ----
const TABS = [['v-notes', 'Документ и замечания', V.length], ['v-check', 'Чек-лист ревью', ((R.checklist || {}).items || []).length],
  ['v-q', 'Вопросы владельцу', qs], ['v-cov', 'Нет критериев приёмки', (R.coverage_gaps || []).length],
  ['v-req', 'Требования', (R.requirements || []).length]];
TABS.forEach(([id, label, n], i) => {
  if (i > 0 && !n) return;
  const b = el('button', null, label); b.setAttribute('role', 'tab'); b.dataset.view = id;
  b.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
  b.append(el('span', 'cnt', String(n)));
  b.onclick = () => showTab(id);
  $('#tabs').append(b);
});
function showTab(id){
  document.querySelectorAll('.view').forEach(s => s.classList.toggle('on', s.id === id));
  document.querySelectorAll('#tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.view === id ? 'true' : 'false'));
}

function rich(t){
  // **bold** and `code` from Markdown; everything else stays plain text.
  const f = document.createDocumentFragment();
  t.split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach(part => {
    if (/^\*\*[^*]+\*\*$/.test(part)) f.append(el('b', null, part.slice(2, -2)));
    else if (/^`[^`]+`$/.test(part)) f.append(el('code', null, part.slice(1, -1)));
    else if (part) f.append(document.createTextNode(part));
  });
  return f;
}

// ---- document ----
const doc = $('#doc'), lineEl = {};
LINES.forEach(([n, t]) => {
  const row = el('div', 'ln'); row.dataset.no = n;
  const h = /^(#+)\s/.exec(t);
  const isSep = /^\s*\|[\s|:\-]+\|?\s*$/.test(t) && t.includes('-');
  if (h) row.classList.add('h', 'h' + h[1].length);
  else if (isSep) row.classList.add('sep');
  else if (/^\s*\|/.test(t)) row.classList.add('t');
  const vs = byLine[n];
  if (vs) {
    row.classList.add('mk', vs.reduce((a, v) => SEV_ORDER[v.severity] < SEV_ORDER[a] ? v.severity : a, 'minor'));
    row.title = vs.length + ' замеч. — нажмите, чтобы показать только их';
    row.tabIndex = 0;
    row.onclick = () => pickLine(n);
    row.onkeydown = e => { if (e.key === 'Enter') pickLine(n); };
  }
  const tx = el('span', 'tx');
  if (row.classList.contains('t')) {
    t.trim().replace(/^\|/, '').replace(/\|$/, '').split(/(?<!\\)\|/).forEach(c => { const cell = el('span', 'cell'); cell.append(rich(c.trim().replace(/\\\|/g, '|'))); tx.append(cell); });
  } else if (!isSep) tx.append(rich(h ? t.slice(h[0].length) : (t || ' ')));
  row.append(el('span', 'no', String(n)), el('span', 'bar'), tx);
  doc.append(row); lineEl[n] = row;
});

// ---- minimap ----
const mm = $('#minimap'), total = LINES.length || 1;
Object.keys(byLine).forEach(n => {
  const sev = lineEl[n].classList.contains('critical') ? 'crit' : lineEl[n].classList.contains('major') ? 'major' : 'minor';
  const i = el('i'); i.style.top = ((n - 1) / total * 100) + '%'; i.style.background = 'var(--' + sev + ')'; mm.append(i);
});
function eye(){
  const e = $('#eye'), h = doc.scrollHeight || 1;
  e.style.top = (doc.scrollTop / h * 100) + '%'; e.style.height = (doc.clientHeight / h * 100) + '%';
}
doc.addEventListener('scroll', eye); window.addEventListener('resize', eye);
mm.onclick = e => { const r = mm.getBoundingClientRect(); doc.scrollTop = (e.clientY - r.top) / r.height * doc.scrollHeight - doc.clientHeight / 2; };

// ---- notes ----
const state = {sev: {critical: true, major: true, minor: true}, bank: false, q: false, text: '', line: null, sel: null};
function where(v){
  if (!v.lines.length) return 'место не найдено';
  const ls = v.lines.length > 4 ? v.lines.slice(0, 4).join(', ') + ' …' : v.lines.join(', ');
  return (v.lines.length === 1 ? 'строка ' : 'строки ') + ls;
}
function note(v){
  const n = el('article', 'note ' + v.severity); n.tabIndex = 0;
  const hd = el('div', 'hd');
  hd.append(el('span', 'sev', SEV[v.severity] || v.severity), el('span', 'kind', DEF[v.defect] || v.defect));
  if (v.tag === 'fintech') hd.append(el('span', 'tag bank', 'банковская специфика'));
  if (v.tag === 'security') hd.append(el('span', 'tag bank', 'информационная безопасность'));
  if (v.code) hd.append(el('span', 'tag', 'проверено кодом'));
  if (v.fix_type === 'question') hd.append(el('span', 'tag q', 'нужно решение'));
  if (v.ids.length) hd.append(el('span', 'tag', v.ids.slice(0, 4).join(', ') + (v.ids.length > 4 ? ' …' : '')));
  hd.append(el('span', 'where', where(v)));
  n.append(hd, el('p', null, v.text));
  const q = el('p'); q.append(el('q', null, v.quote)); n.append(q);
  if (v.quote2) { const q2 = el('p'); q2.append(el('q', null, v.quote2)); n.append(q2); }
  const fx = el('div', 'fix');
  if (v.fix_type === 'question') {
    const b = el('b', null, v.options && v.options.length ? 'Вопрос владельцу продукта: ' : 'Предложение (решает владелец продукта): ');
    fx.append(b, document.createTextNode(v.fix));
    if (v.options && v.options.length) {
      const ul = el('ul', 'opts');
      v.options.forEach((o, i) => { const li = el('li'); li.append(el('span', 'opt', 'АБВГД'[i] + ')'), document.createTextNode(o)); ul.append(li); });
      fx.append(ul);
    }
  } else { fx.append(el('b', null, 'Исправление: '), document.createTextNode(v.fix)); }
  n.append(fx);
  n.onclick = () => focusNote(v, n);
  n.onkeydown = e => { if (e.key === 'Enter') focusNote(v, n); };
  return n;
}
function visible(v){
  if (!state.sev[v.severity]) return false;
  if (state.bank && !v.tag) return false;
  if (state.q && v.fix_type !== 'question') return false;
  if (state.line != null && !v.lines.includes(state.line)) return false;
  if (state.text) {
    const hay = [v.text, v.quote, v.quote2, v.fix, (v.options || []).join(' '), v.ids.join(' '), DEF[v.defect]].join(' ').toLowerCase();
    if (!hay.includes(state.text)) return false;
  }
  return true;
}
function draw(){
  const list = $('#list'); list.textContent = '';
  const shown = V.filter(visible);
  shown.forEach(v => list.append(note(v)));
  if (!shown.length) list.append(el('p', 'empty', 'Под эти фильтры замечаний нет. Включите другие уровни критичности или очистите поиск.'));
  const pi = $('#pickinfo');
  pi.textContent = '';
  if (state.line != null) {
    pi.append(document.createTextNode('Показаны замечания к строке ' + state.line + '. '));
    const b = el('button', null, 'Показать все'); b.onclick = () => { state.line = null; mark(); draw(); }; pi.append(b);
  }
  pi.classList.toggle('on', state.line != null);
}
function clearFlash(){ document.querySelectorAll('.ln.flash').forEach(x => x.classList.remove('flash')); }
function mark(){
  document.querySelectorAll('.ln.pick').forEach(x => x.classList.remove('pick'));
  if (state.line != null && lineEl[state.line]) lineEl[state.line].classList.add('pick');
}
function goLine(n){
  const r = lineEl[n]; if (!r) return;
  r.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
}
function focusNote(v, n){
  document.querySelectorAll('.note.sel').forEach(x => x.classList.remove('sel')); n.classList.add('sel');
  clearFlash(); v.lines.forEach(k => lineEl[k] && lineEl[k].classList.add('flash'));
  if (v.lines.length) goLine(v.lines[0]);
}
function pickLine(n){ state.line = state.line === n ? null : n; mark(); draw(); }
document.querySelectorAll('#filters .chip').forEach(b => b.onclick = () => {
  const on = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(on));
  if (b.dataset.sev) state.sev[b.dataset.sev] = on;
  if (b.dataset.src === 'bank') state.bank = on;
  if (b.dataset.src === 'q') state.q = on;
  draw();
});
$('#search').oninput = e => { state.text = e.target.value.trim().toLowerCase(); draw(); };
function goto(n){ showTab('v-notes'); clearFlash(); if (lineEl[n]) { lineEl[n].classList.add('flash'); goLine(n); } }
function gotoLink(loc){
  if (!loc) return document.createTextNode('—');
  const a = el('a', 'goto', 'строка ' + loc.line_start); a.tabIndex = 0;
  a.onclick = () => goto(loc.line_start); a.onkeydown = e => { if (e.key === 'Enter') goto(loc.line_start); };
  return a;
}
draw(); eye();

// ---- checklist ----
const CL = (R.checklist || {}).items || [];
if (CL.length) {
  const p = $('#p-check');
  p.append(el('h2', null, 'Чек-лист ревью требований'),
    el('p', 'lead', (R.checklist.source === 'document' ? 'Чек-лист «Ревью требований» взят из самого документа. ' : 'В документе чек-листа нет, использован шаблон банковского ревью. ')
      + 'Ответ «Да» ставится только при подтверждении в тексте; открытое существенное замечание по теме пункта даёт «Нет».'));
  const t = el('table', 'grid'), hr = el('tr');
  ['Проверка', 'Ответ', 'Обоснование', 'Место'].forEach(h => hr.append(el('th', null, h)));
  t.append(hr);
  CL.forEach(it => {
    const tr = el('tr'), st = {yes: ['Да', 'yes'], no: ['Нет', 'no'], 'n/a': ['Не требуется', 'na']}[it.status] || [it.status, 'na'];
    const extra = (it.refs || []).filter(r => !it.evidence.includes(r));
    const ev = el('td', null, it.evidence + (extra.length ? ' (' + extra.join(', ') + ')' : ''));
    const wh = el('td'); wh.append(gotoLink(it.location));
    tr.append(el('td', null, it.item), el('td', 'st ' + st[1], st[0]), ev, wh); t.append(tr);
  });
  p.append(t);
}

// ---- questions ----
const QV = V.filter(v => v.fix_type === 'question');
if (QV.length) {
  const p = $('#p-q');
  p.append(el('h2', null, 'Вопросы владельцу продукта'),
    el('p', 'lead', 'Эти места агент не исправляет сам: нужно бизнес-решение. Агент не подставляет значения, которых нет в документе, а предлагает варианты.'));
  const ol = el('ol', 'qlist');
  QV.forEach(v => {
    const li = el('li'); const head = el('div');
    head.append(el('b', null, v.fix + ' '));
    const loc = v.location; if (loc) { head.append(document.createTextNode('(')); head.append(gotoLink(loc)); head.append(document.createTextNode(v.ids.length ? ', ' + v.ids.join(', ') + ')' : ')')); }
    li.append(head);
    if (v.options && v.options.length) { const ul = el('ul'); v.options.forEach((o, i) => { const x = el('li'); x.append(el('span', 'opt', 'АБВГД'[i] + ')'), document.createTextNode(o)); ul.append(x); }); li.append(ul); }
    ol.append(li);
  });
  p.append(ol);
}

// ---- coverage ----
const CG = R.coverage_gaps || [];
if (CG.length) {
  const p = $('#p-cov');
  p.append(el('h2', null, 'Условия без критериев приёмки'),
    el('p', 'lead', 'Для каждого условия в документе нет проверки. Правый столбец — готовая строка для раздела «Критерии приемки».'));
  const t = el('table', 'grid'), hr = el('tr');
  ['Требование', 'Условие', 'Место', 'Предлагаемый критерий приёмки'].forEach(h => hr.append(el('th', null, h)));
  t.append(hr);
  CG.forEach(g => {
    const tr = el('tr');
    const cond = (g.condition || '').replace('Нет критерия приёмки для условия: ', '').split('. Требование покрывают')[0];
    const wh = el('td'); wh.append(gotoLink(g.location));
    tr.append(el('td', null, (g.req_ids || []).join(', ')), el('td', null, cond), wh, el('td', null, (g.fix || '').replace('Добавить критерий приёмки: ', '')));
    t.append(tr);
  });
  p.append(t);
}

// ---- requirements ----
const RQ = R.requirements || [];
if (RQ.length) {
  const p = $('#p-req');
  p.append(el('h2', null, 'Требования, найденные в документе'),
    el('p', 'lead', 'Агент сам нарезал документ на требования и дал им ID отчёта. Число замечаний — по всем проверкам.'));
  const cnt = {}; V.forEach(v => v.ids.forEach(i => cnt[i] = (cnt[i] || 0) + 1));
  const t = el('table', 'grid'), hr = el('tr');
  ['ID', 'ID в документе', 'Раздел', 'Суть', 'Место', 'Замечаний'].forEach(h => hr.append(el('th', null, h)));
  t.append(hr);
  RQ.forEach(r => {
    const tr = el('tr'); const wh = el('td'); wh.append(gotoLink(r.location));
    tr.append(el('td', null, r.rid), el('td', null, r.doc_id || '—'), el('td', null, r.section || ''), el('td', null, r.summary || r.quote), wh, el('td', null, String(cnt[r.rid] || 0)));
    t.append(tr);
  });
  p.append(t);
}
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    sys.exit(main())
