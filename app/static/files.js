// Web file manager: browse, upload (chunked), download (zip for folders), edit.
// Every request runs as the Linux user signed in here (see app/fileops.py).
const FM = {cwd: null, home: null, entries: [], selected: new Set(), showHidden: false, editing: null, dirty: false};
const CHUNK = 8 * 1024 * 1024;
const BLOB_LIMIT = 1.5 * 1024 ** 3; // above this, without a save picker, let the browser download natively

const fmEsc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmSize = n => n == null ? '' : n < 1024 ? `${n} B` : n < 1024 ** 2 ? `${(n / 1024).toFixed(1)} KB` : n < 1024 ** 3 ? `${(n / 1024 ** 2).toFixed(1)} MB` : `${(n / 1024 ** 3).toFixed(2)} GB`;
const fmJoin = (dir, name) => (dir === '/' ? '' : dir) + '/' + name;
const fmRandom = () => Array.from(crypto.getRandomValues(new Uint8Array(12)), b => (b % 36).toString(36)).join('');

async function fmApi(url, opts = {}) {
  const r = await fetch(portalUrl(url), {...opts, headers: {'Content-Type': 'application/json', ...(opts.headers || {})}});
  let d = {};
  try { d = await r.json(); } catch {}
  if (r.status === 401) { location.href = portalUrl('/login'); throw new Error('未登入'); }
  if (r.status === 403 && d.detail === 'FM_LOGIN') { fmShowLogin(); throw new Error('請先以 Linux 帳號登入'); }
  if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
  return d;
}

// ------------------------------------------------------------------ session
async function fmEnter() {
  try {
    const s = await fmApi('/api/files/session');
    if (!s.user) return fmShowLogin();
    fmShowBrowser(s.user, s.home);
    if (!FM.cwd) await fmOpen(s.home);
  } catch (e) { log('檔案管理：' + e.message); }
}
function fmShowLogin() {
  qs('#fm-login').classList.remove('hidden');
  qs('#fm-browser').classList.add('hidden');
  qs('#fm-who').classList.add('hidden');
  FM.cwd = null;
  setTimeout(() => qs('#fm-username').focus(), 50);
}
function fmShowBrowser(user, home) {
  FM.home = home;
  qs('#fm-user').textContent = user;
  qs('#fm-login').classList.add('hidden');
  qs('#fm-browser').classList.remove('hidden');
  qs('#fm-who').classList.remove('hidden');
}
qs('#fm-login-form').onsubmit = async e => {
  e.preventDefault();
  try {
    const d = await fmApi('/api/files/login', {method: 'POST', body: JSON.stringify({username: qs('#fm-username').value.trim(), password: qs('#fm-password').value})});
    qs('#fm-password').value = '';
    fmShowBrowser(d.user, d.home);
    await fmOpen(d.home);
    log(`檔案管理：已以 ${d.user} 登入`);
  } catch (err) { alert(err.message); }
};
qs('#fm-logout').onclick = async () => { await fmApi('/api/files/logout', {method: 'POST'}).catch(() => {}); fmShowLogin(); };

// ------------------------------------------------------------------ browsing
// Items carry their full path, so selection and downloads also work on search
// results spread across folders.
const FM_IMG = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif', 'ico', 'svg']);
const FM_VIDEO = new Set(['mp4', 'webm', 'mov']), FM_AUDIO = new Set(['mp3', 'wav', 'ogg', 'm4a', 'flac']);
const FM_ICONS = {dir: '📁', img: '🖼️', video: '🎬', audio: '🎵', pdf: '📕', archive: '📦', code: '📝', table: '📊', link: '🔗', file: '📄'};
const fmExt = name => (name.includes('.') ? name.split('.').pop() : '').toLowerCase();
const fmIsDir = e => e.type === 'dir' || e.target_dir;
function fmKind(e) {
  if (fmIsDir(e)) return 'dir';
  const x = fmExt(e.name);
  if (FM_IMG.has(x)) return 'img';
  if (FM_VIDEO.has(x)) return 'video';
  if (FM_AUDIO.has(x)) return 'audio';
  if (x === 'pdf') return 'pdf';
  if (['zip', 'tar', 'gz', 'tgz', 'bz2', 'xz', '7z', 'rar'].includes(x)) return 'archive';
  if (['csv', 'tsv', 'xlsx', 'xls'].includes(x)) return 'table';
  if (['py', 'sh', 'js', 'ts', 'c', 'cpp', 'h', 'cu', 'java', 'go', 'rs', 'json', 'yaml', 'yml', 'md', 'ipynb', 'toml'].includes(x)) return 'code';
  return e.type === 'link' ? 'link' : 'file';
}
const fmRaw = e => portalUrl(`/api/files/raw?path=${encodeURIComponent(e.path)}&v=${Math.round(e.mtime)}`);
const collator = new Intl.Collator('zh-Hant', {numeric: true, sensitivity: 'base'});
const fmPref = (k, d) => { try { return localStorage.getItem('fm-' + k) || d; } catch { return d; } };
const fmSetPref = (k, v) => { try { localStorage.setItem('fm-' + k, v); } catch {} };
Object.assign(FM, {view: fmPref('view', 'list'), sortKey: fmPref('sort', 'name'), sortDir: Number(fmPref('dir', '1')), filter: '', search: null});

