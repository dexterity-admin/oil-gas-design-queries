// Thin client: search and page rendering happen on the office server. The answer is written
// either by the server's model, or (inside the desktop app) by the small model on this PC.
const $ = (id) => document.getElementById(id);
const MARK = '<<<FOLLOWUPS>>>';
const MAX_TURNS = 6; // how many earlier question/answer pairs are sent as context
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

// ---------- saved chats ----------
let chats = store.get('chats', []);
const legacy = store.get('chat', []);
if (legacy.length && !chats.length) chats = [{ id: 'c' + Date.now(), title: legacy[0].content.slice(0, 60), updated: Date.now(), messages: legacy }];
let current = chats.find((c) => c.id === store.get('current')) || null;
let messages = current ? current.messages : [];
let busy = false, ref = null, local = null, tick = null;

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
    return `${head}<div class="chat${c === current ? ' active' : ''}" data-id="${c.id}"><button class="t" title="${esc(c.title)}">${esc(c.title)}</button><button class="x" title="Delete chat">✕</button></div>`;
  }).join('') || '<div class="grp">No saved chats yet</div>';
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
$('newBtn').onclick = $('newBtn2').onclick = () => openChat(null);
$('menuBtn').onclick = () => $('side').classList.toggle('closed');
if (innerWidth <= 860) $('side').classList.add('closed');

