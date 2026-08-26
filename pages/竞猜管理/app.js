(() => {
  "use strict";
  const state = { data: null, query: "" };
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);

  function getBridge() {
    if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
    try { return window.parent?.AstrBotPluginPage || null; } catch (_) { return null; }
  }
  async function bridge() {
    for (let i = 0; i < 60; i += 1) {
      const api = getBridge();
      if (api?.apiGet && api?.apiPost) { if (typeof api.ready === "function") await api.ready(); return api; }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("请从 AstrBot 插件拓展页打开此页面");
  }
  async function request(method, path, body = {}) {
    const api = await bridge();
    const endpoint = `esports/${String(path).replace(/^\/+/, "")}`;
    const result = method === "GET" ? await api.apiGet(endpoint) : await api.apiPost(endpoint, body);
    const data = result?.data ?? result;
    if (result?.status === "error" || data?.ok === false) throw new Error(data?.error || result?.message || "请求失败");
    return data;
  }
  function toast(message, kind = "success") {
    const node = document.createElement("div"); node.className = `toast ${kind}`; node.textContent = message; $("#toastRegion").append(node); setTimeout(() => node.remove(), 3200);
  }
  function statusName(value) { return ({not_started:"待开赛",running:"进行中",closed:"已封盘",finished:"待结算",settled:"已结算",refunded:"已退款",canceled:"已取消",postponed:"已延期"})[value] || value || "未知"; }
  function betStatus(value) { return ({pending:"待结算",won:"命中",lost:"未命中",refunded:"已退款",withdrawn:"已撤单"})[value] || value; }

  function render() {
    const data = state.data; if (!data) return;
    const summary = data.summary;
    $("#metrics").innerHTML = [
      ["已收录比赛", summary.match_count], ["开放比赛", summary.open_match_count], ["下注记录", summary.bet_count], ["待结算积分", summary.pending_points]
    ].map(([label,value]) => `<article class="metric"><span>${esc(label)}</span><strong>${Number(value).toLocaleString("zh-CN")}</strong></article>`).join("");
    const settings = data.settings;
    $("#enabled").checked = settings.enabled; $("#syncEnabled").checked = settings.sync_enabled; $("#syncInterval").value = settings.sync_interval_minutes;
    $("#competitions").value = (settings.tracked_competitions || []).join("\n"); $("#gameLol").checked = settings.games.includes("lol"); $("#gameValorant").checked = settings.games.includes("valorant");
    $("#token").placeholder = settings.token_configured ? "已配置；留空保持不变" : "粘贴 PandaScore Token";
    const query = state.query.toLocaleLowerCase();
    const matches = data.matches.filter((item) => !query || `${item.display_id} ${item.competition} ${item.name}`.toLocaleLowerCase().includes(query));
    $("#matchRows").innerHTML = matches.length ? matches.map((match) => {
      const [a,b] = match.teams;
      const disabled = ["settled","refunded"].includes(match.status);
      return `<tr><td><strong>${esc(match.display_id)}</strong><small>${esc(match.game.toUpperCase())} · ${esc(match.competition)}</small><span>${esc(match.name)}</span></td><td>${esc(match.start_time_text)}</td><td><div class="team-line">${esc(a.name)} · ${Number(a.probability).toLocaleString("zh-CN",{style:"percent",maximumFractionDigits:1})} · ${Number(a.odds).toFixed(2)} · ${a.pool}</div><div class="team-line">${esc(b.name)} · ${Number(b.probability).toLocaleString("zh-CN",{style:"percent",maximumFractionDigits:1})} · ${Number(b.odds).toFixed(2)} · ${b.pool}</div></td><td><span class="tag ${disabled ? "done":"open"}">${esc(statusName(match.status))}</span><small>${match.visible ? "群内可见":"已隐藏"}${match.odds_locked ? " · 倍率已锁":""}</small></td><td><div class="actions"><button data-action="settle" data-match="${esc(match.id)}" data-team="${esc(a.id)}" ${disabled?"disabled":""}>A 胜</button><button data-action="settle" data-match="${esc(match.id)}" data-team="${esc(b.id)}" ${disabled?"disabled":""}>B 胜</button><button data-action="refund" data-match="${esc(match.id)}" ${disabled?"disabled":""}>退款</button><button data-action="close" data-match="${esc(match.id)}" ${disabled?"disabled":""}>封盘</button><button data-action="${match.visible?"hide":"show"}" data-match="${esc(match.id)}">${match.visible?"隐藏":"显示"}</button></div></td></tr>`;
    }).join("") : `<tr><td class="empty" colspan="5">没有匹配的比赛</td></tr>`;
    $("#betRows").innerHTML = data.bets.length ? data.bets.slice(0,100).map((bet) => `<tr><td>${esc(bet.match_display_id)}</td><td>${esc(bet.user_id)}</td><td>${esc(bet.team_name)}</td><td>${bet.amount}</td><td>${Number(bet.odds).toFixed(2)}</td><td>${esc(betStatus(bet.status))}<small>返还 ${bet.payout}</small></td></tr>`).join("") : `<tr><td class="empty" colspan="6">暂无下注记录</td></tr>`;
  }
  async function load() {
    try { $("#connection").className="pill"; $("#connection").textContent="加载中"; state.data = await request("GET","overview"); render(); $("#connection").className="pill ok"; $("#connection").textContent="已连接"; }
    catch (error) { $("#connection").className="pill error"; $("#connection").textContent="连接失败"; toast(error.message,"error"); }
  }
  async function post(path, body, success) { try { await request("POST",path,body); toast(success); await load(); } catch (error) { toast(error.message,"error"); } }
  $("#refresh").addEventListener("click", load);
  $("#sync").addEventListener("click", async () => { const button=$("#sync"); button.disabled=true; try { const result=await request("POST","sync"); toast(result.summary || "同步完成"); await load(); } catch(error){toast(error.message,"error");} finally{button.disabled=false;} });
  $("#saveSettings").addEventListener("click", () => post("settings/save", { enabled:$("#enabled").checked, sync_enabled:$("#syncEnabled").checked, sync_interval_minutes:Number($("#syncInterval").value), pandascore_token:$("#token").value.trim(), games:[$("#gameLol").checked?"lol":"",$("#gameValorant").checked?"valorant":""].filter(Boolean), tracked_competitions:$("#competitions").value.split(/\r?\n/).map((x)=>x.trim()).filter(Boolean) }, "设置已保存"));
  $("#addMatch").addEventListener("click", () => { const raw=$("#newStart").value; post("matches/add", { game:$("#newGame").value, competition:$("#newCompetition").value, team_a:$("#newTeamA").value, team_b:$("#newTeamB").value, start_time:raw ? raw.replace("T"," ") : "" }, "比赛已添加"); });
  $("#matchSearch").addEventListener("input", (event) => { state.query=event.target.value; render(); });
  $("#matchRows").addEventListener("click", (event) => { const button=event.target.closest("button[data-action]"); if(!button) return; const action=button.dataset.action; const labels={settle:"确认按所选队伍获胜结算？",refund:"确认全额退款？",close:"确认立即封盘？",hide:"确认从群内隐藏？",show:"确认恢复显示？"}; if(!window.confirm(labels[action] || "确认操作？")) return; post("matches/action", {action,match_id:button.dataset.match,team_id:button.dataset.team || ""}, "操作完成"); });
  load();
})();
