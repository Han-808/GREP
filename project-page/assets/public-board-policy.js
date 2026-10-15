window.GREP_PUBLIC_BOARDS = {"default_scope": "open-space", "scopes": [{"id": "open-space", "label": "Open-space"}, {"id": "multi-room", "label": "Multi-room"}]};
(() => {
  const policy = window.GREP_PUBLIC_BOARDS;
  const url = new URL(location.href);
  const scope = url.searchParams.get('scope');
  if (scope && !policy.scopes.some(item => item.id === scope)) {
    url.searchParams.set('scope', policy.default_scope);
    for (const key of ['condition', 'group', 'dataset', 'model', 'scene', 'layout', 'room']) url.searchParams.delete(key);
    history.replaceState(null, '', url);
  }
})();
