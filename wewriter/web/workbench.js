// 工作台: new task, the current task's pipeline, this run's drafts, history and details.
buildNav('workbench');
const STEPS = ['采集', '预检', '写作', '二审', '待审核'];
const STEP_TEXT = ['正在读取公众号文章列表…', '正在提取正文、检查图片型文章…', '正在联网查资料并逐篇写作…', '正在对照原文做二审…', ''];
const ui = { mode: 'today', count: 2, page: 0, timer: null, last: null };

function cnStamp() { const d = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000); return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; }
setInterval(() => { $('clock').textContent = cnStamp(); }, 15000);
$('clock').textContent = cnStamp();

/* Folding picker: only the chosen option shows; click to open the others. */
function picker(id, options, value, onPick) {
  const group = $(id);
  group.innerHTML = options.map(([v, label]) => `<button aria-pressed="${String(v) === String(value)}" data-v="${v}">${label}</button>`).join('');
  group.classList.add('closed');
  group.onclick = e => {
    const b = e.target.closest('button'); if (!b) return;
    if (group.classList.contains('closed')) { group.classList.remove('closed'); b.focus(); return; }
    for (const x of group.children) x.setAttribute('aria-pressed', String(x === b));
    group.classList.add('closed'); b.focus(); onPick(b.dataset.v);
  };
  group.onkeydown = e => { if (e.key === 'Escape') group.classList.add('closed'); };
}
document.addEventListener('click', e => {
  for (const id of ['range', 'count']) if (!$(id).contains(e.target)) $(id).classList.add('closed');
});

function renderPipe(job) {
  const person = !job.running && job.step >= 4 && (job.waiting || job.fix);
  $('pipe').innerHTML = STEPS.map((name, i) => {
    let cls = i < job.step ? 'done' : (i === job.step && job.running ? 'now' : '');
    if (i === 4 && job.step >= 4 && !job.running) cls = person ? 'person' : 'done';
    const mark = cls === 'done' ? '✓' : String(i + 1);
    return `<li class="${cls}" title="查看详情"><span class="bar"></span><span class="name ${cls === 'now' ? 'shine' : ''}"><i>${mark}</i>${name}${cls === 'now' ? '中' : ''}</span></li>`;
  }).join('');
}

function render(s) {
  ui.last = s;
  $('scope').innerHTML = s.accounts ? `<b>${s.accounts}</b> 个公众号已启用 · 按阅读量选文` : '还没有启用的公众号，请先到“公众号”页添加';
  $('start').textContent = s.busy ? '加入队列' : '开始任务';
  $('queue').hidden = !s.queue;
  $('queue').textContent = s.restored && !s.busy ? `排队 ${s.queue} 个（含上次退出前未开始的任务）` : `排队 ${s.queue} 个，前一个完成后自动开始`;
  $('cancelq').hidden = !s.queue;
  const job = s.job;
  $('curpanel').classList.toggle('running', !!(job && job.running));
  if (!job) {
    $('state').hidden = true;
    $('summary').textContent = '还没有任务';
    $('meta').textContent = '选好文章范围和篇数后点“开始任务”。';
    $('pipe').innerHTML = ''; $('live').textContent = '';
    $('detail').hidden = true; $('stop').hidden = true;
    $('drafts').innerHTML = '<li class="empty">稿件会在这里逐篇出现，单篇写完即可查看。</li>';
    $('passed').textContent = '';
    return;
  }
  $('detail').hidden = false;
  $('state').hidden = false;
  $('state').className = 'chip ' + job.chip;
  $('state').textContent = job.chip_text;
  const parts = [];
  if (job.waiting) parts.push(`<em>${job.waiting} 篇待审核</em>`);
  if (job.fix) parts.push(`${job.fix} 篇需修改`);
  if (job.processing) parts.push(`${job.processing} 篇处理中`);
  if (job.passed) parts.push(`${job.passed} 篇已通过`);
  $('summary').innerHTML = parts.join('，') || (job.running ? '正在准备…' : job.stopped ? '任务已停止，没有生成稿件' : '这次没有生成稿件');
  $('meta').textContent = `${job.range} · 目标 ${job.target} 篇 · 已用时 ${elapsedText(job.elapsed)}`;
  renderPipe(job);
  $('live').className = 'live' + (job.running ? ' shine' : '');
  $('live').textContent = job.running ? (STEP_TEXT[job.step] || job.stage) : job.message;
  $('stop').hidden = !job.running;
  if (!job.running) $('confirm').hidden = true;
  $('passed').textContent = job.drafts.length ? `人工通过 ${job.passed} / ${job.target}` : '';
  const slots = job.drafts.slice();
  for (let i = 0; i < job.processing; i++) slots.push({ title: '写作中…', state: '处理中', pending: true });
  $('drafts').innerHTML = slots.length ? slots.map((d, i) => `
    <li class="${d.pending ? 'pending' : ''}"><span class="n">${pad(i + 1)}</span>
      <div><div class="t">${esc(d.title)}</div><div class="s"><span class="${d.state === '待审核' ? 'wait' : d.state === '已通过' ? 'ok' : ''}">${d.state}</span>${d.hint ? `<span class="flag">${esc(d.hint)}</span>` : ''}</div></div>
      <button class="btn" ${d.pending ? 'disabled' : ''} data-open="${d.id || ''}">查看稿件</button></li>`).join('')
    : '<li class="empty">这次还没有完成的稿件。</li>';
}

