/*
 * 「东方神速」网页视频悬浮下载按钮（IDM 风格）
 *
 * 在页面每个 <video> 右上角叠加一个“下载”小按钮：
 * - 直链视频（http(s) 的 mp4/webm/…）：直接取 currentSrc 交给主程序；
 * - MSE/m3u8（video.src 多为 blob:）：由后台从该标签页的媒体嗅探表中取 m3u8；
 * - 受 DRM/加密保护的流无法下载，后台会返回明确提示。
 * 运行在 ISOLATED world，可使用 chrome.runtime / chrome.storage。
 */
(function () {
  "use strict";
  if (window.__dfsVideoBtnInjected) return;
  window.__dfsVideoBtnInjected = true;

  let enabled = true;
  let current = null;
  let hideTimer = null;
  let tipTimer = null;

  function refreshCfg() {
    try {
      chrome.storage.local.get({ enabled: true, sniff: true }, (d) => {
        enabled = d.enabled !== false && d.sniff !== false;
        if (!enabled) hide();
      });
    } catch (e) { /* ignore */ }
  }
  refreshCfg();
  try {
    chrome.storage.onChanged.addListener(refreshCfg);
  } catch (e) { /* ignore */ }

  const btn = document.createElement("div");
  btn.id = "dfs-video-float-btn";
  Object.assign(btn.style, {
    position: "fixed",
    zIndex: "2147483647",
    display: "none",
    alignItems: "center",
    gap: "5px",
    padding: "5px 10px",
    font: "12px/1.2 -apple-system,'Segoe UI','Microsoft YaHei',Arial,sans-serif",
    color: "#fff",
    background: "linear-gradient(135deg,#2D7FF9,#1660d8)",
    borderRadius: "7px",
    boxShadow: "0 2px 10px rgba(0,0,0,.35)",
    cursor: "pointer",
    userSelect: "none",
    pointerEvents: "auto",
    opacity: "0.96"
  });
  btn.innerHTML =
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" ' +
    'stroke="#fff" stroke-width="2.4" stroke-linecap="round" ' +
    'stroke-linejoin="round"><path d="M12 3v12"/><path d="M7 11l5 5 5-5"/>' +
    '<path d="M5 21h14"/></svg><span class="dfs-vbtn-text">下载视频</span>';
  (document.body || document.documentElement).appendChild(btn);

  function videos() {
    return Array.prototype.slice.call(document.querySelectorAll("video"));
  }

  function show(v) {
    if (!enabled) return;
    current = v;
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    resetLabel();
    updatePos();
    btn.style.display = "flex";
  }

  function hide() {
    btn.style.display = "none";
    current = null;
  }

  function scheduleHide() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!btn.matches(":hover")) hide();
    }, 160);
  }

  function updatePos() {
    if (!current) return;
    const r = current.getBoundingClientRect();
    if (r.width < 90 || r.height < 60 || r.bottom < 0 || r.top > innerHeight) {
      hide();
      return;
    }
    const top = Math.max(4, r.top + 8);
    const left = Math.max(4, r.right - btn.offsetWidth - 8);
    btn.style.top = top + "px";
    btn.style.left = left + "px";
  }

  function bind(v) {
    if (v.__dfsBound) return;
    v.__dfsBound = true;
    v.addEventListener("mouseenter", () => show(v), true);
    v.addEventListener("mouseleave", scheduleHide, true);
    v.addEventListener("focus", () => show(v), true);
  }

  function scan() { videos().forEach(bind); }
  scan();
  const mo = new MutationObserver(scan);
  mo.observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener("scroll", updatePos, true);
  window.addEventListener("resize", updatePos, true);
  document.addEventListener("fullscreenchange", () => setTimeout(updatePos, 120));
  setInterval(updatePos, 900);
  btn.addEventListener("mouseenter", () => { if (hideTimer) clearTimeout(hideTimer); });
  btn.addEventListener("mouseleave", scheduleHide);

  function setLabel(text) {
    const el = btn.querySelector(".dfs-vbtn-text");
    if (el) el.textContent = text;
  }
  function resetLabel() { setLabel("下载视频"); }

  function toast(text, isError) {
    setLabel(text);
    btn.style.background = isError
      ? "linear-gradient(135deg,#e0533d,#c0392b)"
      : "linear-gradient(135deg,#27ae60,#1e8e4a)";
    if (tipTimer) clearTimeout(tipTimer);
    tipTimer = setTimeout(() => {
      btn.style.background = "linear-gradient(135deg,#2D7FF9,#1660d8)";
      resetLabel();
    }, 2200);
  }

  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!current) return;
    const v = current;
    const src = v.currentSrc || v.src || "";
    setLabel("解析中…");
    try {
      chrome.runtime.sendMessage(
        {
          type: "videoDownload",
          currentSrc: src,
          pageUrl: location.href,
          title: (document.title || "").slice(0, 120)
        },
        (resp) => {
          if (chrome.runtime.lastError) {
            toast("扩展未就绪", true);
            return;
          }
          if (resp && resp.ok) {
            toast("已发送到东方神速");
          } else {
            toast((resp && resp.error) || "下载失败", true);
          }
        }
      );
    } catch (err) {
      toast("扩展未就绪", true);
    }
  }, true);
})();
