"""Versioned, compressed JSON payloads inside SQLite's existing backup boundary."""
import base64
import hashlib
import json
import zlib
from ..common import DomainError,json_dumps

MAX_PAYLOAD=32*1024*1024


def encode(value):
    raw=json_dumps(value).encode()
    if len(raw)>MAX_PAYLOAD:raise DomainError('研究快照超过32 MiB，旧版本保留')
    return hashlib.sha256(raw).hexdigest(),json_dumps({'codec':'zlib-base64-v1','body':base64.b64encode(zlib.compress(raw,9)).decode()})


def _decode(content,digest):
    obj=json.loads(content)
    if obj.get('codec')!='zlib-base64-v1':raise DomainError('研究快照编码不受支持')
    payload=base64.b64decode(obj['body'],validate=True);d=zlib.decompressobj()
    raw=d.decompress(payload,MAX_PAYLOAD+1)
    if len(raw)>MAX_PAYLOAD or not d.eof or d.unused_data or d.unconsumed_tail:raise DomainError('研究快照长度或压缩流异常')
    if hashlib.sha256(raw).hexdigest()!=digest:raise DomainError('研究快照校验不一致')
    value=json.loads(raw)
    if not isinstance(value,dict):raise DomainError('研究快照结构无效')
    return value


def decode(content,digest):
    try:return _decode(content,digest)
    except DomainError:raise
    except (ValueError,KeyError,TypeError,zlib.error):raise DomainError('研究快照编码或JSON无效') from None
