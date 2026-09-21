// content_bridge.js —— 运行在隔离世界（ISOLATED world），可使用 chrome.runtime。
// 1) 接收主世界钩子上报的 blob 事件，转发给后台；
// 2) 收到后台“抓取某 blob”指令时，在页面同源下 fetch(blobUrl) 读出二进制，
//    直接 POST 到本地桥接 /add-blob 落盘。
//
// 实验性限制（重要）：
// - blob: 地址只在创建它的页面、且未被 revoke 时可读；
// - 受 EME/Widevine 保护的媒体（MSE 分片本身已加密）读出的是密文，本扩展
//   不会、也无法绕过 DRM；
// - 大体积 blob 会整体读入内存，建议仅用于中小文件。
(function () {
  async function ensureToken(cfg, force) {
    if (cfg.token && !force) return cfg.token;
    try {
      const r = await fetch(
        "http://127.0.0.1:" + cfg.port + "/pair");
      if (r.ok) {
        const j = await r.json();
        if (j.token) {
          cfg.token = j.token;
          await chrome.storage.local.set({ token: j.token });
        }
      }
    } catch (e) { /* 程序未运行，保持空 token */ }
    return cfg.token;
  }

  async function postBlob(cfg, name, buf) {
    return fetch("http://127.0.0.1:" + cfg.port + "/add-blob", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-PyDL-Token": cfg.token || "",
        "X-Blob-Name": name,
      },
      body: buf,
    });
  }

  window.addEventListener("message", function (event) {
    const d = event.data;
    if (!d || d.__pydl !== "blob-event") return;
    try {
      chrome.runtime.sendMessage({
        type: "blobSeen",
        evt: d.evt,
        blobUrl: d.blobUrl,
        size: d.size || 0,
        mime: d.mime || "",
        page: location.href,
      }).catch(function () { /* 后台未就绪 */ });
    } catch (e) { /* 忽略 */ }
  });

  chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
    if (!msg || msg.type !== "fetchBlob") return false;
    (async function () {
      try {
        const resp = await fetch(msg.blobUrl);
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const buf = await resp.arrayBuffer();
        const cfg = await chrome.storage.local.get({ port: 8765, token: "" });
        await ensureToken(cfg, false);
        const name = encodeURIComponent(msg.name || "blob.bin");
        let presp = await postBlob(cfg, name, buf);
        if (presp.status === 403) {  // Token 失效：重新配对后重试一次
          await ensureToken(cfg, true);
          presp = await postBlob(cfg, name, buf);
        }
        const j = await presp.json().catch(function () { return {}; });
        sendResponse({ ok: presp.ok && !!j.ok, status: presp.status, info: j });
      } catch (e) {
        sendResponse({ ok: false, error: String(e) });
      }
    })();
    return true; // 异步 sendResponse
  });
})();
