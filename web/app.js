// Thin client: search and page rendering happen on the office server. The answer is written
// either by the server's model, or (inside the desktop app) by the small model on this PC.
const $ = (id) => document.getElementById(id);
const MAX_TURNS = 6; // how many earlier question/answer pairs are sent as context
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
const pad = (n) => String(n).padStart(2, '0');

// ---------- theme + answer language ----------
const setTheme = (t) => { document.documentElement.dataset.theme = t; store.set('theme', t); };
setTheme(store.get('theme', matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
$('themeBtn').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
let style = store.get('style', 'english');
const paintStyle = () => document.querySelectorAll('#style button').forEach((b) => b.classList.toggle('on', b.dataset.v === style));
$('style').onclick = (e) => { const b = e.target.closest('button'); if (b) { style = b.dataset.v; store.set('style', style); paintStyle(); } };
paintStyle();

// ---------- logbook of saved chats ----------
let chats = store.get('chats', []);
let current = chats.find((c) => c.id === store.get('current')) || null;
let messages = current ? current.messages : [];
let busy = false, ref = null, local = null, tick = null, lib = null, activeSrc = null;

function save() {
  if (messages.length && !current) {
    current = { id: 'c' + Date.now(), title: messages[0].content.slice(0, 60), updated: Date.now(), messages };
    chats.unshift(current);
  }
  if (current) { current.updated = Date.now(); chats.sort((a, b) => b.updated - a.updated); }
  store.set('chats', chats.slice(0, 200)); store.set('current', current?.id || null);
  renderChats();
}
function renderChats() {
  const day = 864e5, now = Date.now(); let last = '';
  $('chatList').innerHTML = chats.map((c) => {
    const age = now - c.updated, g = age < day ? 'Today' : age < 7 * day ? 'This week' : 'Earlier';
    const head = g !== last ? `<div class="grp">${last = g}</div>` : '';
    return `${head}<div class="chat${c === current ? ' active' : ''}" data-id="${c.id}"><button class="t" title="${esc(c.title)}">${esc(c.title)}</button><button class="x" title="Delete">✕</button></div>`;
  }).join('') || '<div class="grp">Nothing logged yet</div>';
}
function openChat(c) {
  if (busy) return;
  current = c; messages = c ? c.messages : []; closeRef(); store.set('current', c?.id || null);
  renderChats(); render(); if (innerWidth <= 860) $('side').classList.add('closed'); $('input').focus();
}
$('chatList').addEventListener('click', (e) => {
  const row = e.target.closest('.chat'); if (!row) return;
  const c = chats.find((x) => x.id === row.dataset.id);
  if (e.target.classList.contains('x')) {
    if (!confirm(`Delete "${c.title}"?`)) return;
    chats = chats.filter((x) => x !== c); if (c === current) openChat(null); save();
  } else openChat(c);
});
$('newBtn').onclick = () => openChat(null);
$('menuBtn').onclick = () => $('side').classList.toggle('closed');
if (innerWidth <= 860) $('side').classList.add('closed');

// ---------- tiny safe markdown ----------
function inline(s, cite) {
  s = esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>');
  return cite ? s.replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1" title="Show source $1">$1</button>') : s;
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
  const follow = tail.split('\n').map((s) => s.replace(/^(\[\d\]|[-*\d.)\s])+/, '').trim()).filter((s) => s.length > 3).slice(0, 3);
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
  const starts = docs.slice(0, 4).map((d, i) => `<button><i>${pad(i + 1)}</i>What does ${esc(d.name.replace(/\.[a-z]+$/i, ''))} cover?</button>`).join('');
  return `<div class="hero"><div class="cap">Company standards, explained</div>
    <h1>Ask the <em>standards</em>.<br>Check the <em>source</em>.</h1>
    <p>Every answer is written only from your own documents. Each fact carries a numbered balloon: tap it to see the exact page, highlighted.</p>
    <div class="dials"><div class="dial"><b>${lib ? lib.documents : '–'}</b><span>Documents</span></div>
      <div class="dial"><b>${lib ? lib.chunks : '–'}</b><span>Passages indexed</span></div></div>
    ${starts ? '<div class="cap" style="margin-bottom:8px">Start with</div><div class="starts">' + starts + '</div>' : ''}</div>`;
}
function render() {
  const t = $('thread');
  const turns = messages.filter((m) => m.role === 'user').length;
  $('input').placeholder = turns ? 'Ask a follow-up…' : 'Ask about your standards and design documents…';
  $('gauge').hidden = !turns;
  if (turns) {
    const used = Math.min(turns, MAX_TURNS), arc = 75.4;
    $('gauge').innerHTML = `<svg viewBox="0 0 62 36"><path class="trk" d="M7 33a24 24 0 0148 0"/><path class="val" d="M7 33a24 24 0 0148 0" stroke-dasharray="${arc}" stroke-dashoffset="${arc * (1 - used / MAX_TURNS)}"/></svg>CONTEXT ${used}/${MAX_TURNS}<button type="button">new topic</button>`;
    $('gauge').querySelector('button').onclick = () => openChat(null);
  }
  if (!messages.length) { t.innerHTML = hero(); return; }
  const cutoff = messages.length - MAX_TURNS * 2;
  let html = '', qn = 0;
  for (let idx = 0; idx < messages.length; idx += 2) {
    const u = messages[idx], bot = messages[idx + 1] || {}; qn++;
    const last = idx + 2 >= messages.length;
    const { text, follow } = split(bot.content || '');
    const q = bot.query;
    let a = '';
    if (bot.sources?.length) {
      a += '<div class="refs">' + bot.sources.map((s) =>
        `<button class="slip${s === activeSrc ? ' on' : ''}" data-i="${idx + 1}" data-n="${s.n}"><span class="n">${s.n}</span><b>${esc(s.doc)}</b><span>${esc(s.doc_kind)} · ${esc(s.loc)}</span></button>`).join('') + '</div>';
    }
    if (bot.stages && last && busy) a += pipeHtml(bot);
    if (text) a += `<div class="card" data-i="${idx + 1}">${md(text)}</div>`;
    if (bot.error) a += `<div class="err">${esc(bot.error)}</div>`;
    if (text && !(last && busy) && bot.took) a += `<div class="meta">Answered in ${bot.took} s · ${bot.sources?.length || 0} sources${bot.by ? ' · ' + esc(bot.by) : ''}</div>`;
    if (follow.length && last && !busy) a += '<div class="next"><div class="cap">Continue the thread</div>' + follow.map((f) => `<button>${esc(f)}</button>`).join('') + '</div>';
    html += `<div class="turn${idx ? ' fu' : ''}${idx < cutoff ? ' stale' : ''}"><div class="node">Q${pad(qn)}</div>
      <div><div class="q">${esc(u.content)}</div>${idx ? `<div class="fuTag"><b>FOLLOW-UP</b>${q && q.toLowerCase() !== u.content.toLowerCase() ? ' · read as “' + esc(q) + '”' : ''}${idx < cutoff ? ' · no longer in context' : ''}</div>` : ''}</div>
      <div class="a">${a}</div></div>`;
  }
  t.innerHTML = html;
  t.scrollTop = t.scrollHeight;
}
$('thread').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.classList.contains('cite') || b.classList.contains('slip')) {
    const idx = b.dataset.i ?? b.closest('.card').dataset.i;
    const s = messages[idx]?.sources?.find((x) => x.n == b.dataset.n);
    if (s) openSource(s);
  } else if (b.closest('.next') || b.closest('.starts')) ask(b.textContent.replace(/^\d\d/, ''));
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
  bot.sources = sources; bot.query = query;
  if (sources[0] && innerWidth > 860) openSource(sources[0]); else render();
}
// The model on this PC, reached through the desktop launcher.
async function localLLM(req, onText) {
  const res = await post('llm/v1/chat/completions', { stream: true, temperature: 0.2, max_tokens: req.max_tokens,
    messages: [{ role: 'system', content: req.system + (local?.system_suffix || '') }, ...req.messages] });
  await readLines(res, (line) => {
    if (!line.startsWith('data:') || line.includes('[DONE]')) return;
    const piece = JSON.parse(line.slice(5)).choices?.[0]?.delta?.content; if (piece) onText(piece);
  });
}
async function askLocal(bot, history) {
  bot.by = 'this PC'; let query = null;
  const rw = await (await post('api/prepare', { messages: history, style, stage: 'rewrite' })).json();
  if (rw.needed) { query = ''; await localLLM(rw, (t) => { query += t; }); query = query.replace(/<\/?think>/g, '').trim(); }
  stage(bot, 'Search');
  const prep = await (await post('api/prepare', { messages: history, style, query })).json();
  showSources(bot, prep.sources, prep.query);
  if (!prep.sources.length) { bot.content = prep.message; return; }
  stage(bot, 'Read');
  await localLLM(prep, (t) => { bot.content += t; stage(bot, 'Write'); render(); });
}
async function askServer(bot, history) {
  const res = await post('api/chat', { style, messages: history });
  await readLines(res, (line) => {
    const ev = JSON.parse(line);
    if (ev.type === 'status' && /search/i.test(ev.message)) stage(bot, 'Search');
    else if (ev.type === 'sources') { showSources(bot, ev.sources, ev.query); stage(bot, 'Read'); }
    else if (ev.type === 'delta') { bot.content += ev.text; stage(bot, 'Write'); }
    else if (ev.type === 'error') bot.error = ev.message;
    render();
  });
}
async function ask(q) {
  q = q.trim(); if (!q || busy) return;
  busy = true; $('send').disabled = true;
  if (!current) messages = [];
  messages.push({ role: 'user', content: q });
  const history = messages.slice(-(MAX_TURNS * 2 + 1)).map((m) => ({ role: m.role, content: m.content }));
  const bot = { role: 'assistant', content: '', sources: [], stages: ['Understand', 'Search', 'Read', 'Write'], stage: 0, secs: 0 };
  messages.push(bot); save(); render();
  const started = Date.now();
  tick = setInterval(() => { bot.secs = Math.round((Date.now() - started) / 1000); const c = document.querySelector('.clock'); if (c) c.textContent = pad(Math.floor(bot.secs / 60)) + ':' + pad(bot.secs % 60); }, 1000);
  try {
    const useLocal = local && ['ready', 'running', 'starting'].includes(local.state);
    if (useLocal) setTimeout(refreshLocal, 1500);
    await (useLocal ? askLocal : askServer)(bot, history);
    if (useLocal) refreshLocal();
  } catch (err) { bot.error = 'Something went wrong: ' + err.message; }
  clearInterval(tick); bot.took = Math.max(1, Math.round((Date.now() - started) / 1000));
  delete bot.stages; delete bot.stage; delete bot.secs; busy = false; $('send').disabled = false;
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
  if (local.state === 'downloading') {
    const pct = local.total ? Math.round(local.done / local.total * 100) : 0;
    c.innerHTML = `<b><i class="led busy"></i>${esc(local.step || 'Preparing')}</b>${mb(local.done)} of ${local.total ? mb(local.total) : '?'} MB · ${pct}%<div class="bar"><i style="width:${pct}%"></i></div>`;
  } else if (local.state === 'missing') {
    c.innerHTML = `<b><i class="led"></i>Answer on this PC</b>One-time download of ${esc(local.model)}, about ${local.size_mb} MB.` +
      (local.error ? `<div class="err">${esc(local.error)}</div>` : '') + '<button class="cta" id="dlBtn">Download model</button>';
    $('dlBtn').onclick = async () => { await fetch('local/download', { method: 'POST' }); refreshLocal(); pollLocal(); };
  } else {
    const on = local.state === 'running';
    c.innerHTML = `<b><i class="led ${on ? 'ok' : local.state === 'starting' ? 'busy' : ''}"></i>${on ? 'Model loaded' : local.state === 'starting' ? 'Loading model' : 'Model standing by'}</b>${esc(local.model)}. ` +
      (on ? 'Unloads by itself when idle.<button class="ghost" id="unBtn">Free the memory now</button>' : 'Loads when you ask.') +
      (local.error ? `<div class="err">${esc(local.error)}</div>` : '');
    if (on) $('unBtn').onclick = async () => { await fetch('local/unload', { method: 'POST' }); refreshLocal(); };
  }
  return local.state === 'downloading' || local.state === 'starting';
}
let localTimer = null;
async function pollLocal() { clearTimeout(localTimer); const fast = await refreshLocal(); if (local) localTimer = setTimeout(pollLocal, fast ? 1000 : 8000); }

