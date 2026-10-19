const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const Module=require('node:module');
const ts=require('typescript');
const filename=require('node:path').resolve(__dirname,'../app/bank-navigation.ts');
const compiled=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const instance=new Module(filename,module);instance._compile(compiled,filename);
const {LatestRequest,bankUrl,readBankLocation}=instance.exports;

test('rapid A to B to C selection rejects out-of-order results and aborts superseded requests',async()=>{
  const gate=new LatestRequest();const applied=[];const releases=[];
  const requests=['A','B','C'].map(key=>{const request=gate.start(key);const promise=new Promise(resolve=>releases.push(()=>resolve(key))).then(value=>{if(request.current())applied.push(value)});return {request,promise};});
  assert.equal(requests[0].request.signal.aborted,true);assert.equal(requests[1].request.signal.aborted,true);
  releases[2]();releases[0]();releases[1]();await Promise.all(requests.map(value=>value.promise));
  assert.deepEqual(applied,['C']);assert.equal(gate.key,'C');
});
test('a failed latest selection cannot reactivate previous bank mutation context',()=>{
  const gate=new LatestRequest();const old=gate.start('A');const current=gate.start('B');
  assert.equal(old.current(),false);assert.equal(current.current(),true);assert.equal(gate.key,'B');
  gate.cancel();assert.equal(current.current(),false);assert.equal(gate.key,null);
});
test('bank, document and stable question identity round trip in an addressable route',()=>{
  const context={bankId:'bank 1',documentId:'doc/2',questionId:'stable-id-51'};
  assert.deepEqual(readBankLocation(bankUrl(context),''),context);
  assert.deepEqual(readBankLocation('/','#bank=legacy&document=source&question=q51'),{bankId:'legacy',documentId:'source',questionId:'q51'});
  assert.equal(readBankLocation('/admin/banks/%broken',''),null);
  assert.equal(readBankLocation('/',''),null);
});

test('unlinked legacy question identity is addressable without inventing a source document',()=>{
 const context={bankId:'bank',questionId:'legacy-orphan'};
 const restored=readBankLocation(bankUrl(context),'');assert.equal(restored.questionId,'legacy-orphan');assert.equal(restored.documentId,undefined);
});
