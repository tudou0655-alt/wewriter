// 稿件库: list, manuscript view, editing with a leave guard, review decisions, copy and export.
buildNav('drafts');
const TABS = ['全部', '待审核', '需修改', '已通过'];
const ui = { q: '', tab: '全部', page: 0, open: null, editing: false, dirty: false, panel: null, fmt: 'docx' };
const pillCls = s => ({ '待审核': 'wait', '需修改': 'fix', '已通过': 'ok' }[s] || 'wait');
const textCls = s => ({ '待审核': 's-wait', '需修改': 's-fix', '已通过': 's-ok' }[s] || '');
window.isDirty = () => ui.dirty;

/* ---- List ---- */
async function loadList() {
  const r = await call('dr_list', ui.q, ui.tab, ui.page);
  if (!r) return;
  const pages = Math.max(1, Math.ceil(r.total / r.per));
  if (ui.page > pages - 1) { ui.page = pages - 1; return loadList(); }
  $('tabs').innerHTML = TABS.map(t => `<button aria-pressed="${t === ui.tab}" data-tab="${t}">${t} ${r.counts[t] ?? 0}</button>`).join('');
  $('total').textContent = `共 ${r.total} 篇`;
  $('list').innerHTML = r.rows.length ? r.rows.map(d => `<li><div><div class="t">${esc(d.t)}</div>
      <div class="m"><span class="${textCls(d.s)}">${d.s}</span><span>${d.when}（中国时间）</span>${d.flag ? `<span class="flag">${esc(d.flag)}</span>` : ''}</div></div>
      <div class="acts"><button class="btn quiet" data-copy="${d.id}" data-state="${d.s}">复制全文</button><button class="btn" data-open="${d.id}">查看稿件</button></div></li>`).join('')
    : `<li class="empty">${ui.q ? '没有找到包含这个关键词的稿件。' : ui.tab === '全部' ? '这里还没有稿件。在工作台开始一个任务后，生成的稿件会出现在这里。' : `没有“${ui.tab}”的稿件。`}</li>`;
  $('pager').hidden = pages <= 1;
  $('pageinfo').textContent = `第 ${ui.page + 1}/${pages} 页 · 每页 ${r.per} 篇`;
  $('prev').disabled = ui.page === 0; $('next').disabled = ui.page >= pages - 1;
}
let searchTimer = null;
$('q').addEventListener('input', e => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { ui.q = e.target.value.trim(); ui.page = 0; loadList(); }, 250); });
$('tabs').addEventListener('click', e => { const b = e.target.closest('[data-tab]'); if (b) { ui.tab = b.dataset.tab; ui.page = 0; loadList(); } });
$('prev').addEventListener('click', () => { ui.page--; loadList(); });
$('next').addEventListener('click', () => { ui.page++; loadList(); });
$('list').addEventListener('click', e => {
  const b = e.target.closest('[data-open]'); if (b) openDraft(Number(b.dataset.open));
  const c = e.target.closest('[data-copy]'); if (c) copyDraft(Number(c.dataset.copy), c.dataset.state);
});

/* ---- Copy ---- */
// Same clean text as “导出发布稿” (no [S1] marks), straight to the clipboard; keeps headings when pasted into the WeChat editor.
async function copyDraft(id, state) {
  const r = await call('dr_copy', id);
  if (!r) return;
  let ok = r.copied;
  if (!ok) try { await navigator.clipboard.writeText(r.text); ok = true; } catch (_) {}
  toast(!ok ? '复制失败：剪贴板正被其他程序占用，请稍后再试，或使用“导出”'
    : `已复制全文，约 ${r.chars} 字，可直接粘贴到公众号编辑器${state === '已通过' ? '' : '（这篇还没审核通过）'}`);
}

