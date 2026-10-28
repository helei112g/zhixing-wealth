/* Exercise the actual saved-note renderer in a bounded child process: a parser
 * regression must fail this command, not lock up the test runner indefinitely. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawnSync}=require('node:child_process'),{createRequire}=require('node:module');
if(!process.argv.includes('--child')){
 const result=spawnSync(process.execPath,[__filename,'--child'],{encoding:'utf8',timeout:15000});
 process.stdout.write(result.stdout||'');process.stderr.write(result.stderr||'');
 assert.ifError(result.error);assert.equal(result.status,0,'Markdown renderer must terminate and pass all assertions');
}else{
 const frontend=path.resolve(__dirname,'../frontend'),req=createRequire(path.join(frontend,'package.json'));
 const ts=req('typescript'),React=req('react'),{renderToStaticMarkup}=req('react-dom/server');
 const source=fs.readFileSync(path.join(frontend,'src/note_markdown.tsx'),'utf8');
 const output=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
 const mod={exports:{}};new Function('require','module','exports',output)(req,mod,mod.exports);
 let renders=0;
 const render=body=>{renders++;return renderToStaticMarkup(React.createElement(mod.exports.NoteMarkdown,{body}));};
 for(const marker of ['# ','## ','###### ','#\t','1. ','12.\t','  1. ','#   ','1. \t']){
  assert.equal(render(marker),'<div class="note-markdown"><p>'+marker+'</p></div>','incomplete marker stays literal');
  assert.equal(render('正文\n'+marker),'<div class="note-markdown"><p>正文\n'+marker+'</p></div>');
  assert.equal(render(marker+'\n\n## 后续'),'<div class="note-markdown"><p>'+marker+'</p><h2>后续</h2></div>');
 }
 const document='# 标题\n\n一段 **加粗** 文字。\n\n## 观察\n\n1. 第一项\n2. 第二项\n\n# \n\n1. ';
 for(let n=0;n<=document.length;n++)render(document.slice(0,n)); // Every intermediate typing/deleting state.
 assert.equal(render('正文\n# 标题\n1. 第一项\n2. 第二项\n1. '),'<div class="note-markdown"><p>正文</p><h1>标题</h1><ol start="1"><li>第一项</li><li>第二项</li></ol><p>1. </p></div>');
 assert.equal(render('## 标题\r\n\r\n**重点**\r3. 第三项'),'<div class="note-markdown"><h2>标题</h2><p><strong>重点</strong></p><ol start="3"><li>第三项</li></ol></div>');
 assert.equal(render(' \n\t\r\n'),'<div class="note-markdown"></div>');
 assert.equal(render('<script>alert(1)</script>'),'<div class="note-markdown"><p>&lt;script&gt;alert(1)&lt;/script&gt;</p></div>');
 assert.ok(!render('[link](javascript:alert(1)) **unfinished').includes('<a'));
 // Deterministic combinations of incomplete and complete blocks, including deletion states.
 const pieces=['# ','1. ','\t','文字','**','\n','\r\n','### 标题','12. 条目'];
 for(let i=0;i<pieces.length;i++)for(let j=0;j<pieces.length;j++)render(pieces[i]+'\n'+pieces[j]);
 assert.ok(render('文'.repeat(200000)).includes('文'.repeat(200000)));
 console.log('PASS saved-note rendering: '+renders+' inputs; incomplete markers, typing prefixes, blocks, literal HTML and maximum body length');
}
