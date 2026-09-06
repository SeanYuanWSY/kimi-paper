const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync(require('node:path').join(__dirname,'../../Resources/paper_studio_viewer.js'),'utf8').split('let kpStudioRefreshing=false;')[1];
(async()=>{
 let callback,data={digest:null},renders=[],refreshes=0;
 const nodes={'#pdf-viewer':{hidden:false},'#kp-empty-project':{hidden:true}};
 const state={paper:{},pdfDigest:'old-preview',layoutReady:false};
 const position={pageNumber:3};
 const context=vm.createContext({state,$:id=>nodes[id],setInterval:fn=>{callback=fn;},kpRequest:async()=>data,kpClosePopup(){},capturePdfView:()=>position,refreshPaper:async()=>{refreshes++;state.pdfDigest=data.digest;},initializePdfViewer:async view=>renders.push(view)});
 vm.runInContext('let kpStudioRefreshing=false;'+script,context);
 await callback();assert.equal(nodes['#pdf-viewer'].hidden,true,'An absent formal PDF must never display an old candidate');assert.equal(state.pdfDigest,null);
 data={digest:'first-draft'};await callback();assert.equal(nodes['#pdf-viewer'].hidden,false);assert.equal(renders.length,1,'First PDF must load even when layoutReady was false');assert.equal(renders[0],position,'Preview switching preserves the reader position');
 await callback();assert.equal(renders.length,1,'Unchanged versions must not reload the reader');
 console.log('PASS: empty formal draft isolation, first PDF, reading position and unchanged preview polling');
})().catch(e=>{console.error(e);process.exitCode=1;});
