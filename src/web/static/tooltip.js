'use strict';

/*
 * 悬停说明：带 data-tip（指向隐藏说明元素的 id）或 data-tip-text（纯文本）的元素，
 * 鼠标悬停或键盘聚焦时显示浮层，触屏点按切换，Esc 收起且不连带关闭所在的模态窗口。
 * 浮层是 manual popover，可显示在已打开的 <dialog> 之上；内容在显示时读取，说明元素
 * 被脚本更新后无需额外同步。
 */
(() => {
  const GAP = 8, EDGE = 12;
  const canPopover = typeof HTMLElement.prototype.showPopover === 'function';
  let tip = null, owner = null, pointerType = 'mouse';

  function element() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.id = 'tooltip';
    tip.setAttribute('role', 'tooltip');
    if (canPopover) tip.popover = 'manual'; else tip.hidden = true;
    document.body.append(tip);
    return tip;
  }
  const visible = () => !!owner;
  function content(trigger) {
    const text = trigger.dataset.tipText;
    if (text) return {text};
    const source = trigger.dataset.tip && document.getElementById(trigger.dataset.tip);
    return source ? {html: source.innerHTML} : null;
  }
  function place(trigger) {
    const box = trigger.getBoundingClientRect(), size = tip.getBoundingClientRect();
    const left = Math.min(Math.max(EDGE, box.left + box.width / 2 - size.width / 2), innerWidth - size.width - EDGE);
    const above = box.top - GAP - size.height, below = box.bottom + GAP;
    const top = above >= EDGE || below + size.height > innerHeight - EDGE ? Math.max(EDGE, above) : below;
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
    tip.dataset.side = top === below ? 'bottom' : 'top';
  }
  function show(trigger) {
    const body = content(trigger);
    if (!body) return;
    const el = element();
    if (body.html !== undefined) el.innerHTML = body.html; else el.textContent = body.text;
    el.style.left = '0px'; el.style.top = '0px';
    if (canPopover) { if (el.matches(':popover-open')) el.hidePopover(); el.showPopover(); }
    else el.hidden = false;
    owner = trigger;
    place(trigger);
  }
  function hide() {
    if (!tip || !owner) return;
    owner = null;
    if (canPopover) { if (tip.matches(':popover-open')) tip.hidePopover(); }
    else tip.hidden = true;
  }
  const triggerOf = target => target instanceof Element ? target.closest('[data-tip],[data-tip-text]') : null;

  document.addEventListener('pointerdown', event => { pointerType = event.pointerType || 'mouse'; }, true);
  document.addEventListener('pointerover', event => {
    if (event.pointerType !== 'mouse') return;
    const trigger = triggerOf(event.target);
    if (trigger && trigger !== owner) show(trigger);
  });
  document.addEventListener('pointerout', event => {
    if (event.pointerType !== 'mouse' || !owner) return;
    const into = triggerOf(event.relatedTarget);
    if (triggerOf(event.target) === owner && into !== owner && !owner.matches(':focus-visible')) hide();
  });
  document.addEventListener('focusin', event => {
    const trigger = triggerOf(event.target);
    if (trigger && trigger.matches(':focus-visible')) show(trigger); else if (owner && trigger !== owner) hide();
  });
  document.addEventListener('focusout', event => { if (triggerOf(event.target) === owner) hide(); });
  document.addEventListener('click', event => {
    const trigger = triggerOf(event.target);
    // 说明图标本身没有动作：触屏点按切换说明；带动作的按钮只在悬停 / 聚焦时提示。
    if (trigger && trigger.matches('[role="button"][data-tip]') && pointerType !== 'mouse') {
      if (owner === trigger) hide(); else show(trigger);
      return;
    }
    if (owner && trigger !== owner) hide();
  });
  document.addEventListener('keydown', event => {
    const trigger = triggerOf(event.target);
    if ((event.key === 'Enter' || event.key === ' ') && trigger && trigger.matches('[role="button"][data-tip]')) {
      event.preventDefault();
      if (owner === trigger) hide(); else show(trigger);
      return;
    }
    if (event.key === 'Escape' && visible()) { event.preventDefault(); event.stopPropagation(); hide(); }
  }, true);
  document.addEventListener('scroll', hide, true);
  addEventListener('resize', hide);
  document.addEventListener('close', hide, true);
})();