/* ---- Manuscript ---- */
// Small Markdown subset the writer uses: headings, quotes, **bold**, lists and pipe tables.
function inline(text) {
  return esc(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\[(S|W)(\d+)\]/g, '<span class="cite" title="来源 $1$2，导出发布稿时会去掉">$1$2</span>');
}
function prose(md) {
  return String(md || '').split(/\n{2,}/).map(raw => {
    const block = raw.trim(), lines = block.split('\n').map(l => l.trim());
    if (/^#{1,6}\s/.test(block)) return `<h3>${inline(block.replace(/^#{1,6}\s*/, ''))}</h3>`;
    if (block.startsWith('>')) return `<blockquote>${lines.map(l => inline(l.replace(/^>\s?/, ''))).join('<br>')}</blockquote>`;
    if (lines.length > 1 && lines.every(l => l.startsWith('|'))) {
      const cells = l => l.replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      const rows = lines.filter(l => !/^\|[\s:|-]+\|$/.test(l)).map(cells);
      return `<table><thead><tr>${rows[0].map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${
        rows.slice(1).map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
    }
    if (lines.every(l => /^\d+[.、]\s/.test(l))) return `<ol>${lines.map(l => `<li>${inline(l.replace(/^\d+[.、]\s*/, ''))}</li>`).join('')}</ol>`;
    if (lines.every(l => /^[-*]\s/.test(l))) return `<ul>${lines.map(l => `<li>${inline(l.replace(/^[-*]\s*/, ''))}</li>`).join('')}</ul>`;
    return `<p>${lines.map(inline).join('<br>')}</p>`;
  }).join('');
}
async function openDraft(id) {
  const d = await call('dr_get', id);
  if (!d) return;
  ui.open = d; ui.editing = false; ui.dirty = false; ui.panel = null;
  $('v-list').hidden = true; $('v-draft').hidden = false; renderDraft(); window.scrollTo({ top: 0 });
}
async function reopen() { const d = await call('dr_get', ui.open.id); if (d) { ui.open = d; renderDraft(); } }
async function leave(next) {
  if (ui.dirty && !(await confirmBox('有未保存的修改', '离开后，这次编辑的内容会丢失。', '放弃修改并离开', '继续编辑'))) return;
  ui.dirty = false; ui.editing = false; next();
}
$('back').addEventListener('click', () => leave(() => { ui.open = null; $('v-draft').hidden = true; $('v-list').hidden = false; loadList(); }));
window.addEventListener('beforeunload', e => { if (ui.dirty) { e.preventDefault(); e.returnValue = ''; } });

function renderDraft() {
  const d = ui.open, st = d.s;
  $('pill').className = 'pill ' + pillCls(st);
  $('pill').textContent = st === '已通过' ? '已通过 · 人工已确认（不代表已发布）' : st;
  if (ui.editing) {
    $('sheet').innerHTML = `<input class="edit-title" id="e-title" value="${esc(d.t)}" aria-label="标题">
      <textarea class="edit-body" id="e-body" aria-label="正文">${esc(d.body)}</textarea>
      <div class="note" style="margin-top: 8px">正文里的 [S1]、[W1] 是来源编号，用来对应素材报告；导出发布稿时会自动去掉。保存修改后稿件回到“待审核”。</div>
      <div class="actions"><button class="btn primary" id="save">保存修改</button><button class="btn" id="cancel">取消编辑</button></div>`;
    const mark = () => { ui.dirty = $('e-title').value !== d.t || $('e-body').value !== d.body; };
    $('e-title').addEventListener('input', mark); $('e-body').addEventListener('input', mark);
    $('save').addEventListener('click', async () => {
      const t = $('e-title').value.trim(), b = $('e-body').value.trim();
      if (!t || !b) { toast('标题和正文都不能为空'); return; }
      const changed = t !== d.t.trim() || b !== d.body.trim();
      if (changed && !(await call('dr_save', d.id, t, b))) return;
      ui.editing = false; ui.dirty = false; await reopen();
      toast(changed ? '已保存为新版本，稿件回到“待审核”' : '内容没有变化');
    });
    $('cancel').addEventListener('click', () => leave(() => renderDraft()));
    $('e-title').focus();
    return;
  }
  const log = d.log.length ? d.log.map(([w, s, n]) => `<li><span>${esc(w)}</span><span class="who">${esc(s)}</span><span>${n ? esc(n) : '—'}</span></li>`).join('')
    : '<li><span class="note">还没有人工审核记录</span></li>';
  $('sheet').innerHTML = `<h2>${esc(d.t)}</h2>
    <div class="byline"><span>${d.when}（中国时间）更新</span><span>第 ${d.rev} 版</span><span>约 ${d.words} 字</span></div>
    ${d.flag ? `<div class="warnline">${esc(d.flag)}：发布前请对照下方“来源与审核提示”核实。</div>` : ''}
    ${st === '已通过' ? '<div class="okline">你已审核通过这篇稿件。审核通过不会自动发布；修改正文后会回到“待审核”。</div>' : ''}
    <div class="prose">${prose(d.body)}</div>
    <div class="actions">
      <button class="btn" id="edit">编辑</button><span class="grow"></span>
      <button class="btn warn" id="fix" ${st === '需修改' ? 'disabled' : ''}>标记需修改</button>
      <button class="btn primary" id="pass" ${st === '已通过' ? 'disabled' : ''}>审核通过</button>
      <button class="btn" id="copy">复制全文</button>
      <button class="btn" id="exp">导出</button>
    </div>
    <div id="inline"></div>
    <details class="more"><summary>来源与审核提示</summary>
      <h5>来源对应关系</h5>${d.sources.length ? `<ul class="src">${d.sources.map(([k, t, m]) => `<li><b>${esc(k)}</b><span>${esc(t)}<small>${esc(m)}</small></span></li>`).join('')}</ul>` : '<div class="note">没有记录来源。</div>'}
      <h5>素材报告与机器审核提示（保留原记录，不会因人工审核被清除）</h5>
      <div class="note" style="white-space: pre-wrap; max-height: 320px; overflow-y: auto; margin-top: 8px; background: var(--ground); padding: 10px 12px; border-radius: 8px">${esc(d.machine || '没有机器审核提示')}</div>
      <h5>人工审核记录</h5><ul class="log">${log}</ul>
      <h5>历史版本</h5><ul class="log">${d.versions.map(([r, w], i) => `<li><span>第 ${r} 版</span><span class="who">${i === 0 ? '当前' : ''}</span><span>${esc(w)}</span></li>`).join('')}</ul>
    </details>`;
  $('edit').addEventListener('click', () => { ui.editing = true; renderDraft(); });
  $('fix').addEventListener('click', () => { ui.panel = 'fix'; renderInline(); });
  $('pass').addEventListener('click', () => { ui.panel = 'pass'; renderInline(); });
  $('copy').addEventListener('click', () => copyDraft(d.id, st));
  $('exp').addEventListener('click', () => { ui.panel = ui.panel === 'exp' ? null : 'exp'; renderInline(); });
  renderInline();
}

function renderInline() {
  const d = ui.open, box = $('inline');
  if (!box) return;
  if (ui.panel === 'pass') {
    box.innerHTML = `<div class="inline ok"><b>确认审核通过？</b><span>请确认已核对正文、事实和来源。审核通过只是确认稿件可用，不会自动发布。</span>
      <div class="row"><button class="btn primary" id="pass-yes">确认通过</button><button class="btn quiet" id="pass-no">继续检查</button></div></div>`;
    $('pass-yes').focus();
    $('pass-yes').addEventListener('click', async () => { if (await call('dr_status', d.id, '已通过', '')) { ui.panel = null; await reopen(); toast('已审核通过，工作台的合格篇数会加 1'); } });
    $('pass-no').addEventListener('click', () => { ui.panel = null; renderInline(); });
  } else if (ui.panel === 'fix') {
    box.innerHTML = `<div class="inline fix"><b>标记为需修改</b><span>写一句要改什么（可选），会记在审核记录里，方便之后修改。</span>
      <textarea id="fix-note" placeholder="例如：第二段的数据需要换成官方口径"></textarea>
      <div class="row"><button class="btn warn" id="fix-yes">标记需修改</button><button class="btn quiet" id="fix-no">取消</button></div></div>`;
    $('fix-note').focus();
    $('fix-yes').addEventListener('click', async () => { if (await call('dr_status', d.id, '需修改', $('fix-note').value.trim())) { ui.panel = null; await reopen(); toast('已标记为需修改'); } });
    $('fix-no').addEventListener('click', () => { ui.panel = null; renderInline(); });
  } else if (ui.panel === 'exp') {
    const unpassed = d.s !== '已通过';
    box.innerHTML = `<div class="inline" style="background: var(--ground)">
      ${unpassed ? '<div style="color: var(--cinnabar)"><b>这篇稿件还没有审核通过。</b>仍可以导出，但建议先审核。</div>' : ''}
      <div class="export"><div class="formats" role="radiogroup" aria-label="导出格式">
        ${[['docx', 'Word（.docx）'], ['md', 'Markdown（.md）'], ['txt', '纯文本（.txt）']].map(([v, l]) =>
          `<label><input type="radio" name="fmt" value="${v}" ${ui.fmt === v ? 'checked' : ''}>${l}</label>`).join('')}</div>
        <div class="row"><button class="btn primary" id="ex-copy">导出发布稿</button><button class="btn" id="ex-report">导出素材报告</button></div>
        <span class="note">发布稿不含 [S1] 这类来源编号和审核备注；来源对应关系和审核记录在素材报告里。保存位置按“设置”里的默认位置，未设置时每次选择。</span></div></div>`;
    box.querySelectorAll('input[name=fmt]').forEach(r => r.addEventListener('change', e => { ui.fmt = e.target.value; }));
    const run = async kind => {
      const r = await call('dr_export', d.id, kind, ui.fmt);
      if (r) toast(r.cancelled ? '已取消导出，没有生成文件' : `已导出${kind === 'report' ? '素材报告' : '发布稿'}：${r.path}`);
    };
    $('ex-copy').addEventListener('click', () => run('copy'));
    $('ex-report').addEventListener('click', () => run('report'));
  } else box.innerHTML = '';
}

ready(async () => {
  let wanted = null;
  try { wanted = sessionStorage.getItem('openDraft'); sessionStorage.removeItem('openDraft'); } catch (_) {}
  await loadList();
  if (wanted) openDraft(Number(wanted));
});
