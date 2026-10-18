'use strict';
const $ = id => document.getElementById(id);
const enc = encodeURIComponent;
const state = {
  sources: [], tab: 'browse', books: [], categories: [], detail: null, detailItem: null, reader: null,
  discover: {source: '', action: 'latest', page: 1, name: '', sort: '', mode: 'day'}, search: null, searchCounts: null, view: 'home',
  health: {}, healthDots: {}, chapterDesc: readLocal('comic:chapter-desc') === true,
  chapterPreviews: readLocal('comic:chapter-previews') === true, recentPage: readLocal('comic:recent-page') !== false,
  searchMode: 'keyword', searchDeduplicate: false, searchCategories: {}, searchCategoryRequests: {},
  browseRequest: 0, shelfRequest: 0, detailRequest: 0, readerRequest: 0, resultItems: [], resultShown: 0, activeTasks: 0,
  reading: [], readingIndex: new Map(), readingLoaded: false, favoriteIds: {},
};
const BROWSE_ACTIONS = {latest: '最新更新', category: '分类浏览', leaderboard: '排行榜', random: '随机推荐'};
const BROWSE_ORDER = ['latest', 'category', 'leaderboard', 'random'];
const TAB_LABELS = {browse:'发现漫画',library:'我的书库',downloads:'下载任务','jm-favorites':'JM 收藏','bika-favorites':'哔咔收藏'};
const statuses = {queued:'排队中',running:'下载中',cancelling:'正在取消',cancelled:'已取消',failed:'失败',completed:'已完成',fetching:'获取章节',downloading:'下载图片',packaging:'打包 PDF',interrupted:'重启中断'};
const TONES = {queued:'muted',running:'info',cancelling:'warn',cancelled:'muted',failed:'danger',interrupted:'danger',completed:'ok'};
const CAP_LABELS = {search:'搜索',latest:'最新',category:'分类',leaderboard:'排行',random:'随机',login:'账号',favorites:'收藏夹',add_favorite:'加入收藏',remove_favorite:'取消收藏'};

