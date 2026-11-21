// Thin client: all heavy work (search, page rendering, the language model) happens on the server.
const $ = (id) => document.getElementById(id);
const MARK = '<<<FOLLOWUPS>>>';
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
let messages = store.get('chat', []);
let busy = false;
let ref = null; // {docId, page, pages, chunkId}

// ---------- tiny safe markdown ----------
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
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
  if (!messages.length) {
    t.innerHTML = `<div class="welcome"><h1>Ask your standards anything</h1>
      <p>Answers come only from your company's own documents. Every fact is cited, and you can open the exact page to check it.</p></div>`;
    return;
  }
  t.innerHTML = '';
  messages.forEach((m, idx) => {
    const el = document.createElement('div');
    el.className = 'msg ' + (m.role === 'user' ? 'user' : 'bot');
    if (m.role === 'user') { el.innerHTML = `<div class="bubble">${esc(m.content)}</div>`; }
    else {
      const { text, follow } = split(m.content || '');
      const last = idx === messages.length - 1;
      let h = '';
      if (m.sources?.length) {
        h += '<div class="sources">' + m.sources.map((s) =>
          `<button class="src" data-i="${idx}" data-n="${s.n}"><b><em>${s.n}</em>${esc(s.doc)}</b><span>${esc(s.loc)}</span></button>`).join('') + '</div>';
      }
      if (text) h += `<div class="bubble">${md(text)}</div>`;
      if (m.status && last && busy) h += `<div class="status"><i></i>${esc(m.status)}…</div>`;
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
async function ask(q) {
  q = q.trim(); if (!q || busy) return;
  busy = true; $('send').disabled = true;
  messages.push({ role: 'user', content: q });
  const bot = { role: 'assistant', content: '', sources: [], status: 'Searching the standards' };
  messages.push(bot); render();
  let firstSource = true;
  try {
    const res = await fetch('api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ style: $('style').value, messages: messages.slice(0, -1).map((m) => ({ role: m.role, content: m.content })) }),
    });
    if (!res.ok) throw new Error('Server error ' + res.status);
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf('\n')) > -1) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1); if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.type === 'status') bot.status = ev.message;
        else if (ev.type === 'sources') {
          bot.sources = ev.sources; bot.status = 'Writing the answer';
          if (firstSource && ev.sources[0] && window.innerWidth > 860) { firstSource = false; openSource(ev.sources[0]); }
        } else if (ev.type === 'delta') { bot.content += ev.text; bot.status = ''; }
        else if (ev.type === 'error') bot.error = ev.message;
        render();
      }
    }
  } catch (err) { bot.error = 'Could not reach the server. ' + err.message; }
  bot.status = ''; busy = false; $('send').disabled = false;
  store.set('chat', messages.slice(-40)); render();
}
$('composer').addEventListener('submit', (e) => { e.preventDefault(); const v = $('input').value; $('input').value = ''; grow(); ask(v); });
$('input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('composer').requestSubmit(); } });
function grow() { const i = $('input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight, 160) + 'px'; }
$('input').addEventListener('input', grow);
$('newBtn').onclick = () => { if (busy) return; messages = []; store.set('chat', []); closeRef(); render(); $('input').focus(); };
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
    const c = await (await fetch(`api/chunks/${s.chunk_id}`)).json();
    if (isPdf && ref) { ref.pages = c.pages; label(); }
    else $('refBody').innerHTML = `<div class="textRef">${md(c.text || 'This source is no longer available.', false)}</div>`;
  } catch { if (!isPdf) $('refBody').innerHTML = '<div class="textRef">Could not load this source.</div>'; }
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

// ---------- status + library ----------
async function refresh() {
  try {
    const s = await (await fetch('api/status')).json();
    document.title = s.app; $('appName').textContent = s.app;
    const working = s.index.state !== 'idle';
    $('dot').className = working ? 'busy' : 'ok';
    $('libText').textContent = working ? `indexing ${s.index.done}/${s.index.total}` : `${s.documents} documents`;
    $('libSummary').textContent = `${s.documents} documents, ${s.chunks} passages. Model: ${s.llm}. Semantic search: ${s.index.embeddings}.` +
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

render(); poll();
