const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');
const listing = readFileSync(join(__dirname, '../src/web/static/bika-favorites.js'), 'utf8');
const favoriteSync = readFileSync(join(__dirname, '../src/web/static/favorite-sync.js'), 'utf8');

class Element {
  constructor(tag) {
    this.tagName=tag;this.children=[];this.className='';this.disabled=false;this.value='';
    this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name),add:name=>this.classes.add(name)};
    this.dataset={};this.listeners={};
  }
  set textContent(value) { this.text=String(value);this.children=[]; }
  get textContent() { return (this.text||'')+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { this.html=value;this.text='';this.children=[]; }
  setAttribute(name,value) { this[name]=String(value); }
  removeAttribute(name) { delete this[name]; }
  addEventListener(type,listener) { this.listeners[type]=listener; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text='';this.children=children; }
  querySelector(selector) { return this.querySelectorAll(selector)[0]||null; }
  prepend(...children) { this.children.unshift(...children); }
  querySelectorAll(selector) {
    return this.children.flatMap(child=>[...(selector==='button'&&child.tagName==='button'||selector.startsWith('.')&&child.className.split(' ').includes(selector.slice(1))?[child]:[]),...child.querySelectorAll(selector)]);
  }
}
const detail={source:'bika',id:'00000000000000000000000a',title:'测试漫画',chapters:[]};
const flush=()=>new Promise(resolve=>setImmediate(resolve));

function harness(){
  const state={tab:'browse',sources:[{id:'bika',capabilities:['favorites','add_favorite'],account:{authenticated:true}}]};
  const roots={},calls=[],toasts=[],bindings=[],tabs=[];
  const root=id=>roots[id]||(roots[id]=new Element('div'));
  const context=vm.createContext({state,document:{createElement:tag=>new Element(tag)},$:root,enc:encodeURIComponent,URLSearchParams,
    location:{search:'',origin:'http://host'},URL,appReady:Promise.resolve(),sourcePlugin:id=>state.sources.find(source=>source.id===id),
    svgIcon:name=>`<svg>${name}</svg>`,run:async(action,control)=>{if(control)control.disabled=true;try{return await action();}finally{if(control)control.disabled=false;}},
    api:async(path,options)=>{calls.push({path,options});return options?.method==='POST'?{success:true,favorited:true,already_favorited:false}:{favorited:null};},
    toast:(message,options)=>toasts.push({message,options}),showTab:tab=>tabs.push(tab),
    bindButton:id=>{const button=new Element('button');button.textContent='绑定哔咔账号';button.onclick=()=>bindings.push(id);return button;},
    card:item=>{const el=new Element('article');el.textContent=item.title;el.item=item;return el;},skeletons:()=>{const el=new Element('i');el.skeleton=true;return [el];},
    coverFor:item=>item.cover||'',readingLast:()=>null,
    renderKeyed:(box,items,keyOf,signatureOf,build)=>{
      const previous=box.keyedCards||new Map(),next=new Map();
      const nodes=items.map(item=>{const key=String(keyOf(item)),signature=JSON.stringify(signatureOf(item)),old=previous.get(key);
        const entry=old&&old.signature===signature?old:{signature,el:build(item)};next.set(key,entry);return entry.el;});
      box.keyedCards=next;box.replaceChildren(...nodes);return nodes;},
  });
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('function emptyState('),app.indexOf('function timeAgo(')),context);
  vm.runInContext(app.slice(app.indexOf('function cloudFavoriteButton('),app.indexOf('function syncDetailHead(')),context);
  vm.runInContext(app.slice(app.indexOf('function coverKey('),app.indexOf('// Reconcile a card list')),context);
  vm.runInContext(favoriteSync.slice(favoriteSync.indexOf('const FAV_SYNC'),favoriteSync.indexOf('const syncField')),context);
  vm.runInContext(favoriteSync.slice(favoriteSync.indexOf('function syncableCard('),favoriteSync.indexOf('async function startFavSync(')),context);
  context.startFavSync=(source,ids)=>{calls.push({sync:source,ids});};
  vm.runInContext(listing,context);
  return {context,state,roots,calls,toasts,bindings,tabs,view:vm.runInContext('bikaFavorites',context)};
}
const page=(items=[],total=items.length,hasNext=false)=>({items,total,page:1,page_size:2,has_next:hasNext});