async function fmOpen(path) {
  try {
    const d = await fmApi(`/api/files/list?path=${encodeURIComponent(path)}`);
    FM.cwd = d.path; FM.parent = d.parent; FM.writable = d.writable;
    FM.entries = d.entries.map(e => ({...e, path: fmJoin(d.path, e.name)}));
    FM.selected.clear(); FM.search = null; FM.filter = '';
    qs('#fm-search').value = '';
    fmRender();
  } catch (e) { alert(e.message); }
}
function fmSorted(list) {
  const k = FM.sortKey, dir = FM.sortDir;
  return [...list].sort((a, b) => {
    const da = fmIsDir(a), db = fmIsDir(b);
    if (da !== db) return da ? -1 : 1; // folders first
    let r = 0;
    if (k === 'mtime') r = a.mtime - b.mtime;
    else if (k === 'size') r = (da ? 0 : a.size) - (db ? 0 : b.size);
    else if (k === 'type') r = collator.compare(fmExt(a.name), fmExt(b.name));
    return (r || collator.compare(a.name, b.name)) * (k === 'name' || r ? dir : 1);
  });
}
function fmVisible() {
  const base = FM.search ? FM.search.results : FM.entries;
  const f = FM.filter.toLowerCase();
  return fmSorted(base.filter(e => (FM.showHidden || !e.name.startsWith('.')) && (!f || FM.search || e.name.toLowerCase().includes(f))));
}
function fmRender() {
  // path bar
  const parts = FM.cwd.split('/').filter(Boolean);
  qs('#fm-crumbs').innerHTML = `<button class="fm-crumb" data-p="/">/</button>` + parts.map((p, i) =>
    `<button class="fm-crumb" data-p="/${fmEsc(parts.slice(0, i + 1).join('/'))}">${fmEsc(p)}</button>`).join('<span class="fm-sep">/</span>');
  qsa('.fm-crumb').forEach(b => b.onclick = ev => { ev.stopPropagation(); fmOpen(b.dataset.p); });
  qs('#fm-up').disabled = !FM.parent;
  qs('#fm-sort').value = FM.sortKey;
  qs('#fm-sort-dir').textContent = FM.sortDir > 0 ? '↑' : '↓';
  qsa('#fm-views .segment').forEach(b => b.classList.toggle('active', b.dataset.view === FM.view));
  qs('#fm-search-clear').classList.toggle('hidden', !FM.filter && !FM.search);
  const banner = qs('#fm-banner');
  banner.classList.toggle('hidden', !FM.search);
  if (FM.search) banner.innerHTML = `🔍 在 <code>${fmEsc(FM.search.root)}</code> 找到 ${FM.search.results.length} 個符合「${fmEsc(FM.search.query)}」的項目${FM.search.partial ? '（結果太多或花太久，只列出一部分，請把搜尋文字打得更精確）' : ''} <button class="ghost compact" id="fm-search-back">回到資料夾</button>`;
  if (FM.search) qs('#fm-search-back').onclick = fmClearSearch;

  const list = fmVisible();
  FM.shown = list;
  const box = qs('#fm-view');
  const inSearch = !!FM.search;
  if (FM.view === 'list') {
    const th = (k, label, cls = '') => `<th class="fm-sortable ${cls}" data-sortkey="${k}">${label}${FM.sortKey === k ? `<span class="fm-arrow">${FM.sortDir > 0 ? '▲' : '▼'}</span>` : ''}</th>`;
    box.innerHTML = `<div class="table-wrap"><table class="fm-table"><thead><tr><th class="fm-check"><input type="checkbox" id="fm-all"></th>${th('name', '名稱')}${inSearch ? '<th>位置</th>' : ''}${th('size', '大小', 'fm-num')}${th('mtime', '修改時間')}${th('type', '類型')}<th>權限</th><th></th></tr></thead><tbody>${
      list.map((e, i) => `<tr data-i="${i}" class="${FM.selected.has(e.path) ? 'selected' : ''}">
        <td class="fm-check"><input type="checkbox" data-sel="${i}" ${FM.selected.has(e.path) ? 'checked' : ''}></td>
        <td><button class="fm-name ${fmIsDir(e) ? 'dir' : ''}" data-open="${i}"><span class="fm-ico">${FM_ICONS[fmKind(e)]}</span>${fmEsc(e.name)}</button>${e.type === 'link' ? `<span class="muted small"> → ${fmEsc(e.link)}</span>` : ''}</td>
        ${inSearch ? `<td class="fm-loc"><button class="fm-crumb" data-goto="${fmEsc(e.dir)}">${fmEsc(e.dir)}</button></td>` : ''}
        <td class="fm-num">${fmIsDir(e) ? '' : fmSize(e.size)}</td>
        <td class="fm-time">${new Date(e.mtime * 1000).toLocaleString('zh-TW', {hour12: false})}</td>
        <td class="fm-type">${fmIsDir(e) ? '資料夾' : (fmExt(e.name) || '—').toUpperCase()}</td>
        <td class="fm-mode">${fmEsc(e.mode)}</td>
        <td class="fm-actions"><button class="ghost compact" data-dl="${i}">下載</button><button class="ghost compact" data-ren="${i}">改名</button><button class="ghost compact fm-danger" data-rm="${i}">刪除</button></td></tr>`).join('')
      || `<tr><td colspan="8" class="muted">${inSearch ? '找不到符合的項目' : FM.filter ? '這個資料夾沒有符合的項目' : '這個資料夾是空的'}</td></tr>`}</tbody></table></div>`;
    qsa('.fm-sortable').forEach(h => h.onclick = () => fmSetSort(h.dataset.sortkey, FM.sortKey === h.dataset.sortkey ? -FM.sortDir : 1));
    qs('#fm-all').onchange = ev => { list.forEach(e => ev.target.checked ? FM.selected.add(e.path) : FM.selected.delete(e.path)); fmRender(); };
  } else {
    box.innerHTML = `<div class="fm-grid ${FM.view}">${list.map((e, i) => {
      const kind = fmKind(e), thumb = kind === 'img' && e.size < 25 * 1024 ** 2;
      return `<div class="fm-tile ${FM.selected.has(e.path) ? 'selected' : ''}" data-open="${i}" title="${fmEsc(e.name)}\n${fmIsDir(e) ? '' : fmSize(e.size) + ' · '}${new Date(e.mtime * 1000).toLocaleString('zh-TW', {hour12: false})}${inSearch ? '\n' + fmEsc(e.dir) : ''}">
        <input type="checkbox" class="fm-tile-check" data-sel="${i}" ${FM.selected.has(e.path) ? 'checked' : ''}>
        <div class="fm-thumb">${thumb ? `<img loading="lazy" decoding="async" src="${fmRaw(e)}" alt="" onerror="this.replaceWith(document.createTextNode('🖼️'))">` : `<span>${FM_ICONS[kind]}</span>`}</div>
        <div class="fm-tile-name">${fmEsc(e.name)}</div></div>`;
    }).join('') || `<p class="muted">${inSearch ? '找不到符合的項目' : '這個資料夾是空的'}</p>`}</div>`;
  }
  qsa('[data-open]').forEach(el => el.onclick = ev => { if (ev.target.matches('input')) return; fmActivate(list[+el.dataset.open]); });
  qsa('[data-sel]').forEach(c => { c.onclick = ev => ev.stopPropagation(); c.onchange = () => { const e = list[+c.dataset.sel]; c.checked ? FM.selected.add(e.path) : FM.selected.delete(e.path); fmSelChanged(); }; });
  qsa('[data-goto]').forEach(b => b.onclick = () => fmOpen(b.dataset.goto));
  qsa('[data-dl]').forEach(b => b.onclick = () => fmDownload([list[+b.dataset.dl]]));
  qsa('[data-ren]').forEach(b => b.onclick = () => fmRename(list[+b.dataset.ren]));
  qsa('[data-rm]').forEach(b => b.onclick = () => fmDelete([list[+b.dataset.rm]]));
  fmSelChanged();
}
function fmSelectedItems() {
  const all = [...FM.entries, ...(FM.search ? FM.search.results : [])];
  return [...FM.selected].map(p => all.find(e => e.path === p)).filter(Boolean);
}
function fmSelChanged() {
  const items = fmSelectedItems(), n = items.length;
  qs('#fm-dl-sel').disabled = qs('#fm-del-sel').disabled = !n;
  qs('#fm-dl-sel').textContent = n ? `⬇ 下載所選（${n}）` : '⬇ 下載所選';
  qsa('[data-sel]').forEach(c => (c.closest('tr') || c.closest('.fm-tile')).classList.toggle('selected', c.checked));
  const shown = FM.shown || [], dirs = shown.filter(fmIsDir).length, bytes = items.filter(e => !fmIsDir(e)).reduce((a, e) => a + e.size, 0);
  qs('#fm-status').textContent = `${dirs} 個資料夾、${shown.length - dirs} 個檔案` + (n ? ` · 已選 ${n} 項${bytes ? `（${fmSize(bytes)}）` : ''}` : '') + (FM.writable === false && !FM.search ? ' · 這個資料夾你沒有寫入權限' : '');
}
function fmSetSort(key, dir) {
  FM.sortKey = key; FM.sortDir = dir;
  fmSetPref('sort', key); fmSetPref('dir', String(dir));
  fmRender();
}
function fmActivate(e) {
  if (fmIsDir(e)) return fmOpen(e.path);
  fmPreview(e);
}
qs('#fm-sort').onchange = ev => fmSetSort(ev.target.value, FM.sortDir);
qs('#fm-sort-dir').onclick = () => fmSetSort(FM.sortKey, -FM.sortDir);
qsa('#fm-views .segment').forEach(b => b.onclick = () => { FM.view = b.dataset.view; fmSetPref('view', FM.view); fmRender(); });
qs('#fm-up').onclick = () => FM.parent && fmOpen(FM.parent);
qs('#fm-refresh').onclick = () => (FM.search ? fmSearchDeep(FM.search.query) : fmOpen(FM.cwd));
qs('#fm-show-hidden').onchange = ev => { FM.showHidden = ev.target.checked; fmRender(); };
qs('#fm-dl-sel').onclick = () => fmDownload(fmSelectedItems());
qs('#fm-del-sel').onclick = () => fmDelete(fmSelectedItems());

