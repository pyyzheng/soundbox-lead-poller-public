# 过滤链中度重构方案 — Claude Code 执行指南

## 背景与目标

当前过滤链经过多次迭代，已有 9 项检测 + LLM 意图分类，但存在职责重叠、权重不合理、冗余逻辑等结构性问题。本次重构目标：**理清各层职责边界，合并冗余检测，补齐权重缺陷，精简 LLM prompt**。不改变主流程（Gmail → 过滤 → LLM → 标准化 → 飞书），只调整过滤链的内部编排。

## 项目位置

- 本地：`/Users/xiaolong/projects/soundbox-lead-poller/`
- 云端：`pyyzheng/soundbox-lead-poller`（GitHub main 分支）

## 重构后的三层架构

```
T1 确定性拦截（命中即 reject，零误杀风险）
├── check_skip_sender          — 黑名单发件人
├── check_skip_subject         — 黑名单主题
├── check_system_notification  — 系统自动通知（已有）
└── check_marketing_email      — 仅 List-Unsubscribe header（从信号层提升）

T2 内容信号累加（≥2 信号 → reject）
├── check_spam                 — bot 检测（随机字符）
├── check_placeholder          — 占位符/测试提交
├── check_promotional_content  — 推销内容检测（合并 spam_content + cold_outreach）
├── check_irrelevant_business  — 无关行业
└── check_trivial_content      — 测试/垃圾提交

T3 LLM 语义层（字段提取 + intent 分类）
├── intent 分类                — inquiry / non_inquiry
└── 字段提取 + 标准化          — name/email/company/phone/message/country/product
```

**删除项：**
- `check_inquiry_keywords` — 被 T3 intent 分类完全替代
- LLM prompt 中的"系统通知→跳过"段落 — 被 T1 check_system_notification 覆盖

---

## 改动 1：提升 marketing_email (List-Unsubscribe header) 为硬拦截

### 原因

List-Unsubscribe 是 RFC 2369 标准头，由邮件营销平台（Mailchimp、SendGrid 等）自动添加。正常客户手写的询盘邮件不可能带这个头。目前它只算 +1 信号，和"无询盘关键词"这种模糊信号同等权重，需要凑第二个信号才能 reject，不合理。

### 涉及文件：`lib/lead_filter_common.py`

#### 1a. 拆分 `check_marketing_email` 为两个函数

当前 `check_marketing_email` 同时检测 List-Unsubscribe header 和正文中的 unsubscribe footer。拆为：

- `check_marketing_header(has_unsubscribe_header: bool)` — **硬拦截**，放入 T1
- 正文 unsubscribe footer 检测保留在原函数中，继续作为信号

```python
def check_marketing_header(has_unsubscribe_header: bool) -> Tuple[bool, str]:
    """硬拦截：List-Unsubscribe header 存在 = 确定性营销邮件"""
    if has_unsubscribe_header:
        return True, "marketing_header(List-Unsubscribe)"
    return False, ""


def check_marketing_footer(raw_body: str) -> Tuple[bool, str]:
    """信号：正文包含 unsubscribe/manage preferences 等退订文本"""
    if raw_body and UNSUBSCRIBE_FOOTER.search(raw_body):
        return True, "marketing_footer(unsubscribe_in_body)"
    return False, ""
```

原来的 `check_marketing_email` 函数删除，用上面两个替代。

### 涉及文件：`cloud-lead-poller.py`

#### 1b. 更新 import

```python
from lead_filter_common import (
    ...
    check_marketing_header,   # 替换 check_marketing_email
    check_marketing_footer,   # 替换 check_marketing_email
    ...
)
```

#### 1c. 更新 `run_filter_chain`

在 T1 硬拦截区域（`check_system_notification` 之后）增加：

```python
# 硬拦截：List-Unsubscribe header = 确定性营销邮件
mkt_header, reason = check_marketing_header(has_unsubscribe_header)
if mkt_header:
    return "reject", [reason]
```

在 T2 信号累加区域，将原来的 `check_marketing_email` 调用替换为：

```python
mkt_footer, reason = check_marketing_footer(body)
if mkt_footer:
    signals.append(reason)
```

---

## 改动 2：合并 spam_content + cold_outreach 为 check_promotional_content

### 原因

两者都在检测"推销类邮件"，但用不同的机制：
- `spam_content` 用 `lead-rules.json` 中的 patterns 列表做关键词/正则匹配
- `cold_outreach` 用硬编码的 action+target 正则组合

后期给 `spam_content` 加的 patterns（如 `"brand appearing first"`, `"rank.*first on.*google"`）和 `cold_outreach` 的 action+target 逻辑大量重叠。

### 涉及文件：`lib/lead_filter_common.py`

#### 2a. 新建 `check_promotional_content` 函数

合并两种检测逻辑为一个函数，内部分两步：

