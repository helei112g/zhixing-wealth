import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowLeft,BookOpen,Edit3,Plus,Trash2} from 'lucide-react';
import {api,ApiError,Obj} from './api';
import {Button,ErrorBox,Modal,Notice} from './components';
import {NoteMarkdown} from './note_markdown';
import './notes.css';

const dateTime=(value:string)=>value?value.replace('T',' ').slice(0,16):'时间未知';

export function InvestmentNotes({pid,instrument,portfolioName,onCount}:{pid:string;instrument:Obj;portfolioName:string;onCount:(n:number)=>void}){
 const base='/portfolios/'+encodeURIComponent(pid)+'/instruments/'+encodeURIComponent(instrument.id)+'/notes';
 const [page,Page]=useState(1),[refresh,Refresh]=useState(0),[result,Result]=useState<Obj|null>(null),[selected,Select]=useState('');
 const [note,Note]=useState<Obj|null>(null),[error,Error]=useState(''),[readError,ReadError]=useState(''),[loading,Loading]=useState(true);
 const [editor,Editor]=useState<Obj|null>(null),[deleting,Deleting]=useState<Obj|null>(null),[undo,Undo]=useState<Obj|null>(null),[busy,Busy]=useState(false),[message,Message]=useState(''),[mobile,Mobile]=useState('list');
 const countRef=useRef(onCount);countRef.current=onCount;
 useEffect(()=>{let active=true;Loading(true);Error('');api(base+'?page='+page).then(v=>{if(!active)return;Result(v);countRef.current(v.total);Page(v.page);Select(old=>v.items.some((n:Obj)=>n.id===old)?old:v.items[0]?.id||'');}).catch(e=>{if(active)Error(e.message);}).finally(()=>{if(active)Loading(false);});return()=>{active=false;};},[base,page,refresh]);
 useEffect(()=>{let active=true;Note(null);ReadError('');if(selected)api(base+'/'+selected).then(v=>{if(active)Note(v);}).catch(e=>{if(active)ReadError(e.message);});return()=>{active=false;};},[base,selected,refresh]);
 const scope=portfolioName+' / '+instrument.name;
 async function newNote(){Busy(true);Error('');try{const current=await api(base+'?page='+page);Result(current);Editor({title:'',body:'',revision:current.revision});}catch(e:any){Error(e.message);}finally{Busy(false);}}
 async function edit(){if(!note)return;Busy(true);ReadError('');try{Editor(await api(base+'/'+note.id));}catch(e:any){ReadError(e.message);}finally{Busy(false);}}
 async function restore(){if(!undo||!result)return;Busy(true);Error('');try{await api(base+'/'+undo.id+'/restore',{expected_revision:result.revision});Select(undo.id);Page(undo.page||1);Undo(null);Message('笔记已恢复');Refresh(n=>n+1);Mobile('reading');}catch(e:any){Error(e.message+' 请刷新列表，重新核对后再撤销。');}finally{Busy(false);}}
 function saved(value:Obj){if(!editor?.id)Page(1);Editor(null);Select(value.id);Refresh(n=>n+1);Mobile('reading');Undo(null);Message('笔记已保存到'+portfolioName);}
 const visible=note?.id===selected?note:null;
 return <section className="investment-notes" aria-label="当前组合标的投资笔记">
  <header className="notes-heading"><div><h2>投资笔记</h2><p className="muted small">{scope} · 仅此组合可见</p></div><Button primary disabled={busy||loading} onClick={newNote}><Plus size={16}/>新建笔记</Button></header>
  <ErrorBox error={error}/>{error&&<Button small onClick={()=>Refresh(n=>n+1)}>刷新笔记列表</Button>}
  {message&&<div className="note-feedback" role="status">{message}{undo&&<Button small disabled={busy||loading} onClick={restore}>撤销删除</Button>}</div>}
  {loading?<p className="muted" role="status">读取投资笔记…</p>:!result?null:result.total===0?<div className="notes-empty"><BookOpen size={26}/><h3>为这只标的写下第一篇笔记</h3><p className="muted">记录持有理由、观察到的变化，或留给下次的提醒。</p><Button primary disabled={busy} onClick={newNote}>新建笔记</Button></div>:<div className="notes-workspace" data-mobile={mobile}>
   <aside className="notes-list" aria-label="投资笔记列表"><div className="notes-list-caption"><span>全部笔记 · {result.total} 篇</span><small>最新在前</small></div>
    <div className="notes-list-items">{result.items.map((n:Obj)=><button key={n.id} className={'note-option'+(selected===n.id?' selected':'')} aria-current={selected===n.id?'true':undefined} data-note-id={n.id} onClick={()=>{Select(n.id);Mobile('reading');}}><time>{n.created_at?.slice(0,10)||'文件待恢复'}</time><strong>{n.title}</strong><span>{n.available?n.excerpt:n.error}</span></button>)}</div>
    <nav className="notes-pagination" aria-label="笔记分页"><span>第 {result.page} / {result.page_count} 页</span><div><Button small disabled={page<=1} onClick={()=>Page(page-1)}>上一页</Button><Button small disabled={page>=result.page_count} onClick={()=>Page(page+1)}>下一页</Button></div></nav>
   </aside>
   <article className="note-reader" aria-busy={!!selected&&!visible&&!readError}>
    <button className="text-button note-mobile-back" onClick={()=>Mobile('list')}><ArrowLeft size={14}/>全部笔记 · {result.total} 篇</button>
    <ErrorBox error={readError}/>{readError&&<Button small onClick={()=>Refresh(n=>n+1)}>重试读取正文</Button>}
    {visible?<><header className="note-reader-head"><span className="muted small">{portfolioName} · 投资笔记</span><div><Button small disabled={busy} onClick={edit}><Edit3 size={14}/>编辑</Button><Button small className="note-delete" disabled={busy} onClick={()=>Deleting(visible)}><Trash2 size={14}/>删除</Button></div></header><h2 className="note-title">{visible.title}</h2><div className="note-byline"><span>发布于 {dateTime(visible.created_at)}</span>{visible.updated_at!==visible.created_at&&<span>更新于 {dateTime(visible.updated_at)}</span>}</div><NoteMarkdown body={visible.body}/><footer className="note-scope-footer">{scope}</footer></>:!readError?<p className="muted">读取笔记正文…</p>:null}
   </article>
  </div>}
  {editor&&<NoteEditor base={base} initial={editor} scope={scope} onClose={()=>Editor(null)} onSaved={saved}/>}
  {deleting&&<DeleteNote base={base} note={deleting} scope={scope} onClose={()=>Deleting(null)} onDeleted={(v:Obj)=>{Deleting(null);Undo({...v,page});Result(old=>old?{...old,revision:v.revision}:old);Message('笔记已删除');Refresh(n=>n+1);}}/>}
 </section>;
}

