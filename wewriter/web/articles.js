// 文章库: search, filters, sort by real numbers (unknown after every value), reading panel.
buildNav('articles');
const ui = { q: '', acct: 'all', range: 'all', sort: 'reads', page: 0, open: null, accountsFilled: false };
const STATES = { used: ['已用于生成稿件', 'st-used'], ok: ['未使用', ''], nobody: ['正文未获取', 'st-wait'], bad: ['正文获取失败', 'st-bad'] };
STATES.summary = ['摘要失败 · 正文已保存', 'st-bad'];
STATES.processing = ['处理异常 · 正文已保存', 'st-wait'];

async function load() {
  const r = await call('art_list', ui.q, ui.acct, ui.range, ui.sort, ui.page);
  if (!r) return;
  if (!ui.accountsFilled) {
    ui.accountsFilled = true;
    $('acct').innerHTML = '<option value="all">全部公众号</option>' + r.accounts.map(a => `<option>${esc(a)}</option>`).join('');
  }
  const pages = Math.max(1, Math.ceil(r.total / r.per));
  if (ui.page > pages - 1) { ui.page = pages - 1; return load(); }
  $('total').textContent = `共 ${r.total} 篇` + (r.total !== r.all ? `（文章库共 ${r.all} 篇）` : '');
  $('list').innerHTML = r.rows.length ? r.rows.map(a => {
    const [label, cls] = STATES[a.state];
    return `<li><div>
        <div class="t">${esc(a.t)}</div>
        <div class="m"><span>${esc(a.acct)}</span><span>${a.when}</span><span class="${cls}">${label}</span></div>
        <div class="stats"><span class="${a.reads === null ? 'unknown' : ''}">阅读<b>${fmtNum(a.reads)}</b></span><span class="${a.likes === null ? 'unknown' : ''}">点赞<b>${fmtNum(a.likes)}</b></span></div>
      </div><button class="btn" data-open="${a.id}">${a.state === 'nobody' || a.state === 'bad' ? '查看详情' : '查看文章'}</button></li>`;
  }).join('') : `<li class="empty">${r.all ? '没有符合条件的文章。可以换个关键词，或把时间范围放宽。' : '文章库还是空的。在工作台开始一个任务后，采集到的文章会出现在这里。'}</li>`;
  $('pager').hidden = pages <= 1;
  $('pageinfo').textContent = `第 ${ui.page + 1}/${pages} 页 · 每页 ${r.per} 篇`;
  $('prev').disabled = ui.page === 0; $('next').disabled = ui.page >= pages - 1;
}

let searchTimer = null;
$('q').addEventListener('input', e => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { ui.q = e.target.value.trim(); ui.page = 0; load(); }, 250); });
for (const [id, key] of [['acct', 'acct'], ['range', 'range'], ['sort', 'sort']])
  $(id).addEventListener('change', e => { ui[key] = e.target.value; ui.page = 0; load(); });
$('prev').addEventListener('click', () => { ui.page--; load(); window.scrollTo({ top: 0 }); });
$('next').addEventListener('click', () => { ui.page++; load(); window.scrollTo({ top: 0 }); });

function bodyHtml(text, images) {
  const paras = String(text || '').split(/\n+/).map(s => s.trim()).filter(Boolean).map(p => `<p>${esc(p)}</p>`);
  const pics = images.map(im => `<p><img src="${im.src}" alt="${esc(im.alt)}" style="max-width: 100%; border-radius: 8px"></p>`);
  return paras.join('') + pics.join('');
}
async function open(id) {
  const a = await call('art_detail', id);
  if (!a) return;
  ui.open = id;
  $('dw-acct').textContent = a.acct;
  $('dw-h').textContent = a.t;
  $('dw-by').textContent = `发布于 ${a.when}（中国时间）`;
  $('dw-figs').innerHTML = `<div class="${a.reads === null ? 'unknown' : ''}"><b>${fmtNum(a.reads)}</b><span>阅读</span></div>
    <div class="${a.likes === null ? 'unknown' : ''}"><b>${fmtNum(a.likes)}</b><span>点赞</span></div>`;
  $('dw-state').innerHTML = ['summary', 'processing'].includes(a.state) ? `<div class="alert">正文已保存。${esc(a.error)}。可在任务详情继续原任务，复用已保存正文和完成的摘要。</div>`
    : a.state === 'bad' ? `<div class="alert">正文获取失败：${esc(a.error)}</div>`
    : a.state === 'nobody' ? '<div class="wait">还没有读取正文。任务选中这篇文章时才会读取，以免多花采集费用。</div>'
    : a.state === 'used' ? '<div class="wait" style="background: var(--jade-soft); color: var(--jade)">这篇已经用来生成过稿件，自动任务不会再选它；如果它是爆款，可以点“再写一篇”换个角度再生成。</div>' : '';
  $('dw-rewrite').hidden = a.state === 'bad';
  ui.drafts = a.drafts || []; $('dw-rw').hidden = true;
  $('dw-body').innerHTML = a.body ? bodyHtml(a.body, a.images) : '<p class="note">没有可显示的正文。</p>';
  $('dw-meta').innerHTML = a.meta.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  $('drawer').hidden = false; $('drawer').scrollTop = 0; $('dw-close').focus();
}
$('list').addEventListener('click', e => { const b = e.target.closest('[data-open]'); if (b) open(Number(b.dataset.open)); });
$('dw-close').addEventListener('click', () => { $('drawer').hidden = true; });
/* 再写一篇: ask for an optional direction first, and show what was already written from this article. */
$('dw-rewrite').addEventListener('click', () => {
  if (!ui.open) return;
  const box = $('dw-rw'), done = ui.drafts || [];
  if (!box.hidden) { box.hidden = true; return; }
  box.innerHTML = `<b>再写一篇：换个角度写</b>
    ${done.length ? `<div class="rw-done">这篇已写过 ${done.length} 篇，新稿会避开它们的标题和结构：<ul>${done.map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>` : ''}
    <label for="rw-note">这次想写的方向（可选）</label>
    <textarea id="rw-note" maxlength="300" placeholder="例如：从企业准备申报材料的角度写；或：重点讲各地执行差异"></textarea>
    <div class="rw-row"><button class="btn primary" id="rw-go">开始生成</button><button class="btn quiet" id="rw-cancel">取消</button></div>`;
  box.hidden = false; $('rw-note').focus();
  $('rw-cancel').addEventListener('click', () => { box.hidden = true; });
  $('rw-go').addEventListener('click', async () => {
    $('rw-go').disabled = true;
    const r = await call('art_rewrite', ui.open, $('rw-note').value.trim());
    if (!r) { $('rw-go').disabled = false; return; }
    box.hidden = true;
    toast(r.queued ? '已加入队列（前面还有任务），完成后自动开始；到工作台查看' : '已开始再写一篇，到工作台查看进度');
  });
});
$('dw-open').addEventListener('click', async e => { e.preventDefault(); if (ui.open && await call('art_open', ui.open)) toast('已在浏览器中打开原文'); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('drawer').hidden = true; });
ready(load);