// Type a path directly (click the empty part of the path bar).
qs('#fm-crumbs').onclick = () => {
  const inp = qs('#fm-path-input');
  qs('#fm-crumbs').classList.add('hidden'); inp.classList.remove('hidden');
  inp.value = FM.cwd; inp.focus(); inp.select();
};
qs('#fm-path-input').onkeydown = ev => {
  if (ev.key === 'Enter') { fmOpen(ev.target.value.trim()); ev.target.blur(); }
  if (ev.key === 'Escape') ev.target.blur();
};
qs('#fm-path-input').onblur = () => { qs('#fm-path-input').classList.add('hidden'); qs('#fm-crumbs').classList.remove('hidden'); };

// Search: typing filters this folder; Enter searches every sub-folder on the server.
async function fmSearchDeep(q) {
  qs('#fm-status').textContent = `搜尋「${q}」中…`;
  try {
    const d = await fmApi(`/api/files/search?path=${encodeURIComponent(FM.cwd)}&q=${encodeURIComponent(q)}`);
    d.results = d.results.map(e => ({...e, path: fmJoin(e.dir, e.name)}));
    FM.search = d; FM.selected.clear();
    fmRender();
  } catch (e) { alert(e.message); fmSelChanged(); }
}
function fmClearSearch() {
  FM.search = null; FM.filter = ''; qs('#fm-search').value = ''; FM.selected.clear();
  fmRender();
}
qs('#fm-search').oninput = ev => { FM.filter = ev.target.value.trim(); if (!FM.search) fmRender(); };
qs('#fm-search').onkeydown = ev => {
  if (ev.key === 'Enter' && ev.target.value.trim()) fmSearchDeep(ev.target.value.trim());
  if (ev.key === 'Escape') fmClearSearch();
};
qs('#fm-search-clear').onclick = fmClearSearch;

