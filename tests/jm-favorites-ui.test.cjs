const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');

class Element {
  constructor(tag) {
    this.tagName=tag;this.children=[];this.className='';this.disabled=false;
    this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};
  }
  set textContent(value) { this.text=String(value);this.children=[]; }
  get textContent() { return (this.text||'')+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { this.html=value;this.text='';this.children=[]; }
  setAttribute(name,value) { this[name]=String(value); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text='';this.children=children; }
  querySelectorAll(selector) {
    return this.children.flatMap(child=>[...(selector==='button'&&child.tagName==='button'||selector.startsWith('.')&&child.className.split(' ').includes(selector.slice(1))?[child]:[]),...child.querySelectorAll(selector)]);
  }
}

function harness() {
  const state={tab:'browse',books:[],sources:[{id:'jm',capabilities:['favorites','add_favorite'],account:{authenticated:true}}]};
  const calls=[],toasts=[],bindings=[],roots={'detail-body':new Element('div'),'detail-title':new Element('div')};
  let refreshes=0;const asks=[];
  const context=vm.createContext({state,asks,ask:async options=>{asks.push(options);return context.confirmAnswer!==false;},document:{createElement:tag=>new Element(tag)},$:id=>roots[id],enc:encodeURIComponent,
    sourcePlugin:id=>state.sources.find(source=>source.id===id),sourceName:id=>id,svgIcon:name=>`<svg>${name}</svg>`,
    api:async(path,options)=>{calls.push({path,options});return options?.method==='POST'?{success:true,favorited:true,already_favorited:false}:options?.method==='DELETE'?{success:true,favorited:false,already_favorited:true}:{comic_id:'123',favorited:false};},
    toast:(message,options)=>toasts.push({message,options}),loadJmFavorites:async()=>{refreshes++;},
    bindButton:id=>{const button=new Element('button');button.textContent='绑定 JM 账号';button.onclick=()=>bindings.push(id);return button;},
    inLibrary:()=>false,readLocal:()=>null,readingLast:()=>null,renderComicLabels:()=>new Element('div'),
    syncChapterSort(){},syncChapterPreviews(){},drawChapters(){},requestAnimationFrame(){},
    detailHero:()=>{const hero=new Element('div'),text=new Element('div');hero.append(text);return {hero,text};},
  });
  vm.runInContext(readFileSync(join(__dirname, '../src/web/static/comic-authors.js'), 'utf8'),context);
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('function cloudFavoriteButton('),app.indexOf('function syncDetailHead(')),context);
  vm.runInContext(app.slice(app.indexOf('function renderDetail('),app.indexOf('async function toggleFavorite(')),context);
  return {context,state,calls,toasts,bindings,roots,get refreshes(){return refreshes;}};
}
const detail={source:'jm',id:'123',title:'测试漫画',chapters:[]};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function open(h,value=detail){const control=h.context.cloudFavoriteButton({...value});await flush();return control;}

test('重新打开详情或页面时，已加入 JM 收藏的漫画显示已收藏',async()=>{
  let favorited=false;
  const remote=async(_path,options={})=>{
    if(options.method==='POST'){favorited=true;return {success:true,favorited:true,already_favorited:false};}
    return {comic_id:'123',favorited};
  };
  const h=harness();h.context.api=remote;
  const first=await open(h);await first.onclick();
  const reopened=await open(h);assert.equal(reopened.textContent,'已收藏');
  const newPage=harness();newPage.context.api=remote;
  const reloaded=await open(newPage);assert.equal(reloaded.textContent,'已收藏');
});

test('详情中的本地书库与 JM 账号收藏使用独立按钮',async()=>{
  const h=harness();h.state.detail={...detail};h.context.renderDetail();await flush();
  const controls=h.roots['detail-body'].querySelectorAll('.detail-actions')[0];
  const texts=controls.querySelectorAll('button').map(button=>button.textContent);
  assert.ok(texts.includes('加入书库'));assert.ok(texts.includes('加入 JM 收藏'));
});

