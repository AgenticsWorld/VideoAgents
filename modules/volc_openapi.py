"""火山引擎 OpenAPI 签名调用(HMAC-SHA256,同官方 SDK Signer)。

服务端(账单余额 / 豆包音色 ListSpeakers / 方舟素材资产)与 genmedia 子进程
(长镜头续接素材入虚拟人像库,modules/ark_assets.py)共用,签名只此一份。
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request


def signed_call(ak: str, sk: str, action: str, version: str,
                body: dict | None = None,
                service: str = "billing", region: str = "cn-north-1",
                host: str = "open.volcengineapi.com") -> dict:
    """火山引擎 OpenAPI 调用;body 为 None 走 GET,否则 POST JSON。返回整个响应 JSON
    (含 ResponseMetadata / Result),HTTP 错误原样抛 urllib.error.HTTPError。"""
    method = "GET" if body is None else "POST"
    payload = b"" if body is None else json.dumps(body).encode()
    query = urllib.parse.urlencode(sorted({"Action": action, "Version": version}.items()))
    xdate = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {"host": host, "x-date": xdate, "x-content-sha256": payload_hash}
    if body is not None:
        headers["content-type"] = "application/json; charset=utf-8"
    signed = ";".join(sorted(headers))
    canon = "\n".join([method, "/", query,
                       "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
                       signed, payload_hash])
    scope = f"{xdate[:8]}/{region}/{service}/request"
    sts = "\n".join(["HMAC-SHA256", xdate, scope,
                     hashlib.sha256(canon.encode()).hexdigest()])

    def h(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    k_sign = h(h(h(h(sk.encode(), xdate[:8]), region), service), "request")
    sig = hmac.new(k_sign, sts.encode(), hashlib.sha256).hexdigest()
    req_headers = {
        "Authorization": (f"HMAC-SHA256 Credential={ak}/{scope}, "
                          f"SignedHeaders={signed}, Signature={sig}"),
        "X-Date": xdate, "X-Content-Sha256": payload_hash,
    }
    if body is None:
        req = urllib.request.Request(f"https://{host}/?{query}", headers=req_headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8", "replace"))
    req_headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(f"https://{host}/?{query}", data=payload,
                                 headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
