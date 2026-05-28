const DEFAULT_SERVER = "http://149.118.130.92";

// Load saved server URL
chrome.storage.sync.get(["serverUrl"], (result) => {
  document.getElementById("server").value = result.serverUrl || DEFAULT_SERVER;
});

// Save server URL
document.getElementById("save").addEventListener("click", () => {
  const serverUrl = document.getElementById("server").value.trim() || DEFAULT_SERVER;
  chrome.storage.sync.set({ serverUrl }, () => {
    const status = document.getElementById("status");
    status.textContent = "Saved!";
    setTimeout(() => (status.textContent = ""), 2000);
  });
});