// ---------- status + library ----------
async function refresh() {
  try {
    const s = await (await fetch('api/status')).json();
    document.title = s.app; $('appName').textContent = s.app;
    const working = s.index.state !== 'idle';
    $('dot').className = 'led ' + (working ? 'busy' : 'ok');
    $('libText').textContent = working ? `indexing ${s.index.done}/${s.index.total}` : `${s.documents} docs`;
    $('libSummary').textContent = `${s.documents} documents, ${s.chunks} passages. Server model: ${s.llm}. Semantic search: ${s.index.embeddings}.` +
      (s.index.errors.length ? ` ${s.index.errors.length} file(s) failed: ${s.index.errors.join('; ')}` : '');
    const first = !lib || lib.documents !== s.documents;
    if (first) { lib = { documents: s.documents, chunks: s.chunks, docs: await (await fetch('api/documents')).json() }; if (!messages.length) render(); }
    return working;
  } catch { $('dot').className = 'led bad'; $('libText').textContent = 'offline'; return false; }
}
async function poll() { const working = await refresh(); setTimeout(poll, working ? 1500 : 20000); }
$('libBtn').onclick = async () => {
  $('libDlg').showModal(); await refresh();
  $('libList').innerHTML = (lib?.docs || []).map((d) => `<div class="doc"><div>${esc(d.name)}<small>${esc(d.note || '')}</small></div>
    <div><small>${d.pages} ${d.kind === 'xlsx' ? 'sheets' : 'pages'} · ${d.chunks} passages</small></div></div>`).join('') || '<p>No documents yet. Copy files into the library folder on the server.</p>';
};
$('libClose').onclick = () => $('libDlg').close();
$('reindex').onclick = async () => { await fetch('api/reindex', { method: 'POST' }); refresh(); setTimeout(poll, 500); };

renderChats(); render(); poll(); pollLocal();