const icons = {
  star:'<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
  'book-open':'<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  download:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  'refresh-cw':'<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
  tag:'<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.83z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
  'trash-2':'<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/>',
  x:'<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  settings:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  key:'<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>',
  unlink:'<path d="m18.84 12.25 1.72-1.71h-.02a5.004 5.004 0 0 0-.12-7.07 5.006 5.006 0 0 0-6.95 0l-1.72 1.71"/><path d="m5.17 11.75-1.71 1.71a5.004 5.004 0 0 0 .12 7.07 5.006 5.006 0 0 0 6.95 0l1.71-1.71"/><line x1="8" y1="2" x2="8" y2="5"/><line x1="2" y1="8" x2="5" y2="8"/><line x1="16" y1="19" x2="16" y2="22"/><line x1="19" y1="16" x2="22" y2="16"/>',
  copy:'<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  play:'<polygon points="6 3 20 12 6 21 6 3"/>',
  check:'<polyline points="20 6 9 17 4 12"/>',
  image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  'toggle-left':'<rect class="switch-track" x="1" y="5" width="22" height="14" rx="7"/><circle class="switch-knob" cx="8" cy="12" r="5"/>',
  'toggle-right':'<rect class="switch-track" x="1" y="5" width="22" height="14" rx="7"/><circle class="switch-knob" cx="16" cy="12" r="5"/>',
  folder:'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  'shield-check':'<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  sort:'<path d="M11 5h10M11 9h7M11 13h4"/><path d="M3 17l3 3 3-3M6 20V4"/>',
  'chevron-left':'<polyline points="15 18 9 12 15 6"/>',
  'chevron-right':'<polyline points="9 18 15 12 9 6"/>',
  'chevrons-right':'<polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/>',
  list:'<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
  compass:'<circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/>',
  search:'<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  alert:'<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>',
  inbox:'<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  bookmark:'<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
  dots:'<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  user:'<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
};
const svgIcon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name]}</svg>`;

function node(tag, text, className) {const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
function button(text, action, className) {const n=node('button',text,className);n.type='button';n.onclick=()=>run(action,n);return n;}
function iconBtn(name, label, action, className) {const n=node('button',undefined,(className?className+' ':'')+'icon-btn');n.type='button';n.title=label;n.setAttribute('aria-label',label);n.innerHTML=svgIcon(name);n.onclick=()=>run(action,n);return n;}
// Author and tag chips are real links, so the browser's own context menu can open the search in a new tab.
// A plain left click stays in the app: modified and middle clicks are left to the browser.
function searchLink(text, href, action, className) {
  const n=node('a',text,className);n.href=href;
  n.onclick=event=>{
    if(event&&(event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey))return;
    event?.preventDefault();return run(action);
  };
  return n;
}
function labelBtn(icon, text, action, className) {const n=button('',action,className);n.innerHTML=svgIcon(icon);n.append(node('span',text));return n;}
async function run(action, control) {if(control)control.disabled=true;try{return await action();}catch(e){if(e.name!=='AbortError')toast(e.message,{tone:'error'});}finally{if(control)control.disabled=false;}}

// The toast is a manual popover so it renders above open modal dialogs.
function toast(message, opts={}) {
  const t=$('toast');t.replaceChildren(node('span',message));t.className=opts.tone||'';
  if(opts.action){const b=node('button',opts.action.label,'toast-action');b.type='button';b.onclick=()=>{hideToast();opts.action.run();};t.append(b);}
  if(POPOVER){if(t.matches(':popover-open'))t.hidePopover();t.showPopover();}else t.hidden=false;
  clearTimeout(toast.timer);toast.timer=setTimeout(hideToast,opts.action?6500:4000);
}
const POPOVER=typeof HTMLElement.prototype.showPopover==='function';
function hideToast(){const t=$('toast');if(POPOVER){if(t.matches(':popover-open'))t.hidePopover();}else t.hidden=true;}
if(POPOVER)$('toast').hidden=false;

async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
  let data={};try{data=await response.json();}catch{}
  if(!response.ok){const err=new Error(data.error?.message||data.detail||`请求失败 (${response.status})`);err.code=data.error?.code;err.source=data.error?.source;throw err;}
  return data;
}
const write=(method,data)=>({method,body:JSON.stringify(data)});
const sourceName=id=>state.sources.find(s=>s.id===id)?.name||id;
const bookPath=b=>`/api/library/${enc(b.source)}/${enc(b.id)}`;
function options(select, values, selected) {select.replaceChildren(...values.map(v=>{const n=node('option',v.label);n.value=v.value;return n;}));if(values.some(v=>v.value===selected))select.value=selected;}
function readLocal(key) {try{return JSON.parse(localStorage.getItem(key));}catch{return null;}}
function saveLocal(key,value) {try{localStorage.setItem(key,JSON.stringify(value));}catch{if(!saveLocal.warned){toast('浏览器无法保存偏好设置，请检查存储权限');saveLocal.warned=true;}}}
// Reading progress lives in server SQLite; detail objects carry their own {comic, chapters} snapshot.
const readingKey=b=>`${b.source}:${String(b.id)}`;
function chapterProgress(b,chapter){const p=b?.reading?.chapters?.[chapter];return p?{index:Number(p.page)||0}:null;}
function readingLast(b){const c=b?.reading?.comic||state.readingIndex?.get(readingKey(b));return c?{chapterId:String(c.chapter_id),chapterName:c.chapter_name||'',index:Number(c.page)||0}:null;}
function safeImage(url) {try {const u=new URL(url,location.origin);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}}
// Route upstream covers through the image proxy so no direct request leaks to the CDN.
function coverFor(item) {if(sourcePlugin(item.source)?.enabled===false)return '';const url=safeImage(item.cover);if(!url)return '';if(url.startsWith(location.origin))return url;return `/api/image/proxy?${new URLSearchParams({source:item.source,url})}`;}
function image(url, alt, className) {const n=node('img',undefined,className);n.alt=alt;n.loading='lazy';n.decoding='async';const safe=safeImage(url);if(safe)n.src=safe;else n.classList.add('broken');n.onerror=()=>{n.removeAttribute('src');n.classList.add('broken');};n.onload=()=>n.classList.add('ready');return n;}
// Cover box keeps its 3:4 slot and shows the title's first glyph when the image is missing.
function coverBox(item,className){const box=node('div',undefined,'cover'+(className?' '+className:''));box.dataset.initial=(item.title||'?').trim().slice(0,1)||'?';box.append(image(coverFor(item),item.title||''));return box;}
// Same image on another CDN host is the same cover; compare by path so host failover does not reload it.
function coverKey(url){try{const u=new URL(url,location.origin),inner=u.searchParams.get('url');return inner?coverKey(inner):u.pathname+u.search;}catch{return String(url||'');}}
// Reconcile a card list by key: unchanged cards keep their DOM node (loaded cover, finished animation); only changed or moved cards are touched.
function renderKeyed(box,items,keyOf,signatureOf,build){
  const previous=box.keyedCards||new Map(),next=new Map();
  const nodes=items.map(item=>{
    const key=String(keyOf(item)),signature=JSON.stringify(signatureOf(item)),old=previous.get(key);
    if(next.has(key))return build(item);
    const entry=old&&old.signature===signature?old:{signature,el:build(item)};next.set(key,entry);return entry.el;
  });
  box.keyedCards=next;
  const keep=new Set(nodes);for(const child of [...box.children])if(!keep.has(child))child.remove();
  nodes.forEach((el,index)=>{if(box.children[index]!==el)box.insertBefore(el,box.children[index]||null);});
  return nodes;
}
function skeletons(count){return Array.from({length:count},()=>{const n=node('div',undefined,'card skeleton');n.append(node('i'),node('b'),node('b'));return n;});}
function emptyState(title,hint,action,icon='inbox'){
  const box=node('div',undefined,'empty-state');const art=node('div',undefined,'empty-icon');art.innerHTML=svgIcon(icon);
  box.append(art,node('h3',title));if(hint)box.append(node('p',hint,'muted'));if(action)box.append(action);return box;
}
function timeAgo(value){
  const t=Date.parse(String(value||'').replace(' ','T')+(/[zZ+]/.test(value||'')?'':'Z'));if(!t)return '';
  const s=Math.max(0,(Date.now()-t)/1000);
  if(s<60)return '刚刚';if(s<3600)return `${Math.floor(s/60)} 分钟前`;if(s<86400)return `${Math.floor(s/3600)} 小时前`;if(s<86400*30)return `${Math.floor(s/86400)} 天前`;
  return new Date(t).toLocaleDateString();
}
async function copyText(text){
  try{await navigator.clipboard.writeText(text);return;}catch{}
  const area=node('textarea');area.value=text;area.style.cssText='position:fixed;opacity:0';document.body.append(area);area.select();
  try{document.execCommand('copy');}finally{area.remove();}
}

// Styled replacement for prompt()/confirm(). Resolves to the entered text / true, or null / false when dismissed.
function ask({title,message='',input=false,value='',placeholder='',choices=[],ok='确定',danger=false}){
  return new Promise(resolve=>{
    const dlg=$('ask-dialog'),field=$('ask-input');
    $('ask-title').textContent=title;$('ask-message').textContent=message;$('ask-message').hidden=!message;
    field.hidden=!input;field.value=value;field.placeholder=placeholder;
    $('ask-options').replaceChildren(...choices.map(v=>{const o=node('option');o.value=v;return o;}));
    $('ask-ok').textContent=ok;$('ask-ok').classList.toggle('danger',danger);
    let settled=false;
    const done=result=>{if(settled)return;settled=true;dlg.onclose=null;if(dlg.open)dlg.close();resolve(result);};
    $('ask-form').onsubmit=event=>{event.preventDefault();done(input?field.value.trim():true);};
    $('ask-cancel').onclick=()=>done(input?null:false);
    dlg.onclose=()=>done(input?null:false);
    dlg.showModal();
    if(input){field.focus();field.select();}else $('ask-ok').focus();
  });
}

/* ---------- sources & discovery state ---------- */
async function loadSources() {
  const request=state.sourceRequest=(state.sourceRequest||0)+1;
  const sources=(await api('/api/sources',{cache:'no-store'})).sources;
  if(request!==state.sourceRequest)return;
  const changed=state.browseReady&&(sources.length!==state.sources.length||sources.some(p=>(p.enabled!==false)!==(sourcePlugin(p.id)?.enabled!==false)));
  state.sources=sources;state.searchCategories={};
  // Cloud favorite marks belong to the bound account; the source list carries no identity, so refetch on every reload.
  state.favoriteIds={};if(state.resultItems.length)loadResultFavorites(state.resultItems,state.browseRequest);
  renderSourceList();syncSourceNavigation();
  if(state.settingsSource)renderSourceSettings();
  if(!state.browseReady){initBrowse();endBooting();}
  else if(changed)refreshSourceViews();
  else if(state.view==='results')renderFilters();
  else if(state.shelfBlocked)run(loadShelves);
}
// Reveals the page once the deep-linked view is in place (set by the inline script in index.html).
function endBooting(){document.documentElement.classList.remove('booting');}
function sourcePlugin(id){return state.sources.find(s=>s.id===id);}
function activeSources(){return state.sources.filter(p=>p.enabled!==false);}
function discoverActions(p){return BROWSE_ORDER.filter(a=>p&&p.enabled!==false&&(p.capabilities||[]).includes(a));}
function browsable(){return activeSources().filter(p=>discoverActions(p).length);}
function syncSourceNavigation(){
  for(const source of ['jm','bika']){
    const p=sourcePlugin(source),hidden=!p||p.enabled===false||!p.capabilities.includes('favorites'),tab=`${source}-favorites`;
    document.querySelectorAll(`[data-tab="${tab}"]`).forEach(control=>control.hidden=hidden);
    if(hidden&&state.tab===tab)showTab('browse',true,false);
  }
}
function refreshSourceViews(){
  state.browseAbort?.abort();++state.browseRequest;++state.shelfRequest;state.searchCounts=null;state.searchCategories={};
  state.resultItems=[];state.resultShown=0;state.shelvesLoaded=false;state.discover.page=1;
  $('results').replaceChildren();$('results-more').hidden=true;$('pager').hidden=true;$('source-shelves').replaceChildren();
  for(const p of state.sources)if(p.enabled===false){state.healthChecks?.[p.id]?.abort();if(state.healthChecks)delete state.healthChecks[p.id];delete state.health[p.id];}
  const detailSource=state.detailItem?.source||state.detail?.source;
  if(detailSource&&sourcePlugin(detailSource)?.enabled===false){
    ++state.detailRequest;chapterPreviews.stop();if($('reader-dialog').open)closeReader(true);
    if($('detail-dialog').open)$('detail-dialog').close();state.detail=state.detailItem=null;
  }
  ++jm.request;jm.items=[];jm.hasNext=false;jm.loadedKey='';jm.selected.clear();syncJmSelection();
  ++bikaFavorites.request;bikaFavorites.loadedKey='';bikaFavorites.items=[];bikaFavorites.hasNext=false;bikaFavorites.selected?.clear();
  for(const source of ['jm','bika']){$(`${source}-favorites-books`).replaceChildren();$(`${source}-favorites-count`).textContent='';$(`${source}-favorites-pager`).hidden=true;}
  syncSourceNavigation();renderQuick();renderContinue();filterBooks();normalizeSearch();
  if(state.view==='results')run(state.search?loadSearch:loadDiscover);else run(loadShelves);
  if(state.tab==='jm-favorites')run(loadJmFavoritesView);
  if(state.tab==='bika-favorites')run(loadBikaFavoritesView);
}
function needsLogin(p){return !!p&&(p.capabilities||[]).includes('login')&&!p.account?.authenticated&&!p.account?.configured;}
// Default to a source that can list without an account, so discovery is never empty on first visit.
function discoverPlugin(){const list=browsable();return list.find(p=>p.id===state.discover.source)||list.find(p=>!needsLogin(p))||list[0];}
function sortsFor(p,action){return action==='category'?(p.sorts||[]):action==='latest'?(p.latest_sorts||[]):[];}
function pick(values,value){return values.some(v=>v.value===value)?value:(values[0]?.value??'');}
function normalizeDiscover(){
  const d=state.discover,p=discoverPlugin();if(!p)return null;d.source=p.id;
  const actions=discoverActions(p);if(!actions.includes(d.action))d.action=actions[0];
  d.name=d.action==='category'?pick(p.categories||[],d.name):'';
  d.mode=d.action==='leaderboard'?pick(p.leaderboard_modes||[],d.mode):'day';
  d.sort=pick(sortsFor(p,d.action),d.sort);
  return p;
}

/* ---------- browse views & history ---------- */
// The browse tab has two views: home (search + shelves) and a results view that owns all filters.
function setView(view){
  state.view=view;
  $('browse').classList.toggle('view-home',view==='home');
  $('browse').classList.toggle('view-results',view==='results');
  $('home-feed').hidden=view!=='home';
  $('results-view').hidden=view!=='results';
}
function viewURL(){
  if(state.tab&&state.tab!=='browse')return '?'+new URLSearchParams({tab:state.tab});
  if(state.view!=='results')return location.pathname;
  const params=new URLSearchParams();
  if(state.search){for(const [key,value] of searchParams(state.search))params.set(key==='keyword'?'q':key,value);}
  else{params.set('browse',state.discover.action);params.set('source',state.discover.source);}
  return '?'+params;
}
// fromHome marks a results entry pushed directly on top of the browse home entry; only then can the
// results back button pop history. Replacing an entry keeps its mark; any other push clears it.
function syncHistory(push,fromHome=false){
  const snap={tab:state.tab,view:state.view,search:state.search&&{...state.search},discover:{...state.discover}};
  if(state.tab==='browse'&&state.view==='results'&&(push?fromHome:history.state?.fromHome))snap.fromHome=true;
  try{history[push?'pushState':'replaceState'](snap,'',viewURL());}catch{}
  document.title=state.tab!=='browse'?`${TAB_LABELS[state.tab]} · Comic`:state.view==='results'?`${state.search?state.search.keyword:BROWSE_ACTIONS[state.discover.action]} · Comic`:'Comic · 聚合漫画';
}
function openResults(){
  const push=state.view!=='results';
  setView('results');
  syncHistory(push,push);
  window.scrollTo({top:0});
  return run(state.search?loadSearch:loadDiscover);
}
function showHome(){
  state.search=null;state.browseAbort?.abort();++state.browseRequest;
  $('keyword').value='';syncClear();
  setView('home');syncSearchMode();renderContinue();renderRecent();
  if(!state.shelvesLoaded||state.shelfBlocked||state.shelfFailed)run(loadShelves);
}
function goHome(){
  if(state.tab!=='browse'){showTab('browse',false);showHome();syncHistory(true);return;}
  if(state.view==='results'&&history.state?.fromHome){history.back();return;}
  showHome();syncHistory(false);
}
function initBrowse(){
  state.browseReady=true;
  const saved=readLocal('comic:discover');if(saved&&typeof saved==='object')Object.assign(state.discover,saved,{page:1});
  renderQuick();renderRecent();renderContinue();syncSearchMode();
  const params=new URLSearchParams(location.search),tab=params.get('tab');
  if(tab==='settings')run(openSiteSettings);
  if(tab&&Object.hasOwn(TAB_LABELS,tab)&&tab!=='browse'){setView('home');showTab(tab,false);syncHistory(false);return;}
  const q=(params.get('q')||'').trim().slice(0,200),src=params.get('source'),action=params.get('browse');
  if(q){
    state.search={keyword:q,source:sourcePlugin(src)?src:'',page:Math.min(1000,Math.max(1,parseInt(params.get('page'),10)||1)),sort:params.get('sort')||'',mode:['tag','author'].includes(params.get('mode'))?params.get('mode'):'keyword',tag_value:(params.get('tag_value')||'').slice(0,200),category:(params.get('category')||'').slice(0,200),deduplicate:['true','1'].includes(params.get('deduplicate'))};
    normalizeSearch();
    $('keyword').value=q;syncClear();setView('results');syncHistory(false);run(loadSearch);return;
  }
  if(BROWSE_ORDER.includes(action)){
    if(sourcePlugin(src))state.discover.source=src;
    Object.assign(state.discover,{action,page:1});setView('results');syncHistory(false);run(loadDiscover);return;
  }
  setView('home');syncHistory(false);run(loadShelves);
}
function renderQuick(){
  const box=$('quick');box.replaceChildren();
  for(const a of BROWSE_ORDER.filter(a=>browsable().some(p=>discoverActions(p).includes(a))))box.append(button(BROWSE_ACTIONS[a],()=>browseAction(a),'quick-link'));
}
function browseAction(action,sourceId){
  if(sourceId)state.discover.source=sourceId;
  const p=discoverPlugin();
  if(!discoverActions(p).includes(action)){const alt=browsable().find(x=>discoverActions(x).includes(action));if(alt)state.discover.source=alt.id;}
  state.search=null;Object.assign(state.discover,{action,page:1});
  if(state.tab!=='browse')showTab('browse');
  $('keyword').value='';syncClear();
  return openResults();
}

/* ---------- recent searches & reading history ---------- */
function readRecent(){
  const list=readLocal('comic:recent');if(!Array.isArray(list))return [];
  return list.map(item=>typeof item==='string'?{keyword:item,mode:'keyword'}:item).filter(item=>item&&typeof item.keyword==='string').slice(0,10).map(item=>({...recentEntry({...item,keyword:item.keyword.slice(0,200)}),page:Math.min(1000,Math.max(1,parseInt(item.page,10)||1)),sort:typeof item.sort==='string'?item.sort.slice(0,200):''}));
}
function recentEntry(search){
  const text=value=>typeof value==='string'?value:'';
  return {keyword:search.keyword,mode:['tag','author'].includes(search.mode)?search.mode:'keyword',source:text(search.source),tag_value:text(search.tag_value),category:text(search.category),deduplicate:search.deduplicate===true,page:search.page||1,sort:text(search.sort)};
}
// A recent entry is identified by its query and filters; page and sort record where browsing left off.
function recentKey(item){return JSON.stringify([item.keyword,item.mode,item.source,item.tag_value,item.category,item.deduplicate]);}
function addRecent(search){
  const entry=recentEntry(search);search.recent=recentKey(entry);
  saveLocal('comic:recent',[entry,...readRecent().filter(item=>recentKey(item)!==search.recent)].slice(0,10));
}
// Writes where a search left off back onto the recent entry it was started from, including filters switched
// on the results page; searches opened from a URL fall back to the entry with identical filters.
function saveRecentPage(search){
  const list=readRecent(),from=search.recent||recentKey(recentEntry(search)),index=list.findIndex(item=>recentKey(item)===from);
  if(index<0)return;
  const entry=recentEntry(search),key=recentKey(entry);search.recent=key;
  if(JSON.stringify(list[index])===JSON.stringify(entry))return;
  list[index]=entry;saveLocal('comic:recent',list.filter((item,i)=>i===index||recentKey(item)!==key));
}
function renderRecent(){
  const list=readRecent();$('recent').hidden=!list.length;
  $('recent-list').replaceChildren(...list.map(item=>{
    const resume=state.recentPage&&item.page>1,chip=button((item.mode==='tag'?'标签 · ':item.mode==='author'?'作者 · ':'')+item.keyword,()=>startSearch(item.keyword,item.source,resume?item:{...item,page:1,sort:''}),'recent-chip');
    if(resume)chip.title=`上次浏览至第 ${item.page} 页`;
    return chip;
  }));
}
function syncRecentPageToggle(){
  const toggle=$('recent-page-toggle'),on=state.recentPage;
  toggle.setAttribute('aria-checked',String(on));toggle.classList.toggle('on',on);
  toggle.innerHTML=svgIcon(on?'toggle-right':'toggle-left');toggle.append(node('span',on?'已开启':'已关闭'));
}
function readHistory(){return readingItems().filter(h=>h.in_history).map(h=>({source:h.source,id:h.id,title:h.title,cover:h.cover,author:h.author,chapterId:h.chapter_id,chapterName:h.chapter_name,index:h.page,total:h.total,updatedAt:h.updated_at}));}
function readingItems(){return state.reading||[];}
function setReading(items){state.reading=items;state.readingIndex=new Map(items.map(h=>[readingKey(h),h]));}
function upsertReading(entry){setReading([entry,...readingItems().filter(h=>readingKey(h)!==readingKey(entry))]);}
async function loadReading(){
  const data=await api('/api/reading'),local=new Map([...readingQueue.pending.values()].map(job=>[readingKey(job.entry),job.entry]));
  setReading([...local.values(),...(data.items||[]).filter(h=>!local.has(readingKey(h)))]);
  renderContinue();filterBooks();refreshCardStates();
}
async function fetchComicReading(item){
  await flushReading();
  try{return await api(`/api/reading/${enc(item.source)}/${enc(item.id)}`);}
  catch{return {comic:state.readingIndex?.get(readingKey(item))||null,chapters:{}};}
}
// Writes are coalesced per chapter and sent in order, so a slow request never overwrites a newer position.
const readingQueue={pending:new Map(),timer:0,warned:false,chain:Promise.resolve()};
function recordReading(r){
  const b=r.book,now=Date.now(),cover=String(b.cover||'');
  const body={title:String(b.title||'').slice(0,500),cover:cover.length<=2048?cover:'',author:comicAuthors(b).join(' / ').slice(0,500),
    chapter_id:r.chapterId,chapter_name:String(r.chapterName||'').slice(0,500),page:r.index,total:r.images.length};
  const entry={source:b.source,id:String(b.id),...body,in_history:true,updated_at:now};
  b.reading=b.reading||{comic:null,chapters:{}};b.reading.comic=entry;b.reading.chapters={...b.reading.chapters,[r.chapterId]:{page:r.index,updated_at:now}};
  upsertReading(entry);
  const key=`${readingKey(b)}:${r.chapterId}`;readingQueue.pending.delete(key);
  readingQueue.pending.set(key,{path:`/api/reading/${enc(b.source)}/${enc(b.id)}`,body:JSON.stringify(body),entry});
  clearTimeout(readingQueue.timer);readingQueue.timer=setTimeout(flushReading,800);
}
function flushReading(keepalive=false){
  clearTimeout(readingQueue.timer);
  const jobs=[...readingQueue.pending.entries()].filter(([,job])=>!job.sending);if(!jobs.length)return readingQueue.chain;
  const send=([key,job])=>{
    job.sending=true;
    return fetch(job.path,{method:'PUT',headers:{'Content-Type':'application/json'},body:job.body,keepalive}).then(response=>{
      if(!response.ok)throw new Error(String(response.status));
      if(readingQueue.pending.get(key)===job)readingQueue.pending.delete(key);readingQueue.warned=false;
    }).catch(()=>{
      job.sending=false;
      if(!readingQueue.warned){readingQueue.warned=true;toast('阅读进度未保存到服务器，稍后自动重试',{tone:'error'});}
      clearTimeout(readingQueue.timer);readingQueue.timer=setTimeout(flushReading,5000);
    });
  };
  if(keepalive){jobs.forEach(send);return readingQueue.chain;}
  readingQueue.chain=readingQueue.chain.then(()=>jobs.reduce((done,job)=>done.then(()=>send(job)),Promise.resolve()));
  return readingQueue.chain;
}
// One-time import of history and progress saved by earlier versions in this browser's localStorage.
async function migrateLocalReading(){
  let storage;try{storage=localStorage;}catch{return;}
  const keys=[];for(let i=0;i<storage.length;i++){const key=storage.key(i);if(/^comic:(history$|progress:|last:)/.test(key))keys.push(key);}
  if(!keys.length)return;
  const text=(value,max)=>typeof value==='string'||typeof value==='number'?String(value).slice(0,max):'';
  const int=(value,max)=>Math.max(0,Math.min(max,Math.floor(Number(value)||0)));
  const parts=key=>{try{const value=JSON.parse(key);return Array.isArray(value)?value.map(v=>String(v??'')):null;}catch{return null;}};
  const valid=(source,id,chapter)=>source&&source.length<=64&&id&&id.length<=200&&chapter&&chapter.length<=200;
  const comics=new Map(),chapters=[];
  const comic=(source,id,value,inHistory)=>{
    const chapterId=text(value?.chapterId,200);if(!valid(source,id,chapterId))return;
    const cover=text(value.cover,4096);
    comics.set(`${source}:${id}`,{source,comic_id:id,title:text(value.title,500),cover:cover.length<=2048?cover:'',author:text(value.author,500),
      chapter_id:chapterId,chapter_name:text(value.chapterName,500),page:int(value.index,100000),total:int(value.total,100000),in_history:inHistory,updated_at:int(value.updatedAt,1e14)});
  };
  for(const key of keys.filter(k=>k.startsWith('comic:last:'))){const id=parts(key.slice(11));if(id?.length===2)comic(id[0],id[1],readLocal(key),false);}
  const history=readLocal('comic:history');
  if(Array.isArray(history))for(const item of history)if(item&&typeof item==='object')comic(text(item.source,64),text(item.id,200),item,true);
  for(const key of keys.filter(k=>k.startsWith('comic:progress:'))){
    const id=parts(key.slice(15)),value=readLocal(key);
    if(id?.length===3&&valid(...id)&&value&&typeof value==='object')chapters.push({source:id[0],comic_id:id[1],chapter_id:id[2],page:int(value.index,100000),updated_at:int(value.updatedAt,1e14)});
  }
  await api('/api/reading/import',write('POST',{comics:[...comics.values()].slice(0,2000),chapters:chapters.slice(0,20000)}));
  for(const key of keys)try{storage.removeItem(key);}catch{}
}
function renderContinue(){
  const list=readHistory().filter(item=>sourcePlugin(item.source)?.enabled!==false).slice(0,24),row=$('continue-row');
  $('continue-shelf').hidden=!list.length;
  row.replaceChildren(...list.map(h=>card(h,{history:h})));
  state.continueScroller?.syncArrows();
}

/* ---------- home shelves ---------- */
function scrollerFor(row){
  const wrap=node('div',undefined,'scroller');
  const step=dir=>row.scrollBy({left:dir*row.clientWidth*0.85,behavior:'smooth'});
  const prev=iconBtn('chevron-left','向左滚动',()=>step(-1),'scroll-btn prev'),next=iconBtn('chevron-right','向右滚动',()=>step(1),'scroll-btn next');
  const sync=()=>{prev.hidden=row.scrollLeft<=4;next.hidden=row.scrollLeft+row.clientWidth>=row.scrollWidth-4;};
  row.addEventListener('scroll',sync,{passive:true});
  wrap.syncArrows=()=>requestAnimationFrame(sync);
  wrap.append(row,prev,next);sync();return wrap;
}
const SHELF_SIZE=18;
async function loadShelves(){
  const request=++state.shelfRequest,box=$('source-shelves');
  state.shelvesLoaded=true;state.shelfBlocked=state.shelfFailed=false;
  const current=discoverPlugin(),list=browsable().sort((a,b)=>(b===current)-(a===current));
  box.replaceChildren();
  if(!list.length){box.append(emptyState('暂无已启用的浏览图源','图源启用状态位于「设置 → 图源管理」。'));return;}
  const jobs=[];
  for(const p of list){
    jobs.push(loadShelf(p,request));
    // A leaderboard shelf makes the home feed feel like a real product.
    if((p.capabilities||[]).includes('leaderboard')){const first=(p.leaderboard_modes||[])[0];if(first)jobs.push(loadShelf(p,request,'leaderboard',first.label,first.value));}
  }
  await Promise.all(jobs);
}
async function loadShelf(p,request,action,label,mode){
  const box=$('source-shelves');action=action||discoverActions(p)[0];
  const section=node('section',undefined,'shelf');section.dataset.source=p.id;if(label)section.dataset.action=action;
  const head=node('div',undefined,'shelf-head'),more=button('',()=>browseAction(action,p.id),'link-btn');
  more.append(node('span','查看更多'));more.insertAdjacentHTML('beforeend',svgIcon('chevron-right'));
  head.append(node('h2',p.name),node('span',label||BROWSE_ACTIONS[action],'pill'),more);
  const status=node('div',undefined,'status');status.setAttribute('role','status');
  const row=node('div',undefined,'row'),scroller=scrollerFor(row);
  section.append(head,status,scroller);
  const old=[...box.children].find(n=>n.dataset.source===p.id&&n.dataset.action===(label?action:undefined));
  old?old.replaceWith(section):box.append(section);
  if(needsLogin(p)){
    state.shelfBlocked=true;scroller.hidden=true;
    status.classList.add('notice');status.append(node('span',`${p.name}需要登录账号后才能浏览。`),bindButton(p.id));return;
  }
  row.replaceChildren(...skeletons(8));
  try{
    const params=new URLSearchParams({page:1});
    if(action==='leaderboard')params.set('mode',mode||pick(p.leaderboard_modes||[],''));
    const sorts=sortsFor(p,action);if(sorts.length)params.set('sort',sorts[0].value);
    const data=await api(`/api/${enc(p.id)}/${action}?`+params);
    if(request!==state.shelfRequest)return;
    const items=(data.data||[]).slice(0,SHELF_SIZE);
    if(!items.length){scroller.hidden=true;status.textContent='此图源暂无内容';return;}
    row.replaceChildren(...items.map(item=>card(item,{showSource:false})));
    scroller.syncArrows();
  }catch(e){
    if(request!==state.shelfRequest)return;
    if(label){section.remove();return;}// bonus shelves fail quietly
    scroller.hidden=true;state.shelfFailed=true;if(e.code==='login_required')state.shelfBlocked=true;
    showError(status,e,()=>loadShelf(p,state.shelfRequest));
  }
}

function bindButton(id){return button(`绑定${sourceName(id)}账号`,async()=>{await loadSources();state.settingsSource=id;configureAccount();$('credentials-dialog').showModal();},'small');}
// Failures get a retry button; login-required errors also get a bind shortcut.
function showError(target,e,retry){
  target.classList.add('warn');target.replaceChildren(node('span',e.message));
  if(e.code==='login_required'&&e.source)target.append(bindButton(e.source));
  if(retry)target.append(button('重试',retry,'small'));
}
function clearStatus(target){target.classList.remove('warn','notice');target.replaceChildren();}

/* ---------- results: filters, pagination ---------- */
function chipGroup(label,values,selected,onpick){
  const row=node('div',undefined,'filter-row'),list=node('div',undefined,'chips');
  list.setAttribute('role','radiogroup');list.setAttribute('aria-label',label);
  for(const v of values){
    const chip=node('button',v.label,'chip'+(v.value===selected?' active':'')+(v.warn?' warn':''));chip.type='button';
    if(v.count!==undefined)chip.append(node('span',String(v.count),'chip-count'));
    if(v.warn||v.hint)chip.title=v.warn||v.hint;
    chip.setAttribute('role','radio');chip.setAttribute('aria-checked',String(v.value===selected));
    chip.onclick=()=>{if(v.value!==selected)onpick(v.value);};list.append(chip);
  }
  row.append(node('span',label,'filter-label'),list);return row;
}
function syncSearchMode(){
  const mode=state.search?.mode||state.searchMode||'keyword';state.searchMode=mode;
  $('search-mode').replaceChildren(chipGroup('搜索方式',[{value:'keyword',label:'关键词'},{value:'tag',label:'标签'},{value:'author',label:'作者'}],mode,value=>{
    state.searchMode=value;
    if(state.search){Object.assign(state.search,{mode:value,page:1,sort:'',tag_value:''});run(loadSearch);}
    else syncSearchMode();
  }));
  const deduplicate=state.search?state.search.deduplicate===true:state.searchDeduplicate===true;state.searchDeduplicate=deduplicate;
  const toggle=button(`结果去重：${deduplicate?'开启':'关闭'}`,()=>{
    state.searchDeduplicate=!deduplicate;
    if(state.search){Object.assign(state.search,{deduplicate:state.searchDeduplicate,page:1});return loadSearch();}
    syncSearchMode();
  },'chip dedup-chip'+(deduplicate?' active':''));
  toggle.textContent='';toggle.append(node('i',undefined,'dedup-switch'),node('span',`结果去重：${deduplicate?'开启':'关闭'}`));
  toggle.setAttribute('role','switch');toggle.setAttribute('aria-label','结果去重');toggle.setAttribute('aria-checked',String(deduplicate));
  toggle.title='仅合并本页高置信度重复作品，保留所有版本入口';$('search-mode').append(toggle);
  $('keyword').placeholder=mode==='tag'?'输入标签名称；拷贝使用题材…':mode==='author'?'搜索作者名称…':'搜索漫画、作者…';
  $('keyword').setAttribute('aria-label',mode==='tag'?'搜索漫画标签':mode==='author'?'搜索漫画作者':'搜索漫画');
}
function normalizeSearch(){
  const s=state.search;if(!s)return;
  s.mode=['tag','author'].includes(s.mode)?s.mode:'keyword';s.deduplicate=s.deduplicate===true;
  if(s.source&&(sourcePlugin(s.source)?.enabled===false||!(sourcePlugin(s.source)?.capabilities||[]).includes({tag:'tag_search',author:'author_search'}[s.mode]||'search'))){s.source='';s.tag_value='';s.category='';s.sort='';s.page=1;}
  if(s.mode!=='tag'||!s.source)s.tag_value='';
  if(!(sourcePlugin(s.source)?.capabilities||[]).includes('search_categories'))s.category='';
  s.sort=pick(sourcePlugin(s.source)?.[s.mode==='tag'?'tag_sorts':'search_sorts']||[],s.sort);
  syncSearchMode();
}
function loadSearchCategories(source,force=false){
  if(sourcePlugin(source)?.enabled===false)return;
  const previous=state.searchCategories[source];
  if(state.searchCategoryRequests[source]||(!force&&previous?.expires>Date.now()))return;
  state.searchCategories[source]={items:previous?.items||[],loading:true};
  state.searchCategoryRequests[source]=api(`/api/sources/${enc(source)}/search-categories`).then(data=>{
    state.searchCategories[source]={items:comicLabels(data.categories),expires:Date.now()+300000};
  }).catch(error=>{state.searchCategories[source]={items:previous?.items||[],error,expires:Date.now()+30000};}).finally(()=>{
    delete state.searchCategoryRequests[source];
    if(state.search?.source===source&&state.view==='results')renderFilters();
  });
}
function searchCategoryFilter(s){
  loadSearchCategories(s.source);
  const cached=state.searchCategories[s.source]||{},values=[{value:'',label:'全部分类'},...(cached.items||[])];
  if(s.category&&!values.some(item=>item.value===s.category))values.push({value:s.category,label:s.category});
  const box=node('div',undefined,'search-category-filter');
  box.append(chipGroup('分类',values,s.category||'',value=>{Object.assign(s,{category:value,page:1});run(loadSearch);}));
  if(cached.loading)box.append(node('span','分类加载中','filter-note'));
  if(cached.error){const notice=node('div',undefined,'filter-note');notice.append(node('span',`分类获取失败：${cached.error.message}`),button('重试',()=>{loadSearchCategories(s.source,true);renderFilters();},'link-btn'));if(cached.error.code==='login_required')notice.append(bindButton(s.source));box.append(notice);}
  return box;
}
function renderFilters(){
  const box=$('filters');box.replaceChildren();
  const s=state.search,d=state.discover;
  if(s){
    const searchable=activeSources().filter(p=>(p.capabilities||[]).includes({tag:'tag_search',author:'author_search'}[s.mode]||'search'));
    const c=!s.category&&!s.tag_value&&state.searchCounts?.key===searchKey(s)?state.searchCounts:null;
    if(searchable.length>1)box.append(chipGroup('图源',[
      {value:'',label:'全部',count:c?c.total??Object.values(c.counts).reduce((a,b)=>a+b,0):undefined,hint:s.deduplicate?'本页合并后的作品数':'本页图源原始结果数'},
      ...searchable.map(p=>({value:p.id,label:p.name,count:c?(c.errors[p.id]?'!':c.counts[p.id]??0):undefined,warn:c?.errors[p.id]?.message,hint:s.deduplicate?'本页图源原始结果数（合并前）':'本页图源原始结果数'}))],
      s.source,v=>{Object.assign(s,{source:v,page:1,sort:'',category:'',tag_value:''});run(loadSearch);}));
    if((sourcePlugin(s.source)?.capabilities||[]).includes('search_categories'))box.append(searchCategoryFilter(s));
    const sorts=sourcePlugin(s.source)?.[s.mode==='tag'?'tag_sorts':'search_sorts']||[];
    if(sorts.length)box.append(chipGroup('排序',sorts,s.sort,v=>{Object.assign(s,{sort:v,page:1});run(loadSearch);}));
  }else{
    const p=discoverPlugin();if(!p)return;
    const reload=patch=>{Object.assign(d,patch,{page:1});run(loadDiscover);};
    const list=browsable();
    if(list.length>1)box.append(chipGroup('图源',list.map(x=>({value:x.id,label:x.name})),d.source,v=>reload({source:v,name:'',sort:'',mode:'day'})));
    box.append(chipGroup('浏览',discoverActions(p).map(a=>({value:a,label:BROWSE_ACTIONS[a]})),d.action,v=>reload({action:v})));
    if(d.action==='category'&&(p.categories||[]).length)box.append(chipGroup('分类',p.categories,d.name,v=>reload({name:v})));
    if(d.action==='leaderboard'&&(p.leaderboard_modes||[]).length)box.append(chipGroup('榜单',p.leaderboard_modes,d.mode,v=>reload({mode:v})));
    const sorts=sortsFor(p,d.action);
    if(sorts.length)box.append(chipGroup('排序',sorts,d.sort,v=>reload({sort:v})));
  }
  box.hidden=!box.children.length;
}
// One pager below the results serves browse lists and searches alike; random lists get "换一批" instead.
function renderPager(count){
  const s=state.search,d=state.discover,random=!s&&d.action==='random',page=s?s.page:d.page;
  for(const id of ['discover-prev','discover-page','discover-next'])$(id).hidden=random;
  $('discover-again').hidden=!random;
  $('discover-page').textContent=`第 ${page} 页`;
  $('discover-prev').disabled=page<=1;
  $('discover-next').disabled=!count;
  $('pager').hidden=!random&&page<=1&&!count;
}
function beginResults(){
  state.browseAbort?.abort();state.browseAbort=new AbortController();
  clearStatus($('browse-status'));state.resultItems=[];state.resultShown=0;
  $('results').replaceChildren(...skeletons(14));$('pager').hidden=true;$('results-more').hidden=true;
  return ++state.browseRequest;
}
// Large aggregate result sets render in chunks as the user scrolls.
const RESULT_CHUNK=35;
function expectedSource(){return state.search?(state.search.source||null):discoverPlugin()?.id;}
function showResults(items,emptyTitle,emptyHint){
  state.resultItems=items;state.resultShown=0;$('results').replaceChildren();
  if(!items.length){$('results').append(emptyState(emptyTitle,emptyHint,undefined,'search'));$('results-more').hidden=true;$('pager').classList.remove('deferred');return;}
  showMoreResults();
}
function showMoreResults(){
  const exp=expectedSource(),next=state.resultItems.slice(state.resultShown,state.resultShown+RESULT_CHUNK);
  $('results').append(...next.map(item=>card(item,{showSource:!!item.source&&item.source!==exp,marks:true})));
  state.resultShown+=next.length;
  const left=state.resultItems.length-state.resultShown;
  $('results-more').hidden=left<=0;$('results-more-btn').textContent=`显示更多（还有 ${left} 部）`;
  $('pager').classList.toggle('deferred',left>0);
}
function showNoEnabledSources(){
  beginResults();renderFilters();$('results-title').textContent='暂无已启用的图源';$('results-meta').textContent='';
  showResults([],'暂无已启用的图源','图源启用状态位于「设置 → 图源管理」。');
}
async function loadDiscover(){
  const p=normalizeDiscover();if(!p){showNoEnabledSources();return;}
  const d=state.discover;
  saveLocal('comic:discover',d);syncHistory(false);
  $('results-title').textContent=BROWSE_ACTIONS[d.action];
  $('results-meta').textContent=p.name+(d.action==='random'?'':` · 第 ${d.page} 页`);
  renderFilters();
  const request=beginResults();
  try{
    const params=new URLSearchParams({page:d.page});
    if(d.sort)params.set('sort',d.sort);
    if(d.action==='leaderboard')params.set('mode',d.mode);
    if(d.name)params.set('name',d.name);
    const data=await api(`/api/${enc(d.source)}/${d.action}?`+params,{signal:state.browseAbort.signal});
    if(request!==state.browseRequest)return;
    const items=data.data||[];
    showResults(items,d.page>1?'已经到最后一页了':'此图源暂无内容',d.page>1?'':'换个分类或图源看看');
    loadResultFavorites(items,request);
    renderPager(items.length);
  }catch(e){if(request===state.browseRequest&&e.name!=='AbortError'){$('results').replaceChildren();showError($('browse-status'),e,loadDiscover);}}
}
async function loadSearch(){
  const s=state.search;if(!s)return;
  normalizeSearch();
  syncHistory(false);
  if(!activeSources().some(p=>p.capabilities.includes({tag:'tag_search',author:'author_search'}[s.mode]||'search'))){showNoEnabledSources();return;}
  const scope=s.source?sourceName(s.source):'全部图源';
  $('results-title').textContent=`${s.mode==='tag'?'标签 · ':s.mode==='author'?'作者 · ':''}“${s.keyword}”`;
  $('results-meta').textContent=`${scope} · 搜索中…`;
  renderFilters();
  const request=beginResults();
  try{
    const params=searchParams(s);
    const data=await api('/api/search?'+params,{signal:state.browseAbort.signal});
    if(request!==state.browseRequest)return;
    if(!s.source)state.searchCounts={key:searchKey(s),total:data.items.length,counts:Object.fromEntries(Object.entries(data.all_results||{}).map(([k,v])=>[k,v.length])),errors:data.errors||{}};
    renderFilters();
    showResults(data.items,s.page>1?'已经到最后一页了':'没有找到相关漫画','换个关键词，或切换到其他图源试试');
    if(state.recentPage&&(data.items.length||s.page===1))saveRecentPage(s);
    loadResultFavorites(data.items,request);
    $('results-meta').textContent=`${scope} · 第 ${s.page} 页 · ${data.items.length} 部`+(data.duplicate_count?` · 合并 ${data.duplicate_count} 项重复结果`:'');
    renderPager(data.raw_count??data.items.length);
    const errors=Object.entries(data.errors||{}),status=$('browse-status');
    if(errors.length){
      status.classList.add('warn');
      status.append(node('span','部分图源暂不可用：'+errors.map(([id,error])=>`${sourceName(id)}（${error.message}）`).join('；')));
      for(const [id,error] of errors)if(error.code==='login_required')status.append(bindButton(id));
    }
  }catch(e){if(request===state.browseRequest&&e.name!=='AbortError'){$('results').replaceChildren();$('results-meta').textContent=scope;showError($('browse-status'),e,loadSearch);}}
}
function startSearch(keyword,source='',filters={}){
  keyword=keyword.trim().slice(0,200);if(!keyword)return;
  if(state.tab!=='browse')showTab('browse');
  state.search={keyword,source,page:filters.page||1,sort:filters.sort||'',mode:filters.mode||state.searchMode||'keyword',tag_value:filters.tag_value||'',category:filters.category||'',deduplicate:filters.deduplicate??state.searchDeduplicate??false};
  normalizeSearch();
  $('keyword').value=keyword;syncClear();addRecent(state.search);renderRecent();
  return openResults();
}
function browse(event) {
  event.preventDefault();
  const keyword=$('keyword').value.trim();
  if(!keyword){$('keyword').focus();toast('请输入关键词');return;}
  if(matchMedia('(pointer:coarse)').matches)$('keyword').blur();
  // Keep the chosen source scope when refining a search from the results view.
  startSearch(keyword,state.search?.source||'',{mode:state.searchMode,category:state.search?.category||'',tag_value:keyword===state.search?.keyword?state.search.tag_value:''});
}
function turnPage(delta){
  const ctx=state.search||state.discover,page=ctx.page+delta;if(page<1)return;
  ctx.page=page;window.scrollTo({top:0,behavior:'smooth'});run(state.search?loadSearch:loadDiscover);
}
// Strip leading [group]/(tag) prefixes so a title search finds the same work on other sources.
function searchableTitle(title){
  const t=String(title||'').replace(/^(\s*[\[【(（][^\]】)）]*[\]】)）])+\s*/,'').trim();
  return (t||String(title||'')).slice(0,60);
}
function searchElsewhere(title){
  if($('reader-dialog').open)closeReader(true);
  ++state.detailRequest;if($('detail-dialog').open)$('detail-dialog').close();
  return startSearch(searchableTitle(title),'',{mode:'keyword'});
}

/* ---------- tabs & cards ---------- */
function showTab(tab,updateHistory=true,dismissSettings=true) {
  if(tab==='settings'){run(openSiteSettings);return;}
  if(dismissSettings)closeSiteSettings();
  if(!Object.hasOwn(TAB_LABELS,tab))tab='browse';
  if(['jm-favorites','bika-favorites'].includes(tab)){const p=sourcePlugin(tab.replace('-favorites',''));if(!p||p.enabled===false||!p.capabilities.includes('favorites'))tab='browse';}
  const changed=state.tab!==tab;state.tab=tab;
  for(const id of Object.keys(TAB_LABELS))$(id).hidden=id!==tab;
  document.querySelectorAll('[data-tab]').forEach(n=>{const on=n.dataset.tab===tab;n.classList.toggle('active',on);n.setAttribute('aria-current',on?'page':'false');});
  if(changed){window.scrollTo({top:0});if(updateHistory&&state.browseReady)syncHistory(true);}
  if(tab==='browse'&&state.view==='home')renderContinue();
  if(tab==='library')run(loadLibrary);
  if(tab==='downloads')run(loadTasks);
  if(tab==='jm-favorites')run(loadJmFavoritesView);
  if(tab==='bika-favorites')run(loadBikaFavoritesView);
}
// Result cards mark works already in the library or in a cloud favorites list; merged cards check every version.
const FAVORITE_LABELS={jm:'JM 收藏',bika:'哔咔收藏'};
function comicVersions(item){return item.variants?.length?item.variants:[item];}
function isFavorited(item){return state.favoriteIds[item.source]?.has(String(item.id))===true;}
function syncComicMarks(box,item){
  const versions=comicVersions(item),favorites=[...new Set(versions.filter(isFavorited).map(v=>FAVORITE_LABELS[v.source]))];
  const marks=[];
  if(versions.some(inLibrary))marks.push(['bookmark','已加入本地书库','library']);
  if(favorites.length)marks.push(['star',`已在${favorites.join('、')}中（按本地记录的收藏列表）`,'favorite']);
  box.replaceChildren(...marks.map(([icon,title,kind])=>{const mark=node('span',undefined,`cover-mark ${kind}`);mark.innerHTML=svgIcon(icon);mark.title=title;mark.setAttribute('role','img');mark.setAttribute('aria-label',title);return mark;}));
  box.hidden=!marks.length;
}
function refreshCardStates(){for(const el of document.querySelectorAll('.card'))el.syncState?.();}
// Favorite marks come from the locally recorded favorites tables only; failures leave the cards unmarked.
async function loadResultFavorites(items,request){
  const wanted={};
  for(const item of items)for(const v of comicVersions(item)){
    const p=sourcePlugin(v.source);
    if(!FAVORITE_LABELS[v.source]||p?.enabled===false||!(p?.capabilities||[]).includes('favorites')||!(p.account?.authenticated||p.account?.configured))continue;
    (wanted[v.source]??=new Set()).add(String(v.id));
  }
  await Promise.all(Object.entries(wanted).map(async([source,ids])=>{
    const list=[...ids];
    for(let i=0;i<list.length;i+=200){
      const chunk=list.slice(i,i+200);
      let data;try{data=await api(`/api/${enc(source)}/sync/favorite-ids?`+new URLSearchParams({ids:chunk.join(',')}),{cache:'no-store'});}catch{return;}
      const known=state.favoriteIds[source]??=new Set();
      for(const id of chunk)known.delete(id);
      for(const id of data.ids||[])known.add(String(id));
    }
  }));
  if(request===state.browseRequest)refreshCardStates();
}
function setFavoriteMark(source,id,favorited){
  const known=state.favoriteIds[source]??=new Set();
  if(favorited)known.add(String(id));else known.delete(String(id));
  refreshCardStates();
}
function card(item,{showSource=true,library=false,history=null,marks=false}={}){
  const el=node('article',undefined,'card'+(history?' card-history':'')+(library?' card-fixed card-library':''));
  // Only the play control resumes reading; the rest of the card always opens the detail page.
  const open=button('',()=>openComic(item),'card-open');open.title=item.title||'';
  const cover=coverBox(item),syncs=[];
  if(showSource&&item.source)cover.append(node('span',sourceName(item.source),'cover-source'));
  if(marks){const box=node('span',undefined,'cover-marks');syncs.push(()=>syncComicMarks(box,item));cover.append(box);}
  if(history){
    const bar=node('i',undefined,'cover-progress');bar.style.setProperty('--p',history.total?Math.min(1,(history.index+1)/history.total):0);
    cover.append(bar);
  }else{
    // Reading history loads independently of the cards, so the tag is refreshed once it arrives.
    const tag=node('span',undefined,'cover-tag');cover.append(tag);
    syncs.push(()=>{const last=readingLast(item);tag.textContent=last?.chapterName?`读至 ${last.chapterName}`:'';tag.hidden=!last?.chapterName;});
  }
  const info=node('div',undefined,'card-info');
  info.append(node('h3',item.title||'未命名'));
  if(history)info.append(node('p',`${history.chapterName||''} · ${history.index+1}/${history.total} 页`,'card-sub accent'));
  open.append(cover,info);el.append(open);
  if(history){
    // A sibling of the card button (buttons cannot nest), laid over the cover's centre.
    const zone=node('div',undefined,'cover-play-zone'),play=button('',()=>resumeComic(item),'cover-play');
    play.innerHTML=svgIcon('play');play.title='继续阅读';play.setAttribute('aria-label',`继续阅读《${item.title||'未命名'}》`);
    zone.append(play);el.append(zone);
  }
  if(!history)el.append(renderComicAuthors(item));
  if(library){
    // Library cards keep a fixed rhythm: two-line title, one author line, one meta line; actions live in a menu.
    const meta=node('p',undefined,'card-meta'),categories=bookCategories(item).join(' / ')||'未分类';
    const category=node('span',categories,'card-meta-category');category.title=categories;
    meta.append(category,node('span',`${item.chapters?.length||0} 章`,'card-meta-count'));el.append(meta);
  }
  if(item.variants?.length>1)el.append(renderComicVariants(item.variants));
  if(library)el.append(libraryActions(item,el));
  if(syncs.length){el.syncState=()=>syncs.forEach(sync=>sync());el.syncState();}
  return el;
}
function renderComicVariants(variants){
  const root=node('div',undefined,'comic-variants');root.setAttribute('aria-label','作品版本');
  root.append(node('span',`${variants.length} 个版本`));
  const counts=new Map(),seen=new Map();
  for(const item of variants)counts.set(item.source,(counts.get(item.source)||0)+1);
  for(const item of variants){
    const index=(seen.get(item.source)||0)+1;seen.set(item.source,index);
    const label=sourceName(item.source)+(counts.get(item.source)>1?` · ${index}`:'');
    const link=button(label,()=>openComic(item),'variant-link');
    link.title=`${sourceName(item.source)} · ${item.title||'未命名'}${item.page_count?` · ${item.page_count} 页`:''} · ID ${item.id}`;
    link.setAttribute('aria-label',link.title);root.append(link);
  }
  return root;
}
function libraryActions(item,cardEl){
  const more=node('button',undefined,'icon-btn card-more');more.type='button';more.title='更多操作';more.setAttribute('aria-label','更多操作');more.innerHTML=svgIcon('dots');more.onclick=()=>showMenu();more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');
  const actions=[
    {label:'检查更新',icon:svgIcon('refresh-cw'),run:async()=>{const result=await api(bookPath(item)+'/refresh',{method:'POST'});toast(result.new_chapters?`《${item.title}》新增 ${result.new_chapters} 章`:'暂无新章节');await loadLibrary();}},
    {label:'设置分类',icon:svgIcon('tag'),run:()=>editCategory(item)},
    {divider:true},
    {label:'移出书库',icon:svgIcon('trash-2'),danger:true,run:async()=>{if(!await ask({title:'移出书库',message:`确定将《${item.title}》移出书库吗？阅读进度会保留。`,ok:'移出',danger:true}))return;await api(bookPath(item),{method:'DELETE'});toast('已移出书库');await loadLibrary();}},
  ];
  const showMenu=point=>dropdown.open({anchor:more,point,owner:more,items:actions,align:'end',label:`《${item.title||'未命名'}》操作`,onPick:action=>{more.classList.add('busy');run(action.run).finally(()=>more.classList.remove('busy'));}});
  // Right-clicking anywhere on the card opens the same menu at the pointer.
  cardEl?.addEventListener('contextmenu',event=>{if(event.target.closest?.('.author-link'))return;event.preventDefault();showMenu({x:event.clientX,y:event.clientY});});
  return more;
}
async function editCategory(item){
  const category=await ask({title:'设置分类',message:`《${item.title}》 · 手动分类不影响收藏同步分类。`,input:true,value:item.library_category||'',placeholder:'留空清除手动分类',choices:state.categories});
  if(category===null)return;
  await api(bookPath(item),write('PATCH',{category}));toast(category?`手动分类已设为「${category}」`:'手动分类已清除');await loadLibrary();
}

/* ---------- library ---------- */
function bookCategories(item){return item.library_categories||[item.library_category].filter(Boolean);}
async function loadLibrary() {
  const data=await api('/api/library');state.books=data.items;state.categories=data.categories||[];
  options($('library-category'),[{value:'*',label:'全部分类'},{value:'',label:'未分类'},...state.categories.map(v=>({value:v,label:v}))],$('library-category').value);
  $('library-count').textContent=state.books.length?String(state.books.length):'';refreshCardStates();
  state.libraryMenu?.sync();
  filterBooks();
}
// Library cards keyed by book; unchanged cards stay mounted so covers and entrance animations do not replay.
function bookSignature(b){return [b.title,coverKey(coverFor(b)),b.author,b.authors,b.library_category,bookCategories(b),b.chapters?.length||0,(b.variants||[]).map(v=>[v.source,v.id,v.title,v.page_count]),readingLast(b)?.chapterName||''];}
function filterBooks(){
  const category=$('library-category').value,sort=$('library-sort').value,needle=($('library-filter').value||'').trim().toLowerCase(),box=$('books');
  if(!state.books.length){box.keyedCards=null;box.replaceChildren(emptyState('书库为空','漫画详情中的「加入书库」用于收藏到本地书库，支持追更和继续阅读。',labelBtn('compass','发现漫画',()=>showTab('browse'),'primary'),'bookmark'));return;}
  const lastRead=new Map(readingItems().map(h=>[readingKey(h),h.updated_at||0]));
  const items=state.books.filter(b=>(category==='*'||(category?bookCategories(b).includes(category):!bookCategories(b).length))&&(!needle||(b.title||'').toLowerCase().includes(needle)||(b.author||'').toLowerCase().includes(needle)));
  if(sort==='read')items.sort((a,b)=>(lastRead.get(b.source+':'+b.id)||0)-(lastRead.get(a.source+':'+a.id)||0));
  else if(sort==='title')items.sort((a,b)=>String(a.title||'').localeCompare(String(b.title||''),'zh'));
  if(!items.length){box.keyedCards=null;box.replaceChildren(emptyState('没有符合条件的漫画','试试其他分类或关键词',undefined,'search'));return;}
  renderKeyed(box,items,readingKey,bookSignature,b=>card(b,{library:true}));
}
const inLibrary=d=>state.books.some(b=>b.source===d.source&&String(b.id)===String(d.id));

/* ---------- detail ---------- */
function detailHero(item,loading){
  const hero=node('div',undefined,'detail-hero'+(loading?' loading':''));
  const ambient=node('div',undefined,'detail-ambient'),backdrop=node('div',undefined,'detail-backdrop'),url=coverFor(item);
  if(url)backdrop.style.backgroundImage=`url("${url.replace(/"/g,'%22')}")`;
  ambient.setAttribute('aria-hidden','true');ambient.append(backdrop);
  const text=node('div',undefined,'detail-text');
  text.append(node('h2',item.title||'未命名','detail-name'));
  hero.append(ambient,coverBox(item,'detail-cover'),text);
  if(loading)text.append(node('i',undefined,'line'),node('i',undefined,'line short'),node('i',undefined,'line'));
  return {hero,text};
}
async function openComic(item) {
  if(sourcePlugin(item.source)?.enabled===false){toast('图源已关闭，重新启用入口位于「设置 → 图源管理」');return;}
  chapterPreviews.stop();
  const request=++state.detailRequest,dlg=$('detail-dialog');state.detail=null;state.detailItem=item;
  $('detail-title').textContent=item.title||'漫画详情';
  $('detail-body').replaceChildren(detailHero(item,true).hero);
  if(!dlg.open)dlg.showModal();$('detail-scroll').scrollTop=0;syncDetailHead();
  try{
    const [detail,reading]=await Promise.all([api(`/api/comic/${enc(item.source)}/${enc(item.id)}`),fetchComicReading(item)]);if(request!==state.detailRequest)return;
    state.detail={...detail,cover:detail.cover||item.cover,category:detail.category||item.category,reading:{comic:reading.comic||null,chapters:reading.chapters||{}}};renderDetail();
  }catch(e){
    if(request!==state.detailRequest)return;
    if(item.chapters){state.detail=item;renderDetail();$('detail-body').querySelector('.detail-hero')?.after(errorPanel('图源暂不可用，以下为书库缓存',e,item,true));}
    else $('detail-body').replaceChildren(detailHero(item,false).hero,errorPanel('无法加载漫画详情',e,item));
  }
}
function errorPanel(title,e,item,compact){
  const box=node('div',undefined,'error-panel'+(compact?' compact':''));const icon=node('span',undefined,'error-icon');icon.innerHTML=svgIcon('alert');
  const text=node('div',undefined,'error-text');text.append(node('strong',title),node('p',e.message));
  const actions=node('div',undefined,'error-actions');
  if(!compact)actions.append(labelBtn('refresh-cw','重试',()=>openComic(item),'small'));
  if(e.code==='login_required'&&e.source)actions.append(bindButton(e.source));
  actions.append(labelBtn('search','在其他图源搜索',()=>searchElsewhere(item.title),'small'));
  box.append(icon,text,actions);return box;
}
const CHAPTER_PAGE=60;
const chapterPreviews=new ChapterPreviews({
  load:async(detail,chapter)=>{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
    try{const data=await api(`/api/chapter/${enc(detail.source)}/${enc(detail.id)}/${enc(chapter.id)}`,{signal:controller.signal});return data.images?.[0]||null;}
    finally{clearTimeout(timer);}
  },
  render:renderChapterPreview,
});
function chapterPreviewVisible(box){return state.chapterPreviews&&box.isConnected&&$('detail-dialog').open&&!$('reader-dialog').open;}
function renderChapterPreview(box,url,detail,chapter){
  if(!chapterPreviewVisible(box))return;
  const fallback=detail.cover?coverFor(detail):'';
  const show=(src,isFallback)=>{
    box.querySelector('img')?.remove();box.classList.remove('has-image');
    if(!src||!safeImage(src)){box.title='章节首图不可用';return;}
    box.title=isFallback?'章节首图不可用，显示漫画封面':'章节首图';
    const img=image(src,'');
    img.onload=()=>{if(chapterPreviewVisible(box)){img.classList.add('ready');box.classList.add('has-image');}};
    img.onerror=()=>{
      img.removeAttribute('src');
      if(!chapterPreviewVisible(box))return;
      if(!isFallback){chapterPreviews.failed(detail,chapter);show(fallback,true);}
      else{img.remove();box.title='章节首图不可用';}
    };
    box.append(img);
  };
  show(url||fallback,!url);
}
function syncChapterPreviews(){
  const control=$('chapter-previews-toggle');if(!control)return;
  control.setAttribute('aria-checked',String(state.chapterPreviews));
  control.innerHTML=svgIcon(state.chapterPreviews?'toggle-right':'toggle-left');
  control.append(node('span','章节封面'));
}
function toggleChapterPreviews(){
  state.chapterPreviews=!state.chapterPreviews;saveLocal('comic:chapter-previews',state.chapterPreviews);
  syncChapterPreviews();drawChapters(true,true);
}
function chapterRow(detail,chapter,lastId){
  const id=String(chapter.id),progress=chapterProgress(detail,id);
  const row=node('div',undefined,'chapter'+(id===lastId?' current':progress?' read':''));row.tabIndex=0;row.setAttribute('role','button');
  if(state.chapterPreviews){
    const preview=node('span',undefined,'chapter-preview');preview.setAttribute('aria-hidden','true');preview.title='章节首图按需加载';preview.innerHTML=svgIcon('image');
    row.append(preview);chapterPreviews.watch(preview,detail,chapter);
  }
  const label=node('div',undefined,'chapter-name');label.append(node('span',chapter.name||`第 ${chapter.order||''} 话`));
  if(id===lastId)label.append(node('small',`上次读到第 ${(progress?.index??0)+1} 页`));
  else if(progress)label.append(node('small',`读到第 ${progress.index+1} 页`));
  row.append(label,iconBtn('download','下载 PDF',()=>queueDownload(detail,chapter),'ghost'));
  row.onclick=event=>{if(event.target.closest('button,a'))return;openReader(id);};
  row.onkeydown=event=>{if((event.key==='Enter'||event.key===' ')&&event.target===row){event.preventDefault();openReader(id);}};
  return row;
}
// Chapters render in chunks so books with hundreds of episodes stay responsive.
function drawChapters(reset,keepShown=false){
  const detail=state.detail,list=$('chapter-list');if(!detail||!list)return;
  if(reset){
    if(state.chapterPreviews&&$('detail-dialog').open&&!$('reader-dialog').open)chapterPreviews.start($('detail-dialog'));
    else chapterPreviews.stop();
    state.chapterView={shown:keepShown?state.chapterView?.shown||CHAPTER_PAGE:CHAPTER_PAGE,needle:($('chapter-filter')?.value||'').trim().toLowerCase()};list.replaceChildren();
  }
  const view=state.chapterView,lastId=readingLast(detail)?.chapterId;
  const matched=(detail.chapters||[]).filter(c=>!view.needle||(c.name||'').toLowerCase().includes(view.needle));
  if(state.chapterDesc)matched.reverse();
  list.querySelector('.more-row')?.remove();
  for(const chapter of matched.slice(list.children.length,view.shown))list.append(chapterRow(detail,chapter,lastId));
  if(!matched.length)list.append(node('p',detail.chapters?.length?'没有匹配的章节':'暂无章节','empty'));
  else if(matched.length>list.children.length){const more=node('div',undefined,'more-row');more.append(button(`显示更多（还有 ${matched.length-list.children.length} 章）`,()=>{view.shown+=CHAPTER_PAGE;drawChapters();}));list.append(more);}
}
function syncChapterSort(){const b=$('chapter-sort');if(!b)return;b.innerHTML=svgIcon('sort');b.append(node('span',state.chapterDesc?'倒序':'正序'));b.title=state.chapterDesc?'切换为正序':'切换为倒序';}
function renderDetail() {
  const detail=state.detail,body=$('detail-body');detail.chapters=detail.chapters||[];$('detail-title').textContent=detail.title;body.replaceChildren();
  const {hero,text}=detailHero(detail,false);
  const meta=node('div',undefined,'detail-meta');
  meta.append(node('span',sourceName(detail.source),'badge accent'));
  const a=node('span',undefined,'meta-item');a.innerHTML=svgIcon('user');a.append(renderComicAuthors(detail));meta.append(a);
  const n=node('span',undefined,'meta-item');n.innerHTML=svgIcon('list');n.append(node('span',`共 ${detail.chapters.length} 章`));meta.append(n);
  localStatusMeta(detail,meta);
  text.append(meta);
  text.append(renderComicLabels(detail));
  const desc=node('p',detail.description||'暂无简介','detail-desc');text.append(desc);
  const controls=node('div',undefined,'detail-actions');
  const last=readingLast(detail);
  if(last&&detail.chapters.some(c=>String(c.id)===last.chapterId))controls.append(labelBtn('book-open',`继续阅读 · ${last.chapterName}`,()=>openReader(last.chapterId),'primary'));
  else if(detail.chapters.length)controls.append(labelBtn('play','开始阅读',()=>openReader(String(detail.chapters[0].id)),'primary'));
  const fav=button('',()=>toggleFavorite(detail).then(syncFav));
  const syncFav=()=>{const saved=inLibrary(detail);fav.innerHTML=svgIcon(saved?'check':'star');fav.append(node('span',saved?'已在书库':'加入书库'));fav.classList.toggle('on',saved);fav.title=saved?'移出书库':'加入书库';};
  syncFav();controls.append(fav);
  const cloudFav=cloudFavoriteButton(detail);if(cloudFav)controls.append(cloudFav);
  if(detail.chapters.length)controls.append(labelBtn('download','下载全部',()=>downloadAll(detail)));
  controls.append(iconBtn('search','在其他图源搜索同名漫画',()=>searchElsewhere(detail.title)));
  text.append(controls);
  const section=node('section',undefined,'chapters'),head=node('div',undefined,'chapters-head'),tools=node('div',undefined,'chapters-tools');
  head.append(node('h3','章节'),node('span',String(detail.chapters.length),'count'),tools);
  if(detail.chapters.length){const preview=button('',toggleChapterPreviews,'small chapter-previews-toggle');preview.id='chapter-previews-toggle';preview.setAttribute('role','switch');preview.setAttribute('aria-label','章节封面');preview.title='使用章节首图，按可见范围加载；设置仅保存在当前浏览器。';tools.append(preview);}
  if(detail.chapters.length>15){const box=node('div',undefined,'input-icon chapter-search'),filter=node('input');box.innerHTML=svgIcon('search');filter.id='chapter-filter';filter.type='search';filter.placeholder='筛选章节';filter.maxLength=50;filter.setAttribute('aria-label','筛选章节');filter.oninput=()=>drawChapters(true);box.append(filter);tools.append(box);}
  if(detail.chapters.length>1){const sort=node('button',undefined,'small chapter-sort');sort.id='chapter-sort';sort.type='button';sort.onclick=()=>{state.chapterDesc=!state.chapterDesc;saveLocal('comic:chapter-desc',state.chapterDesc);syncChapterSort();drawChapters(true);};tools.append(sort);}
  const list=node('div',undefined,'chapter-list');list.id='chapter-list';section.append(head,list);
  body.append(hero,section);syncChapterSort();syncChapterPreviews();drawChapters(true);
  // Long synopses collapse to a few lines with an explicit toggle.
  requestAnimationFrame(()=>{if(desc.scrollHeight>desc.clientHeight+2){const more=node('button','展开简介','link-btn');more.type='button';more.onclick=()=>{const open=desc.classList.toggle('open');more.textContent=open?'收起简介':'展开简介';};desc.after(more);}});
}
// Favorites-sync sources show the last local check or sync result for the configured save directory.
async function localStatusMeta(detail,meta){
  if(!['jm','bika'].includes(detail.source))return;
  let data;
  try{data=await api(`/api/${enc(detail.source)}/sync/local-status?`+new URLSearchParams({ids:String(detail.id)}),{cache:'no-store'});}catch{return;}
  const local=data.items?.[String(detail.id)];
  if(!local||state.detail!==detail)return;
  const labels={downloaded:['check','已下载到本地','ok'],error:['alert','本地文件异常','danger'],missing:['download','本地未下载','']}[local.status];
  if(!labels)return;
  const item=node('span',undefined,`meta-item local-state ${labels[2]}`);item.innerHTML=svgIcon(labels[0]);
  item.append(node('span',local.status==='downloaded'?`${labels[1]} · ${local.images} 张`:labels[1]));
  item.dataset.tipText=local.status==='error'?`${local.message||'原因未记录'}\n目录：${local.path||data.root}`:local.status==='downloaded'?`目录：${local.path}`:`保存目录：${data.root}`;
  meta.append(item);
  if(local.status==='error'&&local.path&&typeof repairFavLocal==='function'){
    const fix=button('修复',()=>repairFavLocal(detail.source,[String(detail.id)]),'link-btn local-repair');
    fix.setAttribute('aria-label',`修复《${detail.title}》的本地文件`);meta.append(fix);
  }
}
async function toggleFavorite(detail){
  if(inLibrary(detail)){
    if(!await ask({title:'移出书库',message:`确定将《${detail.title}》移出书库吗？`,ok:'移出',danger:true}))return;
    await api(bookPath(detail),{method:'DELETE'});await loadLibrary();toast('已移出书库');return;
  }
  await api(bookPath(detail),write('PUT',{category:''}));await loadLibrary();
  const book=state.books.find(b=>b.source===detail.source&&String(b.id)===String(detail.id));
  toast('已加入书库',{action:{label:'设置分类',run:()=>book&&run(()=>editCategory(book))}});
}
function cloudFavoriteButton(detail){
  const source=detail.source,label={jm:'JM',bika:'哔咔'}[source],plugin=sourcePlugin(source);
  if(!label||plugin?.enabled===false||!plugin?.capabilities.includes('add_favorite'))return null;
  const space=source==='jm'?' ':'',name=label+space+'收藏',account=label+space+'账号';
  if(!(plugin.account?.authenticated||plugin.account?.configured)){const bind=bindButton(source);bind.title=`加入${space}${name}需要绑定账号`;return bind;}
  let confirmed=null,loaded=false;
  const path=`/api/${enc(source)}/favorites/${enc(detail.id)}`;
  const sync=busy=>{control.innerHTML=svgIcon(confirmed?'check':'star');control.append(node('span',busy?(typeof busy==='string'?busy:`读取${space}${name}…`):!loaded?'收藏状态未确认':confirmed?'已收藏':`加入${space}${name}`));control.classList.toggle('on',confirmed===true);control.title=loaded&&confirmed===null?'本地收藏表尚无此漫画的状态，添加前实时检查远端':confirmed?`再次点击从${space}${name}中取消；${account}收藏与本地书库独立`:`${account}收藏与本地书库独立；状态读取失败时点击重试`;};
  const report=error=>toast(error.message,{tone:'error',...(error.code==='login_required'?{action:{label:`绑定${space}${account}`,run:()=>bindButton(source).onclick()}}:{})});
  const control=button('',async()=>{
    if(sourcePlugin(source)?.enabled===false){report(new Error('图源已关闭，重新启用入口位于「设置 → 图源管理」'));return;}
    if(!loaded){await loadStatus();return;}
    const remove=confirmed===true;
    if(remove&&!await ask({title:'取消收藏',message:`确定将《${detail.title}》从${space}${name}中移除吗？`,ok:'取消收藏',danger:true}))return;
    sync(remove?'正在取消收藏…':`正在加入${space}${name}…`);
    try{
      const data=await api(path,{method:remove?'DELETE':'POST'});
      if(!data.success||data.favorited!==!remove)throw new Error(`${source==='jm'?'禁漫':'哔咔'}未确认${remove?'取消':'加入'}收藏`);
      confirmed=!remove;loaded=true;setFavoriteMark(source,detail.id,confirmed);
      toast(remove?(data.already_favorited?`已从${space}${name}中取消`:`漫画不在${space}${name}中`):(data.already_favorited?`漫画已在${space}${name}中`:`已加入${space}${name}`));
      if(state.tab===`${source}-favorites`)await (source==='jm'?loadJmFavorites():loadBikaFavorites()).catch(report);
    }catch(error){confirmed=null;loaded=false;report(error);}
    finally{sync(false);}
  });
  const loadStatus=async()=>{
    control.disabled=true;sync(true);
    try{
      const data=await api(path,{cache:'no-store'});
      if(data.favorited!==null&&typeof data.favorited!=='boolean')throw new Error('本地收藏表未返回有效状态');
      confirmed=data.favorited;loaded=true;if(confirmed!==null)setFavoriteMark(source,detail.id,confirmed);
    }catch(error){confirmed=null;loaded=false;report(error);}
    finally{control.disabled=false;sync(false);}
  };
  loadStatus();return control;
}
function syncDetailHead(){$('detail-dialog').querySelector('.dialog-head').classList.toggle('scrolled',$('detail-scroll').scrollTop>150);}
const taskPayload=(detail,chapter)=>write('POST',{source:detail.source,comic_id:String(detail.id),chapter_id:String(chapter.id),title:detail.title,chapter:chapter.name});
async function queueDownload(detail,chapter){await api('/api/downloads',taskPayload(detail,chapter));toast(`已加入下载：${chapter.name}`,{action:{label:'查看',run:openDownloads}});refreshTaskDot();}
async function downloadAll(detail){
  if(!await ask({title:'下载全部章节',message:`将《${detail.title}》全部 ${detail.chapters.length} 章加入下载队列？每章会单独打包为加密 PDF。`,ok:'开始下载'}))return;
  let queued=0,failed=0;
  for(const chapter of detail.chapters){try{await api('/api/downloads',taskPayload(detail,chapter));queued++;}catch{failed++;}}
  toast(`已加入 ${queued} 个任务${failed?`，${failed} 个失败（队列可能已满）`:''}`,{action:{label:'查看',run:openDownloads}});refreshTaskDot();
}
function openDownloads(){for(const id of ['reader-dialog','detail-dialog'])if($(id).open)id==='reader-dialog'?closeReader(true):$(id).close();showTab('downloads');}

