/* UI синхронизатора заявок: слева — кнопки, справа — вывод. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const el = {
    stats: $('[data-role="stats"]'),
    rows: $('[data-role="rows"]'),
    empty: $('[data-role="empty"]'),
    json: $('[data-role="json"]'),
    meta: $('[data-role="meta"]'),
    synced: $('[data-role="synced"]'),
    toasts: $('[data-role="toasts"]'),
    status: $('[data-role="status"]'),
    search: $("#search"),
    autorefresh: $("#autorefresh"),
    rejectedBadge: $('[data-badge="rejected"]'),
    rejectedRows: $('[data-role="rejected-rows"]'),
    rejectedEmpty: $('[data-role="rejected-empty"]'),
    rejectedCount: $('[data-role="rejected-count"]'),
  };

  const state = {
    result: null,
    rejected: [],
    view: "table",
    filter: "",
    timer: null,
  };

  const nf = new Intl.NumberFormat("ru-RU");

  /* ───────────────────────────── helpers ───────────────────────────── */

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  function toast(message, kind = "ok") {
    const node = document.createElement("div");
    node.className = `toast toast--${kind}`;
    node.innerHTML = `<span class="toast__dot"></span><span>${escapeHtml(message)}</span>`;
    el.toasts.appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transform = "translateY(6px)";
      node.style.transition = "opacity .2s, transform .2s";
      setTimeout(() => node.remove(), 220);
    }, 3200);
  }

  function setBusy(button, busy) {
    if (!button) return;
    button.disabled = busy;
    button.classList.toggle("is-busy", busy);
  }

  function setStatus(text, kind) {
    el.status.classList.remove("is-ok", "is-bad");
    if (kind) el.status.classList.add(kind);
    $(".status__text", el.status).textContent = text;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { Accept: "application/json" },
      ...options,
    });
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      /* non-JSON error page */
    }
    if (!response.ok) {
      const detail = (data && (data.detail || data.message)) || `HTTP ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  /* ───────────────────────────── rendering ───────────────────────────── */

  function renderStats(stats) {
    $$("[data-stat]").forEach((node) => {
      const value = stats ? stats[node.dataset.stat] : null;
      node.textContent = value === null || value === undefined ? "—" : nf.format(value);
    });
    el.stats.classList.toggle("is-loading", !stats);
  }

  function matches(client) {
    if (!state.filter) return true;
    const needle = state.filter.toLowerCase();
    return [client.name, client.phone, client.email]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  }

  function renderTable() {
    const clients = (state.result && state.result.clients) || [];
    const rows = clients.filter(matches);

    el.rows.innerHTML = rows
      .map((client, index) => `
        <tr>
          <td class="cell-idx">${index + 1}</td>
          <td class="cell-name">${escapeHtml(client.name) || '<span class="cell-mail">—</span>'}</td>
          <td class="cell-phone">${escapeHtml(client.phone)}</td>
          <td class="cell-mail${client.email ? "" : " is-empty"}">${
            client.email ? escapeHtml(client.email) : "не указан"
          }</td>
          <td class="num"><span class="amount">${nf.format(client.amount)} ₽</span></td>
        </tr>`)
      .join("");

    el.empty.hidden = rows.length > 0;
    if (state.filter && rows.length === 0) {
      el.empty.hidden = false;
      $("p", el.empty).textContent = "Ничего не найдено";
    } else {
      $("p", el.empty).textContent = "Пока нет данных";
    }
  }

  function renderJson() {
    el.json.textContent = state.result
      ? JSON.stringify(state.result, null, 2)
      : "{ }";
  }

  function renderMeta() {
    const stats = state.result && state.result.stats;
    if (!stats) {
      el.meta.textContent = "Нажмите «Синхронизировать», чтобы забрать заявки";
      el.synced.textContent = "—";
      return;
    }
    const clients = state.result.clients.length;
    el.meta.textContent =
      `${nf.format(clients)} клиентов из ${nf.format(stats.received)} заявок · ` +
      `${nf.format(stats.invalid_dropped)} невалидных отброшено · ` +
      `формат +7XXXXXXXXXX`;
  }

  function renderRejected() {
    const rows = state.rejected;
    el.rejectedRows.innerHTML = rows
      .map(
        (item) => `
        <tr>
          <td class="cell-idx">${escapeHtml(item.source_id)}</td>
          <td class="cell-phone">${escapeHtml(item.phone_raw) || '<span class="cell-mail is-empty">пусто</span>'}</td>
          <td class="cell-name">${escapeHtml(item.name) || "—"}</td>
          <td><span class="pill">${escapeHtml(item.reason)}</span></td>
        </tr>`
      )
      .join("");
    el.rejectedEmpty.hidden = rows.length > 0;
    el.rejectedCount.textContent = nf.format(rows.length);
    el.rejectedBadge.textContent = nf.format(rows.length);
    el.rejectedBadge.hidden = rows.length === 0;
  }

  function render() {
    renderStats(state.result && state.result.stats);
    renderTable();
    renderJson();
    renderMeta();
  }

  function showPanel(name) {
    $$("[data-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== name;
    });
    $$('[data-action="view"]').forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.view === name);
    });
    $('[data-action="rejected"]').classList.toggle("is-active", name === "rejected");
  }

  function showRejected() {
    $$("[data-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== "rejected";
    });
    $$('[data-action="view"]').forEach((btn) => btn.classList.remove("is-active"));
    $('[data-action="rejected"]').classList.add("is-active");
  }

  function goToView(name, { push = true } = {}) {
    state.view = name;
    if (name === "rejected") showRejected();
    else showPanel(name);
    if (push && location.hash.slice(1) !== name) location.hash = name;
  }

  /* ───────────────────────────── actions ───────────────────────────── */

  async function loadResult({ announce = false } = {}) {
    state.result = await api("/result");
    render();
    if (announce) toast("Данные обновлены");
    return state.result;
  }

  async function loadHealth({ silent = false } = {}) {
    const health = await api("/health");
    setStatus(
      `Источник отвечает · синхронизаций: ${nf.format(health.runs)}`,
      health.status === "ok" ? "is-ok" : "is-bad"
    );
    if (health.synced_at) {
      const date = new Date(health.synced_at);
      el.synced.textContent = Number.isNaN(date.getTime())
        ? health.synced_at
        : date.toLocaleString("ru-RU");
    }
    if (!silent) toast(`Диагностика: ${health.status}, запусков — ${health.runs}`);
    return health;
  }

  async function loadRejected({ announce = false, show = true } = {}) {
    const data = await api("/admin/rejected");
    state.rejected = data.rejected || [];
    renderRejected();
    if (show) goToView("rejected");
    if (announce) toast(`Отклонённых заявок: ${state.rejected.length}`);
    return state.rejected;
  }

  const actions = {
    async sync(button) {
      setBusy(button, true);
      setStatus("Синхронизация…");
      try {
        state.result = await api("/sync", { method: "POST" });
        render();
        await Promise.all([
          loadRejected({ show: false }),
          loadHealth({ silent: true }),
        ]);
        const s = state.result.stats;
        toast(`Готово: ${nf.format(s.unique)} клиентов, ${nf.format(s.invalid_dropped)} отброшено`);
      } catch (error) {
        setStatus("Ошибка синхронизации", "is-bad");
        toast(error.message, "err");
      } finally {
        setBusy(button, false);
      }
    },

    async reload(button) {
      setBusy(button, true);
      try {
        await loadResult({ announce: true });
        goToView(state.view, { push: false });
      } catch (error) {
        toast(error.message, "err");
      } finally {
        setBusy(button, false);
      }
    },

    async health(button) {
      setBusy(button, true);
      try {
        await loadHealth();
      } catch (error) {
        setStatus("Источник недоступен", "is-bad");
        toast(error.message, "err");
      } finally {
        setBusy(button, false);
      }
    },

    async rejected(button) {
      setBusy(button, true);
      try {
        await loadRejected({ announce: true });
      } catch (error) {
        toast(error.message, "err");
      } finally {
        setBusy(button, false);
      }
    },

    view(button) {
      goToView(button.dataset.view);
    },

    copy(button) {
      const text = state.result ? JSON.stringify(state.result, null, 2) : "{}";
      const done = () => toast("JSON скопирован в буфер обмена");
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
      } else {
        fallbackCopy(text, done);
      }
      setBusy(button, false);
    },
  };

  function fallbackCopy(text, done) {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      done();
    } catch (_) {
      toast("Не удалось скопировать", "err");
    }
    area.remove();
  }

  /* ───────────────────────────── wiring ───────────────────────────── */

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    const handler = actions[button.dataset.action];
    if (handler) handler(button, event);
  });

  el.search.addEventListener("input", (event) => {
    state.filter = event.target.value.trim();
    renderTable();
  });

  el.autorefresh.addEventListener("change", (event) => {
    clearInterval(state.timer);
    state.timer = null;
    if (event.target.checked) {
      state.timer = setInterval(() => actions.sync(), 60000);
      toast("Автосинхронизация включена: раз в 60 секунд");
    } else {
      toast("Автосинхронизация выключена");
    }
  });

  (async function init() {
    goToView(location.hash.slice(1) || "table", { push: false });
    try {
      await Promise.all([
        loadResult(),
        loadHealth({ silent: true }),
        loadRejected({ show: false }),
      ]);
    } catch (_) {
      el.meta.textContent = "Данных пока нет — запустите синхронизацию";
      setStatus("Нет данных", "is-bad");
    }
  })();

  window.addEventListener("hashchange", () => {
    const name = location.hash.slice(1) || "table";
    if (name !== state.view) goToView(name, { push: false });
  });
})();