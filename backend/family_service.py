"""Family writes are isolated from investment tables; all writes require a revision."""
from __future__ import annotations

import calendar
import json
import uuid
from datetime import timedelta

from .common import ConflictError, DomainError, ZERO, day, dec, ds, now, today, month_after, json_dumps, wire
from .family_domain import (TABLES, CATEGORIES, REASONS, text, money, past, fraction, currency,
                            read_state, at_item, active, event_signatures, snapshot, schedule_projection)
from .family_ledger import recording_bounds, debt_summary
from .ledger import Market
from .store import cached_query


def check_fields(data, allowed):
    unknown = set(data) - set(allowed.split())
    if unknown:
        raise DomainError('未知字段：' + '、'.join(sorted(unknown)))


class FamilyService:
    def __init__(self, store):
        self.store = store

    @cached_query
    def read(self, date=None):
        date = past(date or today(), '估值日期')
        with self.store.connection() as db:
            state, family = self.store.state(db), read_state(db)
        market = Market(state)
        snap = snapshot(state, family, date, market=market)
        return wire(dict(snap, debt_summary=debt_summary(state, family, snap, market=market)))

    @cached_query
    def catalog(self):
        with self.store.connection() as db:
            state, family = self.store.state(db), read_state(db)
            market = Market(state) if family['closures'] else None
            for closure in family['closures']:
                previous = closure['snapshot']
                current = wire(snapshot(state, family, closure['effective_on'], market=market))
                fields = ('total_assets', 'total_debt', 'net_assets')
                closure['needs_review'] = any(current[k] != previous[k] for k in fields)
                closure['current_net_assets'] = current['net_assets']
                closure['net_difference'] = ds(dec(current['net_assets']) - dec(previous['net_assets'])) if current['net_assets'] is not None and previous['net_assets'] is not None else None
            return dict(family, revision=state['revision'], bounds=recording_bounds(family, str(today())), portfolios=[{k: p.get(k) for k in
                        ('id', 'name', 'funding_mode', 'archived', 'start_date')} for p in state['portfolios']])

    @cached_query
    def history(self, start=None, end=None, grain='month', comparison='previous'):
        end = day(past(end or today()))
        if grain not in ('month', 'year'):
            raise DomainError('资产统计粒度须为month或year')
        if comparison not in ('previous', 'year_start', 'yoy'):
            raise DomainError('对比口径须为previous、year_start或yoy')
        with self.store.connection() as db:
            state, family = self.store.state(db), read_state(db)
            dates = [v['effective_on'] for i in family['items'] for v in i['versions']] + [l['effective_on'] for l in family['links']]
            if not dates:
                return {'rows': [], 'revision': state['revision'], 'basis': '尚未建档'}
            start = max(day(start or min(dates)), day(min(dates)))
            if start > end or (end - start).days > 366 * 30:
                raise DomainError('历史区间须按时间顺序且不超过30年')
            market = Market(state)
            points, cursor, projections = [], start, {}
            def projected(d):
                if str(d) < min(dates): return None
                if d not in projections: projections[d] = snapshot(state, family, str(d), market=market)
                return projections[d]
            def values_of(snap):
                values = {c['key']: c['value'] for c in snap['categories']}
                values.update(total=snap['total_assets'], net=snap['net_assets'], debt=snap['total_debt'])
                return values
            def scope(snap, metric='net'):
                return sorted(r['id'] + ':' + r['share'] for r in snap['items']
                              if metric == 'net' or (metric == 'total' and r['category'] != 'debt') or r['category'] == metric) if snap else None
            while cursor <= end:
                period_end = cursor.replace(month=12, day=31) if grain == 'year' else cursor.replace(day=calendar.monthrange(cursor.year, cursor.month)[1])
                d = min(period_end, end)
                snap = projected(d)
                values = values_of(snap)
                if comparison == 'yoy':
                    baseline_day = calendar.monthrange(d.year - 1, d.month)[1] if d == period_end and grain == 'month' else min(d.day, calendar.monthrange(d.year - 1, d.month)[1])
                    baseline = d.replace(year=d.year - 1, day=baseline_day)
                elif comparison == 'year_start' or grain == 'year':
                    baseline = d.replace(month=1, day=1) - timedelta(days=1)
                else: baseline = d.replace(day=1) - timedelta(days=1)
                previous = projected(baseline)
                previous_values = values_of(previous) if previous else {}
                scope_changed = previous is not None and scope(previous) != scope(snap)
                changes = {}
                for k, v in values.items():
                    prev = previous_values.get(k)
                    delta = v - prev if v is not None and prev is not None else None
                    changed = previous is not None and scope(previous, k) != scope(snap, k)
                    reason = '缺少对比期记录或金额' if delta is None else '对比期金额不大于零' if prev <= 0 else '含新增、移除或份额调整' if changed else ''
                    changes[k] = {'amount': delta, 'rate': delta / prev if delta is not None and prev > 0 else None,
                                  'scope_changed': changed, 'reason': reason}
                points.append({'date': str(d), 'partial': d != period_end, 'values': values, 'changes': changes,
                               'components': [{k:r[k] for k in ('id','name','category','value')} for r in snap['items']],
                               'quality': '待核对' if snap['provisional'] else '沿用或暂估' if snap['warnings'] else '已记录',
                               'missing': snap['missing_items'], 'warnings': snap['warnings'],
                               'comparison_date': str(baseline), 'scope_changed': scope_changed,
                               'active_ids': scope(snap)})
                cursor = period_end + timedelta(days=1)
            result = wire({'rows': points, 'revision': state['revision'], 'basis': '按当前资料重算；生效日前无数据，手工估值延用并保留来源日'})
            return result

    @cached_query
    def summary(self, grain='month', period=None, start=None, end=None):
        from .family_summary import period_summary
        with self.store.connection() as db:
            state, family = self.store.state(db), read_state(db)
        return period_summary(state, family, grain, period, start, end)

    @cached_query
    def income_summary(self, year=None):
        year = year or today().year
        if not 1900 <= year <= today().year: raise DomainError('收入年份无效')
        with self.store.connection() as db:
            state, family = self.store.state(db), read_state(db)
            from .family_summary import income_records
            records = [r for r in income_records(state, family) if r['month'][:4] == str(year)]
            rows=[]
            for m in range(1,13):
                month=f'{year}-{m:02d}'; selected=[r for r in records if r['month']==month]
                total=None if any(r['value'] is None for r in selected) else sum((r['value'] for r in selected),ZERO)
                rows.append(dict(month=month,total=total,recorded=bool(selected),groups={k:sum((r['value'] for r in selected if r['kind']==k and r['value'] is not None),ZERO) if not any(r['value'] is None for r in selected if r['kind']==k) else None for k in ('salary','rent','fund','custom')}))
            missing = sum(r['value'] is None for r in records)
            known = sum((r['value'] for r in records if r['value'] is not None), ZERO)
            total = known if records and not missing else None
            groups = {}
            for kind in ('salary', 'rent', 'fund', 'custom'):
                selected = [r for r in records if r['kind'] == kind]
                amount = None if any(r['value'] is None for r in selected) else sum((r['value'] for r in selected), ZERO)
                groups[kind] = dict(amount=amount, share=amount / total if amount is not None and total is not None and total > 0 else None)
            summary = dict(total=total, known_total=known, groups=groups, missing_count=missing,
                           recorded_months=len({r['month'] for r in records}))
            return wire(dict(year=year,rows=rows,summary=summary,records=sorted(records,key=lambda r:r['month'],reverse=True),revision=state['revision']))

    def _current_revision(self, db, expected):
        if not isinstance(expected, int) or isinstance(expected, bool):
            raise DomainError('资产写入必须提供整数 expected_revision')
        actual = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        if expected != actual:
            raise ConflictError('资产账本或组合已变化，请重新预览；本次未写入。')
        return actual

    def command(self, operation, payload, expected_revision, record_id=None, preview=False):
        if not isinstance(payload, dict):
            raise DomainError('payload须为对象')
        if preview:
            with self.store.connection() as db:
                revision = self._current_revision(db, expected_revision)
                db.execute('SAVEPOINT family_preview')
                try:
                    result = self._apply(db, operation, payload, record_id)
                    projected = snapshot(self.store.state(db), read_state(db), str(today()))
                    return wire({'preview': True, 'revision': revision, 'result': result,
                                 'summary': {k: projected[k] for k in ('provisional_assets', 'total_debt', 'provisional_net', 'missing_items', 'reconciliation_todos')}})
                finally:
                    db.execute('ROLLBACK TO family_preview'); db.execute('RELEASE family_preview')
        # Require the same version inside the write transaction, not only before it.
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise DomainError('资产写入必须提供整数 expected_revision')
        with self.store.transaction(expected_revision=expected_revision) as db:
            result = self._apply(db, operation, payload, record_id)
        return {'result': wire(result), 'revision': expected_revision + 1}

    def _put(self, db, table, value, reason):
        assert table in TABLES
        before = db.execute(f'SELECT data FROM {table} WHERE id=?', (value['id'],)).fetchone()
        db.execute(f'INSERT INTO {table}(id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                   (value['id'], json_dumps(wire(value))))
        self.store.audit(db, table, value['id'], json.loads(before[0]) if before else None, wire(value), reason)

    def _apply(self, db, operation, data, record_id):
        family, state = read_state(db), self.store.state(db)
        rid = record_id or uuid.uuid4().hex
        def find(group, identity, label):
            obj = next((r for r in family[group] if r['id'] == identity), None)
            if not obj:
                raise DomainError(label + '不存在')
            return obj
        reason = text(data.get('reason'), '修订说明', 300) or '资产资料更新'
        if operation == 'item':
            check_fields(data, 'name category currency share economic_id platform member effective_on closed_on review_days coverage_type coverage_amount insured beneficiary policy_number policy_end policy_lifetime source amount household_amount restricted household_restricted interest fees reason liability_asset_id started_on due_on payment_plan')
            category = data.get('category')
            if category not in CATEGORIES:
                raise DomainError('资产类别无效')
            effective = past(data.get('effective_on'))
            old = find('items', rid, '资产') if record_id else None
            previous = at_item(old, effective) if old else None
            # Simplified editors may omit legacy details; never clear them by omission.
            data = dict(previous or {}, **data)
            if old and category != old['versions'][0]['category']:
                raise DomainError('类别不可直接改写历史；请关闭旧项并以新生效日建档')
            c = currency(data.get('currency', 'CNY'))
            if old and c != old['versions'][0]['currency']:
                raise DomainError('币种不可改写历史；请另行建档')
            name = text(data.get('name'), '名称', 80, True)
            economic_id = text(data.get('economic_id') or ('manual:' + category + ':' + name), '项目识别名称', 120, True)
            if economic_id.startswith('portfolio:'):
                raise DomainError('组合资产须使用关联入口，不得作为手工项目重复录入')
            for item in family['items']:
                if item['id'] != rid and any(v['economic_id'] == economic_id for v in item['versions']):
                    raise DomainError('这个项目已登记，请更新原记录；不同项目请用不同名称区分')
            closed = str(day(data['closed_on'])) if data.get('closed_on') else None
            if closed and closed < effective:
                raise DomainError('关闭日期不能早于本次档案生效日期，关闭日不再计入')
            review_days = data.get('review_days', 30 if category == 'cash' else 365 if category == 'insurance' else 90)
            if not isinstance(review_days, int) or isinstance(review_days, bool) or not 1 <= review_days <= 3650:
                raise DomainError('复核间隔须为1至3650天')
            meta = {'effective_on': effective, 'closed_on': closed, 'category': category,
                    'name': name, 'currency': c,
                    'share': fraction(data.get('share', '1')), 'economic_id': economic_id,
                    'review_days': review_days, 'recorded_at': now()}
            for field in ('platform', 'member', 'insured', 'beneficiary', 'policy_number', 'coverage_type'):
                meta[field] = text(data.get(field), field)
            if category != 'insurance':
                meta['member'] = ''
            meta['coverage_amount'] = money(data.get('coverage_amount'), '保障额度', True)
            lifetime = data.get('policy_lifetime', False)
            if not isinstance(lifetime, bool):
                raise DomainError('终身保障须为布尔值')
            if lifetime and (category != 'insurance' or data.get('policy_end')):
                raise DomainError('终身保障仅适用于保险，且不能同时填写到期日')
            meta['policy_lifetime'] = lifetime
            meta['policy_end'] = str(day(data['policy_end'])) if data.get('policy_end') else None
            meta['started_on'] = past(data['started_on'], '借款或借出日期') if data.get('started_on') else None
            meta['due_on'] = str(day(data['due_on'])) if data.get('due_on') else None
            if (meta['started_on'] or meta['due_on']) and category not in ('debt', 'receivable'):
                raise DomainError('借款和收回日期只适用于负债或应收款')
            if meta['due_on'] and meta['started_on'] and meta['due_on'] < meta['started_on']:
                raise DomainError('预计还清或收回日期不能早于借款或借出日期')
            meta['liability_asset_id'] = data.get('liability_asset_id') or None
            if meta['liability_asset_id']:
                if category != 'debt':
                    raise DomainError('只有负债可以关联抵押资产')
                collateral = find('items', meta['liability_asset_id'], '关联资产')
                if collateral['versions'][0]['category'] == 'debt':
                    raise DomainError('负债不能作为抵押资产')
            if 'payment_plan' in data:
                from .family_plans import payment_plan
                if not isinstance(data['payment_plan'], dict): raise DomainError('缴费或还款设置无效')
                balance = data.get('household_amount')
                if balance is None:
                    values = [v for v in family['values'] if v['item_id'] == rid and v['effective_on'] <= effective]
                    val = max(enumerate(values),key=lambda x:(x[1]['effective_on'],x[0]))[1] if values else None
                    balance = dec(val['amount'])*dec(meta['share']) if val and val['amount'] is not None else None
                # previous contains server-only principal, not part of the editable rule.
                incoming = {k:v for k,v in data['payment_plan'].items() if k != 'principal'}
                meta['payment_plan'] = payment_plan(incoming, category, balance, (previous or {}).get('payment_plan'))
                if category == 'debt' and meta['started_on'] and meta['payment_plan'].get('first_due', meta['started_on']) < meta['started_on']:
                    raise DomainError('首次还款日期不能早于借款日期')
                if category == 'debt' and meta['payment_plan'] != (previous or {}).get('payment_plan') and meta['payment_plan'].get('first_due', effective) < effective:
                    raise DomainError('重新计算还款时，请从本次金额记录日或之后开始，不能用当前欠款倒推过去的还款')
            value = {'id': rid, 'versions': (old['versions'] if old else []) + [meta]}
            self._put(db, 'family_items', value, reason)
            if not old or 'amount' in data or 'household_amount' in data:
                self._apply(db, 'valuation', {k: v for k, v in dict(data, item_id=rid).items()
                            if k in ('item_id', 'effective_on', 'source', 'amount', 'household_amount', 'restricted', 'household_restricted', 'interest', 'fees', 'reason')}, None)
            if category == 'insurance':
                current = at_item(value, str(today()))
                renewal_id = uuid.uuid5(uuid.NAMESPACE_URL, 'family-policy-end:' + rid).hex
                prior = next((s for s in family['schedules'] if s['id'] == renewal_id), None)
                ending = current.get('policy_end')
                if ending and active(current, ending):
                    if not prior or prior['due_date'] != ending or prior['status'] == 'cancelled':
                        renewal = {'id': renewal_id, 'item_id': rid, 'kind': 'renewal', 'due_date': ending,
                                   'amount': '0', 'principal': '0', 'interest': '0', 'status': 'pending',
                                   'confirmation': '', 'snooze_until': None, 'auto_generated': True,
                                   'version': (prior or {}).get('version', 0) + 1, 'updated_at': now()}
                        self._put(db, 'family_schedules', renewal, '根据保单保障有效期生成续保提醒')
                elif prior and prior['status'] == 'pending':
                    self._put(db, 'family_schedules', dict(prior, status='cancelled', confirmation='保单关闭或有效期取消',
                              version=prior['version'] + 1, updated_at=now()), reason)
            if meta.get('payment_plan', {}).get('mode', 'none') != 'none':
                from .family_plans import installments, shifted_date
                plan=meta['payment_plan']; first=plan['first_due']; end=str(shifted_date(first,(plan['periods']-1)*(12 if plan['mode']=='annual' else 1)))
                payments=installments(plan,rid,meta['name'],c,first,end)
                return dict(value,payment_preview=dict(first=payments[0],last_due=end,total=ds(sum((dec(r['amount']) for r in payments),ZERO)),periods=len(payments)))
            return value
        if operation == 'income':
            check_fields(data, 'kind name month date currency amount reason')
            kind = data.get('kind')
            if kind not in ('salary','rent','fund','custom'): raise DomainError('请选择收入类型')
            month = str(data.get('month',''))
            try: parsed = day(month+'-01')
            except (ValueError, DomainError): raise DomainError('请选择收入月份') from None
            if len(month)!=7 or parsed > today(): raise DomainError('不能记录未来月份的实际收入')
            date = past(data.get('date') or min(str(today()),str(parsed.replace(day=calendar.monthrange(parsed.year,parsed.month)[1]))))
            if date[:7] != month: raise DomainError('收入日期须在所选月份内')
            names = {'salary':'工资','rent':'租金','fund':'公积金'}
            value = dict(kind=kind,name=names.get(kind) or text(data.get('name'),'收入名称',80,True),month=month,date=date,
                         currency=currency(data.get('currency','CNY')),amount=money(data.get('amount')),recorded_at=now())
            previous = find('income',rid,'收入') if record_id else None
            entry = dict(id=rid,versions=(previous['versions'] if previous else [])+[value])
            self._put(db,'family_income',entry,reason)
            return entry
        if operation == 'valuation':
            check_fields(data, 'item_id effective_on source amount household_amount restricted household_restricted interest fees reason extend_start')
            if record_id:
                raise DomainError('估值为追加历史记录，不能覆盖旧记录')
            item = find('items', data.get('item_id'), '资产')
            effective = past(data.get('effective_on'))
            meta = at_item(item, effective)
            if 'extend_start' in data and not isinstance(data['extend_start'], bool):
                raise DomainError('补录起始日期设置无效')
            if meta is None and data.get('extend_start'):
                first = min(item['versions'], key=lambda v:v['effective_on'])
                if first['category'] not in ('cash', 'fixed', 'investment', 'receivable'):
                    raise DomainError('此类别不能通过余额补录提前起始日期')
                meta = dict(first, effective_on=effective, recorded_at=now())
                self._put(db, 'family_items', dict(item, versions=item['versions'] + [meta]), '补录最早余额日期')
            if not meta or not active(meta, effective):
                raise DomainError('估值日期须在该资产有效期内')
            if 'household_amount' in data:
                if data['household_amount'] in ('', None) and meta['category'] != 'insurance': raise DomainError('请填写金额，金额为零时请填0')
                if 'amount' in data or 'interest' in data or 'fees' in data:
                    raise DomainError('填写实际金额时，不再拆分本金、利息或费用')
                amount = money(data['household_amount'], '当前金额', True)
                data = dict(data, amount=ds(dec(amount) / dec(meta['share'])) if amount is not None else None)
                if 'household_restricted' in data:
                    data['restricted'] = ds(dec(money(data['household_restricted'], '暂不能用金额')) / dec(meta['share']))
                elif meta['category'] == 'cash':
                    previous_values = [v for v in family['values'] if v['item_id'] == item['id'] and v['effective_on'] <= effective]
                    if previous_values:
                        data['restricted'] = max(enumerate(previous_values), key=lambda x: (x[1]['effective_on'], x[0]))[1]['restricted']
            amount = money(data.get('amount'), '估值', True)
            restricted, interest, fees = [money(data.get(k, '0'), k) for k in ('restricted', 'interest', 'fees')]
            if dec(restricted) and meta['category'] != 'cash':
                raise DomainError('受限金额仅适用于现金')
            if (dec(interest) or dec(fees)) and meta['category'] != 'debt':
                raise DomainError('未付利息与费用仅适用于负债')
            if amount is not None and dec(restricted) > dec(amount):
                raise DomainError('受限现金不能超过账户余额')
            value = {'id': rid, 'item_id': item['id'], 'effective_on': effective, 'recorded_at': now(),
                     'amount': amount, 'restricted': restricted, 'interest': interest, 'fees': fees,
                     'source': text(data.get('source') or '手动填写', '金额来源', 200, True), 'reason': reason}
            self._put(db, 'family_values', value, reason)
            return value
        if operation in ('links', 'unlink'):
            check_fields(data, ('portfolio_ids' if operation == 'links' else 'link_ids') + ' effective_on reason')
            if record_id:
                raise DomainError('批量关联操作不使用 record_id')
            ids = data.get('portfolio_ids' if operation == 'links' else 'link_ids')
            if not isinstance(ids, list) or not ids or len(ids) > 100 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
                raise DomainError('请选择1至100个不同的组合')
            effective = past(data.get('effective_on') or str(today()), '关联或解除日期')
            result = []
            for identity in ids:
                if operation == 'links':
                    result.append(self._apply(db, 'link', dict(portfolio_id=identity, effective_on=effective, share='1', reason=reason), None))
                else:
                    link = find('links', identity, '关联')
                    if not active(link, effective):
                        raise DomainError('所选日期没有这项组合关联')
                    result.append(self._apply(db, 'link', dict(portfolio_id=link['portfolio_id'], effective_on=link['effective_on'],
                                      share=link['share'], closed_on=effective, reason=reason), identity))
            return result
        if operation == 'link':
            check_fields(data, 'portfolio_id effective_on closed_on share reason')
            p = next((p for p in state['portfolios'] if p['id'] == data.get('portfolio_id')), None)
            if not p:
                raise DomainError('组合不存在')
            effective = past(data.get('effective_on'))
            if effective < p['start_date']:
                raise DomainError('关联日期不能早于组合建档日')
            closed = str(day(data['closed_on'])) if data.get('closed_on') else None
            if closed and closed < effective:
                raise DomainError('解除关联日期不能早于关联日期')
            old = find('links', rid, '关联') if record_id else None
            if old and any(data.get(k) != old[k] for k in ('portfolio_id', 'effective_on')):
                raise DomainError('既有组合和生效日不可改写；请关闭旧关联并新建')
            share = fraction(data.get('share', '1'))
            if old and share != old['share']:
                raise DomainError('归属比例变化请关闭旧关联，以新有效期建档')
            for other in family['links']:
                if other.get('closed_on') == other['effective_on'] or closed == effective:
                    continue
                if other['id'] != rid and other['portfolio_id'] == p['id'] and effective < (other.get('closed_on') or '9999-12-31') and other['effective_on'] < (closed or '9999-12-31'):
                    raise DomainError('该组合在此期间已经关联，不能重复计入')
            value = dict(old or {}, id=rid, portfolio_id=p['id'], effective_on=effective, closed_on=closed, share=share,
                         baseline=old['baseline'] if old else event_signatures(state, p['id'], effective), recorded_at=now())
            self._put(db, 'family_links', value, reason)
            return value
        if operation == 'settings':
            check_fields(data, 'effective_on living_expense monthly_income reserve_months debt_plan_complete policy_plan_complete reason')
            if record_id:
                raise DomainError('支出设置追加版本，不覆盖历史')
            months = money(data.get('reserve_months', '12'), '储备月数')
            if not 0 < dec(months) <= 120:
                raise DomainError('储备目标须大于0且不超过120个月')
            for k in ('debt_plan_complete', 'policy_plan_complete'):
                if not isinstance(data.get(k), bool):
                    raise DomainError('请明确核对还款与保费计划是否完整')
            value = {'id': rid, 'effective_on': past(data.get('effective_on')), 'recorded_at': now(),
                     'living_expense': money(data.get('living_expense'), '必要生活开支', True),
                     'monthly_income': money(data.get('monthly_income'), '税后月收入', True), 'reserve_months': months,
                     'debt_plan_complete': data['debt_plan_complete'], 'policy_plan_complete': data['policy_plan_complete']}
            self._put(db, 'family_settings', value, reason)
            return value
        if operation == 'schedule':
            check_fields(data, 'item_id kind due_date amount household_amount principal interest status confirmation snooze_until reason')
            item = find('items', data.get('item_id'), '项目')
            due = str(day(data.get('due_date')))
            old = find('schedules', rid, '提醒') if record_id else None
            meta_date = str(today()) if old and old.get('auto_generated') and old['kind'] in ('debt_end', 'receivable') else due
            meta = at_item(item, meta_date)
            if not meta or not active(meta, meta_date):
                raise DomainError('到期日不在资产有效期内')
            kind = data.get('kind')
            expected_category = {'debt': 'debt', 'debt_end': 'debt', 'receivable': 'receivable', 'premium': 'insurance', 'renewal': 'insurance'}
            if expected_category.get(kind) != meta['category']:
                raise DomainError('提醒类型与所选项目不匹配')
            if 'household_amount' in data:
                if any(k in data for k in ('amount', 'principal', 'interest')):
                    raise DomainError('本次金额只需填写一项，不再拆分本金和利息')
                data = dict(data, amount=ds(dec(money(data['household_amount'])) / dec(meta['share'])))
            if old and old.get('auto_generated') and any(data.get(k) != old[k] for k in ('item_id', 'kind', 'due_date')):
                raise DomainError('这条提醒来自项目的到期日期，请在“编辑信息”中修改日期')
            for other in family['schedules']:
                if other['id'] != rid and other['item_id'] == item['id'] and other['kind'] == kind and other['due_date'] == due and other['status'] != 'cancelled':
                    raise DomainError('该项目同日同类计划已存在，请更新原记录')
            status = data.get('status', 'pending')
            if status not in ('pending', 'done', 'cancelled'):
                raise DomainError('计划状态无效')
            confirmation = text(data.get('confirmation'), '处理核对说明', 300)
            if status in ('done', 'cancelled') and not confirmation:
                raise DomainError('请填写实际还款、续保或取消的核对依据；通知已读不能代替业务处理')
            total_only = kind == 'debt' and 'amount' in data and 'principal' not in data and 'interest' not in data
            principal, interest = (None, None) if total_only else (money(data.get('principal', '0')), money(data.get('interest', '0')))
            amount = money(data['amount']) if total_only else ds(dec(principal) + dec(interest)) if kind == 'debt' else money(data.get('amount', '0'))
            if kind not in ('renewal', 'debt_end', 'receivable') and dec(amount) <= 0:
                raise DomainError('缴款计划金额须大于0')
            snooze = data.get('snooze_until') or None
            if snooze and (day(snooze) <= today() or day(snooze) > today() + timedelta(days=366)):
                raise DomainError('稍后提醒日期须在未来一年内')
            value = {'id': rid, 'item_id': item['id'], 'kind': kind, 'due_date': due, 'amount': amount,
                     'principal': principal, 'interest': interest, 'status': status, 'confirmation': confirmation,
                     'snooze_until': snooze, 'version': (old or {}).get('version', 0) + 1, 'updated_at': now()}
            if total_only:
                value['breakdown_known'] = False
            if old and old.get('auto_generated'):
                value['auto_generated'] = True
            self._put(db, 'family_schedules', value, reason)
            return value
        if operation == 'reconcile':
            check_fields(data, 'link_id effective_on confirmation reason')
            link = find('links', data.get('link_id'), '关联组合')
            effective = past(data.get('effective_on'))
            if not active(link, effective):
                raise DomainError('核对日期不在关联有效期内')
            value = {'id': rid, 'link_id': link['id'], 'effective_on': effective, 'recorded_at': now(),
                     'confirmation': text(data.get('confirmation'), '现金核对依据', 300, True),
                     'signatures': event_signatures(state, link['portfolio_id'], effective)}
            self._put(db, 'family_reconciliations', value, reason)
            return value
        if operation == 'closure':
            check_fields(data, 'effective_on reason')
            effective = past(data.get('effective_on'))
            d = day(effective)
            if d.day != calendar.monthrange(d.year, d.month)[1]:
                raise DomainError('确认盘点须选择已结束的月末日期')
            projected = snapshot(state, family, effective)
            if projected['provisional']:
                raise DomainError('估值缺失或资金未核对，不能确认完整月末盘点')
            value = {'id': rid, 'effective_on': effective, 'recorded_at': now(), 'source_revision': state['revision'], 'snapshot': wire(projected)}
            self._put(db, 'family_closures', value, reason)
            return value
        raise DomainError('不支持的资产操作')

    def reminders(self):
        with self.store.connection() as db:
            family, state = read_state(db), self.store.state(db)
            rows = schedule_projection(family, str(today()))
            return {'revision': state['revision'], 'items': wire(rows), 'count': len(rows),
                    'overdue': 0, 'today': sum(r['days'] == 0 for r in rows)}



def validate_family_backup(db):
    """Reject malformed family data before replacing the active database."""
    family = read_state(db)
    ids = set()
    for item in family['items']:
        if not item['versions'] or item['id'] in ids:
            raise DomainError('资产索引无效')
        ids.add(item['id'])
        for v in item['versions']:
            if v['category'] not in CATEGORIES:
                raise DomainError('资产分类无效')
            currency(v['currency']); fraction(v['share']); day(v['effective_on'])
            lifetime = v.get('policy_lifetime', False)
            if not isinstance(lifetime, bool) or lifetime and (v['category'] != 'insurance' or v.get('policy_end')):
                raise DomainError('备份保险保障期限无效')
            if 'payment_plan' in v:
                from .family_plans import payment_plan
                plan = v['payment_plan']
                if not isinstance(plan, dict): raise DomainError('备份付款设置无效')
                payment_plan({k:value for k,value in plan.items() if k != 'principal'}, v['category'], plan.get('principal'))
    for val in family['values']:
        if val['item_id'] not in ids:
            raise DomainError('资产估值引用不存在的项目')
        money(val['amount'], nullable=True); day(val['effective_on'])
        for k in ('restricted', 'interest', 'fees'):
            money(val[k])
        if val['amount'] is not None and dec(val['restricted']) > dec(val['amount']):
            raise DomainError('备份受限资金大于余额')
    for row in family['schedules']:
        if row['item_id'] not in ids or row['status'] not in ('pending', 'done', 'cancelled'):
            raise DomainError('备份中的资产计划无效')
        day(row['due_date']); money(row['amount'])
    portfolio_ids = {r[0] for r in db.execute('SELECT id FROM portfolios')}
    for link in family['links']:
        if link['portfolio_id'] not in portfolio_ids:
            raise DomainError('备份关联组合不存在')
        day(link['effective_on']); fraction(link['share'])
        if not isinstance(link['baseline'], dict): raise DomainError('关联基线无效')
        if link.get('closed_on') and day(link['closed_on']) < day(link['effective_on']): raise DomainError('关联期间无效')
    for setting in family['settings']:
        day(setting['effective_on']); money(setting['living_expense'], nullable=True); money(setting['monthly_income'], nullable=True)
        if not 0 < dec(setting['reserve_months']) <= 120: raise DomainError('备份现金目标无效')
        if any(not isinstance(setting[k], bool) for k in ('debt_plan_complete','policy_plan_complete')): raise DomainError('备份计划完整性标记无效')
    link_ids = {r['id'] for r in family['links']}
    for rec in family['reconciliations']:
        if rec['link_id'] not in link_ids or not isinstance(rec['signatures'], dict): raise DomainError('备份资金核对无效')
        day(rec['effective_on']); text(rec['confirmation'], '核对依据', 300, True)
    for closure in family['closures']:
        day(closure['effective_on'])
        if not isinstance(closure['snapshot'], dict) or 'net_assets' not in closure['snapshot']: raise DomainError('备份盘点无效')
    for entry in family.get('income', []):
        if not isinstance(entry.get('versions'), list) or not entry['versions']: raise DomainError('收入历史记录无效')
        for value in entry['versions']:
            money(value['amount']); currency(value['currency']); day(value['date'])
            if value['kind'] not in ('salary','rent','fund','custom') or value['date'][:7] != value['month']: raise DomainError('收入记录无效')
    for table in (name for name in TABLES if name.removeprefix('family_') in family):
        for row in db.execute(f'SELECT id,data FROM {table}'):
            if json.loads(row[1]).get('id') != row[0]: raise DomainError('资产备份记录索引不一致')