/* ---------- downloads ---------- */
async function loadTasks(){
  const {tasks}=await api('/api/downloads');
  const active=tasks.filter(t=>['queued','running','cancelling'].includes(t.status)).length,done=tasks.filter(t=>t.status==='completed').length;
  state.activeTasks=active;document.querySelectorAll('[data-downloads-dot]').forEach(d=>d.hidden=!active);
  $('tasks-summary').textContent=tasks.length?`${active} 进行中 · ${done} 已完成 · 共 ${tasks.length}`:'';
  const sig=JSON.stringify(tasks),box=$('tasks');
  if(sig===state.taskSig&&box.children.length)return;state.taskSig=sig;
  if(!tasks.length){box.replaceChildren(emptyState('还没有下载任务','在漫画详情的章节列表中点击下载图标，即可把章节打包为 PDF。',labelBtn('compass','去发现漫画',()=>showTab('browse'),'primary'),'download'));return;}
  box.replaceChildren(...tasks.map(taskCard));
}
function refreshTaskDot(){loadTasks().catch(()=>{});}
function taskCard(task){
  const tone=TONES[task.status]||'info',card=node('article',undefined,'task '+tone);
  const head=node('div',undefined,'task-head'),titles=node('div',undefined,'task-titles');
  titles.append(node('h3',task.title||task.comic_id),node('p',task.chapter||task.chapter_id,'task-chapter'));
  head.append(titles,node('span',statuses[task.status]||task.status,'status-pill '+tone));
  const pct=task.status==='completed'?100:task.total?Math.round(task.completed/task.total*100):0;
  const bar=node('div',undefined,'bar'),fill=node('i');fill.style.width=pct+'%';bar.append(fill);
  const meta=node('p',undefined,'task-meta');
  meta.append(node('span',sourceName(task.source)));
  if(task.status==='running'&&statuses[task.stage])meta.append(node('span',statuses[task.stage]));
  meta.append(node('span',`${task.completed}/${task.total||'?'} 张 · ${pct}%`),node('span',timeAgo(task.updated_at||task.created_at)));
  card.append(head,meta,bar);
  if(task.error)card.append(node('p',task.error,'error'));
  const controls=node('div',undefined,'task-actions');
  if(task.status==='completed'){
    const link=node('a',undefined,'button primary small');link.href=`/api/downloads/${task.id}/file`;link.innerHTML=svgIcon('download');link.append(node('span','下载 PDF'));
    const pwd=node('span',undefined,'password');pwd.append(node('span','密码'),node('code',task.password));
    controls.append(link,pwd,iconBtn('copy','复制密码',async()=>{await copyText(task.password);toast('密码已复制');},'ghost'));
  }
  if(['queued','running'].includes(task.status))controls.append(labelBtn('x','取消',async()=>{await api(`/api/downloads/${task.id}/cancel`,{method:'POST'});await loadTasks();},'small'));
  if(['failed','cancelled','interrupted'].includes(task.status))controls.append(labelBtn('refresh-cw','重新下载',async()=>{await api(`/api/downloads/${task.id}/retry`,{method:'POST'});await loadTasks();},'small'));
  if(['failed','cancelled','completed','interrupted'].includes(task.status))controls.append(iconBtn('trash-2','删除任务及文件',async()=>{if(!await ask({title:'删除任务',message:'删除这条任务及服务器上的 PDF 文件？',ok:'删除',danger:true}))return;await api(`/api/downloads/${task.id}`,{method:'DELETE'});await loadTasks();},'ghost push'));
  card.append(controls);return card;
}

