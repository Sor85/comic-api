const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');

class Element {
  constructor(tag) { this.tagName=tag;this.children=[];this.attributes={};this.className='';this.style={setProperty(){}}; }
  set textContent(value) { this.text=String(value);this.children=[]; }
  get textContent() { return (this.text||'')+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { assert.equal(value,'<svg></svg>'); }
  setAttribute(name,value) { this.attributes[name]=String(value); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text='';this.children=children; }
  querySelectorAll(selector) {
    return this.children.flatMap(child=>[...(selector===child.tagName||selector.startsWith('.')&&child.className.split(' ').includes(selector.slice(1))?[child]:[]),...child.querySelectorAll(selector)]);
  }
}
function harness(){
  const opened=[],searches=[],resumed=[];
  const roots={'reader-dialog':{open:false},'detail-dialog':{open:false}};
  const context=vm.createContext({document:{createElement:tag=>new Element(tag)},$:id=>roots[id],state:{detailRequest:0},
    openComic:(...args)=>opened.push(args),resumeComic:item=>resumed.push(item),sourceName:id=>id,readLocal:()=>null,readingLast:()=>null,
    coverBox:()=>new Element('span'),libraryActions:()=>new Element('div'),svgIcon:()=>'<svg></svg>',
    startSearch:(...args)=>searches.push(args),toast:message=>{throw new Error(message);},
  });
  vm.runInContext(readFileSync(join(__dirname, '../src/web/static/comic-authors.js'), 'utf8'),context);
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('function bookCategories('),app.indexOf('async function loadLibrary(')),context);
  vm.runInContext(app.slice(app.indexOf('function card('),app.indexOf('function libraryActions(')),context);
  return {context,opened,searches,resumed};
}
const book={id:'1',source:'jm',title:'春日书信',authors:['作者一','作者二']};

test('普通卡片与书库卡片的作者按钮不嵌套，点击作者不打开作品',async()=>{
  for(const library of [false,true]){
    const h=harness(),card=h.context.card(book,{library});
    const authors=card.querySelectorAll('.author-link');
    assert.equal(authors.length,2);
    assert.ok(card.querySelectorAll('button').every(button=>button.querySelectorAll('button').length===0));
    await authors[1].onclick();
    assert.equal(h.opened.length,0);assert.equal(h.searches[0][0],'作者二');
    assert.equal(h.searches[0][1],'');assert.equal(h.searches[0][2].mode,'keyword');
    await card.querySelectorAll('.card-open')[0].onclick();
    assert.equal(h.opened[0][0].id,'1');
  }
});

test('书库卡片同时显示手动分类和云收藏分类',()=>{
  const h=harness(),card=h.context.card({...book,library_category:'追更',library_categories:['JM 收藏','追更']},{library:true});
  assert.match(card.textContent,/JM 收藏 \/ 追更/);assert.doesNotMatch(card.textContent,/未分类/);
});

test('合并卡片为每个原始版本保留独立入口，同源版本不重名',async()=>{
  const h=harness(),variants=[book,{...book,id:'2',page_count:42},{...book,source:'bika'}];
  const card=h.context.card({...book,variants}),links=card.querySelectorAll('.variant-link');
  assert.deepEqual(links.map(link=>link.textContent),['jm · 1','jm · 2','bika']);
  assert.match(card.textContent,/3 个版本/);assert.match(links[1].title,/42 页/);
  for(const link of links)await link.onclick();
  assert.deepEqual(h.opened.map(([item])=>[item.source,item.id]),[['jm','1'],['jm','2'],['bika','1']]);
  assert.ok(card.querySelectorAll('button').every(button=>button.querySelectorAll('button').length===0));
  const single=h.context.card(book);assert.equal(single.querySelectorAll('.variant-link').length,0);
});

