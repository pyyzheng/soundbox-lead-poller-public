# 线索管道 QA 规则文档

> 本文档是线索处理管道的"验收标准"。所有代码修改、规则调整都应以此为对照校验。
> 最后更新: 2026-04-20（新增舱网邮件回归用例）

---

## 一、过滤链架构（6 层 → 三档判定）

### 过滤流程

```
邮件进入
  │
  ├─ L1: skip_sender ──→ 命中直接 reject
  ├─ L2: skip_subject ──→ 命中直接 reject
  │
  ├─ L3: check_spam（bot 信号检测）──→ 累积信号
  ├─ L4: check_placeholder（占位符/测试）──→ 累积信号
  ├─ L5: check_spam_content（语义垃圾）──→ 累积信号
  ├─ L6: check_irrelevant_business（无关行业）──→ 累积信号
  ├─ L7: check_inquiry_keywords（询盘关键词）──→ 缺少则累积信号
  │
  └─ 信号数判定:
      0 → pass（正常处理）
      1 → review（带 [待确认] 写入飞书）
      ≥2 → reject（跳过，打标签）
```

### 关键阈值

| 参数 | 值 | 位置 |
|------|-----|------|
| `min_bot_signals` | 2 | `lead-rules.json → spam_rules` |
| name 无空格触发 | >12 字符无空格 | `lead_filter_common.py` |
| message 无空格触发 | >15 字符无空格 | `lead_filter_common.py` |
| name 唯一字符比阈值 | >0.75（≥10 字母） | `lead_filter_common.py` |
| message 唯一字符比阈值 | >0.95（≥16 字母） | `lead_filter_common.py` |
| 交替大小写触发 | 连续 ≥4 次 | `lead_filter_common.py` |
| email dots 触发 | ≥3 个点 | `lead-rules.json` |

---

## 二、已知垃圾邮件特征库

### 2.1 Bot 生成（随机字符串）

**已捕获的模式**：

| 示例 | 类型 | 触发的检测项 |
|------|------|-------------|
| `EVOUODhlLtMlKJzJumhti` | name | 交替大小写(5次) |
| `GUYMaKqfADLmClzw` | name | 无空格(16)+唯一比(0.81>0.75) |
| `e.n.e.f.o.t.e.c.6.0@gmail.com` | email | 9 dots |
| `ok.oculoso.s31@gmail.com` | email | 无空格+含随机子串 |
| `iSOYNUuFvvqGKGUSahECXtj` | message | 无空格(23) |
| `DhLENREjeAgMLHMZOtZ` | message | 无空格(20) |

**识别规则**：
- 名字 >12 字符无空格 → 几乎必定是 bot（正常人名名+姓有空格）
- 消息 >15 字符无空格 → 几乎必定是 bot
- email local 部分 ≥3 个点 → 高度可疑
- 连续大小写交替 ≥4 次 → bot 特征

### 2.2 语义垃圾（SEO/营销）

**匹配关键词**（`spam_content_patterns`）：
- guest post, backlink, DA/DR 值
- "came across your website", "improve your visibility"
- 制造业/批发询盘（来自中国的无关行业）
- crypto, gambling, financial services

### 2.3 占位符/测试提交

**已知模式**：
- Email: `test@`, `john@company`, `example.com`, `noreply@`
- Phone: `1234567890`, `1111111111`, `0000000000`, `9876543210`
- Name+Company: `John Smith + Acme`, `John Doe`, `Test User`

### 2.4 无关行业

**已配置关键词**（`irrelevant_business`）：
- SEO, software development, logistics, clothing, food
- crypto, gambling, insurance, loan, mortgage

---

## 三、已知边界情况与已修复问题

### 3.1 过滤漏网（已修复）

| 问题 | 根因 | 修复 | 日期 |
|------|------|------|------|
| `GUYMaKqfADLmClzw` 漏过 | name 无空格检测被 `is_name` 跳过 | name 也检查无空格(>12) | 2026-04-20 |
| name 唯一字符比 0.812 漏过 | 阈值 0.85 过高 | 降到 0.75 | 2026-04-20 |
| 交替大小写 3 次 > 5 次阈值 | 连续 5 次要求太高 | 降到 4 次 | 2026-04-20 |
| 88→132 关键词差距 | workspace 版未同步到仓库 | 用 workspace 版覆盖 | 2026-04-18 |

### 3.2 去重问题（已修复）

| 问题 | 根因 | 修复 |
|------|------|------|
| 回头客二次询盘被拦 | 仅 email 匹配 | 增加 message 前 80 字符比对 |
| 本地+云端重复写入 | 过渡期两边都写飞书 | 本地切为 DRY-RUN 只读 |
| 飞书格式不匹配导致去重失败 | 本地/云端写入字段不同 | 过渡期需手动清理 |

