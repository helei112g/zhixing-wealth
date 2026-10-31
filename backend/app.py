from __future__ import annotations

import asyncio
import base64
import hmac
import os
import secrets
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .common import ConflictError, DomainError, now, today, wire
from .service import Service
from .store import SCHEMA, Store
from .api_models import PortfolioRequest, EventRequest, AllocationRequest, NoteRequest, NoteMutationRequest, NoteDetail, NotePage, NoteDeletion, TransactionDetail, CancelRequest, TransactionPage, TransactionRow, SyncRequest
from .api_models import PlanCheck, AllocationResult, ReinvestmentEstimate, EventPreview, EventDeletionPreview, DividendReviewRequest, DividendReviewResult

from .environment import LABELS, claim_environment, cookie_name, workspace_for
from .version import VERSION, NAME, release_info

from .family_api import NotificationCommand

ROOT = Path(__file__).resolve().parent.parent


def create_app(data_dir=None, *, testing=False, auto_sync=True, frontend_directory=None, environment=None,
               read_only=False, deployment_guard_path=None):
    environment = environment or ('test' if testing else 'dev')
    directory = Path(data_dir or os.environ.get('PORTFOLIO_DATA_DIR') or workspace_for(ROOT) / 'local/dev/data')
    directory = claim_environment(directory, environment)
    session_cookie = cookie_name(directory, environment)
    store = Store(directory)
    service = Service(store)
    from .family_api import family_router
    household_router, household_notifications = family_router(store, environment)
    from .reminders import ReminderService
    reminder_service = ReminderService(store, household_notifications)
    csrf = secrets.token_urlsafe(32)
    session = secrets.token_urlsafe(32)
    instance = secrets.token_hex(16)
    guard_path = Path(deployment_guard_path) if deployment_guard_path is not None else None

    def writes_blocked():
        return read_only or (guard_path is not None and guard_path.exists())

    @asynccontextmanager
    async def lifespan(app):
        if writes_blocked():
            # Deployment acceptance exposes reads only; no background job,
            # notification or recovery task may write during the comparison.
            yield
            return
        store.recover_jobs()
        if auto_sync: service.start_sync(trigger='startup')
        async def clock():
            last = today().isoformat() if now()[11:16] >= '23:30' else ''
            while True:
                await asyncio.sleep(45)
                current = now()
                if not writes_blocked() and current[:10] != last and current[11:16] >= '23:30':
                    service.start_sync(trigger='schedule'); last = current[:10]
        async def notification_clock():
            while True:
                if not writes_blocked():
                    await asyncio.to_thread(household_notifications.reconcile)
                await asyncio.sleep(5)
        notification_task = asyncio.create_task(notification_clock())
        task = asyncio.create_task(clock()) if auto_sync else None
        try: yield
        finally:
            notification_task.cancel()
            try: await notification_task
            except asyncio.CancelledError: pass
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass

    app = FastAPI(title='知行财富 · 本地API', version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url='/api/openapi.json',
                  description='本机单用户投资账本。AI 客户端与界面共用服务。先 GET /api/bootstrap 获取会话与 revision；写入带 Cookie 和 X-CSRF-Token。金额为十进制字符串，收益率为比例，未知为 null。核心写入可携带 expected_revision 进行原子并发校验。详见 /api/guide。')
    app.state.service = service; app.state.store = store; app.state.instance = instance
    app.include_router(household_router)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost'] + (['testserver'] if testing else []))

    @app.middleware('http')
    async def local_guard(request: Request, call_next):
        if writes_blocked() and request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            return JSONResponse({'error': '版本正在只读验收或部署已阻断，暂不接受业务写入'}, status_code=503)
        origin = request.headers.get('origin')
        if origin:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            if parsed.hostname not in ('127.0.0.1', 'localhost') or origin.rstrip('/') != str(request.base_url).rstrip('/'):
                return JSONResponse({'error': '仅允许本机同源页面访问'}, status_code=403)
        if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            token = request.headers.get('x-csrf-token', '')
            cookie = request.cookies.get(session_cookie, '')
            if not hmac.compare_digest(token, csrf) or not hmac.compare_digest(cookie, session):
                return JSONResponse({'error': '会话校验失败，请刷新页面后重试'}, status_code=403)
        try:
            response = await call_next(request)
        except DomainError as exc:
            return JSONResponse({'error': str(exc)}, status_code=422)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith('/api/'): response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({'error': str(exc)}, status_code=409 if isinstance(exc, ConflictError) else 422)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        details = [{'field': '.'.join(map(str, e['loc'])), 'message': e['msg']} for e in exc.errors()]
        return JSONResponse({'error': '；'.join(f"{e['field']}: {e['message']}" for e in details), 'detail': details}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        traceback.print_exception(exc)
        return JSONResponse({'error': '操作未完成；原账本事务已回滚。请查看本地运行日志。'}, status_code=500)

    def reply(value): return JSONResponse(wire(value))

    @app.get('/api/health')
    def health():
        return {'ok': True, 'application': 'portfolio-journal', 'version': VERSION, 'environment': environment, 'schema': SCHEMA, 'release': release_info(), 'root': str(ROOT), 'data_directory':str(store.directory), 'instance': instance, 'read_only': writes_blocked()}

    @app.get('/api/bootstrap')
    def bootstrap():
        response = reply(dict(service.bootstrap(), csrf_token=csrf, environment=environment, environment_label=LABELS[environment], schema=SCHEMA))
        response.set_cookie(session_cookie, session, httponly=True, samesite='strict', path='/')
        return response

    @app.get('/api/guide', include_in_schema=False)
    def api_guide():
        return FileResponse(ROOT / 'docs' / 'api.md', media_type='text/plain; charset=utf-8')

    @app.get('/api/reminders')
    def reminders(): return reminder_service.read()

    @app.get('/api/notifications')
    def notification_status(): return household_notifications.status()

    @app.post('/api/notifications')
    def notification_action(command: NotificationCommand): return household_notifications.action(command.action)

    @app.get('/api/capabilities')
    def capabilities():
        return {'api_version': VERSION, 'openapi': '/api/openapi.json', 'guide': '/api/guide',
                'authentication': {'bootstrap': '/api/bootstrap', 'cookie': session_cookie, 'header': 'X-CSRF-Token'},
                'concurrency': {'field': 'expected_revision', 'conflict_status': 409,
                                'operations': ['portfolios', 'allocations', 'events', 'investment_notes', 'dividend_confirm', 'dividend_review', 'family']},
                'family': {'label': '资产分析', 'read': '/api/family/overview',
                           'catalog': '/api/family/catalog', 'command_schema': '/api/family/command-schema',
                           'recommended_operations': ['item','valuation','links','unlink','income'],
                           'comparison': {'summary_default': 'month', 'summary_baseline': 'previous_period_end', 'history_curve': 'yoy'},
                           'skills': ['zhixing-assets','zhixing-portfolios','zhixing-notes'], 'history': '/api/family/history',
                           'preview': '/api/family/preview', 'write': '/api/family/commands',
                           'expected_revision_required': True, 'portfolio_link': 'read-only; excludes unused budget',
                           'notifications': '/api/notifications', 'schema': SCHEMA,
                           'income': '/api/family/income', 'income_summary': {'field': 'summary', 'groups': ['salary','rent','fund','custom'], 'shares': '0–1; null when total is unknown or nonpositive'}, 'history_default_comparison': 'previous', 'summary': '/api/family/summary', 'recurring_payments': True,
                           'portfolio_operations': ['links', 'unlink'], 'receivables': True},
                'reminders': {'read': '/api/reminders', 'scope': 'system', 'providers': ['family','dividend','portfolio','sync','system']},
                'pagination': {'endpoint': '/api/portfolios/{portfolio_id}/transactions', 'page_size': 20, 'max_page_size': 100,
                               'filters': ['instrument_id', 'market', 'tag', 'asset_class', 'region'],
                               'sort': ['date desc', 'sequence desc', 'id desc'], 'tag_basis': 'current portfolio allocation'},
                'dashboard': {'endpoint': '/api/portfolios/{portfolio_id}/dashboard', 'default_view': 'full',
                              'views': {'full': 'summary plus historical curves, calendar and periods',
                                        'summary': 'same current financial values without historical projections'}},
                'numbers': 'decimal strings; rates are ratios except *_pct percentages; unknown values are null',
                'plan_funding': {'check_endpoint': '/api/portfolios/{portfolio_id}/instruments/{instrument_id}/plan-check?target_pct=50',
                                 'dashboard_field': 'plan_funding', 'shortfall_blocks_save': False},
                'forward_performance': {'endpoint': '/api/portfolios/{portfolio_id}/transactions/{event_id}',
                                        'months': [1, 3], 'basis': 'market series reference; separate from actual lot profit'},
                'investment_notes': {'endpoint': '/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes',
                                     'scope': ['portfolio_id', 'instrument_id'], 'body_storage': 'local Markdown files; DB stores paths only',
                                     'operations': ['list', 'read', 'create', 'edit', 'delete', 'undo_delete'],
                                     'expected_revision_required': True, 'included_in_full_backup': True},
                'event_kinds': ['BUY', 'SELL', 'DIVIDEND', 'REINVEST'], 'manual_prices': False,
                'event_deletion': {'preview': '/api/events/{event_id}/deletion-preview', 'preview_method': 'GET',
                                   'write': '/api/events/{event_id}', 'method': 'DELETE',
                                   'cascade_fields': ['cascade', 'event_ids', 'expected_revision', 'reason'],
                                   'shared_records': 'whole records listed explicitly; other purchase parents are retained',
                                   'audit_preserved': True, 'legacy_default': 'single event or transfer pair'},
                'corporate_actions': {'confirmation_kind': 'SPLIT', 'automatic_ledger_write': False,
                                      'cash_only_plans': 'full cash-only text including shareholder classes; cash amount conflicts remain review notes',
                                      'sync_recovery': 'latest successful corporate_actions item clears the same instrument sync reminder; retain job history',
                                      'pending_in': ['dashboard.todos', 'instrument_detail.todos'],
                                      'factor': 'new shares / old shares; 10 bonus 8 plus conversion 12 = 3',
                                      'cost_basis': 'preserve total cost; cash dividend and tax recorded separately',
                                      'effect_field': 'transactions[].facts.split; transaction_detail.facts.split',
                                      'preview_changes': 'split_changes: before/after quantities and remaining lots for affected SPLIT records',
                                      'backfill': 'replay remaining lots before ex-date; retain confirmed event; never confirm again for backfills',
                                      'duplicate_policy': 'one new SPLIT per portfolio/instrument/date; correct the existing event',
                                      'notice_link': 'source_split_id; date/factor mismatch reopens verify_split on the original event',
                                      'reference_price': 'prefer adjusted stock series; valuation uses raw prices and confirmed shares'},
                'reinvestment_estimate': {'endpoint': '/api/portfolios/{portfolio_id}/instruments/{instrument_id}/reinvestment-estimate',
                                         'query': ['date', 'gross_amount'], 'market': 'FUND', 'exact_date': True,
                                         'quantity_decimals': 2, 'rounding': 'ROUND_HALF_UP', 'read_only': True},
                'dividend_review': {'read': '/api/portfolios/{portfolio_id}/dividends',
                                    'write': '/api/dividends/{notice_id}/review', 'method': 'POST', 'mode': 'keep_recorded',
                                    'expected_revision_required': True, 'event_write': False,
                                    'audit': '/api/revisions/dividend_decision/{decision_id}',
                                    'yahoo_reference_tolerance': {'relative': '0.0001', 'per_share_absolute': '0.0001',
                                                                 'both_required': True, 'anchor': 'last reviewed basis'},
                                    'entitlement_tolerance': 'none; quantity, dates, currency and split factors compare exactly'},
                'storage': {'schema': SCHEMA, 'prices': 'skip identical latest automatic quote; preserve changed versions',
                            'jobs_visible': 5, 'jobs_total_retained': 20, 'jobs_finished_retained': 20, 'jobs_running_retained': True,
                            'automatic_backup_dates_retained': 30},
                'write_workflow': 'read → preview → confirm intent → write with expected_revision → read back',
                'idempotency': 'No automatic mutation retry. Reuse expected_revision: an already committed write makes it stale.'}

    @app.get('/api/reference-rate')
    def reference_rate(currency: str, date: str):
        from .common import currency as valid_currency, day, today
        from .ledger import Market
        c=valid_currency(currency); d=day(date)
        if d>today(): raise DomainError('交易汇率日期不能在未来')
        result={'currency':c,'rate':'1','date':str(d),'source':'CNY'} if c=='CNY' else Market(store.state()).fx_series[c].at(d)
        return reply({'reference':result,'note':'不使用全局区间估值覆盖；确认后随交易锁定'})

    @app.post('/api/portfolios')
    def create_portfolio(payload: PortfolioRequest):
        payload = payload.model_dump(exclude_unset=True)
        value=service.save_portfolio(payload)
        if auto_sync and value.get('benchmark_id'):service.start_sync(value['benchmark_id'],full=True,trigger='portfolio_create')
        return reply(value)

    @app.put('/api/portfolios/{portfolio_id}')
    def edit_portfolio(portfolio_id: str, payload: PortfolioRequest):
        payload = payload.model_dump(exclude_unset=True)
        value=service.save_portfolio(payload, portfolio_id)
        if auto_sync and value.get('benchmark_id'):service.start_sync(value['benchmark_id'],full=True,trigger='portfolio_edit')
        return reply(value)

    @app.post('/api/instruments')
    def create_instrument(payload: dict): return reply(service.save_instrument(payload))

    @app.put('/api/portfolios/{portfolio_id}/instruments/{instrument_id}', response_model=AllocationResult)
    def allocation(portfolio_id: str, instrument_id: str, payload: AllocationRequest):
        payload = payload.model_dump(exclude_unset=True)
        return reply(service.attach_instrument(portfolio_id, instrument_id, payload))

    @app.get('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/plan-check', response_model=PlanCheck)
    def plan_check(portfolio_id: str, instrument_id: str, target_pct: str):
        return reply(service.check_plan(portfolio_id, instrument_id, target_pct))

    @app.get('/api/portfolios/{portfolio_id}/dashboard')
    def dashboard(portfolio_id: str, start: str | None = None, end: str | None = None, grain: str = 'month', view: str = 'full'):
        return reply(service.dashboard(portfolio_id, start=start, end=end, grain=grain, view=view))

    @app.get('/api/portfolios/{portfolio_id}/instruments/{instrument_id}')
    def detail(portfolio_id: str, instrument_id: str): return reply(service.instrument_detail(portfolio_id, instrument_id))

    @app.get('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes', response_model=NotePage,
             summary='只读取本组合、本标的笔记；按发布时间倒序分页')
    def notes(portfolio_id: str, instrument_id: str, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return service.notes.list(portfolio_id, instrument_id, page=page, page_size=page_size)

    @app.get('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes/{note_id}', response_model=NoteDetail)
    def note(portfolio_id: str, instrument_id: str, note_id: str):
        return service.notes.get(portfolio_id, instrument_id, note_id)

    @app.post('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes', response_model=NoteDetail)
    def create_note(portfolio_id: str, instrument_id: str, payload: NoteRequest):
        return service.notes.save(portfolio_id, instrument_id, payload.model_dump())

    @app.put('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes/{note_id}', response_model=NoteDetail)
    def edit_note(portfolio_id: str, instrument_id: str, note_id: str, payload: NoteRequest):
        return service.notes.save(portfolio_id, instrument_id, payload.model_dump(), note_id)

    @app.delete('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes/{note_id}', response_model=NoteDeletion)
    def delete_note(portfolio_id: str, instrument_id: str, note_id: str, payload: NoteMutationRequest):
        return service.notes.set_deleted(portfolio_id, instrument_id, note_id, deleted=True, expected_revision=payload.expected_revision)

    @app.post('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/notes/{note_id}/restore', response_model=NoteDeletion, summary='撤销本组合、本标的一篇笔记的删除')
    def restore_note(portfolio_id: str, instrument_id: str, note_id: str, payload: NoteMutationRequest):
        return service.notes.set_deleted(portfolio_id, instrument_id, note_id, deleted=False, expected_revision=payload.expected_revision)

    @app.post('/api/events/preview', response_model=EventPreview)
    def preview(payload: EventRequest):
        payload = payload.model_dump(exclude_unset=True)
        return reply(service.preview_event(payload, payload.get('id')))

    @app.post('/api/events')
    def event(payload: EventRequest):
        payload = payload.model_dump(exclude_unset=True)
        value=service.save_event(payload)
        if auto_sync and value.get('instrument_id'):service.start_sync(value['instrument_id'],full=True,trigger='event_create')
        return reply(value)

    @app.put('/api/events/{event_id}')
    def edit_event(event_id: str, payload: EventRequest):
        payload = payload.model_dump(exclude_unset=True)
        value=service.save_event(payload,event_id)
        if auto_sync and value.get('instrument_id'):service.start_sync(value['instrument_id'],full=True,trigger='event_edit')
        return reply(value)

    @app.get('/api/events/{event_id}/deletion-preview', response_model=EventDeletionPreview, summary='只读预览关联删除清单及账务校验结果')
    def preview_deletion(event_id: str):
        return service.preview_deletion(event_id)

    @app.delete('/api/events/{event_id}')
    def delete_event(event_id: str, payload: CancelRequest):
        payload = payload.model_dump(exclude_unset=True)
        return reply(service.delete_event(event_id, payload.get('reason'), payload.get('expected_revision'),
                                          cascade=payload.get('cascade', False), event_ids=payload.get('event_ids')))

    @app.post('/api/reservations')
    def reserve(payload: dict): return reply(service.save_reservation(payload))

    @app.put('/api/reservations/{reservation_id}')
    def edit_reserve(reservation_id: str, payload: dict): return reply(service.save_reservation(payload, reservation_id))

    @app.get('/api/portfolios/{portfolio_id}/transactions', response_model=TransactionPage | list[TransactionRow],
             summary='读取本组合流水；显式page返回筛选分页对象，无参兼容全量数组')
    def transactions(request: Request, portfolio_id: str, instrument_id: str | None = None,
                     page: int | None = Query(None, ge=1), page_size: int = Query(20, ge=1, le=100),
                     market: str = 'ALL', tag: str = '', asset_class: str = '', region: str = ''):
        if 'scope' in request.query_params:
            raise DomainError('交易复盘筛选已移除，请删除 scope 参数')
        # Unparameterized legacy reads remain compatible. All new clients use the paginated envelope.
        if page is None and (market, tag, asset_class, region) == ('ALL', '', '', ''):
            return reply(service.transaction_rows(portfolio_id, instrument_id))
        return reply(service.transaction_page(portfolio_id, instrument_id, page=page or 1, page_size=page_size,
                     market=market, tag=tag, asset_class=asset_class, region=region))

    @app.get('/api/portfolios/{portfolio_id}/transactions/{event_id}', response_model=TransactionDetail, response_model_exclude_unset=True, summary='本组合流水详情、真实收益、同标的买卖导航和修订记录')
    def transaction_detail(portfolio_id: str, event_id: str):
        return service.transaction_detail(portfolio_id, event_id)

    @app.get('/api/portfolios/{portfolio_id}/sale-lots')
    def sale_lots(portfolio_id:str,instrument_id:str,date:str,exclude_event_id:str|None=None,sequence:str|None=None):
        return reply(service.sale_lots(portfolio_id,instrument_id,date,exclude_event_id,sequence))

    @app.get('/api/portfolios/{portfolio_id}/dividends')
    def dividend_candidates(portfolio_id:str,instrument_id:str|None=None):return reply(service.dividend_candidates(portfolio_id,instrument_id))

    @app.get('/api/portfolios/{portfolio_id}/instruments/{instrument_id}/reinvestment-estimate',
             response_model=ReinvestmentEstimate, summary='按同日场外基金单位净值估算再投份额；只读、不自动记账',
             responses={422: {'description': '组合/标的作用域不匹配、日期无效或在未来、再投总价非正或无效'}})
    def reinvestment_estimate(portfolio_id: str, instrument_id: str,
                              date: str = Query(description='再投净值归属日，YYYY-MM-DD'),
                              gross_amount: str = Query(description='不含手续费的再投总价，精确十进制字符串')):
        return service.reinvestment_estimate(portfolio_id, instrument_id, date, gross_amount)

    @app.post('/api/dividends/{notice_id}/confirm')
    def confirm_dividend(notice_id:str,payload:dict):return reply(service.confirm_dividend(notice_id,payload))

    @app.post('/api/dividends/{notice_id}/review', response_model=DividendReviewResult,
              responses={409:{'description':'读取后账本已变化；不写入'},422:{'description':'候选、原流水或核对依据不匹配；不写入'}})
    def review_dividend(notice_id:str,payload:DividendReviewRequest):
        result=service.review_dividend(notice_id,payload.model_dump())
        household_notifications.invalidate()
        return result

    @app.post('/api/dividends/{notice_id}/ignore')
    def ignore_dividend(notice_id:str,payload:dict):return reply(service.ignore_dividend(notice_id,payload))

    @app.get('/api/tags')
    def tag_report(portfolio_id: str | None = None, dimension: str = 'asset_class'):
        return reply(service.tag_report(portfolio_id, dimension))

    @app.post('/api/fx')
    def fx(payload: dict): return reply(service.save_fx(payload))

    @app.post('/api/sync', description='更新行情和公告缓存，不自动记账。corporate_actions 子项核验企业行动数据，失败不代表存在送转计划；纯现金方案的金额差异保留在 notes 和分红核对说明中。按同标的最新子项状态消除已恢复的同步提醒。')
    def sync(payload: SyncRequest): return reply(service.start_sync(payload.instrument_id, full=payload.full, trigger=payload.trigger))

    @app.get('/api/jobs', summary='最近20条同步任务', description='按启动时间倒序返回最多20条，界面展示最近5条；默认最多保存20条（含运行中），运行中任务不会被清理。启动时将上次中断任务标记失败。价格项saved/unchanged统计本次全部返回序列，count为标的主序列点数。')
    def jobs(): return reply({'jobs': store.jobs(), 'syncing': service.syncing})

    @app.get('/api/revisions/{entity}/{entity_id}')
    def revisions(entity: str, entity_id: str): return reply(service.revisions(entity, entity_id))

    @app.post('/api/transfers')
    def transfer(payload: dict): return reply(service.transfer(payload))

    @app.post('/api/csv/preview')
    def csv_preview(payload: dict): return reply(service.csv_preview(payload.get('text', ''), payload.get('category', 'events'), payload.get('portfolio_id'), payload.get('instrument_id')))

    @app.post('/api/csv/import')
    def csv_import(payload: dict):
        value=service.csv_import(payload.get('text', ''), payload.get('category', 'events'), payload.get('portfolio_id'), payload.get('instrument_id'))
        if auto_sync and value['imported'] and payload.get('category','events')=='events':service.start_sync(full=True,trigger='import')
        return reply(value)

    @app.get('/api/portfolios/{portfolio_id}/export.csv')
    def export_csv(portfolio_id: str):
        return Response(service.csv_export(portfolio_id), media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="portfolio-transactions.csv"'})

    @app.get('/api/backup')
    def backup():
        return Response(service.backup_download(), media_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename="portfolio-backup-{today()}.zip"'})

    @app.post('/api/restore')
    def restore(payload: dict):
        if payload.get('confirm') != 'RESTORE': raise DomainError('恢复备份需要明确确认')
        try: content = base64.b64decode(payload.get('content', ''), validate=True)
        except ValueError: raise DomainError('备份文件编码无效') from None
        value = service.restore(content)
        household_notifications.invalidate()
        value['notifications'] = household_notifications.reconcile(True)
        return reply(value)

    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith('api/'):
            return JSONResponse({'error': '接口不存在'}, status_code=404)
        dist = Path(frontend_directory).resolve() if frontend_directory else (ROOT / 'frontend' / 'dist').resolve()
        candidate = (dist / path).resolve()
        if not candidate.is_relative_to(dist): return Response(status_code=404)
        if candidate.is_file(): return FileResponse(candidate)
        index = dist / 'index.html'
        if index.exists(): return FileResponse(index, headers={'Cache-Control': 'no-cache'})
        return JSONResponse({'error': '界面尚未构建，请先运行frontend目录的npm run build'}, status_code=503)

    return app