async function open(h){const control=h.context.cloudFavoriteButton(detail);await flush();return control;}

test('哔咔详情独立读取本地云收藏状态，未知态仍需实时预检查，不写书库',async()=>{
  const h=harness(),control=await open(h);
  assert.equal(control.textContent,'加入哔咔收藏');assert.match(control.title,/本地收藏表尚无/);
  assert.equal(h.calls[0].path,`/api/bika/favorites/${detail.id}`);assert.equal(h.calls[0].options.cache,'no-store');
  await control.onclick();assert.equal(control.textContent,'已收藏');assert.equal(h.calls[1].options.method,'POST');
  assert.equal(h.toasts[0].message,'已加入哔咔收藏');assert.ok(h.calls.every(call=>!call.path.includes('/library')));
});

test('重新打开哔咔详情读最新表状态，不复用旧按钮状态',async()=>{
  const h=harness();let favorited=true;
  h.context.api=async()=>({favorited});
  assert.equal((await open(h)).textContent,'已收藏');favorited=false;
  assert.equal((await open(h)).textContent,'加入哔咔收藏');
});

test('哔咔未绑定时提供账号入口，不查询或写入收藏',async()=>{
  const h=harness();h.state.sources[0].account={};
  const control=await open(h);await control.onclick();assert.deepEqual(h.bindings,['bika']);assert.equal(h.calls.length,0);
  h.state.sources[0].capabilities=['favorites'];assert.equal(h.context.cloudFavoriteButton(detail),null);
});

test('哔咔写入失败后的第一次重试只读状态，不重发切换请求',async()=>{
  const h=harness(),control=await open(h);let posts=0,reads=0;
  h.context.api=async(_path,options={})=>{if(options.method==='POST'){posts++;throw Object.assign(new Error('结果未确认'),{code:'favorite_unknown'});}reads++;return {favorited:true};};
  await control.onclick();assert.equal(control.textContent,'收藏状态未确认');
  await control.onclick();assert.equal(posts,1);assert.equal(reads,1);assert.equal(control.textContent,'已收藏');
});

test('哔咔添加成功仅在哔咔收藏页刷新，已收藏响应也不调用 JM 列表',async()=>{
  const h=harness();h.state.tab='bika-favorites';let refreshes=0;
  const control=await open(h);h.context.loadBikaFavorites=async()=>{refreshes++;};
  h.context.api=async()=>({success:true,favorited:true,already_favorited:true});
  await control.onclick();assert.equal(refreshes,1);assert.equal(h.toasts[0].message,'漫画已在哔咔收藏中');
});

test('哔咔状态查询失效提供哔咔绑定操作，不执行写入',async()=>{
  const h=harness();h.context.api=async()=>{throw Object.assign(new Error('登录失效'),{code:'login_required'});};
  const control=await open(h);assert.equal(control.textContent,'收藏状态未确认');
  await h.toasts[0].options.action.run();assert.deepEqual(h.bindings,['bika']);
});

