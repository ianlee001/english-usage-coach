"use strict";
const $ = (id) => document.getElementById(id);
let thread = null, busy = false, vocabGeneration = 0;
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function notice(text = "") { $("notice").textContent = text; $("notice").hidden = !text; }
async function api(path, options = {}) {
  const headers = {...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  let response;
  try { response = await fetch(path, {...options, headers}); } catch { throw new Error("无法连接本地服务，请检查启动终端。"); }
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(typeof error.detail === "string" ? error.detail : "请求失败，请稍后重试。"); }
  return response;
}
const json = async (path, options) => (await api(path, options)).json();
function link(label, url) {
  const a = node("a", label);
  try { const parsed = new URL(url); if (!["http:", "https:"].includes(parsed.protocol)) return node("span", label); a.href = parsed.href; } catch { return node("span", label); }
  a.target = "_blank"; a.rel = "noopener noreferrer"; return a;
}
function inline(parent, text) {
  // 只通过 DOM 构造有限 Markdown，不执行模型/字幕中的 HTML。
  const pattern = /(\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\))/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(offset, match.index)));
    parent.append(match[2] ? node("strong", match[2]) : match[3] ? node("code", match[3]) : link(match[4], match[5]));
    offset = match.index + match[0].length;
  }
  parent.append(document.createTextNode(text.slice(offset)));
}
function rich(parent, text) {
  parent.replaceChildren(); let code = null;
  for (const line of text.split("\n")) {
    if (line.startsWith("```")) { if (code) code = null; else { code = node("pre", ""); parent.append(code); } continue; }
    if (code) { code.textContent += line + "\n"; continue; }
    const p = node(/^#{1,6} /.test(line) ? "h3" : "p"); inline(p, line.replace(/^#{1,6} /, "").replace(/^[-*] /, "• ")); parent.append(p);
  }
}
function sources(parent, items) {
  if (!items?.length) return;
  const details = node("details", undefined, "sources"); details.append(node("summary", `查看 ${items.length} 条参考来源`));
  for (const source of items) { const p = node("p"); p.append(source.url ? link(`${source.label} · ${source.title || source.url}`, source.url) : node("strong", `${source.label} · ${source.filename}${source.page ? ` · 第 ${source.page} 页` : ""}`)); p.append(node("p", source.excerpt || "")); details.append(p); }
  parent.append(details);
}
function message(role, text) { const box = node("article", undefined, `message ${role}`); box.append(node("div", role === "user" ? "YOU" : "ENGLISH COACH", "role")); const body = node("div", undefined, "answer"); rich(body, text); box.append(body); $("messages").append(box); return {box, body}; }
function welcome() {
  const box = node("div", undefined, "welcome"); box.append(node("span", "A LITTLE MORE NATURAL, EVERY DAY", "eyebrow"), node("h2", "从一句不太懂的英语开始。"), node("p", "理解词句背后的语境、语气和真实用法。\n也可以聊聊你刚在视频里遇到的表达。"));
  const examples = node("div", undefined, "suggestions");
  for (const text of ["chicken out 是什么意思？", "怎样委婉地表达不同意见？", "帮我复习单词本里的表达"]) { const b = node("button", text); b.onclick = () => { $("question").value = text; $("question").focus(); }; examples.append(b); }
  box.append(examples); $("messages").replaceChildren(box);
}
async function loadConversations() {
  const rows = await json("/api/conversations"); $("conversations").replaceChildren();
  for (const row of rows) { const button = node("button", row.title, row.id === thread ? "selected" : ""); button.title = row.title; button.disabled = busy; button.onclick = () => selectThread(row.id).catch(e => notice(e.message)); $("conversations").append(button); }
  return rows;
}
async function selectThread(id) { if (busy) return; thread = id; location.hash = "chat"; await loadMessages(); await loadConversations(); }
async function loadMessages() {
  if (!thread) { welcome(); return; }
  const turns = await json(`/api/conversations/${thread}/messages`); $("messages").replaceChildren();
  if (!turns.length) welcome();
  for (const turn of turns) { message("user", turn.question); const reply = message("assistant", turn.status === "failed" ? `本轮未完成：${turn.error}` : turn.status === "pending" ? "回答正在生成，请稍后刷新。" : turn.answer); sources(reply.box, turn.sources); }
  $("messages").scrollTop = $("messages").scrollHeight;
}
async function view() {
  const name = ["chat", "vocabulary", "materials", "companion"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "chat";
  document.querySelectorAll(".view").forEach(el => el.hidden = el.id !== name);
  document.querySelectorAll("nav button").forEach(el => el.classList.toggle("active", el.dataset.view === name));
  $("page-title").textContent = {chat: "学习对话", vocabulary: "我的单词本", materials: "学习资料", companion: "视频伴学"}[name];
  if (name === "vocabulary") await loadVocabulary();
  if (name === "materials") await loadDocuments();
}
document.querySelectorAll("nav button").forEach(el => el.onclick = () => location.hash = el.dataset.view);
window.addEventListener("hashchange", () => view().catch(e => notice(e.message)));
window.addEventListener("focus", () => { if (location.hash === "#vocabulary") loadVocabulary().catch(e => notice(e.message)); });
$("new-chat").onclick = async () => { if (busy) return; try { thread = (await json("/api/conversations", {method:"POST"})).id; await selectThread(thread); } catch(e) { notice(e.message); } };
$("question").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("chat-form").requestSubmit(); } });
$("chat-form").onsubmit = async event => {
  event.preventDefault(); const question = $("question").value.trim(); if (!question || busy) return;
  busy = true; $("send").disabled = true; $("new-chat").disabled = true; notice();
  try {
    if (!thread) thread = (await json("/api/conversations", {method:"POST"})).id;
    $("messages").querySelector(".welcome")?.remove(); message("user", question); const answer = message("assistant", "正在思考…"); $("question").value = ""; await loadConversations();
    const response = await api(`/api/conversations/${thread}/messages`, {method:"POST", body:JSON.stringify({question})});
    const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "", finished = false;
    function consume(line) { if (!line.trim()) return; const item = JSON.parse(line); if (item.type === "text") rich(answer.body, item.data); if (item.type === "status") $("chat-status").textContent = item.data; if (item.type === "done") { finished = true; rich(answer.body, item.data.answer); sources(answer.box, item.data.sources); } if (item.type === "error") { finished = true; rich(answer.body, `本轮未完成：${item.data}`); notice(item.data); } }
    while (true) { const {value, done} = await reader.read(); buffer += decoder.decode(value, {stream:!done}); const lines = buffer.split("\n"); buffer = lines.pop(); lines.forEach(consume); $("messages").scrollTop = $("messages").scrollHeight; if (done) { consume(buffer); break; } }
    if (!finished) throw new Error("连接已中断，后台可能仍在处理。请稍后打开该聊天查看结果。");
  } catch(e) { notice(e.message); $("question").value = question; }
  finally { busy = false; $("send").disabled = false; $("new-chat").disabled = false; $("chat-status").textContent = "中文讲解 · 英文例句 · 保留学习语境"; await loadConversations().catch(() => {}); }
};
async function loadVocabulary() {
  const generation = ++vocabGeneration, params = new URLSearchParams({query:$("vocab-query").value, video_id:$("vocab-video").value.trim(), favorite:$("vocab-favorite").checked});
  if ($("vocab-state").value) params.set("mastered", $("vocab-state").value);
  const [entries, videos] = await Promise.all([json(`/api/vocabulary?${params}`), json("/api/videos")]); if (generation !== vocabGeneration) return;
  const selectedVideo = $("vocab-video").value; $("vocab-video").replaceChildren(); const allVideos = node("option", "所有来源视频"); allVideos.value = ""; $("vocab-video").append(allVideos);
  for (const video of videos) { const option = node("option", `${video.title}（${video.count} 个表达）`); option.value = video.video_id; $("vocab-video").append(option); } $("vocab-video").value = selectedVideo;
  $("vocab-list").replaceChildren(); $("vocab-count").textContent = `找到 ${entries.length} 个表达（每次最多显示 200 个，可搜索筛选）`;
  if (!entries.length) $("vocab-list").append(node("div", "这里还没有符合条件的表达。\n打开视频伴学，实际遇到的难点会自动保存到这里。", "empty"));
  for (const entry of entries) {
    const card = node("article", undefined, "word-card"); card.append(node("span", entry.mastered ? "已掌握" : "待学习", "eyebrow"), node("h3", entry.expression), node("p", entry.meaning, "meaning"), node("p", entry.explanation, "muted"));
    const actions = node("div", undefined, "actions");
    for (const [key, label] of [["favorite", entry.favorite ? "★ 已收藏" : "☆ 收藏"], ["mastered", entry.mastered ? "重新学习" : "标记已掌握"]]) { const button = node("button", label); button.onclick = async () => { try { await json(`/api/vocabulary/${entry.id}`, {method:"PATCH", body:JSON.stringify({[key]:!entry[key]})}); await loadVocabulary(); } catch(e) { notice(e.message); } }; actions.append(button); }
    const ask = node("button", "继续请教"); ask.onclick = () => { location.hash = "chat"; $("question").value = `结合单词本里的语境，再解释一下 ${entry.expression}（${entry.meaning}）。`; $("question").focus(); }; actions.append(ask); card.append(actions);
    const details = node("details"); details.append(node("summary", `${entry.occurrences.length} 个视频语境`));
    for (const item of entry.occurrences) { const p = node("p"); p.append(link(`${item.title} · ${Math.floor(item.start / 60)}:${String(Math.floor(item.start % 60)).padStart(2,"0")}`, `https://www.youtube.com/watch?v=${encodeURIComponent(item.video_id)}&t=${Math.floor(item.start)}s`)); details.append(p, node("blockquote", item.sentence + "\n" + item.translation)); }
    card.append(details); $("vocab-list").append(card);
  }
}
$("vocab-filters").onsubmit = e => { e.preventDefault(); loadVocabulary().catch(error => notice(error.message)); };
async function loadDocuments() { const rows = await json("/api/documents"); $("document-list").replaceChildren(); for (const row of rows) { const el = node("div", undefined, "document-row"); el.append(node("span", row.name), node("span", `${row.chunks} 个资料片段`, "muted")); $("document-list").append(el); } }
$("upload-form").onsubmit = async e => { e.preventDefault(); const file = $("document-file").files[0]; if (!file) return; $("upload-button").disabled = true; $("upload-status").textContent = "正在解析并导入，PDF / Word 可能需要几分钟…"; try { const data = new FormData(); data.append("file", file); await json("/api/documents", {method:"POST", body:data}); $("upload-status").textContent = "导入完成，可以在聊天中按资料提问。"; await loadDocuments(); } catch(error) { $("upload-status").textContent = error.message; } finally { $("upload-button").disabled = false; } };
$("pair-button").onclick = async () => { try { const data = await json("/api/pairing-token"); $("pair-token").value = data.token; $("pair-result").hidden = false; } catch(e) { notice(e.message); } };
$("copy-token").onclick = async () => { try { await navigator.clipboard.writeText($("pair-token").value); $("copy-token").textContent = "已复制"; } catch { $("pair-token").select(); } };
$("subtitle-form").onsubmit = async e => { e.preventDefault(); try { let id = $("subtitle-video").value.trim(); if (id.includes("://")) { const url = new URL(id); id = url.hostname === "youtu.be" ? url.pathname.slice(1) : url.searchParams.get("v"); } if (!/^[\w-]{11}$/.test(id || "")) throw new Error("请输入普通 YouTube 视频链接或 11 位视频 ID。"); const file = $("subtitle-file").files[0]; if (!file || file.size > 2_000_000) throw new Error("请选择不超过 2 MB 的英文字幕文件。"); const content = await file.text(); const result = await json(`/api/videos/${id}/subtitles`, {method:"POST", body:JSON.stringify({title:$("subtitle-title").value, content})}); $("subtitle-status").textContent = `已导入 ${result.cues} 个字幕片段，请在扩展中重新开始伴学。`; } catch(error) { $("subtitle-status").textContent = error.message; } };
(async () => { welcome(); await view(); const settings = await json("/api/settings"); $("model-status").textContent = settings.model; if (settings.chat_missing.length) notice(`请在项目 .env 中填写 ${settings.chat_missing.join("、")}，然后重启服务。`); const rows = await loadConversations(); if (rows.length) { thread = rows[0].id; await loadMessages(); await loadConversations(); } })().catch(e => notice(e.message));
