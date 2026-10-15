const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname,'../src/web/static/app.js'),'utf8');

class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.className='';this.hidden=false;this.disabled=false;this.open=false;this.value='';this.classes=new Set();this.classList={add:name=>this.classes.add(name),remove:(...names)=>names.forEach(name=>this.classes.delete(name)),toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return (this.text||'')+this.children.map(child=>child.textContent).join('');}
  set innerHTML(value){this.html=value;this.text='';this.children=[];}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.text='';this.children=children;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  close(){this.open=false;}
  querySelectorAll(tag){return this.children.flatMap(child=>[...(child.tagName===tag?[child]:[]),...child.querySelectorAll(tag)]);}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
  const sources=['jm','bika','copy'].map(id=>({id,name:id,capabilities:['search','tag_search','latest',...(id==='copy'?[]:['favorites','login'])],account:{authenticated:true},settings_fields:[]}));
  const state={sources,tab:'browse',view:'home',browseReady:true,health:{},healthDots:{},searchCategories:{},searchCategoryRequests:{},search:null,discover:{source:'bika',page:3},books:[{source:'bika',id:'saved'}],resultItems:[{source:'bika',id:'old'}],browseRequest:0,shelfRequest:0,detailRequest:0};
  const roots={},calls=[],toasts=[],loads=[],root=id=>roots[id]||(roots[id]=new Element('div'));
  const tabs=['jm','bika'].flatMap(source=>Array.from({length:2},()=>{const control=new Element('button');control.dataset.tab=`${source}-favorites`;return control;}));
  const context=vm.createContext({state,$:root,enc:encodeURIComponent,URLSearchParams,AbortController,setTimeout,clearTimeout,
    document:{createElement:tag=>new Element(tag),createTextNode:text=>{const n=new Element('text');n.textContent=text;return n;},querySelectorAll:selector=>tabs.filter(tab=>selector===`[data-tab="${tab.dataset.tab}"]`)},
    svgIcon:name=>`<svg>${name}</svg>`,toast:(message,options)=>toasts.push({message,options}),sourceName:id=>id,write:(method,data)=>({method,body:JSON.stringify(data)}),
    api:async(path,options)=>{calls.push({path,options});const id=path.split('/')[3];return path==='/api/sources'?{sources:state.sources.map(p=>({...p}))}:{success:true,source:{...state.sources.find(p=>p.id===id),enabled:JSON.parse(options.body).enabled}};},
    needsLogin:()=>false,renderSourceSettings(){},initBrowse(){},showTab:tab=>{state.tab=tab;},renderQuick(){},renderContinue(){},filterBooks(){},syncSearchMode(){},renderFilters(){},
    loadShelves:async()=>loads.push(context.activeSources().map(p=>p.id)),loadSearch:async()=>loads.push('search'),loadDiscover:async()=>loads.push('discover'),
    loadJmFavoritesView:async()=>loads.push('jm'),loadBikaFavoritesView:async()=>loads.push('bika'),syncJmSelection(){},
    jm:{request:0,items:[],selected:new Set()},bikaFavorites:{request:0,items:[]},chapterPreviews:{stop:()=>loads.push('previews-stop')},closeReader:()=>{root('reader-dialog').close();loads.push('reader-close');},
    pick:(values,value)=>values.some(v=>v.value===value)?value:values[0]?.value||'',BROWSE_ORDER:['latest'],CAP_LABELS:{search:'搜索',favorites:'收藏夹'},
  });
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('async function loadSources('),app.indexOf('function needsLogin(')),context);
  vm.runInContext(app.slice(app.indexOf('function normalizeSearch('),app.indexOf('function searchCategoryFilter(')),context);
  vm.runInContext(app.slice(app.indexOf('function accountLabel('),app.indexOf('function renderSourceSettings(')),context);
  return {context,state,roots,root,tabs,calls,toasts,loads};
}

test('管理列表保留关闭图源，开关有可访问角色、状态和 SVG 图标',()=>{
  const h=harness();h.state.sources[1].enabled=false;h.context.renderSourceList();
  assert.equal(h.root('source-list').children.length,3);
  const switches=h.root('source-list').querySelectorAll('button').filter(button=>button.attributes.role==='switch');
  assert.deepEqual(switches.map(button=>button.attributes['aria-checked']),['true','false','true']);
  assert.equal(switches[1].textContent,'已关闭');assert.match(switches[1].html,/toggle-left/);
  assert.equal(h.state.healthDots.bika.title,'图源已关闭');assert.match(switches[1].attributes['aria-label'],/bika/);
});

