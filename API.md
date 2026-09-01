# API 参考（io.net / SpherePay 充值链路）

本文档记录 `io-net-api-recharge` 调用到的全部外部端点、请求/响应结构、错误码，
以及最关键的 **`key_set` 拦截根因与 TLS 指纹绕过方案**。

> 所有请求都必须通过 `curl_cffi` 且设置 `impersonate="chrome"`，见文末「风控与指纹」章节。

---

## 端点的身份归属

| 端点 | 域名 | 角色 |
|---|---|---|
| `workos/refresh` | `cloud.io.net` | io.net 登录态刷新，换取 JWT |
| `top-up-credits` | `api.io.solutions` | io.net 业务 API，创建充值订单 |
| `paymentLink/pay/{id}` | `api.spherepay.co` | SpherePay 支付网关，取待签交易 |

---

## 1. 刷新 Access Token

**用途**：用登录后 cookie 中的 `workos_refresh_token` 换取短期 JWT（约 5 分钟有效）。

```
POST https://cloud.io.net/api/workos/refresh
```

**Headers**
| Key | Value |
|---|---|
| `Origin` | `https://cloud.io.net` |
| `Content-Type` | `application/json` |

**Cookies**
| Key | Value |
|---|---|
| `workos_refresh_token` | 登录 io.net 后写入 `.io.net` 域的长时效 refresh token |

**Body**
```json
{}
```

**响应（200）**
```json
{
  "accessToken": "<JWT, 约 989 字符>",
  "user": { "id": "...", "email": "..." }
}
```

**错误**
| HTTP | 含义 | 处理 |
|---|---|---|
| 非 200 | refresh token 失效 / 被拒 | 重新登录获取新 refresh token，抛 `AuthError` |

> 注：返回的 JWT payload 中**不含**任何 `key_set` 字段；`key_set` 只出现在
> `top-up-credits` 接口的响应中（见第 2 节）。

---

## 2. 创建充值订单（top-up-credits）

**用途**：用 JWT 创建一个 crypto 充值订单，返回 `payment_link`。**该步骤不要求账号绑定钱包。**

```
POST https://api.io.solutions/v1/io-cloud/users/top-up-credits
```

**Headers**
| Key | Value | 说明 |
|---|---|---|
| `Token` | `<JWT from step 1>` | 注意是 `Token:` 而非 `Authorization:` |
| `Frontend-Version` | `1.141.1` | 需与官网前端版本一致 |
| `Origin` | `https://cloud.io.net` | 必须带 |

**Body**
```json
{
  "amount": "10",
  "payment_method": "crypto",
  "redirect_url": "https://cloud.io.net/cloud/dashboard?credits_purchase=success&amount=10&currency=usdc",
  "resource_id": "newPurchase"
}
```

**响应（200）**
```json
{
  "status": "succeeded",
  "data": {
    "payment_link": "https://spherepay.co/pay/paymentLink_3a8f071186e6416b91a3e82d228fea99"
  }
}
```

**错误：`key_set: true`（核心坑）**

```json
{ "key_set": true }
```
- **HTTP 400**，这是 Cloudflare Bot Management 的软拦截特征，不是业务错误。
- **根因不是账号锁定**，而是请求的 **TLS 指纹（JA3/JA4）** 被识别为 bot。
- **解法**：用 `curl_cffi` 且 `impersonate="chrome"`（见文末）。代码层面对应抛
  `KeySetBlockedError`。
- 实测已排除的无关因素：Referer / Cookie / User-Agent / Accept / sec-fetch-* /
  X-Requested-With / Origin / Frontend-Version / HTTP 版本 / JWT claim / 前置 GET 调用。

其他错误：非 200 且非 `key_set` → 抛 `TopupError`。

> 从 `payment_link` 中用正则 `paymentLink_[a-f0-9]+` 提取 `link_id`，供第 3 步使用。

---

## 3. 获取待签交易（SpherePay pay）

**用途**：用付款钱包公钥向 SpherePay 换取一笔待签的 Solana v0 交易（base64）。

```
POST https://api.spherepay.co/v1/public/paymentLink/pay/{link_id}
```

**Headers**
| Key | Value |
|---|---|
| `Origin` | `https://cloud.io.net` |
| `Content-Type` | `application/json` |

**Body**
```json
{ "account": "<付款钱包公钥>" }
```

**响应（200）**
```json
{
  "transaction": "AgAA...<base64 编码的 VersionedTransaction>"
}
```

> `transaction` 字段可能嵌套在 `data` 内（不同版本），代码用 `_extract_transaction`
> 递归查找。`client.get_payment_transaction` 负责该处理，成功返回 base64 字符串。
> 非 200 → 抛 `PaymentLinkError`。

**Why 这一步不依赖账号绑定钱包**：
SpherePay 的 payment_link 在创建时（第 2 步，由 JWT 身份决定）就已绑定到具体 io.net
账号（`meta.user_id`）。链上只验证"持有该公钥对应私钥的人是否签名授权扣款"，
完全不关心这个钱包是否"绑定"在某个 io.net 账号上。因此**账号未绑钱包也能充值成功**。

