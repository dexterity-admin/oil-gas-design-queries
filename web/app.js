// Thin client: search and page rendering happen on the office server. The answer is written
// either by the server's model, or (inside the desktop app) by the small model on this PC.
const $ = (id) => document.getElementById(id);
// How many earlier question/answer pairs travel with a follow-up. It is a rolling window, not a
// limit: people can keep asking for as long as they like, older turns simply stop being sent.
const CONTEXT_TURNS = 6;
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
};
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
const pad = (n) => String(n).padStart(2, '0');
// The thread is redrawn often while an answer is written. An entrance plays once: an element that
// started appearing t ms ago gets a negative delay, so a redraw continues its animation instead of
// restarting it, and after two seconds it is drawn still. Use it as the end of a class attribute:
// class="turn${enter(u.at)}" gives class="turn in" style="--t:-300ms".
function enter(t, cls = 'in', d = 0) {
  const ago = Date.now() - (t || 0);
  return ago < 1800 ? ` ${cls}" style="--t:${d - ago}ms` : '';
}

// ---------- theme + answer language ----------
const setTheme = (t) => { document.documentElement.dataset.theme = t; store.set('theme', t); };
setTheme(store.get('theme', matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
$('themeBtn').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
let style = store.get('style', 'english');
const paintStyle = () => document.querySelectorAll('#style button').forEach((b) => { b.classList.toggle('on', b.dataset.v === style); b.setAttribute('role', 'radio'); b.setAttribute('aria-checked', b.dataset.v === style); });
$('style').onclick = (e) => { const b = e.target.closest('button'); if (b) { style = b.dataset.v; store.set('style', style); paintStyle(); } };
paintStyle();

// ---------- logbook of saved chats ----------
let chats = store.get('chats', []);
let current = chats.find((c) => c.id === store.get('current')) || null;
let messages = current ? current.messages : [];
let busy = false, ref = null, local = null, tick = null, lib = null, activeSrc = null, serverLLM = false, heroAt = Date.now();

function save() {
  if (messages.length && !current) {
    current = { id: 'c' + Date.now(), title: messages[0].content.slice(0, 60), updated: Date.now(), messages };
    chats.unshift(current);
  }
  if (current) { current.updated = Date.now(); chats.sort((a, b) => b.updated - a.updated); }
  persist();
}
function persist() {
  let keep = 200; while (!store.set('chats', chats.slice(0, keep)) && keep > 5) keep = Math.floor(keep / 2); // full storage: let the oldest go
  store.set('current', current?.id || null);
  renderChats();
}
function renderChats() {
  const day = 864e5, now = Date.now(); let last = '';
  $('chatList').innerHTML = chats.map((c) => {
    const age = now - c.updated, g = age < day ? 'Today' : age < 7 * day ? 'This week' : 'Earlier';
    const head = g !== last ? `<div class="grp">${last = g}</div>` : '';
    return `${head}<div class="chat${c === current ? ' active' : ''}" data-id="${c.id}"><button class="t" title="${esc(c.title)} (double-click to rename)">${esc(c.title)}</button>` +
      '<button class="e" title="Rename this chat" aria-label="Rename this chat"><svg viewBox="0 0 20 20"><path d="M12.5 4.5l3 3L7 16H4v-3z"/></svg></button>' +
      '<button class="x" title="Delete this chat" aria-label="Delete this chat">✕</button></div>';
  }).join('') || '<div class="grp">Nothing logged yet</div>';
}
// Rename in place: Enter or leaving the box saves, Esc puts the old name back.
function renameChat(row, c) {
  const box = document.createElement('input');
  box.className = 'rename'; box.value = c.title; box.maxLength = 80; box.setAttribute('aria-label', 'Chat name');
  row.querySelector('.t').replaceWith(box); row.classList.add('editing');
  box.focus(); box.select();
  let done = false;
  const finish = (keep) => {
    if (done) return; done = true;
    const v = box.value.replace(/\s+/g, ' ').trim();
    if (keep && v && v !== c.title) { c.title = v; persist(); } else renderChats();
  };
  box.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(true); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
  };
  box.onblur = () => finish(true);
}
function openChat(c) {
  if (busy) return;
  current = c; messages = c ? c.messages : []; closeRef(); store.set('current', c?.id || null);
  if (!messages.length) heroAt = Date.now();
  renderChats(); render(); if (innerWidth <= 860) $('side').classList.add('closed'); $('input').focus();
}
$('chatList').addEventListener('click', (e) => {
  const row = e.target.closest('.chat'); if (!row || row.classList.contains('editing')) return;
  const c = chats.find((x) => x.id === row.dataset.id), b = e.target.closest('button');
  if (b?.classList.contains('x')) {
    if (busy || !confirm(`Delete "${c.title}"?`)) return;
    chats = chats.filter((x) => x !== c); if (c === current) openChat(null); save();
  } else if (b?.classList.contains('e')) renameChat(row, c);
  else if (e.detail < 2) openChat(c); // the second click of a double-click renames instead
});
$('chatList').addEventListener('dblclick', (e) => {
  const row = e.target.closest('.chat'); if (!row || row.classList.contains('editing') || !e.target.closest('.t')) return;
  renameChat(row, chats.find((x) => x.id === row.dataset.id));
});
$('newBtn').onclick = $('topicBtn').onclick = () => openChat(null);
$('clearAll').onclick = () => { if (busy || !chats.length || !confirm('Delete every saved chat on this PC? This cannot be undone.')) return; chats = []; openChat(null); save(); };
$('menuBtn').onclick = () => $('side').classList.toggle('closed');
if (innerWidth <= 860) $('side').classList.add('closed');

// ---------- tiny safe markdown ----------
function inline(s, cite) {
  s = esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>');
  return cite ? s.replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1" title="Show source $1" aria-label="Source $1">$1</button>') : s;
}
function md(text, cite = true) {
  const lines = text.replace(/\r/g, '').split('\n');
  let html = '', i = 0;
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || '')) {
      html += '<div class="tblWrap"><table><tr>' + cells(l).map((c) => `<th>${inline(c, cite)}</th>`).join('') + '</tr>';
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        html += '<tr>' + cells(lines[i]).map((c) => `<td>${inline(c, cite)}</td>`).join('') + '</tr>'; i++;
      }
      html += '</table></div>';
    } else if (/^#{1,6}\s/.test(l)) { html += `<h4>${inline(l.replace(/^#+\s/, ''), cite)}</h4>`; i++; }
    else if (/^\s*([-*•]|\d+[.)])\s+/.test(l)) {
      const ordered = /^\s*\d+[.)]\s/.test(l); html += ordered ? '<ol>' : '<ul>';
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        html += `<li>${inline(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ''), cite)}</li>`; i++;
      }
      html += ordered ? '</ol>' : '</ul>';
    } else if (!l.trim()) { i++; }
    else {
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(\||#{1,6}\s|[-*•]\s|\d+[.)]\s)/.test(lines[i])) para.push(lines[i++]);
      if (!para.length) para.push(lines[i++]);
      html += `<p>${inline(para.join('\n'), cite).replace(/\n/g, '<br>')}</p>`;
    }
  }
  return html;
}
const MARK_RE = /\n[ \t*#]*(?:<{2,}\s*FOLLOW-?UPS\s*>{2,}|next questions:?)[ \t*]*(?:\n|$)/i;
function split(content) {
  // Small local models sometimes leak reasoning tags; never show them.
  content = '\n' + content.replace(/<think>[\s\S]*?<\/think>/g, '').replace(/<\/?think>/g, '');
  const m = MARK_RE.exec(content);
  let text = m ? content.slice(0, m.index) : content;
  const lastLine = text.slice(text.lastIndexOf('\n') + 1).replace(/[*#\s]/g, '').toLowerCase();
  if (!m && lastLine && 'nextquestions:'.startsWith(lastLine)) text = text.slice(0, text.lastIndexOf('\n')); // half-streamed marker
  const tail = m ? content.slice(m.index + m[0].length) : '';
  const follow = tail.split('\n').map((s) => s.replace(/^\s*(?:[-*•]|\d{1,2}[.)]|\[\d\])\s+/, '').trim()).filter((s) => s.length > 3).slice(0, 3);
  return { text: text.trim(), follow };
}

// ---------- the thread ----------
function pipeHtml(bot) {
  const mm = pad(Math.floor((bot.secs || 0) / 60)) + ':' + pad((bot.secs || 0) % 60);
  return '<div class="pipe">' + bot.stages.map((s, i) => {
    const cls = i < bot.stage ? 'done' : i === bot.stage ? 'now' : '';
    return (i ? `<div class="ln ${i <= bot.stage ? (i === bot.stage ? 'now' : 'done') : ''}"></div>` : '') + `<div class="st ${cls}"><i></i>${s}</div>`;
  }).join('') + `<span class="clock">${mm}</span></div>`;
}
function hero() {
  const docs = lib?.docs || [];
  const starts = docs.slice(0, 4).map((d, i) => `<button data-q="What does ${esc(d.name.replace(/\.[a-z]+$/i, ''))} cover?"><i>${pad(i + 1)}</i>What does ${esc(d.name.replace(/\.[a-z]+$/i, ''))} cover?</button>`).join('');
  // A line of flow from the question to the highlighted page: drawn once when the page opens, then still.
  const flow = `<svg class="flow" viewBox="0 0 520 120" aria-hidden="true">
    <path class="run" pathLength="1" d="M40 62H250c8 0 12 4 12 12v6c0 8 4 12 12 12h116"/>
    <circle class="qn" cx="22" cy="62" r="17"/><text class="ql" x="22" y="66.5">Q</text>
    <g class="bal" style="--d:420ms"><circle cx="128" cy="62" r="10"/><text x="128" y="66">1</text></g>
    <g class="bal" style="--d:640ms"><circle cx="322" cy="92" r="10"/><text x="322" y="96">2</text></g>
    <g class="pg" style="--d:560ms"><path d="M408 12h64l18 18v84h-82z"/><path class="hair" d="M472 12v18h18M416 96h66M448 96v18"/>
      <path class="hair" d="M418 34h40M418 44h56M418 54h50M418 76h52M418 86h34"/><rect class="hl" x="414" y="58" width="68" height="13" rx="2"/></g>
    <circle class="end" cx="398" cy="92" r="6"/><path class="tick" pathLength="1" d="M455 104l5 5 10-11"/></svg>`;
  return `<div class="hero${enter(heroAt)}">${flow}<div class="cap">Dexterity Design Services Pvt. Ltd.</div>
    <h1>Ask the <em>standards</em>.<br>Check the <em>source</em>.</h1>
    <p>Every answer is written only from your own documents. Each fact carries a numbered balloon: tap it to see the exact page, highlighted.</p>
    <div class="dials"><div class="dial"><b>${lib ? lib.documents : '–'}</b><span>Documents</span></div>
      <div class="dial"><b>${lib ? lib.chunks : '–'}</b><span>Sections read</span></div></div>
    ${starts ? '<div class="cap" style="margin-bottom:8px">Start with</div><div class="starts">' + starts + '</div>' : ''}</div>`;
}
function render() {
  const t = $('thread');
  const turns = messages.filter((m) => m.role === 'user').length;
  $('input').placeholder = turns ? 'Ask a follow-up…' : 'Ask about your standards and design documents…';
  $('topicBtn').hidden = !turns;
  if (!messages.length) { t.innerHTML = hero(); return; }
  let html = '', qn = 0;
  for (let idx = 0; idx < messages.length; idx += 2) {
    const u = messages[idx], bot = messages[idx + 1] || {}; qn++;
    const last = idx + 2 >= messages.length;
    const { text, follow } = split(bot.content || '');
    const q = bot.query;
    let a = '';
    if (bot.sources?.length) {
      a += '<div class="refs">' + bot.sources.map((s, i) =>
        `<button class="slip${s === activeSrc ? ' on' : ''}${enter(bot.found, 'in', i * 60)}" data-i="${idx + 1}" data-n="${s.n}"><span class="n">${s.n}</span><b>${esc(s.doc)}</b><span>${esc(s.doc_kind)} · ${esc(s.loc)}</span></button>`).join('') + '</div>';
    }
    const live = bot.stages && last && busy;
    if (live) a += pipeHtml(bot) + `<div class="trace" id="liveTrace">${traceHtml(bot, true)}</div><div class="card${enter(bot.wrote)}" id="liveCard" data-i="${idx + 1}" hidden></div>`;
    else if (text) a += `<div class="card${bot.none ? ' none' : ''}" data-i="${idx + 1}">${bot.none ? '<div class="cap">Not in the library</div>' : ''}${md(text)}</div>`;
    if (text && !live && bot.unverified && !bot.cut) a += bot.unverified.length
      ? `<div class="chk bad${enter(bot.at)}"><b>CHECK THE SOURCE</b>${bot.unverified.map(esc).join(', ')} ${bot.unverified.length > 1 ? 'are' : 'is'} not found in the cited documents. Do not use ${bot.unverified.length > 1 ? 'these values' : 'this value'} without checking.</div>`
      : bot.otherRow?.length
      ? `<div class="chk bad${enter(bot.at)}"><b>CHECK THE ROW</b>${bot.otherRow.map(esc).join(', ')} ${bot.otherRow.length > 1 ? 'come' : 'comes'} from a row of the table that does not match your question. Do not use ${bot.otherRow.length > 1 ? 'these values' : 'this value'} without checking the page.</div>`
      : (bot.none ? '' : `<div class="stamp${enter(bot.at, 'in', 200)}" title="Every number in this answer appears in the cited sources. It does not prove the right row was used: check the highlighted page."><svg viewBox="0 0 20 20"><path pathLength="1" d="M4 10.5l4 4 8-9"/></svg><b>NUMBERS FOUND IN SOURCES</b><span>${(text.match(/\d+(?:\.\d+)?/g) || []).length} checked · confirm on the sheet</span></div>`);
    if (text && !live && bot.english) a += `<details class="meta"><summary>English original</summary><div class="card">${md(bot.english)}</div></details>`;
    if (bot.error) a += `<div class="err">${esc(bot.error)}</div>`;
    if (text && !live && bot.took) a += `<details class="meta"><summary>${bot.none ? 'No answer' : 'Answered'} in ${bot.took} s · ${bot.sources?.length || 0} sources${bot.by ? ' · ' + esc(bot.by) : ''}${bot.trace?.length ? ' · how it was worked out' : ''}</summary><div class="trace">${traceHtml(bot, false)}</div></details>`;
    if (text && last && !busy && bot.by === 'this PC' && serverLLM) a += '<button class="ghost again" data-again="1">Get a deeper answer from the office model</button>';
    if (follow.length && last && !busy) a += '<div class="next"><div class="cap">Continue the thread</div>' + follow.map((f, i) => `<button class="${enter(bot.at, 'in', 300 + i * 60).slice(1)}">${esc(f)}</button>`).join('') + '</div>';
    html += `<div class="turn${idx ? ' fu' : ''}${enter(u.at)}"><div class="node">Q${pad(qn)}</div>
      <div><div class="q">${esc(u.content)}</div>${idx ? `<div class="fuTag"><b>FOLLOW-UP</b>${q && q.toLowerCase() !== u.content.toLowerCase() ? ' · read as “' + esc(q) + '”' : ''}</div>` : ''}</div>
      <div class="a">${a}</div></div>`;
  }
  t.innerHTML = html;
  paintCard();
  t.scrollTop = t.scrollHeight;
}

// ---------- live work log and the writing pen ----------
// The work log shows what is really happening (how the question was read, what was searched,
// which sheets are being read, any notes the model makes), line by line as it happens.
function traceHtml(bot, live) {
  const lines = bot.trace || [];
  return lines.map((l, i) => `<div class="tl ${l.k || ''}${live ? enter(l.at) : ''}"><i></i><span>${esc(l.t)}</span>${live && i === lines.length - 1 ? '<b class="spin"></b>' : ''}</div>`).join('');
}
function trace(bot, t, k = '', same = false) {
  bot.trace = bot.trace || [];
  const lastLine = bot.trace[bot.trace.length - 1];
  if (same && lastLine && lastLine.k === k) lastLine.t = t; else bot.trace.push({ t, k, at: Date.now() });
  const el = $('liveTrace'); if (el) { el.innerHTML = traceHtml(bot, true); $('thread').scrollTop = $('thread').scrollHeight; }
}
// Text arrives in bursts; the pen writes it out at an even pace so the page never jumps or stalls.
function paintCard() {
  const card = $('liveCard'), bot = messages[messages.length - 1];
  if (!card || !bot) return;
  const text = split((bot.content || '').slice(0, bot.shown || 0)).text;
  card.hidden = !text; if (!text || card.dataset.len === String(text.length)) return;
  card.dataset.len = text.length;
  const before = card.querySelectorAll('.cite').length;
  card.innerHTML = md(text);
  const cites = card.querySelectorAll('.cite');
  if (cites.length > before) { // a new balloon landed: pop it and flash the slip it points to
    const c = cites[cites.length - 1]; c.classList.add('pop');
    const slip = card.parentElement.querySelector(`.slip[data-n="${c.dataset.n}"]`);
    if (slip) { slip.classList.remove('flash'); void slip.offsetWidth; slip.classList.add('flash'); }
  }
  let host = card.lastElementChild; if (host && /^(UL|OL)$/.test(host.tagName)) host = host.lastElementChild;
  if (host && !host.classList.contains('tblWrap')) host.insertAdjacentHTML('beforeend', '<span class="pen"></span>');
}
const calm = matchMedia('(prefers-reduced-motion: reduce)').matches; // no pen animation for people who asked for less motion
function pump(bot) {
  const tickPen = () => {
    if (!bot.stages) return;
    const total = (bot.content || '').length; bot.shown = bot.shown || 0;
    if (bot.shown < total) {
      bot.shown = calm ? total : Math.min(total, bot.shown + Math.max(1, Math.ceil((total - bot.shown) / 18)));
      const th = $('thread'), pinned = th.scrollHeight - th.scrollTop - th.clientHeight < 80;
      paintCard(); if (pinned) th.scrollTop = th.scrollHeight;
    }
    requestAnimationFrame(tickPen);
  };
  requestAnimationFrame(tickPen);
}
const penDone = (bot) => new Promise((res) => { const w = () => ((bot.shown || 0) >= (bot.content || '').length ? res() : setTimeout(w, 40)); w(); });
$('thread').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.classList.contains('cite') || b.classList.contains('slip')) {
    const idx = b.dataset.i ?? b.closest('.card').dataset.i;
    const s = messages[idx]?.sources?.find((x) => x.n == b.dataset.n);
    if (s) openSource(s);
  } else if (b.dataset.again) { const q = messages[messages.length - 2].content; messages.splice(-2); ask(q, true); }
  else if (b.closest('.next') || b.closest('.starts')) ask(b.dataset.q || b.textContent);
});