### 3.3 仍在存在的已知问题

| 问题 | 影响 | 状态 |
|------|------|------|
| HTML 邮件被误判为垃圾 | HTML 标签被当作内容分析 | **待修复** — 需在过滤前 strip HTML |
| IP→国家映射偶发错误 | ipinfo.io 返回不准 | 需人工修正 |
| LLM 返回格式不稳定 | 偶尔包裹 ```json | 用正则提取兜底 |
| Google OAuth 测试模式 token 7 天过期 | 需确认项目已切到生产 | **待确认** |
| 同一邮件多条重复询盘 | 可能产生多条飞书记录 | 由去重逻辑拦截，需观察 |

---

## 四、数据流完整性校验规则

### 4.1 Gmail → 飞书 一致性

| 校验项 | 正常值 | 异常处理 |
|--------|--------|---------|
| Gmail 已处理邮件数 | ≥ 飞书同期新增记录数 | 差值 >0 → 可能有漏分配 |
| Gmail 标签 `processed-by-openclaw` | 所有已处理邮件都有 | 缺标签 → 可能处理中断 |
| 飞书记录的 message 字段 | 非空、非纯 HTML | 空字段 → 解析失败 |

### 4.2 云端 Pipeline 健康指标

| 指标 | 正常范围 | 异常阈值 |
|------|---------|---------|
| 成功写入/次 | 0-N | 连续 3 次错误 → 告警 |
| 跳过/去重 | ≥0 | 无上限 |
| 运行时间 | <3 分钟 | >8 分钟 → 可能超时 |
| Gmail API 调用 | ≤10 次/轮 | 超过 → 搜索范围可能过大 |

### 4.3 不应遗漏的场景

- [ ] 回头客新询盘（同 email，不同 message）→ 应正常写入
- [ ] 单信号 review 档 → 应带 `[待确认]` 前缀写入飞书
- [ ] 多邮件同一线程 → 每条独立处理，不应合并
- [ ] 纯中文询盘 → 不应因无英文关键词被拒
- [ ] 空名字/空消息 → 应由 review 档处理，不应 reject
- [ ] 舱网中文表单（姓名/邮箱/留言内容）→ 应正确提取字段，tag_line 含对应子渠道
- [ ] 新官网英文表单（Inquiry 字段名）→ 应正确提取 message，tag_line 含"新官网"
- [ ] 表单 Country/国家 字段 → 应优先用于国家识别（比 IP/电话区号更准）
- [ ] 同一用户重复提交（一封邮件多条相同询盘）→ 只录入一条，不重复写入

---

## 五、回归测试用例

每次修改过滤规则后，必须验证以下用例：

### 应拦截（reject）

```python
# 随机字符串 bot
{"name": "GUYMaKqfADLmClzw", "email": "ok.oculoso.s31@gmail.com",
 "message": "iSOYNUuFvvqGKGUSahECXtj", "phone": "2918585836", "company": ""}

# 高 dots email + 随机消息
{"name": "EVOUODhlLtMlKJzJumhti", "email": "e.n.e.f.o.t.e.c.6.0@gmail.com",
 "message": "DhLENREjeAgMLHMZOtZ", "phone": "8451863022", "company": ""}

# SEO 语义垃圾
{"name": "Sarah", "email": "sarah@seocompany.com",
 "message": "I'd like to write a guest post for your website. We can offer backlinks with DA 50+.",
 "phone": "", "company": "SEO Masters"}

# 占位符测试
{"name": "John Smith", "email": "john@company.com",
 "message": "test", "phone": "1234567890", "company": "Acme"}
```

### 应放行（pass 或 review）

```python
# 正常英文询盘
{"name": "John Smith", "email": "john@realcompany.com",
 "message": "I need a soundproof booth for my studio, please send quote",
 "phone": "5551234567", "company": "Real Studio LLC"}

# 正常中文询盘
{"name": "张伟", "email": "zhangwei@163.com",
 "message": "我想了解一下静音舱的价格和规格", "phone": "13800138000", "company": ""}

# 短消息但真实询盘
{"name": "Maria Garcia", "email": "maria@gmail.com",
 "message": "Looking for acoustic panels for office", "phone": "", "company": ""}

# 回头客二次询盘（不同产品）
{"name": "Ahmed Hassan", "email": "ahmed@techcorp.ae",
 "message": "We also need the VR series for our second location",
 "phone": "+971501234567", "company": "TechCorp UAE"}
