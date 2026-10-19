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
    this.style = {};
  }
  set textContent(value) { this.value = String(value); this.children = []; }
  get textContent() { return this.value + this.children.map(child => typeof child === 'string' ? child : child.textContent).join(''); }
  set innerHTML(value) { this.html = value; }
  append(...children) { this.children.push(...children); }
  setAttribute(name, value) { this.attributes[name] = value; }
}

// Evaluate the real run-card renderer without booting the app, polling or opening a browser.
const app = readFileSync(join(__dirname, '../src/web/static/app.js'), 'utf8');
const sync = readFileSync(join(__dirname, '../src/web/static/library-sync.js'), 'utf8');
const context = {document: {createElement: tag => new Element(tag)}, svgIcon: name => `<svg>${name}</svg>`};
vm.createContext(context);
vm.runInContext(
  app.slice(app.indexOf('function node('), app.indexOf('function button(')) +
  sync.slice(sync.indexOf('const JM_SYNC_LABELS'), sync.indexOf('async function loadSyncSettings(')) +
  sync.slice(sync.indexOf('function jmRunCard('), sync.indexOf('function syncProgressLabel(')),
  context,
);

function run(overrides = {}) {
  return {
    id: 'run', mode: 'sync', status: 'completed', stage: 'finished', automatic: false, selected: false,
    started_at: 1, finished_at: 2, albums_done: 2, albums_total: 2, downloaded: 4, skipped: 3, skipped_albums: 1,
    failed: 0, deferred_albums: 0, added_books: 0, received_bytes: 2048, local_albums: 0, local_images: 0,
    local_missing: 0, errors: [], error: '', ...overrides,
  };
}

test('所选同步显示新加入书库的数量和未变化跳过的漫画', () => {
  const text = context.jmRunCard(run({selected: true, added_books: 2})).textContent;
  assert.match(text, /所选同步/);
  assert.match(text, /新下载 · 新加入书库 2 本/);
  assert.match(text, /未变化跳过 · 校验跳过 3 张/);
  assert.doesNotMatch(text, /JM／哔咔已同步/);
});

test('检查与修复任务分别显示各自的统计', () => {
  const check = context.jmRunCard(run({mode: 'check_local', local_albums: 1, local_images: 4, local_missing: 1})).textContent;
  assert.match(check, /本地漫画检查/);
  assert.match(check, /已下载 · 4 张/);
  assert.doesNotMatch(check, /累计接收/);
  const repair = context.jmRunCard(run({mode: 'repair', repaired_albums: 1, moved_images: 2, backup_dir: '/data/library/.comic-api-repair/run'})).textContent;
  assert.match(repair, /异常漫画修复/);
  assert.match(repair, /备份目录：\/data\/library\/\.comic-api-repair\/run/);
});

test('上游标题和错误信息作为纯文本显示', () => {
  const payload = '<img src=x onerror="throw new Error()">';
  const card = context.jmRunCard(run({status: 'partial', failed: 1, errors: [{title: payload, message: payload}]}));
  assert.ok(card.textContent.includes(payload));
});

test('自定义模板示例按变量渲染并补零，未知变量原样保留', () => {
  const example = new vm.Script(sync.slice(sync.indexOf('const TEMPLATE_SAMPLE'), sync.indexOf('function updateSyncPreview(')) + '\nrulePathExample;');
  const rulePathExample = example.runInNewContext({});
  assert.equal(rulePathExample('custom', ' {category}\\{title} [{id}]/{index:03} - {chapter} [{chapter_id}]/ '), '分类/漫画标题 [123456]/001 - 章节标题 [123457]');
  assert.equal(rulePathExample('custom', '{source}-{author}/{nope}'), 'jm-作者/{nope}');
  assert.equal(rulePathExample('title_plain', '{id}/{index}'), '漫画标题/第1話 章节标题');
});

test('清空自定义模板时路径预览不回填默认模板', () => {
  const fields = {directory_rule: {value: 'custom'}, image_format: {value: 'original'}, download_dir: {value: '/data/library'},
    directory_template: {value: ''}, quality: {}};
  const elements = {'fav-directory-template-field': {hidden: true}, 'fav-directory-preview': {}};
  const preview = new vm.Script(sync.slice(sync.indexOf('const TEMPLATE_SAMPLE'), sync.indexOf('function insertTemplateField(')) + '\nupdateSyncPreview;');
  const update = preview.runInNewContext({syncField: name => fields[name], $: id => elements[id],
    libSync: {options: {default_template: '{id}/{index}'}}});
  update();
  assert.equal(fields.directory_template.value, '');
  assert.equal(elements['fav-directory-template-field'].hidden, false);
});
