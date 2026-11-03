from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Annotated, Literal
from ..common import DomainError

class ResolveRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=16)

class AddRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    resolve_token:str
    valuation_token:str=Field(min_length=1,max_length=4096,description='估值预验证返回的原token；同时验证PE/PB成功，或显式选择暂不接入')
    request_id:str=Field(min_length=1,max_length=160)
    expected_research_revision:int=Field(ge=0,strict=True)


class ValuationPreviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=16)
    valuation_suffix:Literal['CSI','SH','SZ','LG']|None=Field(description='显式选择后缀；null表示暂不接入估值，不自动推断')


class ValuationMetricPreview(BaseModel):
    status:Literal['available','error']
    count:int|None=None
    start_date:str|None=None
    date:str|None=None
    current:str|None=Field(None,description='整体法最新有效观测，十进制字符串；失败为null')
    reason:str|None=None


class ValuationPreviewResponse(BaseModel):
    code:str|None
    status:Literal['available','unavailable','skipped']
    metrics:dict[str,ValuationMetricPreview]
    valuation_token:str|None=Field(description='PE和PB均验证成功或显式不接入时返回；失败为null')
    expires_at:int
    reason:str|None=None


Scope=Literal['quotes','fx','index_prices','memberships','valuations','financials']
Basis=Literal['price','total_return']
Method=Literal['overall','median','arithmetic']
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
    target:str=Field(description='研究列表返回的正式指数ID')


class IndexPriceResource(IndexResource):
    scope:Literal['index_prices']
    module:Literal['price','total_return']


class MembershipResource(IndexResource):
    scope:Literal['memberships']
    module:Literal['memberships','historical_memberships']


class ValuationResource(IndexResource):
    scope:Literal['valuations']
    module:Literal['valuations','stock_valuations']


class FinancialResource(IndexResource):
    scope:Literal['financials']
    module:Literal['financials','historical_financials']


UpdateResource=Annotated[FxResource|QuoteResource|IndexPriceResource|MembershipResource|ValuationResource|FinancialResource,Field(discriminator='scope')]

class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    scope:Scope
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
    scopes:list[Scope]=Field(default_factory=lambda:['quotes','fx','index_prices','memberships','valuations','financials'],min_length=1,max_length=6)
    request_id:str=Field(min_length=1,max_length=160)
    targets:list[str]|None=Field(None,min_length=1,max_length=100)
    resources:list[UpdateResource]|None=Field(None,min_length=1,max_length=200,description='精确更新或重试范围；每项scope须属于scopes，重试保持失败项原resource')

class ScenarioRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    analysis_id:str=Field(description='从指数详情读取的原分析版本；变化后409，不拼接新旧数据')
    basis:Basis='price'
    metric:Literal['pe','pb']='pe'
    target_multiple:str|int|float=Field(description='正的目标PE/PB，推荐精确十进制字符串')
    change_ratio:str|int|float=Field('0',description='盈利或净资产变化比例，大于-1；0.1表示10%，推荐十进制字符串')
    accept_fixed_basket_assumption:StrictBool=Field(description='须明确接受固定成分、权重及除数的假设；false不能试算')
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


def router(service):
    api=APIRouter(tags=['指数研究与数据更新'],responses={
        409:{'description':'原研究/设置版本、分析快照或request_id负载冲突；先回读核对'},
        422:{'description':'输入、身份配对、数据口径或条件假设无效；保留缺口，不补零'}})
    @api.get('/api/research/revision')
    def revision():
        with service.store.lock:return dict(research_revision=service.revision(),updating=service.updating())
    @api.get('/api/research/indices')
    def listing(kind:str|None=None,page:int=Query(1,ge=1)):return service.listing(kind,page)
    @api.post('/api/research/indices/resolve')
    def resolve(body:ResolveRequest):return service.resolve(body.code)
    @api.post('/api/research/indices/valuation-preview',response_model=ValuationPreviewResponse,
              summary='只读验证所选来源的PE和PB；不建档、不保存快照或更新任务')
    def valuation_preview(body:ValuationPreviewRequest):return service.valuation_preview(body.code,body.valuation_suffix)
    @api.post('/api/research/indices')
    def add(body:AddRequest):return service.add(body.model_dump())
    @api.get('/api/research/indices/{index_id}')
    def detail(index_id:str,basis:Basis='price',method:Method='overall',
               window:int=Query(3,json_schema_extra={'enum':[3,5,10]}),asof:str|None=None):
        if asof is not None:raise DomainError('尚未开放严格历史时点查询；当前响应为来源当前可得版本')
        return service.detail(index_id,basis,method,window)
    @api.get('/api/research/indices/{index_id}/{part}')
    def projection(index_id:str,part:Part,basis:Basis='price',window:int=Query(3,json_schema_extra={'enum':[3,5,10]}),method:Method='overall',analysis_id:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
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
    return api
