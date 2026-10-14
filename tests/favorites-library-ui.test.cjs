const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname,'../src/web/static/app.js'),'utf8');

class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.disabled=false;this.value='';}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return (this.text||'')+this.children.map(child=>child.textContent).join('');}
  set innerHTML(value){this.html=value;this.text='';this.children=[];}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.text='';this.children=children;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  querySelectorAll(tag){return this.children.flatMap(child=>[...(child.tagName===tag?[child]:[]),...child.querySelectorAll(tag)]);}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(source='jm'){
  const plugin={id:source,name:source==='jm'?'JM':'哔咔',account:{configured:true}},calls=[],toasts=[],roots={};
  const state={books:[],categories:[]};let reloads=0;
  const remote={settings:{enabled:false,interval_minutes:60},category:source==='jm'?'JM 收藏':'哔咔收藏',current:{status:'idle'},active:false,paused:false,next_run_at:null};
  const root=id=>roots[id]||(roots[id]=new Element('div'));
  const context=vm.createContext({state,document:{createElement:tag=>new Element(tag)},$:root,enc:encodeURIComponent,
    svgIcon:name=>`<svg>${name}</svg>`,toast:(message,options)=>toasts.push({message,options}),write:(method,data)=>({method,body:JSON.stringify(data)}),
    api:async(path,options={})=>{calls.push({path,options});if(options.method==='PUT')remote.settings=JSON.parse(options.body);return structuredClone(remote);},
    loadLibrary:async()=>{reloads++;},readingItems:()=>[],readingKey:b=>b.source+':'+b.id,options(){},
    emptyState:title=>{const el=new Element('p');el.textContent=title;return el;},
    card:book=>{const el=new Element('article');el.book=book;return el;},showTab(){},
    renderKeyed:(box,items,_key,_signature,build)=>{const nodes=items.map(build);box.replaceChildren(...nodes);return nodes;},
  });
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('function favoriteLibrarySettings('),app.indexOf('function configureAccount(')),context);
  return {context,plugin,calls,toasts,remote,state,root,get reloads(){return reloads;}};
}
function toggle(section){return section.querySelectorAll('button').find(button=>button.attributes.role==='switch');}
function control(section,text){return section.querySelectorAll('button').find(button=>button.textContent===text);}

for(const source of ['jm','bika']){
  test(`${source} 默认关闭，有无障碍开关，读取设置不触发同步`,async()=>{
    const h=harness(source),section=h.context.favoriteLibrarySettings(h.plugin);await flush();
    assert.equal(toggle(section).attributes['aria-checked'],'false');assert.equal(toggle(section).disabled,false);
    assert.match(toggle(section).attributes['aria-label'],/收藏自动同步到书库/);assert.match(toggle(section).html,/toggle-left/);
    assert.equal(control(section,'立即同步').disabled,true);
    assert.equal(h.calls.length,1);assert.equal(h.calls[0].path,`/api/sources/${source}/library-sync`);
    assert.equal(h.calls[0].options.cache,'no-store');assert.match(section.textContent,/仅同步书目信息，不下载图片/);
    assert.match(section.textContent,/60 分钟/);assert.ok(section.textContent.includes(h.remote.category));
  });

  test(`${source} 保存成功才翻转开关，发送布尔值并刷新书库分类`,async()=>{
    const h=harness(source),section=h.context.favoriteLibrarySettings(h.plugin);await flush();
    const old=toggle(section);let resolve;
    h.context.api=(path,options)=>{h.calls.push({path,options});return new Promise(done=>{resolve=done;});};
    const pending=old.onclick();assert.equal(old.disabled,true);assert.equal(old.attributes['aria-checked'],'false');
    h.remote.settings.enabled=true;resolve(structuredClone(h.remote));await pending;
    assert.equal(toggle(section).attributes['aria-checked'],'true');assert.match(toggle(section).html,/toggle-right/);
    assert.deepEqual(JSON.parse(h.calls[1].options.body),{enabled:true,interval_minutes:60});assert.equal(h.calls[1].options.method,'PUT');
    assert.equal(h.reloads,1);assert.equal(h.toasts[0].message,'书库同步设置已保存');
  });
}

test('保存失败保留原开关状态并报告错误',async()=>{
  const h=harness(),section=h.context.favoriteLibrarySettings(h.plugin);await flush();
  h.context.api=async()=>{throw new Error('保存失败');};await toggle(section).onclick();
  assert.equal(toggle(section).attributes['aria-checked'],'false');assert.equal(toggle(section).disabled,false);
  assert.equal(h.toasts[0].message,'保存失败');assert.equal(h.toasts[0].options.tone,'error');assert.equal(h.reloads,0);
});

test('设置读取失败显示重试，不把错误显示为默认关闭',async()=>{
  const h=harness(),original=h.context.api;h.context.api=async()=>{throw new Error('读取失败');};
  const section=h.context.favoriteLibrarySettings(h.plugin);await flush();
  assert.match(section.textContent,/读取失败/);assert.equal(toggle(section),undefined);
  h.context.api=original;await control(section,'重新读取').onclick();assert.equal(toggle(section).attributes['aria-checked'],'false');
});

test('已暂停图源仍可关闭旧设置，但不能开启或立即同步',async()=>{
  const h=harness();h.plugin.enabled=false;h.remote.paused=true;h.remote.settings.enabled=true;
  const section=h.context.favoriteLibrarySettings(h.plugin);await flush();
  assert.equal(toggle(section).disabled,false);assert.equal(control(section,'立即同步').disabled,true);assert.match(section.textContent,/自动同步暂停/);
  await toggle(section).onclick();assert.equal(toggle(section).attributes['aria-checked'],'false');assert.equal(toggle(section).disabled,true);
});

