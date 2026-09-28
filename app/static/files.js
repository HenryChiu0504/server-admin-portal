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
async function fmOpen(path) {
  try {
    const d = await fmApi(`/api/files/list?path=${encodeURIComponent(path)}`);
    FM.cwd = d.path; FM.parent = d.parent; FM.entries = d.entries; FM.writable = d.writable;
    FM.selected.clear();
    fmRender();
  } catch (e) { alert(e.message); }
}
function fmRender() {
  const parts = FM.cwd.split('/').filter(Boolean);
  qs('#fm-crumbs').innerHTML = `<button class="fm-crumb" data-p="/">/</button>` + parts.map((p, i) =>
    `<button class="fm-crumb" data-p="/${fmEsc(parts.slice(0, i + 1).join('/'))}">${fmEsc(p)}</button>`).join('<span class="fm-sep">/</span>');
  qsa('.fm-crumb').forEach(b => b.onclick = () => fmOpen(b.dataset.p));
  qs('#fm-up').disabled = !FM.parent;
  const list = FM.entries.filter(e => FM.showHidden || !e.name.startsWith('.'));
  const isDir = e => e.type === 'dir' || e.target_dir;
  qs('#fm-rows').innerHTML = list.map(e => {
    const path = fmJoin(FM.cwd, e.name), dir = isDir(e);
    return `<tr class="${FM.selected.has(e.name) ? 'selected' : ''}">
      <td class="fm-check"><input type="checkbox" data-sel="${fmEsc(e.name)}" ${FM.selected.has(e.name) ? 'checked' : ''}></td>
      <td><button class="fm-name ${dir ? 'dir' : ''}" data-open="${fmEsc(e.name)}">${dir ? '📁' : e.type === 'link' ? '🔗' : '📄'} ${fmEsc(e.name)}</button>${e.type === 'link' ? `<span class="muted small"> → ${fmEsc(e.link)}</span>` : ''}</td>
      <td class="fm-num">${dir ? '' : fmSize(e.size)}</td>
      <td class="fm-time">${new Date(e.mtime * 1000).toLocaleString('zh-TW', {hour12: false})}</td>
      <td class="fm-mode">${fmEsc(e.mode)}</td>
      <td class="fm-actions">${dir ? '' : `<button class="ghost compact" data-edit="${fmEsc(e.name)}">編輯</button>`}<button class="ghost compact" data-dl="${fmEsc(e.name)}">下載</button><button class="ghost compact" data-ren="${fmEsc(e.name)}">改名</button><button class="ghost compact fm-danger" data-rm="${fmEsc(e.name)}">刪除</button></td></tr>`;
  }).join('') || '<tr><td colspan="6" class="muted">這個資料夾是空的</td></tr>';
  qsa('[data-open]').forEach(b => b.onclick = () => {
    const e = FM.entries.find(x => x.name === b.dataset.open);
    isDir(e) ? fmOpen(fmJoin(FM.cwd, e.name)) : fmDownload([e.name]);
  });
  qsa('[data-sel]').forEach(c => c.onchange = () => { c.checked ? FM.selected.add(c.dataset.sel) : FM.selected.delete(c.dataset.sel); fmSelChanged(); });
  qsa('[data-edit]').forEach(b => b.onclick = () => fmEdit(fmJoin(FM.cwd, b.dataset.edit)));
  qsa('[data-dl]').forEach(b => b.onclick = () => fmDownload([b.dataset.dl]));
  qsa('[data-ren]').forEach(b => b.onclick = () => fmRename(b.dataset.ren));
  qsa('[data-rm]').forEach(b => b.onclick = () => fmDelete([b.dataset.rm]));
  qs('#fm-all').checked = false;
  fmSelChanged();
  const files = FM.entries.filter(e => !isDir(e)).length;
  qs('#fm-status').textContent = `${FM.entries.length - files} 個資料夾、${files} 個檔案${FM.writable ? '' : ' · 這個資料夾你沒有寫入權限'}`;
}
function fmSelChanged() {
  const n = FM.selected.size;
  qs('#fm-dl-sel').disabled = qs('#fm-del-sel').disabled = !n;
  qs('#fm-dl-sel').textContent = n ? `⬇ 下載所選（${n}）` : '⬇ 下載所選';
  qsa('#fm-rows tr').forEach(tr => { const c = tr.querySelector('[data-sel]'); if (c) tr.classList.toggle('selected', c.checked); });
}
qs('#fm-all').onchange = e => {
  FM.entries.filter(x => FM.showHidden || !x.name.startsWith('.')).forEach(x => e.target.checked ? FM.selected.add(x.name) : FM.selected.delete(x.name));
  qsa('[data-sel]').forEach(c => c.checked = e.target.checked);
  fmSelChanged();
};
qs('#fm-up').onclick = () => FM.parent && fmOpen(FM.parent);
qs('#fm-refresh').onclick = () => fmOpen(FM.cwd);
qs('#fm-show-hidden').onchange = e => { FM.showHidden = e.target.checked; fmRender(); };
qs('#fm-dl-sel').onclick = () => fmDownload([...FM.selected]);
qs('#fm-del-sel').onclick = () => fmDelete([...FM.selected]);

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
async function fmRename(name) {
  const to = prompt('新名稱', name);
  if (!to || to === name) return;
  try { await fmApi('/api/files/rename', {method: 'POST', body: JSON.stringify({src: fmJoin(FM.cwd, name), dst: fmJoin(FM.cwd, to)})}); fmOpen(FM.cwd); } catch (e) { alert(e.message); }
}
async function fmDelete(names) {
  const dirs = names.filter(n => { const e = FM.entries.find(x => x.name === n); return e && e.type === 'dir'; });
  if (!confirm(`確定刪除 ${names.length === 1 ? `「${names[0]}」` : `這 ${names.length} 個項目`}？${dirs.length ? '\n資料夾會連同裡面所有檔案一起刪除。' : ''}\n刪除後無法復原。`)) return;
  try { await fmApi('/api/files/delete', {method: 'POST', body: JSON.stringify({paths: names.map(n => fmJoin(FM.cwd, n))})}); log(`已刪除 ${names.length} 個項目`); fmOpen(FM.cwd); } catch (e) { alert(e.message); }
}

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
async function fmDownload(names) {
  const entries = names.map(n => FM.entries.find(e => e.name === n)).filter(Boolean);
  if (!entries.length) return;
  const single = entries.length === 1 && entries[0].type !== 'dir' && !entries[0].target_dir;
  const zipName = entries.length === 1 ? `${entries[0].name}.zip` : `${FM.cwd.split('/').pop() || 'files'}.zip`;
  const saveName = single ? entries[0].name : zipName;
  let handle;
  try { handle = await fmPickSaveTarget(saveName); } catch { return; } // user closed the save dialog
  const task = fmTask(`⬇ ${saveName}`);
  try {
    if (single) {
      await fmFetchToDisk(`/api/files/download?path=${encodeURIComponent(fmJoin(FM.cwd, entries[0].name))}`, saveName, entries[0].size, handle, task);
      return;
    }
    // 1) compress on the server with progress, 2) download the finished zip
    const job = await fmApi('/api/files/zip', {method: 'POST', body: JSON.stringify({paths: entries.map(e => fmJoin(FM.cwd, e.name))})});
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
