const exam = location.pathname.split("/").pop();
const base = `${location.protocol}//${location.host}`;
const fileUrl = `${base}/seb/launch/${encodeURIComponent(exam)}`;
document.getElementById("open-seb").href = fileUrl.replace(/^https?:/, location.protocol === "https:" ? "sebs:" : "seb:");
document.getElementById("download-seb").href = fileUrl;