test('历史卡片显示阅读进度，只有播放按钮直接进入阅读页，点击其他部分打开简介',async()=>{
  const h=harness(),history={chapterName:'第一章',index:3,total:20};
  const card=h.context.card(book,{history});
  assert.match(card.textContent,/第一章 · 4\/20 页/);assert.equal(card.querySelectorAll('.author-link').length,0);
  await card.querySelectorAll('.card-open')[0].onclick();assert.equal(h.opened[0][1]?.resume,undefined);
  const play=card.querySelectorAll('.cover-play');assert.equal(play.length,1);assert.equal(play[0].tagName,'button');
  assert.equal(card.querySelectorAll('.card-open')[0].querySelectorAll('.cover-play').length,0);
  await play[0].onclick();assert.equal(h.resumed[0].id,'1');assert.equal(h.opened.length,1);
  assert.equal(h.context.card(book).querySelectorAll('.cover-play').length,0);
});

test('结果卡片标识书库与云收藏，合并卡片检查所有版本，收藏状态变更后刷新',async()=>{
  const h=harness(),calls=[],results={children:[]};
  Object.assign(h.context,{
    inLibrary:item=>item.source==='jm'&&item.id==='1',
    sourcePlugin:id=>({id,enabled:true,capabilities:['favorites'],account:{authenticated:id==='bika'}}),
    enc:encodeURIComponent,URLSearchParams,api:async path=>{calls.push(path);return {ids:['b2']};},
  });
  h.context.document.querySelectorAll=selector=>selector==='.card'?results.children:[];
  h.context.state.favoriteIds={};h.context.state.browseRequest=7;
  vm.runInContext(app.slice(app.indexOf('// Result cards mark'),app.indexOf('function card(')),h.context);
  const bika={id:'b2',source:'bika',title:'夏日'},plain=h.context.card({...book,id:'9'},{marks:true});
  const merged=h.context.card({...book,variants:[book,bika]},{marks:true});
  results.children.push(plain,merged);
  const marks=card=>card.querySelectorAll('.cover-mark').map(mark=>{assert.equal(mark.textContent,'');assert.equal(mark.attributes['aria-label'],mark.title);return mark.className.split(' ')[1];});
  assert.deepEqual(marks(merged),['library']);assert.ok(plain.querySelectorAll('.cover-marks')[0].hidden);
  assert.equal(h.context.card(book).querySelectorAll('.cover-marks').length,0);
  await h.context.loadResultFavorites([{...book,variants:[book,bika]}],7);
  assert.equal(calls.length,1);assert.match(calls[0],/^\/api\/bika\/sync\/favorite-ids\?ids=b2$/);
  assert.deepEqual(marks(merged),['library','favorite']);assert.match(merged.querySelectorAll('.favorite')[0].title,/哔咔收藏/);
  h.context.setFavoriteMark('bika','b2',false);assert.deepEqual(marks(merged),['library']);
});

test('阅读记录晚于卡片加载时，刷新后补上读至标签',()=>{
  const h=harness(),cards=[];let reading=null;
  Object.assign(h.context,{readingLast:()=>reading});
  h.context.document.querySelectorAll=selector=>selector==='.card'?cards:[];
  vm.runInContext(app.slice(app.indexOf('function refreshCardStates('),app.indexOf('// Favorite marks come from')),h.context);
  const card=h.context.card(book);cards.push(card);
  const tag=()=>card.querySelectorAll('.cover-tag')[0];
  assert.ok(tag().hidden);assert.equal(tag().textContent,'');
  reading={chapterName:'第 1 章'};h.context.refreshCardStates();
  assert.ok(!tag().hidden);assert.equal(tag().textContent,'读至 第 1 章');
  assert.equal(h.context.card(book,{history:{chapterName:'',index:0,total:1}}).querySelectorAll('.cover-tag').length,0);
});

test('新增作者脚本在主脚本前加载，交互样式声明排版基线且不改鼠标样式',()=>{
  const html=readFileSync(join(__dirname,'../src/web/templates/index.html'),'utf8');
  assert.ok(html.indexOf('/static/comic-authors.js')<html.indexOf('/static/app.js'));
  const css=readFileSync(join(__dirname,'../src/web/static/app.css'),'utf8');
  for(const name of ['comic-authors','comic-variants']){
    const rule=css.match(new RegExp(`\\.${name}\\{([^}]+)\\}`))[1];
    assert.match(rule,/font-size:/);assert.match(rule,/line-height:/);assert.doesNotMatch(rule,/cursor:/);
  }
});
