// content.js - Runs on YouTube watch pages (isolated world)
// Injects a script into the page's main world to extract ytInitialPlayerResponse,
// then adds a "Download MP3" button below the video.

(function () {
  "use strict";

  const DEFAULT_SERVER = "http://149.118.130.92";
  let buttonInjected = false;
  let currentVideoId = null;

  function getServerUrl() {
    return new Promise((resolve) => {
      chrome.storage.sync.get(["serverUrl"], (result) => {
        resolve(result.serverUrl || DEFAULT_SERVER);
      });
    });
  }

  // Listen for audio URL from injected page-world script
  window.addEventListener("yt-mp3-audio-ready", async (e) => {
    const { audioUrl, title, videoId } = e.detail;
    if (!audioUrl) {
      console.warn("[YT-MP3] No audio URL found");
      return;
    }
    currentVideoId = videoId;
    injectButton(audioUrl, title, videoId);
  });

  // Inject script into YouTube's page world to read ytInitialPlayerResponse
  function injectPageScript() {
    const existing = document.getElementById("yt-mp3-extractor");
    if (existing) existing.remove();

    const script = document.createElement("script");
    script.id = "yt-mp3-extractor";
    script.textContent = `
      (function() {
        function extract() {
          let pr = window.ytInitialPlayerResponse;
          if (!pr) {
            // Try parsing from page source
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
              const txt = s.textContent || '';
              const idx = txt.indexOf('ytInitialPlayerResponse');
              if (idx !== -1) {
                const eqIdx = txt.indexOf('=', idx);
                if (eqIdx !== -1) {
                  const start = txt.indexOf('{', eqIdx);
                  if (start !== -1) {
                    let depth = 0;
                    let end = start;
                    for (let i = start; i < txt.length; i++) {
                      if (txt[i] === '{') depth++;
                      else if (txt[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
                    }
                    try { pr = JSON.parse(txt.substring(start, end)); } catch(e) {}
                  }
                }
                if (pr) break;
              }
            }
          }
          if (!pr || !pr.streamingData) {
            setTimeout(extract, 1500);
            return;
          }
          const formats = pr.streamingData.adaptiveFormats || [];
          let best = null;
          let bestBr = 0;
          for (const f of formats) {
            if (f.mimeType && f.mimeType.startsWith('audio/') && f.url && f.bitrate > bestBr) {
              best = f;
              bestBr = f.bitrate;
            }
          }
          const titleEl = document.querySelector('h1.ytd-watch-metadata yt-formatted-string')
                       || document.querySelector('h1.title')
                       || document.querySelector('[itemprop="name"]');
          const title = titleEl ? titleEl.textContent.trim()
                      : document.title.replace(' - YouTube', '').trim();
          const vid = new URLSearchParams(location.search).get('v') || '';
          window.dispatchEvent(new CustomEvent('yt-mp3-audio-ready', {
            detail: { audioUrl: best ? best.url : null, title, videoId: vid }
          }));
        }
        extract();
      })();
    `;
    document.head.appendChild(script);
    script.remove();
  }

  // Get video title from DOM (fallback)
  function getVideoTitle() {
    const el =
      document.querySelector("h1.ytd-watch-metadata yt-formatted-string") ||
      document.querySelector("h1.title") ||
      document.querySelector('[itemprop="name"]');
    return el ? el.textContent.trim() : document.title.replace(" - YouTube", "").trim();
  }

  // Create and inject the download button
  function injectButton(audioUrl, title, videoId) {
    if (buttonInjected) {
      // Update audio URL if video changed
      const btn = document.getElementById("yt-mp3-btn");
      if (btn) btn.dataset.audioUrl = audioUrl;
      return;
    }

    const target =
      document.querySelector("#above-the-fold #actions") ||
      document.querySelector("#actions-inner") ||
      document.querySelector("#menu-container") ||
      document.querySelector("ytd-menu-renderer");

    if (!target) {
      setTimeout(() => injectButton(audioUrl, title, videoId), 1000);
      return;
    }

    const container = document.createElement("div");
    container.id = "yt-mp3-container";
    container.style.cssText = "display:inline-flex;align-items:center;margin:8px 0;gap:10px;";

    const btn = document.createElement("button");
    btn.id = "yt-mp3-btn";
    btn.dataset.audioUrl = audioUrl;
    btn.textContent = "Download MP3";
    btn.style.cssText = `
      background: linear-gradient(135deg, #ff0050, #ff6b00);
      color: white;
      border: none;
      border-radius: 18px;
      padding: 10px 20px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: opacity 0.2s;
    `;
    btn.onmouseover = () => (btn.style.opacity = "0.85");
    btn.onmouseout = () => (btn.style.opacity = "1");

    const status = document.createElement("span");
    status.id = "yt-mp3-status";
    status.style.cssText = "font-size:13px;color:#aaa;";

    btn.addEventListener("click", async () => {
      const url = btn.dataset.audioUrl;
      if (!url) {
        status.textContent = "No audio URL available";
        return;
      }

      btn.disabled = true;
      btn.textContent = "Processing...";
      status.textContent = "";

      const serverUrl = await getServerUrl();

      try {
        const response = await fetch(`${serverUrl}/api/convert-url`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, title: title || getVideoTitle(), videoId }),
        });

        if (!response.ok) throw new Error(`Server error: ${response.status}`);

        const data = await response.json();
        if (data.error) throw new Error(data.error);

        const taskId = data.task_id;
        status.textContent = "Downloading & converting...";

        const poll = setInterval(async () => {
          try {
            const res = await fetch(`${serverUrl}/status/${taskId}`);
            const s = await res.json();

            if (s.status === "done") {
              clearInterval(poll);
              status.textContent = "Ready!";
              btn.textContent = "Download MP3";
              btn.disabled = false;
              window.open(`${serverUrl}/download/${taskId}`, "_blank");
            } else if (s.status === "error") {
              clearInterval(poll);
              status.textContent = `Error: ${s.progress || "Unknown"}`;
              btn.textContent = "Download MP3";
              btn.disabled = false;
            } else {
              status.textContent = s.progress || "Processing...";
            }
          } catch (e) { /* keep polling */ }
        }, 2000);
      } catch (e) {
        status.textContent = `Error: ${e.message}`;
        btn.textContent = "Download MP3";
        btn.disabled = false;
      }
    });

    container.appendChild(btn);
    container.appendChild(status);
    target.parentNode.insertBefore(container, target.nextSibling);
    buttonInjected = true;
  }

  // Handle YouTube SPA navigation
  let lastUrl = location.href;
  const observer = new MutationObserver(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      buttonInjected = false;
      const existing = document.getElementById("yt-mp3-container");
      if (existing) existing.remove();
      setTimeout(injectPageScript, 1500);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });

  // Start
  injectPageScript();
})();
