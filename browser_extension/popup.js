/* 「东方神速」扩展弹窗逻辑 */

const $ = (id) => document.getElementById(id);

function send(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (resp) => resolve(resp || { ok: false, error: "无响应" }));
  });
}

function setMsg(text, ok) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = "msg " + (text ? (ok ? "ok" : "err") : "");
}

async function refreshConn() {
  // connect 会先 ping 再自动配对（获取 Token），真正授权成功才算“已连接”
  const r = await send({ type: "connect" });
  const el = $("conn");
  if (r.ok && r.authorized) {
    el.textContent = "已连接";
    el.className = "conn on";
    return true;
  }
  el.textContent = "未连接（请启动东方神速）";
  el.className = "conn off";
  return false;
}

async function refreshMedia() {
  const r = await send({ type: "media" });
  const box = $("media-list");
  box.innerHTML = "";
  const items = (r.ok && r.data) || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty">暂未检测到媒体，播放视频后再打开此面板</div>';
    return;
  }
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "media-item";
    const tag = document.createElement("span");
    tag.className = "kind" + (it.kind.includes("m3u8") ? " hls" : "");
    tag.textContent = it.kind;
    const url = document.createElement("span");
    url.className = "url";
    url.textContent = it.url;
    url.title = it.url;
    const btn = document.createElement("button");
    btn.textContent = "下载";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      const resp = await send({ type: "download", entry: { url: it.url } });
      btn.disabled = false;
      if (resp.ok) {
        btn.textContent = "已发送";
        setMsg("已发送到东方神速", true);
      } else {
        setMsg("失败：" + resp.error, false);
      }
    });
    row.append(tag, url, btn);
    box.appendChild(row);
  }
}

function fmtSize(n) {
  if (!n) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
}

async function refreshBlobs() {
  const r = await send({ type: "blobs" });
  const box = $("blob-list");
  box.innerHTML = "";
  const items = (r.ok && r.data) || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty">暂未检测到 blob: 资源</div>';
    return;
  }
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "media-item";
    const tag = document.createElement("span");
    tag.className = "kind";
    tag.textContent = it.mime || "blob";
    const label = document.createElement("span");
    label.className = "url";
    label.textContent = (fmtSize(it.size) ? fmtSize(it.size) + " · " : "") +
      it.blobUrl;
    label.title = it.blobUrl;
    const btn = document.createElement("button");
    btn.textContent = "保存";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "读取中";
      const resp = await send({ type: "downloadBlob", blobUrl: it.blobUrl });
      btn.disabled = false;
      if (resp.ok) {
        btn.textContent = "已保存";
        setMsg("Blob 已保存到下载目录", true);
      } else {
        btn.textContent = "保存";
        setMsg("失败：" + (resp.error || "blob 可能已被页面释放"), false);
      }
    });
    row.append(tag, label, btn);
    box.appendChild(row);
  }
}

// 配置加载/保存
async function loadConfig() {
  const c = await chrome.storage.local.get({
    takeover: true, sniff: true, port: 8765, token: ""
  });
  $("takeover").checked = c.takeover;
  $("sniff").checked = c.sniff;
  $("port").value = c.port;
  $("token").value = c.token;
}

function bindSave(id, key) {
  $(id).addEventListener("change", async () => {
    const el = $(id);
    const value = el.type === "checkbox" ? el.checked : Number(el.value) || el.value;
    await chrome.storage.local.set({ [key]: value });
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  await loadConfig();
  bindSave("takeover", "takeover");
  bindSave("sniff", "sniff");
  $("port").addEventListener("change", async () => {
    await chrome.storage.local.set({ port: Number($("port").value) || 8765 });
    refreshConn();
  });
  $("token").addEventListener("change", async () => {
    await chrome.storage.local.set({ token: $("token").value.trim() });
    refreshConn();
  });

  $("test-conn").addEventListener("click", async () => {
    setMsg("正在连接…");
    const ok = await refreshConn();
    setMsg(ok ? "连接成功，桥接正常" : "连接失败：请确认程序已启动并在设置中启用桥接", ok);
  });

  $("clear-media").addEventListener("click", async () => {
    await send({ type: "clearMedia" });
    refreshMedia();
  });

  $("manual-go").addEventListener("click", async () => {
    const url = $("manual-url").value.trim();
    if (!/^https?:\/\//i.test(url)) {
      setMsg("请输入合法的 http(s) 链接", false);
      return;
    }
    const r = await send({ type: "download", entry: { url } });
    if (r.ok) {
      $("manual-url").value = "";
      setMsg("已发送到东方神速", true);
    } else {
      setMsg("失败：" + r.error, false);
    }
  });

  await refreshConn();
  await refreshMedia();
  await refreshBlobs();
});