```python
# 保留原有的 cold outreach 正则（硬编码）
PROMO_ACTION = re.compile(
    r'\b(review|audit|optimi[sz]e|improve|suggest|analysis|launch|show you|rank|boost|grow|scale|increase|drive)\b',
    re.IGNORECASE,
)
PROMO_TARGET = re.compile(
    r'\b(website|web site|site|webpage|landing page|pages|search|brand|visibility|online|google|traffic|leads|revenue|sales|ROI)\b',
    re.IGNORECASE,
)


def check_promotional_content(name: str, subject: str, message: str, company: str,
                               rules: Dict[str, Any], raw_body: str = "") -> Tuple[bool, str]:
    """检测推销类内容（合并原 spam_content + cold_outreach）

    两种检测机制，命中任一即返回信号：
    1. rules 中的 spam_content_patterns（关键词/正则列表）
    2. action + target 正则组合（cold outreach 模式）
    """
    parts = [name or '', subject or '', message or '', company or '']
    if not (message or '').strip() and raw_body:
        parts.append(raw_body)
    full_text = " ".join(parts)
    full_lower = full_text.lower()

    if not full_lower.strip():
        return False, ""

    # ── 机制 1: spam_content_patterns（配置驱动）──
    config = rules.get("spam_content_patterns", {})
    if config.get("enabled", True):
        min_matches = config.get("min_matches", 1)
        matches = []
        for pattern in config.get("patterns", []):
            if not pattern or pattern.startswith("_"):
                continue
            pat_lower = pattern.lower()
            try:
                if re.search(pat_lower, full_lower):
                    matches.append(pattern)
            except re.error:
                if pat_lower in full_lower:
                    matches.append(pattern)
        if len(matches) >= min_matches:
            return True, f"promotional(pattern:{'+'.join(matches[:3])})"

    # ── 机制 2: action + target 组合（硬编码）──
    if PROMO_ACTION.search(full_text) and PROMO_TARGET.search(full_text):
        return True, "promotional(action+target)"

    return False, ""
```

#### 2b. 删除旧函数

- 删除 `check_spam_content` 函数
- 删除 `check_cold_outreach` 函数
- 删除 `COLD_OUTREACH_ACTION` 和 `COLD_OUTREACH_TARGET` 常量

### 涉及文件：`cloud-lead-poller.py`

#### 2c. 更新 import

```python
from lead_filter_common import (
    ...
    check_promotional_content,  # 替换 check_spam_content + check_cold_outreach
    ...
)
```

删除 `check_spam_content`, `check_cold_outreach` 的 import。

#### 2d. 更新 `run_filter_chain` 信号累加区域

删除原来 `check_spam_content` 和 `check_cold_outreach` 的两段调用，替换为：

```python
promo, reason = check_promotional_content(name, subject, message, company, rules, raw_body=body)
if promo:
    signals.append(reason)
```

---

## 改动 3：删除 check_inquiry_keywords

### 原因

190+ 关键词列表过长，几乎所有邮件都能命中，区分度接近于零。真正的"无询盘关键词"场景（纯推销邮件）已被 T2 的 `check_promotional_content` 和 T3 的 intent 分类覆盖。保留它只增加维护负担和认知复杂度。

### 涉及文件：`lib/lead_filter_common.py`

- 删除 `check_inquiry_keywords` 函数

### 涉及文件：`cloud-lead-poller.py`

- 从 import 中删除 `check_inquiry_keywords`
- 从 `run_filter_chain` 中删除对应的调用段：

```python
# 删除以下代码块
has_kw, reason = check_inquiry_keywords(name, message, company, rules)
if not has_kw:
    signals.append(reason)
```

### 涉及文件：`lead-rules.json`

- `inquiry_keywords` 列表和 `require_inquiry_keyword` 字段**暂时保留不删**，因为其他模块可能引用（如 `product_categories` 仍被 LLM 标准化逻辑使用）。只是过滤链不再调用它。如果确认无其他引用，后续可清理。

---

## 改动 4：精简 LLM prompt

### 原因

LLM prompt 中的"系统通知邮件→跳过"段落（约 615-622 行）和 T1 的 `check_system_notification` 做同一件事。系统通知在到达 LLM 之前已经被 T1 硬拦截了，这段 prompt 是死代码，浪费 token 且增加 LLM 认知负担。

### 涉及文件：`cloud-lead-poller.py`

#### 4a. 删除 LLM_SYSTEM_PROMPT 中的系统通知段落

删除以下整段（约第 615-622 行）：

```
## 系统通知邮件 → 必须跳过
以下类型不是询盘，必须返回 {"status": "skipped", "reason": "system_notification"}：
- Google 付款/账单/账号通知（payments-noreply、no-reply@accounts）
- 域名注册/续费通知（namesilo、godaddy、namecheap 等）
- 网站系统通知（WordPress、security alerts、SSL 证书）
- 社交媒体通知（Instagram、Facebook、LinkedIn 系统邮件）
- Newsletter / 营销推广（Unsubscribe 链接、退订）
判断标准：发件人含 noreply/no-reply/do-not-reply，或正文是系统自动生成的通知（无真人询价意图）
```

#### 4b. 在 prompt 开头的 `## 规则` 中增加一行说明

在现有规则第 1 条之前加：

```
0. 到达你这一步的邮件已通过规则引擎预筛（已排除系统通知、营销群发等），你只需专注于意图分类和字段提取
```

