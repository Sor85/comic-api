const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../src/web/static/chapter-previews.js'), 'utf8');
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function observerType() {
  return class Observer {
    static instances = [];
    constructor(callback, options) { this.callback = callback; this.options = options; this.targets = new Set(); this.constructor.instances.push(this); }
    observe(target) { this.targets.add(target); }
    unobserve(target) { this.targets.delete(target); }
    disconnect() { this.targets.clear(); }
    emit(target, visible = true) { this.callback([{target, isIntersecting: visible}]); }
  };
}
function queueHarness() {
  const Observer = observerType(), context = vm.createContext({});
  vm.runInContext(script + '\nglobalThis.Controller = ChapterPreviews;', context);
  const calls = [], renders = [];
  let now = 0;
  const manager = new context.Controller({Observer, now: () => now,
    load: (detail, chapter) => { const task = deferred(); calls.push({detail, chapter, ...task}); return task.promise; },
    render: (...args) => renders.push(args),
  });
  const root = {open: true};
  const add = (source = 'jm', book = '1', chapter = '1') => {
    const element = {isConnected: true};
    manager.watch(element, {source, id: book}, {id: chapter});
    return element;
  };
  return {manager, calls, renders, root, add, Observer, advance: ms => { now += ms; },
    observer: () => Observer.instances.at(-1)};
}

test('预览管理器默认不观察、不请求；开启后仅在章节可见时取图', () => {
  const h = queueHarness();
  h.add();
  assert.equal(h.calls.length, 0);
  assert.equal(h.Observer.instances.length, 0);
  h.manager.start(h.root);
  const target = h.add();
  assert.equal(h.calls.length, 0);
  h.observer().emit(target, false);
  assert.equal(h.calls.length, 0);
  h.observer().emit(target);
  assert.equal(h.calls.length, 1);
  assert.equal(h.observer().options.root, h.root);
  assert.equal(h.observer().options.rootMargin, '0px');
});

test('最多一个预览请求在途，滚出视口的排队章节不发请求', async () => {
  const h = queueHarness(); h.manager.start(h.root);
  const first = h.add(), second = h.add('bika'), third = h.add('copy');
  h.observer().emit(first); h.observer().emit(second); h.observer().emit(third);
  assert.equal(h.calls.length, 1);
  h.observer().emit(second, false);
  h.calls[0].resolve('/first.jpg'); await flush();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].detail.source, 'copy');
  h.calls[1].resolve('/third.jpg'); await flush();
  h.observer().emit(second); assert.equal(h.calls.length, 3);
  h.calls[2].resolve('/second.jpg'); await flush();
  assert.equal(h.renders.length, 3);
});

test('关闭时丢弃队列与旧视图写入，再开启复用已完成的首图请求', async () => {
  const h = queueHarness(); h.manager.start(h.root);
  const first = h.add(), second = h.add('bika');
  const old = h.observer(); old.emit(first); old.emit(second);
  h.manager.stop();
  old.emit(second);
  h.calls[0].resolve('/api/image/proxy?source=jm&chapter_id=1'); await flush();
  assert.equal(h.calls.length, 1); assert.equal(h.renders.length, 0);
  h.manager.start(h.root); const fresh = h.add(); h.observer().emit(fresh);
  assert.equal(h.calls.length, 1);
  assert.equal(h.renders[0][0], fresh);
  assert.equal(h.renders[0][1], '/api/image/proxy?source=jm&chapter_id=1');
});

test('关闭对话框或移除旧节点后不继续排队，也不回写', async () => {
  for (const detach of [false, true]) {
    const h = queueHarness(); h.manager.start(h.root);
    const first = h.add(), second = h.add('bika');
    h.observer().emit(first); h.observer().emit(second);
    if (detach) first.isConnected = second.isConnected = false;
    else h.root.open = false;
    h.calls[0].resolve('/first.jpg'); await flush();
    assert.equal(h.calls.length, 1); assert.equal(h.renders.length, 0);
  }
});

test('切换漫画后忽略旧响应；跨图源、漫画与章节的缓存分别隔离', async () => {
  const h = queueHarness(); h.manager.start(h.root);
  h.observer().emit(h.add());
  h.manager.start(h.root); h.observer().emit(h.add('bika'));
  h.calls[0].resolve('/jm.jpg'); await flush();
  assert.equal(h.renders.length, 0); assert.equal(h.calls.length, 2);
  h.calls[1].resolve('/bika.jpg'); await flush();
  assert.equal(h.renders[0][1], '/bika.jpg');
  for (const [book, chapter] of [['2', '1'], ['1', '2']]) {
    h.observer().emit(h.add('bika', book, chapter));
    h.calls.at(-1).resolve(`/${book}/${chapter}.jpg`); await flush();
  }
  assert.equal(h.calls.length, 4);
});

