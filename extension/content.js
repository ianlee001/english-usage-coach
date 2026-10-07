"use strict";
function playbackState() {
  const url = new URL(location.href), video = document.querySelector("video.html5-main-video");
  const id = url.searchParams.get("v");
  const player = document.getElementById("movie_player");
  if (url.pathname !== "/watch" || !/^[\w-]{11}$/.test(id || "") || !video) return {error:"请打开一个 YouTube 普通视频；第一版不支持 Shorts。"};
  if (!Number.isFinite(video.duration) || player?.classList.contains("ytp-live")) return {error:"请等待视频加载；第一版不支持直播。"};
  return {video_id:id, title:document.querySelector("ytd-watch-metadata h1")?.textContent?.trim() || document.title.replace(/ - YouTube$/, ""), position:video.currentTime, paused:video.paused || video.ended, seeking:video.seeking, rate:video.playbackRate, ad:!!player?.classList.contains("ad-showing"), visible:document.visibilityState === "visible"};
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type === "PLAYBACK_STATE") respond(playbackState());
  if (message.type === "SEEK") {
    const state = playbackState(), video = document.querySelector("video.html5-main-video");
    if (state.video_id === message.video_id && !state.ad && Number.isFinite(message.position) && message.position >= 0) { video.currentTime = message.position; respond({ok:true}); }
    else respond({ok:false});
  }
});