这让 LLM 知道它不需要操心这类邮件，降低"过度谨慎导致误判"的概率。

#### 4c. 删除 LLM 返回的 status=skipped 处理逻辑（可选）

`process_email` 中约 1058-1062 行有：

```python
elif llm_result and llm_result.get("status") == "skipped":
    log.info("LLM 判定跳过: %s", llm_result.get("reason"))
    apply_label(service, msg_id, label_id)
    return {"id": msg_id, "status": "skipped", "reason": llm_result.get("reason")}
```

由于系统通知已在 T1 被拦截，LLM 返回 `status=skipped` 的场景理论上不再发生。**建议保留这段代码作为防御性编程**——万一有极端情况 LLM 仍然判定跳过，这段兜底逻辑不会造成错误。但可以加一行 warning 日志，方便后续观察是否还有触发：

```python
elif llm_result and llm_result.get("status") == "skipped":
    log.warning("LLM 判定跳过（理论上不应触发，T1 应已拦截）: %s", llm_result.get("reason"))
    ...
```

---

## 改动 5：更新 run_filter_chain 函数注释和结构

重构后 `run_filter_chain` 的完整结构应为：

```python
def run_filter_chain(from_addr: str, subject: str, name: str, email: str,
                     message: str, phone: str, company: str, body: str, rules: dict,
                     has_unsubscribe_header: bool = False) -> tuple[str, list[str]]:
    """三层过滤链，返回 (action, signals)

    T1 确定性拦截：命中即 reject（skip_sender / skip_subject / system_notification / marketing_header）
    T2 内容信号累加：≥2 信号 → reject
    T3 LLM 语义层（不在此函数内，由 process_email 的 intent gate 处理）
    """
    # ── T1: 确定性拦截（命中即 reject）──
    skip, reason = check_skip_sender(from_addr, rules)
    if skip:
        return "reject", [reason]

    skip, reason = check_skip_subject(subject, rules)
    if skip:
        return "reject", [reason]

    sys_notif, reason = check_system_notification(from_addr, body)
    if sys_notif:
        return "reject", [reason]

    mkt_header, reason = check_marketing_header(has_unsubscribe_header)
    if mkt_header:
        return "reject", [reason]

    # ── T2: 内容信号累加（≥2 → reject）──
    signals = []

    spam, reason = check_spam(name, email or from_addr, message, rules)
    if spam:
        signals.append(reason)

    placeholder, reason = check_placeholder(name, email or from_addr, phone, company)
    if placeholder:
        signals.append(reason)

    promo, reason = check_promotional_content(name, subject, message, company, rules, raw_body=body)
    if promo:
        signals.append(reason)

    irr, reason = check_irrelevant_business(name, company, message, rules, raw_body=body)
    if irr:
        signals.append(reason)

    mkt_footer, reason = check_marketing_footer(body)
    if mkt_footer:
        signals.append(reason)

    trivial, reason = check_trivial_content(name, message)
    if trivial:
        signals.append(reason)

    if len(signals) >= 2:
        return "reject", signals
    return "pass", signals
```

---

## 测试验证

### 回归用例（必须通过）

1. **Marcus Palmer（SEO 推销）** → T2 `promotional(pattern:brand appearing first)` 或 `promotional(action+target)` 命中，加上其他信号达到 ≥2 → reject
2. **TikTok Shop 系统通知** → T1 `system_notification(auto+no_reply_in_body)` 硬拦截 → reject
3. **带 List-Unsubscribe header 的营销邮件** → T1 `marketing_header(List-Unsubscribe)` 硬拦截 → reject
4. **正常询盘（"I need 5 phone booths"）** → T1 全部通过，T2 信号 = 0 → pass → LLM intent = inquiry → 写入飞书
5. **模糊询盘（"I'm interested in your products"）** → T1/T2 通过 → LLM intent = inquiry → 写入飞书

### 验证命令

```bash
DRY_RUN=true python cloud-lead-poller.py
```

### 检查清单

- [ ] `check_marketing_email` 已被删除，替换为 `check_marketing_header` + `check_marketing_footer`
- [ ] `check_spam_content` 和 `check_cold_outreach` 已被删除，替换为 `check_promotional_content`
- [ ] `check_inquiry_keywords` 已从过滤链移除
- [ ] `COLD_OUTREACH_ACTION` / `COLD_OUTREACH_TARGET` 常量已删除
- [ ] LLM prompt 中"系统通知→跳过"段落已删除
- [ ] `run_filter_chain` 结构清晰，T1 硬拦截在前，T2 信号累加在后
- [ ] import 语句已更新，无引用已删除函数
- [ ] `lead-rules.json` 未被破坏性修改（`inquiry_keywords` 保留，仅不再被过滤链调用）

## 部署

```bash
git add -A
git commit -m "refactor: restructure filter chain into T1/T2/T3 tiers

- Promote List-Unsubscribe header to T1 hard reject
- Merge spam_content + cold_outreach into check_promotional_content
- Remove check_inquiry_keywords (superseded by L3 intent)
- Simplify LLM prompt (remove system notification section)
- Clean up run_filter_chain structure and comments"
git push origin main
```