test('列表使用真实分页、排序、无缓存和哔咔来源卡片',async()=>{
  const h=harness(),items=[{...detail},{...detail,id:'b',title:'另一本'}];
  h.context.api=async(path,options)=>{h.calls.push({path,options});return page(items,3,true);};
  await h.context.loadBikaFavorites();
  assert.equal(h.calls[0].path,'/api/bika/favorites?page=1&sort=dd');assert.equal(h.calls[0].options.cache,'no-store');
  assert.equal(h.roots['bika-favorites-count'].textContent,'3 本');assert.equal(h.roots['bika-favorites-page'].textContent,'第 1 / 2 页');
  assert.equal(h.roots['bika-favorites-prev'].disabled,true);assert.equal(h.roots['bika-favorites-next'].disabled,false);
  assert.equal(h.roots['bika-favorites-books'].children[0].item.source,'bika');
  const lists=()=>h.calls.filter(call=>call.path?.startsWith('/api/bika/favorites?'));
  h.roots['bika-favorites-next'].onclick();await flush();assert.match(lists().at(-1).path,/page=2/);
  h.roots['bika-favorites-sort'].value='vd';h.roots['bika-favorites-sort'].onchange();await flush();
  assert.match(lists().at(-1).path,/page=1&sort=vd/);
});

test('页内标题作者筛选不请求上游，筛选失败与空收藏分别显示',async()=>{
  const h=harness();h.context.api=async()=>page([{...detail,author:'Alice'},{...detail,title:'Second'}]);
  await h.context.loadBikaFavorites();h.roots['bika-favorites-keyword'].value='alice';h.context.renderBikaFavorites();
  assert.equal(h.roots['bika-favorites-books'].children.length,1);
  h.roots['bika-favorites-keyword'].value='unmatched';h.context.renderBikaFavorites();assert.match(h.roots['bika-favorites-books'].textContent,/本页没有匹配漫画/);
  h.context.api=async()=>page();await h.context.loadBikaFavorites();assert.match(h.roots['bika-favorites-books'].textContent,/收藏为空/);
  assert.equal(h.roots['bika-favorites-pager'].hidden,true);
});

test('选择下载状态后按本地记录的全部收藏筛选，并可更新收藏列表',async()=>{
  const h=harness();h.context.setTimeout=()=>0;h.context.clearTimeout=()=>{};
  let refresh={active:false,sort:'',pages:0,total_pages:0,error:''};
  const items=[{...detail,id:'b',title:'乙'},{...detail,id:'c',title:'丙'}];
  h.context.api=async(path,options)=>{
    h.calls.push({path,options});
    if(path.startsWith('/api/bika/sync/catalog/refresh')){refresh={...refresh,active:true,pages:1,total_pages:3};return refresh;}
    if(path.startsWith('/api/bika/sync/catalog'))return {complete:false,updated_at:null,sort:'',counts:{all:3,downloaded:1,missing:2},total:2,page:1,page_size:20,has_next:false,items,local:{b:{status:'error',message:'坏图'}},refresh};
    return page([{...detail,id:'a',title:'甲'}]);
  };
  const shown=()=>h.roots['bika-favorites-books'].children.map(el=>el.item.id),notice=()=>h.roots['bika-favorites-catalog'];
  await h.context.loadBikaFavorites();assert.deepEqual(shown(),['a']);
  h.roots['bika-favorites-local'].value='missing';h.roots['bika-favorites-local'].onchange();await flush();
  assert.match(h.calls.at(-1).path,/^\/api\/bika\/sync\/catalog\?local=missing&page=1$/);
  assert.deepEqual(shown(),['b','c']);assert.equal(h.roots['bika-favorites-count'].textContent,'2 本');
  assert.match(notice().textContent,/本地尚未记录完整收藏列表，当前按已记录的 3 本筛选/);
  await notice().querySelector('button').onclick();await flush();
  assert.equal(h.calls.find(call=>call.path.includes('/refresh')).path,'/api/bika/sync/catalog/refresh?sort=dd');
  assert.match(notice().textContent,/正在更新收藏列表：第 1 \/ 3 页/);
  h.roots['bika-favorites-local'].value='';h.roots['bika-favorites-local'].onchange();await flush();
  assert.deepEqual(shown(),['a']);assert.equal(notice().children.length,0);
});