/* ---------- source management ---------- */
function accountLabel(plugin){return !plugin.capabilities.includes('login')?'无需登录':plugin.account.authenticated?'已登录':plugin.account.configured?'凭证已保存':'未绑定账号';}
async function setSourceEnabled(id,enabled){
  const data=await api(`/api/sources/${enc(id)}/state`,write('PUT',{enabled}));
  if(data.source?.id!==id||data.source.enabled!==enabled)throw new Error('图源未返回有效开关状态');
  state.sourceRequest=(state.sourceRequest||0)+1;
  state.sources=state.sources.map(p=>p.id===id?data.source:p);
  delete state.health[id];renderSourceList();
  if(state.settingsSource===id)renderSourceSettings();
  refreshSourceViews();
  toast(`${sourceName(id)}已${enabled?'启用':'关闭'}`);
}
function sourceToggle(plugin){
  const enabled=plugin.enabled!==false;
  const control=labelBtn(enabled?'toggle-right':'toggle-left',enabled?'已启用':'已关闭',()=>setSourceEnabled(plugin.id,!enabled),'small source-toggle'+(enabled?' on':''));
  control.setAttribute('role','switch');control.setAttribute('aria-checked',String(enabled));control.setAttribute('aria-label',`${plugin.name}启用状态`);
  control.title='关闭后停止使用该图源并取消其下载；账号、书库和已完成文件保留。收藏同步到书库与 JM 图片定时同步暂停，重新启用后按原计划恢复。';
  return control;
}
function renderSourceList(){
  const list=$('source-list');list.replaceChildren();state.healthDots={};
  for(const plugin of state.sources){
    const row=node('article',undefined,'source-row'),info=node('div',undefined,'source-info');
    const h3=node('h3');const dot=node('span',undefined,'health-dot');syncHealthDot(plugin.id,dot);h3.append(dot,node('span',plugin.name,'source-name'));
    const account=node('span',accountLabel(plugin),'account-state'+(needsLogin(plugin)?' warn':''));h3.append(account);
    const caps=node('p',plugin.capabilities.filter(c=>CAP_LABELS[c]&&c!=='login').map(c=>CAP_LABELS[c]).join(' · '),'caps');
    info.append(h3,caps);
    const actions=node('div',undefined,'source-actions');actions.append(sourceToggle(plugin),iconBtn('settings',`${plugin.name}设置`,()=>{state.settingsSource=plugin.id;renderSourceSettings();$('source-settings-dialog').showModal();},'ghost'));
    row.classList.toggle('source-off',plugin.enabled===false);row.append(info,actions);list.append(row);
  }
}
function syncHealthDot(id,dot){
  dot.dataset.source=id;state.healthDots[id]=dot;
  const disabled=sourcePlugin(id)?.enabled===false,status=disabled?'disabled':state.health[id];
  dot.classList.toggle('ok',status==='ok');dot.classList.toggle('down',status==='down');dot.classList.toggle('checking',status==='checking');
  dot.title=disabled?'图源已关闭':status==='ok'?'图源连通':status==='down'?'图源不可用':status==='checking'?'正在检测…':'未检测';
}
// Probe each source with its lightest listing; results land on the row dots.
async function checkSourceHealth(id){
  if(sourcePlugin(id)?.enabled===false)return;
  state.healthChecks=state.healthChecks||{};state.healthChecks[id]?.abort();
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);state.healthChecks[id]=controller;
  state.health[id]='checking';if(state.healthDots[id])syncHealthDot(id,state.healthDots[id]);
  let status='down';
  try{
    const caps=sourcePlugin(id)?.capabilities||[];
    const probe=caps.includes('latest')?`/api/${enc(id)}/latest?page=1`:`/api/search?${new URLSearchParams({keyword:' ',source:id})}`;
    await api(probe,{signal:controller.signal});status='ok';
  }catch{}finally{clearTimeout(timer);}
  if(state.healthChecks[id]!==controller||sourcePlugin(id)?.enabled===false)return;
  delete state.healthChecks[id];state.health[id]=status;
  if(state.healthDots[id])syncHealthDot(id,state.healthDots[id]);
}
function runHealthChecks(force){for(const plugin of activeSources())if(state.health[plugin.id]!=='checking'&&(force||!state.health[plugin.id]))checkSourceHealth(plugin.id);}
function renderSourceSettings(){
  const plugin=state.sources.find(s=>s.id===state.settingsSource),body=$('source-settings-body');body.replaceChildren();if(!plugin)return;
  $('source-settings-title').textContent=plugin.name+' · 设置';
  // A navigation row opens a nested page; it reads as a single tappable line inside a grouped card.
  const linkRow=(icon,title,desc,action)=>{const row=button('',action,'link-row');row.innerHTML=svgIcon(icon);const text=node('span',undefined,'row-text');text.append(node('strong',title),node('span',desc));row.append(text);row.insertAdjacentHTML('beforeend',svgIcon('chevron-right'));return row;};
  const section=(title,desc,className)=>{const box=node('section',undefined,'setting-section'+(className?' '+className:''));box.append(node('h3',title));if(desc)box.append(node('p',desc,'muted'));body.append(box);return box;};
  if(plugin.enabled===false)body.append(node('p','图源已关闭；服务器设置和已保存凭据保留。登录、浏览与下载需先在「设置 → 图源管理」中启用。','callout subtle'));
  for(const field of plugin.settings_fields||[]){const box=section(field.label,field.description||'');
    const form=node('form',undefined,'settings-form inline-form'),label=node('label');let input;
    if(field.type==='select'&&Array.isArray(field.options)){input=node('select');input.name=field.name;
      for(const option of field.options){const opt=node('option',option.label??option.value);opt.value=option.value;input.append(opt);}
      input.value=plugin.settings?.[field.name]??field.options[0]?.value??'';
      if(![...input.options].some(o=>o.value===input.value))input.selectedIndex=0;
    }else{input=node('input');input.name=field.name;input.type=field.type==='url'?'url':'text';input.value=plugin.settings?.[field.name]||'';input.placeholder='留空使用部署默认值';}
    input.setAttribute('aria-label',field.label);label.append(input);if(input.tagName==='SELECT'&&typeof dropdown!=='undefined')dropdown.enhance(input);
    const submit=node('button','保存','primary');submit.type='submit';form.append(label,submit);form.onsubmit=event=>{event.preventDefault();run(async()=>{await api(`/api/sources/${enc(plugin.id)}/settings`,write('PUT',{...plugin.settings,[field.name]:input.value}));await loadSources();toast('设置已保存，后续请求立即生效');},submit);};box.append(form);}
  const links=node('div',undefined,'group');
  if(plugin.capabilities.includes('login'))links.append(linkRow('key','登录凭证',accountLabel(plugin),()=>{configureAccount();$('credentials-dialog').showModal();}));
  if(plugin.id==='jm'&&plugin.enabled!==false&&plugin.capabilities.includes('favorites'))links.append(linkRow('bookmark','收藏与本地同步','浏览远端收藏夹，按目录规则增量保存图片。',()=>{$('source-settings-dialog').close();showTab('jm-favorites');}));
  if(plugin.id==='bika'&&plugin.enabled!==false&&plugin.capabilities.includes('favorites'))links.append(linkRow('bookmark','收藏与本地同步','浏览账号的全部云端收藏，按目录规则增量保存图片；可单独开启收藏同步到书库。',()=>{$('source-settings-dialog').close();showTab('bika-favorites');}));
  if(links.children.length)section('账号与收藏').append(links);
  if(['jm','bika'].includes(plugin.id)&&plugin.capabilities.includes('favorites'))body.append(favoriteLibrarySettings(plugin));
  if(!(plugin.settings_fields||[]).length&&!plugin.capabilities.includes('login'))body.append(node('p','此图源使用自动服务器配置，无需登录，暂无可修改选项。','callout subtle'));
}
function favoriteLibrarySettings(plugin){
  const section=node('section',undefined,'setting-section library-sync-settings'),content=node('div');
  const path=`/api/sources/${enc(plugin.id)}/library-sync`;let busy=false;
  section.append(node('h3','收藏同步到书库'),node('p','默认关闭，仅同步书目信息，不下载图片、不修改云收藏。手动分类保留，关闭同步或取消云收藏不删除已入库漫画。','muted'),content);
  function render(data){
    const {settings,category,current,active,paused}=data;
    const toggle=labelBtn(settings.enabled?'toggle-right':'toggle-left',settings.enabled?'自动同步已开启':'自动同步已关闭',async()=>{
      if(busy)return;busy=true;
      try{render(await api(path,write('PUT',{...settings,enabled:!settings.enabled})));toast('书库同步设置已保存');await loadLibrary();}finally{busy=false;}
    },'small source-toggle switch-only'+(settings.enabled?' on':''));
    toggle.setAttribute('role','switch');toggle.setAttribute('aria-checked',String(settings.enabled));toggle.setAttribute('aria-label',`${plugin.name}收藏自动同步到书库`);
    toggle.disabled=!settings.enabled&&(plugin.enabled===false||!(plugin.account.authenticated||plugin.account.configured));
    const actions=node('div',undefined,'form-actions'),sync=labelBtn('refresh-cw','立即同步',async()=>{
      if(busy)return;busy=true;
      try{render(await api(path+'/run',{method:'POST'}));}finally{busy=false;}
    },'small');sync.disabled=!settings.enabled||active||paused;
    actions.append(sync,labelBtn('refresh-cw','刷新状态',async()=>{await refresh();await loadLibrary();},'small'));
    const labels={idle:'等待同步',running:'同步中',completed:'同步完成',partial:'部分完成',failed:'同步失败',stopped:'同步已停止',interrupted:'同步已中断'};
    const summary=active?'同步中':labels[current.status]||'等待同步';
    const head=node('div',undefined,'group-row');const text=node('span',undefined,'row-text');
    text.append(node('strong','自动同步'),node('span',`开启后创建「${category}」分类并立即同步全部收藏，之后每 ${settings.interval_minutes} 分钟增补。服务运行时自动执行，无需保持页面打开。`));
    head.append(text,toggle);
    const group=node('div',undefined,'group');group.append(head);
    const status=node('div',undefined,'sync-status');
    if(paused)status.append(node('p','图源已关闭，自动同步暂停。','muted'));
    else if(!(plugin.account.authenticated||plugin.account.configured))status.append(node('p','绑定账号后可开启自动同步。','muted'));
    if(current.status!=='idle')status.append(node('p',`${summary} · 已处理 ${current.processed||0}/${current.total||0} 本 · 新增 ${current.added||0} 本 · 失败 ${current.failed||0} 本`,'muted'));
    if(data.next_run_at)status.append(node('p',`下次同步：${new Date(data.next_run_at*1000).toLocaleString('zh-CN')}`,'muted'));
    if(current.error)status.append(node('p',current.error,'error'));
    for(const error of current.errors||[])status.append(node('p',`${error.comic_id}：${error.message}`,'error'));
    if(status.children.length){const row=node('div',undefined,'group-row stacked');row.append(status);group.append(row);}
    content.replaceChildren(group,actions);
  }
  async function refresh(){
    if(busy)return;busy=true;
    try{render(await api(path,{cache:'no-store'}));}
    catch(e){content.replaceChildren(node('p',e.message,'error'),labelBtn('refresh-cw','重新读取',refresh,'small'));}
    finally{busy=false;}
  }
  content.append(node('p','正在读取同步设置…','muted'));refresh();return section;
}
function configureAccount(){
  const plugin=state.sources.find(s=>s.id===state.settingsSource),form=$('login-form');form.replaceChildren();if(!plugin)return;
  $('credentials-title').textContent=plugin.name+' · 登录凭证';
  if(!plugin.capabilities.includes('login')){$('account-state').textContent='此图源插件不提供登录功能。';return;}
  $('account-state').textContent=plugin.account.authenticated?'会话已保存，可直接使用。':plugin.account.configured?'账号已保存，将在请求时自动恢复会话。':'尚未绑定账号。凭证仅加密保存在本服务器。';
  if(plugin.login_hint)form.append(node('p',plugin.login_hint,'field-hint'));
  for(const field of plugin.login_fields){const label=node('label',field.label);const input=node('input');input.name=field.name;input.type=field.type==='password'?'password':'text';input.autocomplete=field.type==='password'?'current-password':'username';input.required=true;label.append(input);form.append(label);}
  const actions=node('div',undefined,'form-actions');
  const submit=node('button','登录并保存','primary');submit.type='submit';submit.disabled=plugin.enabled===false;actions.append(submit);
  if(plugin.enabled===false)$('account-state').textContent='图源已关闭，登录需先启用；已保存凭据仍可解除绑定。';
  if(plugin.account.configured||plugin.account.authenticated)actions.append(labelBtn('unlink','解除绑定',async()=>{if(!await ask({title:'解除绑定',message:`删除已保存的${plugin.name}账号与会话？`,ok:'解除',danger:true}))return;await api(`/api/sources/${enc(plugin.id)}/account`,{method:'DELETE'});await loadSources();configureAccount();if(plugin.id==='bika')refreshBikaAccountViews();toast('已解除绑定');}));
  form.append(actions);
}

