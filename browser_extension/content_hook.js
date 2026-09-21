// content_hook.js —— 运行在页面主世界（MAIN world），document_start 注入。
// 包装 URL.createObjectURL / revokeObjectURL，记录页面生成的 blob: 资源，
// 再通过 window.postMessage 通知隔离世界的 content_bridge.js。
// 说明：MAIN world 无法直接使用 chrome.runtime，只能借 postMessage 中转。
(function () {
  if (window.__pydlBlobHookInstalled) return;
  window.__pydlBlobHookInstalled = true;

  function emit(type, blobUrl, info) {
    try {
      window.postMessage(Object.assign(
        { __pydl: "blob-event", evt: type, blobUrl: blobUrl }, info || {}), "*");
    } catch (e) { /* 忽略 */ }
  }

  function kindOf(obj) {
    try {
      if (obj instanceof Blob) return { size: obj.size || 0, mime: obj.type || "" };
      if (obj && obj.constructor &&
          obj.constructor.name === "MediaSource") {
        return { size: 0, mime: "media-source" };
      }
    } catch (e) { /* 跨 frame 访问可能受限 */ }
    return null;
  }

  const origCreate = URL.createObjectURL.bind(URL);
  const origRevoke = URL.revokeObjectURL.bind(URL);

  URL.createObjectURL = function (obj) {
    const blobUrl = origCreate(obj);
    try {
      const info = kindOf(obj);
      if (info) emit("create", blobUrl, info);
    } catch (e) { /* 忽略 */ }
    return blobUrl;
  };

  URL.revokeObjectURL = function (blobUrl) {
    try { emit("revoke", blobUrl, {}); } catch (e) { /* 忽略 */ }
    return origRevoke(blobUrl);
  };
})();