test('再次进入同一页不显示骨架屏，未变化卡片原样保留',async()=>{
  const h=harness(),items=[{...detail,cover:'/api/image/proxy?source=bika&url=https%3A%2F%2Fa.example%2F1.jpg'},{...detail,id:'b',title:'另一本'}];
  let resolve;h.context.api=async()=>page(items);
  await h.context.loadBikaFavorites();const [first,second]=h.roots['bika-favorites-books'].children;
  h.context.api=()=>new Promise(done=>{resolve=done;});
  const reload=h.context.loadBikaFavorites();
  assert.ok(!h.roots['bika-favorites-books'].children.some(el=>el.skeleton));assert.equal(h.roots['bika-favorites-books'].children[0],first);
  resolve(page([{...items[0],cover:'/api/image/proxy?source=bika&url=https%3A%2F%2Fb.example%2F1.jpg'},{...items[1],title:'改名'}]));await reload;
  assert.equal(h.roots['bika-favorites-books'].children[0],first);assert.notEqual(h.roots['bika-favorites-books'].children[1],second);
  h.roots['bika-favorites-sort'].value='vd';h.roots['bika-favorites-sort'].onchange();
  assert.ok(h.roots['bika-favorites-books'].children[0].skeleton);
});

test('未登录列表无请求，登录失效和上游错误不伪装为空收藏',async()=>{
  const h=harness();h.state.sources[0].account={};await h.context.loadBikaFavorites();assert.equal(h.calls.length,0);
  assert.match(h.roots['bika-favorites-books'].textContent,/需要登录/);
  h.state.sources[0].account={configured:true};h.context.api=async()=>{throw Object.assign(new Error('登录已失效'),{code:'login_required'});};
  await h.context.loadBikaFavorites();assert.match(h.roots['bika-favorites-books'].textContent,/收藏加载失败/);
  assert.equal(h.roots['bika-favorites-message'].children[0].textContent,'绑定哔咔账号');
});

test('迟到的上一页或旧账号响应不覆盖新列表',async()=>{
  const h=harness();let resolve;h.context.api=()=>new Promise(done=>{resolve=done;});
  const first=h.context.loadBikaFavorites();h.state.sources[0].account={};await h.context.loadBikaFavorites();
  resolve(page([detail]));await first;assert.match(h.roots['bika-favorites-books'].textContent,/需要登录/);assert.equal(h.view.items.length,0);
});

