// Floating per-task usage: observed requests/tokens and a clearly labeled price estimate.
(() => {
  const root = document.getElementById('cost-widget');
  const coin = document.getElementById('cost-coin');
  const panel = document.getElementById('cost-panel');
  const content = document.getElementById('cost-content');
  const close = document.getElementById('cost-close');
  if (!root || !coin || !panel || !content || !close) return;
  let current = null, pinned = null, drag = null, ignoreClick = false;
  const money = n => `¥${Number(n).toFixed(4)}`;
  const label = g => g.kind === 'collection' ? '极致了 · 数据采集'
    : g.kind === 'search' ? `联网搜索 · ${g.model || '未知模型'}` : `模型 · ${g.model || '未知模型'}`;
  const kindClass = g => g.kind === 'collection' ? 'collection' : g.kind === 'search' ? 'search' : 'model';
  const count = n => Number(n || 0).toLocaleString('zh-CN');
  const movePanel = () => {
    if (panel.hidden) return;
    const r = coin.getBoundingClientRect();
    const width = panel.offsetWidth, height = panel.offsetHeight;
    panel.style.left = `${Math.max(12, Math.min(innerWidth - width - 12, r.right - width))}px`;
    panel.style.top = `${r.top >= height + 12 ? r.top - height - 10 : Math.min(innerHeight - height - 12, r.bottom + 10)}px`;
  };
  const draw = data => {
    if (!data || !data.tracked) {
      content.innerHTML = '<p class="cost-empty">还没有可统计的用量。旧任务无法追溯此前的调用。</p>';
      movePanel(); return;
    }
    const groups = data.groups || [];
    const total = Math.max(0, Number(data.known_cny) || 0);
    const amount = g => Math.max(0, Number(g.cny) || 0);
    const share = g => total ? Math.min(100, amount(g) / total * 100) : 0;
    const rows = groups.map(g => {
      const kind = kindClass(g);
      const known = Number(g.requests || 0) > Number(g.unknown || 0);
      const price = known ? money(g.cny) : '待核对';
      const shortUsage = g.kind === 'collection' ? `${count(g.requests)} 次接口请求`
        : `${count(g.requests)} 次请求${g.kind === 'search' ? ` · ${count(g.search_calls)} 次搜索` : ''}`;
      const detail = g.kind === 'collection' ? '' :
        `<details class="cost-detail"><summary>查看 token 用量</summary><p>输入 ${count(g.input_tokens)} · 缓存 ${count(g.cached_tokens)} · 输出 ${count(g.output_tokens)} token</p></details>`;
      return `<li class="cost-item cost-${kind}"><div class="cost-item-top"><b>${esc(label(g))}</b><strong>${price}</strong></div>`+
        `<div class="cost-bar" role="img" aria-label="${esc(label(g))}：已知费用占比 ${share(g).toFixed(1)}%"><span style="width:${share(g).toFixed(2)}%"></span></div>`+
        `<div class="cost-item-foot"><span>${shortUsage}${g.unknown ? ` · ${count(g.unknown)} 次待核对` : ''}</span>`+
        `<span>${total && known ? `${share(g).toFixed(1)}%` : ''}</span></div>${detail}</li>`;
    }).join('');
    const split = total ? groups.filter(g => amount(g) > 0).map(g =>
      `<span class="cost-${kindClass(g)}" style="width:${share(g).toFixed(2)}%" title="${esc(label(g))} ${money(g.cny)}"></span>`
    ).join('') : '';
    content.innerHTML = `<div class="cost-total"><span>${data.unknown_requests ? '已知费用估算' : '本次费用估算'}</span>`+
      `<strong>${money(data.known_cny)}</strong><small>按已记录请求计算 · 非实际账单</small></div>`+
      (data.unknown_requests ? `<p class="cost-unknown">另有 ${count(data.unknown_requests)} 次请求费用待核对，图表仅显示已知部分。${data.cache_unknown_requests ? `其中 ${count(data.cache_unknown_requests)} 次旧记录缺少缓存写入明细，无法精确倒算。` : ''}</p>` : '')+
      `<div class="cost-chart-head"><b>费用构成</b><span>按金额占比</span></div>`+
      (split ? `<div class="cost-split" role="img" aria-label="已知费用按项目分布">${split}</div>` : '')+
      `<ul class="cost-items">${rows || '<li class="cost-empty">本任务尚未发出计费请求</li>'}</ul>`+
      `<details class="cost-pricing"><summary>计价依据与说明</summary><p>按请求、实际 token、缓存量及已返回的搜索次数估算。DeepSeek 按中国时间峰谷费率，原生搜索成功一次 ¥0.005；百炼北京 Qwen3.8-Flash 按人民币费率、联网搜索 ¥0.004/次；OpenAI 美元费用按假设汇率 ¥${esc(data.fx)}/USD 换算。极致了按 ¥${esc(data.provider_cny)}/次。价格核对日 ${esc(data.price_date)}；优惠、汇率和账单舍入可能有差异，以供应商账单为准。</p></details>`;
    movePanel();
  };
  window.renderCostWidget = data => { current = data; if (!pinned) draw(current); };
  window.pinCostWidget = data => { pinned = data; draw(pinned); };
  window.unpinCostWidget = () => { pinned = null; draw(current); };
  const show = value => {
    panel.hidden = !value;
    coin.setAttribute('aria-expanded', String(value));
    if (value) { draw(pinned || current); requestAnimationFrame(movePanel); }
  };
  coin.addEventListener('click', () => { if (ignoreClick) { ignoreClick = false; return; } show(panel.hidden); });
  close.addEventListener('click', () => { show(false); coin.focus(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel.hidden) { show(false); coin.focus(); } });
  document.addEventListener('pointerdown', e => { if (!root.contains(e.target)) show(false); });
  coin.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    const r = root.getBoundingClientRect();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, left: r.left, top: r.top, moved: false };
    coin.setPointerCapture(e.pointerId);
  });
  coin.addEventListener('pointermove', e => {
    if (!drag || drag.id !== e.pointerId) return;
    if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) > 5) drag.moved = true;
    if (!drag.moved) return;
    root.style.right = 'auto'; root.style.bottom = 'auto';
    root.style.left = `${Math.max(8, Math.min(innerWidth - root.offsetWidth - 8, drag.left + e.clientX - drag.x))}px`;
    root.style.top = `${Math.max(8, Math.min(innerHeight - root.offsetHeight - 8, drag.top + e.clientY - drag.y))}px`;
    movePanel();
  });
  coin.addEventListener('pointerup', e => {
    if (!drag || drag.id !== e.pointerId) return;
    ignoreClick = drag.moved;
    if (ignoreClick) setTimeout(() => { ignoreClick = false; }, 180);
    drag = null;
  });
  window.addEventListener('resize', () => {
    if (root.style.left) {
      root.style.left = `${Math.max(8, Math.min(innerWidth - root.offsetWidth - 8, parseFloat(root.style.left)))}px`;
      root.style.top = `${Math.max(8, Math.min(innerHeight - root.offsetHeight - 8, parseFloat(root.style.top)))}px`;
    }
    movePanel();
  });
})();
