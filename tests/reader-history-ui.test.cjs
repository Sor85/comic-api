const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');

function harness(mode='page'){
  const recorded=[],opened=[],dialog={open:false,showModal(){this.open=true;}};
  const roots={'reader-dialog':dialog,'reader-pages':{children:[],focus(){}},'reader-mode':{value:mode},'reader-chapter':{value:''}};
  const state={};
  const context=vm.createContext({state,$:id=>roots[id],recordReading:r=>recorded.push(r.index),
    renderReader:()=>context.saveProgress(),syncPageStatus(){},syncReaderProgress(){},toast(){},
    run:action=>action(),openReader:(...args)=>opened.push(args)});
  vm.runInContext(app.slice(app.indexOf('function showReaderDialog('),app.indexOf('// Continue reading straight')),context);
  vm.runInContext(app.slice(app.indexOf('// Opening the reader alone'),app.indexOf('function readerMessage(')),context);
  vm.runInContext(app.slice(app.indexOf('function gotoPage('),app.indexOf('function closeReader(')),context);
  const open=(index=0)=>{context.showReaderDialog();state.reader={book:{chapters:[{id:'1'},{id:'2'}]},chapterId:'1',images:['a','b','c'],index};context.renderReader();};
  return {context,state,recorded,opened,dialog,open};
}

test('只打开阅读器不写观看记录，第一次翻页后开始记录',()=>{
  const h=harness();h.open(1);
  h.context.saveProgress();assert.deepEqual(h.recorded,[]);
  h.context.gotoPage(1);assert.deepEqual(h.recorded,[],'跳到当前页不算翻页');
  h.context.movePage(1);assert.deepEqual(h.recorded,[2]);
  h.context.saveProgress();assert.deepEqual(h.recorded,[2,2],'翻页后关闭等时机继续记录');
});

test('滚动模式同样以页码变化为准，重新打开阅读器重新计算',()=>{
  const h=harness('scroll');h.open();
  h.context.gotoPage(0);assert.deepEqual(h.recorded,[]);
  h.context.gotoPage(2);assert.deepEqual(h.recorded,[2]);
  h.dialog.open=false;h.open();assert.deepEqual(h.recorded,[2]);
});

test('末页继续翻页进入下一章计为翻页，切换章节本身不算',()=>{
  const h=harness();h.open();
  h.context.moveChapter(1);assert.equal(h.state.readerTurned,false);assert.deepEqual(h.opened[0],['2',undefined]);
  h.state.reader.index=2;h.context.movePage(1);
  assert.equal(h.state.readerTurned,true);assert.deepEqual(h.opened[1],['2',0]);
});
