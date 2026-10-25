// 公众号: the WeRead bookshelf sync, manual add, on/off, details and delete.
buildNav('accounts');
const PER = 10;
const ui = { q: '', filter: 'all', page: 0, accounts: [], fresh: new Set(), syncTimer: null, loggedIn: false };

function stateText(a) {
  if (!a.on) return ['已暂停采集，新任务不会读取这个公众号', ''];
  if (!a.last) return ['还没有采集过，下一个任务会读取', ''];
  if (!a.last.ok) return ['上次采集失败 · ' + a.last.when, 'bad'];
  return [`采集正常 · 上次读到 ${a.last.got} 篇，最近一篇 ${a.last.latest}`, ''];
}

async function load() {
  const r = await call('acc_list');
  if (!r) return;
  ui.accounts = r.accounts; ui.loggedIn = r.logged_in;
  renderList();
}

function renderList() {
  const all = ui.accounts.filter(a => (!ui.q || a.name.includes(ui.q)) && (ui.filter === 'all' || (ui.filter === 'on') === a.on));
  const pages = Math.max(1, Math.ceil(all.length / PER));
  ui.page = Math.min(ui.page, pages - 1);
  const rows = all.slice(ui.page * PER, ui.page * PER + PER);
  $('pager').hidden = pages <= 1;
  $('pageinfo').textContent = `共 ${all.length} 个 · 第 ${ui.page + 1}/${pages} 页`;
  $('prev').disabled = ui.page === 0; $('next').disabled = ui.page >= pages - 1;
  $('count').textContent = `${ui.accounts.length} 个公众号 · ${ui.accounts.filter(a => a.on).length} 个已启用`;
  $('list').innerHTML = rows.length ? rows.map(a => {
    const [text, cls] = stateText(a);
    return `<li class="${a.on ? '' : 'off'} ${ui.fresh.has(a.mp) ? 'new' : ''}">
      <div><div class="name">${esc(a.name)}</div><div class="state ${cls}">${esc(text)}</div></div>
      <button class="switch" role="switch" aria-checked="${a.on}" aria-label="${a.on ? '停用' : '启用'} ${esc(a.name)}" data-toggle="${a.id}">
        <span class="track"></span><span class="lbl">${a.on ? '已启用' : '已停用'}</span></button>
      <button class="btn more" data-detail="${a.id}">详情</button></li>`;
  }).join('') : `<li class="empty">${ui.q || ui.filter !== 'all' ? '没有符合条件的公众号。' : '名单还是空的。点“扫码同步书架”，从微信读书书架导入公众号。'}</li>`;
  ui.fresh.clear();
}

$('q').addEventListener('input', e => { ui.q = e.target.value.trim(); ui.page = 0; renderList(); });
$('prev').addEventListener('click', () => { ui.page--; renderList(); });
$('next').addEventListener('click', () => { ui.page++; renderList(); });
$('filter').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  ui.filter = b.dataset.v; ui.page = 0;
  for (const x of $('filter').children) x.setAttribute('aria-pressed', String(x === b));
  renderList();
});
$('list').addEventListener('click', async e => {
  const t = e.target.closest('[data-toggle]');
  if (t) {
    const a = ui.accounts.find(x => x.id === Number(t.dataset.toggle));
    if (await call('acc_toggle', a.id)) { a.on = !a.on; toast(`${a.name} 已${a.on ? '启用' : '停用'}，从下一个新任务起生效`); renderList(); }
    return;
  }
  const d = e.target.closest('[data-detail]'); if (d) openDetail(Number(d.dataset.detail));
});

