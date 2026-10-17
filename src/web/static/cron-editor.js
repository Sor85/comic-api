'use strict';
/*
 * 同步计划浮窗：锚定在「编辑计划」按钮旁的小浮层（manual popover，显示在所在 <dialog> 之上）。
 * 频率模式与 5 字段 Cron 表达式双向同步，无法识别的表达式归为自定义；表达式由服务器校验并返回说明
 * 和下次运行时间，校验通过后才能确定。点击浮层外、Esc 或取消只收起浮层，不关闭所在窗口。
 * open(anchor, expression, onApply)：展开浮层，确定后以 {expression, description} 回调。
 */
const cronEditor = (() => {
  const MODES = [
    {value:'minutes',label:'分钟'},{value:'hours',label:'小时'},{value:'daily',label:'每天'},
    {value:'weekly',label:'每周'},{value:'monthly',label:'每月'},{value:'custom',label:'自定义'},
  ];
  const DAYS = [[1,'一'],[2,'二'],[3,'三'],[4,'四'],[5,'五'],[6,'六'],[0,'日']];
  const GAP = 6, EDGE = 12;
  const pop = $('fav-cron-pop'), expressionInput = $('fav-cron-expression');
  const canPopover = typeof HTMLElement.prototype.showPopover === 'function';
  const field = name => pop.querySelector(`[name="${name}"]`);
  const view = {mode:'hours', days:new Set([1]), request:0, timer:0, valid:null, onApply:null, anchor:null};

  const pad = value => String(value).padStart(2, '0');
  const int = (value, low, high) => {
    const number = Number(value);
    return Number.isInteger(number) && number >= low && number <= high ? number : null;
  };
  const clamp = (value, low, high, fallback) => int(value, low, high) ?? fallback;

  /** 将表达式识别为某个频率模式并回填控件；识别失败返回 custom。 */
  function detect(expression) {
    const text = expression.trim().replace(/\s+/g, ' ');
    let match;
    if ((match = text.match(/^\*\/(\d+) \* \* \* \*$/)) && int(match[1], 1, 59)) {
      field('every_minutes').value = Number(match[1]); return 'minutes';
    }
    if ((match = text.match(/^(\d{1,2}) (\*|\*\/(\d+)) \* \* \*$/)) && int(match[1], 0, 59) !== null && int(match[3] || 1, 1, 23)) {
      field('every_hours').value = Number(match[3] || 1); field('at_minute').value = Number(match[1]); return 'hours';
    }
    const clock = (minute, hour) => int(minute, 0, 59) !== null && int(hour, 0, 23) !== null
      && (field('minute').value = pad(Number(minute)), field('hour').value = pad(Number(hour)), true);
    if ((match = text.match(/^(\d{1,2}) (\d{1,2}) \* \* \*$/)) && clock(match[1], match[2])) return 'daily';
    if ((match = text.match(/^(\d{1,2}) (\d{1,2}) \* \* ([0-7](?:-[0-7])?(?:,[0-7](?:-[0-7])?)*)$/)) && clock(match[1], match[2])) {
      const days = match[3].split(',').flatMap(part => {
        const [start, end = start] = part.split('-').map(Number);
        return Array.from({length: Math.max(0, end - start + 1)}, (_, offset) => (start + offset) % 7);
      });
      if (days.length) { view.days = new Set(days); return 'weekly'; }
    }
    if ((match = text.match(/^(\d{1,2}) (\d{1,2}) (\d{1,2}) \* \*$/)) && int(match[3], 1, 31) && clock(match[1], match[2])) {
      field('day').value = Number(match[3]); return 'monthly';
    }
    return 'custom';
  }

  /** 由频率模式和控件值生成表达式；自定义模式保留输入框原文。 */
  function build() {
    const minute = clamp(field('minute').value, 0, 59, 0), hour = clamp(field('hour').value, 0, 23, 0);
    switch (view.mode) {
      case 'minutes': return `*/${clamp(field('every_minutes').value, 1, 59, 30)} * * * *`;
      case 'hours': {
        const every = clamp(field('every_hours').value, 1, 23, 1);
        return `${clamp(field('at_minute').value, 0, 59, 0)} ${every === 1 ? '*' : `*/${every}`} * * *`;
      }
      case 'daily': return `${minute} ${hour} * * *`;
      case 'weekly': return view.days.size ? `${minute} ${hour} * * ${[...view.days].sort((a, b) => a - b).join(',')}` : '';
      case 'monthly': return `${minute} ${hour} ${clamp(field('day').value, 1, 31, 1)} * *`;
      default: return expressionInput.value;
    }
  }

  function renderModes() {
    $('fav-cron-modes').replaceChildren(...MODES.map(mode => {
      const chip = node('button', mode.label, 'cron-mode' + (mode.value === view.mode ? ' active' : ''));
      chip.type = 'button'; chip.setAttribute('role', 'radio'); chip.setAttribute('aria-checked', String(mode.value === view.mode));
      chip.onclick = () => { if (mode.value !== view.mode) setMode(mode.value); };
      return chip;
    }));
    pop.dataset.mode = view.mode;
    for (const row of pop.querySelectorAll('[data-cron]')) row.hidden = !row.dataset.cron.split(' ').includes(view.mode);
    renderDays();
    if (view.anchor) place();
  }

  function renderDays() {
    $('fav-cron-days').replaceChildren(...DAYS.map(([day, label]) => {
      const on = view.days.has(day), chip = node('button', label, 'cron-day' + (on ? ' active' : ''));
      chip.type = 'button'; chip.setAttribute('aria-pressed', String(on)); chip.setAttribute('aria-label', `周${label}`);
      chip.onclick = () => { if (on) view.days.delete(day); else view.days.add(day); renderDays(); fromBuilder(); };
      return chip;
    }));
  }

  function setMode(mode) {
    view.mode = mode;
    renderModes();
    if (mode === 'custom') expressionInput.focus();
    else fromBuilder();
  }

  function fromBuilder() {
    if (view.mode === 'custom') return;
    expressionInput.value = build();
    schedulePreview();
  }

  /** 失焦时把数字夹回范围；时、分补足两位。 */
  function settle(input) {
    const low = Number(input.dataset.min), high = Number(input.dataset.max);
    const value = Math.min(high, Math.max(low, Number.parseInt(input.value, 10) || low));
    input.value = ['hour', 'minute'].includes(input.name) ? pad(value) : String(value);
  }

  function nudge(input, step) {
    const low = Number(input.dataset.min), high = Number(input.dataset.max), span = high - low + 1;
    const current = Number.parseInt(input.value, 10);
    const value = Number.isFinite(current) ? low + ((current - low + step) % span + span) % span : low;
    input.value = ['hour', 'minute'].includes(input.name) ? pad(value) : String(value);
    fromBuilder();
  }

  function fromExpression() {
    const mode = detect(expressionInput.value);
    if (mode !== view.mode) { view.mode = mode; renderModes(); }
    else renderDays();
    schedulePreview();
  }

  function show(description, message = '', invalid = false) {
    $('fav-cron-description').textContent = description;
    $('fav-cron-message').textContent = message;
    $('fav-cron-code').textContent = invalid ? '' : expressionInput.value.trim().replace(/\s+/g, ' ');
    $('fav-cron-summary-card').classList.toggle('invalid', invalid);
  }

  function schedulePreview() {
    clearTimeout(view.timer);
    view.valid = null; $('fav-cron-apply').disabled = true;
    const expression = expressionInput.value.trim();
    if (!expression) {
      view.request++;
      show('尚未设置计划', view.mode === 'weekly' ? '至少选择一天' : '需要填写 Cron 表达式', true);
      return;
    }
    view.timer = setTimeout(() => preview(expression), 250);
  }

  async function preview(expression) {
    const request = ++view.request;
    try {
      const data = await api('/api/cron/preview?' + new URLSearchParams({expression}));
      if (request !== view.request) return;
      view.valid = data;
      show(data.description, `下次 ${data.runs[0].label}`);
      $('fav-cron-tz').textContent = `服务器时区 ${data.timezone}`;
      $('fav-cron-apply').disabled = false;
    } catch (error) {
      if (request !== view.request) return;
      show('表达式无效', error.message, true);
    }
  }

  /** 右对齐按钮，下方放不下时翻到上方。 */
  function place() {
    const box = view.anchor.getBoundingClientRect(), width = pop.offsetWidth, height = pop.offsetHeight;
    const down = innerHeight - box.bottom - GAP - EDGE >= height || box.top < innerHeight - box.bottom;
    pop.style.left = `${Math.round(Math.min(Math.max(EDGE, box.right - width), innerWidth - width - EDGE))}px`;
    pop.style.top = `${Math.round(down ? box.bottom + GAP : Math.max(EDGE, box.top - GAP - height))}px`;
    pop.dataset.side = down ? 'bottom' : 'top';
  }

  const isOpen = () => !!view.anchor;

  function open(anchor, expression, onApply) {
    if (isOpen()) { const same = view.anchor === anchor; close(); if (same) return; }
    view.anchor = anchor; view.onApply = onApply;
    expressionInput.value = expression;
    view.mode = detect(expression);
    if (canPopover) pop.showPopover(); else pop.hidden = false;
    anchor.setAttribute('aria-expanded', 'true');
    renderModes();
    schedulePreview();
    pop.focus({preventScroll: true});
  }

  function close(restoreFocus = false) {
    if (!isOpen()) return;
    const anchor = view.anchor;
    view.anchor = null; clearTimeout(view.timer); view.request++;
    anchor.setAttribute('aria-expanded', 'false');
    if (canPopover) { if (pop.matches(':popover-open')) pop.hidePopover(); } else pop.hidden = true;
    if (restoreFocus && anchor.isConnected) anchor.focus({preventScroll: true});
  }

  for (const name of ['every_minutes', 'every_hours', 'at_minute', 'hour', 'minute', 'day']) {
    const input = field(name);
    input.addEventListener('input', () => { input.value = input.value.replace(/\D/g, ''); fromBuilder(); });
    input.addEventListener('blur', () => { settle(input); fromBuilder(); });
    input.addEventListener('focus', () => input.select());
    input.addEventListener('keydown', event => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      event.preventDefault(); nudge(input, event.key === 'ArrowUp' ? 1 : -1);
    });
  }
  $('fav-cron-code').onclick = () => setMode('custom');
  expressionInput.addEventListener('input', fromExpression);
  pop.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); }
    else if (event.key === 'Enter' && event.target.tagName === 'INPUT') { event.preventDefault(); $('fav-cron-apply').click(); }
  });
  // Esc 在部分浏览器上直接触发所在窗口的 cancel：浮层展开时只收起浮层。
  pop.closest('dialog')?.addEventListener('cancel', event => {
    if (!isOpen()) return;
    event.preventDefault(); event.stopImmediatePropagation(); close(true);
  }, true);
  // 按下在触发按钮上时交给按钮的 click 切换，避免先收起再立即重开。
  document.addEventListener('pointerdown', event => {
    if (isOpen() && !pop.contains(event.target) && !view.anchor.contains(event.target)) close();
  }, true);
  document.addEventListener('close', () => close(), true);
  document.addEventListener('scroll', event => { if (isOpen() && !pop.contains(event.target)) place(); }, true);
  addEventListener('resize', () => { if (isOpen()) place(); });
  $('fav-cron-cancel').onclick = () => close(true);
  $('fav-cron-apply').onclick = () => {
    if (!view.valid) return;
    view.onApply?.({expression: view.valid.expression, description: view.valid.description});
    close(true);
  };
  // 各模式首次切换时使用的默认值。
  field('every_minutes').value = 30; field('every_hours').value = 1; field('at_minute').value = 0;
  field('hour').value = '03'; field('minute').value = '00'; field('day').value = 1;
  if (!canPopover) pop.hidden = true;

  return {open, close, detect};
})();
