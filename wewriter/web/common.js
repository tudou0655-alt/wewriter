// Shared by the five pages: the Python bridge, navigation, toasts and the leave guard.
const PAGES = [['workbench', '工作台'], ['accounts', '公众号'], ['articles', '文章库'], ['drafts', '稿件库'], ['settings', '设置']];
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const pad = n => String(n).padStart(2, '0');

function ready(fn) {
  if (window.pywebview && window.pywebview.api) fn();
  else window.addEventListener('pywebviewready', fn, { once: true });
}

// Every Python method answers {ok: true, ...} or {ok: false, error}; errors become exceptions here.
async function api(name, ...args) {
  const result = await window.pywebview.api[name](...args);
  if (!result || !result.ok) throw new Error((result && result.error) || '操作没有完成，请重试');
  return result;
}
// Same, but shows the error as a toast and returns null instead of throwing.
async function call(name, ...args) {
  try { return await api(name, ...args); } catch (e) { toast(e.message); return null; }
}

function toast(text) {
  let t = $('toast');
  if (!t) { t = document.createElement('div'); t.id = 'toast'; t.className = 'toast'; document.body.appendChild(t); }
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.h); toast.h = setTimeout(() => { t.hidden = true; }, 3200);
}

// In-page confirmation (pywebview pages have no reliable confirm()).
function confirmBox(title, text, ok = '确定', cancel = '取消') {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'cbox';
    wrap.innerHTML = `<div role="dialog" aria-modal="true"><b>${esc(title)}</b><span>${esc(text)}</span>
      <div class="cbox-row"><button class="btn quiet" data-v="0">${esc(cancel)}</button><button class="btn primary" data-v="1">${esc(ok)}</button></div></div>`;
    document.body.appendChild(wrap);
    wrap.querySelector('[data-v="1"]').focus();
    const done = v => { wrap.remove(); resolve(v); };
    wrap.addEventListener('click', e => { const b = e.target.closest('[data-v]'); if (b) done(b.dataset.v === '1'); });
    wrap.addEventListener('keydown', e => { if (e.key === 'Escape') done(false); });
  });
}

// window.isDirty() is set by pages with unsaved edits.
async function go(page) {
  if (window.isDirty && window.isDirty() &&
      !(await confirmBox('有未保存的修改', '离开后，这次修改的内容会丢失。', '放弃修改并离开', '继续编辑'))) return;
  window.isDirty = null;
  location.href = page + '.html';
}

function buildNav(current) {
  const nav = $('nav');
  nav.innerHTML = PAGES.map(([id, label]) =>
    `<button ${id === current ? 'aria-current="page"' : ''} data-page="${id}">${label}</button>`).join('');
  nav.addEventListener('click', e => { const b = e.target.closest('[data-page]'); if (b && b.dataset.page !== current) go(b.dataset.page); });
  ready(async () => {
    const m = await call('meta');
    if (m && $('foot')) $('foot').textContent = m.version + (m.demo ? ' · 演示数据' : '');
  });
}

function elapsedText(s) {
  return s >= 3600 ? `${Math.floor(s / 3600)} 小时 ${Math.floor(s % 3600 / 60)} 分` : `${Math.floor(s / 60)} 分 ${pad(s % 60)} 秒`;
}
const fmtNum = v => v === null || v === undefined ? '未知' : Number(v).toLocaleString();

// Shared styles that every page needs on top of its own.
document.head.insertAdjacentHTML('beforeend', `<style>
.cbox { position: fixed; inset: 0; background: rgb(0 0 0 / .35); display: grid; place-items: center; z-index: 50; padding: 16px; }
.cbox > div { background: var(--sheet); color: var(--ink); border-radius: 10px; padding: 20px 22px; max-width: 440px; display: grid; gap: 12px; box-shadow: 0 18px 50px rgb(0 0 0 / .25); }
.cbox span { color: var(--ink-2); font-size: 13px; white-space: pre-line; }
.cbox-row { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
.toast { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); background: var(--ink); color: var(--sheet); padding: 8px 16px; border-radius: 8px; font-size: 13px; z-index: 60; max-width: calc(100% - 32px); }
.loading { color: var(--ink-2); font-size: 13px; padding: 18px 0; }
</style>`);

// Side drawers: a collapse tab on the drawer's left edge, always at mid-height, so closing never
// needs scrolling back to the top. It reuses the page's own close button.
(function drawerTab() {
  const drawer = $('drawer');
  if (!drawer) return;
  document.head.insertAdjacentHTML('beforeend', `<style>
.dw-tab { position: fixed; top: 50%; transform: translateY(-50%); width: 26px; height: 64px; display: grid; place-items: center;
  background: var(--sheet); color: var(--ink-2); border: 1px solid var(--rule); border-right: 0; border-radius: 10px 0 0 10px;
  box-shadow: -6px 0 16px rgb(0 0 0 / .10); cursor: pointer; z-index: 21; padding: 0; }
.dw-tab:hover, .dw-tab:focus-visible { color: var(--jade); }
.dw-tab svg { width: 14px; height: 14px; }
</style>`);
  const tab = document.createElement('button');
  tab.className = 'dw-tab'; tab.hidden = true; tab.title = '收起'; tab.setAttribute('aria-label', '收起侧边栏');
  tab.innerHTML = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg>';
  document.body.appendChild(tab);
  const place = () => { tab.hidden = drawer.hidden; if (!drawer.hidden) tab.style.right = drawer.offsetWidth + 'px'; };
  new MutationObserver(place).observe(drawer, { attributes: true, attributeFilter: ['hidden'] });
  window.addEventListener('resize', place);
  tab.addEventListener('click', () => { const c = $('dw-close'); if (c) c.click(); else drawer.hidden = true; });
})();
