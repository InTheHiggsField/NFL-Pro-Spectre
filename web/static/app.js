// Search-as-you-type in the top bar, and click-to-sort on tables with class "sortable".
(() => {
  const box = document.getElementById("search-box");
  const list = document.getElementById("search-suggest");
  if (box && list) {
    let timer = null, items = [], active = -1;
    const close = () => { list.hidden = true; active = -1; };
    const paint = () => {
      list.innerHTML = "";
      items.forEach((it, i) => {
        const a = document.createElement("a");
        a.href = it.url;
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
          const r = await fetch("/api/search?q=" + encodeURIComponent(q));
          items = await r.json(); active = -1; paint();
        } catch (e) { items = []; paint(); }
      }, 150);
    });
    box.addEventListener("keydown", (e) => {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); paint(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); paint(); e.preventDefault(); }
      else if (e.key === "Enter" && active >= 0) { location.href = items[active].url; e.preventDefault(); }
      else if (e.key === "Escape") close();
    });
    document.addEventListener("click", (e) => { if (!list.contains(e.target) && e.target !== box) close(); });
  }

  document.querySelectorAll("table.sortable").forEach((table) => {
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
  });
})();
