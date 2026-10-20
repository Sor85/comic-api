const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');

class Element {
  constructor(tag) { this.tagName=tag;this.children=[];this.attributes={};this.className='';this.value=''; }
  set textContent(value) { this.text=String(value);this.children=[]; }
  get textContent() { return (this.text||'')+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(value) { this.html=value;this.text='';this.children=[]; }
  setAttribute(name,value) { this.attributes[name]=String(value); }
  append(...children) { for(const child of children){child.parentElement=this;this.children.push(child);} }
  replaceChildren(...children) { this.text='';this.children=[];this.append(...children); }
  remove() { if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this); }
  querySelectorAll(selector) { return this.children.flatMap(child=>[...(selector.startsWith('.')?child.className.split(' ').includes(selector.slice(1)):child.tagName===selector)?[child]:[],...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0]||null; }
}
function harness(saved) {
  const roots={'chapter-list':new Element('div'),'chapter-filter':new Element('input')};
  const storage=new Map(saved===undefined?[]:[['comic:chapter-previews',saved]]),requests=[],read=[],downloads=[];
  const context=vm.createContext({
    document:{getElementById:id=>roots[id],createElement:tag=>new Element(tag)},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
    svgIcon:name=>`<svg>${name}</svg>`,toast(){},
    api:async path=>{requests.push(path);return {images:[]};},
    openReader:id=>read.push(id),queueDownload:(...args)=>downloads.push(args),
  });
  vm.runInContext(app.slice(app.indexOf('function readLocal('),app.indexOf('const readingKey=')),context);
  vm.runInContext(app.slice(0,app.indexOf('const BROWSE_ACTIONS')),context);
  vm.runInContext(app.slice(app.indexOf('function node('),app.indexOf('// The toast')),context);
  vm.runInContext(app.slice(app.indexOf('const readingKey='),app.indexOf('function skeletons(')),context);
  vm.runInContext(app.slice(app.indexOf('const CHAPTER_PAGE'),app.indexOf('function renderDetail(')),context);
  const state=vm.runInContext('state',context);
  state.detail={source:'jm',id:'book',chapters:Array.from({length:65},(_,i)=>({id:String(i+1),name:`章节 ${i+1}`}))};
  return {context,state,roots,storage,requests,read,downloads,rows:()=>roots['chapter-list'].querySelectorAll('.chapter')};
}

test('旧章节封面偏好不再影响章节列表，浏览章节不会预取阅读图片',()=>{
  for(const saved of [undefined,'true','false','broken'])for(const source of ['jm','bika','copy']){
    const h=harness(saved);h.state.detail.source=source;h.context.drawChapters(true);
    assert.equal(h.rows().length,60);
    assert.equal(Object.hasOwn(h.state,'chapterPreviews'),false);
    assert.equal(h.roots['chapter-list'].querySelectorAll('img').length,0);
    assert.equal(h.requests.length,0);
    assert.equal(h.storage.get('comic:chapter-previews'),saved);
  }
});

test('章节分批显示并可展开剩余章节，不重复生成已有章节',async()=>{
  const h=harness();h.context.drawChapters(true);
  const first=h.rows()[0],more=h.roots['chapter-list'].querySelector('.more-row');
  assert.match(more.textContent,/还有 5 章/);
  await more.querySelector('button').onclick();
  assert.equal(h.rows().length,65);assert.equal(h.rows()[0],first);
  assert.equal(h.roots['chapter-list'].querySelector('.more-row'),null);
  assert.equal(new Set(h.rows().map(row=>row.textContent)).size,65);
  assert.equal(h.requests.length,0);
});

test('展开后筛选与清空筛选仍按章节分页，正序倒序均保留',()=>{
  const h=harness();h.context.drawChapters(true);
  h.state.chapterView.shown=120;h.context.drawChapters();
  h.roots['chapter-filter'].value='章节 6';h.context.drawChapters(true);
  assert.equal(h.rows().length,7);assert.equal(h.rows()[0].textContent,'章节 6');
  h.state.chapterDesc=true;h.context.drawChapters(true);
  assert.equal(h.rows()[0].textContent,'章节 65');
  h.roots['chapter-filter'].value='';h.context.drawChapters(true);
  assert.equal(h.rows().length,60);assert.equal(h.rows()[0].textContent,'章节 65');
  h.state.chapterDesc=false;h.context.drawChapters(true);
  assert.equal(h.rows()[0].textContent,'章节 1');assert.equal(h.requests.length,0);
});

test('章节列表保留已读状态与上次阅读进度',()=>{
  const h=harness();h.state.detail.reading={comic:{chapter_id:'2'},chapters:{'1':{page:4},'2':{page:3}}};
  h.context.drawChapters(true);
  assert.equal(h.rows()[0].className,'chapter read');assert.match(h.rows()[0].textContent,/读到第 5 页/);
  assert.equal(h.rows()[1].className,'chapter current');assert.match(h.rows()[1].textContent,/上次读到第 4 页/);
});

test('章节点击与键盘操作仍打开阅读器，PDF 按钮不触发阅读',async()=>{
  const h=harness();h.context.drawChapters(true);const row=h.rows()[0];let prevented=0;
  assert.equal(row.attributes.role,'button');assert.equal(row.tabIndex,0);
  row.onclick({target:{closest:()=>null}});assert.deepEqual(h.read,['1']);
  row.onclick({target:{closest:()=>({})}});assert.equal(h.read.length,1);
  for(const key of ['Enter',' '])row.onkeydown({key,target:row,preventDefault:()=>prevented++});
  row.onkeydown({key:'Enter',target:row.querySelector('button'),preventDefault:()=>prevented++});
  assert.equal(prevented,2);assert.deepEqual(h.read,['1','1','1']);
  await row.querySelector('button').onclick();
  assert.equal(h.downloads.length,1);assert.equal(h.downloads[0][0],h.state.detail);assert.equal(h.downloads[0][1].id,'1');
  assert.equal(h.read.length,3);assert.equal(h.requests.length,0);
});

test('无章节与无匹配章节保留各自的空状态',()=>{
  const h=harness();h.roots['chapter-filter'].value='不存在';h.context.drawChapters(true);
  assert.equal(h.rows().length,0);assert.equal(h.roots['chapter-list'].textContent,'没有匹配的章节');
  h.state.detail.chapters=[];h.context.drawChapters(true);
  assert.equal(h.roots['chapter-list'].textContent,'暂无章节');
});
