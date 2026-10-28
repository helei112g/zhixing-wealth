/* Exercise the production formatter; rounded display never changes API inputs. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{createRequire}=require('node:module');
const root=path.resolve(__dirname,'../frontend'),req=createRequire(path.join(root,'package.json')),ts=req('typescript');
const compiled=ts.transpileModule(fs.readFileSync(path.join(root,'src/api.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const mod={exports:{}};new Function('module','exports',compiled)(mod,mod.exports);
const {readableMoney,tone,exactMoney}=mod.exports;
const cases=[[null,'—'],['','—'],['invalid','—'],[0,'0.00'],['9999','9,999.00'],['12345','1.23 万'],['123456','12.35 万'],['1234567','123.46 万'],['12345678','1,234.57 万'],['100000000','1.00 亿'],['99999999','1.00 亿'],['-123456','-12.35 万'],['-0.001','0.00']];
for(const [input,want] of cases)assert.equal(readableMoney(input),want);
assert.equal(readableMoney('123456',true),'+12.35 万');assert.equal(readableMoney('-123456',true),'-12.35 万');
assert.equal(tone('100'),'up');assert.equal(tone('-100'),'down');assert.equal(tone(0),'');assert.equal(tone(null),'');
const precise='123456789.123456';assert.equal(readableMoney(precise),'1.23 亿');assert.equal(exactMoney(precise),'123,456,789.123456');
console.log('PASS amount display: '+(cases.length+8)+' checks; magnitudes, signs, missing values and exact source precision');
