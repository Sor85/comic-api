'use strict';

/*
 * 自绘数字输入框增减按钮：
 * - enhance(input)：原生 <input type="number"> 保留为数据源（name、值、校验照旧），隐藏浏览器原生
 *   箭头，在右侧叠加无底色的上下箭头按钮。按钮按 step 增减并夹在 min/max 之间，随后派发
 *   input / change 事件，与手动输入一致。
 * - 输入框禁用时箭头隐藏；脚本修改 disabled 后自动同步。
 * 键盘用户可直接用方向键调整数值，箭头按钮不进入 Tab 序列。
 */
const numberInput = (() => {
  const ICONS = {
    up: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 15l6-6 6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
  };

  function nudge(input, direction) {
    if (input.disabled || input.readOnly) return;
    const step = Number(input.step) || 1;
    const min = input.min === '' ? -Infinity : Number(input.min);
    const max = input.max === '' ? Infinity : Number(input.max);
    const current = input.value === '' ? (Number.isFinite(min) ? min : 0) : Number(input.value);
    const next = Math.max(min, Math.min(max, (Number.isFinite(current) ? current : 0) + direction * step));
    if (String(next) === input.value) return;
    input.value = String(next);
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  }

  // 外层 <label> 的标题：分组行取 <strong>，普通字段取第一个文本节点并去掉括号内的说明。
  function labelText(input) {
    const label = input.closest('label');
    if (!label) return '';
    const strong = label.querySelector('strong');
    const text = strong ? strong.textContent : [...label.childNodes].find(child => child.nodeType === 3 && child.textContent.trim())?.textContent || '';
    return text.replace(/[（(].*$/, '').trim();
  }

  function enhance(input) {
    if (input.numberInput) return input.numberInput;
    const wrap = document.createElement('span'), steps = document.createElement('span');
    wrap.className = 'num-input'; steps.className = 'num-steps';
    const label = input.getAttribute('aria-label') || labelText(input) || '数值';
    for (const [key, direction, text] of [['up', 1, '增加'], ['down', -1, '减少']]) {
      const button = document.createElement('button');
      button.type = 'button'; button.className = `num-step num-step-${key}`; button.tabIndex = -1;
      button.setAttribute('aria-label', `${text}${label}`);
      button.innerHTML = ICONS[key];
      // 保持焦点在输入框内，避免点击箭头时触发输入框失焦。
      button.addEventListener('pointerdown', event => event.preventDefault());
      button.addEventListener('click', event => { event.preventDefault(); nudge(input, direction); });
      steps.append(button);
    }
    input.before(wrap); wrap.append(input, steps);

    const sync = () => { steps.hidden = input.disabled || input.readOnly; };
    new MutationObserver(sync).observe(input, {attributes: true, attributeFilter: ['disabled', 'readonly']});
    sync();
    return (input.numberInput = {wrap, sync});
  }

  function enhanceAll(root = document) {
    root.querySelectorAll('input[type="number"]').forEach(enhance);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => enhanceAll());
  else enhanceAll();

  return {enhance, enhanceAll};
})();
