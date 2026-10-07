"use strict";
chrome.sidePanel.setPanelBehavior({openPanelOnActionClick:true}).catch(() => {});
// 凭证仅供扩展侧栏使用，不暴露给 YouTube 页面中的 content script。
chrome.storage.local.setAccessLevel({accessLevel:"TRUSTED_CONTEXTS"}).catch(() => {});
function configure(tabId, url) {
  const enabled = typeof url === "string" && url.startsWith("https://www.youtube.com/");
  chrome.sidePanel.setOptions({tabId, path:"sidepanel.html", enabled}).catch(() => {});
}
chrome.tabs.onUpdated.addListener((tabId, info, tab) => { if (info.url || info.status === "complete") configure(tabId, tab.url); });
chrome.runtime.onInstalled.addListener(async () => { for (const tab of await chrome.tabs.query({})) configure(tab.id, tab.url); });
