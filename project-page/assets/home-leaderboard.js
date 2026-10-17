(() => {
  function initialize() {
    const preview = document.getElementById('leaderboard-preview');
    if (!preview) return;

    const buttons = [...preview.querySelectorAll('#preview-scope [data-preview-scope]')];
    const rows = document.getElementById('leaderboard-preview-rows');
    const caption = document.getElementById('leaderboard-preview-caption');
    const status = document.getElementById('leaderboard-preview-status');
    const fullLink = document.getElementById('leaderboard-preview-link');
    if (!buttons.length || !rows || !caption || !status || !fullLink) return;

    const labels = {
      'open-space': 'Open space (no walls)',
      'multi-room': 'With wall layouts',
    };
    let scope = buttons.find(button => button.getAttribute('aria-pressed') === 'true')?.dataset.previewScope;
    if (!Object.hasOwn(labels, scope)) scope = 'open-space';
    let views;
    let failed = false;

    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.setAttribute('aria-atomic', 'true');

    function compareText(a, b) {
      return a < b ? -1 : a > b ? 1 : 0;
    }

    function showMessage(message) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 3;
      cell.textContent = message;
      row.append(cell);
      rows.replaceChildren(row);
    }

    function render() {
      for (const button of buttons) {
        const buttonScope = button.dataset.previewScope;
        button.setAttribute('aria-pressed', String(buttonScope === scope));
        if (Object.hasOwn(labels, buttonScope)) button.textContent = labels[buttonScope];
      }
      fullLink.href = `./leaderboard.html?scope=${encodeURIComponent(scope)}`;
      fullLink.setAttribute('aria-label', `View the full leaderboard: ${labels[scope]}`);
      caption.textContent = `${labels[scope]} · Overall GREP score`;
      preview.setAttribute('aria-busy', String(!views && !failed));

      if (!views && !failed) {
        preview.dataset.state = 'loading';
        showMessage('Loading scores…');
        status.textContent = 'Loading leaderboard scores.';
        return;
      }

      const sourceModels = views?.find(view => view?.id === scope)?.models;
      if (failed || !Array.isArray(sourceModels)) {
        preview.dataset.state = 'error';
        showMessage('Preview unavailable. Use the full leaderboard below.');
        status.textContent = `${labels[scope]} preview unavailable. The full leaderboard link is available.`;
        return;
      }

      const models = sourceModels.filter(model => (
        typeof model?.overall === 'number' && Number.isFinite(model.overall)
        && typeof model.label === 'string' && model.label.trim()
      )).sort((a, b) => (
        b.overall - a.overall
        || compareText(a.label, b.label)
        || compareText(String(a.key ?? ''), String(b.key ?? ''))
      )).slice(0, 3);

      if (!models.length) {
        preview.dataset.state = 'empty';
        showMessage('Scores coming soon.');
        status.textContent = `${labels[scope]} scores are coming soon.`;
        return;
      }

      const fragment = document.createDocumentFragment();
      models.forEach((model, index) => {
        const row = document.createElement('tr');
        const rank = document.createElement('td');
        rank.className = 'preview-rank';
        rank.textContent = String(index + 1);
        const name = document.createElement('th');
        name.scope = 'row';
        name.textContent = model.label;
        const score = document.createElement('td');
        score.className = 'preview-score';
        score.textContent = model.overall.toFixed(1);
        row.append(rank, name, score);
        fragment.append(row);
      });
      rows.replaceChildren(fragment);
      preview.dataset.state = 'ready';
      status.textContent = `${labels[scope]}. Showing the top ${models.length} models by paper average.`;
    }

    for (const button of buttons) {
      button.addEventListener('click', () => {
        const nextScope = button.dataset.previewScope;
        if (!Object.hasOwn(labels, nextScope) || nextScope === scope) return;
        scope = nextScope;
        render();
      });
    }

    render();
    fetch('./paper_leaderboard.json')
      .then(response => {
        if (!response.ok) throw new Error(`Leaderboard request failed: ${response.status}`);
        return response.json();
      })
      .then(data => {
        if (!Array.isArray(data.leaderboard_views)) throw new Error('Missing leaderboard views');
        views = data.leaderboard_views;
      })
      .catch(() => { failed = true; })
      .finally(render);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true });
  else initialize();
})();
