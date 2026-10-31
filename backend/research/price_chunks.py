"""Share immutable calendar-year price blocks between daily snapshot manifests."""
from itertools import groupby
from ..common import DomainError,day
from .metrics import clean_prices
from .snapshots import encode,decode

MARKER='price_year_chunks_v1'


def prepare(value):
    blocks={};refs=[]
    for _,group in groupby(value['rows'],key=lambda r:str(r['date'])[:4]):
        sha,content=encode({'rows':list(group)})
        blocks[sha]=content;refs.append(sha)
    manifest=dict(value,rows={MARKER:refs})
    sha,content=encode(manifest)
    return sha,content,blocks


def references(value):
    rows=value.get('rows')
    if not isinstance(rows,dict):return None
    if set(rows)!={MARKER} or not isinstance(rows[MARKER],list) or not rows[MARKER]:raise DomainError('价格分块清单无效')
    return rows[MARKER]


def expand(db,value):
    refs=references(value)
    if refs is None:return value
    rows=[]
    for sha in refs:
        raw=db.execute('SELECT data FROM research_price_chunks WHERE sha256=?',(sha,)).fetchone()
        if not raw:raise DomainError('价格历史分块引用缺失')
        rows.extend(decode(raw[0],sha)['rows'])
    return dict(value,rows=rows)


def validate_blocks(db):
    summaries={}
    for sha,content in db.execute('SELECT sha256,data FROM research_price_chunks'):
        rows=decode(content,sha).get('rows',[])
        if not rows:raise DomainError('价格历史分块为空')
        end=max(day(r['date']) for r in rows);clean=clean_prices(rows,end)
        if len(clean)!=len(rows) or [day(r['date']) for r in rows]!=sorted(day(r['date']) for r in rows):raise DomainError('价格分块日期重复或无序')
        summaries[sha]=(clean[0]['date'],clean[-1]['date'],rows[-1])
    return summaries


def validation_view(value,summaries):
    """Hash every block once, then validate all manifests without expanding old full histories."""
    refs=references(value)
    if refs is None:return value
    previous=None;last=None
    for sha in refs:
        if sha not in summaries:raise DomainError('价格历史分块引用缺失')
        first,end,last=summaries[sha]
        if previous is not None and first<=previous:raise DomainError('价格分块区间重叠或无序')
        previous=end
    return dict(value,rows=[last])