async function refresh() {
  const s = await call('wb_state');
  if (!s) return;
  if (!refresh.inited) {
    refresh.inited = true;
    ui.mode = s.mode === 'today' ? 'today' : String(s.hours);
    ui.count = s.count;
    const ranges = [['today', '今天'], ['24', '近 24 小时'], ['72', '近 3 天']];
    if (!ranges.some(r => r[0] === ui.mode)) ranges.push([ui.mode, `近 ${ui.mode} 小时`]);
    picker('range', ranges, ui.mode, v => { ui.mode = v; });
    picker('count', [1, 2, 3, 4, 5].map(n => [n, n]), ui.count, v => { ui.count = Number(v); });
  }
  render(s);
  clearTimeout(ui.timer);
  ui.timer = setTimeout(refresh, s.busy ? 1500 : 6000);
  if (!$('hist').hidden && (s.busy !== refresh.wasBusy)) loadHistory();
  refresh.wasBusy = s.busy;
}

$('start').addEventListener('click', async () => {
  $('start').disabled = true;
  const r = await call('wb_start', ui.mode, ui.count);
  $('start').disabled = false;
  if (r) toast(r.queued ? `已加入队列（排队 ${r.count} 个），设置已冻结` : '任务已开始');
  refresh();
});
$('stop').addEventListener('click', () => { $('confirm').hidden = false; $('stop-yes').focus(); });
$('stop-no').addEventListener('click', () => { $('confirm').hidden = true; });
$('stop-yes').addEventListener('click', async () => {
  $('confirm').hidden = true;
  if (await call('wb_stop')) toast('正在停止：当前请求返回后结束，已有稿件保留');
  refresh();
});
$('cancelq').addEventListener('click', async () => {
  if (!(await confirmBox('取消排队', '取消所有尚未开始的任务？正在运行的任务不受影响。', '取消排队', '保留'))) return;
  const r = await call('wb_cancel_queue'); if (r) toast(`已取消 ${r.count} 个排队任务`); refresh();
});
$('queue').addEventListener('click', async () => {
  if (!ui.last || !ui.last.restored || ui.last.busy) return;
  if (!(await confirmBox('开始排队任务', '将依次执行上次退出前排队的任务，会调用采集服务和模型，可能产生费用。', '开始', '暂不'))) return;
  await call('wb_start_restored'); refresh();
});
$('drafts').addEventListener('click', e => {
  const b = e.target.closest('[data-open]');
  if (b && b.dataset.open) { try { sessionStorage.setItem('openDraft', b.dataset.open); } catch (_) {} go('drafts'); }
});

/* History */
async function loadHistory() {
  const r = await call('wb_history', ui.page);
  if (!r) return;
  const pages = Math.max(1, Math.ceil(r.total / r.per));
  $('histbody').innerHTML = r.rows.length ? r.rows.map(h => `
    <tr><td>${h.when}</td><td><span class="chip ${h.chip}">${esc(h.status)}</span></td>
    <td>合格稿件 ${h.ok} / ${h.target}</td><td><button class="btn quiet" data-hist="${h.id}">详情</button></td></tr>`).join('')
    : '<tr><td class="empty">还没有历史任务。</td></tr>';
  $('pageinfo').textContent = `共 ${r.total} 条 · 第 ${ui.page + 1}/${pages} 页`;
  $('prev').disabled = ui.page === 0; $('next').disabled = ui.page >= pages - 1;
}
$('histtoggle').addEventListener('click', e => {
  const open = $('hist').hidden; $('hist').hidden = !open; $('histnote').hidden = open;
  e.target.textContent = open ? '收起' : '展开'; e.target.setAttribute('aria-expanded', String(open));
  if (open) loadHistory();
});
$('prev').addEventListener('click', () => { ui.page--; loadHistory(); });
$('next').addEventListener('click', () => { ui.page++; loadHistory(); });