qs('#fm-mkdir').onclick = async () => {
  const name = prompt('新資料夾名稱');
  if (!name) return;
  try { await fmApi('/api/files/mkdir', {method: 'POST', body: JSON.stringify({path: fmJoin(FM.cwd, name)})}); fmOpen(FM.cwd); } catch (e) { alert(e.message); }
};
qs('#fm-newfile').onclick = async () => {
  const name = prompt('新檔案名稱（例：notes.txt）');
  if (!name) return;
  if (FM.entries.some(e => e.name === name)) return alert('已經有同名的檔案');
  try { await fmApi('/api/files/write', {method: 'POST', body: JSON.stringify({path: fmJoin(FM.cwd, name), content: ''})}); await fmOpen(FM.cwd); fmEdit(fmJoin(FM.cwd, name)); } catch (e) { alert(e.message); }
};
async function fmRename(e) {
  const to = prompt('新名稱', e.name);
  if (!to || to === e.name) return;
  const dir = e.path.slice(0, e.path.length - e.name.length - 1) || '/';
  try { await fmApi('/api/files/rename', {method: 'POST', body: JSON.stringify({src: e.path, dst: fmJoin(dir, to)})}); FM.search ? fmSearchDeep(FM.search.query) : fmOpen(FM.cwd); } catch (err) { alert(err.message); }
}
async function fmDelete(items) {
  if (!items.length) return;
  const dirs = items.filter(fmIsDir).length;
  if (!confirm(`確定刪除 ${items.length === 1 ? `「${items[0].name}」` : `這 ${items.length} 個項目`}？${dirs ? '\n資料夾會連同裡面所有檔案一起刪除。' : ''}\n刪除後無法復原。`)) return;
  try {
    await fmApi('/api/files/delete', {method: 'POST', body: JSON.stringify({paths: items.map(e => e.path)})});
    log(`已刪除 ${items.length} 個項目`);
    FM.search ? fmSearchDeep(FM.search.query) : fmOpen(FM.cwd);
  } catch (e) { alert(e.message); }
}

