// SEB 3.x exposes a Config Key hash bound to this document's URL.
window.assessbridgeSeb = {
  async proof() {
    const security = window.SafeExamBrowser?.security;
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
  async headers() {
    const proof = await this.proof();
    return proof ? { "X-AssessBridge-SEB-ConfigKeyHash": proof } : {};
  },
};
