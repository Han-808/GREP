const $ = id => document.getElementById(id);
    const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
    const fmt = (value, digits=1) => value == null ? "—" : Number(value).toFixed(digits);
    const metricGroups = [
      {id:"plausibility", label:"Physical Validity", fine:["collision","support","oob"], weight:38, weights:[15,15,8]},
      {id:"visual_coherence", label:"Visual Coherence", fine:["scale_consistency","style_consistency","object_pairing_consistency"], weight:10, weights:[3,3,4]},
      {id:"spatial_reasoning", label:"Human-Use", fine:["functional_consistency","semantic_placement_consistency"], weight:52, weights:[32,20]}
    ];
    const metricLabels = {collision:"Collision",support:"Support",oob:"Out-of-bounds",scale_consistency:"Scale",style_consistency:"Style",object_pairing_consistency:"Pairing",functional_consistency:"Function",semantic_placement_consistency:"Placement"};
    const viewState = {view:"overall", category:"plausibility", fine:"collision"};
    let leaderboardData = null;
    let leaderboardRoot = null;
    let paperLeaderboardRoot = null;
    const allowedBoards=new Set(window.GREP_PUBLIC_BOARDS.scopes.map(item => item.id));
    const requestedBoard=new URLSearchParams(location.search).get("scope");
    const selectedModel=new URLSearchParams(location.search).get("dataset") || new URLSearchParams(location.search).get("model");
    let activeBoard=allowedBoards.has(requestedBoard)?requestedBoard:"open-space";

    const categoryMeta = id => metricGroups.find(item => item.id === id) || leaderboardData?.submetrics?.find(item => item.id === id);
    const fineMeta = id => ({...leaderboardData?.fine_metrics?.find(item => item.id === id),id,label:metricLabels[id] || id});
    const scoreValue = model => viewState.view === "overall" ? model.overall : viewState.view === "category" ? model.submetric_scores?.[viewState.category] : model.fine_metric_scores?.[viewState.fine];
    const selectedMeta = () => viewState.view === "overall" ? {label:"Overall", description:"0–100 · Higher is better"} : viewState.view === "category" ? {...categoryMeta(viewState.category),description:""} : {...fineMeta(viewState.fine),description:""};

    function scoreCell(value, kind="") {
      return `<div class="score-cell ${kind}" role="cell"><strong>${fmt(value)}</strong></div>`;
    }

    function caseCount(model) {
      const value = viewState.view === "fine" ? model.available_case_count : model.scored_case_count;
      return Number.isInteger(value) && value >= 0 ? value : "—";
    }

    function exampleLink(model) {
      return `./scene_liveboard.html?scope=${encodeURIComponent(activeBoard)}&dataset=${encodeURIComponent(model.key)}`;
    }

    function legacyDisplayLabel(item) { return window.grepModelLabel(item); }

    function modelNote(model) {
      return model.key === "anthropic_opus48" ? `<span class="model-note">non-thinking</span>` : "";
    }

    function renderScoringSummary() {
      const method = leaderboardData.methodology || {};
      const profile = leaderboardData.scoring_profile || {};
      const aggregation = activeBoard === "open-space"
        ? "Each scored scene has equal weight in its model average. Main averages and sample counts follow Table 1. Metric averages use the available case exports."
        : "A layout enters the ranking when every required room has a score. Room scores are weighted by net floor area within each layout, and ranked layouts have equal weight in the model average.";
      $("scoring-summary").innerHTML = `<div class="summary-formula"><strong>Overall = 38% Physical Validity + 10% Visual Coherence + 52% Human-Use</strong><span>${esc(profile.metric_formula || method.score_mapping || "")}</span></div><div class="summary-categories">${metricGroups.map(group => `<div class="summary-category"><strong>${esc(group.label)} · ${group.weight}%</strong><span>${group.fine.map((id,index) => `${esc(metricLabels[id])} ${group.weights[index]}%`).join(" · ")}</span></div>`).join("")}</div><div class="protocol-notes"><p><strong>Principle scores.</strong> Each principle is the weighted average of its metrics, normalized by that principle’s total weight.</p><p><strong>Aggregation.</strong> ${esc(aggregation)}</p></div>`;
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
      $("ranking-description").textContent = viewState.view === "fine" ? "Available-case avg. · 0–100" : "Paper avg. · 0–100";
      $("paper-table-reference").hidden = viewState.view === "fine";
      const countLabel = viewState.view === "fine"
        ? (activeBoard === "open-space" ? "Available scenes" : "Available layouts")
        : (activeBoard === "open-space" ? "Scored scenes" : "Scored layouts");
      $("ranking-header").innerHTML = viewState.view === "overall"
        ? `<span role="columnheader">Rank</span><span role="columnheader">Model</span><span class="overall-heading" role="columnheader">Avg. Overall ↑</span>${metricGroups.map(group => `<span class="category-heading" role="columnheader">Avg. ${esc(group.label)}</span>`).join("")}<span role="columnheader">${countLabel}</span>`
        : `<span role="columnheader">Rank</span><span role="columnheader">Model</span><span role="columnheader">Avg. ${esc(meta?.label || "Score")} ↑</span><span role="columnheader">${countLabel}</span>`;
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
        return `<div class="ranking-entry ${index === 0 && model.selectedScore != null ? "winner" : ""} ${model.key === selectedModel ? "selected-model" : ""}" data-model="${esc(model.key)}" role="rowgroup"><div class="ranking-row ${rowClass}" role="row"><span class="rank" role="cell">${model.selectedScore == null ? "—" : String(index + 1).padStart(2, "0")}</span><div class="model-cell" role="cell"><a class="model-name" href="${exampleLink(model)}" aria-label="Browse ${esc(legacyDisplayLabel(model, activeBoard))} examples">${esc(legacyDisplayLabel(model, activeBoard))}<span class="model-link-arrow" aria-hidden="true">↗</span></a>${modelNote(model)}</div>${values}<span class="case-count" role="cell">${caseCount(model)}</span></div></div>`;
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
        const title = `${label} · ${fmt(score)}`;
        return `<g class="chart-point${index === 0 ? " chart-point-leader" : ""}" tabindex="0" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title><line class="chart-stem" x1="${x}" y1="${y}" x2="${x}" y2="${baseline}"></line><circle class="chart-halo" cx="${x}" cy="${y}" r="12"></circle><circle class="chart-dot" cx="${x}" cy="${y}" r="5.5"></circle><text class="chart-value" x="${x}" y="${y - 21}" text-anchor="middle">${fmt(score)}</text><text class="chart-model-label" x="${x}" y="${baseline + 30}" text-anchor="middle">${tspans}</text></g>`;
      }).join("");
      const summary = `${axisNote}. ${domain.min}–${domain.max}. ` + ranked.map(model => `${legacyDisplayLabel(model, activeBoard)}: ${fmt(model.overall)}`).join("; ");
      $("overall-chart-range").textContent = `${zh ? "显示区间" : "Range"} ${domain.min}–${domain.max}`;
      $("overall-chart-note").textContent = axisNote;
      canvas.innerHTML = `<svg class="overall-chart-svg" style="min-width:${width}px;height:${height}px" viewBox="0 0 ${width} ${height}" role="group" aria-label="${esc(summary)}"><title>${esc(summary)}</title>${grid}${points}</svg>`;
    }

    window.addEventListener("resize", renderOverallChart);
    $("chart-disclosure").addEventListener("toggle", renderOverallChart);

    function renderBenchmarkTabs() {
      const boards=leaderboardRoot?.leaderboard_views||[];
      const available=boards.find(item=>item.id===activeBoard)||boards[0]||leaderboardRoot;
      const paper=paperLeaderboardRoot?.leaderboard_views?.find(item=>item.id===available.id);
      if (!paper) throw new Error("Paper averages unavailable for this setting.");
      const board={...available, ...paper, models:paper.models.map(model=>{
        const cases=available.models.find(item=>item.key===model.key);
        return {...model, fine_metric_scores:cases?.fine_metric_scores || {}, available_case_count:cases ? (cases.scored_case_count ?? cases.completed ?? 0) : 0};
      })};
      leaderboardData=board;
      activeBoard=board.id||activeBoard;
      const missingAstra=activeBoard==='multi-room'&&selectedModel==='gpt6_astra_high'&&!board.models.some(model=>model.key===selectedModel);
      $("missing-model").hidden=!missingAstra;
      $("missing-model").innerHTML=missingAstra?'<strong>GPT-6 Astra</strong> · Multi-room results are not included in the current dataset. <a href="./leaderboard.html?scope=open-space&dataset=gpt6_astra_high">View open-space results →</a>':'';
      $("benchmark-tabs").querySelectorAll("button").forEach(button=>button.setAttribute("aria-pressed",String(button.dataset.board===activeBoard)));
      $("benchmark-note").textContent="";
      $("board-subtitle").textContent = "Scene generation models evaluated by GREP.";
      $("scope-name").textContent = activeBoard === "open-space" ? "Wall-free single rooms" : "Regular multi-room layouts";
      $("coverage-note").textContent = board.coverage_note || "";
      $("browse-examples").href = `./scene_liveboard.html?scope=${activeBoard}`;
      $("caption-examples").href = `./scene_liveboard.html?scope=${activeBoard}`;
      $("table-caption-scope").textContent = activeBoard === "open-space"
        ? "Scored scenes are reported per model."
        : "Layout scores aggregate their constituent rooms by fixed input net area.";
      $("tier-policy").hidden=false;
      window.updateBoardHierarchy?.(activeBoard, selectedModel);
    }

    function render(data, paperData) {
      leaderboardRoot = data;
      paperLeaderboardRoot = paperData;
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
        const responses = await Promise.all([fetch("./leaderboard.json", {cache:"no-store"}), fetch("./paper_leaderboard.json", {cache:"no-store"})]);
        if (responses.some(response=>!response.ok)) throw new Error(`status ${responses.find(response=>!response.ok).status}`);
        const [available, paper] = await Promise.all(responses.map(response=>response.json()));
        render(available, paper);
      } catch (error) {
        $("rows").innerHTML = `<div class="error">Leaderboard update failed: ${esc(error.message)}</div>`;
      }
    }
    load();
