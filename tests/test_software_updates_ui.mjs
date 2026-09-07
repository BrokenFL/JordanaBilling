import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../app/jordana_invoice/static/js/software_updates.js',import.meta.url),'utf8');
const tick=()=>new Promise(r=>setImmediate(r));
function setup({confirm=true, status='idle', failInstall=false}={}) {
 const nodes=new Map(), calls=[], storage=new Map(), timers=[];
 const document={getElementById(id){if(!nodes.has(id))nodes.set(id,{hidden:true,textContent:''});return nodes.get(id);}};
 const offer={version:'0.1.0.post37',release_label:'v0.1.0-test.37',notes:'<b>plain text only</b>'};
 const context={document,window:{JordanaAPI:{async api(path,options){calls.push({path,options});if(path.endsWith('/install')&&failInstall)throw Error('Update failed');return {offer,installation:{state:status}};}}},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},confirm:()=>confirm,setInterval:()=>{},setTimeout:fn=>timers.push(fn),location:{reload(){}},Date,Promise};
 vm.runInNewContext(source,context);
 return {nodes,calls,storage};
}
test('launch shows promoted update without installing',async()=>{
 const x=setup();await tick();assert.equal(x.nodes.get('softwareUpdateDetails').hidden,false);assert.equal(x.nodes.get('softwareUpdateNotes').textContent,'<b>plain text only</b>');assert.equal(x.calls.length,1);
 x.nodes.get('laterSoftwareUpdate').onclick();assert.equal(x.nodes.get('softwareUpdateDetails').hidden,true);assert.equal(x.storage.get('jordana-update-later'),'0.1.0.post37');
});
test('cancel does not install; confirming posts only the version',async()=>{
 const no=setup({confirm:false});await tick();await no.nodes.get('installSoftwareUpdate').onclick();assert.equal(no.calls.length,1);
 const yes=setup();await tick();await yes.nodes.get('installSoftwareUpdate').onclick();assert.equal(yes.calls[1].path,'/api/updates/install');assert.deepEqual(JSON.parse(yes.calls[1].options.body),{version:'0.1.0.post37'});
});
test('failure permits retry and shows the message',async()=>{
 const x=setup({failInstall:true});await tick();await x.nodes.get('installSoftwareUpdate').onclick();assert.equal(x.nodes.get('installSoftwareUpdate').hidden,false);assert.equal(x.nodes.get('softwareUpdateMessage').textContent,'Update failed');
});
