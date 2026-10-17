const grepModelDisplay = [
  {name:'GPT-6 Astra', wf:'gpt6_astra_high', rc:'gpt6_astra_high'},
  {name:'GPT-5.6 Sol', wf:'gpt56sol', rc:'gpt-5.6-sol'},
  {name:'Claude Opus 5', wf:'anthropic_opus5', rc:'claude-opus-5-aihub'},
  {name:'Claude Fable 5', wf:'anthropic_fable5', rc:'claude-fable-5-aihub'},
  {name:'Claude Sonnet 5', wf:'anthropic_sonnet5', rc:'claude-sonnet-5-aihub'},
  {name:'Kimi K3', wf:'kimi_k3', rc:'kimi-k3'},
  {name:'GLM-5.3', wf:'glm53', rc:'glm-5.3'},
  {name:'HY4 Preview', wf:'hy4_preview', rc:'hy4-preview-tokenhub'}
];
window.grepModelLabel = item => grepModelDisplay.find(model => model.wf === item.key || model.rc === item.key)?.name || item.label || item.key;
window.grepModelKey = (key, scope) => grepModelDisplay.find(model => model.wf === key || model.rc === key)?.[scope === 'open-space' ? 'wf' : 'rc'] || null;
(() => {
  const policy = window.GREP_PUBLIC_BOARDS;
  const ranking = location.pathname.includes('leaderboard') && !location.pathname.includes('liveboard');
  const page = ranking ? './leaderboard.html' : './scene_liveboard.html';
  const makeLink = (label, href, current = false) => {
    const link = document.createElement('a');
    link.textContent = label;
    link.href = href;
    if (current) link.setAttribute('aria-current', 'page');
    return link;
  };
  const header = document.querySelector('.topbar');
  const brand = makeLink('GREP', './'); brand.className = 'brand'; brand.setAttribute('aria-label', 'GREP overview');
  const nav = document.createElement('nav'); nav.className = 'site-navigation'; nav.setAttribute('aria-label', 'Main navigation');
  const code = makeLink('Code ↗', 'https://github.com/Han-808/GREP'); code.className = 'board-code';
  if (header) header.replaceChildren(brand, nav, code);
  window.updateBoardHierarchy = (active, modelKey = new URLSearchParams(location.search).get('dataset') || new URLSearchParams(location.search).get('model')) => {
    const scope = policy.scopes.some(item => item.id === active) ? active : policy.default_scope;
    const boardLink = (target, targetScope = scope) => { const params = new URLSearchParams({scope: targetScope}); const key = targetScope === scope ? modelKey : window.grepModelKey(modelKey, targetScope); if (key) params.set('dataset', key); return `${target}?${params}`; };
    const kinds = document.getElementById('board-kind');
    if (kinds) { kinds.textContent = 'Models'; kinds.setAttribute('aria-label', 'Generation setting'); kinds.hidden = false; }
    const levels = document.getElementById('floorplan-level');
    if (levels) {
      levels.hidden = false;
      levels.setAttribute('aria-label', 'Model benchmarks');
      levels.replaceChildren(...policy.scopes.map(item => makeLink(item.id === 'open-space' ? 'Open space (no walls)' : 'With wall layouts', boardLink(page, item.id), item.id === scope)));
    }
    const context = document.getElementById('hierarchy-context');
    if (context) { context.textContent = ''; context.hidden = true; }
    nav.replaceChildren(
      makeLink('Overview', './'),
      makeLink('Examples', boardLink('./scene_liveboard.html'), !ranking),
      makeLink('Leaderboard', boardLink('./leaderboard.html'), ranking)
    );
  };
  document.title = `${ranking ? 'Leaderboard' : 'Examples'} · GREP`;
  window.updateBoardHierarchy(new URLSearchParams(location.search).get('scope') || policy.default_scope);
  if (!document.querySelector('.board-footer')) {
    const footer = document.createElement('footer'); footer.className = 'board-footer';
    const label = document.createElement('span'); label.textContent = 'Grounding generated room evaluation in principles.';
    const links = document.createElement('div'); links.className = 'footer-links'; links.append(makeLink('Overview', './'));
    footer.append(makeLink('GREP', './'), label, links); document.body.append(footer);
  }
})();
