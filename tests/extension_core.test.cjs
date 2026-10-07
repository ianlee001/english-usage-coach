const {test} = require("node:test");
const assert = require("node:assert/strict");
const {progressed, activeCard} = require("../extension/core.js");
const state = {video_id:"abcdefghijk", position:10, paused:false, seeking:false, ad:false, visible:true, rate:1};
test("only actual playback progresses, including double speed", () => {
  assert.equal(progressed(state, {...state, position:10.5}, .5), true);
  assert.equal(progressed(state, {...state, position:11, rate:2}, .5), true);
  for (const change of [{position:80}, {position:1}, {paused:true}, {seeking:true}, {ad:true}, {visible:false}, {video_id:"newvideo"}]) assert.equal(progressed(state, {...state, position:10.5, ...change}, .5), false);
  assert.equal(progressed(null, state, .5), false);
});
test("sparse cards trigger at their own interval and do not replay known expressions", () => {
  const card = {start:10,end:14,expression:"chicken out"};
  assert.equal(activeCard([card], 9, new Set()), null);
  assert.equal(activeCard([card], 14, new Set()), null);
  assert.equal(activeCard([card], 11, new Set()), card);
  assert.equal(activeCard([card], 11, new Set(["chicken out"])), null);
});