test('详情中的每位作者有独立搜索按钮，搜索时关闭详情',async()=>{
  const h=harness(),searches=[];
  h.state.detail={...detail,authors:['作者一','作者二']};h.state.detailRequest=0;
  h.roots['reader-dialog']={open:false};h.roots['detail-dialog']={open:true,close(){this.open=false;}};
  h.context.startSearch=(...args)=>searches.push(args);
  h.context.renderDetail();await flush();
  const links=h.roots['detail-body'].querySelectorAll('.author-link');
  assert.equal(links.length,2);await links[1].onclick();
  assert.equal(searches[0][0],'作者二');assert.equal(searches[0][2].mode,'keyword');
  assert.equal(h.roots['detail-dialog'].open,false);assert.equal(h.state.detailRequest,1);
});

test('只有支持加入收藏的 JM 图源显示云端操作，未绑定时只显示绑定入口',async()=>{
  const h=harness();
  assert.equal(h.context.cloudFavoriteButton({...detail,source:'bika'}),null);
  h.state.sources[0].capabilities=['favorites'];assert.equal(h.context.cloudFavoriteButton(detail),null);
  h.state.sources[0].capabilities.push('add_favorite');h.state.sources[0].account={};
  const control=h.context.cloudFavoriteButton(detail);assert.match(control.title,/需要绑定账号/);
  await control.onclick();assert.deepEqual(h.bindings,['jm']);assert.equal(h.calls.length,0);
});

test('打开详情只发禁止缓存的 GET，已收藏时不执行任何写入',async()=>{
  const h=harness();h.context.api=async(path,options)=>{h.calls.push({path,options});return {comic_id:'123',favorited:true};};
  const control=await open(h);
  assert.equal(control.textContent,'已收藏');assert.ok(control.classes.has('on'));
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,'/api/jm/favorites/123');
  assert.equal(h.calls[0].options.cache,'no-store');assert.notEqual(h.calls[0].options.method,'POST');
  assert.equal(h.toasts.length,0);
});

test('本地表未记录的状态不被误判，仍可通过实时预检查加入收藏',async()=>{
  const h=harness();let reads=0,posts=0;
  h.context.api=async(_path,options={})=>{
    if(options.method==='POST'){posts++;return {success:true,favorited:true};}
    reads++;return {favorited:null};
  };
  const control=await open(h);
  assert.equal(control.textContent,'加入 JM 收藏');assert.match(control.title,/本地收藏表尚无/);
  assert.equal(h.toasts.length,0);assert.equal(reads,1);assert.equal(posts,0);
  await control.onclick();assert.equal(posts,1);assert.equal(control.textContent,'已收藏');
});

test('成功调用独立 POST，不写本地书库，重复点击仍由服务器实时预检查',async()=>{
  const h=harness(),control=await open(h);
  await control.onclick();assert.equal(control.textContent,'已收藏');assert.equal(control.disabled,false);
  const posts=h.calls.filter(call=>call.options.method==='POST');
  assert.equal(posts[0].path,'/api/jm/favorites/123');assert.deepEqual(h.state.books,[]);
  assert.equal(h.toasts[0].message,'已加入 JM 收藏');
  h.context.confirmAnswer=false;await control.onclick();
  assert.equal(h.context.asks.length,1);assert.equal(h.calls.filter(call=>call.options.method==='DELETE').length,0);assert.equal(control.textContent,'已收藏');
  h.context.confirmAnswer=true;await control.onclick();
  const deletes=h.calls.filter(call=>call.options.method==='DELETE');
  assert.equal(deletes.length,1);assert.equal(deletes[0].path,'/api/jm/favorites/123');
  assert.equal(control.textContent,'加入 JM 收藏');assert.equal(h.toasts[1].message,'已从 JM 收藏中取消');
  await control.onclick();assert.equal(h.calls.filter(call=>call.options.method==='POST').length,2);
});

test('再次点击已收藏取消 JM 收藏，远端已不在收藏中也显示未收藏并刷新收藏页',async()=>{
  const h=harness();h.state.tab='jm-favorites';
  h.context.api=async(_path,options={})=>options.method==='DELETE'?{success:true,favorited:false,already_favorited:false}:{comic_id:'123',favorited:true};
  const control=await open(h);assert.equal(control.textContent,'已收藏');
  await control.onclick();
  assert.equal(h.toasts[0].message,'漫画不在 JM 收藏中');assert.equal(h.refreshes,1);assert.equal(control.textContent,'加入 JM 收藏');
});