/* Detail drawer */
function openDetail(id) {
  const a = ui.accounts.find(x => x.id === id), l = a.last;
  $('dw-h').textContent = a.name;
  const recent = l && l.ok && l.recent && l.recent.length ? `<h4>文章库里的最近文章</h4><ul class="recent">${l.recent.map(([t, w]) => `<li><span>${esc(t)}</span><span>${w}</span></li>`).join('')}</ul>` : '';
  $('dw-body').innerHTML = `
    <div class="note" style="margin-top: 4px">${a.on ? '已启用：新任务会读取这个公众号' : '已停用：新任务不会读取这个公众号'}</div>
    <h4>最近一次采集</h4>
    ${l ? `<dl><dt>时间</dt><dd>${l.when}（中国时间）</dd>
      <dt>结果</dt><dd>${l.ok ? `读到 ${l.got} 篇，最近一篇发布于 ${l.latest}` : `<span style="color: var(--cinnabar)">${esc(l.reason || '读取失败')}</span>`}</dd>
      ${l.coverage ? `<dt>读取范围</dt><dd>${esc(l.coverage)}</dd>` : ''}</dl>
      <div class="note" style="margin-top: 8px">这是软件那次实际读到的结果，不代表这个公众号在全网的最新发文。</div>` : '<div class="note">还没有采集记录。</div>'}
    ${recent}
    <h4>账号信息</h4><dl><dt>账号编号</dt><dd>${esc(a.mp)}</dd></dl>
    <h4>移出名单</h4><div id="del-zone"><button class="btn danger" id="del">删除这个公众号</button></div>`;
  $('drawer').hidden = false; $('dw-close').focus();
  $('del').addEventListener('click', () => {
    $('del-zone').innerHTML = `<div class="confirm">将“${esc(a.name)}”移出后续采集名单。已采集的文章和已生成的稿件都会保留。
      <div><button class="btn danger" id="del-yes">确认删除</button><button class="btn quiet" id="del-no">取消</button></div></div>`;
    $('del-yes').focus();
    $('del-yes').addEventListener('click', async () => {
      if (await call('acc_delete', a.id)) { $('drawer').hidden = true; toast(`已删除“${a.name}”，文章和稿件已保留`); load(); }
    });
    $('del-no').addEventListener('click', () => openDetail(id));
  });
}
$('dw-close').addEventListener('click', () => { $('drawer').hidden = true; });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('drawer').hidden = true; });

