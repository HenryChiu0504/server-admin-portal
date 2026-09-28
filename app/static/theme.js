// Appearance switch: 自動 (follow the OS) → 淺色 → 深色. The choice is kept per
// browser; an inline script in <head> applies it before first paint.
(() => {
  const KEY = 'portal-theme', root = document.documentElement, btn = document.getElementById('theme-toggle');
  const icon = d => `<svg class="i" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    auto: icon('<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>'),
    light: icon('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
    dark: icon('<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>'),
  };
  const LABEL = { auto: '自動', light: '淺色', dark: '深色' };
  const NEXT = { auto: 'light', light: 'dark', dark: 'auto' };
  let mode = 'auto';
  try { mode = localStorage.getItem(KEY) || 'auto'; } catch {}
  if (!LABEL[mode]) mode = 'auto';

  function apply() {
    if (mode === 'auto') delete root.dataset.theme; else root.dataset.theme = mode;
    try { mode === 'auto' ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, mode); } catch {}
    if (btn) {
      btn.innerHTML = `${ICON[mode]}<span class="label">${LABEL[mode]}</span>`;
      btn.title = `外觀：${LABEL[mode]}（點擊切換）`;
    }
  }
  if (btn) btn.onclick = () => { mode = NEXT[mode]; apply(); };
  apply();
})();
