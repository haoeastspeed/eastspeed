/*
 * 「东方神速」浏览器扩展（Manifest V3）后台 Service Worker
 *
 * 职责：
 * 1. 下载接管：浏览器一旦创建下载任务，在确认本地程序在线且已授权后，取消浏览器侧
 *    下载并转交给「东方神速」（透传 filename / referer / cookies）；程序不在线则
 *    不拦截，回退浏览器原生下载，绝不“点了没反应”；
 * 2. 媒体嗅探：观察页面请求，收集 m3u8 / mp4 等媒体地址供 popup 一键下载；
 * 3. 自动配对：首次使用通过 /pair 自动获取桥接 Token，无需手动复制；
 * 4. blob: 资源接管（实验性）。
 */

// ---------- 跨浏览器兼容垫片（Firefox MV2 事件页）----------
// Firefox MV2 用 browserAction，统一映射到 action 名称
if (typeof chrome !== "undefined" && chrome.browserAction && !chrome.action) {
  chrome.action = chrome.browserAction;
}
// Firefox 早期版本无 chrome.storage.session，用内存 Map 模拟（事件页存活期有效）
if (typeof chrome !== "undefined" && chrome.storage && !chrome.storage.session) {
  const _mem = {};
  chrome.storage.session = {
    get(keys) {
      const k = Array.isArray(keys) ? keys[0] : keys;
      if (k && _mem[k] !== undefined) return Promise.resolve({ [k]: _mem[k] });
      if (k === null || k === undefined) return Promise.resolve(Object.assign({}, _mem));
      return Promise.resolve({});
    },
    set(obj) { Object.assign(_mem, obj || {}); return Promise.resolve(); },
    remove(k) { delete _mem[k]; return Promise.resolve(); }
  };
}

const DEFAULT_CONFIG = {
  enabled: true,        // 总开关
  takeover: true,       // 自动接管浏览器下载
  sniff: true,          // 媒体嗅探
  port: 8765,
  token: ""
};

// 明显的媒体资源（ts 分片不单独收集，m3u8 已代表整段视频）
const MEDIA_PATTERNS = [
  { re: /\.m3u8(\?|$)/i, kind: "HLS 视频 (m3u8)" },
  { re: /\.(mp4|m4v|mov|webm|mkv|flv|avi)(\?|$)/i, kind: "视频" },
  { re: /\.(mp3|m4a|aac|flac|ogg|wav)(\?|$)/i, kind: "音频" }
];

let cfg = { ...DEFAULT_CONFIG };
let online = false;          // 桥接可达且已授权（拿到或自带 Token）
let lastFallbackNotify = 0;  // 离线回退通知限频

function timeoutSignal(ms) {
  const ctrl = new AbortController();
  setTimeout(() => ctrl.abort(), ms);
  return ctrl.signal;
}

async function loadConfig() {
  const stored = await chrome.storage.local.get(DEFAULT_CONFIG);
  cfg = { ...DEFAULT_CONFIG, ...stored };
}

// ---------- 桥接通信：连接 / 自动配对 ----------

async function pairOnce() {
  // 简单 GET（不带自定义头，避免触发 CORS/PNA 预检）；服务端按 Origin 只对扩展放行
  const res = await fetch(`http://127.0.0.1:${cfg.port}/pair`, {
    method: "GET",
    signal: timeoutSignal(1800)
  });
  if (!res.ok) throw new Error(`pair HTTP ${res.status}`);
  const data = await res.json();
  if (!data || !data.token) throw new Error("配对响应缺少 Token");
  cfg.token = data.token;
  await chrome.storage.local.set({ token: data.token });
  return data.token;
}

/** 探测桥接并在需要时自动配对。返回 true 表示在线且已授权。 */
async function connect() {
  await loadConfig();
  try {
    const pr = await fetch(`http://127.0.0.1:${cfg.port}/ping`, {
      signal: timeoutSignal(1200)
    });
    if (!pr.ok) { online = false; return false; }
    if (!cfg.token) {
      await pairOnce();  // 旧版程序无 /pair 会抛错，此时保持离线、不劫持
    }
    online = true;
    return true;
  } catch (e) {
    online = false;
    return false;
  }
}

