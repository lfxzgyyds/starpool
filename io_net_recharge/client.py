# -*- coding: utf-8 -*-
"""io.net / SpherePay 充值 HTTP 客户端。

充值流程（纯 API，无需浏览器点击）：

  1. refresh   POST https://cloud.io.net/api/workos/refresh
               用 cookie 中的 workos_refresh_token 换取 accessToken（JWT）。
  2. top-up   POST https://api.io.solutions/v1/io-cloud/users/top-up-credits
               用 JWT 创建充值订单，返回 payment_link。
  3. pay      POST https://api.spherepay.co/v1/public/paymentLink/pay/{link_id}
               用付款钱包公钥换取待签的 Solana 交易（base64）。

【关键坑，详见 API.md】
  - 所有请求必须用 curl_cffi 的 impersonate="chrome" 伪装 Chrome 的 TLS 指纹，
    否则 Cloudflare Bot Management 会返回 {"key_set": true} 软拦截。
  - refresh token 来自 io.net 登录后写入的 cookie：workos_refresh_token。
  - 账号是否“绑定钱包”与充值无关：链上只认私钥签名，充值归属由
    payment_link 与 io.net 账号的绑定决定（JWT 创建时确定）。
"""
from __future__ import annotations

import re
from curl_cffi import requests

from .errors import AuthError, TopupError, PaymentLinkError, KeySetBlockedError
from .models import TransactionPayload

CLOUD_BASE = "https://cloud.io.net"
API_BASE = "https://api.io.solutions/v1/io-cloud"
SPHEREPAY_BASE = "https://api.spherepay.co/v1/public"
FRONTEND_VERSION = "1.141.1"
REDIRECT_URL = (
    "https://cloud.io.net/cloud/dashboard"
    "?credits_purchase=success&amount=10&currency=usdc"
)


class IoNetRechargeClient:
    """封装 io.net 充值所需的三个 HTTP 端点。

    Args:
        refresh_cookie: io.net 登录后 cookie 中的 workos_refresh_token 值。
        amount: 充值金额（USDC，字符串），默认 "10"。
        frontend_version: io.net 前端版本号，需与官网一致（默认 1.141.1）。
        verify_tls: 是否校验 TLS 证书，默认 False（与 curl_cffi 行为一致）。
    """

    def __init__(
        self,
        refresh_cookie: str,
        amount: str = "10",
        frontend_version: str = FRONTEND_VERSION,
        verify_tls: bool = False,
    ) -> None:
        self.refresh_cookie = refresh_cookie
        self.amount = amount
        self.frontend_version = frontend_version
        # impersonate="chrome" 是绕过 Cloudflare key_set 拦截的核心，不可省略。
        self._session = requests.Session(impersonate="chrome", verify=verify_tls)

    # ---- 步骤 1：刷新 accessToken ----
    def refresh_access_token(self) -> str:
        """用 refresh cookie 换取 accessToken（JWT）。

        Returns:
            约 989 字符的 JWT，有效期约 5 分钟。

        Raises:
            AuthError: HTTP 非 200 或未返回 accessToken。
        """
        r = self._session.post(
            f"{CLOUD_BASE}/api/workos/refresh",
            headers={"Origin": CLOUD_BASE},
            cookies={"workos_refresh_token": self.refresh_cookie},
            json={},
            timeout=20,
        )
        if r.status_code != 200:
            raise AuthError(f"refresh 失败 HTTP {r.status_code}: {r.text[:200]}")
        tok = (r.json() or {}).get("accessToken")
        if not tok:
            raise AuthError(f"refresh 未返回 accessToken: {r.text[:200]}")
        return tok

    # ---- 步骤 2：创建充值订单 ----
    def create_topup(self, token: str) -> str:
        """创建充值订单，返回 payment_link 完整 URL。

        Raises:
            KeySetBlockedError: 收到 key_set:true（通常是 TLS 指纹未伪装）。
            TopupError: 其他失败或未返回 payment_link。
        """
        r = self._session.post(
            f"{API_BASE}/users/top-up-credits",
            headers={
                "Token": token,
                "Frontend-Version": self.frontend_version,
                "Origin": CLOUD_BASE,
            },
            json={
                "amount": self.amount,
                "payment_method": "crypto",
                "redirect_url": REDIRECT_URL,
                "resource_id": "newPurchase",
            },
            timeout=20,
        )
        # key_set:true 是 Cloudflare 指纹拦截的特征响应。
        if r.status_code == 400 and "key_set" in r.text:
            raise KeySetBlockedError(
                "收到 key_set:true —— Cloudflare 指纹拦截。"
                "请确认使用 curl_cffi impersonate='chrome' 发起请求。"
            )
        if r.status_code != 200:
            raise TopupError(f"top-up 失败 HTTP {r.status_code}: {r.text[:200]}")
        link_url = (r.json() or {}).get("data", {}).get("payment_link")
        if not link_url:
            raise TopupError(f"top-up 未返回 payment_link: {r.text[:200]}")
        return link_url

    # ---- 步骤 3：获取待签交易 ----
    def get_payment_transaction(self, token: str, link_id: str, pubkey: str) -> str:
        """用付款钱包公钥换取 SpherePay 返回的待签 Solana 交易（base64）。

        Raises:
            PaymentLinkError: HTTP 非 200 或响应未含 transaction 字段。
        """
        r = self._session.post(
            f"{SPHEREPAY_BASE}/paymentLink/pay/{link_id}",
            headers={"Origin": CLOUD_BASE},
            json={"account": pubkey},
            timeout=20,
        )
        if r.status_code != 200:
            raise PaymentLinkError(f"pay 失败 HTTP {r.status_code}: {r.text[:200]}")
        tx = _extract_transaction(r.json())
        if not tx:
            raise PaymentLinkError(f"pay 响应未含 transaction: {r.text[:200]}")
        return tx

    # ---- 组合：一步走完 1->2->3 ----
    def build_transaction(self, pubkey: str) -> TransactionPayload:
        """完整执行 refresh -> top-up -> pay，返回待签交易对象。

        Args:
            pubkey: 付款钱包公钥（Solana 地址）。

        Returns:
            含 link_id / link_url / tx_b64 / pub 的 TransactionPayload。
        """
        token = self.refresh_access_token()
        link_url = self.create_topup(token)
        m = re.search(r"paymentLink_[a-f0-9]+", link_url)
        if not m:
            raise TopupError(f"无法从 link_url 解析 link_id: {link_url}")
        link_id = m.group(0)
        tx_b64 = self.get_payment_transaction(token, link_id, pubkey)
        return TransactionPayload(
            link_id=link_id, link_url=link_url, tx_b64=tx_b64, pub=pubkey
        )


def _extract_transaction(obj):
    """从 SpherePay 响应递归提取 transaction 字段（base64 字符串）。"""
    if isinstance(obj, dict):
        if isinstance(obj.get("transaction"), str):
            return obj["transaction"]
        if obj.get("data"):
            return _extract_transaction(obj["data"])
    if isinstance(obj, list):
        for item in obj:
            found = _extract_transaction(item)
            if found:
                return found
    return None
