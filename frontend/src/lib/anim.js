// Анимации без runtime-инъекции стилей (совместимо со строгим CSP).
// Svelte-переходы fly/fade инжектят <style> с keyframes — это CSP блокирует, поэтому
// используем классы из бандл-CSS (app.css) + CSS-переменные через CSSOM (element.style — разрешено).
import { number } from './format.js';

const reduce = typeof matchMedia === 'function'
  && matchMedia('(prefers-reduced-motion: reduce)').matches;

// use:counter={value} — анимированный счётчик 0→value при монтировании, мгновенно при обновлении.
export function counter(node, value) {
  let raf;
  function to(v, animate) {
    v = Number(v) || 0;
    cancelAnimationFrame(raf);
    if (!animate || reduce || v === 0) { node.textContent = number(v); return; }
    const dur = 650, start = performance.now();
    (function frame(now) {
      const p = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - p, 3);
      node.textContent = number(Math.round(v * e));
      if (p < 1) raf = requestAnimationFrame(frame);
    })(start);
  }
  to(value, true);
  return { update: (v) => to(v, false), destroy: () => cancelAnimationFrame(raf) };
}

// use:reveal={index} — каскадное появление карточки (класс .rise + задержка через CSS-переменную).
export function reveal(node, index = 0) {
  if (reduce) return {};
  node.style.setProperty('--d', Math.min(index * 0.05, 0.4) + 's');
  node.classList.add('rise');
  return {};
}
