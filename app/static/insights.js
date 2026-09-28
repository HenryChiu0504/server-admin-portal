// GPU processes, metric history charts, and disk usage pages.
// Uses api(), qs(), qsa(), log() from app.js.

const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const pageActive = id => qs(`#page-${id}`)?.classList.contains('active');

function fmtBytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`;
}
function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d) return `${d} 天 ${h} 小時`;
  if (h) return `${h} 小時 ${m} 分`;
  return `${m} 分`;
}
const fmtMB = mb => mb == null ? '—' : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;

// ---------------------------------------------------------------- GPU processes
const signalled = new Set();

async function refreshGpuProcs() {
  let d;
  try { d = await api('/api/gpu/processes'); } catch (e) { log('讀取 GPU 程序失敗：' + e.message); return; }
  const byGpu = {};
  d.processes.forEach(p => (byGpu[p.gpu] = byGpu[p.gpu] || []).push(p));
  const busy = d.gpus.filter(g => byGpu[g.index]?.length).length;
  const badge = qs('#gpu-summary');
  badge.textContent = d.gpus.length ? `${busy} / ${d.gpus.length} 張使用中` : '未偵測到 GPU';
  badge.className = 'badge ' + (busy ? 'ok' : '');

  // Per-user totals across all GPUs.
  const users = {};
  d.processes.forEach(p => {
    const k = p.user || '未知';
    const u = users[k] = users[k] || {gpus: new Set(), mb: 0, n: 0};
    u.gpus.add(p.gpu); u.mb += p.vram_mb || 0; u.n++;
  });
  qs('#gpu-users').innerHTML = Object.entries(users).sort((a, b) => b[1].mb - a[1].mb).map(([name, u]) =>
    `<span class="user-chip"><strong>${esc(name)}</strong><span>${u.gpus.size} 張 GPU · ${u.n} 個程式 · ${fmtMB(u.mb)}</span></span>`).join('');

  qs('#gpu-cards').innerHTML = d.gpus.map(g => {
    const procs = byGpu[g.index] || [];
    const rows = procs.map(p => {
      const again = signalled.has(p.pid);
      return `<div class="proc-row">
        <div class="proc-main"><div class="proc-who"><strong>${esc(p.user || '未知')}</strong>${p.container ? `<span class="tag">${esc(p.container)}</span>` : ''}</div>
          <code class="proc-cmd" title="${esc(p.command)}">${esc(p.command)}</code></div>
        <div class="proc-stat"><span>${fmtMB(p.vram_mb)}</span><small>${p.started ? fmtDuration(d.now - p.started) : '—'} · PID ${p.pid}</small></div>
        <button class="${again ? 'danger' : 'ghost'} proc-kill" data-pid="${p.pid}" data-force="${again ? 1 : 0}">${again ? '強制結束' : '結束'}</button>
      </div>`;
    }).join('');
    return `<article class="card gpu-card">
      <div class="card-head"><div><h3>GPU ${g.index}</h3><p class="muted small">${esc(g.name)}</p></div>
        <span class="badge ${procs.length ? 'ok' : ''}">${procs.length ? `${procs.length} 個程式` : '閒置'}</span></div>
      <div class="gpu-stats"><div><small>使用率</small><strong>${g.utilization.toFixed(0)}%</strong></div>
        <div><small>VRAM</small><strong>${(g.vram_used / 1024).toFixed(1)} / ${(g.vram_total / 1024).toFixed(0)} GB</strong></div>
        <div><small>溫度</small><strong>${g.temperature.toFixed(0)}°C</strong></div></div>
      <div class="meter-track"><div class="meter-fill" style="width:${g.vram_percent}%"></div></div>
      ${rows ? `<div class="proc-list">${rows}</div>` : '<p class="muted small empty-note">沒有程式在使用這張 GPU</p>'}
    </article>`;
  }).join('') || '<article class="card"><p class="muted">未偵測到 NVIDIA GPU（找不到 nvidia-smi）。</p></article>';

  qsa('.proc-kill').forEach(b => b.onclick = () => killGpuProc(+b.dataset.pid, b.dataset.force === '1', procsByPid(d.processes, +b.dataset.pid)));
}
const procsByPid = (list, pid) => list.find(p => p.pid === pid) || {};

async function killGpuProc(pid, force, p) {
  const who = `${p.user || '未知'} 的 PID ${pid}（GPU ${p.gpu}）`;
  const msg = force
    ? `程式沒有回應正常結束。要強制結束 ${who} 嗎？\n強制結束不會讓程式存檔，未儲存的訓練進度會遺失。`
    : `確定要結束 ${who} 嗎？\n\n${p.command || ''}\n\n程式會收到結束訊號，有機會先存檔再關閉。`;
  if (!confirm(msg)) return;
  try {
    const r = await api(`/api/gpu/processes/${pid}/kill`, {method: 'POST', body: JSON.stringify({force})});
    log(r.message);
    signalled.add(pid);
    setTimeout(refreshGpuProcs, 1500);
  } catch (e) { log('結束程式失敗：' + e.message); alert(e.message); }
}
setInterval(() => { if (pageActive('gpu')) refreshGpuProcs(); }, 5000);

// ---------------------------------------------------------------- History chart
const HIST = {metric: 'temperature', range: '24h', hidden: new Set(), data: null};
const HIST_META = {
  temperature: {title: 'GPU 溫度', unit: '°C'},
  utilization: {title: 'GPU 使用率', unit: '%', max: 100},
  fan: {title: 'GPU 風扇轉速', unit: '%', max: 100},
  vram: {title: 'GPU VRAM 使用量', unit: '%', max: 100},
  system: {title: 'CPU／記憶體使用率', unit: '%', max: 100},
};
const seriesColor = i => `var(--s${(i % 8) + 1})`;

function bindSegmented(id, key) {
  qsa(`#${id} .segment`).forEach(b => b.onclick = () => {
    qsa(`#${id} .segment`).forEach(x => x.classList.toggle('active', x === b));
    HIST[key] = b.dataset.v;
    if (key === 'metric') HIST.hidden.clear();
    refreshHistory();
  });
}
bindSegmented('hist-metric', 'metric');
bindSegmented('hist-range', 'range');

