from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, SecretStr
from typing import Annotated, Literal
from ..common import DomainError

class ResolveRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=16)

Scope=Literal['quotes','fx','index_prices','memberships','valuations','financials','market']
PolicyScope=Literal['quotes','fx','index_prices','memberships','valuations','financials']
Basis=Literal['price','total_return']
Method=Literal['overall','median','arithmetic','mcw','ew','ewpvo']
Part=Literal['series','constituents','financials','sources']


class FxResource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope:Literal['fx']
    currency:Literal['HKD','USD']
    full:StrictBool=False


class QuoteResource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope:Literal['quotes']
    instrument_id:str|None=Field(description='精确标的ID；null表示全部已建档标的')
    full:StrictBool=False


class IndexResource(BaseModel):
    model_config=ConfigDict(extra='forbid')
    full:StrictBool=False
    target:str=Field(description='研究列表返回的正式指数ID')


class IndexPriceResource(IndexResource):
    scope:Literal['index_prices']
    module:Literal['index_info','price','total_return','volatility']


class MembershipResource(IndexResource):
    scope:Literal['memberships']
    module:Literal['memberships','historical_memberships']


class ValuationResource(IndexResource):
    scope:Literal['valuations']
    module:Literal['valuations','stock_valuations','market_activity']


class FinancialResource(IndexResource):
    scope:Literal['financials']
    module:Literal['financials','historical_financials','index_financials']


class MarketResource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope: Literal['market']
    full: StrictBool = False
    group: Literal['full_market','composites','calendar','china_rates','us_rates','credit','repo','pmi','prices','money','social_financing']
    series: Literal['sh_composite','sz_composite','cpi_yoy','ppi_yoy','fr007','fdr007'] | None = None


UpdateResource=Annotated[FxResource|QuoteResource|IndexPriceResource|MembershipResource|ValuationResource|FinancialResource|MarketResource,Field(discriminator='scope')]

class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope:PolicyScope
    enabled:StrictBool
    cadence:Literal['daily','trade_day','weekly','manual']
    time:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone:Literal['Asia/Shanghai']='Asia/Shanghai'

class SettingsRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    policies:list[Policy]=Field(min_length=6,max_length=6)
    expected_settings_revision:int=Field(ge=0,strict=True)

class UpdateRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scopes:list[Scope]=Field(default_factory=lambda:['quotes','fx','index_prices','memberships','valuations','financials'],min_length=1,max_length=7)
    request_id:str=Field(min_length=1,max_length=160)
    full:StrictBool=False
    targets:list[str]|None=Field(None,min_length=1,max_length=100)
    resources:list[UpdateResource]|None=Field(None,min_length=1,max_length=200,description='精确更新或重试范围；每项scope须属于scopes，重试保持失败项原resource')

class ScenarioRequest(BaseModel):
    method:Method='overall'
    model_config=ConfigDict(extra='forbid')
    analysis_id:str=Field(description='从指数详情读取的原分析版本；变化后409，不拼接新旧数据')
    basis:Basis='price'
    metric:Literal['pe','pb']='pe'
    target_multiple:str|int|float=Field(description='正的目标PE/PB，推荐精确十进制字符串')
    change_ratio:str|int|float=Field('0',description='盈利或净资产变化比例，大于-1；0.1表示10%，推荐十进制字符串')
    accept_fixed_basket_assumption:StrictBool=Field(description='须接受固定成分、股数、权重及除数，以及全部成分价格和盈利/净资产各自同比例变化的假设；false不能试算')
    reference_window:Literal[3,5,10]=5


class ScenarioPoint(BaseModel):
    change_pct:str=Field(description='百分数，10表示10%')
    point:str


class ReferencePoint(BaseModel):
    multiple:str
    point:str


class ScenarioResult(BaseModel):
    analysis_id:str
    basis:Basis
    metric:Literal['pe','pb']
    point:str
    current_point:str
    target_multiple:str
    change_ratio:str
    curve:list[ScenarioPoint]
    reference_window:Literal[3,5,10]
    reference_points:dict[str,ReferencePoint]=Field(description='有效窗口的估值分位参照；样本不足为空对象，不是预测概率')
    formula:str
    assumption:str


class CollectionPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=16)
    modules:list[str]=Field(min_length=1,max_length=12)
    history_start:str|None=None


class CollectionSave(CollectionPreview):
    enabled:StrictBool=True
    expected_research_revision:int=Field(ge=0,strict=True)


