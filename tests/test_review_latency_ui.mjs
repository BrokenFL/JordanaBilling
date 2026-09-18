import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../app/jordana_invoice/static/review.js',import.meta.url),'utf8');
test('list preparation reconciles only once and retries after failure',async()=>{
 const start=source.indexOf('let reviewCalendarPreparation = null;');const end=source.indexOf('async function loadList()',start);
 let calls=0,fail=true;const context={api:async()=>{calls++;if(fail)throw Error('temporary');}};vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 await assert.rejects(vm.runInContext('prepareReviewCalendar()',context));fail=false;
 await Promise.all([vm.runInContext('prepareReviewCalendar()',context),vm.runInContext('prepareReviewCalendar()',context)]);
 await vm.runInContext('prepareReviewCalendar()',context);assert.equal(calls,2);
});
test('approval preparation failure clears busy state and permits retry',async()=>{
 const start=source.indexOf('async function save(approve) {');const end=source.indexOf('\n}',start)+2;
 const button={disabled:false,textContent:'Approve Session'};const state={selected:'fixture'};
 const context={state,approvalState:{},$:()=>button,clearReviewActionError(){},reviewOverlayCtrl:{beginPending(){},endPending(){}},resolveTypedSelections:async()=>{assert.equal(button.textContent,'Approving…');throw Error('Could not resolve client');},showReviewActionError(){},sanitizeUiErrorMessage:x=>x};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);await vm.runInContext('save(true)',context);
 assert.equal(context.approvalState.submitting,false);assert.equal(button.disabled,false);assert.equal(button.textContent,'Approve Session');
});
