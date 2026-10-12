const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.hidden = false;
    this.value = '';
  }
  set textContent(value) { this.value = String(value); this.children = []; }
  get textContent() { return this.value + this.children.map(child => typeof child === 'string' ? child : child.textContent).join(''); }
  set innerHTML(value) { throw new Error('Error messages must not be inserted as HTML'); }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
}

// Evaluate the real render functions without booting the app, polling or opening a browser.
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');
const sync = readFileSync(join(__dirname, '../src/web/static/jm-sync.js'), 'utf8');
const context = {document: {createElement: tag => new Element(tag)}};
vm.createContext(context);
vm.runInContext(
  app.slice(app.indexOf('function node('), app.indexOf('function button(')) +
  sync.slice(sync.indexOf('const JM_SYNC_LABELS'), sync.indexOf('async function loadJmFavoritesView(')) +
  sync.slice(sync.indexOf('function jmRunCard('), sync.indexOf('async function loadJmStatus(')),
  context,
);

function run(overrides = {}) {
  return {
    id: 'test-run', mode: 'check_local', status: 'failed', stage: 'finished',
    started_at: 1, finished_at: 2, albums_done: 0, albums_total: 3,
    local_albums: 0, local_images: 0, local_missing: 0, failed: 1,
    downloaded: 0, skipped: 0, received_bytes: 0,
    error: '漫画详情预读取失败（ID：102）：模拟详情读取失败',
    errors: [{stage: 'matching', album_id: '102', title: '失效漫画', message: '模拟详情读取失败', code: 'not_found'}],
    ...overrides,
  };
}

function elements(root) {
  return [root, ...root.children.filter(child => typeof child !== 'string').flatMap(elements)];
}

function failureNotice(card) {
  return elements(card).find(element => element.className === 'jm-detail-failure');
}

test('终态仍显示详情失败阶段、漫画身份、影响范围和重试说明', () => {
  const item = run();
  const before = JSON.stringify(item);
  const card = context.jmRunCard(item);
  const notice = failureNotice(card);
  assert.ok(notice && !notice.hidden);
  assert.equal(notice.attributes.role, 'note');
  assert.match(notice.textContent, /详情预读取失败，本轮检查已中止/);
  assert.match(notice.textContent, /失效漫画 · ID：102/);
  assert.match(notice.textContent, /尚未开始本地图片校验，也未新增下载登记/);
  assert.match(notice.textContent, /已有文件和此前下载登记保持不变，无需删除本地文件/);
  assert.match(notice.textContent, /同步设置 → 同步范围/);
  assert.match(notice.textContent, /选择不包含该漫画的收藏夹后重试/);
  assert.match(notice.textContent, /定时同步需关闭并保存/);
  assert.equal((card.textContent.match(/模拟详情读取失败/g) || []).length, 1);
  assert.ok(!elements(card).some(element => element.tagName === 'details'));
  assert.equal(JSON.stringify(item), before);
});

test('收藏没有标题时仍明确显示漫画 ID', () => {
  const item = run();
  item.errors[0].title = '';
  assert.match(failureNotice(context.jmRunCard(item)).textContent, /标题不可用 · ID：102/);
});

test('上游标题和错误信息作为纯文本显示', () => {
  const payload = '<img src=x onerror="throw new Error()">';
  const item = run();
  item.errors[0].title = payload;
  item.errors[0].message = '<script>throw new Error()</script>';
  const card = context.jmRunCard(item);
  assert.ok(card.textContent.includes(payload));
  assert.ok(card.textContent.includes(item.errors[0].message));
  assert.ok(!elements(card).some(element => ['img', 'script'].includes(element.tagName)));
});

test('旧记录保留原错误展示，不猜测失败阶段', () => {
  const card = context.jmRunCard(run({error: '旧版整体错误', errors: [{title: '旧漫画', message: '旧异常'}]}));
  assert.equal(failureNotice(card), undefined);
  assert.match(card.textContent, /旧版整体错误/);
  assert.match(card.textContent, /旧漫画：旧异常/);
  assert.ok(elements(card).some(element => element.tagName === 'details'));
});

test('图片检查错误与普通同步不显示详情预读取说明', () => {
  for (const overrides of [
    {status: 'partial', errors: [{stage: 'checking', title: '漫画', message: '图片损坏'}]},
    {mode: 'sync'},
    {status: 'cancelled', error: '', errors: []},
  ]) {
    assert.equal(failureNotice(context.jmRunCard(run(overrides))), undefined);
  }
});