/* ---------- reader ---------- */
// Opening the reader alone leaves history untouched; recording starts with the first page turn of the session.
function saveProgress(){const r=state.reader;if(!r||!r.images.length||!state.readerTurned)return;recordReading(r);}
function markPageTurn(){state.readerTurned=true;}
function readerMessage(text,loading){const box=node('div',undefined,'reader-message');if(loading)box.append(node('span',undefined,'spinner'));box.append(node('p',text));return box;}
function syncPageStatus(){
  const r=state.reader,slider=$('page-slider');if(!r)return;const n=r.images.length;
  $('page-status').textContent=`${n?r.index+1:0} / ${n}`;slider.max=String(Math.max(1,n));slider.value=String(r.index+1);slider.disabled=n<2;
  slider.style.setProperty('--p',n>1?r.index/(n-1):1);
}
function fillChapterSelect(book,chapterId){
  const select=$('reader-chapter');
  if(state.readerSelectBook!==book){state.readerSelectBook=book;select.replaceChildren(...book.chapters.map(c=>{const o=node('option',c.name);o.value=String(c.id);return o;}));}
  select.value=chapterId;select.hidden=book.chapters.length<2;
}
// showModal() focuses the first toolbar button (back), which mobile browsers ring and which pins the toolbar open; hand focus to the pages instead.
function showReaderDialog(){const dlg=$('reader-dialog');if(dlg.open)return;state.readerTurned=false;dlg.showModal();$('reader-pages').focus({preventScroll:true});}
// Continue reading straight into the reader: load the comic behind the scenes without opening its detail page.
async function resumeComic(item){
  if(sourcePlugin(item.source)?.enabled===false){toast('图源已关闭，重新启用入口位于「设置 → 图源管理」');return;}
  saveProgress();++state.detailRequest;chapterPreviews.stop();
  const request=++state.readerRequest;state.readerAbort?.abort();state.readerAbort=new AbortController();state.reader=null;
  state.observer?.disconnect();state.mounter?.disconnect();
  $('reader-comic').textContent=item.title||'';$('reader-title').textContent='';$('reader-chapter').hidden=true;$('page-status').textContent='';
  $('chapter-prev').disabled=$('chapter-next').disabled=true;
  $('reader-pages').replaceChildren(readerMessage('正在获取漫画…',true));
  $('reader-dialog').classList.add('ui-open','ui-pinned');showReaderDialog();
  try{
    const [detail,reading]=await Promise.all([api(`/api/comic/${enc(item.source)}/${enc(item.id)}`,{signal:state.readerAbort.signal}),fetchComicReading(item)]);
    if(request!==state.readerRequest)return;
    state.detailItem=item;
    state.detail={...detail,cover:detail.cover||item.cover,category:detail.category||item.category,reading:{comic:reading.comic||null,chapters:reading.chapters||{}}};
    const chapters=state.detail.chapters||[],last=readingLast(state.detail),target=chapters.find(c=>String(c.id)===last?.chapterId)||chapters[0];
    if(!target){$('reader-pages').replaceChildren(readerMessage('该漫画暂无可阅读章节'));return;}
    await openReader(String(target.id));
  }catch(e){
    if(request!==state.readerRequest||e.name==='AbortError')return;
    const box=readerMessage(e.message),actions=node('div',undefined,'reader-actions');
    actions.append(labelBtn('refresh-cw','重试',()=>resumeComic(item)));
    if(e.code==='login_required'&&e.source)actions.append(bindButton(e.source));
    actions.append(labelBtn('search','在其他图源搜索',()=>searchElsewhere(item.title)));
    box.append(actions);$('reader-pages').replaceChildren(box);
  }
}
async function openReader(chapterId,startAt){
  saveProgress();const book=state.detail;if(!book)return;book.chapters=book.chapters||[];const chapter=book.chapters.find(c=>String(c.id)===chapterId);if(!chapter)return;
  chapterPreviews.stop();
  const request=++state.readerRequest;state.readerAbort?.abort();state.readerAbort=new AbortController();state.reader=null;
  state.observer?.disconnect();state.mounter?.disconnect();
  $('reader-comic').textContent=book.title;$('reader-title').textContent=chapter.name;fillChapterSelect(book,chapterId);
  $('reader-pages').replaceChildren(readerMessage('正在获取章节…',true));$('page-status').textContent='';
  const index=book.chapters.indexOf(chapter);$('chapter-prev').disabled=index<=0;$('chapter-next').disabled=index>=book.chapters.length-1;
  $('reader-dialog').classList.add('ui-open','ui-pinned');
  showReaderDialog();
  try{const data=await api(`/api/chapter/${enc(book.source)}/${enc(book.id)}/${enc(chapterId)}`,{signal:state.readerAbort.signal});if(request!==state.readerRequest)return;
    const saved=chapterProgress(book,chapterId);const resume=Math.max(0,Math.min(Number(saved?.index)||0,data.images.length-1));
    state.reader={book,chapterId,chapterName:chapter.name,images:data.images,index:typeof startAt==='number'?(startAt<0?Math.max(0,data.images.length-1):0):resume};renderReader();
  }catch(e){
    if(request!==state.readerRequest||e.name==='AbortError')return;
    const box=readerMessage(e.message);const actions=node('div',undefined,'reader-actions');
    actions.append(labelBtn('refresh-cw','重试',()=>openReader(chapterId,startAt)));
    if(e.code==='login_required'&&e.source)actions.append(bindButton(e.source));
    actions.append(labelBtn('search','在其他图源搜索',()=>searchElsewhere(book.title)));
    box.append(actions);$('reader-pages').replaceChildren(box);
  }
}
function chapterEnd(r){
  const box=node('div',undefined,'chapter-end'),chapters=r.book.chapters,i=chapters.findIndex(c=>String(c.id)===r.chapterId),next=chapters[i+1];
  box.append(node('p','本章完','chapter-end-title'),node('p',next?`下一章：${next.name}`:'已经是最后一章了','muted'));
  const actions=node('div',undefined,'reader-actions');
  if(next)actions.append(labelBtn('chevrons-right','阅读下一章',()=>moveChapter(1,true),'primary'));
  actions.append(labelBtn('list','返回目录',()=>closeReader()));
  box.append(actions);return box;
}
function renderReader(){
  state.observer?.disconnect();state.mounter?.disconnect();const r=state.reader,container=$('reader-pages');if(!r)return;container.replaceChildren();container.scrollTop=0;
  state.readerQuietUntil=Date.now()+900;
  const paged=$('reader-mode').value==='page';container.classList.toggle('paged',paged);
  syncPageStatus();
  if(!r.images.length){container.replaceChildren(readerMessage('该章节暂无图片'));return;}
  const makeImage=index=>{const wrapper=node('div','','page');wrapper.dataset.index=index;const img=image(r.images[index],`第 ${index+1} 页`);img.loading='eager';img.addEventListener('load',()=>img.classList.toggle('tall',img.naturalHeight>img.naturalWidth*2.5));img.onerror=()=>{const fail=node('div',undefined,'image-error');fail.append(node('p',`第 ${index+1} 页加载失败`),labelBtn('refresh-cw','重新加载',()=>{wrapper.replaceWith(makeImage(index));},'small'));wrapper.replaceChildren(fail);};wrapper.append(img);return wrapper;};
  if(paged){container.append(makeImage(r.index));for(const ahead of [r.index+1,r.index+2,r.index-1]){if(ahead>=0&&ahead<r.images.length){const preload=new Image();preload.src=r.images[ahead];}}}
  else{
    // Mount placeholders for every page; real <img> nodes are created only near the viewport.
    const mount=el=>{const real=makeImage(Number(el.dataset.index));state.mounter?.unobserve(el);state.observer?.unobserve(el);state.observer?.observe(real);el.replaceWith(real);};
    for(let i=0;i<r.images.length;i++){const ph=node('div',undefined,'ph');ph.dataset.index=i;container.append(ph);}
    container.append(chapterEnd(r));
    const pages=()=>[...container.children].filter(n=>n.dataset.index!==undefined);
    state.mounter=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting&&entry.target.classList.contains('ph'))mount(entry.target);},{root:container,rootMargin:'250% 0px'});
    pages().forEach(n=>state.mounter.observe(n));
    mount(container.children[r.index]);
    for(const near of [r.index+1,r.index-1]){const el=container.children[near];if(el?.classList.contains('ph'))mount(el);}
    container.children[r.index]?.scrollIntoView({block:'start'});
    // Wait for the target page to load before observing, so restoring does not overwrite saved progress.
    let restored=false;const restore=()=>{if(restored||state.reader!==r||$('reader-mode').value!=='scroll')return;restored=true;state.readerQuietUntil=Date.now()+600;container.children[r.index]?.scrollIntoView({block:'start'});state.observer=new IntersectionObserver(entries=>{const visible=entries.filter(e=>e.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top);if(visible.length){const index=Number(visible[0].target.dataset.index);if(index!==r.index)markPageTurn();r.index=index;syncPageStatus();saveProgress();}},{root:container,rootMargin:'0px 0px -70% 0px',threshold:0});pages().forEach(n=>state.observer.observe(n));};
    const img=container.children[r.index]?.querySelector('img');
    if(img){img.addEventListener('load',restore,{once:true});img.addEventListener('error',restore,{once:true});if(img.complete)restore();}else restore();
  }
  saveProgress();
  syncReaderProgress();
}
// Pinch/drag zoom for reader images. Zoom state lives on the img element.
const pinch={pointers:new Map(),g:null,suppress:false};
function zoomApply(img){const z=img._zoom||{s:1,x:0,y:0};img.style.transform=`translate(${z.x}px,${z.y}px) scale(${z.s})`;img.classList.toggle('zoomed',z.s>1.01);}
function resetZoom(img){img._zoom={s:1,x:0,y:0};zoomApply(img);}
function zoomAround(img,s,px,py){const z=img._zoom||{s:1,x:0,y:0};const ns=Math.max(1,Math.min(6,z.s*s));const f=ns/z.s;z.x=px-(px-z.x)*f;z.y=py-(py-z.y)*f;z.s=ns;img._zoom=z;zoomApply(img);}
$('reader-pages').addEventListener('pointerdown',event=>{
  const img=event.target.closest('img');if(!img)return;
  pinch.pointers.set(event.pointerId,{x:event.clientX,y:event.clientY,img});
  if(pinch.pointers.size===1)pinch.g={img};
  else if(pinch.pointers.size===2){const [a,b]=[...pinch.pointers.values()];pinch.g={d0:Math.hypot(a.x-b.x,a.y-b.y)||1,img:a.img===b.img?a.img:null};}
  try{$('reader-pages').setPointerCapture(event.pointerId);}catch{}
});
$('reader-pages').addEventListener('pointermove',event=>{
  const p=pinch.pointers.get(event.pointerId);if(!p)return;
  const dx=event.clientX-p.x,dy=event.clientY-p.y;
  p.x=event.clientX;p.y=event.clientY;
  if(pinch.pointers.size===2&&pinch.g&&pinch.g.img){
    const [a,b]=[...pinch.pointers.values()];const d=Math.hypot(a.x-b.x,a.y-b.y)||1;
    zoomAround(pinch.g.img,d/pinch.g.d0,(a.x+b.x)/2,(a.y+b.y)/2);pinch.g.d0=d;pinch.suppress=true;
  }else if(pinch.pointers.size===1){
    const img=p.img,z=img&&img._zoom;
    if(z&&z.s>1.01&&(dx||dy)){z.x+=dx;z.y+=dy;zoomApply(img);pinch.suppress=true;}
  }
});
const pinchEnd=event=>{
  pinch.pointers.delete(event.pointerId);
  const img=pinch.g?.img;
  if(pinch.pointers.size<2&&img&&img._zoom&&img._zoom.s<=1.05)resetZoom(img);
  if(pinch.pointers.size===1)pinch.g={img:[...pinch.pointers.values()][0].img};
  if(!pinch.pointers.size)pinch.g=null;
  setTimeout(()=>pinch.suppress=false,50);
};
$('reader-pages').addEventListener('pointerup',pinchEnd);
$('reader-pages').addEventListener('pointercancel',pinchEnd);
// Thin progress line along the toolbar bottom; scroll mode tracks the container, paged mode the index.
function syncReaderProgress(){const r=state.reader,el=$('reader-progress');if(!r||!el)return;const c=$('reader-pages');
  const p=$('reader-mode').value==='page'?(r.images.length>1?r.index/(r.images.length-1):1):(c.scrollTop/(c.scrollHeight-c.clientHeight||1));
  el.style.setProperty('--p',Math.max(0,Math.min(1,p)));}
