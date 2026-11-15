// SEB 3.x exposes a Config Key hash bound to this document's URL.
window.assessbridgeSeb = {
  async proof() {
    let security = window.SafeExamBrowser?.security;
    // Wait once for late bridge injection; do not delay every native-header request.
    if (!security) {
      this.bridgeWait ||= (async () => { for (let retry=0; !window.SafeExamBrowser?.security && retry<10; retry++) await new Promise(resolve=>setTimeout(resolve,100)); })();
      await this.bridgeWait;
      security=window.SafeExamBrowser?.security;
    }
    if (!security) return null;
    if (!security.configKey && typeof security.updateKeys === "function") {
      await new Promise((resolve) => {
        const timer = setTimeout(resolve, 4000);
        try { security.updateKeys(() => { clearTimeout(timer); resolve(); }); }
        catch { clearTimeout(timer); resolve(); }
      });
    }
    const value = security.configKey;
    return typeof value === "string" && /^[0-9a-f]{64}$/i.test(value) ? value.toLowerCase() : null;
  },
  info() {
    const version = typeof window.SafeExamBrowser?.version === "string" ? window.SafeExamBrowser.version.slice(0, 180).replace(/[\x00-\x1f]/g, "") : "";
    const platform = version.includes("_iOS_") || /iPad|iPhone|iPod/.test(navigator.userAgent) || (/Mac/.test(navigator.platform) && navigator.maxTouchPoints>1) ? "unsupported" : version.includes("_Windows_") ? "Windows" : version.includes("_macOS_") ? "macOS" : /Win/i.test(navigator.userAgentData?.platform || navigator.platform) ? "Windows" : /Mac/i.test(navigator.userAgentData?.platform || navigator.platform) ? "macOS" : "unsupported";
    return { version, platform };
  },
  async headers() {
    const proof = await this.proof();
    return proof ? { "X-AssessBridge-SEB-ConfigKeyHash": proof } : {};
  },
};
