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
  constructor(tag) { this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.hidden=false;this.value='';this.className='';this.open=false;this.classList={add:()=>{},remove:()=>{},toggle:()=>{}}; }
  set textContent(value) { this.text=String(value);this.children=[]; }
  get textContent() { return (this.text||'')+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { if(!String(value).startsWith('<svg'))throw new Error('Labels must be rendered as text, not HTML');this.icon=value; }
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
  const sources=['jm','bika','copy','nhentai'].map(id=>({id,name:id,capabilities:['search','tag_search',...(id!=='copy'?['author_search']:[]),...(id==='bika'?['search_categories']:[])],search_sorts:id==='bika'?[{value:'dd',label:'最新'}]:[],tag_sorts:id==='copy'?[{value:'-popular',label:'热门'}]:[]}));
  const state={sources,search:null,searchMode:'keyword',searchCategories:{},searchCategoryRequests:{},searchCounts:null,view:'results',tab:'browse',detailRequest:0,discover:{}};
  const storage=new Map(),calls=[];
  const context=vm.createContext({state,loadResultFavorites(){},svgIcon:name=>`<svg data-icon="${name}"></svg>`,run:(action,...args)=>action(...args),document:{getElementById:id=>roots[id],createElement:tag=>new Element(tag)},
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

test('点击作者清除标签与分类，使用独立作者搜索并保留URL和历史',async()=>{
  const h=harness();h.context.startSearch('科幻','bika',{mode:'tag',category:'全彩',tag_value:'科幻'});
  h.state.search.page=4;h.state.tab='library';h.roots['detail-dialog'].open=true;h.roots['reader-dialog'].open=true;
  let closedReader=false;h.context.closeReader=()=>{closedReader=true;h.roots['reader-dialog'].open=false;};
  const author='作者 A&B / 名称',box=h.context.renderComicAuthors({authors:[author]});
  const href=new URL(box.querySelectorAll('a')[0].href,'http://test').searchParams;
  assert.equal(href.get('q'),author);assert.equal(href.get('mode'),'author');
  box.querySelectorAll('a')[0].onclick({button:0,ctrlKey:true,preventDefault(){throw new Error('Modified clicks belong to the browser');}});
  assert.equal(h.state.search.mode,'tag');
  await box.querySelectorAll('a')[0].onclick({button:0,preventDefault(){}});
  assert.equal(closedReader,true);assert.equal(h.roots['detail-dialog'].open,false);assert.equal(h.state.detailRequest,1);
  assert.equal(h.state.tab,'browse');assert.equal(h.state.search.keyword,author);
  assert.equal(h.state.search.mode,'author');assert.equal(h.state.search.source,'');
  assert.equal(h.state.search.page,1);assert.equal(h.state.search.category,'');assert.equal(h.state.search.tag_value,'');
  await h.context.loadSearch();
  const query=new URL(h.calls.at(-1),'http://test').searchParams;
  assert.equal(query.get('keyword'),author);assert.equal(query.get('mode'),'author');assert.equal(query.has('source'),false);
  assert.match(h.roots['results-title'].textContent,/作者/);
  const fresh=harness();fresh.context.location.search=h.context.viewURL();fresh.context.initBrowse();await flush();
  assert.equal(fresh.state.search.keyword,author);assert.equal(fresh.state.search.mode,'author');
  assert.equal(h.context.readRecent()[0].keyword,author);assert.equal(h.context.readRecent()[0].mode,'author');
  assert.match(h.roots['recent-list'].textContent,/作者 · /);
});

test('作者搜索仅显示支持的图源，切换搜索方式清理筛选与页码',async()=>{
  const h=harness();h.context.startSearch('作者','copy',{mode:'author',tag_value:'旧标签',sort:'-popular',page:3});
  assert.equal(h.state.search.source,'');assert.equal(h.state.search.page,1);assert.equal(h.state.search.tag_value,'');
  await h.context.loadSearch();
  const sources=h.roots.filters.children[0].querySelectorAll('button').map(button=>button.textContent);
  assert.deepEqual(sources,['全部0','jm0','bika0','nhentai0']);
  assert.equal(h.roots.keyword.attributes['aria-label'],'搜索漫画作者');
  assert.notEqual(h.context.searchKey({keyword:'作者',mode:'author'}),h.context.searchKey({keyword:'作者',mode:'keyword'}));
  const keyword=h.roots['search-mode'].querySelectorAll('button').find(button=>button.textContent==='关键词');
  keyword.onclick();await flush();
  assert.equal(h.state.search.mode,'keyword');assert.equal(new URL(h.calls.at(-1),'http://test').searchParams.has('mode'),false);
});

test('作者兼容数组与旧字符串，多作者分别点击，未知作者不生成按钮',()=>{
  const h=harness();
  assert.deepEqual(plain(h.context.comicAuthors({authors:[' A/B ',{name:'Bob'},'bob',7,null,{name:5}],author:'旧作者'})),['A/B','Bob']);
  assert.deepEqual(plain(h.context.comicAuthors({author:'Alice / Bob、Alice'})),['Alice','Bob']);
  for(const author of ['',null,'佚名','unknown','N/A'])assert.equal(h.context.renderComicAuthors({author}).querySelectorAll('a').length,0);
  const root=h.context.renderComicAuthors({authors:['<script>alert(1)</script>','作者二']});
  assert.equal(root.querySelectorAll('a').length,2);assert.match(root.textContent,/<script>/);
});

test('全部图源计数与各源计数分别展示，搜索请求不带去重参数',async()=>{
  const h=harness();let pagerCount;
  h.context.api=async path=>{h.calls.push(path);return {items:[{id:'1'},{id:'2'},{id:'3'}],all_results:{jm:[{},{}],bika:[{}]},errors:{}};};
  h.context.renderPager=count=>{pagerCount=count;};
  h.context.startSearch('测试','',{mode:'keyword'});await h.context.loadSearch();
  const chips=h.roots.filters.children[0].querySelectorAll('button');
  assert.equal(chips[0].textContent,'全部3');assert.equal(chips[1].textContent,'jm2');assert.equal(chips[2].textContent,'bika1');
  assert.match(h.roots['results-meta'].textContent,/3 部$/);assert.equal(pagerCount,3);
  assert.equal(new URL(h.calls.at(-1),'http://test').searchParams.has('deduplicate'),false);
  h.context.syncSearchMode();
  assert.equal(h.roots['search-mode'].querySelectorAll('button').filter(button=>button.attributes.role==='switch').length,0);
});

test('标签与关键词搜索计数不混用，分类筛选不显示聚合旧计数',()=>{
  const h=harness();h.context.startSearch('全彩','bika',{mode:'tag',category:'全彩'});
  h.state.searchCounts={key:h.context.searchKey(h.state.search),counts:{bika:99},errors:{}};
  h.context.renderFilters();assert.ok(!h.roots.filters.textContent.includes('99'));
  assert.notEqual(h.context.searchKey({keyword:'全彩',mode:'tag'}),h.context.searchKey({keyword:'全彩',mode:'keyword'}));
});

test('多标签与排除标签按服务端规则解析，只显示支持多标签的图源，分页依据上游返回数',async()=>{
  const h=harness();let pagerCount;
  assert.deepEqual(plain(h.context.tagQueryTerms('巨乳，眼鏡 -中文 "big breasts" -tag:"glasses" 巨乳 - +')),
    {include:['巨乳','眼鏡','big breasts'],exclude:['中文','tag:"glasses"']});
  assert.equal(h.context.searchCapability({keyword:'巨乳',mode:'tag'}),'tag_search');
  assert.equal(h.context.searchCapability({keyword:'巨乳 -中文',mode:'tag'}),'multi_tag_search');
  assert.equal(h.context.searchCapability({keyword:'巨乳 -中文',mode:'tag',tag_value:'巨乳'}),'tag_search');
  assert.equal(h.context.searchCapability({keyword:'巨乳 -中文',mode:'keyword'}),'search');
  for(const source of h.state.sources)if(source.id!=='copy')source.capabilities.push('multi_tag_search');
  h.context.api=async path=>{h.calls.push(path);return {items:[],upstream_count:20,all_results:{},errors:{}};};
  h.context.renderPager=count=>{pagerCount=count;};
  h.context.startSearch('巨乳 -中文','copy',{mode:'tag'});
  assert.equal(h.state.search.source,'');
  await h.context.loadSearch();
  const sources=h.roots.filters.children[0].querySelectorAll('button').map(button=>button.textContent);
  assert.deepEqual(sources,['全部0','jm0','bika0','nhentai0']);
  assert.equal(new URL(h.calls.at(-1),'http://test').searchParams.get('keyword'),'巨乳 -中文');
  assert.equal(pagerCount,20);
});

test('启用的图源都不支持多标签时提示改用单个标签，而不是提示没有启用图源',async()=>{
  const h=harness();let empty;
  vm.runInContext(app.slice(app.indexOf('function showNoEnabledSources('),app.indexOf('async function loadDiscover(')),h.context);
  h.context.showResults=(_items,title,hint)=>{empty=[title,hint];};
  h.context.startSearch('女性向 恋爱','',{mode:'tag'});await h.context.loadSearch();
  assert.equal(h.roots['results-title'].textContent,'已启用的图源不支持多标签或排除标签搜索');
  assert.match(empty[1],/只保留一个包含的标签/);assert.equal(h.calls.length,0);
});

test('标签模式把输入显示为可切换包含或排除的标签，编辑写回输入框并在结果页重新搜索',async()=>{
  const h=harness();h.context.syncClear=h.context.renderTagTerms;
  for(const source of h.state.sources)if(source.id!=='copy')source.capabilities.push('multi_tag_search');
  h.state.searchMode='tag';h.context.syncSearchMode();
  const box=()=>h.state.tagTermsBox,info=()=>box().children.at(-1);
  assert.match(box().textContent,/点击标签切换包含或排除/);
  assert.match(info().dataset.tipText,/标签前加 - 表示排除，含空格的标签用英文引号包裹；拷贝仅支持单个题材/);
  h.roots.keyword.value='巨乳 -中文 "big breasts" tag:"glasses"';h.context.renderTagTerms();
  const chips=box().children.slice(0,-1);
  assert.deepEqual(chips.map(chip=>chip.className),['tag-term','tag-term exclude','tag-term','tag-term']);
  assert.deepEqual(chips.map(chip=>chip.children[0].textContent),['巨乳','中文','big breasts','glasses']);
  // Home view: edits rewrite the input only.
  chips[0].children[0].onclick();
  assert.equal(h.roots.keyword.value,'-巨乳 -中文 "big breasts" tag:"glasses"');
  assert.equal(h.state.search,null);
  h.context.startSearch('巨乳 -中文 眼鏡','',{mode:'tag'});h.calls.length=0;
  box().children[1].children[0].onclick();await flush();
  assert.equal(h.roots.keyword.value,'巨乳 中文 眼鏡');
  assert.equal(h.state.search.keyword,'巨乳 中文 眼鏡');
  box().children[2].children[1].onclick();await flush();
  assert.equal(h.state.search.keyword,'巨乳 中文');
  // Excluding every tag leaves the query unsearchable, so the previous search stays.
  box().children[0].children[0].onclick();box().children[1].children[0].onclick();
  assert.equal(h.roots.keyword.value,'-巨乳 -中文');assert.equal(h.state.search.keyword,'-巨乳 中文');
  assert.equal(h.context.tagQueryText([{term:'a b',exclude:false},{term:'-x',exclude:true},{term:'tag:"c d"',exclude:false}]),'"a b" -"-x" tag:"c d"');
  h.state.searchMode='keyword';h.state.search.mode='keyword';h.context.syncSearchMode();
  assert.equal(h.state.tagTermsBox,null);
});
