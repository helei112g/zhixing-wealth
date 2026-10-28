import React from 'react';

function inline(value:string):React.ReactNode[]{
 return value.split(/(\*\*[^*]+\*\*)/g).map((text,n)=>text.startsWith('**')&&text.endsWith('**')?<strong key={n}>{text.slice(2,-2)}</strong>:text);
}
const headingLine=(line:string)=>line.match(/^(#{1,6})[ \t]+(\S.*)$/);
const listLine=(line:string)=>line.match(/^[ \t]*(\d+)\.[ \t]+(\S.*)$/);

// Render saved notes as React text nodes, never HTML or user-supplied links/scripts.
export function NoteMarkdown({body}:{body:string}){
 const lines=body.replace(/\r\n?/g,'\n').split('\n'),nodes:React.ReactNode[]=[];
 for(let n=0;n<lines.length;){
  if(!lines[n].trim()){n++;continue;}
  const start=n,heading=headingLine(lines[n]),list=listLine(lines[n]);
  if(heading){nodes.push(React.createElement('h'+heading[1].length,{key:start},inline(heading[2])));n++;continue;}
  if(list){
   const items:React.ReactNode[]=[];
   while(n<lines.length){const item=listLine(lines[n]);if(!item)break;items.push(<li key={n}>{inline(item[2])}</li>);n++;}
   nodes.push(<ol key={start} start={Number(list[1])}>{items}</ol>);continue;
  }
  // Consume at least one line. Incomplete markers such as "# " or "1. " are
  // ordinary text; block detection must use the same rules as block rendering.
  const paragraph=[lines[n++]];
  while(n<lines.length&&lines[n].trim()&&!headingLine(lines[n])&&!listLine(lines[n]))paragraph.push(lines[n++]);
  nodes.push(<p key={start}>{inline(paragraph.join('\n'))}</p>);
 }
 return <div className="note-markdown">{nodes}</div>;
}