test('已经收藏也显示成功，当前收藏页在成功后刷新',async()=>{
  const h=harness();h.state.tab='jm-favorites';
  h.context.api=async(_path,options={})=>options.method==='POST'?{success:true,favorited:true,already_favorited:true}:{favorited:false};
  const control=await open(h);await control.onclick();
  assert.equal(h.toasts[0].message,'漫画已在 JM 收藏中');assert.equal(h.refreshes,1);assert.equal(control.textContent,'已收藏');
});

test('状态检查期间禁用按钮，旧详情的迟到状态不覆盖新详情',async()=>{
  const h=harness();let resolve;
  h.context.api=()=>new Promise(done=>{resolve=done;});
  const old=h.context.cloudFavoriteButton(detail);
  assert.equal(old.disabled,true);assert.equal(old.textContent,'读取 JM 收藏…');
  const complete=resolve;h.context.api=async()=>({favorited:false});
  const current=await open(h,{...detail,id:'456'});
  complete({favorited:true});await flush();
  assert.equal(old.textContent,'已收藏');assert.equal(current.textContent,'加入 JM 收藏');assert.equal(current.disabled,false);
});

test('添加期间禁用按钮，详情切换后的迟到响应不改动新漫画',async()=>{
  const h=harness(),control=await open(h);let resolve;
  h.context.api=()=>new Promise(done=>{resolve=done;});
  const pending=control.onclick();
  await flush();assert.equal(control.disabled,true);assert.equal(control.textContent,'正在加入 JM 收藏…');
  h.state.detail={...detail,id:'456'};
  resolve({success:true,favorited:true});await pending;
  assert.equal(control.disabled,false);assert.equal(h.state.detail.id,'456');assert.equal(h.state.detail.favorited,undefined);
});

test('网络不确定和并发冲突显示未知状态，重试只读取状态，不自动重发 POST',async()=>{
  for(const code of ['favorite_unknown','favorite_conflict']){
    const h=harness(),control=await open(h);let posts=0,reads=0;
    h.context.api=async(_path,options={})=>{
      if(options.method==='POST'){posts++;throw Object.assign(new Error('收藏结果未确认'),{code});}
      reads++;return {favorited:true};
    };
    await control.onclick();
    assert.equal(posts,1);assert.equal(control.textContent,'收藏状态未确认');assert.equal(control.disabled,false);
    assert.equal(h.toasts[0].message,'收藏结果未确认');assert.equal(h.toasts[0].options.tone,'error');assert.equal(h.refreshes,0);
    await control.onclick();assert.equal(reads,1);assert.equal(posts,1);assert.equal(control.textContent,'已收藏');
  }
});

test('状态查询失败或字段缺失不显示未收藏，重试也不写入',async()=>{
  for(const failure of [new Error('收藏查询失败'),{}]){
    const h=harness();let writes=0;
    h.context.api=async(_path,options={})=>{if(options.method==='POST')writes++;if(failure instanceof Error)throw failure;return failure;};
    const control=await open(h);assert.equal(control.textContent,'收藏状态未确认');assert.equal(control.disabled,false);
    h.context.api=async()=>({favorited:false});await control.onclick();
    assert.equal(control.textContent,'加入 JM 收藏');assert.equal(writes,0);
  }
});

test('查询登录失效提供绑定操作，未确认的添加响应不能误显示成功',async()=>{
  const h=harness();h.context.api=async()=>{throw Object.assign(new Error('会话失效'),{code:'login_required'});};
  const control=await open(h);
  assert.equal(control.textContent,'收藏状态未确认');assert.equal(h.toasts[0].options.action.label,'绑定 JM 账号');
  await h.toasts[0].options.action.run();assert.deepEqual(h.bindings,['jm']);
  h.context.api=async()=>({favorited:false});await control.onclick();
  h.context.api=async()=>({success:true});await control.onclick();
  assert.equal(control.textContent,'收藏状态未确认');assert.equal(h.toasts.at(-1).message,'禁漫未确认加入收藏');
});

test('拉取收藏更新本地表后，重开详情读取新状态而非旧按钮或书库缓存',async()=>{
  const h=harness();h.state.books=[{source:'jm',id:'123'}];let favorited=true;
  h.context.api=async()=>({favorited});
  const first=await open(h);assert.equal(first.textContent,'已收藏');
  favorited=false;
  const second=await open(h);assert.equal(second.textContent,'加入 JM 收藏');
});