// ------------------------------------------------------------------ preview
const PV = {item: null, mode: 'tail', timer: null};
function fmPreviewList() { return (FM.shown || []).filter(e => !fmIsDir(e)); }
async function fmPreview(e) {
  PV.item = e;
  clearInterval(PV.timer); PV.timer = null;
  qs('#fm-pv-follow').checked = false;
  const kind = fmKind(e), body = qs('#fm-pv-body');
  qs('#fm-pv-name').textContent = e.name;
  qs('#fm-pv-meta').textContent = `${fmSize(e.size)} · ${new Date(e.mtime * 1000).toLocaleString('zh-TW', {hour12: false})} · ${e.mode}`;
  qs('#fm-pv-textctl').classList.add('hidden');
  qs('#fm-pv-edit').classList.add('hidden');
  const list = fmPreviewList(), i = list.findIndex(x => x.path === e.path);
  qs('#fm-pv-prev').disabled = i <= 0;
  qs('#fm-pv-next').disabled = i < 0 || i >= list.length - 1;
  if (!qs('#fm-preview').open) qs('#fm-preview').showModal();
  body.className = 'fm-preview-body';
  if (kind === 'img') {
    body.classList.add('media');
    body.innerHTML = `<img src="${fmRaw(e)}" alt="${fmEsc(e.name)}" onload="document.getElementById('fm-pv-meta').textContent += ' · ' + this.naturalWidth + '×' + this.naturalHeight">`;
  } else if (kind === 'video') {
    body.classList.add('media');
    body.innerHTML = `<video src="${fmRaw(e)}" controls autoplay></video>`;
  } else if (kind === 'audio') {
    body.classList.add('media');
    body.innerHTML = `<audio src="${fmRaw(e)}" controls autoplay></audio>`;
  } else if (kind === 'pdf') {
    body.classList.add('media');
    body.innerHTML = `<iframe src="${fmRaw(e)}" title="${fmEsc(e.name)}"></iframe>`;
  } else {
    // Anything else: try to show it as text; binary files get an info panel.
    PV.mode = /\.(log|out|err)$/i.test(e.name) || e.size > 512 * 1024 ? 'tail' : 'head';
    await fmPeek();
  }
}
async function fmPeek(keepScroll) {
  const e = PV.item, body = qs('#fm-pv-body');
  let d;
  try { d = await fmApi(`/api/files/peek?path=${encodeURIComponent(e.path)}&mode=${PV.mode}`); }
  catch (err) { body.innerHTML = `<div class="fm-pv-info"><p>${fmEsc(err.message)}</p></div>`; return; }
  if (PV.item !== e) return; // user moved on meanwhile
  if (d.binary) {
    body.innerHTML = `<div class="fm-pv-info"><div class="fm-pv-icon">${FM_ICONS[fmKind(e)]}</div><p><strong>${fmEsc(e.name)}</strong></p><p class="muted">這個檔案無法在網頁上預覽（${fmSize(d.size)}）。</p><button class="primary" onclick="fmDownload([PV.item])">⬇ 下載</button></div>`;
    return;
  }
  qs('#fm-pv-textctl').classList.remove('hidden');
  qsa('[data-pvmode]').forEach(b => b.classList.toggle('active', b.dataset.pvmode === PV.mode));
  qs('#fm-pv-edit').classList.toggle('hidden', !(d.writable && d.size <= 2 * 1024 * 1024));
  const old = body.querySelector('pre'), atBottom = old && old.scrollTop + old.clientHeight >= old.scrollHeight - 30;
  body.innerHTML = `${d.truncated ? `<div class="fm-pv-note">檔案較大（${fmSize(d.size)}），只顯示${PV.mode === 'tail' ? '最後' : '最前面'} 512 KB。</div>` : ''}<pre class="fm-pv-text ${qs('#fm-pv-wrap').checked ? 'wrap' : ''}"></pre>`;
  const pre = body.querySelector('pre');
  pre.textContent = d.text;
  if (PV.mode === 'tail' && (!keepScroll || atBottom)) pre.scrollTop = pre.scrollHeight;
  if (keepScroll && !atBottom && old) pre.scrollTop = old.scrollTop;
  qs('#fm-pv-meta').textContent = `${fmSize(d.size)} · ${new Date(d.mtime * 1000).toLocaleString('zh-TW', {hour12: false})} · ${e.mode}`;
}
function fmPreviewStep(dir) {
  const list = fmPreviewList(), i = list.findIndex(x => x.path === PV.item.path);
  if (list[i + dir]) fmPreview(list[i + dir]);
}
function fmClosePreview() {
  clearInterval(PV.timer); PV.timer = null; PV.item = null;
  qs('#fm-pv-body').innerHTML = ''; // stops video / audio
  qs('#fm-preview').close();
}
qs('#fm-pv-prev').onclick = () => fmPreviewStep(-1);
qs('#fm-pv-next').onclick = () => fmPreviewStep(1);
qs('#fm-pv-close').onclick = fmClosePreview;
qs('#fm-preview').addEventListener('cancel', ev => { ev.preventDefault(); fmClosePreview(); });
qs('#fm-preview').addEventListener('keydown', ev => {
  if (ev.target.matches('input,textarea')) return;
  if (ev.key === 'ArrowLeft') { ev.preventDefault(); fmPreviewStep(-1); }
  if (ev.key === 'ArrowRight') { ev.preventDefault(); fmPreviewStep(1); }
});
qs('#fm-pv-dl').onclick = () => PV.item && fmDownload([PV.item]);
qs('#fm-pv-edit').onclick = () => { const p = PV.item.path; fmClosePreview(); fmEdit(p); };
qsa('[data-pvmode]').forEach(b => b.onclick = () => { PV.mode = b.dataset.pvmode; fmPeek(); });
qs('#fm-pv-wrap').onchange = ev => qs('.fm-pv-text')?.classList.toggle('wrap', ev.target.checked);
qs('#fm-pv-follow').onchange = ev => {
  clearInterval(PV.timer); PV.timer = null;
  if (ev.target.checked) { PV.mode = 'tail'; fmPeek(); PV.timer = setInterval(() => PV.item && fmPeek(true), 3000); }
};

