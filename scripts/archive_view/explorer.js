/* Read-only archival navigation; aggregation never changes the source graph. */
(async () => {
  "use strict";
  const $ = (id) => document.getElementById(id),
    el = (tag, text) => {
      const n = document.createElement(tag);
      if (text !== undefined) n.textContent = String(text);
      return n;
    };
  const btn = (text, action, cls) => {
    const b = el("button", text);
    if (cls) b.className = cls;
    b.addEventListener("click", action);
    return b;
  };
  try {
    if (!("DecompressionStream" in window))
      throw new Error(
        "此浏览器不支持离线 gzip 解压，请使用当前版本的 Chromium 或 Firefox。",
      );
    const bytes = Uint8Array.from(atob($("archive").textContent.trim()), (c) =>
      c.charCodeAt(0),
    );
    const graph = JSON.parse(
      await new Response(
        new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip")),
      ).text(),
    );
    const nodes = graph.nodes,
      edges = graph.edges,
      groups = graph.groups,
      index = new Map(nodes.map((n, i) => [n.id, i])),
      groupIndex = new Map(groups.map((g, i) => [g.id, i]));
    const adjacency = Array.from({ length: nodes.length }, () => []),
      members = groups.map(() => []),
      groupOf = graph.display_groups.map((g) => groupIndex.get(g));
    const source = new Uint32Array(edges.length),
      target = new Uint32Array(edges.length);
    nodes.forEach((n, i) => members[groupOf[i]].push(i));
    for (let i = 0; i < edges.length; i++) {
      source[i] = index.get(edges[i].source);
      target[i] = index.get(edges[i].target);
      adjacency[source[i]].push(i);
      if (target[i] !== source[i]) adjacency[target[i]].push(i);
    }
    const groupConnections = new Map();
    for (let i = 0; i < edges.length; i++) {
      const a = groupOf[source[i]],
        b = groupOf[target[i]];
      if (a === b) continue;
      const key = Math.min(a, b) + ":" + Math.max(a, b);
      groupConnections.set(key, (groupConnections.get(key) || 0) + 1);
    }
    const primary = (i) => nodes[i].primary === true;
    const rank = (i) =>
      Number.isFinite(nodes[i].priority)
        ? nodes[i].priority
        : primary(i)
          ? 10
          : 0;
    const kindName = (n) =>
      ({
        experiment: "实验",
        research: "科研问题",
        line: "研究主线",
        measurement: "末评观测",
        finding: "批次结论",
        attempt: "来源版本",
        run: "原执行",
        receipt: "原回执",
        project: "项目",
        source: "来源范围",
        evidence: "原始证据",
        snapshot: "历史快照",
        hyperedge: "原超边",
        original_node: "原始记录",
        history_node: "历史节点",
        version: "阶段版本",
        knowledge: "历史结论",
      })[n.type] || String(n.type || "记录");
    const fmt = (n) => n.toLocaleString();
    const nodeName = (i) => String(nodes[i].label || nodes[i].id),
      groupName = (i) => String(groups[i].label || groups[i].id);
    $("total").textContent =
      `${fmt(nodes.length)} 节点 · ${fmt(edges.length)} 条边 · 同一数据集`;
    const canvas = $("canvas"),
      ctx = canvas.getContext("2d"),
      plot = $("plot");
    let width = 1,
      height = 1,
      ratio = 1,
      scale = 1,
      panX = 0,
      panY = 0;
    let mode = "overview",
      group = null,
      selected = null,
      page = 0,
      showRecords = false,
      drawNodes = [],
      drawEdges = [],
      points = new Map(),
      pending = false,
      searchTimer = null,
      searchPage = 0;
    const colors = [
      "#e1bb87",
      "#89accb",
      "#9ac2b1",
      "#b6a3d2",
      "#c8a19c",
      "#93b9cd",
    ];
    const viewCapacity = () =>
      Math.max(1, Math.floor(width / 175)) *
      Math.max(1, Math.floor(height / 108));
    function pager(total, size, action) {
      $("pager").replaceChildren();
      const pages = Math.max(1, Math.ceil(total / size));
      page = Math.max(0, Math.min(page, pages - 1));
      if (pages <= 1) return;
      const a = btn("← 上一页", () => {
          page--;
          action();
        }),
        b = btn("下一页 →", () => {
          page++;
          action();
        });
      a.disabled = page === 0;
      b.disabled = page >= pages - 1;
      $("pager").append(
        a,
        el("span", `${page + 1} / ${pages} · 共 ${fmt(total)} 项`),
        b,
      );
    }
    function breadcrumb() {
      const c = $("crumb");
      c.replaceChildren(btn("全部研究", home));
      if (group !== null) {
        c.append(
          el("span", "/"),
          btn(groupName(group), () => openGroup(group)),
        );
      }
      if (selected !== null) c.append(el("span", "/ " + nodeName(selected)));
    }
    function regularPositions(ids) {
      const count = ids.length,
        cols = Math.max(1, Math.min(count, Math.floor(width / 175))),
        rows = Math.ceil(count / cols),
        spaceX = width / cols,
        spaceY = height / Math.max(1, rows);
      points = new Map();
      ids.forEach((id, j) =>
        points.set(id, {
          x: spaceX * ((j % cols) + 0.5) + (j % 2 === 0 ? -5 : 5),
          y: spaceY * (Math.floor(j / cols) + 0.47),
        }),
      );
      scale = 1;
      panX = panY = 0;
    }
    function boundsFit() {
      if (!points.size) return;
      let l = Infinity,
        r = -Infinity,
        t = Infinity,
        b = -Infinity;
      for (const p of points.values()) {
        l = Math.min(l, p.x);
        r = Math.max(r, p.x);
        t = Math.min(t, p.y);
        b = Math.max(b, p.y);
      }
      scale = Math.min(
        1,
        Math.min((width - 70) / (r - l + 100), (height - 70) / (b - t + 100)),
      );
      panX = width / 2 - ((l + r) / 2) * scale;
      panY = height / 2 - ((t + b) / 2) * scale;
    }
    function overview() {
      mode = "overview";
      group = selected = null;
      showRecords = false;
      breadcrumb();
      $("title").textContent = graph.title || "研究星图";
      $("summary").textContent =
        graph.summary ||
        "点击研究簇进入二级图谱。分组用于浏览，不代表科学支持或因果关系。";
      $("detail").replaceChildren(
        el("p", "选择一个研究簇，查看其中的实验、结论、失败与证据。"),
      );
      const size = viewCapacity();
      pager(groups.length, size, overview);
      drawNodes = groups.map((_, i) => i).slice(page * size, (page + 1) * size);
      drawEdges = [];
      regularPositions(drawNodes);
      $("view-status").textContent =
        `一级星图 · ${drawNodes.length} / ${groups.length} 个研究簇 · 点击进入二级 · 细节按需展开，原始数据完整保留`;
      labels();
      changed();
    }
    function home() {
      page = 0;
      overview();
    }
    function openGroup(g) {
      group = g;
      selected = null;
      page = 0;
      showRecords = false;
      groupView();
    }
    function groupView() {
      mode = "group";
      selected = null;
      breadcrumb();
      $("title").textContent = groupName(group);
      $("summary").textContent =
        groups[group].summary || "选择实验或关键记录，进入其直接关系与证据。";
      const all = members[group],
        core = all.filter(primary),
        choice = showRecords || !core.length ? all : core,
        size = viewCapacity();
      pager(choice.length, size, groupView);
      drawNodes = choice.slice(page * size, (page + 1) * size);
      const shown = new Set(drawNodes);
      drawEdges = [];
      for (const i of drawNodes)
        for (const e of adjacency[i])
          if (source[e] === i && shown.has(target[e])) drawEdges.push(e);
      regularPositions(drawNodes);
      $("view-status").textContent =
        `二级图谱 · 当前 ${drawNodes.length} / ${choice.length} 项 · 本簇共 ${fmt(all.length)} 个节点 · 当前 ${fmt(drawEdges.length)} 条原关系`;
      const d = $("detail");
      d.replaceChildren(
        el("h2", groupName(group)),
        el("p", groups[group].summary || ""),
      );
      d.append(el("span", `${fmt(all.length)} 条记录`));
      d.append(
        btn(
          showRecords ? "仅看关键实验与结论" : "浏览本簇全部记录",
          () => {
            showRecords = !showRecords;
            page = 0;
            groupView();
          },
          "group-action",
        ),
      );
      d.append(
        el("h3", "阅读方式"),
        el(
          "p",
          "点击实验或结论，查看其原始状态和相邻节点。全部记录可通过上方搜索直接定位。来源归属和历史先后不自动成为科学支持。",
        ),
      );
      labels();
      changed();
    }
    function neighbors(i) {
      const ids = new Set();
      for (const e of adjacency[i]) {
        ids.add(source[e]);
        ids.add(target[e]);
      }
      ids.delete(i);
      return [...ids].sort((a, b) => rank(b) - rank(a) || a - b);
    }
    function openNode(i) {
      selected = i;
      group = groupOf[i];
      page = 0;
      nodeView();
    }
    function nodeView() {
      mode = "node";
      breadcrumb();
      const n = nodes[selected],
        near = neighbors(selected),
        size = Math.max(1, viewCapacity() - 1);
      pager(near.length, size, nodeView);
      drawNodes = [selected, ...near.slice(page * size, (page + 1) * size)];
      const shown = new Set(drawNodes);
      drawEdges = adjacency[selected].filter(
        (e) => shown.has(source[e]) && shown.has(target[e]),
      );
      regularPositions(drawNodes);
      $("title").textContent = nodeName(selected);
      $("summary").textContent =
        "直接关系 · 点击相邻节点继续追踪；连线均来自原始记录。";
      $("view-status").textContent =
        `当前节点 + ${drawNodes.length - 1} / ${fmt(near.length)} 个相邻节点 · 显示 ${fmt(drawEdges.length)} / ${fmt(adjacency[selected].length)} 条直接关系`;
      detail(selected);
      labels();
      changed();
    }
    function safeEvidence(path) {
      if (typeof path !== "string" || !path) return null;
      try {
        let decoded = decodeURIComponent(path);
        if (
          /^(?:[a-z][a-z0-9+.-]*:|[\\/])/i.test(decoded) ||
          decoded.split(/[\\/]/).includes("..")
        )
          return null;
        const url = new URL(path.replaceAll("\\", "/"), location.href),
          base = new URL(".", location.href);
        if (
          url.origin !== base.origin ||
          !url.pathname.startsWith(base.pathname) ||
          !["http:", "https:", "file:"].includes(url.protocol)
        )
          return null;
        return url.href;
      } catch {
        return null;
      }
    }
    function detail(i) {
      const n = nodes[i],
        d = $("detail");
      d.replaceChildren(el("h2", nodeName(i)));
      for (const text of [kindName(n), n.status])
        if (text) {
          const t = el("span", text);
          t.className = "tag";
          d.append(t);
        }
      d.append(
        el("p", n.summary || n.claim || "此记录未提供可读总结，原始状态保持。"),
      );
      const identity = el("p", n.id);
      identity.className = "meta";
      d.append(identity);
      const refs = [
        n.raw_ref,
        n.source,
        n.native_source,
        ...(Array.isArray(n.evidence) ? n.evidence : []),
      ].filter((x) => x && typeof x === "object");
      if (refs.length) d.append(el("h3", "原始证据"));
      for (const ref of refs) {
        const href = safeEvidence(ref.path || ref.file);
        if (href) {
          const a = el("a", "打开证据原件 ↗");
          a.href = href;
          a.target = "_blank";
          a.rel = "noopener";
          d.append(a);
        }
        if (ref.sha256) {
          const p = el("p", "SHA256 " + ref.sha256);
          p.className = "meta";
          d.append(p);
        }
      }
      d.append(el("h3", `${fmt(adjacency[i].length)} 条原始关系`));
      const orderedRelations = [...adjacency[i]].sort(
        (a, b) =>
          rank(source[b] === i ? target[b] : source[b]) -
            rank(source[a] === i ? target[a] : source[a]) || a - b,
      );
      let offset = 0;
      const more = btn("加载更多关系", loadRelations, "group-action");
      function loadRelations() {
        for (const eid of orderedRelations.slice(offset, offset + 30)) {
          const e = edges[eid],
            out = source[eid] === i,
            j = out ? target[eid] : source[eid];
          const b = btn(
            (out ? "→ " : "← ") + nodeName(j),
            () => openNode(j),
            "record",
          );
          b.append(el("small", e.label || e.type || e.semantic || "原始关系"));
          b.title = e.id;
          d.insertBefore(b, more);
        }
        offset += 30;
        more.hidden = offset >= adjacency[i].length;
        more.textContent = `加载更多 · ${Math.min(offset, adjacency[i].length)} / ${adjacency[i].length}`;
      }
      d.append(more);
      loadRelations();
      d.append(
        btn("返回所属研究簇", () => openGroup(groupOf[i]), "group-action"),
      );
    }
    function fullView() {
      mode = "all";
      group = selected = null;
      page = 0;
      breadcrumb();
      $("pager").replaceChildren();
      $("title").textContent = "完整关系总览";
      $("summary").textContent =
        "全量视图保留所有节点和边。返回一级星图可按研究簇阅读；滚轮缩放，拖动画布。";
      drawNodes = nodes.map((_, i) => i);
      drawEdges = edges.map((_, i) => i);
      points = new Map();
      const cols = Math.max(1, Math.ceil(Math.sqrt(groups.length * 1.5)));
      for (let g = 0; g < groups.length; g++) {
        const ids = members[g],
          radius = Math.max(100, Math.sqrt(ids.length) * 10);
        ids.forEach((i, j) => {
          const a = j * 2.39996323,
            r = radius * Math.sqrt((j + 0.5) / ids.length);
          points.set(i, {
            x: (g % cols) * 3200 + r * Math.cos(a),
            y: Math.floor(g / cols) * 3200 + r * Math.sin(a),
          });
        });
      }
      boundsFit();
      $("detail").replaceChildren(
        el(
          "p",
          "完整记录无固定数量上限。大图阅读推荐返回一级星图，按研究簇或搜索进入节点。",
        ),
      );
      $("view-status").textContent =
        `全量视图 · ${fmt(nodes.length)} / ${fmt(nodes.length)} 节点 · ${fmt(edges.length)} / ${fmt(edges.length)} 条边`;
      labels();
      changed();
    }
    function labels() {
      const layer = $("labels");
      layer.replaceChildren();
      if (mode === "all") return;
      for (const id of drawNodes) {
        const p = points.get(id),
          isGroup = mode === "overview",
          b = btn(
            "",
            () => (isGroup ? openGroup(id) : openNode(id)),
            "star-label " + (isGroup ? "group" : "node"),
          );
        const name = isGroup ? groupName(id) : nodeName(id);
        b.append(el("b", name));
        b.append(
          el(
            "small",
            isGroup
              ? `${fmt(members[id].filter(primary).length)} 个关键节点 · ${fmt(members[id].length)} 条记录`
              : kindName(nodes[id]) +
                  (adjacency[id].length
                    ? ` · ${fmt(adjacency[id].length)} 条关系`
                    : ""),
          ),
        );
        b.style.left = p.x + "px";
        b.style.top = p.y + 21 + "px";
        b.title = name;
        layer.append(b);
      }
    }
    function changed() {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        draw();
      });
    }
    function draw() {
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      canvas.dataset.mode = mode;
      canvas.dataset.totalNodes = String(nodes.length);
      canvas.dataset.totalEdges = String(edges.length);
      canvas.dataset.visibleNodes = String(drawNodes.length);
      canvas.dataset.visibleEdges = String(drawEdges.length);
      const point = (i) => {
        const p = points.get(i);
        return p ? { x: p.x * scale + panX, y: p.y * scale + panY } : null;
      };
      if (mode === "overview") {
        // These are aggregate counts of real cross-group edges, not new dependencies.
        ctx.strokeStyle = "#7392bd";
        ctx.lineWidth = 0.6;
        ctx.globalAlpha = 0.16;
        ctx.beginPath();
        for (const [key] of groupConnections) {
          const [a, b] = key.split(":").map(Number),
            p = point(a),
            q = point(b);
          if (p && q) {
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(q.x, q.y);
          }
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
        for (const id of drawNodes) {
          const p = point(id),
            color = colors[id % colors.length];
          const glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, 26);
          glow.addColorStop(0, color + "62");
          glow.addColorStop(1, color + "00");
          ctx.fillStyle = glow;
          ctx.fillRect(p.x - 27, p.y - 27, 54, 54);
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(p.x, p.y, 4.2, 0, Math.PI * 2);
          ctx.fill();
          for (let j = 0; j < 13; j++) {
            const a = j * 2.3999632,
              r = 11 + Math.sqrt(j) * 6;
            ctx.globalAlpha = 0.2 + (j % 3) * 0.16;
            ctx.beginPath();
            ctx.arc(
              p.x + Math.cos(a) * r,
              p.y + Math.sin(a) * r * 0.6,
              0.8,
              0,
              Math.PI * 2,
            );
            ctx.fill();
          }
        }
        ctx.globalAlpha = 1;
      } else {
        const full = mode === "all";
        ctx.strokeStyle = full ? "#80a49f" : "#728ba9";
        ctx.lineWidth = full ? 0.35 : 1;
        ctx.globalAlpha = full ? 0.13 : 0.5;
        ctx.beginPath();
        for (const eid of drawEdges) {
          const p = point(source[eid]),
            q = point(target[eid]);
          if (!p || !q) continue;
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
        const buckets = new Map();
        for (const i of drawNodes) {
          const color = colors[groupOf[i] % colors.length];
          let a = buckets.get(color);
          if (!a) buckets.set(color, (a = []));
          a.push(i);
        }
        for (const [color, ids] of buckets) {
          ctx.fillStyle = color;
          ctx.beginPath();
          for (const i of ids) {
            const p = point(i),
              r = full ? Math.max(0.6, 4 * scale) : i === selected ? 6 : 3.6;
            ctx.moveTo(p.x + r, p.y);
            ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
          }
          ctx.fill();
        }
      }
      canvas.dataset.renderComplete = "true";
    }
    function resize() {
      width = plot.clientWidth;
      height = plot.clientHeight;
      ratio = Math.min(2, devicePixelRatio || 1);
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      if (mode === "overview") overview();
      else if (mode === "group") groupView();
      else if (mode === "node") nodeView();
      else {
        boundsFit();
        changed();
      }
    }
    let drag = null;
    canvas.addEventListener("pointerdown", (e) => {
      if (mode === "all") {
        drag = { x: e.offsetX, y: e.offsetY, px: panX, py: panY };
        canvas.setPointerCapture(e.pointerId);
      }
    });
    canvas.addEventListener("pointermove", (e) => {
      if (drag) {
        panX = drag.px + e.offsetX - drag.x;
        panY = drag.py + e.offsetY - drag.y;
        changed();
      }
    });
    canvas.addEventListener("pointerup", () => (drag = null));
    canvas.addEventListener("pointercancel", () => (drag = null));
    canvas.addEventListener(
      "wheel",
      (e) => {
        if (mode !== "all") return;
        e.preventDefault();
        const old = scale;
        scale = Math.max(
          Number.EPSILON,
          Math.min(10, scale * Math.exp(-e.deltaY * 0.001)),
        );
        panX = e.offsetX - ((e.offsetX - panX) * scale) / old;
        panY = e.offsetY - ((e.offsetY - panY) * scale) / old;
        changed();
      },
      { passive: false },
    );
    function search() {
      const q = $("search").value.trim().toLocaleLowerCase(),
        out = $("search-results");
      out.replaceChildren();
      if (!q) {
        $("search-meta").textContent = "";
        return;
      }
      const matches = [];
      for (let i = 0; i < nodes.length; i++) {
        const n = nodes[i];
        if (
          [n.id, n.label, n.summary, n.claim, ...(n.experiment_ids || [])].some(
            (x) =>
              String(x || "")
                .toLocaleLowerCase()
                .includes(q),
          )
        )
          matches.push(i);
      }
      matches.sort(
        (a, b) =>
          Number(nodeName(b).toLocaleLowerCase() === q) -
          Number(nodeName(a).toLocaleLowerCase() === q),
      );
      const size = 30,
        pages = Math.max(1, Math.ceil(matches.length / size));
      searchPage = Math.min(searchPage, pages - 1);
      $("search-meta").textContent =
        `${fmt(matches.length)} 个匹配 · ${searchPage + 1} / ${pages} 页`;
      for (const i of matches.slice(
        searchPage * size,
        (searchPage + 1) * size,
      )) {
        const b = btn(nodeName(i), () => openNode(i), "record");
        b.append(
          el("small", groupName(groupOf[i]) + " · " + kindName(nodes[i])),
        );
        out.append(b);
      }
      if (pages > 1) {
        const a = btn("上一页", () => {
            searchPage--;
            search();
          }),
          b = btn("下一页", () => {
            searchPage++;
            search();
          });
        a.disabled = searchPage === 0;
        b.disabled = searchPage >= pages - 1;
        out.append(a, b);
      }
    }
    $("search").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchPage = 0;
      searchTimer = setTimeout(search, 140);
    });
    $("home").addEventListener("click", home);
    $("all").addEventListener("click", fullView);
    $("fit").addEventListener("click", () => {
      if (mode === "all") {
        boundsFit();
        changed();
      } else resize();
    });
    const observer = new ResizeObserver(resize);
    observer.observe(plot);
    window.addEventListener(
      "pagehide",
      () => {
        observer.disconnect();
        clearTimeout(searchTimer);
      },
      { once: true },
    );
    resize();
  } catch (e) {
    $("error").hidden = false;
    $("error").textContent = "图谱未完整载入：" + (e.message || String(e));
    $("total").textContent = "载入失败；未将部分数据当作完整图谱";
    console.error(e);
  }
})();