// ---------- asking ----------
const post = async (url, body) => {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Server error ' + r.status);
  return r;
};
async function readLines(res, onLine) {
  const reader = res.body.getReader(), dec = new TextDecoder(); let buf = '';
  for (;;) {
    const { done, value } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl; while ((nl = buf.indexOf('\n')) > -1) { const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1); if (line) onLine(line); }
  }
}
const stage = (bot, name) => { const i = bot.stages.indexOf(name); if (i > bot.stage) { bot.stage = i; render(); } };
function showSources(bot, sources, query) {
  bot.sources = sources; bot.query = query; bot.found = Date.now();
  trace(bot, sources.length ? `Found ${sources.length} matching passage${sources.length > 1 ? 's' : ''} in ${new Set(sources.map((s) => s.doc)).size} document(s)` : 'Nothing in the library matches', 'hit');
  if (sources[0] && innerWidth > 1100) openSource(sources[0]); else render();
  sources.forEach((s, i) => setTimeout(() => { if (bot.stages && !bot.content) trace(bot, `Reading [${s.n}] ${s.doc} · ${s.loc}`, 'read'); }, 350 * (i + 1)));
  document.querySelector('.sheet')?.classList.add('scanning');
}
// The model on this PC, reached through the desktop launcher.
async function localLLM(req, onText, onNote) {
  let finished = '';
  const res = await post('llm/v1/chat/completions', { stream: true, temperature: 0, max_tokens: req.max_tokens,
    messages: [{ role: 'system', content: req.system + (local?.system_suffix || '') }, ...req.messages] });
  await readLines(res, (line) => {
    if (!line.startsWith('data:')) return;
    if (line.includes('[DONE]')) { finished = finished || 'stop'; return; }
    const j = JSON.parse(line.slice(5)); if (j.error) throw new Error('The answer was cut off. Ask again.');
    const ch = j.choices?.[0] || {}, d = ch.delta || {};
    if (ch.finish_reason) finished = ch.finish_reason;
    if (d.reasoning_content && onNote) onNote(d.reasoning_content);
    if (d.content) onText(d.content);
  });
  if (!finished) throw new Error('The model stopped before finishing. Ask again.');
  return finished;
}
const noteTaker = (bot) => { let n = ''; return (t) => { n += t; trace(bot, 'Model notes: ' + n.replace(/\s+/g, ' ').slice(-220), 'note', true); }; };
const wrote = (bot) => { if (!bot.wrote) { bot.wrote = Date.now(); trace(bot, 'Writing the answer', 'write'); document.querySelector('.sheet')?.classList.remove('scanning'); } };
async function askLocal(bot, history) {
  bot.by = 'this PC'; let query = null;
  const rw = await (await post('api/prepare', { messages: history, style, stage: 'rewrite' })).json();
  if (rw.needed) {
    query = ''; await localLLM(rw, (t) => { query += t; trace(bot, 'Reading it as: “' + query.replace(/<\/?think>/g, '').trim() + '”', 'rw', true); });
    query = query.replace(/<\/?think>/g, '').trim();
  }
  stage(bot, 'Search'); trace(bot, `Searching ${lib ? lib.documents + ' documents, ' + lib.chunks + ' passages' : 'the library'}`, 'search');
  const prep = await (await post('api/prepare', { messages: history, style, query })).json();
  showSources(bot, prep.sources, prep.query);
  if (!prep.sources.length || prep.skip_model) { trace(bot, 'The library does not cover this question', 'hit'); bot.none = true; bot.content = prep.message; stage(bot, 'Write'); closeRef(); return; }
  stage(bot, 'Read');
  const end = await localLLM(prep, (t) => { wrote(bot); bot.content += t; stage(bot, 'Write'); }, noteTaker(bot));
  if (end === 'length') bot.error = 'This answer reached its length limit and may be incomplete.';
  bot.content = tidy(bot.content);
  if (prep.tanglish_step) { // the PC's small model cannot write Tanglish reliably: the office model rewords, code checks it
    const english = split(bot.content).text; await penDone(bot);
    trace(bot, 'Asking the office model to reword in Tanglish', 'rw');
    const r = await (await post('api/tanglish', { answer: english })).json();
    if (r.ok) { bot.english = english; bot.content = r.text; bot.shown = 0; } else trace(bot, r.reason, 'hit');
  }
  if (end !== 'stop') return; // an unfinished answer gets no check mark
  const chk = await (await post('api/verify', { answer: bot.english || bot.content, grounding: prep.grounding })).json();
  bot.unverified = chk.unverified; bot.otherRow = chk.other_row || [];
}
// Small models sometimes tack the refusal sentence onto a real answer; a real answer wins.
function tidy(a) {
  const s = a.replace(/\n+\s*Sources?\s*:\s*(?=\[)/g, ' ').replace(/\s*The documents do not cover this( question)?( directly)?\.?\s*$/i, '').trim();
  return s.length > 25 ? s : a.trim();
}
async function askServer(bot, history) {
  let ended = false;
  const res = await post('api/chat', { style, messages: history });
  await readLines(res, (line) => {
    const ev = JSON.parse(line);
    if (ev.type === 'status' && /search/i.test(ev.message)) { stage(bot, 'Search'); trace(bot, `Searching ${lib ? lib.documents + ' documents, ' + lib.chunks + ' passages' : 'the library'}`, 'search'); }
    else if (ev.type === 'sources') {
      if (ev.query && ev.query.toLowerCase() !== history[history.length - 1].content.toLowerCase()) trace(bot, 'Reading it as: “' + ev.query + '”', 'rw');
      showSources(bot, ev.sources, ev.query); stage(bot, 'Read');
    } else if (ev.type === 'thinking') noteTaker(bot)(ev.text);
    else if (ev.type === 'check') { bot.unverified = ev.unverified; bot.otherRow = ev.other_row || []; }
    else if (ev.type === 'status' && /tanglish/i.test(ev.message)) trace(bot, 'Rewording in Tanglish, then checking every number', 'rw');
    else if (ev.type === 'replace') { bot.english = ev.english; bot.content = ev.text; bot.shown = 0; }
    else if (ev.type === 'delta') { wrote(bot); bot.content += ev.text; stage(bot, 'Write'); if (/^(The documents do not cover this|I could not find anything)/.test(bot.content)) bot.none = true; }
    else if (ev.type === 'done') ended = true;
    else if (ev.type === 'error') { bot.error = ev.message; render(); }
  });
}
async function ask(q, forceServer = false) {
  q = q.trim(); if (!q || busy) return;
  busy = true; $('send').disabled = true; closeRef();
  if (!current) messages = [];
  messages.push({ role: 'user', content: q, at: Date.now() });
  const history = messages.slice(-(CONTEXT_TURNS * 2 + 1)).map((m) => ({ role: m.role, content: m.content }));
  const bot = { role: 'assistant', content: '', sources: [], stages: ['Understand', 'Search', 'Read', 'Write'], stage: 0, secs: 0 };
  bot.trace = [{ t: messages.length > 1 ? 'Follow-up received · reading it together with the questions before it' : 'Question received', k: 'in' }];
  messages.push(bot); save(); render(); pump(bot);
  const started = Date.now();
  tick = setInterval(() => { bot.secs = Math.round((Date.now() - started) / 1000); const c = document.querySelector('.clock'); if (c) c.textContent = pad(Math.floor(bot.secs / 60)) + ':' + pad(bot.secs % 60); }, 1000);
  try {
    const useLocal = !forceServer && local && ['ready', 'running', 'starting'].includes(local.state);
    if (!useLocal && serverLLM) bot.by = 'office model';
    if (useLocal) setTimeout(refreshLocal, 1500);
    await (useLocal ? askLocal : askServer)(bot, history);
    if (useLocal) refreshLocal();
  } catch (err) {
    const offline = /fetch|network|reach/i.test(err.message);
    bot.error = offline ? 'Cannot reach the Library PC. Check the network, then ask again: your question is back in the box.' : err.message;
    if (offline) { $('input').value = q; grow(); refresh(); }
    if (bot.content) bot.error += ' The text above is incomplete.'; bot.cut = true;
  }
  $('sr').textContent = bot.error || (bot.none ? 'The library does not cover this.' : `Answer ready, ${bot.sources.length} sources.`);
  await penDone(bot); document.querySelector('.sheet')?.classList.remove('scanning');
  clearInterval(tick); delete bot.shown; delete bot.wrote; bot.took = Math.max(1, Math.round((Date.now() - started) / 1000));
  delete bot.stages; delete bot.stage; delete bot.secs; delete bot.found; bot.at = Date.now(); busy = false; $('send').disabled = false;
  save(); render();
}
$('composer').addEventListener('submit', (e) => { e.preventDefault(); const v = $('input').value; $('input').value = ''; grow(); ask(v); });
$('input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('composer').requestSubmit(); } });
function grow() { const i = $('input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight, 150) + 'px'; }
$('input').addEventListener('input', grow);

// ---------- source sheet: the cited page, highlighted, with a title block ----------
async function openSource(s) {
  activeSrc = s; render();
  document.querySelectorAll('.cite').forEach((b) => b.classList.toggle('on', b.dataset.n == s.n && messages[b.closest('.card').dataset.i]?.sources?.includes(s)));
  $('ref').hidden = false;
  $('refDoc').textContent = s.doc; $('refLoc').textContent = s.loc; $('refSec').textContent = s.heading || '–';
  const isPdf = s.doc_kind === 'pdf';
  $('prevPg').hidden = $('nextPg').hidden = $('pgLabel').hidden = $('zoomBtn').hidden = !isPdf;
  $('openOrig').href = `api/docs/${s.doc_id}/file` + (isPdf ? `#page=${s.page}` : '');
  $('openOrig').querySelector('b').textContent = isPdf ? 'Open original ↗' : 'Download original ↓';
  if (isPdf) { ref = { docId: s.doc_id, page: s.page, pages: 0, chunkId: s.chunk_id, home: s.page }; showPage(); }
  else { ref = null; $('refBody').innerHTML = '<div class="textRef">Loading…</div>'; }
  try {
    const r = await fetch(`api/chunks/${s.chunk_id}`); const c = await r.json();
    if (!r.ok) throw new Error(c.detail);
    if (c.name !== s.doc) throw new Error('The library has changed since this answer was written. Ask the question again.');
    if (isPdf && ref) { ref.pages = c.pages; label(); }
    else $('refBody').innerHTML = `<div class="textRef">${md(c.text, false)}</div>`;
  } catch (e) { $('refBody').innerHTML = `<div class="textRef">This source is no longer available. ${esc(e.message || '')}</div>`; }
}
function label() { $('pgLabel').textContent = `${pad(ref.page)} / ${ref.pages ? pad(ref.pages) : '--'}`; $('prevPg').disabled = ref.page <= 1; $('nextPg').disabled = !!ref.pages && ref.page >= ref.pages; }
function showPage() {
  const hl = ref.page === ref.home ? `?chunk=${ref.chunkId}` : '';
  $('refBody').innerHTML = `<img alt="Page ${ref.page}" src="api/docs/${ref.docId}/page/${ref.page}.png${hl}">`;
  $('refBody').scrollTop = 0; label();
}
$('prevPg').onclick = () => { if (ref && ref.page > 1) { ref.page--; showPage(); } };
$('nextPg').onclick = () => { if (ref && (!ref.pages || ref.page < ref.pages)) { ref.page++; showPage(); } };
$('zoomBtn').onclick = () => $('zoomBtn').classList.toggle('on', $('refBody').classList.toggle('zoomed'));
function closeRef() { $('ref').hidden = true; ref = null; activeSrc = null; }
addEventListener('keydown', (e) => { if (e.key !== 'Escape') return; if (!$('ref').hidden) { closeRef(); render(); } else if (!$('desk').hidden) $('desk').hidden = true; });
$('refClose').onclick = () => { closeRef(); render(); };

// ---------- dictation (where the browser supports it) ----------
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SR) {
  const rec = new SR(); rec.interimResults = true; rec.lang = 'en-IN'; let base = '';
  $('mic').hidden = false;
  $('mic').onclick = () => { if ($('mic').classList.contains('on')) rec.stop(); else { base = $('input').value; rec.start(); } };
  rec.onstart = () => $('mic').classList.add('on');
  rec.onend = () => $('mic').classList.remove('on');
  rec.onresult = (e) => { $('input').value = (base + ' ' + [...e.results].map((r) => r[0].transcript).join('')).trim(); grow(); };
}

// ---------- the model on this PC (only inside the desktop app) ----------
async function refreshLocal() {
  try {
    const r = await fetch('local/status'); if (!r.ok) throw 0; local = await r.json();
  } catch { local = null; $('modelCard').hidden = true; return false; }
  const c = $('modelCard'); c.hidden = false;
  const mb = (n) => (n / 1048576).toFixed(0);
  const acc = local.accel, accLine = !acc ? '' : acc.mode === 'gpu' ? ` Using graphics card: ${esc(acc.gpu)} (${acc.gpu_s} s vs ${acc.cpu_s ?? '–'} s on the processor).` : acc.gpu ? ` ${esc(acc.gpu)} was tested: the processor is faster here${acc.gpu_s ? ` (${acc.cpu_s} s vs ${acc.gpu_s} s)` : ''}.` : '';
  const ver = `<div class="ver">DexMentor ${esc(local.version || '')}${status && local.version && status.version && local.version !== status.version && status.version !== 'dev' && local.version !== 'dev' ? ` · <a href="https://github.com/dexterity-admin/oil-gas-design-queries/releases/latest" target="_blank" rel="noopener">update to ${esc(status.version)}</a>` : ''}</div>`;
  setTimeout(() => c.insertAdjacentHTML('beforeend', ver));
  if (local.state === 'tuning') {
    c.innerHTML = `<b><i class="led busy"></i>Checking this PC's speed</b>${esc(local.step || '')}…`;
  } else if (local.state === 'downloading') {
    const pct = local.total ? Math.round(local.done / local.total * 100) : 0;
    c.innerHTML = `<b><i class="led busy"></i>${esc(local.step || 'Preparing')}</b>${mb(local.done)} of ${local.total ? mb(local.total) : '?'} MB · ${pct}%<div class="bar"><i style="width:${pct}%"></i></div>`;
  } else if (local.state === 'missing') {
    c.innerHTML = `<b><i class="led"></i>Answer on this PC</b>One-time download of ${esc(local.model)}, about ${local.size_mb} MB.` +
      (local.error ? `<div class="err">${esc(local.error)}</div>` : '') + '<button class="cta" id="dlBtn">Download model</button>';
    $('dlBtn').onclick = async () => { await fetch('local/download', { method: 'POST' }); refreshLocal(); pollLocal(); };
  } else {
    const on = local.state === 'running';
    c.innerHTML = `<b><i class="led ${on ? 'ok' : local.state === 'starting' ? 'busy' : ''}"></i>${on ? 'Model loaded' : local.state === 'starting' ? 'Loading model' : 'Model standing by'}</b>${esc(local.model)}. ` +
      accLine + (on ? ' Unloads by itself when idle.<button class="ghost" id="unBtn">Stop the model now</button>' : 'Loads when you ask.') +
      (local.error ? `<div class="err">${esc(local.error)}</div>` : '');
    if (on) $('unBtn').onclick = async () => { await fetch('local/unload', { method: 'POST' }); refreshLocal(); };
  }
  return ['downloading', 'starting', 'tuning'].includes(local.state);
}
let localTimer = null;
async function pollLocal() { clearTimeout(localTimer); const fast = await refreshLocal(); if (local) localTimer = setTimeout(pollLocal, fast ? 1000 : 8000); }

// ---------- status ----------
async function refresh() {
  try {
    const r = await fetch('api/status'); if (!r.ok) throw 0; const s = await r.json();
    document.title = s.app; $('appName').textContent = s.app;
    const working = s.index.state !== 'idle'; serverLLM = !!s.llm_available; status = s;
    // English is the default. Simple English and Tanglish need the office model, so they appear only when it is connected.
    $('style').hidden = !serverLLM; if (!serverLLM && style !== 'english') { style = 'english'; paintStyle(); }
    $('dot').className = 'led ' + (working ? 'busy' : 'ok');
    $('libText').textContent = working ? `indexing ${s.index.done}/${s.index.total}` : `${s.documents} docs`;
    const changed = !lib || lib.documents !== s.documents || lib.chunks !== s.chunks;
    if (changed) { lib = { documents: s.documents, chunks: s.chunks, docs: await (await fetch('api/documents')).json() }; if (!messages.length) render(); }
    if (!$('desk').hidden) paintDesk();
    if ($('connectDlg').open) $('connectDlg').close();
    return working;
  } catch {
    $('dot').className = 'led bad'; $('libText').textContent = 'offline';
    if (local && !$('connectDlg').open) { $('serverUrl').value = local.server_url || ''; $('connectDlg').showModal(); findLibrary(); }
    return false;
  }
}
let status = null, pollTimer = null;
async function poll() { clearTimeout(pollTimer); const working = await refresh(); pollTimer = setTimeout(poll, working || !$('desk').hidden ? 1500 : 20000); }
// The engineer app looks for the Library on the office network by itself; typing an address is the fallback.
async function findLibrary() {
  $('connectMsg').textContent = 'Searching the office network for DexMentor Library…';
  try {
    const r = await (await fetch('local/discover', { method: 'POST' })).json();
    if (r.connected) { $('connectMsg').textContent = 'Found it at ' + r.server_url + '. Connecting…'; return refresh(); }
  } catch {}
  $('connectMsg').textContent = 'No Library answered on this network. Check that the Library PC is on and the DexMentor Library program is running, or enter its address (it is shown at the top of the Library desk).';
}
$('connectBtn').onclick = async () => {
  const r = await fetch('local/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ server_url: $('serverUrl').value }) });
  if (!r.ok) { $('connectErr').textContent = (await r.json()).error; return; }
  $('connectErr').textContent = 'Trying…'; await refreshLocal(); if (!(await refresh()) && $('connectDlg').open) $('connectErr').textContent = 'Still no answer from that address. Check it and that the server PC is on.';
};

// ---------- library desk: add documents, watch them being read, check what was read ----------
let admin = false, pin = sessionStorage.getItem('pin') || '', inspecting = null, jobs = [];
const adminHeaders = () => (pin ? { 'X-Admin-Pin': pin } : {});
async function openDesk() {
  $('desk').hidden = false;
  const c = await (await fetch('api/admin/check', { headers: adminHeaders() })).json();
  admin = c.admin; $('drop').hidden = !admin; $('pinBar').hidden = admin;
  paintOffice(); paintPack(); paintFolders(); paintAdmin(c); poll();
}
// Admin PIN and stopping the Library: the Library has no window of its own to close.
function paintAdmin(c) {
  const el = $('adminCard'); el.hidden = !admin; if (!admin) return;
  el.innerHTML = `<b><i class="led ${c.pin_set ? 'ok' : ''}"></i>Managing from other PCs · ${c.pin_set ? 'PIN set' : 'off'}</b>Without a PIN the library can only be changed on this PC. Set one to manage it from any PC on the network.
    <div class="row"><input id="newPin" type="password" placeholder="New PIN, 8 characters or more" autocomplete="new-password"><button class="ghost" id="setPin">${c.pin_set ? 'Change PIN' : 'Set PIN'}</button><button class="ghost" id="quitLib">Stop the Library</button></div><div id="adminErr" class="err" role="alert"></div>`;
  $('setPin').onclick = async () => { const v = $('newPin').value; const r = await fetch('api/admin/pin', { method: 'POST', headers: { 'Content-Type': 'application/json', ...adminHeaders() }, body: JSON.stringify({ pin: v }) }); const j = await r.json(); if (!r.ok) { $('adminErr').textContent = j.detail; return; } pin = v; sessionStorage.setItem('pin', v); openDesk(); };
  $('quitLib').onclick = async () => { if (!confirm('Stop DexMentor Library? Engineers will not get answers until it is started again.')) return; await fetch('api/admin/quit', { method: 'POST', headers: adminHeaders() }); $('adminErr').textContent = 'The Library is stopping. Start it again from the Start menu.'; };
}
async function paintFolders(list) {
  list = list || await (await fetch('api/admin/folders')).json(); const c = $('folderCard'); c.hidden = !admin && !list.length;
  c.innerHTML = '<b><i class="led ' + (list.length ? (list.every((f) => f.reachable) ? 'ok' : 'bad') : '') + '"></i>Linked folders</b>' +
    'Read a folder where it already is, such as a mapped network drive, including all its sub-folders. Files there are never copied, changed or deleted. New files are picked up every 30 minutes.' +
    list.map((f) => `<div class="job${f.reachable ? '' : ' err'}"><span>${esc(f.path)}</span><span class="grow"></span><span>${f.reachable ? f.documents + ' documents' + (f.skipped ? ' · ' + f.skipped + ' old-format files skipped' : '') : 'cannot be reached right now (its documents are kept)'}</span>${admin ? `<button class="rm" data-p="${esc(f.path)}" title="Unlink this folder">✕</button>` : ''}</div>`).join('') +
    (admin ? `<div class="row"><input id="folderPath" placeholder="Z:\\Standards   or   \\\\fileserver\\Standards"><button class="ghost" id="folderAdd">Link folder</button><button class="ghost" id="rescan">Re-scan now</button></div><div id="folderErr" class="err"></div>` : '');
  if (!admin) return;
  const call = async (url, path) => { const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...adminHeaders() }, body: JSON.stringify({ path }) }); const j = await r.json(); if (!r.ok) { $('folderErr').textContent = j.detail; return; } lib = null; paintFolders(j); poll(); setTimeout(() => paintFolders(), 4000); };
  $('folderAdd').onclick = () => call('api/admin/folders', $('folderPath').value);
  $('rescan').onclick = async () => { await fetch('api/reindex', { method: 'POST' }); poll(); setTimeout(() => paintFolders(), 4000); };
  c.querySelectorAll('.rm').forEach((b) => { b.onclick = () => confirm(`Unlink "${b.dataset.p}"? Its documents leave the index; the files themselves are not touched.`) && call('api/admin/folders/unlink', b.dataset.p); });
}
function paintDesk() {
  const s = status, docs = lib?.docs || [];
  $('deskSummary').textContent = `${s.documents} documents · ${s.chunks} sections read · version ${s.version} · engineers' PCs connect to ${s.address}`;
  const log = Object.fromEntries((s.index.log || []).map((e) => [e.name, e]));
  let prog = jobs.map((j) => `<div class="job ${j.state}"><span>${esc(j.name)}</span><div class="bar"><i style="width:${j.pct}%"></i></div><span>${j.state === 'err' ? esc(j.msg) : j.pct < 100 ? j.pct + '%' : 'received'}</span></div>`).join('');
  if (s.index.state !== 'idle') prog += `<div class="pipe"><div class="st done"><i></i>Receive</div><div class="ln done"></div><div class="st ${s.index.state === 'indexing' ? 'now' : 'done'}"><i></i>Read</div><div class="ln ${s.index.state === 'indexing' ? 'now' : 'done'}"></div><div class="st ${s.index.state === 'embedding' ? 'now' : ''}"><i></i>Index</div><div class="ln"></div><div class="st"><i></i>Ready</div><span class="clock">${s.index.done}/${s.index.total}</span></div><div class="job"><span>${esc(s.index.current || 'building the meaning index')}</span></div>`;
  $('deskProgress').innerHTML = prog;
  $('register').innerHTML = docs.length ? `<table><thead><tr><th>No.</th><th></th><th>Document</th><th>Type</th><th>Pages</th><th>Passages</th><th>Tables</th><th></th></tr></thead><tbody>` +
    docs.map((d, i) => `<tr data-id="${d.id}" class="${inspecting === d.id ? 'sel' : ''}"><td class="mono">${pad(i + 1)}</td><td><i class="led ${log[d.name]?.error ? 'bad' : d.note || !d.chunks ? 'busy' : 'ok'}" title="${log[d.name]?.error ? 'Could not be read' : d.note || !d.chunks ? 'Check: ' + esc(d.note || 'no text found') : 'Read'}"></i></td><td class="nm">${esc(d.name)}${d.folder ? `<small style="color:var(--mute)">${d.linked ? '⇄ ' : ''}${esc(d.folder)}</small>` : ''}${d.note ? `<small>${esc(d.note)}</small>` : ''}${log[d.name]?.error ? `<small>${esc(log[d.name].error)}</small>` : ''}</td><td class="mono">${esc(d.kind.toUpperCase())}</td><td class="mono">${d.pages}</td><td class="mono">${d.chunks}</td><td class="mono">${d.tables}</td><td>${admin && !d.linked ? '<button class="rm" title="Remove from the library">Remove</button>' : ''}</td></tr>`).join('') + '</tbody></table>'
    : '<p class="hint">The library is empty. Add your standards, design bases and reference documents to begin.</p>';
  const failed = (s.index.errors || []).length; if (failed) $('deskProgress').innerHTML += `<div class="job err">${failed} file(s) could not be read: ${esc(s.index.errors.join('; '))}</div>`;
}
$('register').addEventListener('click', async (e) => {
  const tr = e.target.closest('tr[data-id]'); if (!tr) return; const id = +tr.dataset.id, d = lib.docs.find((x) => x.id === id);
  if (e.target.classList.contains('rm')) {
    if (!confirm(`Remove "${d.name}" from the library? The file is deleted from the server's library folder.`)) return;
    await fetch(`api/admin/docs/${id}`, { method: 'DELETE', headers: adminHeaders() }); if (inspecting === id) { inspecting = null; $('inspect').innerHTML = ''; } lib = null; return poll();
  }
  inspecting = id; paintDesk();
  const ps = await (await fetch(`api/admin/docs/${id}/passages`)).json();
  $('inspect').innerHTML = `<p class="hint"><b>${esc(d.name)}</b>: ${ps.length} passages, ${ps.filter((p) => p.kind === 'table').length} tables. Compare a few against the original.</p>` +
    ps.map((p) => `<div class="psg ${p.kind}"><div class="cap"><b>${esc(p.kind)}</b><span>${esc(p.loc)}</span><span>${esc(p.heading || '')}</span></div>${md(p.text, false)}</div>`).join('');
});
function sendFiles(files) {
  [...files].forEach((f) => {
    const j = { name: f.name, pct: 0, state: '' }; jobs.unshift(j); jobs = jobs.slice(0, 12);
    const x = new XMLHttpRequest(); x.open('PUT', 'api/admin/files/' + encodeURIComponent(f.name));
    if (pin) x.setRequestHeader('X-Admin-Pin', pin);
    x.upload.onprogress = (e) => { j.pct = Math.round(e.loaded / e.total * 100); paintDesk(); };
    x.onload = () => { if (x.status === 200) { j.pct = 100; j.state = 'done'; } else { j.state = 'err'; try { j.msg = JSON.parse(x.responseText).detail; } catch { j.msg = 'failed'; } } lib = null; poll(); };
    x.onerror = () => { j.state = 'err'; j.msg = 'connection lost'; paintDesk(); };
    x.send(f);
  });
  paintDesk();
}
$('fileInput').onchange = (e) => { sendFiles(e.target.files); e.target.value = ''; };
['dragover', 'dragleave', 'drop'].forEach((t) => $('drop').addEventListener(t, (e) => { e.preventDefault(); $('drop').classList.toggle('over', t === 'dragover'); if (t === 'drop') sendFiles(e.dataTransfer.files); }));
$('pinBtn').onclick = async () => { pin = $('pinInput').value; sessionStorage.setItem('pin', pin); await openDesk(); if (!admin) { pin = ''; $('pinInput').value = ''; $('pinErr').textContent = 'That PIN is not right.'; } else $('pinErr').textContent = ''; };
async function paintOffice(o) {
  o = o || await (await fetch('api/admin/office', { headers: adminHeaders() })).json(); const c = $('officeCard'); c.hidden = false;
  const state = !o.enabled ? 'Not connected' : !o.reachable ? 'Cannot reach that PC' : !o.model_present ? 'Reached, but the model is not installed there' : 'Connected';
  c.innerHTML = `<b><i class="led ${o.enabled && o.reachable && o.model_present ? 'ok' : o.enabled ? 'bad' : ''}"></i>Office model · ${state}</b>
    A larger model on the office PC (through Ollama) adds deeper answers, Simple English and Tanglish for everyone. Without it the app works in English.` +
    (admin ? `<div class="row"><input id="offUrl" placeholder="http://gaming-pc:11434" value="${o.enabled ? esc(o.url) : ''}"><input id="offModel" placeholder="model name" value="${esc(o.model || '')}"></div>
      <div class="row"><button class="ghost" id="offSave">Connect and test</button>${o.enabled ? '<button class="ghost" id="offOff">Disconnect</button>' : ''}</div>` +
      (o.reachable && o.models.length ? `<div class="mono" style="margin-top:8px">Installed there: ${o.models.map(esc).join(', ')}</div>` : '') : '');
  const set = async (url) => { const r = await fetch('api/admin/office', { method: 'POST', headers: { 'Content-Type': 'application/json', ...adminHeaders() }, body: JSON.stringify({ url, model: $('offModel').value.trim() }) }); paintOffice(await r.json()); refresh(); };
  if (admin) { $('offSave').onclick = () => set($('offUrl').value.trim() || 'http://127.0.0.1:11434'); if ($('offOff')) $('offOff').onclick = () => set(''); }
}
async function paintPack(p) {
  p = p || await (await fetch('api/admin/pack', { headers: adminHeaders() })).json(); const c = $('packCard');
  c.hidden = !(admin || p.connected); if (c.hidden) return;
  const state = !p.connected ? 'Not connected' : p.error ? 'Needs attention' : `${p.documents} document${p.documents === 1 ? '' : 's'} on the shelf`;
  c.innerHTML = `<b><i class="led ${p.connected && !p.error ? 'ok' : p.connected ? 'bad' : ''}"></i>Starter pack · ${state}</b>
    The company's own reference documents, kept in a private GitHub repository. The Library downloads them by itself and picks up new ones you add there.` +
    (p.error ? `<div class="err">${esc(p.error)}</div>` : '') +
    (admin ? `<div class="row"><input id="packRepo" placeholder="owner/private-repository" value="${esc(p.repo || '')}"><input id="packKey" type="password" autocomplete="off" placeholder="${p.connected ? 'access key saved' : 'read-only access key'}"></div>
      <div class="row"><button class="ghost" id="packSave">${p.connected ? 'Check for new documents' : 'Connect'}</button>${p.connected ? '<button class="ghost" id="packOff">Disconnect</button>' : ''}</div>` : '');
  if (!admin) return;
  const set = async (repo) => {
    $('packSave').disabled = true; $('packSave').textContent = 'Working…';
    const r = await fetch('api/admin/pack', { method: 'POST', headers: { 'Content-Type': 'application/json', ...adminHeaders() }, body: JSON.stringify({ repo, token: $('packKey').value.trim() }) });
    const j = await r.json(); paintPack(r.ok ? j : { connected: p.connected, documents: p.documents, repo, error: j.detail || 'That did not work.' }); poll();
  };
  $('packSave').onclick = () => set($('packRepo').value.trim() || ' '); if ($('packOff')) $('packOff').onclick = () => set('');
}
$('libBtn').onclick = openDesk;
$('deskClose').onclick = () => { $('desk').hidden = true; };

renderChats(); render(); poll(); pollLocal();
if (location.hash === '#desk') openDesk();
