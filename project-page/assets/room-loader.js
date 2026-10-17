// Keep recovery available even if the 3D module or one of its imports fails.
(() => {
  const script = document.currentScript;
  const stage = document.getElementById('scene-stage');
  const panel = document.getElementById('loading-state');
  const title = document.getElementById('room-loading-title');
  const note = document.getElementById('room-loading-note');
  const retry = document.getElementById('retry-room');
  const poster = document.getElementById('room-poster');
  if (!script || !stage || !panel || !title || !note || !retry || !poster) return;

  let ready = false;
  let failed = false;
  stage.dataset.loadState = 'loading';
  retry.addEventListener('click', () => window.location.reload());

  function showRecovery(isFailure) {
    if (ready && !isFailure) return;
    if (isFailure) { failed = true; ready = false; clearTimeout(slowTimer); }
    stage.dataset.loadState = isFailure ? 'error' : 'slow';
    if (isFailure) {
      document.querySelectorAll('.demo-shell button, .finding-row, .hotspot').forEach(button => { button.disabled = true; });
      retry.disabled = false;
    }
    panel.hidden = false;
    poster.hidden = false;
    title.textContent = isFailure ? 'The 3D preview could not load.' : 'The room is taking longer to load.';
    note.textContent = isFailure
      ? 'The static room preview is shown. Retry loading, or continue reading below.'
      : 'The static preview is shown while loading continues. You can retry if the connection has stalled.';
    retry.hidden = false;
  }

  const slowTimer = setTimeout(() => { if (!failed) showRecovery(false); }, 20000);
  window.addEventListener('grep:room-ready', () => {
    ready = true;
    failed = false;
    clearTimeout(slowTimer);
    stage.dataset.loadState = 'ready';
    panel.hidden = true;
    poster.hidden = true;
    retry.hidden = true;
    stage.removeAttribute('aria-busy');
  });
  window.addEventListener('grep:room-error', () => {
    stage.setAttribute('aria-busy', 'false');
    showRecovery(true);
  });
  window.addEventListener('pagehide', () => clearTimeout(slowTimer), { once: true });
  stage.setAttribute('aria-busy', 'true');

  const moduleUrl = new URL(script.dataset.module, document.baseURI).href;
  import(moduleUrl).catch(error => {
    console.error('GREP room module:', error);
    stage.setAttribute('aria-busy', 'false');
    showRecovery(true);
  });
})();