// Scrolling down hides the reader chrome, scrolling up brings it back.
$('reader-pages').addEventListener('scroll',()=>{
  syncReaderProgress();
  const c=$('reader-pages'),dlg=$('reader-dialog'),y=c.scrollTop,dy=y-(state.lastReaderY??y);state.lastReaderY=y;
  if(Date.now()<(state.readerQuietUntil||0)||$('reader-mode').value!=='scroll'||dlg.querySelector(':focus-within select,:focus-within input,.dd-trigger[aria-expanded="true"]'))return;
  if(dy>24){dlg.classList.remove('ui-open','ui-pinned');}
  else if(dy<-36){dlg.classList.add('ui-open');}
},{passive:true});
function gotoPage(index){const r=state.reader;if(!r||index<0||index>=r.images.length)return;const from=r.index;if(index!==from)markPageTurn();r.index=index;if($('reader-mode').value==='page')renderReader();else{const el=$('reader-pages').children[index];if(el?.classList.contains('ph')){state.readerQuietUntil=Date.now()+900;}el?.scrollIntoView({block:'start',behavior:Math.abs(index-from)>3?'auto':'smooth'});syncPageStatus();saveProgress();}syncReaderProgress();}
function movePage(delta){const r=state.reader;if(!r)return;const index=r.index+delta;if(index<0||index>=r.images.length){moveChapter(delta,true);return;}gotoPage(index);}
function moveChapter(delta,turn){const r=state.reader,book=r?.book||state.detail;if(!book)return;const chapters=book.chapters,current=r?r.chapterId:$('reader-chapter').value,index=chapters.findIndex(c=>String(c.id)===current);if(chapters[index+delta]){if(turn)markPageTurn();run(()=>openReader(String(chapters[index+delta].id),turn?(delta>0?0:-1):undefined));}else toast(delta>0?'已经是最后一章了':'已经是第一章了');}
function closeReader(silent){
  saveProgress();state.readerAbort?.abort();++state.readerRequest;state.observer?.disconnect();state.mounter?.disconnect();state.reader=null;
  if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});
  $('reader-dialog').close();$('reader-pages').replaceChildren();flushReading();renderContinue();refreshCardStates();
  if(!silent&&state.detail&&$('detail-dialog').open){const scroller=$('detail-scroll'),top=scroller.scrollTop;renderDetail();scroller.scrollTop=top;}
}

