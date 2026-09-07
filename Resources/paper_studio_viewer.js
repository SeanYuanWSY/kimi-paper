// Native translation stays shared; annotations now enter the selected persistent conversation.
let kpComposeRevision = 0;
const kpStudioOpenCompose = openCompose;
openCompose = function(...args) {
  ++kpComposeRevision;
  kpStudioOpenCompose(...args);
  $('#kp-compose-status').textContent = '';
  $('#kp-compose-translation').hidden = true;
  $('#kp-compose-translation').textContent = '';
};
function kpComposeQuote() {
  const anchor = state.pendingAnchor || {};
  return anchor.text || anchor.exact || anchor.quote || '';
}
async function kpTranslateCompose() {
  if (state.composeSubmitting) return;
  const quote = kpComposeQuote();
  if (!quote) { $('#kp-compose-status').textContent = '请先在 PDF 中划选需要翻译的文字。'; return; }
  const revision = kpComposeRevision;
  const output = $('#kp-compose-translation');
  output.hidden = false; output.textContent = '正在翻译…';
  $('#kp-compose-status').textContent = '';
  $('#kp-compose-translate').disabled = true;
  try {
    const result = await kpNative({operation:'translate', text:quote});
    if (revision === kpComposeRevision) output.textContent = result.text;
  } catch (error) {
    if (revision === kpComposeRevision) output.textContent = error.message || String(error);
  } finally {
    $('#kp-compose-translate').disabled = state.composeSubmitting;
  }
}
submitCompose = async function(event) {
  event.preventDefault();
  if(state.composeSubmitting)return;
  const text=$('#compose-text').value.trim();
  if(!text)return;
  const revision=kpComposeRevision;
  $('#kp-compose-status').textContent = '正在发送到左侧 Kimi…';
  applyComposeSubmitting(true);
  $('#compose-submit').textContent = '正在发送…';
  try {
    const anchor=state.pendingAnchor||{};
    const quote=kpComposeQuote();
    const deferred=!globalThis.KP_DIRECT&&$('#kp-deferred').checked;
    await kpNative({operation:'sendAnnotation',quote,text,digest:anchor.pdf_digest||state.pdfDigest});
    if(revision===kpComposeRevision){$('#compose-dialog').close();clearPendingSelection();}
    $('#kp-reading-message').textContent=deferred?'批注已保存，可继续添加。':'已发送到左侧 Kimi，完成后会刷新 PDF。';
  }catch(error){if(revision===kpComposeRevision)$('#kp-compose-status').textContent=error.message||String(error);}
  finally{applyComposeSubmitting(false);$('#compose-submit').textContent='发送修改要求';}
};
document.addEventListener('DOMContentLoaded',()=>{
  // Keep navigation available without taking width away from the paper.
  document.head.append(h('style',{text:'.layout{position:relative;grid-template-columns:minmax(0,1fr)!important}#sidebar{position:absolute;right:0;top:0;bottom:0;width:min(320px,85%);z-index:30;box-shadow:-6px 0 24px #0002}.sidebar-tabs{font-size:12px}#kp-reading-status{flex-wrap:wrap}#sidebar-toggle-btn{margin-left:auto;white-space:nowrap;padding:5px 10px;border:1px solid #ddd;border-radius:6px;background:#fff;color:#444}'}));
  const sidebarToggle=$('#sidebar-toggle-btn');
  sidebarToggle.textContent='目录 / 批注';
  sidebarToggle.title='展开或收起目录、批注与编译信息';
  sidebarToggle.setAttribute('aria-controls','sidebar');
  $('#kp-reading-status').append(sidebarToggle);
  const updateSidebarLabel=()=>{
    const expanded=!$('.layout').classList.contains('sidebar-collapsed');
    sidebarToggle.textContent=expanded?'收起侧栏':'目录 / 批注';
    sidebarToggle.setAttribute('aria-expanded',String(expanded));
  };
  new MutationObserver(updateSidebarLabel).observe($('.layout'),{attributes:true,attributeFilter:['class']});
  setSidebarCollapsed(true);
  updateSidebarLabel();
  $('#pdf-pane').append(h('p',{id:'kp-empty-project',hidden:true,text:globalThis.KP_DIRECT?'尚未生成 PDF。请在左侧 Kimi 中起草或修复编译问题。':'正式稿尚未生成。请在左侧起草，查看预览后确认采纳。',style:{padding:'36px',color:'#62665d'}}));
  document.head.append(h('style',{text:'#pdf-viewer[hidden]{display:none!important} .topbar{display:none!important}#kp-model-rows,#kp-model-status,#compose-suggestion-details,#kp-translation{display:none!important}fieldset>button{display:none!important}fieldset{border:0;padding:8px 0}#kp-reading-status{display:flex;align-items:center;gap:8px;padding:8px 12px!important;background:#fafaf8;border-bottom:1px solid #eee}body{background:#f5f4f0}'}));
  $('#compose-title').textContent='翻译或修改选中段落';
  const legend=document.querySelector('fieldset legend');if(legend)legend.textContent='处理方式';
  const deferred=$('#kp-deferred');if(deferred.nextSibling)deferred.nextSibling.textContent=' 先保存，稍后一起发给 Kimi';
  if(globalThis.KP_DIRECT)deferred.closest('fieldset').hidden=true;
  $('#compose-anchor').after(h('button',{id:'kp-compose-translate',type:'button',text:'仅翻译选中文字',onclick:kpTranslateCompose}),
    h('div',{id:'kp-compose-translation',hidden:true,'aria-live':'polite',style:{whiteSpace:'pre-wrap',lineHeight:'1.7',maxHeight:'220px',overflow:'auto',margin:'12px 0'}}));
  $('#compose-text').before(h('p',{text:'需要修改论文时，在下方填写要求并发送给左侧 Kimi。仅翻译不会修改论文。'}));
  $('#compose-text').after(h('p',{id:'kp-compose-status',role:'status','aria-live':'polite',style:{whiteSpace:'pre-wrap',color:'#9c402d'}}));
  $('#compose-dialog').addEventListener('close',()=>{++kpComposeRevision;});
  $('#compose-dialog').addEventListener('cancel',event=>{if(state.composeSubmitting)event.preventDefault();});
  $('#compose-cancel').textContent='取消';
  $('#compose-text').placeholder='希望这段如何修改？直接写要求即可。';
  $('#compose-submit').textContent='发送修改要求';
  if(globalThis.KP_DIRECT)$('#kp-reading-message').textContent='划选文字可翻译或直接让 Kimi 修改';
  else $('#kp-reading-status').append(h('button',{text:'发送积攒批注',onclick:async()=>{try{await kpRequest('/kp/studio/send',{});$('#kp-reading-message').textContent='已发送到当前会话。';}catch(e){$('#kp-reading-message').textContent=e.message;}}}));
});

let kpStudioRefreshing=false;
setInterval(async()=>{
  if(kpStudioRefreshing||!state.paper)return;
  kpStudioRefreshing=true;
  try{const data=await kpRequest('/kp/paper-status',undefined,'GET');
    if(globalThis.KP_DIRECT&&data.error)$('#kp-reading-message').textContent=data.error+' 右侧仍显示上一次成功编译的 PDF。';
    $('#pdf-viewer').hidden=!data.digest;$('#kp-empty-project').hidden=!!data.digest;
    if(!data.digest){if(state.pdfDigest){kpClosePopup();await refreshPaper();}}
    else if(data.digest!==state.pdfDigest){const view=capturePdfView();await refreshPaper();await initializePdfViewer(view);}
  }catch(_){/* The native connection indicator handles transient failures. */}
  finally{kpStudioRefreshing=false;}
},1500);