/* Detail drawer: how the articles were chosen, in order. */
async function openDetail(jobId) {
  const d = await call('wb_detail', jobId);
  if (!d) return;
  $('dw-h').textContent = d.title; $('dw-sub').textContent = d.sub;
  let html = d.alert ? `<h4>需要你注意</h4><div class="alert">${esc(d.alert)}</div>` : '';
  if (d.has_funnel) {
    const picked = d.picked.map(p => `<li><span>${esc(p.title)}</span><span>阅读 ${fmtNum(p.reads)}</span></li>`).join('');
    const failed = d.accounts.filter(a => a.failed).length;
    html += `<h4>这次是怎么选出文章的</h4><ol class="steps">
      <li><span class="no">1</span><div><p>从 ${d.accounts_n} 个公众号读到<b>${d.returned}</b>篇文章</p><small>${failed ? `${failed} 个公众号读取失败，见下表` : '所有公众号都读取成功'}</small></div></li>
      <li><span class="no">2</span><div><p>其中<b>${d.in_window}</b>篇在时间范围内</p><small>其余 ${d.outside} 篇发布时间更早或条目无效，不参与选文</small></div></li>
      <li><span class="no">3</span><div><p>筛选后<b>${d.candidates}</b>篇进入候选处理队列（处理前）</p><small>${d.reasons.length ? '筛选及后续处理记录：' + esc(d.reasons.join('；')) : '暂无排除或处理失败记录'}；入队不代表已完成正文、摘要或写作</small></div></li>
      <li><span class="no">4</span><div><p>按阅读量从高到低选文写作</p>${picked ? `<ul class="picked">${picked}</ul>` : '<small>还没有选出文章</small>'}</div></li>
    </ol>
    <h4>各公众号读取情况</h4><div class="scroll-x"><table class="accts"><thead><tr><th>公众号</th><th>读到</th><th>最近一篇</th></tr></thead><tbody>
    ${d.accounts.map(a => a.failed ? `<tr><td>${esc(a.name)}</td><td class="bad" colspan="2">读取失败：${esc(a.error)}</td></tr>`
      : `<tr><td>${esc(a.name)}</td><td>${a.got} 篇</td><td>${a.latest}</td></tr>`).join('')}</tbody></table></div>`;
  } else if (d.rewrite) {
    html += `<h4>这次写的是哪篇</h4><div class="note">在文章库点“再写一篇”发起，不重新采集；沿用这篇文章并换一个角度写。</div>`
      + (d.picked.length ? `<ul class="picked">${d.picked.map(p => `<li><span>${esc(p.title)}</span><span>阅读 ${fmtNum(p.reads)}</span></li>`).join('')}</ul>` : '');
  } else html += '<div class="note" style="margin-top: 16px">这个任务还没有采集记录。</div>';
  $('dw-body').innerHTML = html;
  const actions = [];
  if (d.retryable) actions.push(['resume', '继续原任务', '沿用当时的设置和选好的文章，只补完没写完的稿件']);
  if (!d.running) actions.push(['recollect', '重新采集', '用现在的设置重新读取文章，新建一个任务']);
  $('dw-actions').innerHTML = actions.map(([k, l, h], i) => `<div><button class="btn ${i === actions.length - 1 ? 'primary' : ''}" data-act="${k}" data-job="${jobId}">${l}</button><small>${h}</small></div>`).join('');
  $('drawer').hidden = false; $('dw-close').focus();
}
$('detail').addEventListener('click', () => ui.last && ui.last.job && openDetail(ui.last.job.id));
$('pipe').addEventListener('click', () => ui.last && ui.last.job && openDetail(ui.last.job.id));
$('histbody').addEventListener('click', e => { const b = e.target.closest('[data-hist]'); if (b) openDetail(Number(b.dataset.hist)); });
$('dw-close').addEventListener('click', () => { $('drawer').hidden = true; });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('drawer').hidden = true; });
$('dw-actions').addEventListener('click', async e => {
  const b = e.target.closest('[data-act]'); if (!b) return;
  if (b.dataset.act === 'recollect' && !(await confirmBox('重新采集', '将用现在的设置重新读取公众号文章并新建任务，会调用采集服务和模型，可能产生费用。', '开始', '取消'))) return;
  const r = b.dataset.act === 'resume' ? await call('wb_resume', Number(b.dataset.job)) : await call('wb_recollect');
  if (r) { toast(b.dataset.act === 'resume' ? '已继续原任务' : r.queued ? '已加入队列' : '已开始重新采集'); $('drawer').hidden = true; }
  refresh();
});

ready(refresh);