/* ---------- wiring ---------- */
document.querySelectorAll('[data-tab]').forEach(n=>n.onclick=()=>showTab(n.dataset.tab));
document.querySelectorAll('[data-close]').forEach(n=>n.onclick=()=>{if(n.dataset.close==='detail-dialog')++state.detailRequest;$(n.dataset.close).close();});
// Click on the dialog backdrop (outside its content) closes it, same as the close button.
document.querySelectorAll('dialog:not(#reader-dialog)').forEach(dlg=>dlg.addEventListener('click',event=>{if(event.target!==dlg)return;const r=dlg.getBoundingClientRect();if(event.clientX>=r.left&&event.clientX<=r.right&&event.clientY>=r.top&&event.clientY<=r.bottom)return;if(dlg.id==='detail-dialog')++state.detailRequest;dlg.close();}));
$('detail-dialog').addEventListener('close',()=>{if(!$('detail-dialog').open){++state.detailRequest;chapterPreviews.stop();}});
$('detail-dialog').addEventListener('cancel',()=>chapterPreviews.stop());
$('detail-scroll').addEventListener('scroll',syncDetailHead,{passive:true});
$('browse-form').onsubmit=browse;
function syncClear(){$('keyword-clear').hidden=!$('keyword').value;}
$('keyword').addEventListener('input',syncClear);
$('keyword').addEventListener('keydown',event=>{if(event.key==='Escape'&&$('keyword').value){event.preventDefault();$('keyword').value='';syncClear();}});
$('keyword-clear').onclick=()=>{$('keyword').value='';syncClear();$('keyword').focus();};
$('home-link').onclick=event=>{event.preventDefault();goHome();$('keyword').focus();};
$('brand').onclick=event=>{event.preventDefault();goHome();};
$('results-back').onclick=goHome;
$('discover-prev').onclick=()=>turnPage(-1);$('discover-next').onclick=()=>turnPage(1);
$('discover-again').onclick=()=>run(loadDiscover,$('discover-again'));
$('results-more-btn').onclick=showMoreResults;
new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)&&!$('results-more').hidden)showMoreResults();},{rootMargin:'800px 0px'}).observe($('results-more'));
$('recent-clear').onclick=()=>{saveLocal('comic:recent',[]);renderRecent();};
$('recent-page-toggle').onclick=()=>{state.recentPage=!state.recentPage;saveLocal('comic:recent-page',state.recentPage);syncRecentPageToggle();renderRecent();};
syncRecentPageToggle();
$('history-clear').onclick=()=>run(async()=>{if(!await ask({title:'清除阅读记录',message:'清空「继续阅读」列表？各章节的阅读进度会保留。',ok:'清除',danger:true}))return;await flushReading();await api('/api/reading/history',{method:'DELETE'});setReading(readingItems().map(h=>({...h,in_history:false})));renderContinue();});
{const row=$('continue-row'),shelf=row.parentElement;shelf.append(state.continueScroller=scrollerFor(row));}
window.addEventListener('resize',()=>document.querySelectorAll('.scroller').forEach(s=>s.syncArrows?.()),{passive:true});
// Restore tabs as well as browse queries when navigating back or forward.
function restorePage(snap){
  if(!state.browseReady)return;
  const tab=snap?.tab||new URLSearchParams(location.search).get('tab')||'browse';
  showTab(tab==='settings'?'browse':tab,false);
  if(state.tab==='browse'){
    if(snap?.view==='results'){
      state.search=snap.search?{...snap.search}:null;Object.assign(state.discover,snap.discover||{});
      $('keyword').value=state.search?.keyword||'';syncClear();setView('results');run(state.search?loadSearch:loadDiscover);
    }else showHome();
  }
  syncHistory(false);
  if(tab==='settings')run(openSiteSettings);
}
window.addEventListener('popstate',event=>restorePage(event.state));
$('library-refresh').onclick=()=>run(loadLibrary,$('library-refresh'));$('library-category').onchange=filterBooks;$('library-sort').onchange=filterBooks;$('library-filter').oninput=filterBooks;if(typeof dropdown!=='undefined')state.libraryMenu=dropdown.filterMenu($('library-filter-open'),[{select:$('library-sort')},{select:$('library-category'),default:'*'}]);$('tasks-refresh').onclick=()=>run(loadTasks,$('tasks-refresh'));
initSiteSettings();
$('health-recheck').onclick=()=>runHealthChecks(true);
$('login-form').onsubmit=event=>{event.preventDefault();const source=state.settingsSource;run(async()=>{await api(`/api/sources/${enc(source)}/login`,write('POST',Object.fromEntries(new FormData(event.target))));await loadSources();configureAccount();if(source==='bika')refreshBikaAccountViews();toast('图源账号已保存');},event.submitter);};
$('reader-close').onclick=()=>closeReader();$('reader-dialog').addEventListener('cancel',event=>{event.preventDefault();closeReader();});
$('reader-download').onclick=()=>{const r=state.reader,book=r?.book||state.detail;if(!book)return;const chapter=(book.chapters||[]).find(c=>String(c.id)===(r?r.chapterId:$('reader-chapter').value));if(chapter)queueDownload(book,chapter);};
const toggleReaderUI=()=>{const dlg=$('reader-dialog');const on=!dlg.classList.contains('ui-open');dlg.classList.toggle('ui-open',on);dlg.classList.toggle('ui-pinned',on);};
const syncReaderUI=event=>{if(event.pointerType!=='mouse'||$('reader-dialog').querySelector('.dd-pop'))return;const y=event.clientY;const dlg=$('reader-dialog');if(y<=70||y>=innerHeight-110)dlg.classList.add('ui-open');else if(!dlg.classList.contains('ui-pinned'))dlg.classList.remove('ui-open');};
$('reader-dialog').addEventListener('pointermove',syncReaderUI);$('reader-dialog').addEventListener('pointerleave',()=>{const dlg=$('reader-dialog');if(!dlg.classList.contains('ui-pinned'))dlg.classList.remove('ui-open');});
// Tap zones: in paged mode left/right thirds turn pages, middle toggles UI; in scroll mode any tap toggles UI.
$('reader-pages').addEventListener('click',event=>{if(pinch.suppress||event.target.closest('button,a,select,input'))return;const paged=$('reader-mode').value==='page';const third=event.clientX/innerWidth;if(!paged||third>0.33&&third<0.67){toggleReaderUI();return;}movePage(third<0.5?-1:1);});
$('reader-mode').value=readLocal('comic:reader-mode')==='page'?'page':'scroll';$('reader-mode').onchange=()=>{saveLocal('comic:reader-mode',$('reader-mode').value);renderReader();};
$('reader-chapter').onchange=()=>run(()=>openReader($('reader-chapter').value));
$('page-slider').oninput=()=>{const r=state.reader;if(r)$('page-status').textContent=`${$('page-slider').value} / ${r.images.length}`;};
$('page-slider').onchange=()=>gotoPage(Number($('page-slider').value)-1);
$('reader-fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});else $('reader-dialog').requestFullscreen?.().catch(()=>toast('浏览器不支持全屏'));};
$('page-prev').onclick=()=>movePage(-1);$('page-next').onclick=()=>movePage(1);$('chapter-prev').onclick=()=>moveChapter(-1);$('chapter-next').onclick=()=>moveChapter(1);
window.addEventListener('keydown',event=>{
  if(!$('reader-dialog').open||['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName)||event.target.closest?.('.dd-select,.dd-pop')||$('ask-dialog').open)return;const r=state.reader;
  if(['ArrowRight','PageDown',' '].includes(event.key)){event.preventDefault();movePage(1);}
  else if(['ArrowLeft','PageUp'].includes(event.key)){event.preventDefault();movePage(-1);}
  else if(event.key==='Home'&&r){event.preventDefault();gotoPage(0);}
  else if(event.key==='End'&&r){event.preventDefault();gotoPage(r.images.length-1);}
  else if(event.key==='['){moveChapter(-1);}else if(event.key===']'){moveChapter(1);}
  else if(event.key==='f'){$('reader-fullscreen').click();}
});
// "/" focuses the search box from anywhere outside inputs and dialogs.
window.addEventListener('keydown',event=>{
  if(event.key!=='/'||event.ctrlKey||event.metaKey||event.altKey||document.querySelector('dialog[open]'))return;
  if(event.target.closest?.('input,textarea,select,[contenteditable="true"]'))return;
  event.preventDefault();if(state.tab!=='browse')showTab('browse');$('keyword').focus();$('keyword').select();
});
window.addEventListener('pagehide',()=>{saveProgress();flushReading(true);});
// Mobile browsers may never fire pagehide; flush on hide and pick up other devices' progress on return.
document.addEventListener('visibilitychange',()=>{if(document.hidden){saveProgress();flushReading(true);}else if(state.readingLoaded)loadReading().catch(()=>{});});
// Page-level scrollbar + floating back-to-top button.
window.addEventListener('scroll',()=>{
  const h=document.documentElement,p=h.scrollTop/(h.scrollHeight-h.clientHeight||1);
  $('scroll-progress').style.transform=`scaleX(${p})`;
  $('to-top').classList.toggle('show',h.scrollTop>600);
  document.body.classList.toggle('scrolled',h.scrollTop>8);
},{passive:true});
$('to-top').onclick=()=>window.scrollTo({top:0,behavior:'smooth'});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',event=>{if(readLocal('comic:light')===null)document.documentElement.classList.toggle('light',!event.matches);});
function syncThemeColor(){document.querySelector('meta[name=theme-color]').content=document.documentElement.classList.contains('light')?'#f7f5f1':'#0e0e12';}
$('theme').onclick=()=>{saveLocal('comic:light',document.documentElement.classList.toggle('light'));syncThemeColor();};
syncThemeColor();
setInterval(()=>{if((state.tab==='downloads'||state.activeTasks)&&!document.hidden&&!loadTasks.busy){loadTasks.busy=true;loadTasks().catch(e=>{if(state.tab==='downloads')toast(e.message);}).finally(()=>loadTasks.busy=false);}},2500);
// autofocus pops the on-screen keyboard on touch devices; drop it there.
if(matchMedia('(pointer:coarse)').matches&&document.activeElement===$('keyword'))$('keyword').blur();
// All deferred feature scripts must be ready before restoring a deep-linked tab.
const appReady=new Promise(resolve=>document.addEventListener('DOMContentLoaded',resolve,{once:true}))
  .then(()=>run(async()=>{await loadSources();await loadLibrary();refreshTaskDot();}))
  .finally(endBooting);
appReady.then(()=>run(async()=>{
  try{await migrateLocalReading();}catch(e){toast(`旧阅读记录导入失败，下次打开页面时重试：${e.message}`,{tone:'error'});}
  await loadReading();state.readingLoaded=true;
}));