test('桌面和移动导航有哔咔收藏入口，并支持动态列数及独立排版基线',()=>{
  const html=readFileSync(join(__dirname,'../src/web/templates/index.html'),'utf8');
  const css=readFileSync(join(__dirname,'../src/web/static/app.css'),'utf8');
  assert.equal(html.match(/data-tab="bika-favorites"/g).length,2);
  assert.match(html,/bika-favorites.js\?v=/);assert.match(css,/\.bika-favorites-view\{font-size:15px;line-height:1\.6\}/);
  assert.match(css,/\.tabbar\{display:grid;grid-auto-flow:column;grid-auto-columns:minmax\(0,1fr\)/);
  assert.match(app,/if\(tab==='bika-favorites'\)run\(loadBikaFavoritesView\)/);
});

test('收藏卡片可选择并批量或单本同步到本地，换页清空选择',async()=>{
  const h=harness(),items=[{...detail},{...detail,id:'00000000000000000000000b',title:'第二本'}];
  h.context.api=async path=>{h.calls.push({path});return page(items,4,true);};
  await h.context.loadBikaFavorites();
  const books=h.roots['bika-favorites-books'],[first]=books.children;
  assert.equal(h.roots['bika-sync-selected'].hidden,true);assert.equal(h.roots['bika-select-page'].disabled,false);
  const check=first.querySelectorAll('.select-mark')[0].children[0];check.checked=true;check.onchange();
  assert.equal(JSON.stringify([...h.view.selected]),JSON.stringify([detail.id]));assert.equal(h.roots['bika-sync-selected'].hidden,false);
  assert.equal(h.roots['bika-sync-selected'].textContent,'同步所选（1）');assert.ok(books.classes.has('selecting'));
  // 有选中项时按钮为「取消选择」（X），点击清空；无选中项时为「选择本页」。
  assert.equal(h.roots['bika-select-page']['aria-label'],'取消选择');assert.equal(h.roots['bika-select-page']['aria-pressed'],'true');
  h.roots['bika-select-page'].onclick();assert.equal(h.view.selected.size,0);
  assert.equal(h.roots['bika-select-page']['aria-label'],'选择本页');assert.equal(h.roots['bika-select-page']['aria-pressed'],'false');
  h.roots['bika-select-page'].onclick();assert.equal(h.view.selected.size,2);await h.roots['bika-sync-selected'].onclick();
  assert.equal(JSON.stringify(h.calls.at(-1)),JSON.stringify({sync:'bika',ids:items.map(item=>item.id)}));
  await first.querySelectorAll('.card-sync')[0].onclick();
  assert.equal(JSON.stringify(h.calls.at(-1)),JSON.stringify({sync:'bika',ids:[detail.id]}));
  h.roots['bika-favorites-next'].onclick();await flush();assert.equal(h.view.selected.size,0);
});

test('哔咔收藏页头提供本地同步入口，与 JM 共用同步窗口',()=>{
  const html=readFileSync(join(__dirname,'../src/web/templates/index.html'),'utf8');
  assert.match(html,/id="bika-sync-open"[^>]*aria-controls="fav-sync-dialog"/);
  assert.match(html,/id="jm-sync-open"[^>]*aria-controls="fav-sync-dialog"/);
  assert.ok(html.indexOf('/static/favorite-sync.js')<html.indexOf('/static/jm-sync.js'));
  assert.ok(html.indexOf('/static/favorite-sync.js')<html.indexOf('/static/bika-favorites.js'));
  assert.match(favoriteSync,/\/api\/\$\{source\}\/sync\/run/);
});

test('收藏卡片在作者行末尾附加本地状态提示（不画在封面上），未下载不显示',async()=>{
  const h=harness(),ids=['00000000000000000000000a','00000000000000000000000b','00000000000000000000000c'];
  const items=ids.map((id,index)=>({...detail,id,title:`漫画 ${index}`}));
  const local={[ids[0]]:{status:'downloaded',images:12,path:'/data/a',message:''},[ids[1]]:{status:'error',images:0,path:'/data/b',message:'章节图片数量不符'},[ids[2]]:{status:'missing',images:0,path:'',message:''}};
  h.context.card=item=>{const el=new Element('article'),cover=new Element('div'),authors=new Element('span');cover.className='cover';authors.className='comic-authors';el.append(cover,authors);el.item=item;return el;};
  h.context.api=async path=>{h.calls.push({path});return path.includes('/sync/local-status')?{root:'/data',counts:{},items:local}:page(items);};
  await h.context.loadBikaFavorites();await flush();await flush();
  const query=h.calls.find(call=>call.path.includes('/sync/local-status')).path;
  assert.match(query,/^\/api\/bika\/sync\/local-status\?ids=/);assert.equal(decodeURIComponent(query.split('ids=')[1]),ids.join(','));
  const cards=h.roots['bika-favorites-books'].children;
  assert.ok(cards.every(card=>!card.querySelectorAll('.cover')[0].querySelectorAll('.card-local').length));
  const badges=cards.map(card=>card.querySelectorAll('.comic-authors')[0].querySelectorAll('.card-local')[0]);
  assert.equal(badges[0].textContent,'已下载');assert.ok(badges[0].className.includes('ok'));
  assert.equal(badges[1].textContent,'本地异常');assert.match(badges[1].dataset.tipText,/章节图片数量不符/);
  assert.equal(badges[2],undefined);
});
