"use strict";
const $ = id => document.getElementById(id);
const core = globalThis.CompanionCore;
let config = {base:"http://127.0.0.1:8000", token:"", level:"中级"};
let session = null, attachedTab = null, attachedVideo = null, running = false, generation = 0;
let cards = [], seen = new Set(), previous = null, previousAt = 0, lastFetch = 0, polling = false, ticking = false, currentCard = null;
function status(text) { $("status").textContent = text; }
function element(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function empty() { const el = element("div", "等待下一个值得学的表达。", "empty"); el.append(element("small", "只在播放到难点时显示卡片。")); $("card-space").replaceChildren(el); currentCard = null; }
function validateBase(value) { const url = new URL(value); if (url.protocol !== "http:" || !["127.0.0.1","localhost"].includes(url.hostname) || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("请输入 http://127.0.0.1:8000 这样的本地服务地址。"); return url.origin; }
async function api(path, options = {}) {
  let response;
  try { response = await fetch(config.base + path, {...options, signal:AbortSignal.timeout(15000), headers:{"Authorization":`Bearer ${config.token}`, ...(options.body ? {"Content-Type":"application/json"} : {})}}); }
  catch { throw new Error("无法连接本地服务。请启动项目，并检查地址和端口。"); }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "请求失败，请稍后重试。");
  return result;
}
async function activeState() {
  const [tab] = await chrome.tabs.query({active:true, currentWindow:true});
  if (!tab?.id) throw new Error("没有找到当前视频标签页。");
  let state;
  try { state = await chrome.tabs.sendMessage(tab.id, {type:"PLAYBACK_STATE"}); }
  catch { throw new Error("请打开 YouTube 视频；安装或更新扩展后需要刷新该页面。"); }
  if (!state || state.error) throw new Error(state?.error || "视频尚未加载。");
  return {...state, tabId:tab.id};
}
async function stop(message = "伴学已暂停。已保存的表达可以在单词本查看。") {
  generation++; const old = session; session = null; running = false; cards = []; previous = null; attachedVideo = null; currentCard = null;
  $("start").disabled = false; $("stop").disabled = true; $("level").disabled = false; status(message);
  if (old) await api(`/api/video-sessions/${old}/pause`, {method:"POST"}).catch(() => {});
}
$("connect").onclick = async () => {
  try { await stop(); config.base = validateBase($("base-url").value.trim()); config.token = $("token").value.trim(); if (!config.token) throw new Error("请先从主网页的视频伴学页面复制连接码。"); await api("/api/settings"); await chrome.storage.local.set(config); $("connection").open = false; status("连接成功。打开视频后点击开始伴学。"); }
  catch(e) { status(e.message); }
};
$("open-main").onclick = () => { chrome.tabs.create({url:config.base + "/#vocabulary"}); };
$("stop").onclick = () => stop();
$("start").onclick = async () => {
  await stop(""); const epoch = generation; $("start").disabled = true;
  try {
    const state = await activeState(); if (state.ad) throw new Error("请等待广告结束，再开始伴学。");
    config.level = $("level").value; await chrome.storage.local.set({level:config.level});
    const created = await api("/api/video-sessions", {method:"POST", body:JSON.stringify({video_id:state.video_id, title:state.title.slice(0,300), level:config.level, position:state.position})});
    if (epoch !== generation) { await api(`/api/video-sessions/${created.id}/pause`, {method:"POST"}); return; }
    session = created.id; attachedTab = state.tabId; attachedVideo = state.video_id; running = true; cards = []; seen = new Set(); lastFetch = 0; previous = null; empty(); $("video-title").textContent = state.title; $("source").textContent = ""; $("stop").disabled = false; $("level").disabled = true; status("正在准备英文字幕…");
  } catch(e) { if (epoch === generation) { $("start").disabled = false; status(e.message); } }
};
async function poll(state, epoch) {
  if (polling || !session) return; polling = true; lastFetch = Date.now(); const id = session;
  try {
    const data = await api(`/api/video-sessions/${id}?position=${state.position}`);
    if (epoch !== generation) return;
    cards = data.cards; $("source").textContent = data.source;
    if (data.status === "failed" || data.status === "paused") { await stop(data.error || "会话已暂停，请重新开始。"); return; }
    status(data.status === "loading" ? "正在获取英文字幕…" : data.status === "analyzing" ? "正在准备附近的难点，视频可以继续播放。" : state.paused ? "视频已暂停。播放后继续提示难点。" : "正在跟随播放 · 普通句子不会显示卡片");
    if (data.status !== "loading" && !state.paused) await api(`/api/video-sessions/${id}/window`, {method:"POST", body:JSON.stringify({position:state.position})});
  } catch(e) { if (epoch === generation) await stop(e.message); }
  finally { polling = false; }
}
async function showCard(card, state, epoch) {
  if (epoch !== generation || !session) return;
  seen.add(card.expression.toLowerCase()); currentCard = card;
  const box = element("article", undefined, "card"), time = `${Math.floor(card.start/60)}:${String(Math.floor(card.start%60)).padStart(2,"0")}`;
  box.append(element("span", `刚刚遇到 · ${time}`, "time"), element("h2", card.expression), element("p", card.meaning, "meaning"), element("p", card.explanation, "explanation"));
  const detail = element("details"); detail.append(element("summary", "展开原句与翻译"), element("p", card.sentence), element("p", card.translation)); box.append(detail);
  const actions = element("div", undefined, "actions"), replay = element("button", "重播这句"), master = element("button", "已掌握"), favorite = element("button", "收藏"); master.disabled = true; favorite.disabled = true;
  replay.onclick = () => chrome.tabs.sendMessage(attachedTab, {type:"SEEK", video_id:attachedVideo, position:card.start}).catch(() => status("视频标签页已关闭。"));
  actions.append(replay, master, favorite); box.append(actions); const saved = element("p", "正在保存到单词本…", "saved"); box.append(saved); $("card-space").replaceChildren(box);
  try {
    const result = await api(`/api/video-sessions/${session}/exposures`, {method:"POST", body:JSON.stringify({difficulty_id:card.id, position:state.position})});
    if (epoch !== generation) return;
    saved.textContent = "已自动保存到单词本"; master.disabled = false; favorite.disabled = false;
    function action(button, field, label) { button.onclick = async () => { try { await api(`/api/vocabulary/${result.vocabulary_id}`, {method:"PATCH", body:JSON.stringify({[field]:true})}); button.textContent = label; button.disabled = true; } catch(e) { status(e.message); } }; }
    action(master, "mastered", "已掌握 ✓"); action(favorite, "favorite", "已收藏 ★");
  } catch(e) { if (epoch === generation) { saved.textContent = "保存失败：" + e.message; seen.delete(card.expression.toLowerCase()); } }
}
async function tick() {
  if (!running || ticking || document.visibilityState !== "visible") return;
  ticking = true; const epoch = generation;
  try {
    const state = await activeState(); if (epoch !== generation) return;
    if (state.tabId !== attachedTab || state.video_id !== attachedVideo) { empty(); await stop("当前标签页或视频已改变，请点击开始伴学。 "); return; }
    const currentAt = performance.now(), elapsed = (currentAt - previousAt)/1000;
    const playing = core.progressed(previous, state, elapsed);
    if (previous && (state.seeking || Math.abs(state.position - previous.position) > Math.max(2, elapsed * state.rate + 1))) { empty(); cards = []; lastFetch = 0; }
    previous = state; previousAt = currentAt;
    if (state.ad) { status("广告播放中，暂不分析和保存。"); return; }
    if (Date.now() - lastFetch > 2000) void poll(state, epoch);
    if (playing) { const card = core.activeCard(cards, state.position, seen); if (card && card.id !== currentCard?.id) await showCard(card, state, epoch); }
  } catch(e) { if (epoch === generation) await stop(e.message); }
  finally { ticking = false; }
}
setInterval(tick, 500);
document.addEventListener("visibilitychange", () => { previous = null; });
window.addEventListener("pagehide", () => { if (session) fetch(config.base + `/api/video-sessions/${session}/pause`, {method:"POST", keepalive:true, headers:{Authorization:`Bearer ${config.token}`}}).catch(() => {}); });
(async () => { const saved = await chrome.storage.local.get(["base","token","level"]); config = {...config,...saved}; try { config.base = validateBase(config.base); } catch { config.base = "http://127.0.0.1:8000"; } $("base-url").value = config.base; $("token").value = config.token; $("level").value = config.level; $("connection").open = !config.token; if (config.token) { try { await api("/api/settings"); status("已连接。打开视频并点击开始伴学。"); } catch(e) { status(e.message); } } })();