test('开关等待服务端确认，成功后隐藏入口并保留本地书库',async()=>{
  const h=harness();let resolve;h.context.api=()=>new Promise(done=>{resolve=done;});
  const control=h.context.sourceToggle(h.state.sources[1]),pending=control.onclick();
  assert.equal(control.disabled,true);assert.notEqual(h.state.sources[1].enabled,false);
  resolve({source:{...h.state.sources[1],enabled:false}});await pending;
  assert.equal(h.state.sources[1].enabled,false);assert.equal(control.disabled,false);
  assert.ok(h.tabs.filter(tab=>tab.dataset.tab==='bika-favorites').every(tab=>tab.hidden));
  assert.equal(h.state.books.length,1);assert.equal(h.state.resultItems.length,0);assert.equal(h.state.discover.page,1);
  assert.deepEqual(Array.from(h.context.activeSources(),p=>p.id),['jm','copy']);assert.equal(h.toasts.at(-1).message,'bika已关闭');
});

test('保存失败不翻转开关或清空原页面，错误明确展示',async()=>{
  const h=harness();h.context.api=async()=>{throw new Error('保存失败');};
  const control=h.context.sourceToggle(h.state.sources[1]);await control.onclick();
  assert.notEqual(h.state.sources[1].enabled,false);assert.equal(h.state.resultItems.length,1);assert.equal(control.disabled,false);
  assert.equal(h.toasts[0].message,'保存失败');assert.equal(h.toasts[0].options.tone,'error');
});

test('重新启用发送布尔值并恢复导航，不依赖浏览器本地存储',async()=>{
  const h=harness();h.state.sources[1].enabled=false;h.context.syncSourceNavigation();
  await h.context.sourceToggle(h.state.sources[1]).onclick();
  assert.equal(h.calls[0].path,'/api/sources/bika/state');assert.deepEqual(JSON.parse(h.calls[0].options.body),{enabled:true});
  assert.equal(h.calls[0].options.method,'PUT');assert.equal(h.state.sources[1].enabled,true);
  assert.ok(h.tabs.filter(tab=>tab.dataset.tab==='bika-favorites').every(tab=>!tab.hidden));
});

test('关闭正在显示的收藏和漫画时清理迟到请求、预览及阅读器',async()=>{
  const h=harness();h.state.tab='bika-favorites';h.state.detailItem={source:'bika'};h.state.detail={source:'bika'};
  h.root('detail-dialog').open=h.root('reader-dialog').open=true;
  await h.context.setSourceEnabled('bika',false);
  assert.equal(h.state.tab,'browse');assert.equal(h.state.detail,null);assert.equal(h.root('detail-dialog').open,false);assert.equal(h.root('reader-dialog').open,false);
  assert.equal(h.state.detailRequest,1);assert.ok(h.context.bikaFavorites.request>0);assert.ok(h.context.jm.request>0);
  assert.ok(h.loads.includes('previews-stop')&&h.loads.includes('reader-close'));
});

test('关闭的单源搜索清除专属筛选并回到聚合搜索',()=>{
  const h=harness();h.state.sources[1].enabled=false;
  h.state.search={source:'bika',mode:'tag',keyword:'标签',page:5,sort:'dd',category:'全彩',tag_value:'tag'};
  h.context.normalizeSearch();
  assert.deepEqual(JSON.parse(JSON.stringify(h.state.search)),{source:'',mode:'tag',keyword:'标签',page:1,sort:'',category:'',tag_value:'',deduplicate:false});
  assert.deepEqual(Array.from(h.context.browsable(),p=>p.id),['jm','copy']);
});

test('全部关闭后没有可浏览图源，健康检查和分类请求不发送',async()=>{
  const h=harness();h.state.sources.forEach(p=>p.enabled=false);
  assert.equal(h.context.browsable().length,0);h.context.runHealthChecks(true);h.context.loadSearchCategories('bika');await flush();
  assert.equal(h.calls.length,0);
});

test('关闭后的迟到健康响应不会把状态重新标成正常',async()=>{
  const h=harness();let resolve;h.context.api=()=>new Promise(done=>{resolve=done;});
  const pending=h.context.checkSourceHealth('bika');assert.equal(h.state.health.bika,'checking');
  h.state.sources[1].enabled=false;h.context.refreshSourceViews();resolve({});await pending;
  assert.equal(h.state.health.bika,undefined);h.context.renderSourceList();assert.equal(h.state.healthDots.bika.title,'图源已关闭');
});

test('迟到的图源清单不能覆盖刚保存的开关状态',async()=>{
  const h=harness(),oldSources=h.state.sources.map(p=>({...p}));let resolve;
  const request=h.context.api;h.context.api=()=>new Promise(done=>{resolve=done;});
  const pending=h.context.loadSources();h.context.api=request;
  await h.context.setSourceEnabled('bika',false);resolve({sources:oldSources});await pending;
  assert.equal(h.state.sources[1].enabled,false);
});

test('重新打开管理列表取得服务器状态变化并更新当前视图',async()=>{
  const h=harness();h.context.api=async()=>({sources:h.state.sources.map(p=>({...p,enabled:p.id!=='bika'}))});
  await h.context.loadSources();
  assert.equal(h.state.sources[1].enabled,false);assert.equal(h.state.resultItems.length,0);
  assert.ok(h.tabs.filter(tab=>tab.dataset.tab==='bika-favorites').every(tab=>tab.hidden));
});
