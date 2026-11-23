// Static (GitHub Pages) versions of the pages that filter or search.
// The live site does this in SQL (web/queries.py); here the same filters run in the
// browser on JSON files written by build_site.py. Only loaded when SITE.static is true.
(() => {
  const SITE = window.SITE || {};
  const root = document.querySelector("[data-static-page]");
  if (!SITE.static || !root) return;
  const cfgEl = document.getElementById("page-config");
  const cfg = cfgEl ? JSON.parse(cfgEl.textContent) : {};
  const params = new URLSearchParams(location.search);
  const $ = (sel) => document.querySelector(sel);

  // ---- formatting, same rules as the Jinja filters in web/app.py
  const fixed = (v, d) => Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const num = (v, d = 0) => {
    if (v === null || v === undefined || v === "") return "–";
    const f = Number(v);
    if (isNaN(f)) return v;
    if (d === 3 && Math.abs(f) <= 1) return `${(f * 100).toFixed(1)}%`;
    return fixed(f, d);
  };
  const money = (v) => {
    if (v === null || v === undefined) return "–";
    if (v === 0) return "$0";
    return Math.abs(v) >= 1 ? `$${fixed(v, 1)}M` : `$${fixed(v * 1000, 0)}K`;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const a = (url, text, cls = "") => `<a${cls ? ` class="${cls}"` : ""} href="${esc(siteHref(url))}">${esc(text)}</a>`;
  const desc = (key) => (x, y) => {           // descending, nulls last (like SQL DESC NULLS LAST)
    const u = key(x), v = key(y);
    if (u === null || u === undefined) return v === null || v === undefined ? 0 : 1;
    if (v === null || v === undefined) return -1;
    return v - u;
  };

  const load = async (path) => {
    const r = await fetch(SITE.base + path);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    const j = await r.json();
    return j.rows.map((row) => Object.fromEntries(j.cols.map((c, i) => [c, row[i]])));
  };

  // Put the URL's filters back into the form so it shows what is on screen.
  const fillForm = () => {
    const form = root.querySelector("form.filters");
    if (!form) return;
    for (const [k, v] of params) if (form.elements[k]) form.elements[k].value = v;
  };
  const sortableTable = () => root.querySelectorAll("table.sortable").forEach((t) => window.makeSortable && makeSortable(t));

  // ---------------------------------------------------------------- pages
  const pages = {};

  pages.players = async () => {
    fillForm();
    const season = parseInt(params.get("season")) || cfg.season;
    const position = params.get("position") || "";
    const team = params.get("team") || "";
    let sort = params.get("sort") || "fantasy_points_ppr";
    if (!(sort in cfg.sortable)) sort = "fantasy_points_ppr";
    document.title = `Players ${season} · NFL Pro Spectre`;
    let rows = await load(`data/players/${season}.json`);
    if (position) rows = rows.filter((r) => (["OL", "DL", "LB", "DB"].includes(position) ? r.position_group : r.position) === position);
    if (team) rows = rows.filter((r) => r.recent_team === team);
    rows = rows.sort(desc((r) => r[sort])).slice(0, 100);
    const cols = cfg.columns[position] || cfg.columns[""];
    const head = `<tr><th>#</th><th>Player</th><th>Pos</th><th>Team</th><th class="n">Age</th><th class="n">G</th>`
      + cols.map(([, label]) => `<th class="n">${esc(label)}</th>`).join("") + `<th class="n">Cap hit</th></tr>`;
    const body = rows.map((r, i) => `<tr><td class="muted">${i + 1}</td><td>${a(`/players/${r.player_id}`, r.player_display_name)}</td>`
      + `<td>${esc(r.position)}</td><td>${a(`/teams/${r.recent_team}?season=${season}`, r.recent_team)}</td>`
      + `<td class="n">${num(r.age, 0)}</td><td class="n">${num(r.games)}</td>`
      + cols.map(([c, , d]) => `<td class="n">${num(r[c], d)}</td>`).join("")
      + `<td class="n">${money(r.cap_hit_musd)}</td></tr>`).join("")
      || `<tr><td colspan="20" class="muted">No players match those filters.</td></tr>`;
    $("#static-table").innerHTML = `<table class="data sortable"><thead>${head}</thead><tbody>${body}</tbody></table>`;
    sortableTable();
  };

  pages.value = async () => {
    fillForm();
    const season = parseInt(params.get("season")) || cfg.season;
    const pos = params.get("pos") || "";
    const maxCost = parseFloat(params.get("max_cost"));
    const sort = params.get("sort") || "surplus";
    let rows = (await load(`data/value/${season}.json`))
      .filter((r) => r.cap_pct !== null && r.true_value_pct >= 0.005);
    if (pos) rows = rows.filter((r) => r.pos_group === pos);
    if (!isNaN(maxCost)) rows = rows.filter((r) => r.cap_hit_today_musd !== null && r.cap_hit_today_musd <= maxCost);
    const key = { value: (r) => r.true_value_pct, cheapest: (r) => -r.cap_pct }[sort] || ((r) => r.surplus_today_musd);
    rows = rows.sort(desc(key)).slice(0, 100);
    $("#static-rows").innerHTML = rows.map((r, i) => `<tr><td class="muted">${i + 1}</td><td>${a(`/players/${r.gsis_id}`, r.display_name)}</td>`
      + `<td>${esc(r.pos_group)}</td><td>${esc(r.team)}</td><td class="n">${num(r.age, 0)}</td><td class="n">${num(r.games)}</td>`
      + `<td class="n">${money(r.true_value_today_musd)}</td><td class="n">${money(r.cap_hit_today_musd)}</td>`
      + `<td class="n ${r.surplus_today_musd > 0 ? "pos" : "neg"}">${money(r.surplus_today_musd)}</td>`
      + `<td class="n" title="Percentile of true value among ${esc(r.pos_group)}s that season">${num(r.value_pctile)}</td>`
      + `<td>${a(`/value/alternatives/${r.gsis_id}?season=${season}`, "Similar for less", "small")}</td></tr>`).join("")
      || `<tr><td colspan="11" class="muted">No players match.</td></tr>`;
  };

  pages.projections = async () => {
    fillForm();
    const bases = cfg.bases;
    let base = parseInt(params.get("base"));
    if (!bases.includes(base)) base = bases[0];
    const pos = params.get("pos") || "", outlook = params.get("outlook") || "", sort = params.get("sort") || "projected";
    const cap = cfg.latest_cap;
    document.title = `${base + 1} projections · NFL Pro Spectre`;
    $("#static-heading").textContent = `${base + 1} projections`;
    $("#static-intro").innerHTML = `Projected true value for ${base + 1}, from each player's ${base}`
      + `${base === bases[0] && bases.length > 1 ? " pace so far" : ""} and ${base - 1} seasons.
      <b>Strong</b> means projected at least 15% above his ${base} value, <b>Weak</b> at least 15% below. ${a("/models", "How accurate is this?")}`;
    let rows = await load(`data/projections/${base}.json`);
    if (pos) rows = rows.filter((r) => r.pos_group === pos);
    if (outlook) rows = rows.filter((r) => r.outlook === outlook);
    const change = (r) => r.projected_value_pct - r.base_value_pct;
    const key = { rise: change, fall: (r) => -change(r) }[sort] || ((r) => r.projected_value_pct);
    rows = rows.sort(desc(key)).slice(0, 100);
    const soFar = rows.length && rows[0].actual_value_pct !== null;
    const head = `<tr><th>#</th><th>Player</th><th>Pos</th><th>Team</th><th class="n">Age</th>
      <th class="n">${base} value</th><th class="n">${base + 1} projected</th><th class="n">Change</th><th>Outlook</th>`
      + (soFar ? `<th class="n">${base + 1} so far</th>` : "") + "</tr>";
    const pct = (r) => { const x = (r.projected_value_pct / Math.max(r.base_value_pct, 0.002) - 1) * 100; return (x >= 0 ? "+" : "") + x.toFixed(0); };
    const body = rows.map((r, i) => `<tr><td class="muted">${i + 1}</td><td>${a(`/players/${r.gsis_id}`, r.display_name)}</td>`
      + `<td>${esc(r.pos_group)}</td><td>${esc(r.team)}</td><td class="n">${num(r.age, 0)}</td>`
      + `<td class="n">${money(r.base_value_pct * cap)}</td><td class="n"><b>${money(r.projected_value_pct * cap)}</b></td>`
      + `<td class="n ${r.projected_value_pct > r.base_value_pct ? "pos" : "neg"}">${pct(r)}%</td>`
      + `<td><span class="tag ${esc((r.outlook || "").toLowerCase())}">${esc(r.outlook)}</span></td>`
      + (soFar ? `<td class="n">${r.actual_value_pct !== null ? money(r.actual_value_pct * cap) : "–"}</td>` : "") + "</tr>").join("")
      || `<tr><td colspan="10" class="muted">No players match.</td></tr>`;
    $("#static-table").innerHTML = `<table class="data sortable"><thead>${head}</thead><tbody>${body}</tbody></table>`;
    sortableTable();
  };

  pages.alternatives = async () => {
    const id = params.get("id");
    const season = parseInt(params.get("season")) || cfg.season;
    const all = await load(`data/value/${season}.json`);
    const t = all.find((r) => r.gsis_id === id);
    if (!t) { $("#static-content").innerHTML = `<h1>Not found</h1><p class="muted">No value score for that player in ${season}.</p>`; return; }
    const pn = cfg.pos_names[t.pos_group] || t.pos_group;
    const limitCap = t.cap_pct !== null ? t.cap_pct : 1.0;
    const alts = all.filter((r) => r.pos_group === t.pos_group && r.gsis_id !== t.gsis_id
      && r.true_value_pct >= t.true_value_pct * 0.8 && r.cap_pct !== null && r.cap_pct < limitCap)
      .sort(desc((r) => r.true_value_pct - r.cap_pct)).slice(0, 25);
    document.title = `Cheaper than ${t.display_name} · NFL Pro Spectre`;
    const last = t.display_name.split(" ").pop();
    const opts = cfg.seasons.map((s) => `<option value="${s}" ${s === season ? "selected" : ""}>${s}</option>`).join("");
    const body = alts.map((r) => `<tr><td>${a(`/players/${r.gsis_id}`, r.display_name)}</td><td>${esc(r.team)}</td><td class="n">${num(r.age, 0)}</td>`
      + `<td class="n">${money(r.true_value_today_musd)}</td><td class="n">${Math.round(r.true_value_pct / t.true_value_pct * 100)}%</td>`
      + `<td class="n">${money(r.cap_hit_today_musd)}</td>`
      + `<td class="n pos">${t.cap_hit_today_musd !== null ? money(t.cap_hit_today_musd - r.cap_hit_today_musd) : ""}</td></tr>`).join("")
      || `<tr><td colspan="7" class="muted">Nobody at this position matched that value for less in ${season}.</td></tr>`;
    $("#static-content").innerHTML = `<h1>Can't afford ${esc(t.display_name)}?</h1>
      <p>In ${season}, ${a(`/players/${t.gsis_id}`, t.display_name)} (${esc(pn)}) produced
      <b>${money(t.true_value_today_musd)}</b> of value and cost <b>${money(t.cap_hit_today_musd)}</b> (today's dollars).
      These ${esc(pn.toLowerCase())}s gave at least 80% of that value for less.</p>
      <form class="filters"><label>Season <select id="alt-season">${opts}</select></label></form>
      <table class="data sortable"><thead><tr><th>Player</th><th>Team</th><th class="n">Age</th><th class="n">True value</th>
      <th class="n">vs ${esc(last)}</th><th class="n">Cap hit</th><th class="n">Saves</th></tr></thead><tbody>${body}</tbody></table>`;
    $("#alt-season").addEventListener("change", (e) => { location.href = siteHref(`/value/alternatives/${id}?season=${e.target.value}`); });
    sortableTable();
  };

  pages.search = async () => {
    const q = (params.get("q") || "").trim();
    const box = $("#search-box");
    if (box) box.value = q;
    if (!q) { location.replace(siteHref("/players")); return; }
    const ix = await staticSearch.load();
    if (/^\d{4}$/.test(q) && ix.seasons.includes(parseInt(q))) { location.replace(siteHref(`/seasons/${q}`)); return; }
    const players = q.length >= 2 ? staticSearch.players(ix, q, 50) : [];
    const teams = staticSearch.teams(ix, q);
    if (teams.length === 1 && !players.length) { location.replace(siteHref(`/teams/${teams[0].team_abbr}`)); return; }
    document.title = `Search: ${q} · NFL Pro Spectre`;
    let html = `<h1>Results for “${esc(q)}”</h1>`;
    if (teams.length) html += `<h2>Teams</h2><div class="logos">` + teams.map((t) => `<a href="${esc(siteHref(`/teams/${t.team_abbr}`))}"><img src="${esc(t.team_logo_espn)}" alt=""> ${esc(t.team_name)}</a>`).join("") + `</div>`;
    html += `<h2>Players</h2>`;
    html += players.length
      ? `<table class="data"><thead><tr><th>Name</th><th>Pos</th><th>Latest team</th><th>Years</th><th>Status</th></tr></thead><tbody>`
        + players.map((p) => `<tr><td>${a(`/players/${p.gsis_id}`, p.display_name)}</td><td>${esc(p.position)}</td>`
          + `<td>${p.latest_team ? a(`/teams/${p.latest_team}`, p.latest_team) : ""}</td>`
          + `<td>${p.rookie_season ?? "?"}–${p.last_season ?? "?"}</td><td>${esc(p.status)}</td></tr>`).join("") + `</tbody></table>`
      : `<p class="muted">No players match. Try a last name, like “Kelce”.</p>`;
    $("#static-content").innerHTML = html;
  };

  const run = pages[root.dataset.staticPage];
  if (run) run().catch((e) => {
    const target = $("#static-content") || $("#static-table") || $("#static-rows");
    if (target) target.innerHTML = `<p class="muted">Could not load the data for this page (${esc(e.message)}).</p>`;
  });
})();
