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
  querySelectorAll(selector) {
    return this.children.flatMap(child=>[...(selector===child.tagName||selector.startsWith('.')&&child.className.split(' ').includes(selector.slice(1))?[child]:[]),...child.querySelectorAll(selector)]);
  }
}
function harness(){
  const opened=[],searches=[];
  const roots={'reader-dialog':{open:false},'detail-dialog':{open:false}};
  const context=vm.createContext({document:{createElement:tag=>new Element(tag)},$:id=>roots[id],state:{detailRequest:0},
    openComic:(...args)=>opened.push(args),sourceName:id=>id,readLocal:()=>null,lastKey:()=>'',
    coverBox:()=>new Element('span'),libraryActions:()=>new Element('div'),svgIcon:()=>'<svg></svg>',
    startSearch:(...args)=>searches.push(args),toast:message=>{throw new Error(message);},
  });
  vm.runInContext(readFileSync(join(__dirname, '../src/web/static/comic-authors.js'), 'utf8'),context);
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('function card('),app.indexOf('function libraryActions(')),context);
  return {context,opened,searches};
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

test('历史卡片仍显示阅读进度并从封面续读，不替换成作者入口',async()=>{
  const h=harness(),history={chapterName:'第一章',index:3,total:20};
  const card=h.context.card(book,{history});
  assert.match(card.textContent,/第一章 · 4\/20 页/);assert.equal(card.querySelectorAll('.author-link').length,0);
  await card.querySelectorAll('.card-open')[0].onclick();assert.equal(h.opened[0][1].resume,true);
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