async function bridgeAttempt(path, payload, retried) {
  let res;
  try {
    res = await fetch(`http://127.0.0.1:${cfg.port}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-PyDL-Token": cfg.token || ""
      },
      body: JSON.stringify(payload || {}),
      signal: timeoutSignal(10000)
    });
  } catch (e) {
    online = false;
    throw new Error("无法连接东方神速，请确认程序正在运行且已启用桥接");
  }
  // Token 失效：自动重新配对后重试一次
  if (res.status === 403 && !retried) {
    try { await pairOnce(); } catch (e) { /* 旧版程序或配对被拒 */ }
    return bridgeAttempt(path, payload, true);
  }
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch (e) { /* ignore */ }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  online = true;
  return data;
}

function bridge(path, payload) {
  return bridgeAttempt(path, payload, false);
}

async function ping() {
  const res = await fetch(`http://127.0.0.1:${cfg.port}/ping`, {
    signal: timeoutSignal(1200)
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function sendDownload(entry) {
  return bridge("/add", {
    url: entry.url,
    filename: entry.filename || "",
    referer: entry.referer || "",
    cookies: entry.cookies || ""
  });
}

function notify(title, message) {
  try {
    chrome.notifications.create({
      type: "basic",
      iconUrl: "icons/icon128.png",
      title,
      message: String(message || "")
    });
  } catch (e) { /* 通知不可用时忽略 */ }
}

function notifyFallback() {
  const now = Date.now();
  if (now - lastFallbackNotify < 5 * 60 * 1000) return;
  lastFallbackNotify = now;
  notify("东方神速未连接", "本次下载已改用浏览器自带下载。请启动东方神速并在设置中启用桥接。");
}

async function cookiesFor(urls) {
  const list = [];
  for (const u of (Array.isArray(urls) ? urls : [urls])) {
    if (!u) continue;
    try {
      const got = await chrome.cookies.getAll({ url: u });
      for (const c of got) {
        if (!list.some((x) => x.name === c.name)) list.push(c);
      }
    } catch (e) { /* 单个域读取失败时忽略 */ }
  }
  return list.map((c) => `${c.name}=${c.value}`).join("; ");
}

function basenameFromUrl(url) {
  try {
    const p = new URL(url).pathname;
    const name = decodeURIComponent(p.substring(p.lastIndexOf("/") + 1));
    return name || "";
  } catch (e) {
    return "";
  }
}

// 判断文件名是否“可信/像样”：需有合法扩展名，且不是 UUID 或长十六进制随机资源 ID
// （例如 GitHub Release 跳转到 objects.githubusercontent.com 后路径末段的随机串）
function isMeaningfulName(name) {
  if (!name) return false;
  const base = String(name).split(/[\\/]/).pop();
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return false;
  const ext = base.slice(dot + 1).toLowerCase();
  if (!/^[a-z0-9]{1,6}$/.test(ext)) return false;
  const stem = base.slice(0, dot);
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(stem)) return false;
  if (/^[0-9a-f]{24,}$/i.test(stem)) return false;
  return true;
}

// 多源择优取名：浏览器解析出的最终名 > 原始请求 URL 名 > 跳转后临时 URL 名
function pickFilename(item) {
  const fromChrome = String(item.filename || "").split(/[\\/]/).pop();
  const fromReq = basenameFromUrl(item.url || "");
  const fromFinal = basenameFromUrl(item.finalUrl || "");
  if (isMeaningfulName(fromChrome)) return fromChrome;
  if (isMeaningfulName(fromReq)) return fromReq;
  return fromChrome || fromReq || fromFinal || "download";
}

// ---------- 1) 下载接管 ----------

chrome.downloads.onCreated.addListener(async (item) => {
  if (!cfg.enabled || !cfg.takeover) return;
  // 下载地址优先用“原始请求 URL”：交由桌面程序自行跟随 302，既能从原始路径
  // 得到正确文件名（GitHub Release 等），也避免跳转后的临时签名地址过期影响续传
  const reqUrl = item.url || "";
  const finalUrl = item.finalUrl || "";
  const url = /^https?:\/\//i.test(reqUrl) ? reqUrl : finalUrl;
  // blob/data/扩展内部 URL 无法由外部程序直接下载，交给浏览器
  if (!/^https?:\/\//i.test(url)) return;

  // 关键：先确认程序在线且已授权，否则不拦截浏览器下载（避免“点了没反应”）
  if (!online) {
    const ok = await connect();
    if (!ok) {
      notifyFallback();
      return;
    }
  }

  // 立即取消浏览器侧下载
  chrome.downloads.cancel(item.id).catch(() => {});
  chrome.downloads.erase({ id: item.id }).catch(() => {});

  const filename = pickFilename(item);

  const entry = {
    url,
    filename,
    referer: item.referrer || "",
    cookies: await cookiesFor([reqUrl, finalUrl].filter(Boolean))
  };

  try {
    const r = await sendDownload(entry);
    if (r && r.cancelled) {
      // 用户在确认窗点了取消：浏览器侧下载已取消，这里仅提示
      notify("已取消接管", filename || url);
    } else {
      notify("已转交给东方神速", filename || url);
    }
  } catch (e) {
    notify("东方神速接管失败",
      `${String(e.message || e)}。可打开扩展面板用“手动下载”重试。`);
  }
});

// ---------- 2) 媒体嗅探 ----------

const sessionMedia = {
  async get(tabId) {
    const key = `media_${tabId}`;
    const data = await chrome.storage.session.get(key);
    return data[key] || [];
  },
  async add(tabId, entry) {
    const key = `media_${tabId}`;
    const data = await chrome.storage.session.get(key);
    const list = data[key] || [];
    if (list.some((x) => x.url === entry.url)) return;
    list.push(entry);
    if (list.length > 50) list.shift();
    await chrome.storage.session.set({ [key]: list });
    try {
      const hls = list.filter((x) => x.kind.includes("m3u8")).length;
      chrome.action.setBadgeText({ tabId, text: hls > 0 ? String(hls) : String(list.length) });
      chrome.action.setBadgeBackgroundColor({ tabId, color: "#2D7FF9" });
    } catch (e) { /* ignore */ }
  },
  async clear(tabId) {
    await chrome.storage.session.remove(`media_${tabId}`);
    try { chrome.action.setBadgeText({ tabId, text: "" }); } catch (e) { /* ignore */ }
  }
};

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (!cfg.enabled || !cfg.sniff) return;
    if (details.tabId < 0 || !details.url) return;
    for (const { re, kind } of MEDIA_PATTERNS) {
      if (re.test(details.url)) {
        sessionMedia.add(details.tabId, {
          url: details.url,
          kind,
          time: Date.now()
        });
        break;
      }
    }
  },
  { urls: ["*://*/*"] }
);

chrome.tabs.onRemoved.addListener((tabId) => {
  sessionMedia.clear(tabId);
  sessionBlobs.clear(tabId);
});

// ---------- 3) blob: 资源接管（实验性） ----------

const sessionBlobs = {
  async get(tabId) {
    const key = `blobs_${tabId}`;
    const data = await chrome.storage.session.get(key);
    return data[key] || [];
  },
  async add(tabId, entry) {
    const key = `blobs_${tabId}`;
    const data = await chrome.storage.session.get(key);
    const list = data[key] || [];
    if (list.some((x) => x.blobUrl === entry.blobUrl)) return;
    list.push(entry);
    if (list.length > 50) list.shift();
    await chrome.storage.session.set({ [key]: list });
  },
  async remove(tabId, blobUrl) {
    const key = `blobs_${tabId}`;
    const data = await chrome.storage.session.get(key);
    const list = (data[key] || []).filter((x) => x.blobUrl !== blobUrl);
    await chrome.storage.session.set({ [key]: list });
  },
  async clear(tabId) {
    await chrome.storage.session.remove(`blobs_${tabId}`);
  }
};

const MIME_EXT = {
  "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv",
  "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav",
  "audio/flac": ".flac", "image/png": ".png", "image/jpeg": ".jpg",
  "image/gif": ".gif", "application/pdf": ".pdf",
  "application/zip": ".zip", "application/octet-stream": ".bin"
};

function blobName(entry) {
  if (MIME_EXT[entry.mime]) {
    return `blob-${Date.now()}${MIME_EXT[entry.mime]}`;
  }
  let base = "blob";
  try { base = new URL(entry.page || "about:blank").hostname || "blob"; } catch (e) {}
  return `${base}-${Date.now()}.bin`;
}

// ---------- 启动 / 配置变化 ----------

loadConfig().then(() => connect()).catch(() => {});
chrome.runtime.onStartup.addListener(() => { loadConfig().then(connect); });
chrome.runtime.onInstalled.addListener(() => { loadConfig().then(connect); });

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  for (const [k, v] of Object.entries(changes)) {
    cfg[k] = v.newValue;
  }
  if (changes.port || changes.token) {
    connect();
  }
});

