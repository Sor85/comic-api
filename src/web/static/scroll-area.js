'use strict';

/*
 * 自绘滚动条：[data-scroll-area] 是滚动视口，原生滚动条隐藏（scrollbar-width:none，Chrome 与
 * Firefox 均支持），轨道画在视口的父容器上。规范与参照项目一致：
 * - 轨道透明、只画滑块；轨道 16px 整条都是命中区，滑块贴外侧，与视口边缘净距 1px。
 * - 滑块默认 5px，悬停轨道或按住拖动时变粗到 7px，粗细只向内长。
 * - 滑动方向两端让出容器圆角，避免滑块伸进圆角被裁掉。
 * - 悬停滚动区或滚动时显示，内容不溢出时整条隐藏。
 */
const scrollAreas = (() => {
  const EDGE = 6, MIN_THUMB = 28, IDLE = 900;

  function attach(viewport) {
    if (viewport.scrollArea) return viewport.scrollArea;
    const host = viewport.parentElement;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    const track = document.createElement('div'), thumb = document.createElement('div');
    track.className = 'sa-track'; thumb.className = 'sa-thumb'; track.setAttribute('aria-hidden', 'true');
    track.append(thumb); host.append(track);
    let frame = 0, idle = 0, drag = null, geometry = null;

    function measure() {
      frame = 0;
      const {scrollHeight, clientHeight, scrollTop} = viewport;
      const overflow = scrollHeight - clientHeight > 1 && clientHeight > 0;
      track.hidden = !overflow;
      if (!overflow) { geometry = null; return; }
      const radius = parseFloat(getComputedStyle(host).borderTopRightRadius) || 0;
      const top = Math.max(viewport.offsetTop + EDGE, radius);
      const bottom = Math.min(viewport.offsetTop + clientHeight - EDGE, host.clientHeight - radius);
      const right = host.clientWidth - (viewport.offsetLeft + viewport.offsetWidth);
      const length = Math.max(0, bottom - top), size = Math.max(MIN_THUMB, length * clientHeight / scrollHeight);
      geometry = {length, size, range: scrollHeight - clientHeight};
      track.style.top = `${top}px`; track.style.height = `${length}px`; track.style.right = `${right}px`;
      thumb.style.height = `${size}px`;
      thumb.style.transform = `translateY(${(length - size) * Math.min(1, scrollTop / geometry.range)}px)`;
    }
    const update = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const wake = () => {
      track.classList.add('sa-active'); clearTimeout(idle);
      idle = setTimeout(() => { if (!drag) track.classList.remove('sa-active'); }, IDLE);
    };

    viewport.addEventListener('scroll', () => { update(); wake(); }, {passive: true});
    const resize = new ResizeObserver(update);
    const watch = () => { resize.disconnect(); resize.observe(viewport); for (const child of viewport.children) resize.observe(child); update(); };
    new MutationObserver(watch).observe(viewport, {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'open', 'class']});
    watch();

    thumb.addEventListener('pointerdown', event => {
      if (!geometry || event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      drag = {y: event.clientY, scroll: viewport.scrollTop};
      thumb.setPointerCapture(event.pointerId); track.classList.add('sa-dragging', 'sa-active');
    });
    thumb.addEventListener('pointermove', event => {
      if (!drag || !geometry) return;
      const ratio = geometry.range / Math.max(1, geometry.length - geometry.size);
      viewport.scrollTop = drag.scroll + (event.clientY - drag.y) * ratio;
    });
    const release = () => { if (!drag) return; drag = null; track.classList.remove('sa-dragging'); wake(); };
    thumb.addEventListener('pointerup', release); thumb.addEventListener('pointercancel', release);
    track.addEventListener('pointerdown', event => {
      if (event.target !== track || !geometry) return;
      const thumbTop = thumb.getBoundingClientRect().top;
      viewport.scrollBy({top: (event.clientY < thumbTop ? -1 : 1) * viewport.clientHeight * 0.9, behavior: 'smooth'});
    });
    // Firefox 与 Chrome 都按视口内容滚动；在轨道上滚轮同样驱动视口。
    track.addEventListener('wheel', event => { viewport.scrollBy({top: event.deltaY}); event.preventDefault(); }, {passive: false});

    viewport.scrollArea = {update};
    return viewport.scrollArea;
  }

  document.querySelectorAll('[data-scroll-area]').forEach(attach);
  return {attach};
})();
