buildNav('wiki');
let data, mode='knowledge', topic='', selected='', serial=0;
const graph=$('graph'), detail=$('detail');
const unique=a=>[...new Set(a)];
const button=(key,label,cls='')=>'<button type="button" class="btn '+cls+'" data-key="'+esc(key)+'">'+esc(label)+'</button>';
function render() {
  const knowledge=mode==='knowledge';
  const scopes=unique(knowledge?data.entries.map(e=>e.topic):data.preferences.map(p=>p.scope||'全部文章'));
  if(!scopes.includes(topic)) topic=scopes[0]||'';
  $('scope').innerHTML=scopes.map(s=>'<option>'+esc(s)+'</option>').join('');
  $('scope').value=topic; $('sources-panel').hidden=!knowledge;
  $('compile').hidden=!knowledge || !topic || data.compiled.includes(topic);
  $('knowledge').setAttribute('aria-pressed',String(knowledge)); $('habits').setAttribute('aria-pressed',String(!knowledge));
  const entries=data.entries.filter(e=>e.topic===topic);
  const byId=new Map(entries.map(e=>[e.id,e]));
  const cards=data.cards.filter(c=>byId.has(c.entry_id));
  const prefs=data.preferences.filter(p=>(p.scope||'全部文章')===topic);
  const cols=[[],[],[]], links=[];
  if(topic) cols[0].push(['root',topic]);
  if(knowledge){
    for(const c of cards){cols[1].push(['c'+c.id,c.point]); links.push(['root','c'+c.id],['c'+c.id,'e'+c.entry_id]);}
    for(const e of entries){cols[2].push(['e'+e.id,e.origin]); if(!cards.some(c=>c.entry_id===e.id)) links.push(['root','e'+e.id]);}
  }else{
    const drafts=new Map();
    for(const p of prefs){cols[1].push(['p'+p.id,p.instruction]);links.push(['root','p'+p.id]);
      if(p.draft_title){drafts.set('d'+p.draft_id,p.draft_title);links.push(['p'+p.id,'d'+p.draft_id]);}}
    cols[2]=[...drafts];
  }
  graph.innerHTML=topic?'<svg aria-hidden="true"></svg>'+cols.map((nodes,i)=>'<div class="nodes"><strong>'+
    (knowledge?['主题','Wiki 词条','资料出处']:['适用范围','已确认的写法','来源稿件'])[i]+'</strong>'+
    nodes.map(([k,label])=>button(k,label,selected===k?'chosen':'')).join('')+'</div>').join(''):
    '<p>'+ (knowledge?'先添加一份资料，再从原文建立词条。':'在稿件库编辑稿件，点「保存并记住改法」即可添加第一条偏好。')+'</p>';
  graph._edges=links; requestAnimationFrame(draw);
}
function draw(){
  const svg=graph.querySelector('svg'); if(!svg)return;
  const box=graph.getBoundingClientRect();
  svg.setAttribute('width',graph.scrollWidth);svg.setAttribute('height',graph.scrollHeight);
  svg.replaceChildren();
  for(const [a,b] of graph._edges){
    const x=graph.querySelector('[data-key="'+a+'"]').getBoundingClientRect(),y=graph.querySelector('[data-key="'+b+'"]').getBoundingClientRect();
    const x1=x.right-box.left,x2=y.left-box.left,y1=x.top+x.height/2-box.top,y2=y.top+y.height/2-box.top,m=(x1+x2)/2;
    const line=document.createElementNS('http://www.w3.org/2000/svg','path');
    line.setAttribute('d','M'+x1+' '+y1+' C'+m+' '+y1+' '+m+' '+y2+' '+x2+' '+y2);
    svg.append(line);
  }
}
new ResizeObserver(draw).observe(graph);
async function refresh(){const r=await call('wiki_list'); if(!r)return; data=r;render();}
function clearDetail(){++serial;selected='';detail.replaceChildren();}
function view(m){mode=m;topic='';clearDetail();render();}
$('knowledge').onclick=()=>view('knowledge');$('habits').onclick=()=>view('preferences');
$('scope').onchange=e=>{topic=e.target.value;clearDetail();render();};
async function select(key){
  const n=++serial;selected=key;render();detail.replaceChildren();
  if(key==='root'){detail.textContent=topic;return;}
  const id=Number(key.slice(1)),kind=key[0];
  if(kind==='d'){sessionStorage.setItem('openDraft',id);await go('drafts');return;}
  if(kind==='p'){
    const p=data.preferences.find(p=>p.id===id);if(!p)return;
    detail.innerHTML='<h2>写作偏好</h2><label>适用关键词（留空表示全部）<input id="scope-edit" maxlength="40"></label><label>写法<textarea id="point" maxlength="160"></textarea></label><button class="btn" id="store">保存修改</button> <button class="btn" id="remove">删除偏好</button>';
    $('scope-edit').value=p.scope;$('point').value=p.instruction;
    $('store').onclick=async()=>{if(await call('wiki_preference',id,0,0,$('scope-edit').value,$('point').value)){topic=$('scope-edit').value.trim()||'全部文章';clearDetail();await refresh();}};
    $('remove').onclick=async()=>{if(await confirmBox('删除偏好','后续新任务将不再使用这条偏好。','删除')&&await call('wiki_forget',id)){clearDetail();await refresh();}};return;
  }
  const card=kind==='c'?data.cards.find(c=>c.id===id):null;
  const e=await call('wiki_entry',card?card.entry_id:id);
  if(!e||serial!==n)return;
  detail.innerHTML='<h2>'+esc(e.origin)+'</h2><details '+(card?'':'open')+'><summary>查看来源原文</summary><div class="original">'+esc(e.body)+'</div></details>'+
   '<label>词条要点<textarea id="point" maxlength="150"></textarea></label><label>原文引句<textarea id="quote" maxlength="320" placeholder="复制来源中连续的 6–320 字"></textarea></label>'+
   '<button class="btn primary" id="store">'+(card?'保存词条修改':'新增 Wiki 词条')+'</button> '+
   (card||!e.article_id?'<button class="btn" id="remove">'+(card?'删除词条':'删除这份资料')+'</button>':'');
  $('point').value=card?.point||'';$('quote').value=card?.quote||'';
  $('store').onclick=async()=>{const r=await call('wiki_card',e.id,card?.id||0,$('point').value,$('quote').value);
    if(r){await refresh();await select('c'+r.id);toast('词条已保存');}};
  if($('remove'))$('remove').onclick=async()=>{if(await confirmBox(card?'删除词条':'删除来源资料',card?'来源原文将保留。':'这份资料及其关联词条将一并删除。','删除')&&
    await call(card?'wiki_remove_card':'wiki_delete',id)){clearDetail();await refresh();}};
}
graph.onclick=e=>{const b=e.target.closest('[data-key]');if(b)select(b.dataset.key);};
$('add').onclick=async()=>{const t=$('topic').value.trim();if(await call('wiki_add',t,$('origin').value,$('body').value)){topic=t;$('body').value='';clearDetail();await refresh();toast('资料已保存，点出处可新增词条');}};
$('import').onclick=async()=>{const t=$('topic').value.trim();const r=await call('wiki_import',t);if(r&&!r.cancelled){topic=t;clearDetail();await refresh();}};
$('backfill').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const r=await call('wiki_backfill');if(r){await refresh();toast('已整理 '+r.count+' 篇');}}finally{b.disabled=false;}};
$('compile').onclick=async e=>{if(!await confirmBox('归纳主题','发送本主题最近 8 条资料各最多 800 字给摘要模型，产生一次模型费用。','归纳'))return;
 const b=e.currentTarget;b.disabled=true;try{if(await call('wiki_compile',topic)){clearDetail();await refresh();}}finally{b.disabled=false;}};
ready(refresh);