---

## 4. 广播交易（Solana RPC sendTransaction）

**用途**：将签名后的交易广播上链，真实扣款。

```
POST <Solana RPC, 默认 https://solana-rpc.publicnode.com>
```

**Body**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "sendTransaction",
  "params": [
    "<签名后交易的 base64>",
    { "encoding": "base64", "skipPreflight": true }
  ]
}
```

**响应（200）**
```json
{ "jsonrpc": "2.0", "id": 1, "result": "<链上交易签名>" }
```
错误 → `error` 字段非空 → 抛 `BroadcastError`。

**`dry_run=True` 时不发送请求**，直接返回占位串 `"dry_run_no_broadcast"`，用于无花费验证。

---

## 5. 链上验证（getTransaction）

**用途**：确认交易成功，且付款方 USDC 精确减少 `expected_usdc`。

```
POST <Solana RPC>
```

**Body**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "getTransaction",
  "params": [
    "<链上交易签名>",
    { "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0 }
  ]
}
```

**判定逻辑**（`wallet.verify_on_chain`）：
- `meta.err` 非空 → `ok=False`；
- 遍历 `preTokenBalances` / `postTokenBalances`，定位 `mint == USDC_MINT`
  且 `owner == payer_pub` 的条目，计算 `delta = post - pre`；
- `abs(abs(delta) - expected_usdc) < 0.01` → `ok=True`。

> USDC mint：`EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`

---

## 风控与 TLS 指纹（必读）

### 现象
同样的 token、同样的 header、同样的 body、同样的 IP，用 Node `https` / 普通 `requests`
发起 `top-up-credits` 一律返回 `400 {"key_set": true}`；即使用浏览器抓到的真实 token 原样重放也失败。

### 根因
Cloudflare Bot Management 按 **TLS 指纹（JA3/JA4）** 做软拦截：
- Node `https` 模块、Python 原生 `requests` 的 TLS ClientHello 与真实 Chrome 不同；
- Cloudflare 据此判定为 bot 流量，返回 `key_set:true` 而非真正处理请求。

### 解法（已在代码中实现）
使用 [`curl_cffi`](https://github.com/lexiforest/curl_cffi) 的 **`impersonate="chrome"`**，
它通过在底层复用 Chrome 的 TLS 握手特征（含 JA3/JA4 指纹、扩展顺序、ALPN 等）伪装成真实浏览器。

```python
from curl_cffi import requests
session = requests.Session(impersonate="chrome", verify=False)
# 之后所有 client 请求都走这个 session，key_set 拦截消失，返回 200。
```

### 批量使用的额外风险与已实现缓解

单笔通过 ≠ 高频批量通过。Cloudflare 还可能叠加**行为风控**（请求节奏、并发、IP 信誉）。
代码已在 `batch.py` 内置以下缓解（参数见 README「批量风控参数」章节）：

| 缓解 | 实现 | 状态 |
|---|---|---|
| 抖动间隔 | 账号间 `sleep(uniform(min_gap, max_gap))` | ✅ 已实现 |
| 阶梯冷却 | 每 `step_size` 个账号 `sleep(step_cooldown)` | ✅ 已实现 |
| 失败退避 | `KeySetBlockedError` / `BroadcastError` 指数退避重试 | ✅ 已实现 |
| 账号隔离 | 单账号失败用 try/except 隔离，不连坐整批 | ✅ 已实现 |
| IP 轮换 | `curl_cffi` 预留 `proxies=` 但**未接入** | ❌ 未实现（剩余风险） |
| verify 重试 | **刻意不重试**，避免双花 | ⚠️ 设计决策 |

**verify 不重试（与用户确认的设计）**：广播返回签名即视为已上链尝试，重放同笔已签
交易可能造成双花，故 `verify_on_chain` 的失败（含确认查询的网络抖动）一律判账号失败、不重试。

**建议（使用者自行落地）**：
1. 先用 2–3 个账号做 `dry-run` + 真实广播冒烟，观察是否出现 429 / 风控升级；
2. 若检测到单 IP 限流，自行在 `client.py` 的 `curl_cffi` session 配置 `proxies` 接入代理池；
3. 批量 reseller 的 ToS 合规边界需自行核查（见 README⚠️合规风险）。

---

## 错误码汇总

| 异常类 | 触发位置 | 含义 |
|---|---|---|
| `AuthError` | `refresh_access_token` | refresh 失败 / 无 accessToken |
| `KeySetBlockedError` | `create_topup` | 收到 `key_set:true`（TLS 指纹未伪装） |
| `TopupError` | `create_topup` / `build_transaction` | 建链失败 / link_id 解析失败 |
| `PaymentLinkError` | `get_payment_transaction` | pay 失败 / 无 transaction 字段 |
| `SignError` | `sign_transaction` | 私钥 / 交易格式错误 |
| `BroadcastError` | `broadcast_transaction` | RPC 错误 / 网络异常 |

所有异常均继承自 `IonetRechargeError`，可统一 `except IonetRechargeError` 捕获。
