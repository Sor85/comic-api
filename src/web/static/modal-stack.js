'use strict';

/*
 * 层叠模态卡片：新打开的窗口从下方升起，下面一层轻微缩小、上移，在新窗口顶边之上露出一道
 * 轮廓，形成 iOS 模态卡片的空间层次；前景关闭时下层平滑放大回原位。页面本身是栈底，任何
 * 窗口打开时页面原地虚化、压暗（见 app.css 的 body[data-modal-depth]）。
 *
 * 所有 <dialog>（阅读器除外）在加载时登记。实例上的 showModal / close 被包装：
 * - close() 先播退场动画，结束后才调用原生 close；退场期间 open 读作 false，
 *   业务代码可以照常判断窗口已关闭，退场中再次 showModal() 会直接收回退场。
 * - Esc 触发的 cancel 改走同一条退场路径；浏览器跳过 cancel 直接关闭时同样整理栈。
 * 动画走 Web Animations API，可从当前帧接着改目标，连续开合不跳变。
 */
const modalStack = (() => {
  const SCALE_STEP = 0.06, MIN_SCALE = 0.82, PEEK = 14, LIFT = 10, SAFE_TOP = 8;
  const DURATION = 480, EXIT = 300;
  const EASING = 'cubic-bezier(0.32, 0.72, 0, 1)', EXIT_EASING = 'cubic-bezier(0.4, 0, 0.6, 1)';
  const proto = HTMLDialogElement.prototype;
  const nativeShow = proto.showModal, nativeClose = proto.close;
  const openProperty = Object.getOwnPropertyDescriptor(proto, 'open');
  const entries = [], running = new WeakMap(), targets = new WeakMap(), closing = new WeakMap();
  let scheduled = false, resizeObserver = null;

  const nativeOpen = dialog => openProperty.get.call(dialog);
  const active = dialog => nativeOpen(dialog) && !closing.has(dialog);
  const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const travel = () => Math.min(innerHeight * 0.14, 112);

  /** 去掉当前 transform（含进行中的动画）后的布局盒。 */
  function naturalBox(element) {
    const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
    const [originX = '', originY = ''] = style.transformOrigin.split(' ');
    let scaleX = 1, scaleY = 1, translateX = 0, translateY = 0;
    if (style.transform && style.transform !== 'none') {
      const matrix = new DOMMatrix(style.transform);
      scaleX = matrix.a || 1; scaleY = matrix.d || 1; translateX = matrix.e; translateY = matrix.f;
    }
    const width = rect.width / scaleX, height = rect.height / scaleY;
    const ox = Number.isFinite(parseFloat(originX)) ? parseFloat(originX) : width / 2;
    const oy = Number.isFinite(parseFloat(originY)) ? parseFloat(originY) : height / 2;
    const top = rect.top - oy * (1 - scaleY) - translateY, left = rect.left - ox * (1 - scaleX) - translateX;
    return {top, bottom: top + height, left, right: left + width, originY: oy};
  }

  function animateTo(element, target) {
    const identity = target.ty === 0 && target.scale === 1, previous = running.get(element);
    // 没退到过背景的窗口不碰：它可能正在播自己的进场动画。
    if (identity && !previous) { targets.delete(element); return; }
    const last = targets.get(element);
    if (last && Math.abs(last.ty - target.ty) < 0.5 && Math.abs(last.scale - target.scale) < 0.001) return;
    targets.set(element, target);
    const current = getComputedStyle(element).transform;
    previous?.cancel();
    const animation = element.animate([
      {transform: current && current !== 'none' ? current : 'none'},
      {transform: identity ? 'none' : `translateY(${target.ty}px) scale(${target.scale})`},
    ], {duration: reducedMotion() ? 0 : DURATION, easing: EASING, fill: 'forwards'});
    running.set(element, animation);
    if (identity) animation.onfinish = () => {
      // 回到原位后撤掉动画：一直 fill 着会压住窗口自己的退场动画。
      if (running.get(element) !== animation) return;
      animation.cancel(); running.delete(element); targets.delete(element);
    };
  }

  function placeBehind(element, depth, reference) {
    const box = naturalBox(element);
    const width = box.right - box.left, height = box.bottom - box.top, center = (box.left + box.right) / 2;
    let scale = Math.max(MIN_SCALE, 1 - SCALE_STEP * depth);
    // 左右都被上一层盖住时，只让顶边露出来；下层太高就再缩一点，底边收进上一层之内。
    const sideways = center - width * scale / 2 >= reference.left - 1 && center + width * scale / 2 <= reference.right + 1;
    if (sideways) {
      const room = reference.bottom - (reference.top - PEEK);
      if (height * scale > room) scale = Math.max(MIN_SCALE, room / height);
    }
    const scaledTop = box.top + box.originY * (1 - scale), scaledHeight = height * scale;
    let visualTop = Math.min(reference.top - PEEK, scaledTop - LIFT);
    if (sideways && visualTop + scaledHeight > reference.bottom) visualTop = reference.bottom - scaledHeight;
    visualTop = Math.max(visualTop, Math.min(SAFE_TOP, scaledTop));
    element.dataset.modalDepth = String(depth);
    animateTo(element, {ty: visualTop - scaledTop, scale});
    return {top: visualTop, bottom: visualTop + scaledHeight, left: center - width * scale / 2, right: center + width * scale / 2, originY: 0};
  }

  function layout() {
    scheduled = false;
    const stack = entries.filter(active);
    let reference = null;
    for (let index = stack.length - 1, depth = 0; index >= 0; index--, depth++) {
      const element = stack[index];
      if (!reference) { delete element.dataset.modalDepth; animateTo(element, {ty: 0, scale: 1}); reference = naturalBox(element); }
      else reference = placeBehind(element, depth, reference);
    }
    if (stack.length) document.body.dataset.modalDepth = String(stack.length);
    else delete document.body.dataset.modalDepth;
    document.documentElement.toggleAttribute('data-modal-open', stack.length > 0);
  }
  function schedule() { if (!scheduled) { scheduled = true; queueMicrotask(layout); } }

  function backdrop(dialog, frames, options) {
    try { return dialog.animate(frames, {...options, pseudoElement: '::backdrop'}); } catch { return null; }
  }
  function enter(dialog) {
    const duration = reducedMotion() ? 120 : DURATION;
    const lift = reducedMotion() ? 'none' : `translateY(${travel()}px) scale(0.985)`;
    dialog.animate([{opacity: 0, transform: lift}, {opacity: 1, offset: 0.6}, {opacity: 1, transform: 'none'}], {duration, easing: EASING});
    backdrop(dialog, [{opacity: 0}, {opacity: 1}], {duration, easing: EASING});
  }
  function stopExit(dialog) {
    const exit = closing.get(dialog);
    if (!exit) return;
    closing.delete(dialog); exit.panel.cancel(); exit.backdrop?.cancel();
    delete dialog.dataset.modalClosing;
  }
  function forget(dialog) {
    const index = entries.indexOf(dialog);
    if (index !== -1) entries.splice(index, 1);
    running.get(dialog)?.cancel(); running.delete(dialog); targets.delete(dialog);
    delete dialog.dataset.modalDepth; delete dialog.dataset.modalLevel;
    schedule();
  }
  function finish(dialog, value) {
    stopExit(dialog); forget(dialog);
    if (nativeOpen(dialog)) nativeClose.call(dialog, value);
  }

  function show() {
    if (closing.has(this)) { stopExit(this); forget(this); }
    else if (nativeOpen(this)) return;
    else nativeShow.call(this);
    entries.push(this);
    this.dataset.modalLevel = String(entries.filter(active).length);
    enter(this); schedule();
  }
  function close(value) {
    if (!nativeOpen(this) || closing.has(this)) return;
    if (reducedMotion()) { finish(this, value); return; }
    // 退到背景过的窗口保持当前位置只淡出，不先弹回原位再消失。
    const receded = running.has(this);
    const panel = this.animate(receded
      ? [{opacity: 1}, {opacity: 0, offset: 0.45}, {opacity: 0}]
      : [{opacity: 1, transform: 'none'}, {opacity: 0, offset: 0.45}, {opacity: 0, transform: `translateY(${travel() * 1.15}px) scale(0.985)`}],
    {duration: EXIT, easing: EXIT_EASING, fill: 'forwards'});
    const exit = {panel, backdrop: backdrop(this, [{opacity: 1}, {opacity: 0}], {duration: EXIT, easing: EXIT_EASING, fill: 'forwards'})};
    closing.set(this, exit); this.dataset.modalClosing = '';
    schedule();
    panel.finished.then(() => { if (closing.get(this) === exit) finish(this, value); }, () => {});
  }

  function install(dialog) {
    Object.defineProperty(dialog, 'open', {configurable: true, get() { return active(this); }, set(value) { openProperty.set.call(this, value); }});
    dialog.showModal = show; dialog.close = close;
    dialog.addEventListener('cancel', event => { if (event.defaultPrevented) return; event.preventDefault(); dialog.close(); });
    // 浏览器不经 close() 直接关闭（例如连续 Esc 跳过 cancel）时同样整理栈。
    dialog.addEventListener('close', () => { if (!nativeOpen(dialog)) { stopExit(dialog); forget(dialog); } });
    resizeObserver ??= new ResizeObserver(schedule);
    resizeObserver.observe(dialog);
  }

  document.querySelectorAll('dialog:not(#reader-dialog)').forEach(install);
  addEventListener('resize', schedule, {passive: true});
  return {install, layout: schedule};
})();
