const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');
const tags = readFileSync(join(__dirname, '../src/web/static/comic-tags.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag) { this.tagName=tag;this.children=[];this.attributes={};this.hidden=false;this.value='';this.className='';this.open=false;this.classList={add:()=>{},remove:()=>{},toggle:()=>{}}; }
  set textContent(value) { this.text=String(value);this.children=[]; }
  get textContent() { return (this.text||'')+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { throw new Error('Labels must be rendered as text, not HTML'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text='';this.children=children; }
  setAttribute(name,value) { this.attributes[name]=String(value); }
  querySelectorAll(selector) { return this.children.flatMap(child=>[...(child.tagName===selector?[child]:[]),...child.querySelectorAll(selector)]); }
  close() { this.open=false; }
  focus() {}
}
function harness() {
  const ids=['search-mode','keyword','filters','results-title','results-meta','browse-status','results','recent','recent-list','detail-dialog','reader-dialog'];
  const roots=Object.fromEntries(ids.map(id=>[id,new Element('div')]));
  const sources=['jm','bika','copy'].map(id=>({id,name:id,capabilities:['search','tag_search',...(id==='bika'?['search_categories']:[])],search_sorts:id==='bika'?[{value:'dd',label:'最新'}]:[],tag_sorts:id==='copy'?[{value:'-popular',label:'热门'}]:[]}));
  const state={sources,search:null,searchMode:'keyword',searchCategories:{},searchCategoryRequests:{},searchCounts:null,view:'results',tab:'browse',detailRequest:0,discover:{}};
  const storage=new Map(),calls=[];
  const context=vm.createContext({state,loadResultFavorites(){},document:{getElementById:id=>roots[id],createElement:tag=>new Element(tag)},
    $:id=>roots[id],enc:encodeURIComponent,URLSearchParams,location:{pathname:'/',search:''},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
    api:async path=>{calls.push(path);return path.includes('search-categories')?{categories:[{label:'全彩',value:'全彩'},{label:'新分类',value:'新分类'}]}:{items:[],all_results:{},errors:{}};},
    toast(){},syncHistory(){},syncClear(){},openResults(){},showTab:tab=>{state.tab=tab;},setView:view=>{state.view=view;},
    sourceName:id=>id,pick:(values,value)=>values.some(item=>item.value===value)?value:values[0]?.value||'',
    beginResults:()=>{state.browseAbort={signal:undefined};return ++state.browseRequest;},showResults(){},renderPager(){},showError(_target,error){throw error;},
    bindButton:()=>new Element('button'),matchMedia:()=>({matches:false}),
    renderQuick(){},renderContinue(){},loadShelves(){},BROWSE_ORDER:[],
  });
  state.browseRequest=0;
  vm.runInContext(tags,context);
  vm.runInContext(readFileSync(join(__dirname, '../src/web/static/comic-authors.js'), 'utf8'),context);
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('function readLocal('),app.indexOf('const readingKey=')),context);
  vm.runInContext(app.slice(app.indexOf('function sourcePlugin('),app.indexOf('function discoverActions(')),context);
  vm.runInContext(app.slice(app.indexOf('function chipGroup('),app.indexOf('// One pager')),context);
  vm.runInContext(app.slice(app.indexOf('async function loadSearch('),app.indexOf('function turnPage(')),context);
  vm.runInContext(app.slice(app.indexOf('function readRecent('),app.indexOf('function readHistory(')),context);
  vm.runInContext(app.slice(app.indexOf('function viewURL('),app.indexOf('function syncHistory(')),context);
  vm.runInContext(app.slice(app.indexOf('function initBrowse('),app.indexOf('function renderQuick(')),context);
  return {context,state,roots,storage,calls};
}

test('标签名称与检索值分别保留，兼容旧字符串并去重',()=>{
  const h=harness();
  assert.deepEqual(plain(h.context.comicLabels([' 全彩 ','全彩',{label:'科幻',value:'kehuan'},null,{},7])),[{label:'全彩',value:'全彩'},{label:'科幻',value:'kehuan'}]);
});

test('详情标签可搜索，分类保持独立；长列表可展开且上游文本不作为HTML',async()=>{
  const h=harness();
  const labels=Array.from({length:13},(_,i)=>({label:i===0?'<script>alert(1)</script>':`标签${i}`,value:`value${i}`}));
  const box=h.context.renderComicLabels({source:'copy',id:'book',tags:labels,categories:['分类']});
  assert.equal(box.children.length,2);
  assert.match(box.children[0].textContent,/<script>alert\(1\)<\/script>/);
  assert.equal(box.children[1].querySelectorAll('a').length,0);
  const buttons=box.children[0].querySelectorAll('a');
  assert.equal(buttons[12].hidden,true);
  // Links carry the same search as a click, so the browser can open it in a new tab.
  const tagHref=new URL(buttons[1].href,'http://test').searchParams;
  assert.equal(tagHref.get('q'),'标签1');assert.equal(tagHref.get('mode'),'tag');
  assert.equal(tagHref.get('tag_value'),'value1');assert.equal(tagHref.get('source'),'copy');
  await box.children[0].querySelectorAll('button').at(-1).onclick();
  assert.equal(buttons[12].hidden,false);
  h.roots['detail-dialog'].open=true;
  await buttons[1].onclick({button:0,preventDefault(){}});
  assert.equal(h.roots['detail-dialog'].open,false);
  assert.equal(h.state.search.mode,'tag');
  assert.equal(h.state.search.source,'copy');
  assert.equal(h.state.search.tag_value,'value1');
});

test('标签搜索调用独立mode并保留拷贝theme标识',async()=>{
  const h=harness();
  h.context.startSearch('科幻','copy',{mode:'tag',tag_value:'kehuan'});
  await h.context.loadSearch();
  const query=new URL(h.calls[0],'http://test').searchParams;
  assert.equal(query.get('mode'),'tag');assert.equal(query.get('tag_value'),'kehuan');
  assert.equal(query.get('keyword'),'科幻');assert.equal(query.get('source'),'copy');
  assert.equal(query.get('sort'),'-popular');
  assert.match(h.roots['results-title'].textContent,/标签/);
});

test('切换图源清理上一个图源的题材标识和哔咔分类',async()=>{
  const h=harness();
  h.context.startSearch('科幻','bika',{mode:'tag',tag_value:'本源标签',category:'全彩'});
  h.state.search.page=3;
  h.context.renderFilters();
  const sourceRow=h.roots.filters.children[0];
  const copy=sourceRow.querySelectorAll('button').find(button=>button.textContent==='copy');
  copy.onclick();await flush();
  assert.equal(h.state.search.source,'copy');
  assert.equal(h.state.search.page,1);assert.equal(h.state.search.category,'');assert.equal(h.state.search.tag_value,'');
  const last=new URL(h.calls.filter(path=>path.startsWith('/api/search?')).at(-1),'http://test');
  assert.equal(last.searchParams.has('category'),false);assert.equal(last.searchParams.has('tag_value'),false);
});

test('分类只在哔咔单源显示，选中后重置页码并传给关键词和标签搜索',async()=>{
  for(const mode of ['keyword','tag']){
    const h=harness();h.context.startSearch('职场','bika',{mode});
    await h.context.loadSearch();await flush();
    assert.equal(h.calls.filter(path=>path.endsWith('search-categories')).length,1);
    h.state.search.page=4;
    const fullColor=h.roots.filters.querySelectorAll('button').find(button=>button.textContent==='全彩');
    fullColor.onclick();await flush();
    assert.equal(h.state.search.page,1);assert.equal(h.state.search.category,'全彩');
    assert.ok(h.calls.some(path=>path.startsWith('/api/search?')&&new URL(path,'http://test').searchParams.get('category')==='全彩'));
    h.context.renderFilters();assert.equal(h.calls.filter(path=>path.endsWith('search-categories')).length,1);
    h.context.startSearch('职场','jm',{mode,category:'全彩'});h.context.renderFilters();
    assert.equal(h.state.search.category,'');
    assert.ok(!h.roots.filters.textContent.includes('全部分类'));
    h.context.startSearch('职场','',{mode});h.context.renderFilters();
    assert.ok(!h.roots.filters.textContent.includes('全部分类'));
  }
});

test('分类目录失败明确显示并可重试，不影响漫画搜索',async()=>{
  const h=harness();let fail=true;
  h.context.api=async path=>{if(path.endsWith('search-categories')){if(fail)throw new Error('分类网络失败');return {categories:[{label:'恢复分类',value:'恢复分类'}]};}return {items:[],errors:{}};};
  h.context.startSearch('测试','bika',{mode:'keyword'});await h.context.loadSearch();await flush();
  assert.match(h.roots.filters.textContent,/分类获取失败：分类网络失败/);
  fail=false;
  const retry=h.roots.filters.querySelectorAll('button').find(button=>button.textContent==='重试');
  await retry.onclick();await flush();
  assert.match(h.roots.filters.textContent,/恢复分类/);
  assert.ok(!h.roots.filters.textContent.includes('分类获取失败'));
});

test('修改搜索词不携带旧题材标识，分类仅保留在哔咔',()=>{
  const h=harness();h.context.startSearch('科幻','copy',{mode:'tag',tag_value:'kehuan'});
  h.roots.keyword.value='冒险';h.context.browse({preventDefault(){}});
  assert.equal(h.state.search.keyword,'冒险');assert.equal(h.state.search.tag_value,'');
  h.context.startSearch('科幻','bika',{mode:'keyword',category:'全彩'});
  h.roots.keyword.value='职场';h.context.browse({preventDefault(){}});
  assert.equal(h.state.search.category,'全彩');
});

test('搜索URL、刷新及最近搜索保留模式、分类和题材值，兼容旧历史',async()=>{
  const h=harness();
  h.storage.set('comic:recent',JSON.stringify(['旧关键词']));
  h.context.startSearch('职场','bika',{mode:'tag',tag_value:'職場',category:'全彩'});
  h.state.search.page=2;
  const url=h.context.viewURL();
  assert.equal(new URL(url,'http://test').searchParams.get('tag_value'),'職場');
  const fresh=harness();fresh.context.location.search=url;
  fresh.context.initBrowse();await flush();
  assert.equal(fresh.state.search.mode,'tag');assert.equal(fresh.state.search.category,'全彩');
  assert.equal(fresh.state.search.tag_value,'職場');assert.equal(fresh.state.search.page,2);
  const recent=plain(h.context.readRecent());
  assert.equal(recent[0].mode,'tag');assert.equal(recent[0].category,'全彩');
  assert.equal(recent[1].keyword,'旧关键词');assert.equal(recent[1].mode,'keyword');
});

test('切换为关键词模式清理标签标识及标签排序',async()=>{
  const h=harness();h.context.startSearch('科幻','copy',{mode:'tag',tag_value:'kehuan'});
  const keyword=h.roots['search-mode'].querySelectorAll('button').find(button=>button.textContent==='关键词');
  keyword.onclick();await flush();
  assert.equal(h.state.search.mode,'keyword');assert.equal(h.state.search.tag_value,'');assert.equal(h.state.search.sort,'');
  const query=new URL(h.calls.at(-1),'http://test').searchParams;
  assert.equal(query.has('mode'),false);assert.equal(query.has('tag_value'),false);
});

test('延迟返回的哔咔分类目录不会覆盖已切换的图源界面',async()=>{
  const h=harness();let finish;
  h.context.api=async path=>path.endsWith('search-categories')?new Promise(resolve=>{finish=resolve;}):{items:[],errors:{}};
  h.context.startSearch('测试','bika',{mode:'tag'});await h.context.loadSearch();
  h.context.startSearch('测试','jm',{mode:'tag'});await h.context.loadSearch();
  finish({categories:[{label:'延迟分类',value:'延迟分类'}]});await flush();
  assert.equal(h.state.search.source,'jm');assert.ok(!h.roots.filters.textContent.includes('延迟分类'));
  assert.equal(h.state.searchCategories.bika.items[0].label,'延迟分类');
});

test('点击作者清除标签与分类，在全部图源重新搜索并保留URL和历史',async()=>{
  const h=harness();h.context.startSearch('科幻','bika',{mode:'tag',category:'全彩',tag_value:'科幻'});
  h.state.search.page=4;h.state.tab='library';h.roots['detail-dialog'].open=true;h.roots['reader-dialog'].open=true;
  let closedReader=false;h.context.closeReader=()=>{closedReader=true;h.roots['reader-dialog'].open=false;};
  const author='作者 A&B / 名称',box=h.context.renderComicAuthors({authors:[author]});
  assert.equal(new URL(box.querySelectorAll('a')[0].href,'http://test').searchParams.get('q'),author);
  await box.querySelectorAll('a')[0].onclick({button:0,preventDefault(){}});
  assert.equal(closedReader,true);assert.equal(h.roots['detail-dialog'].open,false);assert.equal(h.state.detailRequest,1);
  assert.equal(h.state.tab,'browse');assert.equal(h.state.search.keyword,author);
  assert.equal(h.state.search.mode,'keyword');assert.equal(h.state.search.source,'');
  assert.equal(h.state.search.page,1);assert.equal(h.state.search.category,'');assert.equal(h.state.search.tag_value,'');
  await h.context.loadSearch();
  const query=new URL(h.calls.at(-1),'http://test').searchParams;
  assert.equal(query.get('keyword'),author);assert.equal(query.has('mode'),false);assert.equal(query.has('source'),false);
  const fresh=harness();fresh.context.location.search=h.context.viewURL();fresh.context.initBrowse();await flush();
  assert.equal(fresh.state.search.keyword,author);assert.equal(fresh.state.search.mode,'keyword');
  assert.equal(h.context.readRecent()[0].keyword,author);
});

test('作者兼容数组与旧字符串，多作者分别点击，未知作者不生成按钮',()=>{
  const h=harness();
  assert.deepEqual(plain(h.context.comicAuthors({authors:[' A/B ',{name:'Bob'},'bob',7,null,{name:5}],author:'旧作者'})),['A/B','Bob']);
  assert.deepEqual(plain(h.context.comicAuthors({author:'Alice / Bob、Alice'})),['Alice','Bob']);
  for(const author of ['',null,'佚名','unknown','N/A'])assert.equal(h.context.renderComicAuthors({author}).querySelectorAll('a').length,0);
  const root=h.context.renderComicAuthors({authors:['<script>alert(1)</script>','作者二']});
  assert.equal(root.querySelectorAll('a').length,2);assert.match(root.textContent,/<script>/);
});

test('合并数量、全部图源计数与原始源计数分别展示，分页依据原始返回数',async()=>{
  const h=harness();let pagerCount;
  h.context.api=async()=>({items:[{id:'1'}],raw_count:3,duplicate_count:2,all_results:{jm:[{},{}],bika:[{}]},errors:{}});
  h.context.renderPager=count=>{pagerCount=count;};
  h.context.startSearch('测试','',{mode:'keyword',deduplicate:true});await h.context.loadSearch();
  const chips=h.roots.filters.children[0].querySelectorAll('button');
  assert.equal(chips[0].textContent,'全部1');assert.equal(chips[1].textContent,'jm2');assert.equal(chips[2].textContent,'bika1');
  assert.match(chips[1].title,/合并前/);assert.match(h.roots['results-meta'].textContent,/1 部 · 合并 2 项重复结果/);
  assert.equal(pagerCount,3);
});

test('结果去重默认关闭，首页开关只改变选择，启用后搜索才传去重参数',async()=>{
  const h=harness();h.context.syncSearchMode();
  const toggle=()=>h.roots['search-mode'].querySelectorAll('button').find(button=>button.attributes.role==='switch');
  assert.equal(toggle().attributes['aria-checked'],'false');assert.match(toggle().textContent,/关闭/);
  assert.equal(h.context.searchParams({keyword:'测试'}).has('deduplicate'),false);
  await toggle().onclick();assert.equal(h.calls.length,0);assert.equal(toggle().attributes['aria-checked'],'true');
  h.context.startSearch('测试');await h.context.loadSearch();
  assert.equal(new URL(h.calls.at(-1),'http://test').searchParams.get('deduplicate'),'true');
  assert.equal(h.context.readRecent()[0].deduplicate,true);
  h.state.search.page=4;await toggle().onclick();
  assert.equal(h.state.search.page,1);assert.equal(h.state.search.deduplicate,false);
  assert.equal(new URL(h.calls.at(-1),'http://test').searchParams.has('deduplicate'),false);
  assert.equal(toggle().attributes['aria-checked'],'false');
});

test('去重开关保留搜索方式和筛选，作者搜索沿用当前选择，显式关闭可覆盖选择',async()=>{
  const h=harness();h.context.startSearch('测试','bika',{mode:'tag',category:'全彩',tag_value:'原始标签'});
  const toggle=h.roots['search-mode'].querySelectorAll('button').find(button=>button.attributes.role==='switch');
  await toggle.onclick();
  const query=new URL(h.calls.filter(path=>path.startsWith('/api/search?')).at(-1),'http://test').searchParams;
  assert.equal(query.get('deduplicate'),'true');assert.equal(query.get('mode'),'tag');
  assert.equal(query.get('category'),'全彩');assert.equal(query.get('tag_value'),'原始标签');
  h.context.searchComicAuthor('作者');assert.equal(h.state.search.deduplicate,true);assert.equal(h.state.search.mode,'keyword');
  h.context.startSearch('测试','',{deduplicate:false});assert.equal(h.state.search.deduplicate,false);
});

test('去重状态随搜索链接和历史恢复，旧链接默认关闭，计数缓存隔离',async()=>{
  const h=harness();h.context.startSearch('测试','',{deduplicate:true});h.state.search.page=2;
  const url=h.context.viewURL();assert.equal(new URL(url,'http://test').searchParams.get('deduplicate'),'true');
  const fresh=harness();fresh.context.location.search=url;fresh.context.initBrowse();await flush();
  assert.equal(fresh.state.search.deduplicate,true);assert.equal(fresh.state.search.page,2);
  const recent=h.context.readRecent()[0];fresh.context.startSearch(recent.keyword,recent.source,recent);
  assert.equal(fresh.state.search.deduplicate,true);
  for(const query of ['?q=旧搜索','?q=测试&deduplicate=false']){
    fresh.context.location.search=query;fresh.context.initBrowse();await flush();assert.equal(fresh.state.search.deduplicate,false);
  }
  assert.notEqual(h.context.searchKey({keyword:'测试',deduplicate:true}),h.context.searchKey({keyword:'测试'}));
});

test('切换去重后旧请求的原始结果不会覆盖新结果',async()=>{
  const h=harness();let finishOld,shown;
  h.context.api=path=>new URL(path,'http://test').searchParams.get('deduplicate')==='true'
    ?Promise.resolve({items:[{id:'合并结果'}],raw_count:3,duplicate_count:2,all_results:{jm:[{},{},{}]},errors:{}})
    :new Promise(resolve=>{finishOld=resolve;});
  h.context.showResults=items=>{shown=items;};h.context.startSearch('测试');
  const old=h.context.loadSearch();
  await h.roots['search-mode'].querySelectorAll('button').find(button=>button.attributes.role==='switch').onclick();
  finishOld({items:[{id:'旧结果'},{id:'旧结果2'},{id:'旧结果3'}],raw_count:3,duplicate_count:0,all_results:{jm:[{},{},{}]},errors:{}});
  await old;assert.equal(shown[0].id,'合并结果');assert.equal(shown.length,1);
  assert.match(h.roots['results-meta'].textContent,/合并 2 项重复结果/);
});

test('标签与关键词搜索计数不混用，分类筛选不显示聚合旧计数',()=>{
  const h=harness();h.context.startSearch('全彩','bika',{mode:'tag',category:'全彩'});
  h.state.searchCounts={key:h.context.searchKey(h.state.search),counts:{bika:99},errors:{}};
  h.context.renderFilters();assert.ok(!h.roots.filters.textContent.includes('99'));
  assert.notEqual(h.context.searchKey({keyword:'全彩',mode:'tag'}),h.context.searchKey({keyword:'全彩',mode:'keyword'}));
});