// ---------- popup 消息接口 ----------

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "ping") {
        sendResponse({ ok: true, data: await ping() });
      } else if (msg.type === "connect") {
        const authorized = await connect();
        sendResponse({ ok: authorized, authorized, online });
      } else if (msg.type === "media") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        sendResponse({ ok: true, data: tab ? await sessionMedia.get(tab.id) : [] });
      } else if (msg.type === "clearMedia") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (tab) await sessionMedia.clear(tab.id);
        sendResponse({ ok: true });
      } else if (msg.type === "download") {
        const entry = msg.entry || {};
        if (!/^https?:\/\//i.test(entry.url || "")) {
          sendResponse({ ok: false, error: "URL 无效" });
          return;
        }
        if (!entry.cookies) entry.cookies = await cookiesFor(entry.url);
        // 手动下载也确保已配对在线
        if (!online) await connect();
        await sendDownload(entry);
        sendResponse({ ok: true });
      } else if (msg.type === "videoDownload") {
        // 网页视频悬浮按钮：直链用 currentSrc；MSE/m3u8（currentSrc 多为 blob:）
        // 回退到该标签页的媒体嗅探表，优先 m3u8，其次普通视频文件。
        const tabId = _sender.tab && _sender.tab.id;
        let url = msg.currentSrc || "";
        let kind = "";
        if (!/^https?:\/\//i.test(url) && tabId >= 0) {
          const media = await sessionMedia.get(tabId);
          const m3u8 = media.find((x) => (x.kind || "").includes("m3u8"));
          const vid = media.find((x) => x.kind === "视频");
          const pick = m3u8 || vid;
          if (pick) { url = pick.url; kind = pick.kind; }
        }
        if (!/^https?:\/\//i.test(url)) {
          sendResponse({ ok: false,
            error: "未检测到可下载的视频地址（可能是加密或 DRM 受保护流）" });
          return;
        }
        let filename = "";
        if (msg.title) {
          const bn = basenameFromUrl(url);
          const ext = (bn.match(/\.[a-z0-9]+$/i) || [".mp4"])[0];
          filename = `${String(msg.title).replace(/[\\/:*?"<>|]+/g, "_").slice(0, 60)}${ext}`;
        }
        if (!online) await connect();
        await sendDownload({
          url,
          filename,
          referer: msg.pageUrl || "",
          cookies: await cookiesFor(url)
        });
        sendResponse({ ok: true, kind });
      } else if (msg.type === "blobSeen") {
        const tabId = _sender.tab && _sender.tab.id;
        if (tabId >= 0 && msg.blobUrl) {
          if (msg.evt === "revoke") {
            await sessionBlobs.remove(tabId, msg.blobUrl);
          } else if (msg.mime !== "media-source") {
            // MediaSource（MSE 分片流）不是单个可保存的 Blob，且多为受 DRM
            // 保护内容，这里不收集；只收集普通 Blob。
            await sessionBlobs.add(tabId, {
              blobUrl: msg.blobUrl, size: msg.size || 0,
              mime: msg.mime || "", page: msg.page || "", time: Date.now()
            });
          }
        }
        sendResponse({ ok: true });
      } else if (msg.type === "blobs") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        sendResponse({ ok: true, data: tab ? await sessionBlobs.get(tab.id) : [] });
      } else if (msg.type === "downloadBlob") {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab) { sendResponse({ ok: false, error: "无活动标签页" }); return; }
        const list = await sessionBlobs.get(tab.id);
        const entry = list.find((x) => x.blobUrl === msg.blobUrl);
        if (!entry) { sendResponse({ ok: false, error: "该 blob 已失效" }); return; }
        const name = blobName(entry);
        const r = await chrome.tabs.sendMessage(tab.id, {
          type: "fetchBlob", blobUrl: entry.blobUrl, name
        });
        sendResponse(r || { ok: false, error: "页面无响应" });
      } else {
        sendResponse({ ok: false, error: "未知请求" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e.message || e) });
    }
  })();
  return true; // 异步响应
});
