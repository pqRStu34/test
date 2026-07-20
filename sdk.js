/**
 * Internet Archive Router SDK (sdk.js)
 * Allows any website to embed files dynamically using `ia-id="<CRC32_OR_SHA1>"`
 * 
 * Usage in HTML:
 *   <script src="https://pqrstu34.github.io/test/sdk.js"></script>
 *   <div ia-id="dd4ef44a"></div>
 *   <a ia-id="b2e414f6">Download File</a>
 */

(function () {
  const ITEM_ID = "corrupted_files";
  const API_URL = `https://archive.org/metadata/${ITEM_ID}`;
  const BASE_DOWNLOAD = `https://archive.org/download/${ITEM_ID}/`;
  const STORAGE_KEY = "ia_sdk_cache_" + ITEM_ID;

  let fileMapByCrc32 = new Map();
  let fileMapBySha1 = new Map();
  let fileMapByName = new Map();

  function normalize(str) {
    return (str || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  async function fetchMetadata() {
    try {
      const cached = sessionStorage.getItem(STORAGE_KEY);
      if (cached) return JSON.parse(cached);
    } catch (e) {}

    const res = await fetch(API_URL);
    const data = await res.json();
    const files = data.files || [];

    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(files));
    } catch (e) {}

    return files;
  }

  function indexFiles(files) {
    fileMapByCrc32.clear();
    fileMapBySha1.clear();
    fileMapByName.clear();

    for (let f of files) {
      const downloadUrl = BASE_DOWNLOAD + encodeURIComponent(f.name);
      const item = { ...f, download_url: downloadUrl };

      if (f.crc32) fileMapByCrc32.set(normalize(f.crc32), item);
      if (f.sha1) fileMapBySha1.set(normalize(f.sha1), item);
      if (f.name) fileMapByName.set(normalize(f.name), item);
    }
  }

  function findFile(id) {
    const norm = normalize(id);
    return fileMapByCrc32.get(norm) || fileMapBySha1.get(norm) || fileMapByName.get(norm) || null;
  }

  function processDomElements() {
    // Select elements with ia-id or data-ia-id
    const elements = document.querySelectorAll("[ia-id], [data-ia-id], [data-crc32], [data-sha1]");

    elements.forEach((el) => {
      const id = el.getAttribute("ia-id") || el.getAttribute("data-ia-id") || el.getAttribute("data-crc32") || el.getAttribute("data-sha1");
      if (!id) return;

      const file = findFile(id);
      if (!file) {
        console.warn(`[IA-SDK] File not found for ia-id: '${id}'`);
        return;
      }

      const tagName = el.tagName.toLowerCase();
      const format = (file.format || "").toLowerCase();
      const name = (file.name || "").toLowerCase();
      const isVideo = format.includes("mpeg") || format.includes("matroska") || name.endsWith(".mp4") || name.endsWith(".mkv");
      const isImage = format.includes("image") || format.includes("jpeg") || format.includes("png") || name.endsWith(".jpg") || name.endsWith(".png");

      // Auto-handle element based on IA format
      if (tagName === "a") {
        el.setAttribute("href", file.download_url);
        if (!el.textContent.trim()) el.textContent = file.name;
      } else if (tagName === "video" || tagName === "audio" || tagName === "img" || tagName === "iframe" || tagName === "source") {
        el.setAttribute("src", file.download_url);
      } else {
        // Generic elements (div, span, etc.): render appropriate HTML media based on Internet Archive file format!
        if (isVideo) {
          el.innerHTML = `<video src="${file.download_url}" controls style="max-width:100%; border-radius:8px;"></video>`;
        } else if (isImage) {
          el.innerHTML = `<img src="${file.download_url}" alt="${file.name}" style="max-width:100%; border-radius:8px;">`;
        } else {
          el.innerHTML = `<a href="${file.download_url}" target="_blank">${file.name}</a>`;
        }
      }

      el.setAttribute("data-ia-format", file.format || "");
      el.setAttribute("data-ia-size", file.size || "0");
      el.setAttribute("data-ia-sha1", file.sha1 || "");
    });
  }

  async function initSdk() {
    try {
      const files = await fetchMetadata();
      indexFiles(files);
      processDomElements();
    } catch (err) {
      console.error("[IA-SDK] Initialization Error:", err);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initSdk);
  } else {
    initSdk();
  }

  window.IASDK = {
    init: initSdk,
    getFile: findFile,
    getAll: () => Array.from(fileMapByCrc32.values())
  };
})();
