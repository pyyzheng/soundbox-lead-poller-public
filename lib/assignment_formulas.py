"""询盘主表分配相关公式（lark-cli [字段名] 语法）。

刚录入时 lookup / 工作流 / 兜底脚本尚未回填业务员，系统匹配必须留空，
分配状态才能显示「🔄 正在匹配规则」，而不是立刻变成「未命中规则 → 分配异常」。
"""

from __future__ import annotations

FIELD_DUP = "[Duplicate（重复）]"
FIELD_DUP_OWNER = "[Dup_Match_Owner]"
FIELD_SUBOFFICE = "[是否是子办国家]"
FIELD_SUBOFFICE_OWNER = "[子办规则命中负责人]"
FIELD_AGENT_ASSIGNEE = "[代理规则命中业务员]"
FIELD_QUEUE_ASSIGNEE = "[渠道顺序队列匹配业务员]"
FIELD_AGENT_COUNTRY = "[是否命中代理国家]"
FIELD_AGENT_PRODUCT = "[是否命中代理产品]"
FIELD_ROTATION = "[是否满足渠道轮转]"
FIELD_QUEUE_KEY = "[队列Key]"
FIELD_FINAL = "[Owner（最终负责人）]"
FIELD_SYSTEM = "[Matched Sales Rep（系统匹配业务员）]"
FIELD_ACCOUNT = "[Assigned Salesperson（匹配的业务员账号）]"

# lookup / 子办 / 代理 / 渠道轮转尚未完成时，不要输出「未命中规则」。
_WAITING = (
    f"OR(ISBLANK({FIELD_SUBOFFICE}),"
    f"{FIELD_SUBOFFICE}=\"是\","
    f"ISBLANK({FIELD_AGENT_COUNTRY}),"
    f"AND({FIELD_AGENT_COUNTRY}=\"是\","
    f"OR(ISBLANK({FIELD_AGENT_PRODUCT}),{FIELD_AGENT_PRODUCT}=\"待确认\")),"
    f"{FIELD_ROTATION}=\"是\","
    f"ISBLANK({FIELD_QUEUE_KEY}))"
)


def build_system_assignee_expression() -> str:
    return (
        f"IFERROR("
        f"IF({FIELD_DUP}=\"查重中\",\"\","
        f"IF({FIELD_DUP}=\"查重冲突\",\"匹配错误请检查\","
        f"IF(AND({FIELD_DUP}=\"查重命中\",NOT(ISBLANK({FIELD_DUP_OWNER}))),{FIELD_DUP_OWNER},"
        f"IF(NOT(ISBLANK({FIELD_SUBOFFICE_OWNER})),{FIELD_SUBOFFICE_OWNER},"
        f"IF(NOT(ISBLANK({FIELD_AGENT_ASSIGNEE})),{FIELD_AGENT_ASSIGNEE},"
        f"IF(NOT(ISBLANK({FIELD_QUEUE_ASSIGNEE})),{FIELD_QUEUE_ASSIGNEE},"
        f"IF({_WAITING},\"\",\"未命中规则\"))))))),"
        f"\"公式计算异常\")"
    )


def build_assignment_status_expression() -> str:
    error = (
        f"OR({FIELD_FINAL}=\"匹配错误请检查\",{FIELD_FINAL}=\"公式计算异常\","
        f"{FIELD_SYSTEM}=\"匹配错误请检查\",{FIELD_SYSTEM}=\"公式计算异常\","
        f"{FIELD_FINAL}=\"未命中规则\",{FIELD_SYSTEM}=\"未命中规则\")"
    )
    return (
        f"IF({error},\"❌ 分配异常\","
        f"IF(ISBLANK({FIELD_FINAL}),\"🔄 正在匹配规则\","
        f"IF(ISBLANK({FIELD_ACCOUNT}),\"⏳ 分配中/阻塞\",\"✅ 已分配\")))"
    )
