// Search-as-you-type in the top bar, and click-to-sort on tables with class "sortable".
// The same file runs on the live site (python run_web.py) and on the static GitHub Pages
// build (python build_site.py). window.SITE, set in base.html, says which one this is.
(() => {
  const SITE = window.SITE || { base: "/", static: false };

  // Turn a live-site URL ("/teams/KC?season=2010") into the URL of the same page on
  // whichever site this is. Mirrors map_url() in web/static_site.py.
  window.siteHref = (url) => {
    if (!SITE.static || !url.startsWith("/") || url.startsWith("//")) return url;
    const [path, query = ""] = url.split("?");
    const q = new URLSearchParams(query);
    const parts = path.split("/").filter(Boolean);
    let out;
    if (parts[0] === "static") out = parts.join("/");
    else if (parts[0] === "teams" && parts.length === 2 && q.has("season")) {
      out = `teams/${parts[1].toUpperCase()}/${q.get("season")}/`;
      q.delete("season");
    } else if (parts[0] === "value" && parts[1] === "alternatives" && parts.length === 3) {
      const season = q.get("season");
      q.delete("season");
      q.set("id", parts[2]);
      if (season) q.set("season", season);
      out = "value/alternatives/";
    } else out = parts.length ? parts.join("/") + "/" : "";
    const qs = q.toString();
    return SITE.base + out + (qs ? "?" + qs : "");
  };

  // ---------------------------------------------------------------- static search index
  // On the static site there is no /api/search, so search runs on data/search.json.
  let index = null;
  const loadIndex = async () => {
    if (!index) {
      index = fetch(SITE.base + "data/search.json").then((r) => r.json()).then((j) => {
        j.players = j.rows.map((r) => Object.fromEntries(j.cols.map((c, i) => [c, r[i]])));
        j.players.forEach((p) => { p._a = (p.display_name || "").toLowerCase(); p._b = (p.full_name || p._a).toLowerCase(); });
        return j;
      });
    }
    return index;
  };
  const searchPlayers = (ix, q, limit) => {
    q = q.trim().toLowerCase();
    return ix.players.filter((p) => p._a.includes(q) || p._b.includes(q))
      .sort((x, y) => (y._a.startsWith(q) - x._a.startsWith(q))
        || ((y.last_season ?? -1) - (x.last_season ?? -1))
        || x.display_name.localeCompare(y.display_name))
      .slice(0, limit);
  };
  const searchTeams = (ix, q) => {
    q = q.trim().toLowerCase();
    return ix.teams.filter((t) => t.team_name.toLowerCase().includes(q)
      || (t.team_nick || "").toLowerCase().includes(q) || t.team_abbr.toLowerCase() === q);
  };
  window.staticSearch = { load: loadIndex, players: searchPlayers, teams: searchTeams };

  const suggestions = async (q) => {
    if (!SITE.static) return (await fetch("/api/search?q=" + encodeURIComponent(q))).json();
    const ix = await loadIndex();
    const out = searchTeams(ix, q).map((t) => ({ label: t.team_name, sub: "Team", url: `/teams/${t.team_abbr}` }));
    searchPlayers(ix, q, 10).forEach((p) => out.push({
      label: p.display_name,
      sub: [p.position, p.latest_team, `${p.rookie_season ?? "?"}–${p.last_season ?? "?"}`].filter(Boolean).join(" · "),
      url: `/players/${p.gsis_id}`,
    }));
    return out.slice(0, 12);
  };

  // ---------------------------------------------------------------- autocomplete
  const box = document.getElementById("search-box");
  const list = document.getElementById("search-suggest");
  if (box && list) {
    let timer = null, items = [], active = -1;
    const close = () => { list.hidden = true; active = -1; };
    const paint = () => {
      list.innerHTML = "";
      items.forEach((it, i) => {
        const a = document.createElement("a");
        a.href = siteHref(it.url);
        a.className = i === active ? "active" : "";
        a.textContent = it.label;
        const s = document.createElement("span");
        s.textContent = it.sub;
        a.appendChild(s);
        list.appendChild(a);
      });
      list.hidden = items.length === 0;
    };
    box.addEventListener("input", () => {
      clearTimeout(timer);
      const q = box.value.trim();
      if (q.length < 2) { items = []; paint(); return; }
      timer = setTimeout(async () => {
        try {
          items = await suggestions(q); active = -1; paint();
        } catch (e) { items = []; paint(); }
      }, 150);
    });
    box.addEventListener("keydown", (e) => {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); paint(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); paint(); e.preventDefault(); }
      else if (e.key === "Enter" && active >= 0) { location.href = siteHref(items[active].url); e.preventDefault(); }
      else if (e.key === "Escape") close();
    });
    document.addEventListener("click", (e) => { if (!list.contains(e.target) && e.target !== box) close(); });
  }

  // ---------------------------------------------------------------- sortable tables
  window.makeSortable = (table) => {
    table.querySelectorAll("thead th").forEach((th, col) => {
      let asc = false;
      th.addEventListener("click", () => {
        const body = table.tBodies[0];
        const val = (tr) => {
          const t = (tr.children[col]?.textContent || "").replace(/[$,%MK]/g, "").trim();
          const n = parseFloat(t);
          return isNaN(n) ? t.toLowerCase() : n;
        };
        const rows = [...body.rows].sort((a, b) => {
          const x = val(a), y = val(b);
          if (x === y) return 0;
          return (x > y ? 1 : -1) * (asc ? 1 : -1);
        });
        asc = !asc;
        rows.forEach((r) => body.appendChild(r));
      });
    });
  };
  document.querySelectorAll("table.sortable").forEach(window.makeSortable);
})();