test('成功缓存十分钟，失败缓存三十秒且不阻塞后续章节', async () => {
  const h = queueHarness(); h.manager.start(h.root);
  h.observer().emit(h.add()); h.calls[0].reject(new Error('登录失效')); await flush();
  assert.equal(h.renders[0][1], null);
  h.observer().emit(h.add()); assert.equal(h.calls.length, 1);
  h.advance(30001);
  h.observer().emit(h.add()); h.calls[1].resolve('/first.jpg'); await flush();
  h.observer().emit(h.add()); assert.equal(h.calls.length, 2);
  h.advance(600001);
  h.observer().emit(h.add()); assert.equal(h.calls.length, 3);
  h.calls[2].resolve('/refreshed.jpg'); await flush();
});

test('首图图片加载失败会短期回退；缓存最多保留256项', async () => {
  const h = queueHarness(); h.manager.start(h.root);
  h.observer().emit(h.add()); h.calls[0].resolve('/bad.jpg'); await flush();
  h.manager.failed({source: 'jm', id: '1'}, {id: '1'});
  h.observer().emit(h.add()); assert.equal(h.calls.length, 1);
  assert.equal(h.renders.at(-1)[1], null);
  for (let i = 0; i < 300; i++) h.manager.remember(String(i), '/image.jpg');
  assert.equal(h.manager.cache.size, 256);
});

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {}; this.className = '';
    this.value = ''; this.isConnected = true; this.open = false; this.parentElement = null;
    this.classList = {
      contains: name => this.className.split(' ').includes(name),
      add: name => { if (!this.classList.contains(name)) this.className = `${this.className} ${name}`.trim(); },
      remove: name => { this.className = this.className.split(' ').filter(value => value !== name).join(' '); },
      toggle: (name, on) => { const next = on ?? !this.classList.contains(name); this.classList[next ? 'add' : 'remove'](name); return next; },
    };
  }
  set textContent(value) { this.replaceChildren(); this.text = String(value); }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  set innerHTML(value) { this.replaceChildren(); this.html = value; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'src') delete this.src; }
  append(...children) { for (const child of children) { child.parentElement = this; child.connected(this.isConnected); this.children.push(child); } }
  connected(value) { this.isConnected = value; for (const child of this.children) child.connected(value); }
  replaceChildren(...children) { for (const child of this.children) child.connected(false); this.children = []; this.text = ''; this.append(...children); }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(child => child !== this); this.connected(false); }
  querySelectorAll(selector) { return this.children.flatMap(child => [...((selector.startsWith('.') ? child.classList.contains(selector.slice(1)) : child.tagName === selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function uiHarness(saved) {
  const roots = Object.fromEntries(['detail-dialog', 'reader-dialog', 'chapter-list', 'chapter-filter', 'chapter-previews-toggle'].map(id => [id, new Element('div')]));
  roots['detail-dialog'].open = true;
  roots['detail-dialog'].append(roots['chapter-list']);
  const storage = new Map(saved === undefined ? [] : [['comic:chapter-previews', saved]]);
  const Observer = observerType(), requests = [], read = [], downloads = [];
  const context = vm.createContext({
    document: {getElementById: id => roots[id], createElement: tag => new Element(tag)},
    localStorage: {getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value)},
    location: {origin: 'http://ui.test'}, URL, URLSearchParams, AbortController, setTimeout, clearTimeout,
    IntersectionObserver: Observer, svgIcon: name => `<svg data-test-icon="${name}"></svg>`, toast() {},
    api: async path => { requests.push(path); return {images: [`/api/image/proxy?source=jm&chapter_id=1&url=${encodeURIComponent(path)}`]}; },
    openReader: id => read.push(id), queueDownload: (...args) => downloads.push(args),
  });
  vm.runInContext(script, context);
  vm.runInContext(app.slice(app.indexOf('function readLocal('), app.indexOf('function progressKey(')), context);
  vm.runInContext(app.slice(0, app.indexOf('const BROWSE_ACTIONS')), context);
  vm.runInContext(app.slice(app.indexOf('function node('), app.indexOf('// The toast')), context);
  vm.runInContext(app.slice(app.indexOf('function progressKey('), app.indexOf('function skeletons(')), context);
  vm.runInContext(app.slice(app.indexOf('const CHAPTER_PAGE'), app.indexOf('function renderDetail(')), context);
  const state = vm.runInContext('state', context), manager = vm.runInContext('chapterPreviews', context);
  state.detail = {source: 'jm', id: 'book', cover: 'https://cover.example/book.jpg', chapters: Array.from({length: 65}, (_, i) => ({id: String(i + 1), name: `章节 ${i + 1}`}))};
  return {context, state, manager, roots, storage, requests, Observer, read, downloads};
}

test('无设置、false、损坏值均默认关闭；仅保存的布尔true会恢复开启', () => {
  for (const saved of [undefined, 'false', 'broken', '"true"', 'true']) {
    const h = uiHarness(saved); h.context.drawChapters(true);
    assert.equal(h.state.chapterPreviews, saved === 'true');
    assert.equal(h.roots['chapter-list'].querySelectorAll('.chapter-preview').length, saved === 'true' ? 60 : 0);
    assert.equal(h.requests.length, 0);
  }
});

test('开关立即生效并持久化；开关往返保留已展开的章节数量与筛选', () => {
  const h = uiHarness(); h.context.drawChapters(true);
  h.state.chapterView.shown = 120; h.context.drawChapters();
  h.context.toggleChapterPreviews();
  assert.equal(h.storage.get('comic:chapter-previews'), 'true');
  assert.equal(h.roots['chapter-previews-toggle'].attributes['aria-checked'], 'true');
  assert.equal(h.roots['chapter-list'].querySelectorAll('.chapter-preview').length, 65);
  h.roots['chapter-filter'].value = '章节 6'; h.context.drawChapters(true);
  h.context.toggleChapterPreviews();
  assert.equal(h.storage.get('comic:chapter-previews'), 'false');
  assert.equal(h.roots['chapter-list'].querySelectorAll('.chapter-preview').length, 0);
  assert.equal(h.roots['chapter-list'].querySelectorAll('.chapter').length, 7);
  assert.equal(h.requests.length, 0);
});

test('三图源均走统一章节API，保留返回的代理地址与章节参数', async () => {
  for (const source of ['jm', 'bika', 'copy']) {
    const h = uiHarness('true'); h.state.detail.source = source;
    h.context.drawChapters(true);
    const preview = h.roots['chapter-list'].querySelector('.chapter-preview');
    h.Observer.instances.at(-1).emit(preview); await flush();
    assert.equal(h.requests[0], `/api/chapter/${source}/book/1`);
    const img = preview.querySelector('img');
    assert.ok(img.src.includes('chapter_id=1'));
    assert.equal(img.loading, 'lazy'); assert.equal(img.decoding, 'async');
    img.onload(); assert.ok(preview.classList.contains('has-image'));
  }
});

test('排序重绘使旧节点失效，旧请求结束后只处理新列表的可见章节', async () => {
  const h = uiHarness('true'), tasks = [];
  h.context.api = path => { const task = deferred(); tasks.push({path, ...task}); return task.promise; };
  h.context.drawChapters(true);
  const old = h.roots['chapter-list'].querySelector('.chapter-preview');
  h.Observer.instances.at(-1).emit(old);
  h.state.chapterDesc = true; h.context.drawChapters(true);
  const fresh = h.roots['chapter-list'].querySelector('.chapter-preview');
  h.Observer.instances.at(-1).emit(fresh);
  assert.equal(tasks.length, 1);
  tasks[0].resolve({images: ['/first.jpg']}); await flush();
  assert.equal(old.querySelector('img'), null);
  assert.equal(tasks[1].path, '/api/chapter/jm/book/65');
  tasks[1].resolve({images: ['/last.jpg']}); await flush();
  assert.equal(fresh.querySelector('img').src, 'http://ui.test/last.jpg');
});

test('追加更多章节复用观察器，不自动请求新章节', () => {
  const h = uiHarness('true'); h.context.drawChapters(true);
  const observer = h.Observer.instances.at(-1);
  h.state.chapterView.shown = 120; h.context.drawChapters();
  assert.equal(h.Observer.instances.length, 1);
  assert.equal(observer.targets.size, 65);
  assert.equal(h.requests.length, 0);
});

test('首图失败回退漫画封面，封面再失败保留占位且不循环重试', async () => {
  const h = uiHarness('true'); h.context.drawChapters(true);
  const preview = h.roots['chapter-list'].querySelector('.chapter-preview');
  h.Observer.instances.at(-1).emit(preview); await flush();
  preview.querySelector('img').onerror();
  const fallback = preview.querySelector('img');
  assert.ok(fallback.src.includes('cover.example'));
  assert.ok(!fallback.src.includes('chapter_id='));
  fallback.onerror(); assert.equal(preview.querySelector('img'), null);
  assert.equal(preview.title, '章节首图不可用');
});

test('关闭后旧图片的失败回调不再触发封面请求，原有阅读与下载交互保留', async () => {
  const h = uiHarness('true'); h.context.drawChapters(true);
  const row = h.roots['chapter-list'].querySelector('.chapter'), preview = row.querySelector('.chapter-preview');
  h.Observer.instances.at(-1).emit(preview); await flush();
  const img = preview.querySelector('img');
  h.context.toggleChapterPreviews(); img.onerror();
  assert.equal(preview.querySelector('img'), img);
  assert.equal(img.src, undefined);
  row.onclick({target: {closest: () => null}}); assert.deepEqual(h.read, ['1']);
  row.onclick({target: {closest: () => ({})}}); assert.equal(h.read.length, 1);
  row.querySelector('button').onclick(); await flush(); assert.equal(h.downloads.length, 1);
});