// ---------- tiny safe markdown ----------
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function inline(s, cite) {
  s = esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>');
  return cite ? s.replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1">$1</button>') : s;
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

// ---------- chat rendering ----------
function split(content) {
  const [body, tail = ''] = content.split(MARK);
  const cut = body.lastIndexOf('<<<'); // hide a half-streamed marker
  const text = (cut > -1 && MARK.startsWith(body.slice(cut))) ? body.slice(0, cut) : body;
  const follow = tail.split('\n').map((s) => s.replace(/^[-*\d.)\s]+/, '').trim()).filter((s) => s.length > 3).slice(0, 3);
  return { text: text.trim(), follow };
}
function render() {
  const t = $('thread');
  const turns = messages.filter((m) => m.role === 'user').length;
  $('input').placeholder = turns ? 'Ask a follow-up…' : 'Ask about your standards and design documents…';
  $('ctxBar').hidden = !turns;
  if (turns) {
    const used = Math.min(turns, MAX_TURNS);
    $('ctxBar').innerHTML = `<span class="meter"><i style="width:${used / MAX_TURNS * 100}%"></i></span>
      <span>Follow-ups use your last ${used} question${used > 1 ? 's' : ''} as context${turns > MAX_TURNS ? ' (older ones are dropped)' : ''}.</span>
      <button type="button" id="freshBtn">Start a new topic</button>`;
    $('freshBtn').onclick = () => openChat(null);
  }
  if (!messages.length) {
    t.innerHTML = `<div class="welcome"><h1>Ask your standards anything</h1>
      <p>Answers come only from your company's own documents. Every fact is cited, and you can open the exact page to check it.</p></div>`;
    return;
  }
  t.innerHTML = '';
  const cutoff = messages.length - MAX_TURNS * 2; // messages before this are no longer sent as context
  messages.forEach((m, idx) => {
    if (idx === cutoff && cutoff > 0) t.insertAdjacentHTML('beforeend', '<div class="thread-line">earlier messages are not used as context</div>');
    const el = document.createElement('div');
    el.className = 'msg ' + (m.role === 'user' ? 'user' : 'bot');
    if (m.role === 'user') {
      const bot = messages[idx + 1], q = bot?.query;
      let h = `<div class="bubble">${esc(m.content)}</div>`;
      if (idx > 0) h += `<div class="fu">↳ <b>Follow-up</b>${q && q.toLowerCase() !== m.content.toLowerCase() ? ' · understood as: “' + esc(q) + '”' : ''}</div>`;
      el.innerHTML = h;
    } else {
      const { text, follow } = split(m.content || '');
      const last = idx === messages.length - 1;
      let h = '';
      if (m.sources?.length) {
        h += '<div class="sources">' + m.sources.map((s) =>
          `<button class="src" data-i="${idx}" data-n="${s.n}"><b><em>${s.n}</em>${esc(s.doc)}</b><span>${esc(s.loc)}</span></button>`).join('') + '</div>';
      }
      if (text) h += `<div class="bubble">${md(text)}</div>`;
      if (m.status && last && busy) h += `<div class="status"><i></i>${esc(m.status)}… <span class="secs">${m.secs ? m.secs + ' s' : ''}</span></div>`;
      if (m.error) h += `<div class="err">${esc(m.error)}</div>`;
      if (follow.length && last && !busy) h += '<div class="follow">' + follow.map((f) => `<button>${esc(f)}</button>`).join('') + '</div>';
      el.innerHTML = h;
      el.dataset.i = idx;
    }
    t.appendChild(el);
  });
  t.scrollTop = t.scrollHeight;
}
$('thread').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.classList.contains('cite') || b.classList.contains('src')) {
    const idx = b.dataset.i ?? b.closest('.msg').dataset.i;
    const s = messages[idx]?.sources?.find((x) => x.n == b.dataset.n);
    if (s) openSource(s);
  } else if (b.parentElement.classList.contains('follow')) { ask(b.textContent); }
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
function showSources(bot, sources, query) {
  bot.sources = sources; bot.query = query;
  if (sources[0] && innerWidth > 860) openSource(sources[0]);
}
// The model on this PC, reached through the desktop launcher.
async function localLLM(req, onText) {
  const res = await post('llm/v1/chat/completions', { stream: true, temperature: 0.2, max_tokens: req.max_tokens,
    messages: [{ role: 'system', content: req.system }, ...req.messages] });
  await readLines(res, (line) => {
    if (!line.startsWith('data:') || line.includes('[DONE]')) return;
    const piece = JSON.parse(line.slice(5)).choices?.[0]?.delta?.content; if (piece) onText(piece);
  });
}
async function askLocal(bot, history, style) {
  let query = null;
  const rw = await (await post('api/prepare', { messages: history, style, stage: 'rewrite' })).json();
  if (rw.needed) { bot.status = 'Understanding your follow-up'; render(); query = ''; await localLLM(rw, (t) => { query += t; }); }
  bot.status = 'Searching the standards'; render();
  const prep = await (await post('api/prepare', { messages: history, style, query })).json();
  showSources(bot, prep.sources, prep.query);
  if (!prep.sources.length) { bot.content = prep.message; return; }
  bot.status = 'Reading the sources on this PC'; render();
  await localLLM(prep, (t) => { bot.content += t; bot.status = ''; render(); });
}
async function askServer(bot, history, style) {
  const res = await post('api/chat', { style, messages: history });
  await readLines(res, (line) => {
    const ev = JSON.parse(line);
    if (ev.type === 'status') bot.status = ev.message;
    else if (ev.type === 'sources') { showSources(bot, ev.sources, ev.query); bot.status = 'Writing the answer'; }
    else if (ev.type === 'delta') { bot.content += ev.text; bot.status = ''; }
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
  const bot = { role: 'assistant', content: '', sources: [], status: messages.length > 1 ? 'Understanding your follow-up' : 'Searching the standards', secs: 0 };
  messages.push(bot); save(); render();
  const started = Date.now();
  tick = setInterval(() => { bot.secs = Math.round((Date.now() - started) / 1000); if (bot.status) render(); }, 1000);
  try {
    const useLocal = local && ['ready', 'running', 'starting'].includes(local.state);
    if (useLocal) setTimeout(refreshLocal, 1500);
    await (useLocal ? askLocal : askServer)(bot, history, $('style').value);
    if (useLocal) refreshLocal();
  } catch (err) { bot.error = 'Something went wrong: ' + err.message; }
  clearInterval(tick); bot.status = ''; delete bot.secs; busy = false; $('send').disabled = false;
  save(); render();
}
$('composer').addEventListener('submit', (e) => { e.preventDefault(); const v = $('input').value; $('input').value = ''; grow(); ask(v); });
$('input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('composer').requestSubmit(); } });
function grow() { const i = $('input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight, 160) + 'px'; }
$('input').addEventListener('input', grow);
$('style').value = store.get('style', 'english');
$('style').onchange = () => store.set('style', $('style').value);

// ---------- reference pane: the cited page, highlighted ----------
async function openSource(s) {
  document.querySelectorAll('.src').forEach((b) => b.classList.toggle('active', b.dataset.n == s.n && messages[b.dataset.i]?.sources?.includes(s)));
  $('ref').hidden = false;
  $('refDoc').textContent = s.doc; $('refLoc').textContent = s.loc + (s.heading ? ' · ' + s.heading : '');
  const isPdf = s.doc_kind === 'pdf';
  $('prevPg').hidden = $('nextPg').hidden = $('pgLabel').hidden = $('zoomBtn').hidden = !isPdf;
  $('openOrig').href = `api/docs/${s.doc_id}/file` + (isPdf ? `#page=${s.page}` : '');
  $('openOrig').textContent = isPdf ? 'Open original ↗' : 'Download original ↓';
  if (isPdf) { ref = { docId: s.doc_id, page: s.page, pages: 0, chunkId: s.chunk_id, home: s.page }; showPage(); }
  else { ref = null; $('refBody').innerHTML = '<div class="textRef">Loading…</div>'; }
  try {
    const r = await fetch(`api/chunks/${s.chunk_id}`); const c = await r.json();
    if (!r.ok) throw new Error(c.detail);
    if (isPdf && ref) { ref.pages = c.pages; label(); }
    else $('refBody').innerHTML = `<div class="textRef">${md(c.text, false)}</div>`;
  } catch (e) { $('refBody').innerHTML = `<div class="textRef">This source is no longer available. ${esc(e.message || '')}</div>`; }
}
function label() { $('pgLabel').textContent = `Page ${ref.page}` + (ref.pages ? ` of ${ref.pages}` : ''); $('prevPg').disabled = ref.page <= 1; $('nextPg').disabled = ref.pages && ref.page >= ref.pages; }
function showPage() {
  const hl = ref.page === ref.home ? `?chunk=${ref.chunkId}` : '';
  $('refBody').innerHTML = `<img alt="Page ${ref.page}" src="api/docs/${ref.docId}/page/${ref.page}.png${hl}">`;
  $('refBody').scrollTop = 0; label();
}
$('prevPg').onclick = () => { if (ref && ref.page > 1) { ref.page--; showPage(); } };
$('nextPg').onclick = () => { if (ref && (!ref.pages || ref.page < ref.pages)) { ref.page++; showPage(); } };
$('zoomBtn').onclick = () => $('refBody').classList.toggle('zoomed');
function closeRef() { $('ref').hidden = true; ref = null; }
$('refClose').onclick = closeRef;

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
    c.innerHTML = `<b>${esc(local.step || 'Preparing download')}</b>${mb(local.done)} of ${local.total ? mb(local.total) : '?'} MB (${pct}%)<div class="bar"><i style="width:${pct}%"></i></div>`;
  } else if (local.state === 'missing') {
    c.innerHTML = `<b>Answer on this PC</b>One-time download of the ${esc(local.model)} model, about ${local.size_mb} MB.` +
      (local.error ? `<div class="err">${esc(local.error)}</div>` : '') + '<button class="primary" id="dlBtn">Download model</button>';
    $('dlBtn').onclick = async () => { await fetch('local/download', { method: 'POST' }); refreshLocal(); };
  } else {
    const on = local.state === 'running';
    c.innerHTML = `<b>${on ? '● Model loaded' : local.state === 'starting' ? 'Loading model…' : '○ Model ready'}</b>${esc(local.model)}. ` +
      (on ? 'Unloads by itself when idle.<button class="ghost" id="unBtn">Free the memory now</button>' : 'Loads when you ask a question.') +
      (local.error ? `<div class="err">${esc(local.error)}</div>` : '');
    if (on) $('unBtn').onclick = async () => { await fetch('local/unload', { method: 'POST' }); refreshLocal(); };
  }
  return local.state === 'downloading' || local.state === 'starting';
}
async function pollLocal() { const fast = await refreshLocal(); if (local) setTimeout(pollLocal, fast ? 1000 : 8000); }

// ---------- status + library ----------
async function refresh() {
  try {
    const s = await (await fetch('api/status')).json();
    document.title = s.app; $('appName').textContent = s.app;
    const working = s.index.state !== 'idle';
    $('dot').className = working ? 'busy' : 'ok';
    $('libText').textContent = working ? `indexing ${s.index.done}/${s.index.total}` : `${s.documents} documents`;
    $('libSummary').textContent = `${s.documents} documents, ${s.chunks} passages. Server model: ${s.llm}. Semantic search: ${s.index.embeddings}.` +
      (s.index.errors.length ? ` ${s.index.errors.length} file(s) failed: ${s.index.errors.join('; ')}` : '');
    return working;
  } catch { $('dot').className = 'bad'; $('libText').textContent = 'server offline'; return false; }
}
async function poll() { const working = await refresh(); setTimeout(poll, working ? 1500 : 20000); }
$('libBtn').onclick = async () => {
  $('libDlg').showModal(); await refresh();
  const docs = await (await fetch('api/documents')).json();
  $('libList').innerHTML = docs.map((d) => `<div class="doc"><div>${esc(d.name)}<small>${esc(d.note || '')}</small></div>
    <div><small>${d.pages} ${d.kind === 'xlsx' ? 'sheets' : 'pages'} · ${d.chunks} passages</small></div></div>`).join('') || '<p>No documents yet. Copy files into the library folder on the server.</p>';
};
$('libClose').onclick = () => $('libDlg').close();
$('reindex').onclick = async () => { await fetch('api/reindex', { method: 'POST' }); refresh(); setTimeout(poll, 500); };

renderChats(); render(); poll(); pollLocal();
