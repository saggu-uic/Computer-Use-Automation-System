// Rote in-page perception and resolution (one copy per frame).
// Record time and replay time use the SAME role/name/label/table rules, so a locator
// verified at record time means exactly the same thing when it is resolved at replay.
(() => {
  if (window.__rote && window.__rote.version === 1) return;

  const CONTROL_SEL =
    "a[href], button, input, select, textarea, [onclick], [role=button], [role=link], [role=textbox], [role=checkbox]";
  const HAS_CONTROL_SEL =
    "input:not([type=hidden]), select, textarea, button, a[href], [onclick], [role=button], [role=link]";

  const clean = (s) => (s || "").replace(/ /g, " ").replace(/\s+/g, " ").trim();
  const norm = (s) => clean(s).replace(/[:\s]+$/, "").toUpperCase();

  function isVisible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== "hidden" && cs.display !== "none";
  }

  function box(el) {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) };
  }

  function roleOf(el) {
    const explicit = el.getAttribute && el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "a") return el.hasAttribute("href") ? "link" : null;
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t === "hidden") return null;
      if (["submit", "button", "reset", "image"].includes(t)) return "button";
      if (t === "checkbox") return "checkbox";
      if (t === "radio") return "radio";
      return "textbox";
    }
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (el.hasAttribute("onclick")) return "button";
    return null;
  }

  function labelFor(el) {
    if (el.id) {
      const l = el.ownerDocument.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) return clean(l.textContent);
    }
    const wrap = el.closest && el.closest("label");
    return wrap ? clean(wrap.textContent) : "";
  }

  function nameOf(el) {
    const aria = el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const tag = el.tagName.toLowerCase();
    if (tag === "input") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (["submit", "button", "reset"].includes(t)) return clean(el.value || (t === "submit" ? "Submit" : ""));
      if (t === "image") return clean(el.getAttribute("alt"));
      return labelFor(el) || clean(el.getAttribute("title") || el.getAttribute("placeholder") || "");
    }
    if (tag === "select" || tag === "textarea") return labelFor(el) || clean(el.getAttribute("title") || "");
    return clean(el.innerText || el.textContent);
  }

  const hasControl = (el) => !!el.querySelector(HAS_CONTROL_SEL);
  const cellText = (td) => (hasControl(td) ? "" : clean(td.innerText));

  // Legacy label inference: the nearest preceding cell in the same row that holds text.
  function precedingText(el) {
    const td = el.closest && el.closest("td,th");
    if (!td || !td.parentElement) return null;
    const cells = Array.from(td.parentElement.children);
    for (let i = cells.indexOf(td) - 1; i >= 0; i--) {
      const t = cellText(cells[i]);
      if (t) return t;
    }
    return null;
  }

  // ---------------- data tables ----------------
  function tableInfo(table) {
    const rows = Array.from(table.rows).filter((r) => r.closest("table") === table);
    if (rows.length < 2) return null;
    const cells = Array.from(rows[0].cells);
    if (cells.some((c) => hasControl(c))) return null;
    const columns = cells.map((c) => norm(c.innerText));
    if (columns.filter(Boolean).length < 2) return null;
    const headerLike = cells.every((c) => !clean(c.innerText) || c.tagName === "TH" || c.querySelector("b,strong"));
    if (!headerLike) return null;
    const caption = table.caption ? norm(table.caption.innerText) : "";
    const name = norm(table.getAttribute("aria-label") || "") || caption || columns.filter(Boolean).join(" | ");
    return { table, name, columns, rows: rows.slice(1) };
  }

  function dataTables() {
    const list = [];
    for (const t of document.querySelectorAll("table")) {
      if (!isVisible(t)) continue;
      const info = tableInfo(t);
      if (info) list.push(info);
    }
    return list;
  }

  function rowValues(info, tr) {
    const out = {};
    Array.from(tr.cells).forEach((td, i) => {
      const col = info.columns[i];
      if (!col) return;
      const t = cellText(td);
      if (t) out[col] = norm(t);
    });
    return out;
  }

  function tableFor(el, infos) {
    const tr = el.closest && el.closest("tr");
    if (!tr) return null;
    for (const info of infos) if (info.rows.includes(tr)) return { info, tr };
    return null;
  }

  function columnOf(info, el) {
    const td = el.closest("td,th");
    if (!td) return null;
    return info.columns[Array.from(td.parentElement.cells).indexOf(td)] || null;
  }

  // Label–value rows outside data tables ("TAX ID | 900-00-4321"): the first text cell after the label is a readable field.
  function fieldOf(td, infos) {
    if (!td || (td.tagName !== "TD" && td.tagName !== "TH")) return null;
    const tr = td.parentElement;
    if (!tr || tr.tagName !== "TR") return null;
    if (infos.some((info) => info.table === tr.closest("table"))) return null;
    const cells = Array.from(tr.cells);
    if (cells.indexOf(td) < 1 || td.querySelector("table")) return null;
    const label = norm(cellText(cells[0]));
    if (!label) return null;
    const first = cells.slice(1).find((c) => !c.querySelector("table") && cellText(c));
    return first === td ? { label } : null;
  }

  // ---------------- dialogs (overlays, ARIA dialogs) ----------------
  function dialogs() {
    const found = [];
    if (!document.body) return found;
    const area = (window.innerWidth || 1) * (window.innerHeight || 1);
    for (const el of document.body.querySelectorAll("*")) {
      if (el.__roteOverlay || found.some((d) => d.el.contains(el))) continue;
      const roleAttr = el.getAttribute("role");
      let isDialog = roleAttr === "dialog" || roleAttr === "alertdialog" || (el.tagName === "DIALOG" && el.open);
      if (!isDialog) {
        const cs = getComputedStyle(el);
        if (cs.position === "fixed" && (parseInt(cs.zIndex, 10) || 0) >= 10) {
          const r = el.getBoundingClientRect();
          isDialog = r.width * r.height >= 0.2 * area;
        }
      }
      if (isDialog && isVisible(el) && clean(el.innerText)) {
        const firstLine = (el.innerText || "").split("\n").map(clean).find(Boolean) || "";
        found.push({ el, text: norm(el.innerText), title: norm(firstLine) });
      }
    }
    return found;
  }

  // ---------------- scan ----------------
  function describe(el, i, infos, dialogOf) {
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : null;
    const role = roleOf(el);
    const d = {
      index: i,
      role,
      name: nameOf(el) || null,
      tag,
      input_type: type,
      inferred_label: null,
      value: null,
      options: null,
      states: [],
      box: box(el),
      dialog: dialogOf(el),
      table: null,
      form_submit: (tag === "input" && ["submit", "image"].includes(type)) ||
        (tag === "button" && (el.getAttribute("type") || "submit").toLowerCase() === "submit"),
      form_method: el.form ? (el.form.getAttribute("method") || "GET").toUpperCase() : null,
    };
    if (["textbox", "combobox", "checkbox", "radio"].includes(role)) {
      if (tag === "select") {
        d.value = el.selectedOptions[0] ? clean(el.selectedOptions[0].text) : "";
        d.options = Array.from(el.options).map((o) => clean(o.text));
      } else if (type === "password") {
        d.states.push("secret");
      } else if (role === "textbox") {
        d.value = el.value || "";
      }
      if (!labelFor(el)) d.inferred_label = precedingText(el);
    } else if (role === "button" || role === "link") {
      d.inferred_label = precedingText(el);
    }
    if (el.disabled) d.states.push("disabled");
    if (el.checked) d.states.push("checked");
    if (el.readOnly) d.states.push("readonly");
    const tf = tableFor(el, infos);
    if (tf) d.table = { table: tf.info.name, row: rowValues(tf.info, tf.tr), column: columnOf(tf.info, el) };
    return d;
  }

  function scan() {
    const els = [];
    const reg = (el) => (els.push(el), els.length - 1);
    const infos = dataTables();
    const dlgs = dialogs();
    const dialogOf = (el) => {
      for (const d of dlgs) if (d.el.contains(el)) return d.title;
      return null;
    };
    const elements = [];
    const controlIndex = new Map();
    for (const el of document.querySelectorAll(CONTROL_SEL)) {
      if (el.__roteOverlay || (el.closest && el.closest("[data-rote-overlay]"))) continue;
      if (!roleOf(el) || !isVisible(el)) continue;
      const i = reg(el);
      controlIndex.set(el, i);
      elements.push(describe(el, i, infos, dialogOf));
    }
    const tables = infos.map((info) => ({
      index: reg(info.table),
      name: info.name,
      columns: info.columns,
      box: box(info.table),
      dialog: dialogOf(info.table),
      rows: info.rows.map((tr) => ({
        values: rowValues(info, tr),
        cells: Array.from(tr.cells).map((td, ci) => {
          const col = info.columns[ci];
          if (!col || hasControl(td)) return null;
          return { index: reg(td), column: col, text: clean(td.innerText), box: box(td) };
        }),
        controls: Array.from(tr.querySelectorAll(CONTROL_SEL))
          .map((c) => controlIndex.get(c))
          .filter((x) => x !== undefined),
      })),
    }));
    const tableByEl = new Map(infos.map((info, k) => [info.table, tables[k]]));
    const fields = [];
    const regField = (td, label) => {
      const i = reg(td);
      fields.push({ index: i, label, text: clean(td.innerText), box: box(td), dialog: dialogOf(td) });
      return i;
    };
    const layout = [];
    if (document.body) walk(document.body, layout, { dlgs, controlIndex, tableByEl, infos, regField });
    window.__rote.els = els;
    return {
      url: location.href,
      title: document.title,
      elements,
      tables,
      fields,
      layout,
      headings: headings(),
      dialogs: dlgs.map((d) => ({ text: d.text, title: d.title, box: box(d.el) })),
    };
  }

  function headings() {
    return Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6"))
      .filter(isVisible)
      .map((h) => ({ text: clean(h.innerText), box: box(h) }));
  }

  const BLOCK_SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
  const STRUCTURE_SEL = HAS_CONTROL_SEL + ", table, h1, h2, h3, h4, h5, h6";

  function walk(node, out, ctx) {
    for (const child of node.children) {
      const tag = child.tagName;
      if (BLOCK_SKIP.has(tag) || child.__roteOverlay) continue;
      if (tag === "INPUT" && (child.getAttribute("type") || "").toLowerCase() === "hidden") continue;
      if (!["TBODY", "THEAD", "TFOOT", "TR", "FORM"].includes(tag) && !isVisible(child)) continue;
      const dlg = ctx.dlgs.find((d) => d.el === child);
      if (dlg) {
        const items = [];
        walk(child, items, ctx);
        out.push({ type: "dialog", text: dlg.text, title: dlg.title, items });
        continue;
      }
      if (ctx.tableByEl.has(child)) {
        out.push({ type: "table", index: ctx.tableByEl.get(child).index });
        continue;
      }
      if (["TABLE", "TBODY", "THEAD", "TFOOT"].includes(tag)) {
        walk(child, out, ctx);
        continue;
      }
      if (ctx.controlIndex.has(child)) {
        out.push({ type: "control", index: ctx.controlIndex.get(child) });
        continue;
      }
      if (/^H[1-6]$/.test(tag)) {
        out.push({ type: "heading", text: clean(child.innerText) });
        continue;
      }
      if (tag === "TR") {
        const cells = [];
        for (const td of child.cells) {
          const items = [];
          if (!td.querySelector(STRUCTURE_SEL)) {
            const t = clean(td.innerText);
            if (t) {
              const f = fieldOf(td, ctx.infos);
              items.push(f ? { type: "text", text: t, field: ctx.regField(td, f.label) } : { type: "text", text: t });
            }
          } else {
            walk(td, items, ctx);
          }
          if (items.length) cells.push(items);
        }
        if (cells.length) {
          const first = cells[0].length === 1 && cells[0][0].type === "text" ? norm(cells[0][0].text) : null;
          out.push({ type: "row", label: first, cells });
        }
        continue;
      }
      if (!child.querySelector(STRUCTURE_SEL)) {
        const t = clean(child.innerText);
        if (t) out.push({ type: "text", text: t });
        continue;
      }
      walk(child, out, ctx);
    }
  }

  // Cheap state probe used for polling screen predicates.
  function probe() {
    return {
      url: location.href,
      headings: headings().map((h) => norm(h.text)),
      text: norm(document.body ? document.body.innerText : ""),
      dialogs: dialogs().map((d) => d.text),
      ready: document.readyState,
    };
  }

  // ---------------- resolution ----------------
  function fill(s, params) {
    return (s || "").replace(/\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g, (m, k) =>
      params && Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m
    );
  }

  function eqText(actual, expected, exact) {
    const a = norm(actual);
    const e = norm(expected);
    if (!e) return false;
    return exact === false ? a.includes(e) : a === e;
  }

  function rowMatches(info, tr, where, params) {
    const vals = rowValues(info, tr);
    return Object.entries(where || {}).every(([k, v]) => vals[norm(k)] === norm(fill(v, params)));
  }

  function resolveAll(loc, params) {
    const infos = dataTables();
    const dlgs = dialogs();
    const dialogOf = (el) => {
      for (const d of dlgs) if (d.el.contains(el)) return d.title;
      return null;
    };
    const controls = Array.from(document.querySelectorAll(CONTROL_SEL)).filter(
      (el) => !el.__roteOverlay && roleOf(el) && isVisible(el)
    );
    let matches = [];
    let reason = null;
    const kind = loc.kind;
    if (kind === "role") {
      matches = controls.filter((el) => roleOf(el) === loc.role && eqText(nameOf(el), fill(loc.name, params), loc.exact));
    } else if (kind === "label") {
      const want = fill(loc.label, params);
      matches = controls.filter((el) => {
        if (roleOf(el) !== loc.role) return false;
        const own = labelFor(el);
        return own ? eqText(own, want) : eqText(precedingText(el) || "", want);
      });
    } else if (kind === "near_text") {
      matches = controls.filter(
        (el) => roleOf(el) === loc.role && !tableFor(el, infos) && eqText(precedingText(el) || "", fill(loc.text, params))
      );
    } else if (kind === "text") {
      matches = controls.filter(
        (el) => (!loc.role || roleOf(el) === loc.role) && eqText(nameOf(el), fill(loc.text, params), loc.exact)
      );
    } else if (kind === "table" || kind === "table_cell") {
      const want = norm(fill(loc.table, params));
      const tabs = infos.filter((info) => info.name === want);
      if (!tabs.length) reason = "no_table";
      if (kind === "table") {
        matches = tabs.map((t) => t.table);
      } else {
        let rowsFound = 0;
        for (const info of tabs) {
          const ci = info.columns.indexOf(norm(loc.column));
          if (ci < 0) {
            reason = reason || "no_column";
            continue;
          }
          for (const tr of info.rows) {
            if (!rowMatches(info, tr, loc.row_where, params)) continue;
            rowsFound++;
            if (tr.cells[ci]) matches.push(tr.cells[ci]);
          }
        }
        if (tabs.length && !rowsFound && !reason) reason = "no_row";
      }
    } else if (kind === "field") {
      const want = norm(fill(loc.label, params));
      matches = Array.from(document.querySelectorAll("td, th")).filter((td) => {
        if (!isVisible(td)) return false;
        const f = fieldOf(td, infos);
        return !!f && f.label === want;
      });
      if (!matches.length) reason = "no_field";
    } else if (kind === "css") {
      try {
        matches = Array.from(document.querySelectorAll(loc.value)).filter(isVisible);
      } catch (e) {
        reason = "bad_css";
      }
    }
    if (loc.row_where && kind !== "table_cell") {
      matches = matches.filter((el) => {
        const tf = tableFor(el, infos);
        if (!tf) return false;
        if (loc.table && tf.info.name !== norm(fill(loc.table, params))) return false;
        return rowMatches(tf.info, tf.tr, loc.row_where, params);
      });
    }
    if (loc.within_dialog) {
      const want = norm(fill(loc.within_dialog, params));
      matches = matches.filter((el) => (dialogOf(el) || "").includes(want));
    }
    if (!matches.length && !reason) reason = "no_match";
    return { matches, reason };
  }

  function resolve(loc, params) {
    const r = resolveAll(loc, params);
    return { count: r.matches.length, el: r.matches.length === 1 ? r.matches[0] : null, reason: r.reason };
  }

  function cssPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node.tagName !== "BODY" && node.tagName !== "HTML") {
      if (node.tagName === "FORM" && node.getAttribute("name")) {
        parts.unshift(`form[name="${node.getAttribute("name")}"]`);
        break;
      }
      let part = node.tagName.toLowerCase();
      if (node.tagName === "INPUT" && node.getAttribute("name")) {
        part += `[name="${node.getAttribute("name")}"]`;
      } else if (node.parentElement) {
        const same = Array.from(node.parentElement.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  function rowScopes(tf, excludeColumn) {
    if (!tf) return [];
    const vals = rowValues(tf.info, tf.tr);
    const keys = Object.keys(vals).filter((k) => k !== excludeColumn);
    const scopes = [];
    for (let n = 1; n <= keys.length; n++) {
      const where = {};
      keys.slice(0, n).forEach((k) => (where[k] = vals[k]));
      scopes.push(where);
    }
    return scopes;
  }

  // Candidate locators for a live element, each verified against the same resolver used at replay.
  function candidatesForEl(el) {
    const infos = dataTables();
    const dlgs = dialogs();
    const dlg = dlgs.find((d) => d.el.contains(el));
    const tf = tableFor(el, infos);
    const out = [];
    const tag = el.tagName;
    if (tag === "TD" || tag === "TH") {
      const col = tf ? columnOf(tf.info, el) : null;
      if (tf && col) {
        for (const where of rowScopes(tf, col)) {
          out.push({ kind: "table_cell", table: tf.info.name, column: col, row_where: where });
        }
      } else {
        const f = fieldOf(el, infos);
        if (f) out.push({ kind: "field", label: f.label });
      }
    } else if (tag === "TABLE") {
      const info = infos.find((i) => i.table === el);
      if (info) out.push({ kind: "table", table: info.name });
    } else {
      const role = roleOf(el);
      if (role) {
        const name = nameOf(el);
        if (name) {
          out.push({ kind: "role", role, name });
          for (const where of rowScopes(tf)) out.push({ kind: "role", role, name, table: tf.info.name, row_where: where });
        }
        const label = labelFor(el) || precedingText(el);
        if (["textbox", "combobox", "checkbox", "radio"].includes(role) && label) out.push({ kind: "label", role, label });
        if ((role === "button" || role === "link") && !tf) {
          const near = precedingText(el);
          if (near) out.push({ kind: "near_text", role, text: near, direction: "right" });
        }
        if (role === "link" && name && !tf) out.push({ kind: "text", text: name });
      }
    }
    if (dlg) out.forEach((loc) => (loc.within_dialog = dlg.title));
    out.push({ kind: "css", value: cssPath(el), fragile: true });
    return out.map((loc) => {
      const r = resolveAll(loc, {});
      return { locator: loc, count: r.matches.length, same: r.matches.length === 1 && r.matches[0] === el };
    });
  }

  function readTable(el) {
    const info = dataTables().find((i) => i.table === el);
    if (!info) return null;
    return { name: info.name, columns: info.columns, rows: info.rows.map((tr) => Array.from(tr.cells).map((td) => clean(td.innerText))) };
  }

  // Rectangles of every visible occurrence of the given values (text nodes and input values), for screenshot masking.
  function rectsFor(values) {
    const rects = [];
    const wanted = (values || []).map((v) => String(v).toUpperCase()).filter((v) => v.length >= 2);
    if (!document.body) return rects;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const text = node.textContent.replace(/ /g, " ").toUpperCase();
      for (const v of wanted) {
        let idx = text.indexOf(v);
        while (idx >= 0) {
          const range = document.createRange();
          range.setStart(node, idx);
          range.setEnd(node, idx + v.length);
          for (const r of range.getClientRects()) rects.push({ x: r.x, y: r.y, width: r.width, height: r.height });
          idx = text.indexOf(v, idx + v.length);
        }
      }
    }
    for (const input of document.querySelectorAll("input, textarea")) {
      const t = (input.getAttribute("type") || "").toLowerCase();
      const value = (input.value || "").toUpperCase();
      if (!isVisible(input) || !value) continue;
      if (t === "password" || wanted.some((v) => value.includes(v))) rects.push(box(input));
    }
    return rects;
  }

  function setBlock(on, label) {
    const existing = document.querySelector("[data-rote-overlay]");
    if (!on) {
      if (existing) existing.remove();
      return;
    }
    if (existing || !document.body) return;
    const div = document.createElement("div");
    div.setAttribute("data-rote-overlay", "1");
    div.__roteOverlay = true;
    div.style.cssText =
      "position:fixed;left:0;top:0;right:0;height:18px;z-index:2147483647;background:rgba(0,128,0,.85);color:#fff;font:11px monospace;padding:2px 6px;pointer-events:none";
    div.textContent = label || "AUTOMATION IN CONTROL";
    document.body.appendChild(div);
  }

  // ---------------- human capture ----------------
  function humanEvent(type, el, extra) {
    if (typeof window.__roteHuman !== "function" || !el || el.nodeType !== 1) return;
    try {
      const infos = dataTables();
      const tf = tableFor(el, infos);
      const role = roleOf(el);
      const payload = {
        type,
        role,
        name: role ? nameOf(el) || null : null,
        label: labelFor(el) || precedingText(el) || null,
        table: tf ? { table: tf.info.name, row: rowValues(tf.info, tf.tr) } : null,
        route: location.pathname,
        ...extra,
      };
      if (type === "click" && role) payload.candidates = candidatesForEl(el).filter((c) => c.same).map((c) => c.locator);
      window.__roteHuman(payload);
    } catch (e) {
      /* never break the page */
    }
  }

  document.addEventListener(
    "click",
    (e) => {
      const t = e.target && e.target.closest ? e.target.closest(CONTROL_SEL) || e.target : e.target;
      humanEvent("click", t, {});
    },
    true
  );
  document.addEventListener(
    "change",
    (e) => {
      const t = e.target;
      const extra = {};
      if (t.tagName === "SELECT") extra.option = t.selectedOptions[0] ? clean(t.selectedOptions[0].text) : "";
      else if ((t.getAttribute("type") || "").toLowerCase() === "checkbox") extra.checked = !!t.checked;
      else if ("value" in t) extra.value_length = (t.value || "").length;
      humanEvent("change", t, extra);
    },
    true
  );
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Enter") humanEvent("key", e.target, { key: "Enter" });
    },
    true
  );

  window.__rote = {
    version: 1,
    els: [],
    scan,
    probe,
    resolve,
    candidatesFor: (i) => candidatesForEl(window.__rote.els[i]),
    candidatesForEl,
    readTable,
    rectsFor,
    setBlock,
  };
})();