```

### HTML 邮件（必须正确处理）

```python
# HTML 格式的正常询盘 — strip 后应 pass，不应因 HTML 标签误判为垃圾
{"name": "David Chen", "email": "david@av-install.com",
 "message": "<html><body><p>I'm looking for a <b>soundproof booth</b> for our recording studio. "
            "Please send pricing for the SB-100 model.</p></body></html>",
 "phone": "+14155551234", "company": "AV Install Co."}

# HTML 格式的垃圾询盘 — strip 后应 reject，不应被 HTML 标签掩盖垃圾特征
{"name": "GUYMaKqfADLmClzw", "email": "ok.oculoso.s31@gmail.com",
 "message": "<div><span>iSOYNUuFvvqGKGUSahECXtj</span></div>",
 "phone": "2918585836", "company": ""}

# 含 HTML 实体和链接的正常询盘
{"name": "Maria Lopez", "email": "maria@eventpro.es",
 "message": "<p>Hello, we are organizing a conference and need &nbsp; acoustic booths. "
            "Can you provide a quote? Visit our site for reference.</p>",
 "phone": "+34612345678", "company": "EventPro Madrid"}
```

### 应 review（待确认）

```python
# 无询盘关键词但看起来像真人
{"name": "Yuki Tanaka", "email": "yuki@music.co.jp",
 "message": "Can you help me?", "phone": "", "company": ""}

# 空消息
{"name": "Li Wei", "email": "liwei@outlook.com",
 "message": "", "phone": "13900139000", "company": ""}
```

### 舱网邮件（必须正确处理）

新官网（英文字段，`Inquiry` 字段名，subject 含"新官网"）：

```python
# 新官网询盘 — 应 pass，tag_line 含"新官网"，country 从 Country 字段识别
{"name": "Daberr", "email": "dabeerali0010@gmail.com",
 "message": "How much will it cost to me in Pakistan", "phone": "+923123290096",
 "company": "", "country": "Pakistan",
 "subject": "新官网询价通知（https://www.soundbox-sys.com/）",
 "from": "service@soundbox-sys.com"}
```

舱网中文表单（姓名/邮箱/电话/公司/国家/留言内容，subject 含子渠道关键词）：

```python
# 美国舱网 — 应 pass，tag_line 含"美国舱网"，country 从"国家"字段识别
{"name": "Tony", "email": "2460juan@gmail.com",
 "message": "Please call", "phone": "+16475682460",
 "company": "Private", "country": "Canada",
 "subject": "美国舱网询价通知（soundbox-pod.com/us）",
 "from": "service@soundbox-sys.com"}

# 总舱网 — 应 pass，tag_line 含"总舱网"
{"name": "Gleb", "email": "komarov@example.ru",
 "message": "I hope this message finds you well", "phone": "+79090828999",
 "company": "LLC", "country": "Russia",
 "subject": "总舱网询价通知（soundbox-pod.com）",
 "from": "service@soundbox-sys.com"}

# 加拿大舱网 — 应 pass，tag_line 含"加拿大舱网"
{"name": "Tony", "email": "2460juan@gmail.com",
 "message": "Please call", "phone": "+16475682460",
 "company": "Private", "country": "Canada",
 "subject": "加拿大舱网询价通知（soundbox-pod.com/ca）",
 "from": "service@soundbox-sys.com"}
```

---

## 六、自动化健康检查设计

### 检查频率与内容

| 检查项 | 频率 | 实现方式 |
|--------|------|---------|
| 垃圾邮件漏网扫描 | 每 6 小时 | 查飞书 review/recent 记录，用过滤链重验 |
| Gmail-飞书一致性 | 每天 1 次 | 比对两边记录数 |
| Pipeline 运行状态 | 每 4 小时 | 检查 GitHub Actions 最近 N 次运行结果 |
| OAuth token 有效性 | 每天 1 次 | 用 refresh token 尝试获取 access token |

### 告警格式（含修复指引）

```
⚠️ 线索管道健康检查报告

检查项: 垃圾邮件漏网扫描
异常: 发现 2 条记录符合垃圾特征但未被过滤
疑似原因: 随机字符串检测阈值过高（name 唯一比 0.85）
修复建议:
  1. 在 lead_filter_common.py has_random_chars() 中降低 unique_threshold
  2. 用本文档第五节回归用例验证修改
  3. 推送到 main 分支，等待下次定时运行生效
相关记录:
  - recvh83qrzIeUy | name=GUYMaKqfADLmClzw | email=ok.oculoso.s31@gmail.com
  - recXXXXXX | name=... | email=...
```
