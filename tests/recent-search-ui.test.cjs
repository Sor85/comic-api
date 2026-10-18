const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {test}=require('node:test');
const vm=require('node:vm');
const app=readFileSync(join(__dirname,'../src/web/static/app.js'),'utf8');
const html=readFileSync(join(__dirname,'../src/web/templates/index.html'),'utf8');
const plain=value=>JSON.parse(JSON.stringify(value));

class Element{
  constructor(){this.children=[];this.attributes={};this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return (this.text||'')+this.children.map(child=>child.textContent).join('');}
  set innerHTML(value){this.markup=value;this.children=[];this.text='';}
  setAttribute(name,value){this.attributes[name]=String(value);}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.text='';this.children=children;}
}
function harness(storage={}){
  const roots={},searches=[];
  const context=vm.createContext({
    $:id=>roots[id]||(roots[id]=new Element()),state:{recentPage:storage['comic:recent-page']!==false},
    readLocal:key=>key in storage?plain(storage[key]):null,saveLocal:(key,value)=>{storage[key]=plain(value);},
    node:(_tag,text)=>{const el=new Element();if(text!==undefined)el.textContent=text;return el;},
    button:(text,action)=>{const el=new Element();el.textContent=text;el.onclick=action;return el;},
    svgIcon:name=>`<svg data-icon="${name}"></svg>`,startSearch:(...args)=>searches.push(args),
  });
  vm.runInContext(app.slice(app.indexOf('function readRecent('),app.indexOf('function readHistory(')),context);
  return {context,storage,searches,root:context.$};
}
const search=(page,extra={})=>({keyword:'123',mode:'keyword',source:'',tag_value:'',category:'',deduplicate:false,page,sort:'',...extra});

test('翻页记录到对应最近搜索，不改变列表顺序',()=>{
  const h=harness();
  h.context.addRecent(search(1));h.context.addRecent(search(1,{keyword:'456'}));
  h.context.saveRecentPage(search(3,{sort:'hot'}));
  assert.deepEqual(h.storage['comic:recent'].map(item=>[item.keyword,item.page,item.sort]),[['456',1,''],['123',3,'hot']]);
  h.context.saveRecentPage(search(5,{source:'jm'}));
  assert.equal(h.storage['comic:recent'][1].page,3);
});

test('结果页切换图源后翻页，页码和筛选写回原最近搜索并合并重复记录',()=>{
  const h=harness();
  const jm=search(1,{source:'jm'});h.context.addRecent(jm);
  const current=search(1);h.context.addRecent(current);
  Object.assign(current,{source:'jm',page:3});h.context.saveRecentPage(current);
  assert.deepEqual(h.storage['comic:recent'].map(item=>[item.keyword,item.source,item.page]),[['123','jm',3]]);
  current.page=4;h.context.saveRecentPage(current);
  assert.equal(h.storage['comic:recent'][0].page,4);
  const linked=search(2,{keyword:'456'});h.context.saveRecentPage(linked);
  assert.equal(h.storage['comic:recent'].length,1);
});

test('开启时点击最近搜索跳转到上次页码，关闭时从第 1 页开始',()=>{
  for(const on of [true,false]){
    const h=harness({'comic:recent-page':on});
    h.context.addRecent(search(1));h.context.saveRecentPage(search(3,{sort:'hot'}));
    h.context.renderRecent();
    const chip=h.root('recent-list').children[0];
    chip.onclick();
    assert.equal(h.searches[0][2].page,on?3:1);assert.equal(h.searches[0][2].sort,on?'hot':'');
    assert.equal(chip.title,on?'上次浏览至第 3 页':undefined);
  }
});

test('重新搜索同一关键词时重置记录的页码，旧数据缺少页码时按第 1 页处理',()=>{
  const h=harness({'comic:recent':['123',{keyword:'456',page:'abc'}]});
  assert.deepEqual(h.context.readRecent().map(item=>item.page),[1,1]);
  h.context.saveRecentPage(search(4));
  h.context.addRecent(search(1));
  assert.deepEqual(h.storage['comic:recent'].map(item=>[item.keyword,item.page]),[['123',1],['456',1]]);
});

test('设置面板提供默认开启的恢复页码开关',()=>{
  assert.match(html,/data-settings-pane="browse"[^>]*aria-controls="settings-pane-browse"/);
  assert.match(html,/<section id="settings-pane-browse"[^>]*role="tabpanel"[^>]*hidden>/);
  const h=harness();h.context.syncRecentPageToggle();
  const toggle=h.root('recent-page-toggle');
  assert.equal(toggle.attributes['aria-checked'],'true');assert.match(toggle.markup,/toggle-right/);assert.equal(toggle.textContent,'已开启');
  const off=harness({'comic:recent-page':false});off.context.syncRecentPageToggle();
  assert.equal(off.root('recent-page-toggle').attributes['aria-checked'],'false');
});
