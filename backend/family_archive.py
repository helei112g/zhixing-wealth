"""Dated lifecycle changes preserve original balances, portfolio records and closed intervals."""
from .common import DomainError, now
from .family_domain import active, at_item, past


def archive_entries(family, state, date):
    rows = []
    for item in family['items']:
        meta = at_item(item, date)
        if meta and meta.get('closed_on') and meta['closed_on'] <= date:
            rows.append(dict(id=item['id'], target='item', name=meta['name'], category=meta['category'],
                             currency=meta['currency'], closed_on=meta['closed_on'], can_restore=True))
    portfolios = {p['id']: p for p in state['portfolios']}
    for link in family['links']:
        if not link.get('closed_on') or link['closed_on'] > date:continue
        portfolio = portfolios.get(link['portfolio_id'])
        if portfolio:
            linked = any(other['portfolio_id'] == portfolio['id'] and active(other, date) for other in family['links'])
            rows.append(dict(id=link['id'], target='link', name=portfolio['name'], category='investment',
                             currency='CNY', closed_on=link['closed_on'], portfolio_id=portfolio['id'], can_restore=not linked))
    return sorted(rows, key=lambda r:(r['closed_on'],r['id']), reverse=True)


def lifecycle(service, db, family, state, operation, data, record_id):
    if set(data) - {'target','effective_on','reason'}:raise DomainError('归档或恢复参数包含未知字段')
    target = data.get('target')
    if target not in ('item','link'):raise DomainError('请选择项目或组合关联')
    group = 'items' if target == 'item' else 'links'
    row = next((r for r in family[group] if r['id'] == record_id), None)
    if not row:raise DomainError('归档或恢复对象不存在')
    date = past(data.get('effective_on'), '归档或恢复日期')
    reason = str(data.get('reason') or ('归档资产项目' if operation == 'archive' else '恢复资产项目'))
    if len(reason) > 300:raise DomainError('修订说明不能超过300字')
    if target == 'link':
        if operation == 'archive':
            if not active(row,date):raise DomainError('所选日期没有有效的组合关联')
            return service._apply(db,'link',dict(portfolio_id=row['portfolio_id'],effective_on=row['effective_on'],
                                  share=row['share'],closed_on=date,reason=reason),record_id)
        if not row.get('closed_on') or date < row['closed_on']:raise DomainError('恢复日期不能早于归档日期')
        # Create a new interval; clearing the old closed_on would rewrite archived history.
        return service._apply(db,'link',dict(portfolio_id=row['portfolio_id'],effective_on=date,
                              share=row['share'],reason=reason),None)
    if any(v['effective_on'] > date for v in row['versions']):
        raise DomainError('归档或恢复日期不能早于已有档案生效日')
    meta = at_item(row,date)
    if not meta:raise DomainError('所选日期还没有这个项目')
    if operation == 'archive':
        if not active(meta,date):raise DomainError('项目在所选日期已经归档')
        closed = date
    else:
        if not meta.get('closed_on') or date < meta['closed_on']:raise DomainError('恢复日期不能早于归档日期')
        closed = None
    version = dict(meta,effective_on=date,closed_on=closed,recorded_at=now())
    value = dict(row,versions=[*row['versions'],version])
    # Never add a zero valuation or remove a schedule: past balances and events remain intact.
    service._put(db,'family_items',value,reason)
    return value
