// Versioned integration appended to the pinned upstream viewer module.
let kpModels = [], kpRows = [], kpSubmission = null;
const kpRequest = async (path, body, method = "POST") => {
  const response = await fetch(path, {method, headers: {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
};
const kpNative = (body) => {
  if (!window.webkit?.messageHandlers?.paper) return Promise.reject(new Error("请在 Kimi Paper 应用中使用翻译设置。"));
  return window.webkit.messageHandlers.paper.postMessage(body);
};

function kpRow(value) {
  const tool = h("select", {"aria-label": "执行工具"}, h("option", {value: "kimi", text: "Kimi Code"}));
  const model = h("select", {"aria-label": "模型", style: {maxWidth: "310px"}});
  const processing = h("select", {"aria-label": "处理方式"},
    h("option", {value: "direct", text: "直接修改 · 润色与改写"}),
    h("option", {value: "research", text: "深入处理 · 查资料与工具"}));
  processing.value = value?.processing === "research" ? "research" : "direct";
  tool.value = "kimi";
  const populate = () => {
    model.replaceChildren(...kpModels.filter(v => v.tool === tool.value).map(v => h("option", {value: v.model, text: v.name})));
    const preferred = kpModels.find(v => v.tool === tool.value && v.default);
    if (preferred) model.value = preferred.model;
  };
  populate();
  if (value?.model && kpModels.some(v => v.tool === tool.value && v.model === value.model)) model.value = value.model;
  tool.addEventListener("change", populate);
  const row = h("div", {style: {display: "flex", flexWrap: "wrap", gap: "8px", margin: "8px 0"}}, tool, model, processing);
  const item = {row, tool, model, processing};
  row.append(h("button", {type: "button", text: "移除", onclick: () => {kpRows = kpRows.filter(v => v !== item); row.remove();}}));
  kpRows.push(item);
  $("#kp-model-rows").append(row);
}

const kpOriginalCompose = openCompose;
openCompose = function(...args) {kpSubmission = crypto.randomUUID(); kpOriginalCompose(...args);};
const kpOriginalJumpToComment = jumpToComment;
jumpToComment = async function(commentId) {
  const comment = state.comments.find(item => item.id === commentId);
  if (comment) focusComment(comment);
  return kpOriginalJumpToComment(commentId);
};
let kpSuggestionComment = null, kpSuggestionTasks = [], kpSuggestionBusy = false;
const kpOriginalFocusComment = focusComment;
focusComment = function(comment) {
  kpOriginalFocusComment(comment);
  kpSuggestionComment = comment.id;
  kpClosePopup();
  const popup = $("#kp-suggestion-popup");
  if (popup) {
    popup.hidden = false;
    $("#kp-suggestion-feedback").value = "";
    $("#kp-suggestion-versions").replaceChildren();
    $("#kp-suggestion-versions").dataset.ids = "";
    kpLoadSuggestions();
  }
};

async function kpLoadSuggestions() {
  const commentId = kpSuggestionComment;
  if (!commentId || kpSuggestionBusy) return;
  kpSuggestionBusy = true;
  try {
    const data = await kpRequest("/kp/tasks", undefined, "GET");
    if (commentId !== kpSuggestionComment) return;
    const tasks = data.tasks.filter(t => t.comment.id === commentId).sort((a,b) => b.created-a.created);
    kpSuggestionTasks = tasks;
    const versions = $("#kp-suggestion-versions"), selected = versions.value;
    const ids = tasks.map(t=>t.id).join(",");
    if (versions.dataset.ids !== ids) {
      versions.replaceChildren(...tasks.map((t,i)=>h("option", {value:t.id, text:`第 ${tasks.length-i} 版 · ${t.model}`})));
      if (tasks.some(t=>t.id===selected)) versions.value = selected;
      versions.dataset.ids = ids;
    }
    kpRenderSuggestion();
  } catch (error) {$("#kp-suggestion-status").textContent = error.message;}
  finally {kpSuggestionBusy = false;}
}

function kpRenderSuggestion() {
  const task = kpSuggestionTasks.find(t=>t.id === $("#kp-suggestion-versions").value);
  $("#kp-suggestion-status").textContent = task?.message || "此批注还没有修改建议。";
  $("#kp-suggestion-text").textContent = task ? (task.explanation || "") + "\n" + (task.changes || []).map(c=>c.diff).join("\n").slice(0,2000) : "";
  $("#kp-suggestion-apply").disabled = !task || task.status !== "ready" || task.commentChanged;
  $("#kp-suggestion-revise").disabled = !task || ["running","queued","waiting","applying","draft"].includes(task.status);
  $("#kp-suggestion-details").disabled = !task;
}

async function kpSuggestionAction(action) {
  const id = $("#kp-suggestion-versions").value;
  if (!id) return;
  const task = kpSuggestionTasks.find(t=>t.id===id);
  try {
    $("#kp-suggestion-apply").disabled = true;
    $("#kp-suggestion-revise").disabled = true;
    if (action === "focus") await kpRequest("/kp/focus", {id});
    else {
      if (action === "retry") await kpNative({operation:"prepareAgents"});
      await kpRequest(`/kp/tasks/${id}/${action}`, {instruction:$("#kp-suggestion-feedback").value});
      if (action === "retry") {
        $("#kp-suggestion-feedback").value = "";
        $("#kp-suggestion-versions").value = "";
      }
      await refreshComments();
    }
    await kpLoadSuggestions();
  } catch (error) {$("#kp-suggestion-status").textContent = error.message; kpRenderSuggestion(); $("#kp-suggestion-status").textContent = error.message;}
}
submitCompose = async function(event) {
  event.preventDefault();
  if (state.composeSubmitting) return;
  const text = $("#compose-text").value.trim();
  if (!text || !state.pendingAnchor) return;
  const selections = kpRows.map(v => ({tool: "kimi", model: v.model.value, processing: v.processing.value}));
  if (!selections.length || selections.some(v => !v.model)) throw new Error("请选择至少一个可用的模型。");
  const deferred = $("#kp-deferred").checked;
  const body = {anchor: state.pendingAnchor, text, kimi_paper: {submission: kpSubmission || (kpSubmission = crypto.randomUUID()), selections, deferred}};
  const old = $("#compose-suggestion-old").value.trim(), replacement = $("#compose-suggestion-new").value.trim();
  if (old && replacement) body.suggestion = {old, new: replacement};
  applyComposeSubmitting(true);
  try {
    if (!deferred) await kpNative({operation: "prepareAgents"});
    await kpRequest("/comments", body);
    $("#compose-dialog").close();
    clearPendingSelection();
    await refreshComments();
    $("#kp-reading-message").textContent = deferred ? "批注已保存；在左侧点击统一处理。" : "已交给后台处理，你可以继续阅读。";
  } finally {applyComposeSubmitting(false); $("#compose-submit").textContent = "开始生成候选";}
};

let kpTranslationEnabled = false, kpReadKey = "", kpTranslateRevision = 0, kpScrollTimer, kpLastText = "";
let kpSelectionEnabled = sessionStorage.getItem("kp.selection-enabled") !== "false", kpSelectionScope = null, kpSelectionUnsubscribe = [];
let kpPopupRevision = 0, kpPopupTimer, kpPopupBusy = false, kpPopupQueued = null, kpPopupText = "";
let kpPopupPoint = {x: 100, y: 100}, kpPopupDigest = null;

function kpClosePopup() {
  ++kpPopupRevision; clearTimeout(kpPopupTimer); kpPopupQueued = null;
  const popup = $("#kp-selection-translation");
  if (popup) popup.hidden = true;
}

function kpPositionPopup() {
  const popup = $("#kp-selection-translation");
  const width = Math.min(360, window.innerWidth - 24);
  popup.style.width = `${width}px`;
  const height = Math.min(330, window.innerHeight - 24);
  popup.style.maxHeight = `${height}px`;
  const x = Math.max(12, Math.min(kpPopupPoint.x + 16, window.innerWidth - width - 12));
  let y = kpPopupPoint.y + 36;
  if (y + height > window.innerHeight - 12) y = kpPopupPoint.y - height - 16;
  popup.style.left = `${x}px`;
  popup.style.top = `${Math.max(12, Math.min(y, window.innerHeight - height - 12))}px`;
}

async function kpDrainPopup() {
  if (kpPopupBusy) return;
  kpPopupBusy = true;
  try {
    while (kpPopupQueued) {
      const item = kpPopupQueued; kpPopupQueued = null;
      try {
        const result = await kpNative({operation: "translate", text: item.text});
        if (item.revision === kpPopupRevision) $("#kp-selection-output").textContent = result.text;
      } catch (error) {
        if (item.revision === kpPopupRevision) $("#kp-selection-output").textContent = error.message || String(error);
      }
    }
  } finally {kpPopupBusy = false;}
}

async function kpShowSelection() {
  if (!kpSelectionEnabled || !state.selection) return;
  const revision = ++kpPopupRevision, scope = state.selection, digest = state.pdfDigest;
  clearTimeout(kpScrollTimer); ++kpTranslateRevision;
  try {
    const lines = await scope.getSelectedText().toPromise();
    const text = lines.join(" ").trim();
    if (revision !== kpPopupRevision || scope !== state.selection || digest !== state.pdfDigest) return;
    if (!text) {kpClosePopup(); return;}
    kpPopupText = text;
    $("#kp-selection-translation").hidden = false;
    kpPositionPopup();
    $("#kp-selection-output").textContent = "正在翻译…";
    kpPopupQueued = {text, revision};
    await kpDrainPopup();
  } catch (error) {
    if (revision === kpPopupRevision) {
      $("#kp-selection-translation").hidden = false; kpPositionPopup();
      $("#kp-selection-output").textContent = error.message || String(error);
    }
  }
}

function kpBindSelection() {
  if (kpPopupDigest !== state.pdfDigest) {kpClosePopup(); kpPopupDigest = state.pdfDigest;}
  if (state.selection === kpSelectionScope) return;
  kpSelectionUnsubscribe.forEach(fn => fn()); kpSelectionUnsubscribe = [];
  kpClosePopup(); kpSelectionScope = state.selection;
  if (!kpSelectionScope) return;
  const root = state.viewer.shadowRoot;
  const pointer = event => {kpPopupPoint = {x: event.clientX, y: event.clientY};};
  const scroll = () => kpClosePopup();
  root.addEventListener("pointerup", pointer, true);
  root.addEventListener("scroll", scroll, true);
  kpSelectionUnsubscribe.push(() => {root.removeEventListener("pointerup", pointer, true); root.removeEventListener("scroll", scroll, true);});
  kpSelectionUnsubscribe.push(kpSelectionScope.onBeginSelection(kpClosePopup));
  kpSelectionUnsubscribe.push(kpSelectionScope.onEndSelection(() => {
    clearTimeout(kpPopupTimer);
    kpPopupTimer = setTimeout(kpShowSelection, 180);
  }));
  kpSelectionUnsubscribe.push(kpSelectionScope.onSelectionChange(() => {
    if (!kpSelectionScope.getFormattedSelection().length) kpClosePopup();
  }));
}

async function kpTranslate(text, manual = false) {
  if (!text || (!manual && !kpTranslationEnabled)) return;
  const revision = ++kpTranslateRevision;
  kpLastText = text;
  const output = $("#kp-translation-output");
  output.textContent = "正在翻译…";
  try {
    const result = await kpNative({operation: "translate", text});
    if (revision === kpTranslateRevision && (manual || kpTranslationEnabled)) output.textContent = result.text;
  } catch (error) {
    if (revision === kpTranslateRevision) output.textContent = error.message || String(error);
  }
}

async function kpReadVisible(key, pages, digest, revision) {
  try {
    const result = await kpRequest("/kp/visible", {pages, digest});
    if (kpReadKey === key && kpTranslationEnabled && revision === kpTranslateRevision) await kpTranslate(result.text);
  } catch (error) {if (kpReadKey === key && revision === kpTranslateRevision) $("#kp-translation-output").textContent = error.message;}
}

function kpVisiblePages() {
  return state.scroll?.getMetrics().pageVisibilityMetrics.map(p => ({page: p.pageNumber,
    rect: [p.original.pageX, p.original.pageY, p.original.pageX + p.original.visibleWidth, p.original.pageY + p.original.visibleHeight]})) || [];
}

document.addEventListener("DOMContentLoaded", async () => {
  const suggestions = h("section", {id:"kp-suggestion-popup", role:"region", "aria-label":"批注修改建议", hidden:true,
    style:{position:"fixed",right:"18px",top:"100px",width:"min(420px, calc(100vw - 36px))",maxHeight:"calc(100vh - 125px)",overflow:"auto",zIndex:"9999",background:"white",padding:"16px",border:"1px solid #ddd",borderRadius:"12px",boxShadow:"0 8px 32px #0003"}},
    h("strong",{text:"此处的修改建议"}),
    h("button",{type:"button",text:"关闭",style:{float:"right"},onclick:()=>{suggestions.hidden=true;kpSuggestionComment=null;}}),
    h("select",{id:"kp-suggestion-versions","aria-label":"建议版本",onchange:kpRenderSuggestion,style:{display:"block",maxWidth:"100%",margin:"12px 0"}}),
    h("p",{id:"kp-suggestion-status",role:"status"}),
    h("pre",{id:"kp-suggestion-text",style:{whiteSpace:"pre-wrap",overflowWrap:"anywhere",maxHeight:"220px",overflow:"auto",fontSize:"12px"}}),
    h("textarea",{id:"kp-suggestion-feedback","aria-label":"给此建议追加意见",placeholder:"还想怎么改？填写意见后生成下一版",rows:3,style:{width:"100%",boxSizing:"border-box"}}),
    h("div",{style:{display:"flex",gap:"8px",flexWrap:"wrap",marginTop:"10px"}},
      h("button",{id:"kp-suggestion-apply",type:"button",text:"采纳写入",onclick:()=>kpSuggestionAction("apply")}),
      h("button",{id:"kp-suggestion-revise",type:"button",text:"按意见再改",onclick:()=>kpSuggestionAction("retry")}),
      h("button",{id:"kp-suggestion-details",type:"button",text:"左侧查看完整差异",onclick:()=>kpSuggestionAction("focus")})));
  document.body.append(suggestions);
  setInterval(()=>{if(!suggestions.hidden)kpLoadSuggestions();},1500);
  const popup = h("section", {id: "kp-selection-translation", role: "region", "aria-label": "选区翻译", hidden: true,
    style: {position: "fixed", zIndex: "10000", boxSizing: "border-box", padding: "12px 14px", border: "1px solid #d9dce6", borderRadius: "12px", background: "#fff", color: "#20232c", boxShadow: "0 8px 32px #0003", display: "flex", flexDirection: "column", gap: "10px"}},
    h("div", {style: {display: "flex", alignItems: "center", gap: "8px"}}, h("strong", {text: "选区翻译", style: {flex: "1"}}),
      h("button", {type: "button", text: "写批注", onclick: () => {kpClosePopup(); openTextSelectionCompose();}}),
      h("button", {type: "button", text: "重试", onclick: () => {
        const revision = ++kpPopupRevision;
        $("#kp-selection-output").textContent = "正在翻译…";
        kpPopupQueued = {text: kpPopupText, revision}; kpDrainPopup();
      }}),
      h("button", {type: "button", text: "关闭", "aria-label": "关闭选区翻译", onclick: kpClosePopup})),
    h("div", {id: "kp-selection-output", "aria-live": "polite", style: {overflow: "auto", whiteSpace: "pre-wrap", lineHeight: "1.7", fontSize: "14px", minHeight: "32px"}}));
  // Keep the hidden attribute authoritative despite the popup's flex layout.
  document.head.append(h("style", {text: "#kp-selection-translation[hidden]{display:none!important}"}));
  document.body.append(popup);
  document.addEventListener("pointerdown", event => {
    if (!event.composedPath().includes(popup)) kpClosePopup();
  }, true);
  document.addEventListener("keydown", event => {if (event.key === "Escape") kpClosePopup();});
  window.addEventListener("resize", kpClosePopup);
  const settings = h("fieldset", {}, h("legend", {text: "Kimi 修改建议"}),
    h("label", {}, h("input", {type: "checkbox", id: "kp-deferred"}), " 先保存批注，之后统一顺序处理"), h("div", {id: "kp-model-rows"}),
    h("button", {type: "button", text: "＋ 添加一个候选", onclick: () => {if (kpRows.length < 6) kpRow();}}),
    h("p", {id: "kp-model-status", text: "正在读取可用模型…"}));
  $("#compose-suggestion-details").before(settings);
  $("#compose-title").textContent = "希望如何修改？";
  $("#compose-text").placeholder = "写下要求，提交后后台立即生成候选，确认前不会修改正文。";
  $("#compose-submit").textContent = "开始生成候选";
  const translation = h("details", {id: "kp-translation", style: {background: "#f6f7fb", padding: "8px 14px", maxHeight: "240px", overflow: "auto"}},
    h("summary", {text: "阅读翻译"}),
    h("label", {}, h("input", {type: "checkbox", id: "kp-translation-enabled"}), " 滚动停下后自动翻译"),
    h("button", {type: "button", text: "翻译选中文字", onclick: async () => {
      try {
        const lines = state.selection ? await state.selection.getSelectedText().toPromise() : [];
        const text = lines.join(" ").trim() || window.getSelection()?.toString();
        if (!text) throw new Error("请先在论文中划选文字。");
        clearTimeout(kpScrollTimer);
        kpReadKey = JSON.stringify([state.pdfDigest, kpVisiblePages()]);
        await kpTranslate(text, true);
      } catch (error) {$("#kp-translation-output").textContent = error.message;}
    }}),
    h("button", {type: "button", text: "重试", onclick: () => kpTranslate(kpLastText, true)}),
    h("p", {text: "接口与模型可在左侧「修改任务 → 翻译设置」配置。"}),
    h("div", {id: "kp-translation-output", "aria-live": "polite", style: {whiteSpace: "pre-wrap", lineHeight: "1.8"}}));
  $("#app").insertBefore(translation, $(".layout"));
  $(".topbar").after(h("div", {id: "kp-reading-status", role: "status", style: {padding: "3px 14px", fontSize: "12px"}}));
  $("#kp-reading-status").append(h("label", {}, h("input", {id: "kp-selection-enabled", type: "checkbox", checked: kpSelectionEnabled}), " 划选后自动翻译"), h("span", {id: "kp-reading-message", style: {marginLeft: "12px"}}));
  $("#kp-selection-enabled").addEventListener("change", event => {kpSelectionEnabled = event.target.checked; sessionStorage.setItem("kp.selection-enabled", String(kpSelectionEnabled)); kpClosePopup();});
  $("#kp-translation-enabled").addEventListener("change", e => {
    kpTranslationEnabled = e.target.checked;
    ++kpTranslateRevision; kpReadKey = ""; clearTimeout(kpScrollTimer);
  });
  try {const config = await kpNative({operation: "settings"}); kpTranslationEnabled = config.enabled; $("#kp-translation-enabled").checked = config.enabled; translation.open = config.enabled;} catch (_) { /* Browser preview has no native translation transport. */ }
  const loadModels = async () => {
    try {
      const data = await kpRequest("/kp/models", undefined, "GET");
      if (data.loading) {setTimeout(loadModels, 1000); return;}
      kpModels = data.models;
      $("#kp-model-status").textContent = Object.entries(data.errors).map(([tool, error]) => `${tool}：${error}`).join("；") || "生成候选后由你确认写入。";
      const prefs = await kpRequest("/kp/preferences", undefined, "GET");
      $("#kp-model-rows").replaceChildren(); kpRows = [];
      const choices = prefs.selections.length ? prefs.selections : [kpModels.find(v => v.tool === "kimi" && v.default) || kpModels[0]];
      choices.filter(Boolean).forEach(kpRow);
    } catch (error) {$("#kp-model-status").textContent = error.message;}
  };
  settings.append(h("button", {type: "button", text: "刷新模型", onclick: async () => {
    try {await kpRequest("/kp/models/refresh", {}); await loadModels();}
    catch (error) {$("#kp-model-status").textContent = error.message;}
  }}));
  if (!window.KP_STUDIO) loadModels();
  setInterval(() => {
    kpBindSelection();
    if (!kpTranslationEnabled || !state.scroll || !state.layoutReady) return;
    const pages = kpVisiblePages();
    const digest = state.pdfDigest;
    const key = JSON.stringify([digest, pages]);
    if (key === kpReadKey) return;
    kpReadKey = key; const revision = ++kpTranslateRevision; clearTimeout(kpScrollTimer);
    kpScrollTimer = setTimeout(() => kpReadVisible(key, pages, digest, revision), 800);
  }, 200);
});
