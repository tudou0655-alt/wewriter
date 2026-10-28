// 设置: models and keys, collection services, writing, export, health check, maintenance.
buildNav('settings');
const MODELS = { 'deepseek-v4-pro': 'DeepSeek · 长文处理', 'deepseek-flash': 'DeepSeek · 低成本',
                 'gpt-5.6-terra': 'OpenAI · 质量优先', 'gpt-6-luna': 'OpenAI · 低成本',
                 'qwen3.8-flash': '百炼 · 低成本' };
const provider = id => /^deepseek/i.test(id || '') ? 'deepseek' : /^qwen/i.test(id || '') ? 'bailian' : 'openai';
const PNAME = { deepseek: 'DeepSeek', openai: 'OpenAI', bailian: '阿里云百炼' };
const KEYMAP = { 'writer-key': null, 'oa-key': 'openai', 'ds-key': 'deepseek', 'bl-key': 'bailian', 'djl-key': 'dajiala', 'ei-key': 'everyinfra' };
const STYLE_TEXT = { '政策申报实务': '面向企业老板和申报负责人：像懂行的朋友当面讲事，短句、说人话，结论先行；用具体数字和例子讲清政策变化和该做什么，不写公文腔和论文腔，不夸张营销。', '参考原文风格': '参考原文的语气、结构和表达习惯，但不照搬原句。', '通俗自然': '面向普通读者，通俗自然，清晰有逻辑，避免夸张营销和空泛套话。', '专业简洁': '专业简洁，结论先行，用词准确，避免口语化和空泛套话。' };
let conf = null, saved = null;
window.isDirty = () => changes().length > 0;

function options(sel, ids, value, custom) {
  const list = ids.includes(value) || !value ? ids : [value, ...ids];
  sel.innerHTML = list.map(id => `<option value="${esc(id)}">${esc(id)}（${MODELS[id] || '已保存的模型'}）</option>`).join('') +
    (custom ? '<option value="custom">自定义模型 ID…</option>' : '');
  sel.value = value || ids[0];
}
function snapshot() {
  const v = {}; document.querySelectorAll('[data-k]').forEach(el => { v[el.dataset.k] = el.value; });
  document.querySelectorAll('[data-sw]').forEach(s => { v['sw-' + s.dataset.sw] = s.getAttribute('aria-checked'); });
  return v;
}
const LABELS = { writer: '写作模型', custom: '自定义模型', summary: '摘要模型', search: '联网搜索模型', searchprovider: '联网搜索服务', words: '目标篇幅', style: '写作风格',
  stylecustom: '自定义风格', extra: '补充要求', fmt: '导出格式', dir: '保存位置', oaurl: '接口地址', dsurl: '接口地址', blurl: '接口地址', schedtime: '定时时间',
  limit: '读取上限', minreads: '最低阅读量', paidcap: '付费请求次数上限', 'sw-sched': '每日自动运行', 'sw-draftfirst': '优先生成草稿', 'sw-review': 'AI 二审', 'sw-verify': '自动查证', 'sw-vision': '识图', 'sw-recollect': '重新采集', 'sw-startup': '开机启动' };
function changes() {
  if (!saved) return [];
  const now = snapshot();
  const list = Object.keys(now).filter(k => now[k] !== saved[k]).map(k => LABELS[k] || k);
  if (Object.keys(KEYMAP).some(id => $(id).value.trim())) list.push('API Key');
  return [...new Set(list)];
}
function refreshBar() {
  const list = changes();
  $('savebar').hidden = !list.length;
  $('save-text').innerHTML = list.length ? `有未保存的修改：<b>${esc(list.join('、'))}</b>。保存后对新任务生效，进行中的任务继续用原设置。` : '';
}
document.addEventListener('input', refreshBar);
document.addEventListener('change', refreshBar);
window.addEventListener('beforeunload', e => { if (changes().length) { e.preventDefault(); e.returnValue = ''; } });

