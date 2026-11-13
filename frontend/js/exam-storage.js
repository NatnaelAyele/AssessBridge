/* Encrypted, persistent recovery data. Keys are non-extractable and scoped to an invitation. */
window.examStorage = (() => {
  let db, scope, key;
  const MAX_BYTES = 64 * 1024 * 1024;
  const request = r => new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
  function transaction(mode, callback) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction("records", mode);
      let result;
      try { result = callback(tx.objectStore("records")); } catch (error) { reject(error); return; }
      tx.oncomplete = () => resolve(result?.result);
      tx.onabort = tx.onerror = () => reject(tx.error || new Error("Local storage is unavailable"));
    });
  }
  const id = name => `${scope}|${name}`;
  async function init(value) {
    if (!crypto.subtle || !window.indexedDB) throw new Error("This browser cannot securely preserve exam data.");
    scope = value;
    if (!db) {
      const opening = indexedDB.open("assessbridge-exam-recovery", 1);
      opening.onupgradeneeded = () => opening.result.createObjectStore("records");
      db = await request(opening);
    }
    key = await transaction("readonly", store => store.get(id("key")));
    if (!key) {
      key = await crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
      await transaction("readwrite", store => store.put(key, id("key")));
    }
    // Delete expired encrypted payloads, including abandoned attempts. Keep keys
    // until their payloads have been cleared; an upload needs the original key.
    const names = await transaction("readonly", store => store.getAllKeys());
    for (const name of names) {
      if (String(name).endsWith("|key")) continue;
      const record = await transaction("readonly", store => store.get(name));
      if (record?.expires && record.expires < Date.now()) await transaction("readwrite", store => store.delete(name));
    }
  }
  async function put(name, value, binary = false) {
    const bytes = binary ? await value.arrayBuffer() : new TextEncoder().encode(JSON.stringify(value));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encrypted = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(id(name)) }, key, bytes);
    await transaction("readwrite", store => store.put({ iv, encrypted, binary, expires: Date.now() + 86400000 }, id(name)));
  }
  async function get(name) {
    const record = await transaction("readonly", store => store.get(id(name)));
    if (!record) return null;
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: record.iv, additionalData: new TextEncoder().encode(id(name)) }, key, record.encrypted);
    return record.binary ? new Blob([plain]) : JSON.parse(new TextDecoder().decode(plain));
  }
  async function names(prefix = "") {
    const all = await transaction("readonly", store => store.getAllKeys());
    return all.filter(name => String(name).startsWith(id(prefix)) && name !== id("key")).map(name => name.slice(scope.length + 1)).sort();
  }
  async function remove(name) { await transaction("readwrite", store => store.delete(id(name))); }
  async function bytesUsed() {
    let size = 0;
    for (const name of await names("chunk:")) {
      const record = await transaction("readonly", store => store.get(id(name)));
      size += record?.encrypted?.byteLength || 0;
    }
    return size;
  }
  async function check() {
    await put("storage-check", { ok: true });
    if (!(await get("storage-check"))?.ok) throw new Error("Local storage check failed");
    await remove("storage-check");
    await navigator.storage?.persist?.().catch(() => false);
    const estimate = await navigator.storage?.estimate?.();
    if (estimate?.quota && estimate.quota - estimate.usage < MAX_BYTES) throw new Error("Free at least 64 MB of browser storage, then retry.");
    return true;
  }
  async function clear() { for (const name of await names()) await remove(name); }
  return { init, put, get, names, remove, bytesUsed, check, clear, MAX_BYTES };
})();