/* Bookshelf sync: login → read → choose. The Python side runs it; this page polls its state. */
function stepper(index) { [...$('stepper').children].forEach((li, i) => { li.className = i < index ? 'done' : i === index ? 'now' : ''; }); }
async function pollSync() {
  clearTimeout(ui.syncTimer);
  const s = await call('sync_state');
  if (!s || s.phase === 'closed') return;
  const body = $('sync-body');
  if (s.phase === 'login') {
    stepper(0);
    body.innerHTML = `<div class="qr"><div class="code">扫码窗口<br>已在 Edge 中打开</div>
      <div><ol><li>已打开一个 Edge 登录窗口，里面有微信读书二维码</li><li>用<b>手机微信</b>扫码，并在手机上确认登录</li><li>登录成功后软件自动读取书架，登录状态加密保存在本机，下次不用再扫</li></ol>
      <div class="note shine" style="margin-top: 10px">正在等待扫码…（二维码过期时关闭此面板再点“扫码同步书架”）</div></div></div>`;
  } else if (s.phase === 'reading') {
    stepper(1);
    body.innerHTML = '<div class="note shine" style="font-size: 14px">正在读取微信读书书架…</div>';
  } else if (s.phase === 'error') {
    stepper(1);
    body.innerHTML = `<div class="err">${esc(s.message)}。现有公众号名单没有任何改动。</div>
      <div class="bar-row"><button class="btn primary" id="retry">重试读取</button>${s.relogin ? '<button class="btn" id="relogin">重新扫码登录</button>' : '<button class="btn" id="relogin">重新扫码登录</button>'}</div>`;
    $('retry').addEventListener('click', () => startSync(false));
    $('relogin').addEventListener('click', () => startSync(true));
    return;
  } else if (s.phase === 'choose') {
    stepper(2);
    const fresh = s.shelf.filter(x => !x.have);
    body.innerHTML = s.shelf.length ? `<div class="note" style="margin-bottom: 8px">书架上有 ${s.shelf.length} 个公众号，其中 <b>${fresh.length}</b> 个还不在名单里。</div>
      <ul class="pick">${s.shelf.map(x => x.have
        ? `<li><label class="have"><input type="checkbox" checked disabled><span>${esc(x.name)}</span><span class="tag have">已在名单，保留原启停状态</span></label></li>`
        : `<li><label><input type="checkbox" checked data-mp="${esc(x.mp)}"><span>${esc(x.name)}</span><span class="tag">新增</span></label></li>`).join('')}</ul>
      <div class="bar-row"><button class="btn" id="all">全选新增</button><span class="grow"></span>
        <button class="btn quiet" id="cancel">取消</button><button class="btn primary" id="import" ${fresh.length ? '' : 'disabled'}>导入所选</button></div>`
      : '<div class="note">书架里没有公众号。请先在手机微信读书把公众号加入书架，再回来同步；现有名单不变。</div>';
    if (!s.shelf.length) return;
    $('all').addEventListener('click', () => body.querySelectorAll('input[data-mp]').forEach(x => { x.checked = true; }));
    $('cancel').addEventListener('click', closeSync);
    $('import').addEventListener('click', async () => {
      const chosen = [...body.querySelectorAll('input[data-mp]:checked')].map(x => x.dataset.mp);
      const r = await call('sync_import', chosen);
      if (!r) return;
      chosen.forEach(mp => ui.fresh.add(mp));
      $('sync').hidden = true; toast(`已导入 ${r.count} 个公众号，从下一个新任务起采集`);
      ui.filter = 'all'; ui.page = 99; load();
    });
    return;
  }
  ui.syncTimer = setTimeout(pollSync, 1000);
}
async function startSync(force) {
  $('add').hidden = true; $('sync').hidden = false;
  stepper(force || !ui.loggedIn ? 0 : 1);
  $('sync-body').innerHTML = '<div class="note shine">正在准备…</div>';
  const r = await call('sync_start', force);
  if (!r) { $('sync').hidden = true; return; }
  pollSync();
}
async function closeSync() { clearTimeout(ui.syncTimer); await call('sync_close'); $('sync').hidden = true; }
$('sync-open').addEventListener('click', () => startSync(false));
$('sync-close').addEventListener('click', closeSync);

/* Manual add: the account is identified from the article link (__biz), never guessed from the name. */
$('add-open').addEventListener('click', () => { $('sync').hidden = true; $('add').hidden = false; $('add-name').focus(); });
$('add-close').addEventListener('click', () => { $('add').hidden = true; });
let parseTimer = null;
$('add-link').addEventListener('input', e => {
  clearTimeout(parseTimer);
  const h = $('add-hint'), value = e.target.value.trim();
  if (!value) { h.className = 'hint'; h.textContent = '软件从链接里的账号编号（__biz）识别是哪个公众号，不按名称猜测；名称需和公众号实际名称一致。'; return; }
  parseTimer = setTimeout(async () => {
    try { const r = await api('acc_parse', value); h.className = 'hint ok'; h.textContent = '已识别账号编号：' + r.mp; }
    catch (err) { h.className = 'hint bad'; h.textContent = err.message; }
  }, 250);
});
$('add-form').addEventListener('submit', async e => {
  e.preventDefault();
  const h = $('add-hint');
  try {
    await api('acc_add', $('add-name').value, $('add-link').value);
    const name = $('add-name').value.trim();
    $('add-form').reset(); $('add').hidden = true; toast(`已添加“${name}”，从下一个新任务起采集`);
    ui.filter = 'all'; ui.page = 99; load();
  } catch (err) { h.className = 'hint bad'; h.textContent = err.message; }
});

ready(load);
