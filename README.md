# io-net-api-recharge

> 通过纯 API（无需浏览器点击）为 io.net 账号充值 USDC 的 Python 工具包。
> 已实测跑通整条链路：refresh → 建链 → 取待签交易 → 离线签名 → 广播 → 链上确认。

[English](#english) | [中文](#中文)

---

## 中文

### ⚠️ 风险提示（必读）

1. **资金风险**：本工具执行真实链上交易，广播即真实扣款（默认每笔 10 USDC + 少量 SOL gas）。
   任何带 `--broadcast` 或 `dry_run=False` 的调用都会花钱，请务必先用 `--dry-run` 验证链路。
2. **密钥风险**：配置文件中包含钱包私钥（base58）。**请勿提交私钥到公开仓库**，
   建议使用环境变量或本地未跟踪文件，并将 `config.json` 加入 `.gitignore`。
3. **合规风险**：io.net / SpherePay 的 ToS 可能对自动化、批量注册、reseller 模式有限制。
   本仓库仅作技术可行性验证，**使用者需自行评估并承担合规责任**。
4. **指纹伪装**：所有出向请求必须使用 `curl_cffi` 的 `impersonate="chrome"` 伪装 Chrome TLS
   指纹，否则会被 Cloudflare Bot Management 以 `{"key_set": true}` 软拦截（详见 [API.md](./API.md)）。

### ✨ 特性

- **纯 API、零浏览器交互**：登录态通过 `workos_refresh_token` cookie 复用，无需 Selenium/CDP。
- **账号无需绑定钱包即可充值**：链上只认私钥签名，充值归属由 payment_link 与账号的绑定决定。
- **离线签名**：用 `solders` 在本地对 v0 交易签名，私钥不出本机。
- **批量编排**：`run_batch` / `cli.py batch` 支持多账号循环 build→sign→broadcast。
- **链上验证**：广播后自动核对付款方 USDC 精确扣减。
- **不花钱 dry-run**：每个入口都支持 `dry_run`，可在不广播的情况下验证全链路。

### 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    cli.py  (single/batch/verify)            │
├─────────────────────────────────────────────────────────────┤
│   io_net_recharge.client   IoNetRechargeClient              │
│     ├─ refresh_access_token()    workos/refresh             │
│     ├─ create_topup()            api.io.solutions top-up    │
│     ├─ get_payment_transaction() spherepay pay/{id}         │
│     └─ build_transaction()       组合 1→2→3                 │
├─────────────────────────────────────────────────────────────┤
│   io_net_recharge.wallet    sign / broadcast / verify        │
│     ├─ sign_transaction()        solders 离线签名           │
│     ├─ broadcast_transaction()   Solana RPC sendTransaction  │
│     └─ verify_on_chain()         getTransaction USDC 核对    │
├─────────────────────────────────────────────────────────────┤
│   io_net_recharge.batch     run_batch() 多账号编排          │
└─────────────────────────────────────────────────────────────┘
          │                              │
          ▼                              ▼
   io.net / SpherePay HTTP         Solana RPC (curl_cffi)
   (curl_cffi impersonate=chrome)  (sendTransaction / getTransaction)
```

### 📦 安装

```bash
# 需要 Python 3.10+
git clone <your-fork-or-this-repo-url> io-net-api-recharge
cd io-net-api-recharge

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

依赖说明：

| 包 | 用途 |
|---|---|
| `curl_cffi>=0.7.0` | 提供 `impersonate="chrome"` TLS 指纹伪装，绕过 Cloudflare `key_set` 拦截 |
| `solders>=0.23.0` | 底层 Solana 类型 `Keypair` / `VersionedTransaction`，用于离线签名 |
| `base58>=2.1.1` | 私钥 base58 编解码（测试与工具辅助） |

### 🚀 快速开始

#### 1. 获取凭据

- **refresh cookie**：在已登录 io.net 的浏览器中，从 `.io.net` 域 cookie 取出
  `workos_refresh_token` 的值（长时效，可复用）。
- **钱包**：准备一个 Solana 钱包（公钥 + base58 私钥），并确保其中有足够 USDC 与少量 SOL。

> 注意：io.net 账号**无需绑定该钱包**即可充值（见 [API.md](./API.md) 原理说明）。

#### 2. 单账号（先 dry-run，再广播）

```bash
# ① 只建链 + 签名，不广播（不花钱）
python cli.py single \
  --refresh-cookie "<workos_refresh_token 值>" \
  --pubkey "<付款钱包公钥>" \
  --secret "<付款钱包私钥 base58>"

# ② 确认无误后，真正广播（花钱）
python cli.py single \
  --refresh-cookie "<workos_refresh_token 值>" \
  --pubkey "<付款钱包公钥>" \
  --secret "<付款钱包私钥 base58>" \
  --broadcast
```

#### 3. 编程调用

```python
from io_net_recharge.client import IoNetRechargeClient
from io_net_recharge.wallet import sign_transaction, broadcast_transaction, verify_on_chain

client = IoNetRechargeClient(refresh_cookie="<workos_refresh_token>")
payload = client.build_transaction(pubkey="<付款钱包公钥>")   # refresh→建链→取交易

signed = sign_transaction(payload.tx_b64, "<私钥 base58>")
sig = broadcast_transaction(signed, dry_run=True)              # dry_run 不花钱
print("签名:", sig)

# 真正广播：
# sig = broadcast_transaction(signed)
# v = verify_on_chain(sig, "<付款钱包公钥>", expected_usdc=10.0)
# print("链上验证:", v)
```

### 🔁 批量

`examples/config.example.json` 复制为 `config.json` 并填入真实凭据（**勿提交**）：

```json
{
  "rpc": "https://solana-rpc.publicnode.com",
  "accounts": [
    {
      "name": "acc-1",
      "refresh_cookie": "<账号1 的 workos_refresh_token>",
      "secret_base58": "<账号1 付款钱包私钥>",
      "pubkey": "<账号1 付款钱包公钥>",
      "amount": "10"
    }
  ]
}
```

```bash
# 先 dry-run（不广播，验证 build+sign 链路）
python cli.py batch --config config.json --dry-run

# 真实批量广播
python cli.py batch --config config.json
```

> ⚠️ 批量并发/连续请求可能触发 Cloudflare 行为风控升级，建议先用少量账号（2–3 个）
> 做冒烟测试，再逐步放大。详见 [API.md](./API.md) 风控章节。

### 🛡️ 批量风控参数（batch）

`cli.py batch` 与 `run_batch` 支持以下风控参数，也可写入 `config.json` 顶层。
**默认值均为"不干预"**，即行为与无风控时一致，可放心渐进调参。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `min_gap` / `max_gap` | float(秒) | `0` / `0` | 账号之间随机抖动间隔 `uniform(min_gap, max_gap)`，避免固定节奏被行为风控识别为脚本 |
| `step_size` | int | `0` | 每处理 N 个账号插入一次阶梯冷却；`0` = 不启用 |
| `step_cooldown` | float(秒) | `0` | 阶梯冷却时长；模拟"分批次操作"而非一次性灌完 |
| `max_retries` | int | `1` | 瞬态失败（限流 `key_set` / 广播网络抖动）最大重试次数（不含首次） |
| `backoff_base` | float | `2.0` | 指数退避底数，第 k 次重试前等待 `backoff_base**k` 秒 |

**重试范围（重要）**

- ✅ **会重试**：`KeySetBlockedError`（Cloudflare 限流）、`BroadcastError`（RPC 网络抖动）。每次重试重建 client 并重新 refresh token，利于限流后恢复。
- ❌ **不重试**：`SignError` / `TopupError` / `PaymentLinkError` 等业务错误（重试无意义，直接判失败）。
- ❌ **verify 不重试（与用户确认的设计）**：广播一旦返回交易签名即视为已上链尝试，若链上确认查询失败或对账不符，重放广播可能造成**双花**。因此 `verify_on_chain` 的失败一律判该账号失败、不重试，由人工核查链上交易状态。

**已知剩余风险（代码未覆盖，需使用者自行评估）**

- 🔴 **IP 层未做代理轮换**：`curl_cffi` 已预留 `proxies=` 能力但当前未接入，100 账号共用单一出口 IP 仍是 Cloudflare 行为风控的高风险点。如需多 IP，请自行在 `client.py` 的 session 上配置 `proxies`。
- 🔴 **批量 reseller 合规边界**：io.net / SpherePay 对批量注册、reseller 模式的 ToS 态度需使用者自行核查（见顶部⚠️合规风险）。
- 🟡 **账号登录态来源**：100 个 `workos_refresh_token` 的获取与保鲜不在本工具范畴（涉及人工登录 / 验证码 / DPAPI 等），需另建凭据供给管线。

### ⚙️ 配置项

| 参数 | 说明 | 默认 |
|---|---|---|
| `refresh_cookie` | `workos_refresh_token` 值 | 必填 |
| `pubkey` / `secret_base58` | 付款钱包公钥 / 私钥（base58） | 必填 |
| `amount` | 充值金额（USDC，字符串） | `"10"` |
| `rpc` | Solana RPC 端点 | `https://solana-rpc.publicnode.com` |
| `frontend_version` | io.net 前端版本号 | `1.141.1` |
| `verify_tls` | 是否校验 TLS（curl_cffi） | `False` |

### 📚 API 概览

| 模块 | 关键方法 | 作用 |
|---|---|---|
| `client.IoNetRechargeClient` | `refresh_access_token()` | 换取 JWT |
| | `create_topup(token)` | 创建充值订单，返回 payment_link |
| | `get_payment_transaction(token, link_id, pubkey)` | 取待签交易 |
| | `build_transaction(pubkey)` | 一键走完 1→2→3 |
| `wallet` | `sign_transaction(tx_b64, secret)` | 离线签名 |
| | `broadcast_transaction(signed, dry_run=)` | 广播 |
| | `verify_on_chain(sig, pub, expected_usdc=)` | 链上核对 |
| `batch` | `run_batch(accounts, dry_run=, verify=)` | 多账号编排 |

完整端点、请求/响应字段、错误码见 [API.md](./API.md)。

### 📁 项目结构

```
io-net-api-recharge/
├── cli.py                      # 命令行入口（single/batch/verify）
├── requirements.txt
├── README.md                   # 本文档
├── API.md                     # 端点与错误详解
├── LICENSE
├── .gitignore
├── examples/
│   └── config.example.json     # 批量配置模板
├── io_net_recharge/
│   ├── __init__.py
│   ├── client.py               # HTTP 客户端（3 个端点封装）
│   ├── wallet.py               # 签名 / 广播 / 链上验证
│   ├── batch.py                # 批量编排
│   ├── models.py               # TransactionPayload 数据结构
│   └── errors.py               # 异常层级
└── tests/
    ├── test_client.py          # 签名单元测试（不广播）
    └── sample_tx.json          # 真实待签交易样本（仅用于本地签名测试）
```

### 🧪 测试

```bash
python tests/test_client.py
```

测试仅做**本地离线签名**（不联网、不广播、不使用真实资产私钥），验证：
1. `sign_transaction` 能反序列化待签交易并产出可重新解析的签名 base64；
2. 签名后交易至少含 1 个签名；
3. 非法输入抛出 `SignError`。

### 📜 免责声明

本仓库为技术可行性验证项目，作者不对使用后果负责：
- 不保证 io.net / SpherePay 接口长期不变；接口路径/参数可能随时调整。
- 使用本工具产生的任何资金损失、账号封禁、合规风险由使用者自行承担。
- 请勿将本工具用于违反服务条款或当地法律的用途。

### 📄 许可证

[MIT](./LICENSE)

---

## English

### ⚠️ Risk Warning (read first)

1. **Funds**: This tool performs real on-chain transactions. Any call with `--broadcast`
   or `dry_run=False` spends real money (default 10 USDC + small SOL gas). Always
   validate with `--dry-run` first.
2. **Secrets**: Config files contain wallet private keys (base58). **Never commit
   private keys to a public repo.** Use env vars or untracked local files, and add
   `config.json` to `.gitignore`.
3. **Compliance**: io.net / SpherePay ToS may restrict automation, bulk registration,
   or reseller models. This repo is a technical feasibility proof only — users must
   assess and bear compliance responsibility themselves.
4. **TLS fingerprint**: All outbound requests MUST use `curl_cffi` with
   `impersonate="chrome"` to mimic Chrome's TLS fingerprint, otherwise Cloudflare
   Bot Management soft-blocks with `{"key_set": true}` (see [API.md](./API.md)).

### ✨ Features

- **Pure API, zero browser interaction**: login state reused via `workos_refresh_token`
  cookie; no Selenium/CDP required.
- **Recharge without binding a wallet**: on-chain only verifies the private-key
  signature; recharge attribution is decided by the payment_link↔account binding.
- **Offline signing**: sign v0 transactions locally with `solders`; keys never leave
  the machine.
- **Batch orchestration**: `run_batch` / `cli.py batch` loops build→sign→broadcast
  across accounts.
- **On-chain verification**: automatically confirms exact USDC deduction by payer.
- **Money-free dry-run**: every entry point supports `dry_run`.

### 🚀 Quick Start

```bash
pip install -r requirements.txt

# 1) dry-run (no broadcast, no spend)
python cli.py single --refresh-cookie "<token>" --pubkey "<pub>" --secret "<priv>"

# 2) real broadcast (spends)
python cli.py single --refresh-cookie "<token>" --pubkey "<pub>" --secret "<priv>" --broadcast
```

See the Chinese section above for full details, batch usage, config schema, and the
project layout. Endpoint/error reference is in [API.md](./API.md).

### 📄 License

[MIT](./LICENSE)
