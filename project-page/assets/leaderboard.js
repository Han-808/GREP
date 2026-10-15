const $ = id => document.getElementById(id);
    const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
    const fmt = (value, digits=(activeBoard === "open-space" ? 1 : 2)) => value == null ? "—" : Number(value).toFixed(digits);
    const metricGroups = [
      {id:"plausibility", label:"Physical Plausibility", fine:["collision","oob","support"]},
      {id:"spatial_reasoning", label:"Functional Semantics", fine:["functional_consistency","semantic_placement_consistency"]},
      {id:"visual_coherence", label:"Visual Coherence", fine:["style_consistency","scale_consistency","object_pairing_consistency"]}
    ];
    const viewState = {view:"overall", category:"plausibility", fine:"collision"};
    let leaderboardData = null;
    let leaderboardRoot = null;
    const allowedBoards=new Set(window.GREP_PUBLIC_BOARDS.scopes.map(item => item.id));
    const requestedBoard=new URLSearchParams(location.search).get("scope");
    let activeBoard=allowedBoards.has(requestedBoard)?requestedBoard:"open-space";

    const categoryMeta = id => leaderboardData?.submetrics?.find(item => item.id === id) || metricGroups.find(item => item.id === id);
    const fineMeta = id => leaderboardData?.fine_metrics?.find(item => item.id === id);
    const scoreValue = model => viewState.view === "overall" ? model.overall : viewState.view === "category" ? model.submetric_scores?.[viewState.category] : model.fine_metric_scores?.[viewState.fine];
    const selectedMeta = () => viewState.view === "overall" ? {label:"Overall", description:"Scores range from 0 to 100; higher is better."} : viewState.view === "category" ? {...categoryMeta(viewState.category),description:""} : {...fineMeta(viewState.fine),description:""};

    function scoreCell(value, kind="") {
      const digits = activeBoard !== "open-space" || kind === "overall" ? 2 : 1;
      return `<div class="score-cell ${kind}" role="cell"><strong>${fmt(value, digits)}</strong></div>`;
    }

    function caseCount(model) {
      const value = activeBoard === "open-space" ? model.scored_case_count : model.completed;
      return Number.isInteger(value) && value >= 0 ? value : "—";
    }

    function exampleLink(model) {
      return `./scene_liveboard.html?scope=${encodeURIComponent(activeBoard)}&dataset=${encodeURIComponent(model.key)}`;
    }

    function legacyDisplayLabel(item, scope) {
      return scope === "open-space" && item.key === "hy3_aw" && !/\blegacy\b/i.test(item.label)
        ? `${item.label} · legacy` : item.label;
    }

    function modelNote(model) {
      return model.key === "anthropic_opus48" ? `<span class="model-note">non-thinking</span>` : "";
    }

    function renderScoringSummary() {
      const method = leaderboardData.methodology || {};
      const profile = leaderboardData.scoring_profile || {};
      const descriptions = {plausibility:"Collision, Support and Out of Bounds",spatial_reasoning:"Function and Placement",visual_coherence:"Scale, Style and Pairing"};
      const aggregation = activeBoard === "open-space"
        ? "Per-model averages over the scored scene results. The table reports the scored case count for each model."
        : method.aggregation_policy || "Fixed input net-area-weighted rooms within each layout; equal weight across layouts.";
      $("scoring-summary").innerHTML = `<div class="summary-formula"><strong>${esc(method.overall_formula || "Scores use the source reporting profile.")}</strong><span>${esc(profile.metric_formula || method.score_mapping || "")}</span></div><div class="summary-categories">${leaderboardData.metrics.map(metric => `<div class="summary-category"><strong>${esc(metric.label)}</strong><span>${esc(descriptions[metric.id] || metric.label)}</span><span>${esc(metric.description || "")}</span></div>`).join("")}</div><div class="protocol-notes"><p><strong>Aggregation.</strong> ${esc(aggregation)}</p></div>`;
    }

    function renderMetricOptions() {
      const options = $("metric-options");
      if (viewState.view === "overall") {
        options.hidden = true;
        options.innerHTML = "";
        return;
      }
      options.hidden = false;
      if (viewState.view === "category") {
        options.innerHTML = `<div class="metric-group">${metricGroups.map(group => `<button class="metric-option" type="button" data-category="${group.id}" aria-pressed="${group.id === viewState.category}">${esc(group.label)}</button>`).join("")}</div>`;
      } else {
        options.innerHTML = metricGroups.map(group => `<div class="metric-group" role="group" aria-label="${esc(group.label)}"><span class="metric-group-label">${esc(group.label)}</span>${group.fine.map(id => { const metric = fineMeta(id); return `<button class="metric-option" type="button" data-fine="${id}" aria-pressed="${id === viewState.fine}">${esc(metric?.label || id)}</button>`; }).join("")}</div>`).join("");
      }
      options.querySelectorAll(".metric-option").forEach(button => button.addEventListener("click", () => {
        if (button.dataset.category) viewState.category = button.dataset.category;
        if (button.dataset.fine) viewState.fine = button.dataset.fine;
        renderSelectors(); renderHeader(); renderRows();
      }));
    }

    function renderSelectors() {
      $("view-tabs").querySelectorAll("button").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.view === viewState.view)));
      renderMetricOptions();
    }

    function renderHeader() {
      const meta = selectedMeta();
      const board = $("ranking-board");
      board.dataset.view = viewState.view === "overall" ? "overall" : "metric";
      $("ranking-title").textContent = viewState.view === "overall" ? "Overall ranking" : `${meta?.label || "Metric"} ranking`;
      $("ranking-description").textContent = meta?.description || "Models are reranked by the selected metric.";
      $("ranking-header").innerHTML = viewState.view === "overall"
        ? `<span role="columnheader">Rank</span><span role="columnheader">Model</span><span class="overall-heading" role="columnheader">Overall ↑</span>${metricGroups.map(group => `<span class="category-heading" role="columnheader">${esc(group.label)}</span>`).join("")}<span role="columnheader">${activeBoard === "open-space" ? "Scored scenes" : "Layouts"}</span>`
        : `<span role="columnheader">Rank</span><span role="columnheader">Model</span><span role="columnheader">${esc(meta?.label || "Score")} ↑</span><span role="columnheader">${activeBoard === "open-space" ? "Scored scenes" : "Layouts"}</span>`;
    }

    function renderRows() {
      const ranked = leaderboardData.models.map((model, sourceIndex) => ({...model, sourceIndex, selectedScore: scoreValue(model)})).sort((a, b) => {
        if (a.selectedScore == null) return b.selectedScore == null ? a.sourceIndex - b.sourceIndex : 1;
        if (b.selectedScore == null) return -1;
        return Number(b.selectedScore) - Number(a.selectedScore) || a.sourceIndex - b.sourceIndex;
      });
      $("rows").innerHTML = ranked.map((model, index) => {
        const rowClass = viewState.view === "overall" ? "overall-view" : "metric-view";
        const values = viewState.view === "overall" ? `${scoreCell(model.overall, "overall")}${metricGroups.map(group => scoreCell(model.submetric_scores?.[group.id], "category-score")).join("")}` : scoreCell(model.selectedScore, viewState.view === "overall" ? "overall" : "");
        return `<div class="ranking-entry ${index === 0 ? "winner" : ""}" role="rowgroup"><div class="ranking-row ${rowClass}" role="row"><span class="rank" role="cell">${String(index + 1).padStart(2, "0")}</span><div class="model-cell" role="cell"><a class="model-name" href="${exampleLink(model)}" aria-label="Browse ${esc(legacyDisplayLabel(model, activeBoard))} examples">${esc(legacyDisplayLabel(model, activeBoard))}<span class="model-link-arrow" aria-hidden="true">↗</span></a>${modelNote(model)}</div>${values}<span class="case-count" role="cell">${caseCount(model)}</span></div></div>`;
      }).join("");
      $("ranking-board").setAttribute("aria-rowcount", String(ranked.length + 1));
      renderOverallChart();
    }

    function chartLabelLines(label) {
      const lines = [];
      String(label || "—").split(/\s*·\s*/).forEach(part => {
        let current = "";
        part.replace(/([a-z])([A-Z])/g, "$1 $2").split(/\s+/).filter(Boolean).forEach(word => {
          if (current && `${current} ${word}`.length > 15) {
            lines.push(current);
            current = word;
          } else current = current ? `${current} ${word}` : word;
        });
        if (current) lines.push(current);
      });
      return lines.length ? lines : ["—"];
    }

    function chartDomain(scores, scope = "") {
      const valid = scores.filter(value => value != null && Number.isFinite(Number(value))).map(Number);
      if (!valid.length) return {min: 0, max: 100, ticks: [0, 25, 50, 75, 100]};
      const minimum = Math.min(...valid), maximum = Math.max(...valid);
      const span = Math.max(4, maximum - minimum), center = (minimum + maximum) / 2;
      const padding = Math.max(.75, span * .06), rounding = span > 20 ? 5 : 1;
      // Round the floor down to a decade and leave visible stems below the lowest score.
      const lowerPadding = Math.max(2, span * .2);
      let min = Math.max(0, Math.floor((minimum - lowerPadding) / 10) * 10);
      let max = Math.min(100, Math.ceil((center + span / 2 + padding) / rounding) * rounding);
      if (max - min < 4) {
        max = Math.min(100, min + 4);
      }
      if (scope === "agent" || scope === "agent-plus20" || scope === "pipeline") {
        // Preferred floors must never override a lower, data-driven safe bound.
        min = Math.min(min, scope === "pipeline" ? 50 : 80);
      }
      const interval = [1, 2, 5, 10, 20, 25].find(value => value >= (max - min) / 5) || 25;
      const ticks = [min];
      for (let value = Math.ceil(min / interval) * interval; value < max; value += interval) {
        if (value - min >= interval * .35 && max - value >= interval * .35) ticks.push(value);
      }
      ticks.push(max);
      return {min, max, ticks};
    }

    function renderOverallChart() {
      const chart = $("overall-chart"), canvas = $("overall-chart-canvas");
      const ranked = (leaderboardData?.models || [])
        .map((model, sourceIndex) => ({...model, sourceIndex}))
        .filter(model => model.overall != null && Number.isFinite(Number(model.overall)))
        .sort((a, b) => Number(b.overall) - Number(a.overall) || a.sourceIndex - b.sourceIndex);
      if (viewState.view !== "overall" || !ranked.length) {
        $("chart-disclosure").hidden = true;
        chart.hidden = true;
        canvas.innerHTML = "";
        $("overall-chart-range").textContent = "";
        return;
      }
      $("chart-disclosure").hidden = false;
      chart.hidden = false;
      if (!$("chart-disclosure").open) return;
      const domain = chartDomain(ranked.map(model => model.overall), activeBoard === "agent-plus20" ? "agent" : activeBoard);
      const labels = ranked.map(model => chartLabelLines(legacyDisplayLabel(model, activeBoard) || model.key));
      const width = Math.max(480, chart.clientWidth - 48, 76 + ranked.length * 118);
      const left = 48, right = 20, top = 40, plotHeight = 204;
      const bottom = 34 + Math.max(...labels.map(lines => lines.length)) * 19;
      const height = top + plotHeight + bottom, baseline = top + plotHeight;
      const plotWidth = width - left - right, step = plotWidth / ranked.length;
      const scaleY = score => top + (domain.max - Number(score)) / (domain.max - domain.min) * plotHeight;
      const zh = document.documentElement.dataset.lang === "zh";
      const axisNote = zh ? "纵轴已缩放 · 满分 100" : "Zoomed score axis · full scale 0–100";
      const grid = domain.ticks.map(value => {
        const y = scaleY(value);
        return `<line class="chart-grid-line" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"></line><text class="chart-tick" x="${left - 12}" y="${y + 5}" text-anchor="end">${value}</text>`;
      }).join("");
      const points = ranked.map((model, index) => {
        const score = Number(model.overall), x = left + step * (index + .5), y = scaleY(score);
        const label = String(legacyDisplayLabel(model, activeBoard) || model.key || "Model");
        const tspans = labels[index].map((line, lineIndex) => `<tspan x="${x}" dy="${lineIndex ? 19 : 0}">${esc(line)}</tspan>`).join("");
        const title = `${label} · ${fmt(score, 2)}`;
        return `<g class="chart-point${index === 0 ? " chart-point-leader" : ""}" tabindex="0" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title><line class="chart-stem" x1="${x}" y1="${y}" x2="${x}" y2="${baseline}"></line><circle class="chart-halo" cx="${x}" cy="${y}" r="12"></circle><circle class="chart-dot" cx="${x}" cy="${y}" r="5.5"></circle><text class="chart-value" x="${x}" y="${y - 21}" text-anchor="middle">${fmt(score, 2)}</text><text class="chart-model-label" x="${x}" y="${baseline + 30}" text-anchor="middle">${tspans}</text></g>`;
      }).join("");
      const summary = `${axisNote}. ${domain.min}–${domain.max}. ` + ranked.map(model => `${legacyDisplayLabel(model, activeBoard)}: ${fmt(model.overall, 2)}`).join("; ");
      $("overall-chart-range").textContent = `${zh ? "显示区间" : "Range"} ${domain.min}–${domain.max}`;
      $("overall-chart-note").textContent = axisNote;
      canvas.innerHTML = `<svg class="overall-chart-svg" style="min-width:${width}px;height:${height}px" viewBox="0 0 ${width} ${height}" role="group" aria-label="${esc(summary)}"><title>${esc(summary)}</title>${grid}${points}</svg>`;
    }

    window.addEventListener("resize", renderOverallChart);
    $("chart-disclosure").addEventListener("toggle", renderOverallChart);

    function renderBenchmarkTabs() {
      const boards=leaderboardRoot?.leaderboard_views||[];
      const board=boards.find(item=>item.id===activeBoard)||boards[0]||leaderboardRoot;
      leaderboardData=board;
      activeBoard=board.id||activeBoard;
      $("benchmark-tabs").querySelectorAll("button").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.board===activeBoard)));
      $("benchmark-note").textContent="";
      $("board-subtitle").textContent = activeBoard === "open-space"
        ? "Single-room generation, evaluated for physical plausibility, visual coherence, and human use."
        : "Multi-room generation, evaluated across the rooms and layouts of each model.";
      $("scope-name").textContent = activeBoard === "open-space" ? "Single-room · wall-free" : "Multi-room";
      $("coverage-note").textContent = board.coverage_note || "";
      $("browse-examples").href = `./scene_liveboard.html?scope=${activeBoard}`;
      $("caption-examples").href = `./scene_liveboard.html?scope=${activeBoard}`;
      $("table-caption-scope").textContent = activeBoard === "open-space"
        ? "Scored scenes are reported per model."
        : "Layout scores aggregate their constituent rooms by fixed input net area.";
      $("tier-policy").hidden=activeBoard!=="open-space";
      window.updateBoardHierarchy?.(activeBoard);
    }

    function render(data) {
      leaderboardRoot = data;
      if (!data.leaderboard_views?.some(item=>item.id===activeBoard)) activeBoard=data.default_leaderboard_view||"open-space";
      renderBenchmarkTabs();
      renderScoringSummary();
      renderSelectors();
      renderHeader();
      renderRows();
    }

    $("benchmark-tabs").querySelectorAll("button").forEach(button=>button.addEventListener("click",()=>{activeBoard=button.dataset.board;viewState.view="overall";const url=new URL(location.href);url.searchParams.set("scope",activeBoard);history.replaceState(null,"",url);renderBenchmarkTabs();renderScoringSummary();renderSelectors();renderHeader();renderRows()}));
    $("view-tabs").querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
      viewState.view = button.dataset.view;
      renderSelectors(); renderHeader(); renderRows();
    }));
    async function load() {
      try {
        const response = await fetch("./leaderboard.json", {cache:"no-store"});
        if (!response.ok) throw new Error(`status ${response.status}`);
        render(await response.json());
      } catch (error) {
        $("rows").innerHTML = `<div class="error">Leaderboard update failed: ${esc(error.message)}</div>`;
      }
    }
    load();