// ------------------------------------------------------------------ transfers panel
function fmTask(label) {
  const panel = qs('#fm-transfers');
  panel.classList.remove('hidden');
  const el = document.createElement('div');
  el.className = 'fm-task';
  el.innerHTML = `<div class="fm-task-top"><span class="fm-task-name">${fmEsc(label)}</span><button class="ghost compact fm-task-cancel">取消</button></div>
    <div class="meter-track"><div class="meter-fill"></div></div><div class="fm-task-info muted small">準備中…</div>`;
  qs('#fm-transfer-list').prepend(el);
  const fill = el.querySelector('.meter-fill'), info = el.querySelector('.fm-task-info'), cancel = el.querySelector('.fm-task-cancel');
  const started = Date.now();
  const task = {
    cancelled: false, onCancel: null,
    progress(stage, done, total) {
      const pct = total ? Math.min(100, done / total * 100) : 0;
      fill.style.width = `${total ? pct : 100}%`;
      fill.classList.toggle('indeterminate', !total);
      const secs = (Date.now() - started) / 1000, rate = secs > 1 ? done / secs : 0;
      info.textContent = total ? `${stage} ${pct.toFixed(0)}% · ${fmSize(done)} / ${fmSize(total)}${rate ? ` · ${fmSize(rate)}/s` : ''}` : `${stage} ${fmSize(done)}`;
    },
    finish(msg, ok = true) {
      el.classList.add(ok ? 'done' : 'failed');
      fill.style.width = '100%';
      fill.classList.remove('indeterminate');
      info.textContent = msg;
      cancel.remove();
    },
  };
  cancel.onclick = () => { task.cancelled = true; task.onCancel && task.onCancel(); task.finish('已取消', false); };
  return task;
}
qs('#fm-transfers-clear').onclick = () => {
  qsa('.fm-task.done,.fm-task.failed').forEach(t => t.remove());
  if (!qs('#fm-transfer-list').children.length) qs('#fm-transfers').classList.add('hidden');
};