async function refreshHistory() {
  try { HIST.data = await api(`/api/history?metric=${HIST.metric}&range=${HIST.range}`); }
  catch (e) { log('讀取歷史資料失敗：' + e.message); return; }
  drawHistory();
}
setInterval(() => { if (pageActive('history')) refreshHistory(); }, 60000);
window.addEventListener('resize', () => { if (pageActive('history') && HIST.data) drawHistory(); });

function drawHistory() {
  const d = HIST.data, meta = HIST_META[d.metric];
  qs('#hist-title').textContent = meta.title;
  const fmtT = t => new Date(t * 1000).toLocaleString('zh-TW', d.range === '7d'
    ? {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false}
    : {hour: '2-digit', minute: '2-digit', hour12: false});
  const fmtV = v => v == null ? '—' : `${v.toFixed(meta.unit === '°C' ? 0 : 0)}${meta.unit}`;
  qs('#hist-sub').textContent = `每 ${Math.round(d.bucket / 60)} 分鐘平均一點`;

  // Legend: identity is never color-only (label + latest value in text).
  qs('#hist-legend').innerHTML = d.series.map((s, i) => {
    const last = s.points.length ? s.points[s.points.length - 1][1] : null;
    return `<button class="legend-item ${HIST.hidden.has(s.key) ? 'off' : ''}" data-key="${s.key}"><span class="swatch" style="background:${seriesColor(i)}"></span>${esc(s.label)}<strong>${fmtV(last)}</strong></button>`;
  }).join('');
  qsa('#hist-legend .legend-item').forEach(b => b.onclick = () => {
    HIST.hidden.has(b.dataset.key) ? HIST.hidden.delete(b.dataset.key) : HIST.hidden.add(b.dataset.key);
    drawHistory();
  });

  const box = qs('#hist-chart'), W = Math.max(320, box.clientWidth), H = 320;
  const M = {l: 46, r: 14, t: 12, b: 30}, iw = W - M.l - M.r, ih = H - M.t - M.b;
  const shown = d.series.map((s, i) => ({...s, i})).filter(s => !HIST.hidden.has(s.key));
  const vals = shown.flatMap(s => s.points.map(p => p[1])).filter(v => v != null);
  if (!d.series.some(s => s.points.length)) {
    box.innerHTML = '<div class="chart-empty">尚無資料。Portal 更新後每分鐘記錄一次，請稍後再回來看。</div>';
    return;
  }
  let y0 = 0, y1 = meta.max || 100;
  if (!meta.max && vals.length) {
    y0 = Math.max(0, Math.floor((Math.min(...vals) - 5) / 10) * 10);
    y1 = Math.ceil((Math.max(...vals) + 5) / 10) * 10;
  }
  const x = t => M.l + (t - d.since) / (d.until - d.since) * iw;
  const y = v => M.t + ih - (v - y0) / (y1 - y0) * ih;

  const yTicks = [];
  const step = (y1 - y0) / 4;
  for (let v = y0; v <= y1 + 1e-9; v += step) yTicks.push(v);
  const xTicks = [];
  for (let k = 0; k <= 6; k++) xTicks.push(d.since + (d.until - d.since) * k / 6);

  const paths = shown.map(s => {
    // Break the line where samples are missing (e.g. Portal was restarting).
    let dStr = '', prev = null;
    s.points.forEach(([t, v]) => {
      if (v == null) { prev = null; return; }
      dStr += `${prev == null || t - prev > d.bucket * 2.5 ? 'M' : 'L'}${x(t).toFixed(1)},${y(v).toFixed(1)}`;
      prev = t;
    });
    return `<path d="${dStr}" fill="none" stroke="${seriesColor(s.i)}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join('');

  box.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${meta.title}">
    ${yTicks.map(v => `<line class="grid" x1="${M.l}" x2="${W - M.r}" y1="${y(v)}" y2="${y(v)}"/><text class="tick" x="${M.l - 8}" y="${y(v) + 4}" text-anchor="end">${Math.round(v)}${meta.unit}</text>`).join('')}
    ${xTicks.map(t => `<text class="tick" x="${x(t)}" y="${H - 8}" text-anchor="middle">${fmtT(t)}</text>`).join('')}
    <line class="axis" x1="${M.l}" x2="${W - M.r}" y1="${M.t + ih}" y2="${M.t + ih}"/>
    ${paths}
    <line class="crosshair hidden" y1="${M.t}" y2="${M.t + ih}"/>
    <g class="dots"></g>
    <rect class="hit" x="${M.l}" y="${M.t}" width="${iw}" height="${ih}" fill="transparent"/>
  </svg>`;

  // Hover: crosshair + dots + tooltip listing every visible series.
  const svg = box.querySelector('svg'), tip = qs('#hist-tip'), cross = svg.querySelector('.crosshair'), dots = svg.querySelector('.dots');
  const times = [...new Set(shown.flatMap(s => s.points.map(p => p[0])))].sort((a, b) => a - b);
  svg.querySelector('.hit').onmousemove = ev => {
    if (!times.length) return;
    const r = svg.getBoundingClientRect(), mx = (ev.clientX - r.left) * (W / r.width);
    const t0 = d.since + (mx - M.l) / iw * (d.until - d.since);
    const t = times.reduce((a, b) => Math.abs(b - t0) < Math.abs(a - t0) ? b : a);
    cross.setAttribute('x1', x(t)); cross.setAttribute('x2', x(t)); cross.classList.remove('hidden');
    const at = shown.map(s => ({s, v: (s.points.find(p => p[0] === t) || [])[1]})).filter(o => o.v != null);
    dots.innerHTML = at.map(o => `<circle cx="${x(t)}" cy="${y(o.v)}" r="4" fill="${seriesColor(o.s.i)}" class="dot"/>`).join('');
    tip.innerHTML = `<div class="tip-time">${fmtT(t)}</div>` + at.sort((a, b) => b.v - a.v).map(o =>
      `<div class="tip-row"><span class="swatch" style="background:${seriesColor(o.s.i)}"></span>${esc(o.s.label)}<strong>${fmtV(o.v)}</strong></div>`).join('');
    tip.classList.remove('hidden');
    const left = x(t) / W * r.width;
    tip.style.left = `${left > r.width / 2 ? left - tip.offsetWidth - 14 : left + 14}px`;
  };
  svg.querySelector('.hit').onmouseleave = () => { cross.classList.add('hidden'); dots.innerHTML = ''; tip.classList.add('hidden'); };
}

// ---------------------------------------------------------------- Disks
let diskPoll = null;

async function refreshDisks() {
  let d;
  try { d = await api('/api/disks'); } catch (e) { log('讀取硬碟資訊失敗：' + e.message); return; }
  const usage = d.usage?.disks || {}, scan = d.scan;
  const badge = qs('#disk-scan-badge'), btn = qs('#disk-scan');
  if (scan.running) {
    badge.textContent = `掃描中 ${scan.current || ''} · ${scan.files.toLocaleString()} 個檔案`;
    badge.className = 'badge warn';
  } else if (scan.error) {
    badge.textContent = '掃描失敗'; badge.className = 'badge bad'; badge.title = scan.error;
  } else if (d.usage?.scanned_at) {
    badge.textContent = `上次掃描 ${new Date(d.usage.scanned_at * 1000).toLocaleString('zh-TW', {hour12: false})}（耗時 ${fmtDuration(d.usage.duration)}）`;
    badge.className = 'badge ok';
  } else {
    badge.textContent = '尚未掃描使用者用量'; badge.className = 'badge';
  }
  btn.disabled = scan.running;

  qs('#disk-list').innerHTML = d.disks.map(k => {
    const level = k.percent >= 90 ? 'bad' : k.percent >= 80 ? 'warn' : '';
    const users = (usage[k.mount]?.users || []).filter(u => u.bytes > 0);
    const top = users.slice(0, 10), rest = users.slice(10);
    const restBytes = rest.reduce((a, u) => a + u.bytes, 0);
    // Bars compare users with each other (longest = largest user); the text gives the share of the disk.
    const scale = Math.max(restBytes, ...top.map(u => u.bytes), 1);
    const userRows = top.map(u => userBar(u.user, u.bytes, k.total, scale)).join('') +
      (rest.length ? userBar(`其他 ${rest.length} 位`, restBytes, k.total, scale, true) : '');
    return `<article class="card disk-card">
      <div class="card-head"><div><h3>${esc(k.mount)}</h3><p class="muted small">${esc(k.device)} · ${esc(k.fstype)}</p></div>
        <div class="disk-free"><small>剩餘</small><strong>${fmtBytes(k.free)}</strong></div></div>
      <div class="disk-meter ${level}"><div class="meter-track"><div class="meter-fill" style="width:${k.percent}%"></div></div>
        <div class="disk-meter-text"><span>已用 ${fmtBytes(k.used)} / ${fmtBytes(k.total)}</span><strong>${k.percent.toFixed(0)}%${level === 'bad' ? ' · 空間不足' : level === 'warn' ? ' · 偏高' : ''}</strong></div></div>
      <div class="disk-users"><h4>各使用者用量</h4>${userRows || `<p class="muted small">${scan.running ? '掃描中…' : '尚無資料，請按「重新掃描使用者用量」。'}</p>`}</div>
    </article>`;
  }).join('') || '<article class="card"><p class="muted">找不到可顯示的硬碟。</p></article>';

  clearTimeout(diskPoll);
  if (scan.running && pageActive('disks')) diskPoll = setTimeout(refreshDisks, 3000);
}
function userBar(name, bytes, total, scale, muted = false) {
  const pct = total ? bytes / total * 100 : 0;
  return `<div class="user-bar ${muted ? 'muted-row' : ''}"><span class="user-name" title="${esc(name)}">${esc(name)}</span>
    <div class="bar-track"><div class="bar-fill" style="width:${Math.max(bytes / scale * 100, 1)}%"></div></div>
    <span class="user-size">${fmtBytes(bytes)}<small>占硬碟 ${pct < 0.1 ? '<0.1' : pct.toFixed(1)}%</small></span></div>`;
}
qs('#disk-scan').onclick = async () => {
  try { const r = await api('/api/disks/scan', {method: 'POST'}); log(r.message); refreshDisks(); }
  catch (e) { log('啟動掃描失敗：' + e.message); }
};

// ---------------------------------------------------------------- Dashboard cards
function dashBadge(id, text, cls = '') {
  const b = qs(`#${id}-badge`);
  if (!b) return;
  b.textContent = text || '';
  b.className = `badge ${cls}` + (text ? '' : ' hidden');
}
let dashPkgAt = 0;

async function refreshDashboard() {
  const jobs = [
    api('/api/gpu/processes').then(d => {
      const busy = new Set(d.processes.map(p => p.gpu));
      const users = [...new Set(d.processes.map(p => p.user || '未知'))];
      qs('#dash-gpu').textContent = d.gpus.length
        ? `${busy.size} / ${d.gpus.length} 張使用中` + (users.length ? ` · ${users.join('、')}` : ' · 全部閒置')
        : '未偵測到 GPU';
      dashBadge('dash-gpu', d.gpus.length && busy.size === d.gpus.length ? '全部使用中' : '', 'warn');
    }),
    api('/api/disks').then(d => {
      const worst = [...d.disks].sort((a, b) => b.percent - a.percent)[0];
      qs('#dash-disk').textContent = worst
        ? `${d.disks.length} 顆硬碟 · 最滿：${worst.mount} 剩 ${fmtBytes(worst.free)}（已用 ${worst.percent.toFixed(0)}%）`
        : '找不到硬碟';
      const full = d.disks.filter(k => k.percent >= 90).length, high = d.disks.filter(k => k.percent >= 80).length;
      dashBadge('dash-disk', full ? `${full} 顆空間不足` : high ? `${high} 顆偏高` : '', full ? 'bad' : 'warn');
    }),
    api('/api/portal/update-status').then(d => {
      qs('#dash-update').classList.toggle('hidden', !d.update_available);
      if (d.update_available) {
        qs('#dash-update-title').textContent = `Server Admin Portal 有新版本（${d.behind} 個更新）`;
        qs('#dash-update-sub').textContent = d.latest ? `最新：${d.latest.hash} · ${d.latest.date} · ${d.latest.subject}` : '';
      }
    }),
    refreshFan(),
  ];
  // apt is slow; check packages at most every 10 minutes.
  if (Date.now() - dashPkgAt > 600000) {
    dashPkgAt = Date.now();
    jobs.push(api('/api/packages/status').then(d => {
      const n = d.upgradable.length;
      qs('#dash-pkg').textContent = d.running ? '套件更新進行中…' : n ? `${n} 個套件可更新` : '已是最新';
      dashBadge('dash-pkg', d.reboot_required ? '需重新開機' : '', 'warn');
    }));
  }
  await Promise.allSettled(jobs);
}
setInterval(() => { if (pageActive('dashboard')) refreshDashboard(); }, 30000);
refreshDashboard();

// ---------------------------------------------------------------- Fan curve editor
const CURVE_PRESETS = {
  quiet: [[45, 50], [65, 55], [75, 70], [82, 85], [88, 95]],
  standard: [[40, 50], [60, 60], [70, 70], [78, 85], [85, 95]],
  cool: [[35, 60], [55, 70], [65, 80], [72, 90], [78, 95]],
};
const CURVE = {points: CURVE_PRESETS.standard.map(p => [...p]), dirty: false, state: null, enabled: false};

function syncCurve(curve) {
  if (!curve) return;
  CURVE.state = curve.state;
  CURVE.enabled = curve.enabled;
  // Never overwrite points the user is still editing.
  if (!CURVE.dirty) CURVE.points = curve.points.map(p => [...p]);
  renderCurve();
}

function renderCurve() {
  const box = qs('#curve-points');
  if (!box) return;
  if (!box.contains(document.activeElement)) {
    box.innerHTML = CURVE.points.map((p, i) => `<div class="curve-row"><label>溫度<input type="number" min="20" max="100" value="${p[0]}" data-i="${i}" data-k="0"><span>°C</span></label>
      <span class="muted">→</span><label>風扇<input type="number" min="50" max="95" value="${p[1]}" data-i="${i}" data-k="1"><span>%</span></label></div>`).join('');
    qsa('#curve-points input').forEach(inp => inp.oninput = () => {
      const v = Number(inp.value);
      if (Number.isFinite(v)) { CURVE.points[+inp.dataset.i][+inp.dataset.k] = v; CURVE.dirty = true; drawCurve(); }
    });
  }
  drawCurve();
  const st = CURVE.state, el = qs('#curve-status');
  if (!CURVE.enabled) el.textContent = '溫度曲線目前未啟用。';
  else if (st?.error) el.innerHTML = `<span class="warn-text">⚠ ${esc(st.error)}</span>`;
  else if (st?.max_temp != null) el.textContent = `運作中：最熱 GPU ${st.hot_gpu} ${st.max_temp.toFixed(0)}°C → 目標 ${st.target}%，目前 ${st.applied ?? '—'}%（${Math.max(0, Math.round(Date.now() / 1000 - st.updated_at))} 秒前更新）`;
  else el.textContent = '運作中，等待第一次檢查…';
}

function drawCurve() {
  const box = qs('#curve-chart');
  if (!box) return;
  const W = Math.max(260, box.clientWidth || 360), H = 200, M = {l: 38, r: 12, t: 10, b: 26};
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const x = t => M.l + (Math.min(100, Math.max(20, t)) - 20) / 80 * iw;
  const y = s => M.t + ih - (Math.min(100, Math.max(40, s)) - 40) / 60 * ih;
  const pts = [...CURVE.points].sort((a, b) => a[0] - b[0]);
  const line = [[20, pts[0][1]], ...pts, [100, pts[pts.length - 1][1]]];
  const hot = CURVE.state?.max_temp;
  const speedAt = t => {
    if (t <= pts[0][0]) return pts[0][1];
    for (let i = 1; i < pts.length; i++) if (t <= pts[i][0]) return Math.round(pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (t - pts[i - 1][0]) / (pts[i][0] - pts[i - 1][0]));
    return pts[pts.length - 1][1];
  };
  box.innerHTML = `<svg class="chart" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="風扇溫度曲線">
    ${[40, 60, 80, 100].map(s => `<line class="grid" x1="${M.l}" x2="${W - M.r}" y1="${y(s)}" y2="${y(s)}"/><text class="tick" x="${M.l - 6}" y="${y(s) + 4}" text-anchor="end">${s}%</text>`).join('')}
    ${[20, 40, 60, 80, 100].map(t => `<text class="tick" x="${x(t)}" y="${H - 6}" text-anchor="middle">${t}°C</text>`).join('')}
    <line class="axis" x1="${M.l}" x2="${W - M.r}" y1="${M.t + ih}" y2="${M.t + ih}"/>
    <path d="${line.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('')}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round"/>
    ${pts.map(p => `<circle cx="${x(p[0])}" cy="${y(p[1])}" r="4" fill="var(--s1)" class="dot"/>`).join('')}
    ${hot != null && CURVE.enabled ? `<line class="crosshair" x1="${x(hot)}" x2="${x(hot)}" y1="${M.t}" y2="${M.t + ih}"/>
      <text class="tick" x="${x(hot) + (x(hot) > W - 110 ? -6 : 6)}" y="${M.t + 12}" text-anchor="${x(hot) > W - 110 ? 'end' : 'start'}">目前 ${hot.toFixed(0)}°C → ${speedAt(hot)}%</text>` : ''}
  </svg>`;
}

async function applyCurve() {
  const points = [...CURVE.points].sort((a, b) => a[0] - b[0]);
  try {
    const r = await api('/api/fan/curve', {method: 'POST', body: JSON.stringify({enabled: true, points})});
    log(r.message);
    CURVE.dirty = false;
    await refreshFan();
    return true;
  } catch (e) { log('套用溫度曲線失敗：' + e.message); alert(e.message); return false; }
}

qsa('.curve-presets [data-preset]').forEach(b => b.onclick = () => {
  CURVE.points = CURVE_PRESETS[b.dataset.preset].map(p => [...p]);
  CURVE.dirty = true;
  qs('#curve-points').innerHTML = '';
  renderCurve();
});
window.addEventListener('resize', () => { if (pageActive('fan')) drawCurve(); });
renderCurve();
