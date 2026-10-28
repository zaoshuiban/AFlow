const state = {
  data: null,
  activeExperiment: "expB_extended_v3",
  activeRound: 2,
  visible: new Set(),
  type: null,
  level: null,
  operator: null,
};

const $ = (selector) => document.querySelector(selector);
const ns = "http://www.w3.org/2000/svg";
const svgEl = (name, attrs = {}) => {
  const el = document.createElementNS(ns, name);
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value));
  return el;
};
const pct = (value) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const escapeHtml = (text) => String(text ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

function experiment(id = state.activeExperiment) {
  return state.data.experiments.find((item) => item.id === id);
}

function activeRound() {
  const exp = experiment();
  return exp.rounds.find((item) => item.round === state.activeRound) || exp.rounds[0];
}

function questionMap() {
  return new Map(state.data.questions.map((item) => [item.id, item]));
}

function filteredOutcomes(round) {
  if (!round?.outcomes?.length) return [];
  const questions = questionMap();
  return round.outcomes.filter((item) => {
    const q = questions.get(item.q);
    if (!q) return false;
    if (state.type && q.type !== state.type) return false;
    if (state.level && q.level !== state.level) return false;
    return true;
  });
}

function scoreForRound(round) {
  if (!state.type && !state.level) return round.score;
  const rows = filteredOutcomes(round);
  if (!rows.length) return null;
  return rows.reduce((sum, item) => sum + item.score, 0) / rows.length;
}

function initToggles() {
  const wrap = $("#experiment-toggles");
  wrap.innerHTML = "";
  state.data.experiments.forEach((exp) => {
    state.visible.add(exp.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `toggle ${exp.id === state.activeExperiment ? "active" : ""}`;
    button.style.setProperty("--toggle-color", exp.color);
    button.textContent = exp.short;
    button.title = `${exp.label}：${exp.description}`;
    button.dataset.id = exp.id;
    button.addEventListener("click", (event) => {
      const id = event.currentTarget.dataset.id;
      if (event.shiftKey) {
        state.visible.has(id) ? state.visible.delete(id) : state.visible.add(id);
        if (!state.visible.size) state.visible.add(id);
      } else {
        state.activeExperiment = id;
        state.visible.add(id);
        const target = experiment(id);
        state.activeRound = target.summary.best_round || target.rounds[0].round;
        state.operator = null;
      }
      renderAll();
    });
    wrap.appendChild(button);
  });
}

function updateToggleClasses() {
  document.querySelectorAll(".toggle").forEach((button) => {
    button.classList.toggle("off", !state.visible.has(button.dataset.id));
    button.classList.toggle("active", button.dataset.id === state.activeExperiment);
  });
}

function renderKpis() {
  const exp = experiment();
  const round = activeRound();
  const selectedScore = scoreForRound(round);
  const values = exp.rounds.map(scoreForRound).filter((value) => value != null);
  const best = values.length ? Math.max(...values) : null;
  const rows = filteredOutcomes(round);
  $("#kpi-selection").textContent = `${exp.short} · R${round.round}`;
  $("#kpi-stage").textContent = exp.stage + "阶段";
  $("#kpi-score").textContent = pct(selectedScore);
  $("#kpi-score-note").textContent = round.status === "evaluated" ? "完成119题评测" : "该轮未留下逐题评测";
  $("#kpi-best").textContent = pct(best);
  $("#kpi-best-note").textContent = (state.type || state.level) ? "当前切片内最佳" : `原始最佳：R${exp.summary.best_round}`;
  $("#kpi-scope").textContent = rows.length ? `${rows.length} 道` : "—";
  $("#kpi-scope-note").textContent = state.type || "全部题目（均为 Level 5）";
  $("#filter-readout").textContent = `当前切片：${state.type || "全部题型"}${state.operator ? ` · 含 ${state.operator}` : ""}`;
}

function renderLineChart() {
  const host = $("#line-chart");
  host.innerHTML = "";
  const width = 1200, height = 420;
  const margin = {top: 30, right: 40, bottom: 50, left: 58};
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const maxRound = Math.max(...state.data.experiments.flatMap((exp) => exp.rounds.map((r) => r.round)));
  const x = (round) => margin.left + ((round - 1) / Math.max(1, maxRound - 1)) * innerW;
  const y = (score) => margin.top + (1 - score) * innerH;
  const svg = svgEl("svg", {viewBox: `0 0 ${width} ${height}`, role: "img"});

  [0, .2, .4, .6, .8, 1].forEach((tick) => {
    svg.appendChild(svgEl("line", {x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick), class: "grid-line"}));
    const label = svgEl("text", {x: margin.left - 12, y: y(tick) + 4, "text-anchor": "end", class: "axis-label"});
    label.textContent = `${tick * 100}%`;
    svg.appendChild(label);
  });
  for (let r = 1; r <= maxRound; r++) {
    const label = svgEl("text", {x: x(r), y: height - 20, "text-anchor": "middle", class: "axis-label"});
    label.textContent = `R${r}`;
    svg.appendChild(label);
  }
  const bestGuide = .840336;
  svg.appendChild(svgEl("line", {x1: margin.left, x2: width - margin.right, y1: y(bestGuide), y2: y(bestGuide), class: "baseline-line"}));
  const guideText = svgEl("text", {x: width - margin.right, y: y(bestGuide) - 7, "text-anchor": "end", class: "axis-label"});
  guideText.textContent = "固定算子全局最佳 84.0%";
  svg.appendChild(guideText);

  state.data.experiments.filter((exp) => state.visible.has(exp.id)).forEach((exp) => {
    const points = exp.rounds.map((round) => ({round, value: scoreForRound(round)})).filter((item) => item.value != null);
    if (!points.length) return;
    const containsOperator = (round) => !state.operator || round.operators.includes(state.operator);
    const segments = [];
    let current = [];
    points.forEach((item) => {
      if (item.value == null) { if (current.length) segments.push(current); current = []; }
      else current.push(item);
    });
    if (current.length) segments.push(current);
    segments.forEach((segment) => {
      const d = segment.map((item, index) => `${index ? "L" : "M"}${x(item.round.round)},${y(item.value)}`).join(" ");
      const path = svgEl("path", {d, stroke: exp.color, class: `series-line ${exp.id === state.activeExperiment ? "active" : ""}`});
      if (state.operator && !segment.some((item) => containsOperator(item.round))) path.classList.add("dimmed");
      svg.appendChild(path);
    });
    points.forEach(({round, value}) => {
      const isActive = exp.id === state.activeExperiment && round.round === state.activeRound;
      const hasEvolution = round.events.some((event) => event.kind === "evolution");
      if (hasEvolution) {
        svg.appendChild(svgEl("circle", {cx: x(round.round), cy: y(value), r: 10, stroke: "#f6903d", class: "event-ring"}));
      }
      const shape = (round.status === "rejected" || round.score === 0) ? "rect" : "circle";
      const attrs = shape === "rect"
        ? {x: x(round.round) - 4.5, y: y(value) - 4.5, width: 9, height: 9, transform: `rotate(45 ${x(round.round)} ${y(value)})`}
        : {cx: x(round.round), cy: y(value), r: isActive ? 7 : 4.5};
      const point = svgEl(shape, {...attrs, fill: exp.color, stroke: isActive ? "#17223b" : "#fff", "stroke-width": isActive ? 3 : 1.5, class: "point"});
      if (!containsOperator(round)) point.classList.add("dimmed");
      const title = svgEl("title");
      title.textContent = `${exp.short} R${round.round}: ${pct(value)}${round.events.length ? ` · ${round.events.map((e) => e.label).join(" / ")}` : ""}`;
      point.appendChild(title);
      point.addEventListener("click", () => {
        state.activeExperiment = exp.id;
        state.activeRound = round.round;
        renderAll();
      });
      svg.appendChild(point);
    });
  });
  host.appendChild(svg);
}

function heatColor(value) {
  if (value == null) return "#eeeae2";
  if (value < .55) return "#efc1b7";
  if (value < .7) return "#f5d6ac";
  if (value < .82) return "#e8e3ac";
  if (value < .9) return "#a9d9b9";
  return "#62c59d";
}

function renderHeatmap() {
  const host = $("#heatmap");
  host.innerHTML = "";
  const exp = experiment();
  const questions = questionMap();
  const types = state.data.meta.types;
  const rounds = exp.rounds;
  host.style.gridTemplateColumns = `110px repeat(${rounds.length}, minmax(28px, 1fr))`;
  const corner = document.createElement("div");
  corner.className = "heat-label head";
  corner.textContent = "题型 / 轮次";
  host.appendChild(corner);
  rounds.forEach((round) => {
    const label = document.createElement("div");
    label.className = "heat-label head";
    label.textContent = `R${round.round}`;
    host.appendChild(label);
  });
  types.forEach((type) => {
    const label = document.createElement("div");
    label.className = "heat-label";
    label.textContent = type;
    host.appendChild(label);
    rounds.forEach((round) => {
      const rows = round.outcomes.filter((item) => {
        const q = questions.get(item.q);
        return q && q.type === type;
      });
      const value = rows.length ? rows.reduce((sum, item) => sum + item.score, 0) / rows.length : null;
      const button = document.createElement("button");
      button.type = "button";
      button.className = `heat-cell ${value == null ? "empty" : ""}`;
      if (state.type === type && state.activeRound === round.round) button.classList.add("selected");
      button.style.background = heatColor(value);
      button.innerHTML = value == null ? (round.status === "rejected" ? "×" : "—") : `${Math.round(value * 100)}<small>n=${rows.length}</small>`;
      button.title = value == null ? `R${round.round} 没有逐题结果` : `${type} / R${round.round} / ${pct(value)} / ${rows.length}题`;
      if (value != null) button.addEventListener("click", () => {
        state.type = type;
        state.level = null;
        state.activeRound = round.round;
        renderAll();
      });
      host.appendChild(button);
    });
  });
  $("#matrix-caption").textContent = `${exp.short} 的逐轮题型准确率；数字为百分比，× 表示该轮被拒绝。`;
}

function renderWorkflow() {
  const round = activeRound();
  const host = $("#workflow");
  host.innerHTML = "";
  const sequence = round.sequence || [];
  const evolvedNames = new Set(experiment().evolution.map((item) => item.name));
  if (!sequence.length) {
    host.innerHTML = `<div class="empty-state">该轮没有可解析的工作流调用序列</div>`;
  } else {
    sequence.forEach((name, index) => {
      if (index) { const arrow = document.createElement("span"); arrow.className = "arrow"; arrow.textContent = "→"; host.appendChild(arrow); }
      const button = document.createElement("button");
      button.type = "button";
      button.className = `operator ${evolvedNames.has(name) ? "evolved" : ""} ${state.operator === name ? "selected" : ""}`;
      button.textContent = name;
      button.addEventListener("click", () => { state.operator = state.operator === name ? null : name; renderAll(); });
      host.appendChild(button);
    });
  }
  $("#workflow-caption").textContent = round.graph_source ? `解析自 ${round.graph_source}` : "无 graph.py 数据";
  const status = $("#workflow-status");
  status.textContent = round.status === "evaluated" ? "已完成评测" : round.status === "rejected" ? "被拒绝" : "仅有汇总";
  status.classList.toggle("rejected", round.status !== "evaluated");
  const eventHost = $("#event-list");
  eventHost.innerHTML = round.events.length
    ? round.events.map((event) => `<div class="event-item">${event.kind === "evolution" ? "🧬" : "⚠"} ${escapeHtml(event.label)}</div>`).join("")
    : `<div class="event-item">本轮无记录的进化或拒绝事件</div>`;
}

function renderCases() {
  const round = activeRound();
  const qmap = questionMap();
  const rows = filteredOutcomes(round)
    .map((item) => ({...item, question: qmap.get(item.q)}))
    .filter((item) => item.question)
    .sort((a, b) => a.score - b.score || a.question.index - b.question.index);
  const correct = rows.filter((item) => item.score > 0).length;
  $("#case-summary").textContent = rows.length ? `${correct} 正确 / ${rows.length - correct} 错误 / ${rows.length} 道` : "该轮没有逐题结果";
  const body = $("#case-table");
  body.innerHTML = "";
  rows.slice(0, 24).forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${item.question.id}</td><td>${escapeHtml(item.question.type)}</td><td>L${item.question.level}</td><td><span class="result ${item.score > 0 ? "ok" : "bad"}">${item.score > 0 ? "✓" : "×"}</span></td><td class="question">${escapeHtml(item.question.problem)}</td>`;
    tr.addEventListener("click", () => openProblem(item.question.id));
    body.appendChild(tr);
  });
  if (!rows.length) body.innerHTML = `<tr><td colspan="5" class="empty-state">当前轮次或切片没有逐题数据</td></tr>`;
}

function openProblem(qid) {
  const q = questionMap().get(qid);
  const round = activeRound();
  const outcome = round.outcomes.find((item) => item.q === qid);
  $("#dialog-meta").textContent = `${q.id} · ${q.type} · Level ${q.level} · ${experiment().short} R${round.round}`;
  $("#dialog-title").textContent = q.problem;
  $("#dialog-prediction").textContent = outcome?.prediction || "该轮没有预测记录";
  $("#dialog-solution").textContent = q.solution;
  const trajectory = experiment().rounds.flatMap((item) => {
    const row = item.outcomes.find((result) => result.q === qid);
    return row ? [{round: item.round, score: row.score}] : [];
  });
  $("#dialog-trajectory").innerHTML = trajectory.map((item) => `<span class="trajectory-row"><i class="${item.score > 0 ? "ok" : "bad"}"></i>R${item.round}</span>`).join("");
  $("#problem-dialog").showModal();
}

function renderAll() {
  const exp = experiment();
  if (!exp.rounds.some((round) => round.round === state.activeRound)) state.activeRound = exp.rounds[0].round;
  updateToggleClasses();
  renderKpis();
  renderLineChart();
  renderHeatmap();
  renderWorkflow();
  renderCases();
}

function resetFilters() {
  state.type = null;
  state.level = null;
  state.operator = null;
  state.visible = new Set(state.data.experiments.map((item) => item.id));
  renderAll();
}

async function init() {
  try {
    const response = await fetch("data/dashboard.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    initToggles();
    $("#reset-filter").addEventListener("click", resetFilters);
    $("#clear-slice").addEventListener("click", () => { state.type = null; state.level = null; renderAll(); });
    $("#dialog-close").addEventListener("click", () => $("#problem-dialog").close());
    $("#problem-dialog").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
    renderAll();
  } catch (error) {
    document.body.innerHTML = `<main style="padding:40px"><h1>数据加载失败</h1><p>请在本目录运行 <code>python3 -m http.server 8000</code> 后访问 <code>http://localhost:8000</code>。</p><pre>${escapeHtml(error.message)}</pre></main>`;
  }
}

init();