// ------------------------------------------------------------------ upload (chunked)
function fmSendChunk(url, blob, onProgress, task) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    task.onCancel = () => xhr.abort();
    xhr.open('POST', portalUrl(url));
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = e => onProgress(e.loaded);
    xhr.onload = () => {
      let d = {};
      try { d = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status === 403 && d.detail === 'FM_LOGIN') { fmShowLogin(); return reject(new Error('登入已過期，請重新登入')); }
      xhr.status < 300 ? resolve(d) : reject(new Error(d.detail || `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error('網路連線中斷'));
    xhr.onabort = () => reject(new Error('已取消'));
    xhr.send(blob);
  });
}
async function fmUploadFile(file, dir) {
  const dest = fmJoin(dir, file.name);
  const exists = FM.entries.some(e => e.name === file.name);
  if (exists && !confirm(`「${file.name}」已經存在，要覆蓋嗎？`)) return;
  const id = fmRandom(), task = fmTask(`⬆ ${file.name}`);
  let offset = 0;
  try {
    do {
      const blob = file.slice(offset, offset + CHUNK), last = offset + blob.size >= file.size ? 1 : 0;
      const url = `/api/files/upload?path=${encodeURIComponent(dest)}&id=${id}&offset=${offset}&last=${last}&overwrite=${exists ? 1 : 0}`;
      for (let attempt = 0; ; attempt++) {
        try { await fmSendChunk(url, blob, n => task.progress('上傳中', offset + n, file.size), task); break; }
        catch (e) { if (task.cancelled || attempt >= 2 || /已取消|登入/.test(e.message)) throw e; await new Promise(r => setTimeout(r, 1500)); }
      }
      offset += blob.size;
      task.progress('上傳中', offset, file.size);
    } while (offset < file.size);
    task.finish(`✓ 上傳完成 · ${fmSize(file.size)}`);
    log(`已上傳 ${dest}`);
  } catch (e) {
    if (!task.cancelled) task.finish('✕ ' + e.message, false);
    fmApi('/api/files/upload/abort', {method: 'POST', body: JSON.stringify({path: dest, id})}).catch(() => {});
  }
}
async function fmUpload(files) {
  if (!files.length) return;
  const dir = FM.cwd;
  for (const f of files) await fmUploadFile(f, dir);
  if (FM.cwd === dir) fmOpen(dir);
}
qs('#fm-upload').onchange = e => { fmUpload([...e.target.files]); e.target.value = ''; };
const drop = qs('#fm-drop');
drop.addEventListener('dragover', e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); drop.classList.add('over'); } });
drop.addEventListener('dragleave', e => { if (!drop.contains(e.relatedTarget)) drop.classList.remove('over'); });
drop.addEventListener('drop', e => {
  e.preventDefault(); drop.classList.remove('over');
  const items = [...(e.dataTransfer.items || [])];
  if (items.some(i => i.webkitGetAsEntry && i.webkitGetAsEntry()?.isDirectory)) alert('目前只支援上傳檔案；資料夾請先壓縮成 zip 再上傳。');
  fmUpload([...e.dataTransfer.files].filter((f, i) => !(items[i]?.webkitGetAsEntry && items[i].webkitGetAsEntry()?.isDirectory)));
});

// ------------------------------------------------------------------ download (zip for folders / multi-select)
async function fmPickSaveTarget(name) {
  if (!window.showSaveFilePicker) return null;
  try { return await window.showSaveFilePicker({suggestedName: name}); }
  catch (e) { if (e.name === 'AbortError') throw e; return null; }
}
async function fmFetchToDisk(url, name, size, handle, task) {
  if (!handle && size > BLOB_LIMIT) {
    // Very large file and no save picker: hand it to the browser's own downloader.
    const a = document.createElement('a');
    a.href = portalUrl(url); a.download = name; document.body.appendChild(a); a.click(); a.remove();
    task.finish('已交給瀏覽器下載（檔案很大，進度請看瀏覽器的下載列）');
    return;
  }
  const ctrl = new AbortController();
  task.onCancel = () => ctrl.abort();
  const r = await fetch(portalUrl(url), {signal: ctrl.signal});
  if (!r.ok) { let d = {}; try { d = await r.json(); } catch {} throw new Error(d.detail || `HTTP ${r.status}`); }
  const total = Number(r.headers.get('Content-Length')) || size || 0;
  const reader = r.body.getReader(), writer = handle ? await handle.createWritable() : null, parts = [];
  let done = 0;
  try {
    for (;;) {
      const {value, done: end} = await reader.read();
      if (end) break;
      writer ? await writer.write(value) : parts.push(value);
      done += value.length;
      task.progress('下載中', done, total);
    }
    if (writer) await writer.close();
  } catch (e) { if (writer) await writer.abort().catch(() => {}); throw e; }
  if (!writer) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(parts)); a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }
  task.finish(`✓ 下載完成 · ${fmSize(done)}`);
}
async function fmDownload(entries) {
  entries = entries.filter(Boolean);
  if (!entries.length) return;
  const single = entries.length === 1 && entries[0].type !== 'dir' && !entries[0].target_dir;
  const zipName = entries.length === 1 ? `${entries[0].name}.zip` : `${FM.cwd.split('/').pop() || 'files'}.zip`;
  const saveName = single ? entries[0].name : zipName;
  let handle;
  try { handle = await fmPickSaveTarget(saveName); } catch { return; } // user closed the save dialog
  const task = fmTask(`⬇ ${saveName}`);
  try {
    if (single) {
      await fmFetchToDisk(`/api/files/download?path=${encodeURIComponent(entries[0].path)}`, saveName, entries[0].size, handle, task);
      return;
    }
    // 1) compress on the server with progress, 2) download the finished zip
    const job = await fmApi('/api/files/zip', {method: 'POST', body: JSON.stringify({paths: entries.map(e => e.path)})});
    task.onCancel = () => fmApi(`/api/files/zip/${job.id}/cancel`, {method: 'POST'}).catch(() => {});
    let st;
    for (;;) {
      if (task.cancelled) return;
      st = await fmApi(`/api/files/zip/${job.id}`);
      task.progress(`壓縮中（${job.files} 個檔案）`, st.done, st.total);
      if (st.state === 'ready') break;
      if (st.state === 'error') throw new Error(st.error || '壓縮失敗');
      await new Promise(r => setTimeout(r, 700));
    }
    await fmFetchToDisk(`/api/files/zip/${job.id}/download`, zipName, st.size, handle, task);
  } catch (e) {
    if (!task.cancelled) task.finish('✕ ' + (e.name === 'AbortError' ? '已取消' : e.message), false);
  }
}

// ------------------------------------------------------------------ editor
async function fmEdit(path) {
  try {
    const d = await fmApi(`/api/files/read?path=${encodeURIComponent(path)}`);
    FM.editing = path; FM.dirty = false;
    qs('#fm-editor-path').textContent = path;
    qs('#fm-editor-text').value = d.content;
    qs('#fm-editor-text').readOnly = !d.writable;
    qs('#fm-editor-save').disabled = !d.writable;
    qs('#fm-editor-state').textContent = d.writable ? `${fmSize(d.size)}` : '唯讀（你沒有寫入權限）';
    qs('#fm-editor').showModal();
    qs('#fm-editor-text').focus();
  } catch (e) { alert(e.message); }
}
async function fmSave() {
  if (!FM.editing || qs('#fm-editor-save').disabled) return;
  try {
    await fmApi('/api/files/write', {method: 'POST', body: JSON.stringify({path: FM.editing, content: qs('#fm-editor-text').value})});
    FM.dirty = false;
    qs('#fm-editor-state').textContent = `已儲存 ${new Date().toLocaleTimeString('zh-TW', {hour12: false})}`;
    log(`已儲存 ${FM.editing}`);
  } catch (e) { alert('儲存失敗：' + e.message); }
}
qs('#fm-editor-text').oninput = () => { FM.dirty = true; qs('#fm-editor-state').textContent = '尚未儲存'; };
qs('#fm-editor-text').onkeydown = e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); fmSave(); }
  if (e.key === 'Tab') { e.preventDefault(); document.execCommand('insertText', false, '    '); }
};
qs('#fm-editor-save').onclick = fmSave;
function fmCloseEditor() {
  if (FM.dirty && !confirm('有尚未儲存的修改，確定要關閉嗎？')) return false;
  qs('#fm-editor').close(); FM.editing = null; FM.dirty = false;
  if (FM.cwd) fmOpen(FM.cwd);
  return true;
}
qs('#fm-editor-close').onclick = fmCloseEditor;
qs('#fm-editor').addEventListener('cancel', e => { e.preventDefault(); fmCloseEditor(); });
window.addEventListener('beforeunload', e => {
  if (FM.dirty || qsa('.fm-task:not(.done):not(.failed)').length) { e.preventDefault(); e.returnValue = ''; }
});
