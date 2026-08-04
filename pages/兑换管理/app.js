(() => {
  "use strict";

  const DEFAULT_TEMPLATE = "兑换成功！\n兑换物：{item}\n兑换内容：{content}\n消耗 {cost} {points_name}，剩余 {remaining} {points_name}。";
  const DEFAULT_SCOPE = { mode: "blacklist", scope: [] };
  const state = { data: null, draft: [], scope: { ...DEFAULT_SCOPE }, selected: -1, dirty: false, saving: false, saveStatus: "clean", view: "inventory", theme: "system" };
  let saveStateTimer = 0;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
  const uniqueLines = (value) => [...new Set(String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean))];
  const uniqueScopeLines = (value) => {
    const seen = new Set();
    return String(value || "").replace(/，/g, ",").split(/[\r\n,]+/).map((item) => item.trim()).filter((item) => {
      const normalized = item.toLocaleLowerCase();
      if (!normalized || seen.has(normalized)) return false;
      seen.add(normalized);
      return true;
    });
  };

  function icons() { if (window.lucide?.createIcons) window.lucide.createIcons({ attrs: { "aria-hidden": "true" } }); }

  function getBridge() {
    if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
    try { if (window.parent && window.parent !== window) return window.parent.AstrBotPluginPage || null; } catch (_) { return null; }
    return null;
  }

  async function bridge() {
    for (let index = 0; index < 60; index += 1) {
      const api = getBridge();
      if (api?.apiGet && api?.apiPost) {
        if (typeof api.ready === "function") await api.ready();
        return api;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    throw new Error("请从 AstrBot 插件拓展页打开此页面");
  }

  async function requestEndpoint(method, path, body = {}) {
    const api = await bridge();
    const result = method === "GET" ? await api.apiGet(`page/${path}`) : await api.apiPost(`page/${path}`, body);
    if (result?.status === "error") throw new Error(result.message || "请求失败");
    return result?.data ?? result;
  }

  function toast(message, kind = "success") {
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.innerHTML = `<i data-lucide="${kind === "error" ? "circle-alert" : "circle-check"}"></i><span>${escapeHtml(message)}</span>`;
    $("#toastRegion").append(node);
    icons();
    window.setTimeout(() => node.remove(), 3200);
  }

  function setConnection(kind, text) {
    const node = $("#connectionState");
    node.className = `connection-state ${kind}`;
    node.innerHTML = `<i data-lucide="${kind === "ok" ? "cloud-check" : kind === "error" ? "cloud-off" : "loader-circle"}"></i>${escapeHtml(text)}`;
    icons();
  }

  function setSaveStatus(status) {
    window.clearTimeout(saveStateTimer);
    state.saveStatus = status;
    state.saving = status === "saving";
    const states = {
      clean: { icon: "save", button: "保存修改", state: "", hidden: true },
      dirty: { icon: "save", button: "保存修改", state: "有未保存修改", hidden: false },
      saving: { icon: "loader-circle", button: "保存中", state: "正在保存", hidden: false },
      saved: { icon: "circle-check", button: "已保存", state: "修改已生效", hidden: false },
    };
    const current = states[status] || states.clean;
    const indicator = $("#saveState");
    indicator.hidden = current.hidden;
    indicator.className = `save-state ${status}`;
    indicator.innerHTML = `<i data-lucide="${status === "dirty" ? "circle-dot" : current.icon}"></i><span id="saveStateText">${current.state}</span>`;
    const button = $("#saveButton");
    button.innerHTML = `<i data-lucide="${current.icon}"></i><span id="saveButtonText">${current.button}</span>`;
    button.disabled = status !== "dirty" || !state.data?.can_save;
    icons();
    if (status === "saved") {
      saveStateTimer = window.setTimeout(() => {
        if (!state.dirty && state.saveStatus === "saved") setSaveStatus("clean");
      }, 2200);
    }
  }

  function setDirty(value = true, status = value ? "dirty" : "clean") {
    state.dirty = value;
    setSaveStatus(status);
  }

  function applyTheme(theme) {
    state.theme = theme;
    const dark = theme === "dark" || (theme === "system" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try { window.localStorage?.setItem("point-exchange-theme", theme); } catch (_) { /* sandboxed pages may not expose storage */ }
  }

  function toggleTheme() {
    const currentDark = document.documentElement.dataset.theme === "dark";
    applyTheme(currentDark ? "light" : "dark");
  }

  function stockFor(item) {
    const used = Number(item.used_count || 0);
    return Math.max((item.contents || []).length - used, 0);
  }

  function updateMetrics() {
    const metrics = state.data?.metrics || {};
    $("#stockMetric").textContent = metrics.stock ?? 0;
    $("#itemMetric").textContent = `${metrics.enabled_count ?? 0} / ${metrics.item_count ?? 0}`;
    $("#redeemedMetric").textContent = metrics.redeemed_count ?? 0;
    $("#spentMetric").textContent = `${metrics.points_spent ?? 0} ${state.data?.points_name || "积分"}`;
    $("#recordCount").textContent = metrics.redeemed_count ?? 0;
  }

  function normalizeScope(value) {
    const mode = value?.mode === "whitelist" ? "whitelist" : "blacklist";
    const scope = uniqueScopeLines(Array.isArray(value?.scope) ? value.scope.join("\n") : "");
    return { mode, scope };
  }

  function updateScopeStatus() {
    const count = state.scope.scope.length;
    const whitelist = state.scope.mode === "whitelist";
    $("#scopeSummary").textContent = whitelist
      ? (count ? `白名单已开放 ${count} 个范围` : "白名单为空，当前未开放兑换")
      : (count ? `黑名单已排除 ${count} 个范围` : "黑名单为空，所有群和账号可兑换");
    const hint = $("#scopeHint");
    hint.textContent = whitelist
      ? "只有名单中的群或账号可以兑换；可填写 group: / user: 前缀"
      : "名单中的群或账号无法兑换；留空表示不限制";
    hint.classList.toggle("warning", whitelist && count === 0);
  }

  function renderScope() {
    $$('[data-scope-mode]').forEach((button) => {
      const active = button.dataset.scopeMode === state.scope.mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    $("#scopeValues").value = state.scope.scope.join("\n");
    updateScopeStatus();
  }

  function renderItemList() {
    const query = $("#itemSearch").value.trim().toLocaleLowerCase();
    const visible = state.draft.map((item, index) => ({ item, index })).filter(({ item }) => !query || String(item.name || "").toLocaleLowerCase().includes(query));
    const list = $("#itemList");
    if (!visible.length) {
      list.innerHTML = state.draft.length
        ? `<div class="list-empty"><i data-lucide="search-x"></i><span>没有匹配的兑换物</span><button class="text-button" type="button" data-action="clear-search">清除搜索</button></div>`
        : `<div class="list-empty"><i data-lucide="package-open"></i><span>还没有兑换物</span><small>从右侧快速向导开始创建</small></div>`;
      icons();
      return;
    }
    list.innerHTML = visible.map(({ item, index }) => {
      const stock = stockFor(item);
      return `<button class="item-row ${index === state.selected ? "active" : ""} ${item.enabled ? "" : "disabled"}" type="button" data-index="${index}">
        <strong>${escapeHtml(item.name || "未命名兑换物")}</strong><span class="stock-pill ${stock ? "" : "empty"}">${stock}</span>
        <small>${escapeHtml(item.cost)} ${escapeHtml(state.data?.points_name || "积分")}${item.private_only ? " · 私聊" : ""}</small>
      </button>`;
    }).join("");
  }

  function renderEditor() {
    const item = state.draft[state.selected];
    $("#editorEmpty").hidden = Boolean(item);
    $("#itemEditor").hidden = !item;
    if (!item) { icons(); return; }
    $("#editorTitle").textContent = item.name || "未命名兑换物";
    $("#itemName").value = item.name || "";
    $("#itemCost").value = item.cost || 1;
    $("#itemEnabled").checked = Boolean(item.enabled);
    $("#itemPrivate").checked = Boolean(item.private_only);
    $("#itemContents").value = (item.contents || []).join("\n");
    $("#successTemplate").value = item.success_template || DEFAULT_TEMPLATE;
    $("#pointsNameSuffix").textContent = state.data?.points_name || "积分";
    clearValidation();
    updateStockEditor();
    renderTemplatePreview();
    icons();
  }

  function updateStockEditor(sourceText = null) {
    const item = state.draft[state.selected];
    if (!item) return;
    const total = (item.contents || []).length;
    const used = Math.min(Number(item.used_count || 0), total);
    const available = Math.max(total - used, 0);
    $("#availableStock").textContent = available;
    $("#usedStock").textContent = used;
    $("#totalStock").textContent = total;
    const meta = $("#contentMeta");
    const entered = sourceText === null ? total : String(sourceText).split(/\r?\n/).map((line) => line.trim()).filter(Boolean).length;
    const ignored = Math.max(entered - total, 0);
    meta.textContent = total
      ? `${available} 份可用，共识别 ${total} 份${ignored ? `，忽略 ${ignored} 条重复内容` : ""}`
      : "还没有可发放内容，保存后用户暂时无法兑换";
    meta.classList.toggle("warning", available === 0);
  }

  function renderTemplatePreview() {
    const item = state.draft[state.selected];
    if (!item) return;
    const pointsName = state.data?.points_name || "积分";
    const used = Math.min(Number(item.used_count || 0), (item.contents || []).length);
    const sampleContent = item.contents?.[used] || item.contents?.[0] || "示例发放内容";
    let template = item.success_template || DEFAULT_TEMPLATE;
    if (!template.includes("{content}")) template += "\n兑换内容：{content}";
    const values = {
      item: item.name || "兑换物名称",
      content: sampleContent,
      cost: item.cost || 1,
      points_name: pointsName,
      remaining: 500,
    };
    $("#templatePreview").textContent = template.replace(
      /\{(item|content|cost|points_name|remaining)\}/g,
      (_, key) => String(values[key]),
    );
  }

  function selectItem(index, reveal = false) {
    state.selected = Number(index);
    renderItemList();
    renderEditor();
    if (reveal && window.matchMedia?.("(max-width: 680px)").matches) {
      window.requestAnimationFrame(() => $(".editor-surface").scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  }

  function nextItemName() {
    const names = new Set(state.draft.map((item) => item.name));
    let index = 1;
    while (names.has(index === 1 ? "新兑换物" : `新兑换物 ${index}`)) index += 1;
    return index === 1 ? "新兑换物" : `新兑换物 ${index}`;
  }

  function addItem(example = false) {
    const item = { name: nextItemName(), enabled: true, cost: 100, contents: [], private_only: true, success_template: DEFAULT_TEMPLATE, stock: 0, used_count: 0, total_count: 0 };
    if (example) {
      item.name = state.draft.some((entry) => entry.name === "新人礼包示例") ? nextItemName() : "新人礼包示例";
      item.enabled = false;
      item.private_only = false;
      item.contents = ["奖励内容 001", "奖励内容 002", "奖励内容 003"];
    }
    state.draft.push(item);
    setDirty();
    selectItem(state.draft.length - 1, true);
    window.requestAnimationFrame(() => {
      $("#itemName").select();
      if (example) toast("已载入未启用的填写示例，请按实际内容修改");
    });
  }

  function clearValidation() {
    ["itemName", "itemCost"].forEach((name) => {
      const input = $(`#${name}`);
      const error = $(`#${name}Error`);
      input?.removeAttribute("aria-invalid");
      if (error) { error.hidden = true; error.textContent = ""; }
    });
  }

  function showValidation(index, field, message) {
    switchView("inventory");
    selectItem(index, true);
    const input = $(`#${field}`);
    const error = $(`#${field}Error`);
    input.setAttribute("aria-invalid", "true");
    error.textContent = message;
    error.hidden = false;
    window.requestAnimationFrame(() => input.focus());
    toast(message, "error");
  }

  function normalizeContents() {
    const input = $("#itemContents");
    const cleaned = uniqueLines(input.value).join("\n");
    const changed = cleaned !== input.value;
    input.value = cleaned;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    toast(changed ? "已移除空行和重复内容" : "内容已经整理好了");
  }

  function deleteSelected() {
    const item = state.draft[state.selected];
    if (!item) return;
    $("#deleteMessage").textContent = `将删除“${item.name}”及配置中的全部库存内容，历史兑换记录仍会保留。`;
    $("#deleteDialog").showModal();
  }

  function confirmDelete() {
    if (state.selected < 0) return;
    state.draft.splice(state.selected, 1);
    state.selected = Math.min(state.selected, state.draft.length - 1);
    setDirty();
    renderItemList();
    renderEditor();
  }

  function updateSelected(key, value, rerenderList = true) {
    const item = state.draft[state.selected];
    if (!item) return;
    item[key] = value;
    setDirty();
    if (rerenderList) renderItemList();
  }

  function renderRecords() {
    const query = $("#recordSearch").value.trim().toLocaleLowerCase();
    const records = (state.data?.redemptions || []).filter((item) => !query || `${item.item_name} ${item.user_id}`.toLocaleLowerCase().includes(query));
    const list = $("#recordList");
    if (!records.length) {
      list.innerHTML = `<div class="record-empty">${query ? "没有匹配的兑换记录" : "暂无兑换记录"}</div>`;
      return;
    }
    list.innerHTML = records.map((item) => `<div class="record-table record-row">
      <strong>${escapeHtml(item.item_name || "已删除兑换物")}</strong>
      <span>${escapeHtml(item.user_id || "未知")}</span>
      <span class="record-cost">-${escapeHtml(item.cost)} ${escapeHtml(state.data?.points_name || "积分")}</span>
      <span>${escapeHtml(formatDate(item.redeemed_at))}</span>
    </div>`).join("");
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
  }

  function switchView(view) {
    state.view = view;
    $$(".view-tab").forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $$('[data-view-panel]').forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== view; panel.classList.toggle("active", panel.dataset.viewPanel === view); });
    if (view === "records") renderRecords();
  }

  async function loadData(force = false) {
    if (state.dirty && !force && !window.confirm("当前修改尚未保存，仍要刷新吗？")) return;
    setConnection("", "连接中");
    $("#refreshButton").disabled = true;
    try {
      const data = await requestEndpoint("GET", "overview");
      state.data = data;
      state.draft = JSON.parse(JSON.stringify(data.items || []));
      state.scope = normalizeScope(data.exchange_scope);
      state.selected = state.draft.length ? Math.min(Math.max(state.selected, 0), state.draft.length - 1) : -1;
      setDirty(false);
      updateMetrics();
      renderScope();
      renderItemList();
      renderEditor();
      renderRecords();
      setConnection("ok", "已同步");
      if (!data.can_save) toast("当前 AstrBot 版本不支持从拓展页保存配置", "error");
    } catch (error) {
      setConnection("error", "连接失败");
      toast(error.message || "无法读取兑换数据", "error");
    } finally {
      $("#refreshButton").disabled = false;
      icons();
    }
  }

  async function saveData() {
    if (!state.dirty || !state.data?.can_save || state.saving) return;
    clearValidation();
    const names = new Set();
    for (let index = 0; index < state.draft.length; index += 1) {
      const item = state.draft[index];
      const name = String(item.name || "").trim();
      if (!name) { showValidation(index, "itemName", "请填写兑换物名称"); return; }
      if (names.has(name.toLocaleLowerCase())) { showValidation(index, "itemName", `名称“${name}”已经用过，请换一个`); return; }
      item.name = name;
      names.add(name.toLocaleLowerCase());
      if (!Number.isFinite(Number(item.cost)) || Number(item.cost) < 1 || Number(item.cost) > 1000000000) { showValidation(index, "itemCost", `请为“${name}”填写有效的兑换积分`); return; }
    }
    setSaveStatus("saving");
    setConnection("", "保存中");
    try {
      const data = await requestEndpoint("POST", "items/save", { revision: state.data.revision, items: state.draft, exchange_scope: state.scope });
      state.data = data;
      state.draft = JSON.parse(JSON.stringify(data.items || []));
      state.scope = normalizeScope(data.exchange_scope);
      state.selected = state.draft.length ? Math.min(Math.max(state.selected, 0), state.draft.length - 1) : -1;
      setDirty(false, "saved");
      updateMetrics();
      renderScope();
      renderItemList();
      renderEditor();
      renderRecords();
      setConnection("ok", "已保存");
      toast("兑换配置已保存并立即生效");
    } catch (error) {
      setConnection("error", "保存失败");
      toast(error.message || "保存失败", "error");
      setSaveStatus("dirty");
    }
  }

  function bindEvents() {
    $("#themeButton").addEventListener("click", toggleTheme);
    $("#refreshButton").addEventListener("click", () => loadData());
    $("#saveButton").addEventListener("click", saveData);
    $("#addItemButton").addEventListener("click", () => addItem());
    $$("[data-action='add-item']").forEach((button) => button.addEventListener("click", () => addItem()));
    $("[data-action='add-example']").addEventListener("click", () => addItem(true));
    $("#deleteItemButton").addEventListener("click", deleteSelected);
    $("#confirmDelete").addEventListener("click", confirmDelete);
    $("#itemSearch").addEventListener("input", renderItemList);
    $("#recordSearch").addEventListener("input", renderRecords);
    $$('[data-scope-mode]').forEach((button) => button.addEventListener("click", () => {
      const mode = button.dataset.scopeMode === "whitelist" ? "whitelist" : "blacklist";
      if (state.scope.mode === mode) return;
      state.scope.mode = mode;
      setDirty();
      renderScope();
    }));
    $("#scopeValues").addEventListener("input", (event) => {
      const nextScope = uniqueScopeLines(event.target.value);
      if (JSON.stringify(nextScope) !== JSON.stringify(state.scope.scope)) {
        state.scope.scope = nextScope;
        setDirty();
      }
      updateScopeStatus();
    });
    $("#scopeValues").addEventListener("blur", (event) => { event.target.value = state.scope.scope.join("\n"); });
    $("#itemList").addEventListener("click", (event) => {
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (action === "clear-search") { $("#itemSearch").value = ""; renderItemList(); $("#itemSearch").focus(); return; }
      const row = event.target.closest("[data-index]");
      if (row) selectItem(row.dataset.index, true);
    });
    $$(".view-tab").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
    $("#itemName").addEventListener("input", (event) => { clearValidation(); updateSelected("name", event.target.value); $("#editorTitle").textContent = event.target.value || "未命名兑换物"; renderTemplatePreview(); });
    $("#itemCost").addEventListener("input", (event) => { clearValidation(); updateSelected("cost", Number(event.target.value || 0)); renderTemplatePreview(); });
    $("#itemEnabled").addEventListener("change", (event) => updateSelected("enabled", event.target.checked));
    $("#itemPrivate").addEventListener("change", (event) => updateSelected("private_only", event.target.checked));
    $("#itemContents").addEventListener("input", (event) => { updateSelected("contents", uniqueLines(event.target.value), false); updateStockEditor(event.target.value); renderTemplatePreview(); });
    $("#cleanContentsButton").addEventListener("click", normalizeContents);
    $("#successTemplate").addEventListener("input", (event) => { updateSelected("success_template", event.target.value, false); renderTemplatePreview(); });
    $("#resetTemplateButton").addEventListener("click", () => { $("#successTemplate").value = DEFAULT_TEMPLATE; updateSelected("success_template", DEFAULT_TEMPLATE, false); renderTemplatePreview(); });
    $(".variable-row").addEventListener("click", (event) => {
      const button = event.target.closest("[data-variable]");
      if (!button) return;
      const input = $("#successTemplate");
      const start = input.selectionStart ?? input.value.length;
      input.setRangeText(button.dataset.variable, start, input.selectionEnd ?? start, "end");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
    window.addEventListener("beforeunload", (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
    window.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "s") {
        event.preventDefault();
        saveData();
      }
    });
  }

  function init() {
    let savedTheme = "system";
    try { savedTheme = window.localStorage?.getItem("point-exchange-theme") || "system"; } catch (_) { /* noop */ }
    applyTheme(savedTheme);
    bindEvents();
    icons();
    loadData(true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true }); else init();
})();