test('未绑定账号不可开启同步',async()=>{
  const h=harness();h.plugin.account={};const section=h.context.favoriteLibrarySettings(h.plugin);await flush();
  assert.equal(toggle(section).disabled,true);assert.match(section.textContent,/绑定账号后可开启/);
});

test('运行中可关闭，手动运行不能重入，刷新显示部分失败',async()=>{
  const h=harness();h.remote.settings.enabled=true;h.remote.active=true;h.remote.current={status:'running',total:3,processed:1,added:1};
  const section=h.context.favoriteLibrarySettings(h.plugin);await flush();
  assert.equal(toggle(section).disabled,false);assert.equal(control(section,'立即同步').disabled,true);assert.match(section.textContent,/已处理 1\/3 本/);
  h.remote.active=false;h.remote.current={status:'partial',total:3,processed:3,added:2,failed:1,errors:[{comic_id:'3',message:'详情失败'}]};
  await control(section,'刷新状态').onclick();
  assert.match(section.textContent,/部分完成/);assert.match(section.textContent,/3：详情失败/);assert.equal(control(section,'立即同步').disabled,false);
  await control(section,'立即同步').onclick();assert.equal(h.calls.at(-1).path,'/api/sources/jm/library-sync/run');assert.equal(h.calls.at(-1).options.method,'POST');
});

function libraryHarness(){
  const h=harness();
  vm.runInContext(app.slice(app.indexOf('function bookCategories('),app.indexOf('const inLibrary=')),h.context);
  h.state.books=[
    {source:'jm',id:'1',title:'甲',author:'作者一',library_category:'追更',library_categories:['JM 收藏','追更']},
    {source:'jm',id:'2',title:'乙',author:'作者二',library_category:'',library_categories:['JM 收藏']},
    {source:'bika',id:'3',title:'丙',library_category:'',library_categories:['哔咔收藏']},
    {source:'jm',id:'4',title:'丁',library_category:'',library_categories:[]},
  ];
  return h;
}

test('书库按手动与自动分类并集筛选，未分类不包含自动归类漫画',()=>{
  const h=libraryHarness();
  const filter=value=>{h.root('library-category').value=value;h.context.filterBooks();return h.root('books').children.map(el=>el.book?.id);};
  assert.deepEqual(filter('JM 收藏'),['1','2']);assert.deepEqual(filter('追更'),['1']);
  assert.deepEqual(filter('哔咔收藏'),['3']);assert.deepEqual(filter(''),['4']);assert.deepEqual(filter('*'),['1','2','3','4']);
  h.root('library-filter').value='作者二';assert.deepEqual(filter('JM 收藏'),['2']);
});

test('兼容旧书库响应，手动分类编辑不提交自动分类关联',async()=>{
  const h=libraryHarness(),patches=[],prompts=[];
  assert.deepEqual(Array.from(h.context.bookCategories({library_category:'旧分类'})),['旧分类']);
  assert.deepEqual(Array.from(h.context.bookCategories({library_category:''})),[]);
  h.context.ask=async options=>{prompts.push(options);return '';};h.context.bookPath=book=>`/api/library/${book.source}/${book.id}`;
  h.context.api=async(path,options)=>patches.push({path,options});h.context.loadLibrary=async()=>{};
  vm.runInContext(app.slice(app.indexOf('async function editCategory('),app.indexOf('/* ---------- library')),h.context);
  await h.context.editCategory(h.state.books[0]);
  assert.equal(prompts[0].value,'追更');assert.match(prompts[0].message,/手动分类不影响收藏同步分类/);
  assert.deepEqual(JSON.parse(patches[0].options.body),{category:''});assert.equal(h.toasts[0].message,'手动分类已清除');
});

test('列表按键复用未变化的卡片节点，只重建变化项并按新顺序排列',()=>{
  class Box{
    constructor(){this.children=[];}
    insertBefore(el,ref){const i=this.children.indexOf(el);if(i>=0)this.children.splice(i,1);el.parent=this;const at=ref?this.children.indexOf(ref):-1;at<0?this.children.push(el):this.children.splice(at,0,el);}
    replaceChildren(...nodes){this.children=[];for(const el of nodes)this.insertBefore(el,null);}
  }
  const card=id=>({id,remove(){this.parent.children.splice(this.parent.children.indexOf(this),1);}});
  const context=vm.createContext({location:{origin:'http://host'},URL});
  vm.runInContext(app.slice(app.indexOf('function coverKey('),app.indexOf('function skeletons(')),context);
  const box=new Box();let builds=0;
  const render=items=>context.renderKeyed(box,items,item=>item.id,item=>[item.title],item=>{builds++;return card(item.id);});
  render([{id:'1',title:'甲'},{id:'2',title:'乙'},{id:'3',title:'丙'}]);
  const [first,second]=box.children;assert.equal(builds,3);
  render([{id:'2',title:'乙'},{id:'1',title:'甲'},{id:'3',title:'丙改'}]);
  assert.equal(builds,4);assert.deepEqual(box.children.map(el=>el.id),['2','1','3']);
  assert.equal(box.children[0],second);assert.equal(box.children[1],first);
  render([{id:'1',title:'甲'}]);assert.deepEqual(box.children.map(el=>el.id),['1']);assert.equal(box.children[0],first);
  assert.equal(context.coverKey('/api/image/proxy?source=jm&url=https%3A%2F%2Fcdn-a.example%2Fmedia%2Falbums%2F1_3x4.jpg'),
    context.coverKey('/api/image/proxy?source=jm&url=https%3A%2F%2Fcdn-b.example%2Fmedia%2Falbums%2F1_3x4.jpg'));
});
