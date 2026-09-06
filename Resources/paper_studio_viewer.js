// Native translation stays shared; annotations now enter the selected persistent conversation.
submitCompose = async function(event) {
  event.preventDefault();
  if(state.composeSubmitting)return;
  const text=$('#compose-text').value.trim();
  if(!text)return;
  applyComposeSubmitting(true);
  try {
    const anchor=state.pendingAnchor||{};
    const quote=anchor.text||anchor.exact||anchor.quote||JSON.stringify(anchor);
    const deferred=!globalThis.KP_DIRECT&&$('#kp-deferred').checked;
    await kpRequest('/kp/studio/note',{quote,text,digest:state.pdfDigest,send:!deferred});
    $('#compose-dialog').close();clearPendingSelection();
    $('#kp-reading-message').textContent=deferred?'批注已保存，可继续添加。':'已发送到左侧 Kimi，完成后会刷新 PDF。';
  }catch(error){$('#kp-reading-message').textContent=error.message;}
  finally{applyComposeSubmitting(false);$('#compose-submit').textContent='提交批注';}
};
document.addEventListener('DOMContentLoaded',()=>{
  $('#pdf-pane').append(h('p',{id:'kp-empty-project',hidden:true,text:globalThis.KP_DIRECT?'尚未生成 PDF。请在左侧 Kimi 中起草或修复编译问题。':'正式稿尚未生成。请在左侧起草，查看预览后确认采纳。',style:{padding:'36px',color:'#62665d'}}));
  document.head.append(h('style',{text:'#pdf-viewer[hidden]{display:none!important} .topbar{display:none!important}#kp-model-rows,#kp-model-status,#compose-suggestion-details,#kp-translation{display:none!important}fieldset>button{display:none!important}fieldset{border:0;padding:8px 0}#kp-reading-status{display:flex;align-items:center;gap:8px;padding:8px 12px!important;background:#fafaf8;border-bottom:1px solid #eee}body{background:#f5f4f0}'}));
  $('#compose-title').textContent=globalThis.KP_DIRECT?'让 Kimi 直接修改这段':'发给当前 Kimi 会话';
  const legend=document.querySelector('fieldset legend');if(legend)legend.textContent='处理方式';
  const deferred=$('#kp-deferred');if(deferred.nextSibling)deferred.nextSibling.textContent=' 先保存，稍后一起发给 Kimi';
  if(globalThis.KP_DIRECT){deferred.parentElement.hidden=true;const translationToggle=$('#kp-selection-enabled');if(translationToggle)translationToggle.parentElement.hidden=true;}
  $('#compose-cancel').textContent='取消';
  $('#compose-text').placeholder='希望这段如何修改？直接写要求即可。';
  $('#compose-submit').textContent=globalThis.KP_DIRECT?'开始修改':'提交批注';
  if(globalThis.KP_DIRECT)$('#kp-reading-message').textContent='划选文字可翻译或直接让 Kimi 修改';
  else $('#kp-reading-status').append(h('button',{text:'发送积攒批注',onclick:async()=>{try{await kpRequest('/kp/studio/send',{});$('#kp-reading-message').textContent='已发送到当前会话。';}catch(e){$('#kp-reading-message').textContent=e.message;}}}));
});

let kpStudioRefreshing=false;
setInterval(async()=>{
  if(kpStudioRefreshing||!state.paper)return;
  kpStudioRefreshing=true;
  try{const data=await kpRequest('/kp/studio/status',undefined,'GET');
    if(globalThis.KP_DIRECT&&data.error)$('#kp-reading-message').textContent=data.error+' 右侧仍显示上一次成功编译的 PDF。';
    $('#pdf-viewer').hidden=!data.digest;$('#kp-empty-project').hidden=!!data.digest;
    if(!data.digest){if(state.pdfDigest){kpClosePopup();await refreshPaper();}}
    else if(data.digest!==state.pdfDigest){const view=capturePdfView();await refreshPaper();await initializePdfViewer(view);}
  }catch(_){/* The native connection indicator handles transient failures. */}
  finally{kpStudioRefreshing=false;}
},1500);