function NoteEditor({base,initial,scope,onClose,onSaved}:Obj){
 const [title,Title]=useState(initial.title||''),[body,Body]=useState(initial.body||''),[reference,Reference]=useState(initial);
 const [error,Error]=useState(''),[busy,Busy]=useState(false),[discard,Discard]=useState(false);
 const [conflict,Conflict]=useState(false),[remote,Remote]=useState<Obj|null>(null),[checked,Checked]=useState('');
 const source=useRef<HTMLTextAreaElement>(null),dirty=title!==reference.title||body!==reference.body;
 const pendingCaret=useRef<number|null>(null);
 useLayoutEffect(()=>{const el=source.current,position=pendingCaret.current;if(el&&position!==null){pendingCaret.current=null;el.focus();el.setSelectionRange(position,position);}},[body]);
 useEffect(()=>{const prevent=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',prevent);return()=>window.removeEventListener('beforeunload',prevent);},[dirty]);
 function close(){if(busy)return;if(discard){Discard(false);return;}if(dirty)Discard(true);else onClose();}
 function format(kind:string){const el=source.current;if(!el)return;const start=el.selectionStart,end=el.selectionEnd,part=body.slice(start,end);let value=kind==='bold'?'**'+(part||'重点内容')+'**':kind==='heading'?'## '+(part||'小标题'):'1. '+(part||'待观察的事情');if(kind!=='bold'&&start>0&&body[start-1]!=='\n')value='\n'+value;pendingCaret.current=start+value.length;Body(body.slice(0,start)+value+body.slice(end));}
 async function save(){if(!title.trim()||!body.trim()){Error('请填写标题和正文后再发布。');return;}Busy(true);Error('');try{onSaved(await api(base+(initial.id?'/'+initial.id:''),{title,body,expected_revision:reference.revision},initial.id?'PUT':'POST'));}catch(e:any){Error(e.message);Conflict(e instanceof ApiError&&e.status===409);}finally{Busy(false);}}
 async function recheck(){Busy(true);try{const latest=await api(base+(initial.id?'/'+initial.id:''));if(initial.id&&latest.path!==reference.path){Remote(latest);Checked('这篇笔记已被其他操作修改。当前草稿保留在编辑区，请先对照最新正文。');}else{Reference({...reference,revision:latest.revision});Conflict(false);Error('');Checked('已核对，笔记正文未被其他操作更改。请检查草稿后再次保存。');}}catch(e:any){Error(e.message);}finally{Busy(false);}}
 return <Modal title={discard?'放弃这次编辑？':initial.id?'编辑投资笔记':'新建投资笔记'} subtitle={scope} onClose={close} wide busy={busy} className="note-editor">
  {discard?<><p>本次尚未保存的内容会被放弃，已发布的笔记保持原样。</p><div className="form-actions"><Button onClick={()=>Discard(false)}>继续编辑</Button><Button className="note-delete" onClick={onClose}>放弃编辑</Button></div></>:<>
   <fieldset disabled={busy}><label className="field"><span>笔记标题</span><input aria-label="笔记标题" maxLength={100} value={title} onChange={e=>Title(e.target.value)} placeholder="给这次判断起一个清楚的标题"/></label>
   <div className="note-editor-toolbar"><div className="note-format" role="toolbar" aria-label="Markdown格式"><Button small onClick={()=>format('heading')} aria-label="插入标题">H</Button><Button small onClick={()=>format('bold')} aria-label="加粗文字"><b>B</b></Button><Button small onClick={()=>format('list')} aria-label="插入序号列表">1.</Button><small>Markdown</small></div></div>
   <div className="note-source"><textarea ref={source} aria-label="Markdown正文" maxLength={200000} rows={16} value={body} onChange={e=>Body(e.target.value)} placeholder={'## 目前的看法\n\n用 **加粗** 标出重点。\n\n1. 下一次要核对的事情。'}/></div>
   <p className="muted small">支持标题、段落、加粗和序号列表，保存后在阅读区显示排版效果。正文保存在本机 Markdown 文件中。</p></fieldset>
   <ErrorBox error={error}/>{conflict&&!remote&&<Button small disabled={busy} onClick={recheck}>核对最新版本</Button>}{checked&&<Notice>{checked}</Notice>}
   {remote&&<details className="note-conflict" open><summary>最新已保存版本：{remote.title}</summary><pre>{remote.body}</pre><Button disabled={busy} onClick={()=>{Title(remote.title);Body(remote.body);Reference(remote);Remote(null);Conflict(false);Checked('已载入最新正文，可以继续编辑。');Error('');}}>放弃当前草稿，载入最新正文</Button></details>}
   <footer className="form-actions"><small className="muted note-save-state">{dirty?'有未保存内容':initial.id?'编辑不会改变最初发布时间':'尚未发布'}</small><Button disabled={busy} onClick={close}>取消</Button><Button primary disabled={busy||conflict||!!remote} onClick={save}>{busy?'保存中…':initial.id?'保存修改':'发布笔记'}</Button></footer>
  </>}
 </Modal>;
}

function DeleteNote({base,note,scope,onClose,onDeleted}:Obj){
 const [current,Current]=useState<Obj|null>(null),[error,Error]=useState(''),[busy,Busy]=useState(false),[retry,Retry]=useState(0);
 useEffect(()=>{let active=true;Current(null);Error('');api(base+'/'+note.id).then(v=>{if(active)Current(v);}).catch(e=>{if(active)Error(e.message);});return()=>{active=false;};},[base,note.id,retry]);
 async function remove(){Busy(true);Error('');try{onDeleted(await api(base+'/'+note.id,{expected_revision:current?.revision},'DELETE'));}catch(e:any){Error(e.message);Current(null);}finally{Busy(false);}}
 return <Modal title="删除这篇笔记？" subtitle={scope} onClose={onClose} busy={busy} className="note-delete-dialog"><blockquote>{current?.title||note.title}</blockquote><p>只删除这篇笔记，交易记录和其他笔记不受影响。删除后可以撤销。</p><ErrorBox error={error}/>{error&&<Button small onClick={()=>Retry(n=>n+1)}>重新读取并核对</Button>}<footer className="form-actions"><Button onClick={onClose} disabled={busy}>保留笔记</Button><Button className="note-delete" disabled={!current||busy} onClick={remove}>删除笔记</Button></footer></Modal>;
}
