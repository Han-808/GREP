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
  window.updateBoardHierarchy = active => {
    const scope = policy.scopes.some(item => item.id === active) ? active : policy.default_scope;
    const kinds = document.getElementById('board-kind');
    if (kinds) { kinds.replaceChildren(); kinds.hidden = true; }
    const levels = document.getElementById('floorplan-level');
    if (levels) {
      levels.hidden = false;
      levels.setAttribute('aria-label', 'Model benchmarks');
      levels.replaceChildren(...policy.scopes.map(item => makeLink(item.id === 'open-space' ? 'Single-room' : 'Multi-room', `${page}?scope=${item.id}`, item.id === scope)));
    }
    const context = document.getElementById('hierarchy-context');
    if (context) context.textContent = scope === 'open-space' ? 'Model-generated · Wall-free rooms' : 'Model-generated · Multiple rooms';
    nav.replaceChildren(
      makeLink('Overview', './'),
      makeLink('Examples', `./scene_liveboard.html?scope=${scope}`, !ranking),
      makeLink('Leaderboard', `./leaderboard.html?scope=${scope}`, ranking)
    );
  };
  document.title = `${ranking ? 'Leaderboard' : 'Examples'} · GREP`;
  window.updateBoardHierarchy(new URLSearchParams(location.search).get('scope') || policy.default_scope);
  if (!document.querySelector('.board-footer')) {
    const footer = document.createElement('footer'); footer.className = 'board-footer';
    const label = document.createElement('span'); label.textContent = 'Grounding generated room evaluation in principles.';
    const links = document.createElement('div'); links.className = 'footer-links'; links.append(makeLink('Overview', './'), makeLink('Code ↗', 'https://github.com/Han-808/GREP'));
    footer.append(makeLink('GREP', './'), label, links); document.body.append(footer);
  }
})();
