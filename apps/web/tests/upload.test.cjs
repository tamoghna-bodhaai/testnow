const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const Module=require('node:module');const ts=require('typescript');
const filename=require('node:path').resolve(__dirname,'../app/upload-preflight.ts');const compiled=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;const instance=new Module(filename,module);instance._compile(compiled,filename);const {reviewUploads}=instance.exports;
test('preflight validates configured limits, formats, duplicates and ambiguous pairing',()=>{
const config={extensions:['pdf','docx'],max_file_bytes:100};
const result=reviewUploads([{name:'chapter.pdf',size:90},{name:'chapter.pdf',size:90},{name:'bad.txt',size:101}], [{name:'chapter_answers.pdf',size:20},{name:'chapter_solutions.pdf',size:20}],config);
assert.equal(result[0].errors[0],'Multiple matching answer files; select one');assert.equal(result[1].duplicate,true);assert.equal(result[2].errors.length,2);
});
test('single question and answer source have explicit fallback pairing',()=>{
const result=reviewUploads([{name:'questions.pdf',size:20}],[{name:'different-key.docx',size:30}],{extensions:['pdf','docx'],max_file_bytes:100});assert.equal(result[0].paired,'different-key.docx');assert.equal(result[0].errors.length,0);
});
