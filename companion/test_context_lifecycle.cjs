const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const code=fs.readFileSync(process.argv[2]||__dirname+'/extension/content.js','utf8');
function fixture(mode) {
 let heartbeat,listener,cleared=0,removed=0,sends=0,clicks=0;
 const editor={textContent:'',getClientRects:()=>[{}],focus(){},closest:()=>({querySelectorAll:()=>[button]})};
 const button={getClientRects:()=>[{}],getAttribute:()=> 'Enviar',disabled:false,click(){clicks++;}};
 const runtime={id:'test',onMessage:{addListener:f=>listener=f,removeListener(){removed++;}},sendMessage(){sends++;if(mode==='sync')throw Error('Extension context invalidated.');if(mode==='async')return Promise.reject(Error('Extension context invalidated.'));return Promise.resolve({});}};
 const document={addEventListener(){},removeEventListener(){removed++;},querySelectorAll(selector){if(selector.startsWith('[contenteditable'))return [editor];if(selector.includes('role="status"'))return [{textContent:'Resposta concluída'}];return [];},execCommand(_,__,text){editor.textContent=text;return true;}};
 vm.runInNewContext(code,{document,chrome:{runtime},crypto:{randomUUID:()=> 'test-document-123456'},location:{href:'https://chatgpt.com/c/test'},getComputedStyle:()=>({visibility:'visible'}),setInterval:f=>{heartbeat=f;return 7;},clearInterval:id=>{assert.equal(id,7);cleared++;},setTimeout:f=>{if(mode==='mid-send')runtime.id=undefined;f();}});
 return {tick:()=>heartbeat(),runtime,send:()=>new Promise(resolve=>listener({kind:'send',url:'https://chatgpt.com/c/test',text:'test'},{id:'test'},resolve)),stats:()=>({cleared,removed,sends,clicks})};
}
(async()=>{
 for(const mode of ['sync','async']){const f=fixture(mode);await f.tick();await f.tick();assert.equal(f.stats().cleared,1);assert.equal(f.stats().sends,1);assert.equal(f.stats().removed,2);}
 const missing=fixture('none');missing.runtime.id=undefined;await missing.tick();assert.equal(missing.stats().sends,0);assert.equal(missing.stats().cleared,1);
 const mid=fixture('mid-send');const r=await mid.send();assert.equal(r.state,'uncertain');assert.equal(mid.stats().clicks,0);
 console.log('PASS lifecycle: sync throw, rejected promise, missing context, timer cleanup and no click after reload');
})().catch(e=>{console.error(e);process.exitCode=1;});