function keyHint(id, name) {
  const has = conf.keys[name];
  $(id).placeholder = has ? '已配置，留空保留原 Key' : '未配置';
  return has;
}
function writerModel() { return $('writer').value === 'custom' ? $('custom').value.trim() : $('writer').value; }
function writerUi() {
  const custom = $('writer').value === 'custom';
  $('custom-box').hidden = !custom;
  const p = provider(writerModel());
  $('writer-hint').textContent = custom ? '按模型 ID 判断服务商：deepseek 为 DeepSeek，qwen 为百炼，其余为 OpenAI' : `服务商：${PNAME[p]}`;
  $('writer-key-l').textContent = `${PNAME[p]} API Key`;
  const has = keyHint('writer-key', p);
  $('writer-key-st').textContent = has ? '已配置' : '未配置：写作需要';
  $('writer-key-st').className = 'hint ' + (has ? 'ok' : 'bad');
}
function wordsUi() {
  const raw = $('words').value.trim(), n = Number(raw), ok = /^\d+$/.test(raw) && n >= 200 && n <= 20000;
  $('words').classList.toggle('bad', !ok);
  $('words-hint').className = 'hint' + (ok ? '' : ' bad');
  $('words-hint').textContent = ok ? '可填 200—20000 的任意整数；这是写作目标，实际字数会有浮动' : '请填写 200—20000 之间的整数';
  document.querySelectorAll('[data-w]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.w === raw)));
  return ok;
}
function styleUi() {
  const v = $('style').value; $('style-custom').hidden = v !== 'custom';
  $('style-hint').textContent = v === 'custom' ? '' : (STYLE_TEXT[v] || '');
}
function setSwitch(name, on) { document.querySelector(`[data-sw="${name}"]`).setAttribute('aria-checked', String(!!on)); }
const sw = name => document.querySelector(`[data-sw="${name}"]`).getAttribute('aria-checked') === 'true';

async function load() {
  const r = await call('set_get');
  if (!r) return;
  conf = r;
  const all = [...r.models.deepseek, ...r.models.openai, ...r.models.bailian];
  options($('writer'), all, r.writer_model, true);
  options($('summary'), all, r.summary_model, false);
  $('search-provider').value = r.search_provider || 'deepseek';
  options($('search'), r.models[$('search-provider').value], r.search_model, false);
  $('custom').value = '';
  $('oa-url').value = r.openai_url; $('ds-url').value = r.deepseek_url; $('bl-url').value = r.bailian_url;
  $('words').value = r.words;
  $('word-chips').innerHTML = [800, 1200, 1500, 2000, 3000].map(n => `<button type="button" data-w="${n}" aria-pressed="false">${n}</button>`).join('');
  $('style').value = r.style_preset && STYLE_TEXT[r.style_preset] ? r.style_preset : 'custom';
  $('style-custom').value = r.style_preset && STYLE_TEXT[r.style_preset] ? '' : (r.style_custom || r.style_preset || '');
  $('extra').value = r.style_extra || '';
  $('fmt').value = r.export_format;
  $('dir').value = r.export_dir || '每次导出时选择';
  $('dir-each').hidden = !r.export_dir;
  $('sched-time').value = r.schedule_time;
  $('limit').value = r.limit; $('minreads').value = r.min_reads;
  $('paidcap').value = r.max_paid_requests == null ? '' : r.max_paid_requests;
  const util = r.utility_costs || { requests: 0, known_cny: '0', unknown_requests: 0 };
  $('utility-cost').textContent = `设置页连接测试和风格卡生成：${util.requests} 次付费请求，已知费用估算 ¥${util.known_cny}` +
    (util.unknown_requests ? `；${util.unknown_requests} 次费用未知` : '') + '。与写稿任务分别记录。';
  setSwitch('sched', r.schedule_enabled); setSwitch('draftfirst', r.draft_first !== false); setSwitch('review', r.review); setSwitch('verify', r.verify !== false); setSwitch('vision', r.vision);
  setSwitch('recollect', r.recollect); setSwitch('startup', r.startup);
  for (const [id, name] of Object.entries(KEYMAP)) if (name) keyHint(id, name);
  $('oa-st').textContent = r.keys.openai ? '已配置' : '未配置';
  $('oa-st').className = 'hint ' + (r.keys.openai ? 'ok' : 'bad');
  for (const [id, name, configured] of [
    ['ds-st', 'deepseek', '已配置'],
    ['bl-st', 'bailian', '已配置'],
    ['djl-st', 'dajiala', '已配置 · 按调用计费'],
    ['ei-st', 'everyinfra', '已配置 · 极致了失败时备用']
  ]) {
    $(id).textContent = r.keys[name] ? configured : '未配置';
    $(id).className = 'hint' + (r.keys[name] ? ' ok' : '');
  }
  document.querySelectorAll('[data-k]').forEach(el => { if (el.id === 'djl-key' || el.id === 'ei-key') delete el.dataset.k; });
  for (const id of Object.keys(KEYMAP)) $(id).value = '';
  writerUi(); wordsUi(); styleUi(); renderKeys();
  saved = snapshot(); refreshBar();
}

$('writer').addEventListener('change', writerUi);
$('custom').addEventListener('input', writerUi);
$('search-provider').addEventListener('change', () => {
  options($('search'), conf.models[$('search-provider').value], '', false); refreshBar();
});
$('reco-apply').addEventListener('click', () => {
  $('writer').value = 'deepseek-flash'; $('summary').value = 'deepseek-flash';
  $('search-provider').value = 'deepseek'; options($('search'), conf.models.deepseek, 'deepseek-flash', false);
  writerUi(); refreshBar(); toast('已填入推荐模型，点“保存设置”后生效');
});
document.querySelectorAll('[data-reveal]').forEach(b => b.addEventListener('click', () => {
  const input = $(b.dataset.reveal), show = input.type === 'password';
  input.type = show ? 'text' : 'password'; b.textContent = show ? '隐藏' : '显示';
}));
$('words').addEventListener('input', wordsUi);
$('word-chips').addEventListener('click', e => { const b = e.target.closest('[data-w]'); if (b) { $('words').value = b.dataset.w; wordsUi(); refreshBar(); } });
$('style').addEventListener('change', styleUi);
$('dir-pick').addEventListener('click', async () => {
  const r = await call('set_pick_folder');
  if (r && !r.cancelled) { $('dir').value = r.path; $('dir-each').hidden = false; refreshBar(); }
});
$('dir-each').addEventListener('click', () => { $('dir').value = '每次导出时选择'; $('dir-each').hidden = true; refreshBar(); });
document.querySelectorAll('[data-sw]').forEach(s => s.addEventListener('click', () => { s.setAttribute('aria-checked', String(s.getAttribute('aria-checked') !== 'true')); refreshBar(); }));

/* Health check: the free part always, the billed part only after asking. */
function setStatus(id, cls, text, run) {
  $(id).className = 'st' + (run ? ' run' : ''); $(id).innerHTML = `<i class="dot ${cls}"></i><span class="${run ? 'shine' : ''}">${esc(text)}</span>`;
}
async function runCheck(billed) {
  $('check-box').innerHTML = '';
  setStatus('st-collect', '', '检查中…', true);
  if (billed) setStatus('st-model', '', '检查中…', true);
  $('check').disabled = true;
  const r = await call('set_check', billed);
  $('check').disabled = false;
  if (!r) { setStatus('st-collect', '', '未检查'); if (billed) setStatus('st-model', '', '未检查'); return; }
  setStatus('st-collect', r.collect.ok ? 'ok' : 'bad', `${r.collect.text} · ${r.time} 检查`);
  if (r.model) setStatus('st-model', r.model.ok ? 'ok' : 'bad', `${r.model.text} · ${r.time} 检查`);
  else setStatus('st-model', '', '未检查（会产生少量费用）');
}
$('check').addEventListener('click', () => {
  if (changes().length) { $('check-box').innerHTML = '<div class="inline warn">请先保存设置，再检查连接，这样检查的是你刚改好的设置。</div>'; return; }
  $('check-box').innerHTML = `<div class="inline warn"><b>同时检查文章生成吗？</b><span>采集检查不计费；文章生成检查会向 ${PNAME[provider(writerModel())]} 发送一次很短的测试请求，产生少量费用。</span>
    <div class="row"><button class="btn primary" id="ck-yes">两项都检查</button><button class="btn" id="ck-no">只检查采集</button></div></div>`;
  $('ck-yes').addEventListener('click', () => runCheck(true));
  $('ck-no').addEventListener('click', () => runCheck(false));
});

/* Maintenance */
const ACTS = [['set_backup', r => `已备份到：${r.path}（不含 Key）`], ['set_restore', r => '已恢复数据库，定时任务已暂停；恢复前的数据库已自动保存'],
              ['set_open_dir', () => '已打开数据目录']];
document.querySelectorAll('[data-act]').forEach((b, i) => b.addEventListener('click', async () => {
  if (i === 1 && !(await confirmBox('恢复数据库', '恢复会替换当前的文章、稿件和设置，并暂停定时任务。当前数据会先自动备份。', '选择备份并恢复', '取消'))) return;
  const r = await call(ACTS[i][0]);
  if (r && !r.cancelled) { toast(ACTS[i][1](r)); if (i === 1) load(); }
}));
const KEY_NAMES = [['openai', 'OpenAI'], ['deepseek', 'DeepSeek'], ['bailian', '阿里云百炼'], ['dajiala', '极致了'], ['everyinfra', 'EveryInfra']];
function renderKeys() {
  $('keys').innerHTML = KEY_NAMES.map(([k, n]) => `<div class="row"><span>${n}</span>${conf.keys[k]
    ? `<span style="display: flex; gap: 8px; align-items: center"><span class="hint ok">已保存（加密在本机）</span><button class="btn danger" data-clear="${k}">清除</button></span>`
    : '<span class="hint">未保存</span>'}</div>`).join('');
}
$('keys').addEventListener('click', async e => {
  const b = e.target.closest('[data-clear]'); if (!b) return;
  const name = KEY_NAMES.find(k => k[0] === b.dataset.clear)[1];
  if (!(await confirmBox('清除 Key', `清除已保存的 ${name} Key？相关功能将无法使用，直到重新填写。`, '确认清除', '取消'))) return;
  if (await call('set_clear_key', b.dataset.clear)) { toast(`${name} Key 已清除`); load(); }
});

/* Save / undo */
$('save').addEventListener('click', async () => {
  if (!wordsUi()) { $('words').focus(); toast('目标篇幅需要是 200—20000 之间的整数'); return; }
  if ($('writer').value === 'custom' && !$('custom').value.trim()) { $('custom').focus(); toast('请填写自定义模型 ID'); return; }
  const keys = {};
  const wp = provider(writerModel());
  if ($('writer-key').value.trim()) keys[wp] = $('writer-key').value.trim();
  for (const [id, name] of Object.entries(KEYMAP)) if (name && $(id).value.trim()) keys[name] = $(id).value.trim();
  const style = $('style').value;
  const payload = {
    writer_model: writerModel(), summary_model: $('summary').value,
    search_provider: $('search-provider').value, search_model: $('search').value,
    openai_url: $('oa-url').value, deepseek_url: $('ds-url').value, bailian_url: $('bl-url').value, words: $('words').value,
    style_preset: style === 'custom' ? '' : style, style_custom: style === 'custom' ? $('style-custom').value : '',
    style_extra: $('extra').value, export_format: $('fmt').value,
    export_dir: $('dir').value === '每次导出时选择' ? '' : $('dir').value,
    schedule_enabled: sw('sched'), schedule_time: $('sched-time').value, draft_first: sw('draftfirst'), review: sw('review'), verify: sw('verify'), vision: sw('vision'),
    recollect: sw('recollect'), startup: sw('startup'), limit: $('limit').value,
    min_reads: $('minreads').value, max_paid_requests: $('paidcap').value, keys };
  $('save').disabled = true;
  const r = await call('set_save', payload);
  $('save').disabled = false;
  if (!r) return;
  for (const id of Object.keys(KEYMAP)) { $(id).value = ''; $(id).type = 'password'; }
  document.querySelectorAll('[data-reveal]').forEach(b => { b.textContent = '显示'; });
  await load(); toast('设置已保存，新任务会使用新设置');
});
$('undo').addEventListener('click', async () => { await load(); toast('已撤销未保存的修改'); });
ready(load);

$('clear-data').addEventListener('click', async () => {
  if (changes().length) { toast('请先保存或撤销设置修改，再清理数据'); return; }
  const button = $('clear-data');
  button.disabled = true;
  try {
    if (!(await confirmBox('清除后台数据', '将清空文章、稿件、任务历史和分析/图片缓存。保留公众号、API Key、登录信息及其他配置，暂停每日自动运行。已导出的文件不受影响。执行前自动备份数据库（不含图片文件），清除后可重新采集原文章。', '备份并清除', '取消'))) return;
    button.textContent = '正在备份并清理…';
    const r = await call('set_clear_data', true);
    if (!r) return;
    await load();
    $('clear-result').textContent = `已清除 ${r.removed.articles} 条文章、${r.removed.drafts} 条稿件、${r.removed.jobs} 条任务记录。自动运行已暂停。备份：${r.path}`;
    toast('后台数据已清空，公众号和配置已保留');
  } finally {
    button.disabled = false;
    button.textContent = '一键清除后台数据';
  }
});

/* ---- 范文库与风格卡（T-029） ----
   Built here rather than in settings.html: the writer learns the customer's style from these
   examples through one style card; each article carries the card and at most one example outline. */
const CARD_LABELS = [['reader', '读者是谁'], ['task', '帮读者办成什么事'], ['title', '标题手法'], ['opening', '开头手法'],
  ['hierarchy', '层级手法'], ['voice', '口吻'], ['ending', '收尾手法']];
function styleSection() {
  let box = $('style-lib');
  if (box) return box;
  box = document.createElement('section');
  box.className = 'panel'; box.id = 'style-lib'; box.setAttribute('aria-labelledby', 'sl-h');
  const anchor = document.querySelector('section[aria-labelledby="w-h"]');
  if (anchor) anchor.after(box); else (document.querySelector('main') || document.body).appendChild(box);
  return box;
}
let styleTimer = null;
async function loadStyleLib() {
  const r = await call('ex_list');
  if (!r) return;
  const box = styleSection();
  const items = r.examples.map(e => `<li style="display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; padding: 8px 0; border-top: 1px solid var(--rule)">
      <div><b>${e.kind === 'bad' ? '反例' : '范文'}</b> · ${esc(e.title || '（无标题）')}${e.score != null ? ` · ${e.score} 分` : ''}
        <div class="hint">${esc(e.origin || '')} · ${e.chars} 字 · ${esc(e.when)}${e.avoid ? ` · 不要学：${esc(e.avoid)}` : ''}</div></div>
      <button class="btn quiet" data-ex-del="${e.id}">删除</button></li>`).join('');
  const card = r.card;
  box.innerHTML = `<h2 id="sl-h">写作风格<small>范文库与风格卡：模型从范文学写法，事实只用素材</small></h2>
    <div class="hint">每篇稿件只带一张风格卡（约 800 字以内）和最多一篇范文骨架，范文库再大也不会增加每篇的输入量。范文只学写法，不学其中的事实和数字。</div>
    <ul style="list-style: none; padding: 0; margin: 10px 0">${items || '<li class="hint">范文库还是空的。可以粘贴或导入客户认可的文章，也可以在稿件库里把稿件“设为范文/反例”。</li>'}</ul>
    <details><summary>添加范文</summary><div class="inner" style="display: grid; gap: 8px">
      <input class="input" id="ex-title" placeholder="标题">
      <textarea class="input" id="ex-body" rows="6" placeholder="正文（可直接粘贴，小标题用 ## 开头）"></textarea>
      <div style="display: flex; gap: 8px; flex-wrap: wrap">
        <select class="select" id="ex-kind" style="width: auto"><option value="good">范文（要学）</option><option value="bad">反例（不要学）</option></select>
        <input class="input" id="ex-score" placeholder="客户打分 0–100（可空）" style="width: 180px">
      </div>
      <input class="input" id="ex-avoid" placeholder="不要学的地方，例如：原文没有的夸大后果">
      <div style="display: flex; gap: 8px; flex-wrap: wrap"><button class="btn primary" id="ex-add">加入范文库</button>
        <button class="btn" id="ex-import">从 Word/Markdown 导入…</button></div>
    </div></details>
    <details><summary>风格卡${r.card_is_default ? '（当前为内置默认卡）' : ''}</summary><div class="inner" style="display: grid; gap: 8px">
      ${CARD_LABELS.map(([k, label]) => `<label class="hint">${label}<textarea class="input" rows="2" data-card="${k}">${esc(card[k] || '')}</textarea></label>`).join('')}
      <label class="hint">禁忌（每行一条）<textarea class="input" rows="3" id="card-taboos">${esc((card.taboos || []).join('\n'))}</textarea></label>
      <label class="hint">对照示例（每行一组：事实｜可以写｜不能写）<textarea class="input" rows="3" id="card-examples">${esc((card.examples || []).map(e => [e.fact, e.good, e.bad].join('｜')).join('\n'))}</textarea></label>
      <div style="display: flex; gap: 8px; flex-wrap: wrap">
        <button class="btn primary" id="card-save">保存风格卡</button>
        <button class="btn" id="card-default">恢复默认卡</button>
        <button class="btn" id="card-gen" ${r.generating ? 'disabled' : ''}>${r.generating ? '正在生成…' : '根据范文库重新生成'}</button>
      </div>
      <div class="hint">重新生成会调用一次写作模型（付费），只在范文库变化后需要；生成后可以在这里修改。</div>
    </div></details>`;
  clearTimeout(styleTimer);
  if (r.generating) styleTimer = setTimeout(loadStyleLib, 3000);
}
document.addEventListener('click', async e => {
  const del = e.target.closest('[data-ex-del]');
  if (del && await confirmBox('删除范文', '删除后不会影响已生成的稿件；风格卡要重新生成才会反映这次变化。', '删除', '取消')) {
    if (await call('ex_delete', Number(del.dataset.exDel))) loadStyleLib();
  }
  if (e.target.id === 'ex-add') {
    const r = await call('ex_add', $('ex-title').value, $('ex-body').value, $('ex-kind').value, $('ex-score').value, $('ex-avoid').value);
    if (r) { toast('已加入范文库，记得重新生成风格卡'); loadStyleLib(); }
  }
  if (e.target.id === 'ex-import') {
    const r = await call('ex_import', $('ex-kind') ? $('ex-kind').value : 'good');
    if (r && !r.cancelled) { toast(`已导入《${r.title}》，记得重新生成风格卡`); loadStyleLib(); }
  }
  if (e.target.id === 'card-save') {
    const card = {};
    document.querySelectorAll('[data-card]').forEach(t => { card[t.dataset.card] = t.value.trim(); });
    card.taboos = $('card-taboos').value.split('\n').map(s => s.trim()).filter(Boolean);
    card.examples = $('card-examples').value.split('\n').map(s => s.split('｜').map(x => x.trim()))
      .filter(p => p.length >= 3 && p.every(Boolean)).map(([fact, good, bad]) => ({ fact, good, bad }));
    if (await call('style_save', card)) { toast('风格卡已保存，新任务会使用'); loadStyleLib(); }
  }
  if (e.target.id === 'card-default' && await confirmBox('恢复默认风格卡', '会丢弃当前风格卡，改用内置默认卡。', '恢复', '取消')) {
    if (await call('style_save', null)) { toast('已恢复默认风格卡'); loadStyleLib(); }
  }
  if (e.target.id === 'card-gen' && await confirmBox('重新生成风格卡', '会把范文库交给写作模型归纳一张新风格卡，调用一次（付费），约 1 分钟。', '生成', '取消')) {
    e.target.disabled = true; e.target.textContent = '正在生成…';
    if (await call('style_generate')) { toast('正在生成风格卡'); setTimeout(loadStyleLib, 1500); }
    else loadStyleLib();
  }
});
ready(loadStyleLib);