class TokenSave(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:SecretStr=Field(min_length=8,max_length=512)
    expected_revision:int=Field(ge=0,strict=True)


class AccessSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:int=Field(ge=0,strict=True)
    enabled:StrictBool
    limit:int|None=Field(default=None,strict=True,description='兼容旧客户端，忽略该值；本机不设调用上限')
    automatic:StrictBool=False
    clear_block:str|None=None


class SourceCheck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    analysis_id:str=Field(pattern='^[a-f0-9]{64}$')


def router(service):
    api=APIRouter(tags=['指数研究与数据更新'],responses={
        409:{'description':'原研究/设置版本、分析快照或request_id负载冲突；先回读核对'},
        422:{'description':'输入、身份配对、数据口径或条件假设无效；保留缺口，不补零'}})
    @api.get('/api/research/catalog')
    def catalog():
        from .providers import CATALOG
        from .lixinger import MODULES,MODULE_SOURCES
        return dict(items=CATALOG,modules=MODULES,module_sources=MODULE_SOURCES,default_modules=MODULES,subscriptions=service.profiles(),research_revision=service.revision())
    @api.post('/api/research/collection/preview', summary='新指数在线校验理杏仁身份和日线；已保存指数只做本地预览')
    def collection_preview(body:CollectionPreview):return service.collection.preview(body.code,body.modules,body.history_start)
    @api.put('/api/research/collection', summary='按原研究版本保存订阅；首次添加同时排队全量同步')
    def collection_save(body:CollectionSave):return service.collection.save(body.code,body.modules,body.history_start,body.enabled,body.expected_research_revision)
    @api.get('/api/settings/lixinger')
    def access_status():return dict(service.access.status(), automatic=False)
    @api.put('/api/settings/lixinger/token',summary='仅由用户在本机页面输入凭据；保存到本地文件，响应不返回Token')
    def access_token(body:TokenSave):
        if not service.writes_allowed():raise DomainError('系统处于只读状态')
        return service.access.save_token(body.token.get_secret_value(),body.expected_revision)
    @api.put('/api/settings/lixinger')
    def access_settings(body:AccessSettings):
        if not service.writes_allowed():raise DomainError('系统处于只读状态')
        return service.access.configure(body.expected_revision,body.enabled,body.limit,False,body.clear_block)
    @api.get('/api/research/indices/{index_id}/studio')
    def studio(index_id:str,basis:Basis='price',method:Method='mcw',window:int=Query(3),metric:Literal['pe','pb','ps','dividend_yield']='pe'):
        from .analytics import studio_analysis
        return studio_analysis(service,index_id,basis,method,window,metric)
    @api.get('/api/research/indices/{index_id}/versions')
    def versions(index_id:str):return service.versions(index_id)
    @api.post('/api/research/indices/{index_id}/source-check')
    def check_source(index_id:str,body:SourceCheck):return service.check_source(index_id,body.analysis_id)
    @api.get('/api/research/indices/{index_id}/versions/{snapshot_id}')
    def frozen(index_id:str,snapshot_id:str):return service.frozen(index_id,snapshot_id)
    @api.get('/api/research/revision')
    def revision():
        with service.store.lock:return dict(research_revision=service.revision(),updating=service.updating())
    @api.get('/api/research/indices')
    def listing(kind:str|None=None,page:int=Query(1,ge=1)):return service.listing(kind,page)
    @api.post('/api/research/indices/resolve')
    def resolve(body:ResolveRequest):return service.resolve(body.code)
    @api.post('/api/research/indices/valuation-preview', deprecated=True)
    @api.post('/api/research/indices', deprecated=True)
    def retired_selection():
        from fastapi.responses import JSONResponse
        return JSONResponse({'error':'旧估值后缀验证和建档入口已退役；请使用 /api/research/collection/preview 与 PUT /api/research/collection'},status_code=410)
    @api.get('/api/research/indices/{index_id}')
    def detail(index_id:str,basis:Basis='price',method:Method='overall',
               window:int=Query(3,json_schema_extra={'enum':[0,3,5,10]}),asof:str|None=None):
        if asof is not None:raise DomainError('尚未开放严格历史时点查询；当前响应为来源当前可得版本')
        return service.detail(index_id,basis,method,window)
    @api.get('/api/research/indices/{index_id}/{part}')
    def projection(index_id:str,part:Part,basis:Basis='price',window:int=Query(3,json_schema_extra={'enum':[0,3,5,10]}),method:Method='overall',analysis_id:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
        return service.projection(index_id,part,basis,window,analysis_id,page,page_size,method)
    @api.post('/api/research/indices/{index_id}/scenarios',response_model=ScenarioResult,
              summary='只读条件试算，不建档、不触发更新、不写账本')
    def scenario(index_id:str,body:ScenarioRequest):return service.scenario(index_id,body.model_dump())
    @api.get('/api/settings/data-updates')
    def settings():return service.settings()
    @api.put('/api/settings/data-updates')
    def save_settings(body:SettingsRequest):return service.save_settings(body.model_dump())
    @api.post('/api/data-updates/run',status_code=202)
    def run(body:UpdateRequest):return service.run(body.model_dump(exclude_unset=True))
    @api.get('/api/data-updates/jobs/{job_id}')
    def job(job_id:str):return service.updates.get(job_id)
    @api.get('/api/data-updates/jobs')
    def jobs():return {'jobs':service.jobs()}
    @api.get('/api/data-updates/history', summary='合并公共数据与行情更新，父子任务去重，按开始时间倒序返回最近5条')
    def history(area: Literal['portfolio', 'research'] | None = None):return service.updates.history(area)
    return api
