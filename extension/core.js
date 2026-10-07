"use strict";
// 无 Chrome/DOM 依赖，便于测试拖动、倍速和按时间触发的边界。
(function(root) {
  function progressed(previous, current, elapsedSeconds) {
    if (!previous || current.video_id !== previous.video_id || current.paused || current.seeking || current.ad || !current.visible) return false;
    const delta = current.position - previous.position;
    return delta > 0 && delta <= Math.max(1.5, elapsedSeconds * Math.max(1, current.rate) + 0.8);
  }
  function activeCard(cards, position, seen) {
    return cards.find(card => card.start <= position && position < card.end && !seen.has(card.expression.toLowerCase())) || null;
  }
  const api = {progressed, activeCard};
  root.CompanionCore = api;
  if (typeof module !== "undefined") module.exports = api;
})(globalThis);
