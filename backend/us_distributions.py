"""Cash distributions published by US ETF issuers, identified by ticker and CUSIP."""
from datetime import date, datetime
from io import BytesIO
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError

from openpyxl import load_workbook

from . import market
from .common import dec, ds

SSGA = 'https://www.ssga.com/library-content/products/fund-data/etfs/us/spdr-etf-historical-distributions.xlsx'
VANGUARD = 'https://investor.vanguard.com/irr/funds/profile/VIG.json'
ISSUERS = {'SPY', 'VIG'}


def us_date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.strptime(value, '%m/%d/%Y').date().isoformat()


def cash_notice(code, ex, record, pay, value, source, url, note):
    amount = ds(dec(value, positive=True))
    return dict(code=code, source_ref=f'cash:US:{code}:{ex}:USD', record_date=record, ex_date=ex,
                pay_date=pay, per_share=amount, currency='USD', source=source, source_url=url,
                original_rate=amount, original_unit='每份USD', status='published', coverage_note=note)


def state_street(data):
    with ZipFile(BytesIO(data)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024:
            raise market.ProviderError('State Street 分派文件超过大小限制')
    book = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        records = book['dividend'].values
        headers = next(records, ())
        columns = ('TICKER', 'CUSIP', 'EX-DATE', 'RECORD DATE', 'PAYABLE DATE',
                   'DIVIDEND ($)', 'SHORT TERM CAPITAL GAIN ($)', 'LONG TERM CAPITAL GAIN ($)')
        indexes = [headers.index(key) for key in columns]
        notices = []
        for row in records:
            code, cusip, ex, record, pay, dividend, short_gain, long_gain = (row[i] for i in indexes)
            if code != 'SPY':
                continue
            if cusip != '78462F103':
                raise market.ProviderError('State Street 分派表的证券身份不匹配')
            components = [dec(str(v).strip(), nonnegative=True) for v in (dividend, short_gain, long_gain)
                          if v is not None and str(v).strip()]
            notices.append(cash_notice(code, us_date(ex), us_date(record), us_date(pay), sum(components),
                                       'State Street·ETF分派表', SSGA, '总现金分派包含表中列明的股息与资本利得分派。'))
        return notices
    finally:
        book.close()


def vanguard(data):
    if data['dashboard']['ticker'] != 'VIG' or data['overview']['cusip'] != '921908844':
        raise market.ProviderError('Vanguard 分派表的证券身份不匹配')
    notices = []
    for row in data['distributions']['incomeCapitalGains']:
        if row['type'] != 'Dividend' or not str(row['share']).startswith('$'):
            raise market.ProviderError('Vanguard 分派类型或单位需要核对')
        # The US issuer profile defines its reinvest date as the ex-dividend date.
        ex = us_date(row['reInvestDate'])
        notices.append(cash_notice('VIG', ex, us_date(row['recordDate']), us_date(row['payableDate']),
                                   row['share'][1:], 'Vanguard·ETF分派表', VANGUARD,
                                   '发行方产品页近期分派；历次已同步公告保留。'))
    return notices


def notices(inst):
    if inst['market'] != 'US' or inst['currency'] != 'USD':
        raise market.ProviderError('发行方分派表仅适用于对应美元上市份额')
    try:
        if inst['code'] == 'SPY':
            rows = state_street(market.fetch(SSGA, binary=True))
        elif inst['code'] == 'VIG':
            rows = vanguard(market.parse_json(market.fetch(VANGUARD)))
        else:
            raise market.ProviderError('未配置该代码的发行方')
        if not rows or len({r['ex_date'] for r in rows}) != len(rows):
            raise market.ProviderError('发行方未返回可核对的唯一除息日记录')
        return sorted(rows, key=lambda r: r['ex_date'], reverse=True)
    except (IndexError, KeyError, TypeError, ValueError, BadZipFile, ParseError) as exc:
        raise market.ProviderError(f'发行方分派表未通过校验（{type(exc).__name__}）') from None
