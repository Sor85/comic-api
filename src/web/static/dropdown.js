'use strict';

/*
 * 自绘下拉菜单：
 * - enhance(select)：原生 <select> 保留在 DOM 中作为数据源（值、name、change/input 事件照旧），
 *   视觉上由触发按钮和浮层列表替代。脚本写 select.value / selectedIndex、增删选项、切换
 *   disabled / hidden 时触发按钮自动同步。
 * - open(options)：通用操作菜单，可锚定到按钮或指针坐标（右键菜单）。
 * 浮层是 manual popover，显示在已打开的 <dialog> 之上；Esc 只收起菜单，不连带关闭所在窗口。
 * 列表滚动使用 scroll-area.js 的自绘滚动条。
 */
const dropdown = (() => {
  const GAP = 6, EDGE = 12, MAX_HEIGHT = 360, PAD = 10;
  const canPopover = typeof HTMLElement.prototype.showPopover === 'function';
  const CHEVRON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
  const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>';
  const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  let current = null, uid = 0, typed = '', typedAt = 0;

  function place() {
    const {pop, list, anchor, point, align} = current;
    const box = point ? {left: point.x, right: point.x, top: point.y, bottom: point.y, width: 0} : anchor.getBoundingClientRect();
    if (!point && anchor.matches('.dd-trigger')) pop.style.minWidth = `${Math.round(box.width)}px`;
    const below = innerHeight - box.bottom - GAP - EDGE, above = box.top - GAP - EDGE;
    list.style.maxHeight = `${MAX_HEIGHT}px`;
    const natural = pop.offsetHeight, down = natural <= below || below >= above;
    list.style.maxHeight = `${Math.max(80, Math.min(MAX_HEIGHT, (down ? below : above) - PAD))}px`;
    const height = pop.offsetHeight, width = pop.offsetWidth;
    const left = align === 'end' && !point ? box.right - width : box.left;
    pop.style.left = `${Math.round(Math.min(Math.max(EDGE, left), innerWidth - width - EDGE))}px`;
    pop.style.top = `${Math.round(down ? box.bottom + GAP : Math.max(EDGE, box.top - GAP - height))}px`;
    pop.dataset.side = down ? 'bottom' : 'top';
  }

  function reveal(entry) {
    const {list} = current, el = entry.el;
    if (el.offsetTop < list.scrollTop) list.scrollTop = el.offsetTop - 4;
    else if (el.offsetTop + el.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = el.offsetTop + el.offsetHeight - list.clientHeight + 4;
  }
  function activate(index, scroll) {
    if (!current) return;
    current.entries.forEach((entry, i) => entry.el.classList.toggle('active', i === index));
    current.index = index;
    const entry = current.entries[index];
    if (entry) { current.list.setAttribute('aria-activedescendant', entry.el.id); if (scroll) reveal(entry); }
    else current.list.removeAttribute('aria-activedescendant');
  }
  function move(from, step) {
    const {entries} = current;
    for (let i = from + step; i >= 0 && i < entries.length; i += step) if (!entries[i].item.disabled) return i;
    return from;
  }
  function pick(index) {
    const entry = current?.entries[index];
    if (!entry || entry.item.disabled) return;
    const {onPick, point, anchor, owner, align, opts} = current;
    // 项自带 onPick 时由它负责（分组筛选），否则回落菜单级回调（enhance 的 select 同步）。
    (entry.item.onPick || onPick)?.(entry.item);
    // 粘性项（分组筛选）选完不收起：原位按新的选中态重建列表，高亮留在该项上。
    if (entry.item.sticky && !point) { close(false); show({...opts, anchor, owner, align, index, opts}); return; }
    close(true);
  }

  function close(restoreFocus) {
    if (!current) return;
    const {pop, owner} = current;
    current = null;
    owner?.setAttribute('aria-expanded', 'false');
    if (restoreFocus && owner?.isConnected) owner.focus({preventScroll: true});
    pop.style.pointerEvents = 'none';
    const remove = () => { if (canPopover && pop.matches(':popover-open')) pop.hidePopover(); pop.remove(); };
    if (reducedMotion()) remove();
    else pop.animate([{opacity: 1}, {opacity: 0}], {duration: 110, easing: 'ease-out', fill: 'forwards'}).finished.then(remove, remove);
  }

  function typeahead(key) {
    const now = Date.now();
    typed = now - typedAt > 600 ? key : typed + key; typedAt = now;
    const {entries, index} = current, needle = typed.toLocaleLowerCase();
    for (let n = 1; n <= entries.length; n++) {
      const i = (index + (typed.length > 1 ? n - 1 : n) + entries.length) % entries.length;
      if (!entries[i].item.disabled && entries[i].item.label.toLocaleLowerCase().startsWith(needle)) { activate(i, true); return; }
    }
  }
  function onKey(event) {
    if (!current) return;
    const {index, entries} = current;
    switch (event.key) {
      case 'ArrowDown': activate(index < 0 ? move(-1, 1) : move(index, 1), true); break;
      case 'ArrowUp': activate(index < 0 ? move(entries.length, -1) : move(index, -1), true); break;
      case 'Home': activate(move(-1, 1), true); break;
      case 'End': activate(move(entries.length, -1), true); break;
      case 'PageDown': { let i = index; for (let n = 0; n < 8; n++) i = move(i, 1); activate(i, true); break; }
      case 'PageUp': { let i = index; for (let n = 0; n < 8; n++) i = move(i, -1); activate(i, true); break; }
      case 'Enter': case ' ': pick(index); break;
      case 'Escape': close(true); break;
      case 'Tab': close(false); return;
      default:
        if (event.key.length !== 1 || event.ctrlKey || event.metaKey || event.altKey) return;
        typeahead(event.key);
    }
    event.preventDefault(); event.stopPropagation();
  }

  /**
   * items: [{label, icon?(svg 字符串), danger?, disabled?, selected?, divider?, ...任意数据}]
   * anchor：锚定元素；point：{x,y} 指针坐标（右键菜单）；owner：展开状态与焦点归还的目标。
   */
  function open(options) {
    if (current && options.owner && current.owner === options.owner && !options.point) { close(true); return; }
    close(false);
    show({...options, opts: options});
  }

  /** 构建并展开浮层；粘性项选完按新的选中态重建，因此与 open 共用同一份参数。 */
  function show({anchor, point, owner = anchor, items, role = 'menu', align = 'start', label, className, onPick, index = -1, opts = {}}) {
    // items 可以是函数：粘性项选完重建时重新取值，才能反映最新选中态。
    if (typeof items === 'function') items = items();
    const pop = document.createElement('div'), list = document.createElement('div'), id = `dd-${++uid}`;
    pop.className = 'dd-pop' + (className ? ` ${className}` : '');
    if (canPopover) pop.popover = 'manual';
    list.className = 'dd-list'; list.id = id; list.tabIndex = -1; list.setAttribute('role', role); list.setAttribute('data-scroll-area', '');
    if (label) list.setAttribute('aria-label', label);
    const entries = [];
    for (const item of items) {
      if (item.heading !== undefined) { const head = document.createElement('div'); head.className = 'dd-heading'; head.setAttribute('role', 'presentation'); head.textContent = item.heading; list.append(head); continue; }
      if (item.divider) { const sep = document.createElement('div'); sep.className = 'dd-sep'; sep.setAttribute('role', 'separator'); list.append(sep); continue; }
      const el = document.createElement('div'), index = entries.length;
      el.id = `${id}-${index}`; el.className = 'dd-item' + (item.danger ? ' danger' : '');
      el.setAttribute('role', role === 'listbox' ? 'option' : 'menuitem');
      if (role === 'listbox') el.setAttribute('aria-selected', String(!!item.selected));
      if (item.disabled) el.setAttribute('aria-disabled', 'true');
      if (item.icon) el.insertAdjacentHTML('beforeend', item.icon);
      const text = document.createElement('span'); text.className = 'dd-label'; text.textContent = item.label; el.append(text);
      if (role === 'listbox') el.insertAdjacentHTML('beforeend', `<span class="dd-check">${CHECK}</span>`);
      el.addEventListener('pointermove', () => { if (current?.index !== index && !item.disabled) activate(index, false); });
      el.addEventListener('click', () => pick(index));
      entries.push({item, el}); list.append(el);
    }
    pop.append(list);
    // 模态窗口打开时窗口外的节点不可交互，浮层挂在所在窗口内部；不放进 <label>，避免点击选项被转发给控件。
    (anchor?.closest('dialog[open]') || document.body).append(pop);
    current = {pop, list, entries, index: -1, anchor, point, owner, align, items, role, label, className, onPick, opts};
    if (canPopover) pop.showPopover();
    owner?.setAttribute('aria-expanded', 'true');
    place();
    if (typeof scrollAreas !== 'undefined') scrollAreas.attach(list);
    list.addEventListener('keydown', onKey);
    list.addEventListener('focusout', event => { if (current?.pop === pop && !current.ownerPress && !pop.contains(event.relatedTarget)) close(false); });
    const selected = index >= 0 && index < entries.length ? index : entries.findIndex(entry => entry.item.selected);
    activate(selected >= 0 ? selected : -1, true);
    list.focus({preventScroll: true});
  }

  function enhance(select) {
    if (select.dropdown) return select.dropdown;
    const wrap = document.createElement('span'), trigger = document.createElement('button'), value = document.createElement('span');
    wrap.className = 'dd-select'; if (select.id) wrap.dataset.for = select.id;
    trigger.type = 'button'; trigger.className = 'dd-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    const name = select.getAttribute('aria-label');
    if (name) trigger.setAttribute('aria-label', name);
    value.className = 'dd-value';
    trigger.append(value); trigger.insertAdjacentHTML('beforeend', CHEVRON);
    // 触发按钮排在 select 之前：外层 <label> 的点击转给按钮，即点标签文字也能展开。
    select.before(wrap); wrap.append(trigger, select);
    select.classList.add('dd-native'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');

    const sync = () => {
      const option = select.options[select.selectedIndex];
      value.textContent = option ? option.textContent : '';
      trigger.title = option ? option.textContent : '';
      trigger.disabled = select.disabled; wrap.hidden = select.hidden;
    };
    for (const key of ['value', 'selectedIndex']) {
      const native = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, key);
      Object.defineProperty(select, key, {configurable: true, get() { return native.get.call(this); }, set(next) { native.set.call(this, next); sync(); }});
    }
    new MutationObserver(records => {
      sync();
      if (current?.owner === trigger && records.some(record => record.type !== 'attributes' || record.attributeName !== 'hidden')) close(false);
    }).observe(select, {childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['disabled', 'hidden', 'selected', 'label']});

    const show = () => open({
      anchor: trigger, role: 'listbox', label: name || undefined, className: 'dd-select-pop',
      items: [...select.options].map(option => ({label: option.textContent, value: option.value, disabled: option.disabled, selected: option.selected})),
      onPick: item => {
        if (select.value === item.value) return;
        select.value = item.value;
        select.dispatchEvent(new Event('input', {bubbles: true}));
        select.dispatchEvent(new Event('change', {bubbles: true}));
      },
    });
    trigger.addEventListener('click', show);
    trigger.addEventListener('keydown', event => {
      if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); event.stopPropagation(); show(); }
      // 回车 / 空格交给按钮的原生点击展开，只阻止冒泡到页面级快捷键（例如阅读器翻页）。
      else if (['Enter', ' '].includes(event.key)) event.stopPropagation();
    });
    sync();
    select.dropdown = {trigger, sync};
    return select.dropdown;
  }

  document.addEventListener('pointerdown', event => {
    if (!current) return;
    // 在触发按钮上按下时由随后的 click 负责收起，焦点离开列表不提前关闭。
    if (current.owner?.contains(event.target)) { current.ownerPress = true; return; }
    if (!current.pop.contains(event.target)) close(false);
  }, true);
  document.addEventListener('pointerup', () => setTimeout(() => { if (current) current.ownerPress = false; }), true);
  document.addEventListener('scroll', event => { if (current && !current.pop.contains(event.target)) close(false); }, true);
  document.addEventListener('close', () => close(false), true);
  addEventListener('resize', () => close(false));
  addEventListener('blur', () => close(false));

  /**
   * 分组筛选菜单：把若干隐藏的原生 <select> 合并到一枚触发按钮下。
   * groups：[{select, default?}]，首项视为排序，当前值写在按钮文字上；其余项偏离 default 时计入徽标。
   * 每组以 select 的 aria-label 为标题，组内互斥；选中后派发 change，菜单保持展开并刷新勾选。
   * 返回 sync()：脚本改动 select 值后调用以刷新按钮。
   */
  function filterMenu(trigger, groups) {
    const items = () => groups.flatMap(({select}) => [
      {heading: select.getAttribute('aria-label') || ''},
      ...[...select.options].map(option => {
        const value = option.value;
        return {label: option.textContent, value, disabled: option.disabled, selected: option.selected, sticky: true,
          onPick: () => { if (select.value === value) return; select.value = value; select.dispatchEvent(new Event('change', {bubbles: true})); }};
      }),
    ]);
    const label = trigger.querySelector('span'), [sort, ...rest] = groups;
    const sync = () => {
      if (sort && label) label.textContent = sort.select.options[sort.select.selectedIndex]?.textContent || '';
      const active = rest.filter(({select, default: initial = ''}) => select.value !== initial).length;
      trigger.dataset.state = active ? 'filtered' : '';
      let badge = trigger.querySelector('.menu-count');
      if (!badge) { badge = document.createElement('span'); badge.className = 'menu-count'; trigger.append(badge); }
      badge.textContent = active ? String(active) : '';
    };
    trigger.addEventListener('click', () => open({anchor: trigger, owner: trigger, role: 'listbox', label: trigger.getAttribute('aria-label') || '筛选', className: 'dd-filter-pop', items}));
    for (const {select} of groups) select.addEventListener('change', sync);
    sync();
    return {sync};
  }

  document.querySelectorAll('select:not([data-menu])').forEach(enhance);
  return {enhance, open, close, filterMenu};
})();
