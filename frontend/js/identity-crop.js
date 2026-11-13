/* Original captures stay intact; selected corners are sent as a crop hint. */
(() => {
  const element = id => document.getElementById(id);
  const editor = element("card-crop-editor"), canvas = element("card-crop-canvas");
  const surface = element("card-crop-surface"), handles = [...surface.querySelectorAll("button")];
  const adjust = element("adjust-card-outline"), status = element("card-outline-status");
  let source = null, original = null, selected = null, draft = null, activePointer = null, busy = false;
  const copy = points => points ? points.map(point => [...point]) : null;
  const changed = () => window.dispatchEvent(new Event("identity-crop-change"));
  const error = text => candidateFeedback.show("card-crop-error", text);

  function draw(points) {
    if (!source) return;
    canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
    const context = canvas.getContext("2d"); context.drawImage(source, 0, 0);
    context.beginPath(); context.rect(0, 0, canvas.width, canvas.height);
    points.forEach(([x, y], index) => context[index ? "lineTo" : "moveTo"](x*canvas.width, y*canvas.height));
    context.closePath(); context.fillStyle = "rgba(7, 24, 45, .55)"; context.fill("evenodd");
    context.beginPath();
    points.forEach(([x, y], index) => context[index ? "lineTo" : "moveTo"](x*canvas.width, y*canvas.height));
    context.closePath(); context.strokeStyle = "#58b9ff"; context.lineWidth = Math.max(3, canvas.width/250); context.stroke();
    points.forEach(([x, y], index) => {
      handles[index].style.left = `${x*100}%`; handles[index].style.top = `${y*100}%`;
    });
  }
  function open() {
    if (!source) return;
    draft = copy(selected) || [[.12, .16], [.88, .16], [.88, .84], [.12, .84]];
    editor.hidden = false; error(""); draw(draft); changed();
    editor.scrollIntoView({ behavior: "smooth", block: "center" }); handles[0].focus({ preventScroll: true });
  }
  function close() { editor.hidden = true; activePointer = null; changed(); }
  function reset() {
    source = original = selected = draft = null;
    editor.hidden = true; adjust.hidden = true; status.hidden = true; activePointer = null; error(""); changed();
  }
  function setSource(data) {
    reset(); original = data;
    const loaded = new Image();
    loaded.onload = () => { if (original !== data) return; source = loaded; adjust.hidden = false; adjust.disabled = busy; };
    loaded.src = data;
  }
  function move(index, x, y) {
    draft[index] = [Math.max(.005, Math.min(.995, x)), Math.max(.005, Math.min(.995, y))];
    error(""); draw(draft);
  }
  handles.forEach((handle, index) => {
    handle.addEventListener("pointerdown", event => {
      event.preventDefault(); handle.focus({ preventScroll: true });
      activePointer = { id: event.pointerId, index }; handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener("pointermove", event => {
      if (activePointer?.id !== event.pointerId || activePointer.index !== index) return;
      const bounds = surface.getBoundingClientRect();
      move(index, (event.clientX-bounds.left)/bounds.width, (event.clientY-bounds.top)/bounds.height);
    });
    const release = () => { activePointer = null; };
    handle.addEventListener("pointerup", release); handle.addEventListener("pointercancel", release);
    handle.addEventListener("keydown", event => {
      const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
      if (!directions[event.key]) return;
      event.preventDefault(); const step = event.shiftKey ? .02 : .003;
      const [x, y] = draft[index], [dx, dy] = directions[event.key]; move(index, x+dx*step, y+dy*step);
    });
  });
  adjust.addEventListener("click", open);
  element("cancel-card-outline").addEventListener("click", () => { close(); adjust.focus(); });
  element("apply-card-outline").addEventListener("click", () => {
    const crosses = draft.map((point, index) => {
      const next = draft[(index+1)%4], last = draft[(index+2)%4];
      return (next[0]-point[0])*(last[1]-next[1])-(next[1]-point[1])*(last[0]-next[0]);
    });
    if (!crosses.every(value => value > .005)) { error("Place the markers around the four corners in order."); return; }
    selected = copy(draft); draw(selected);
    element("identity-card-preview").src = canvas.toDataURL("image/jpeg", .94);
    status.hidden = false; close(); adjust.focus();
  });
  function setBusy(value) {
    busy = value;
    adjust.disabled = value;
    editor.querySelectorAll("button").forEach(button => { button.disabled = value; });
  }
  window.assessbridgeCardCrop = { setSource, reset, open, setBusy, corners: () => copy(selected), isOpen: () => !editor.hidden };
})();
