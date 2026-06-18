"""诉讼时效知识表 —— 基于《民法典》及特别法规定。

覆盖常见案由的诉讼时效期间，供 statute_of_limitations 工具查询。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LimitationRule:
    """诉讼时效规则。"""
    case_type: str
    period_years: float
    legal_basis: str
    article: str
    notes: str


LIMITATION_RULES: dict = {
    "劳动争议": LimitationRule(
        case_type="劳动争议",
        period_years=1,
        legal_basis="《劳动争议调解仲裁法》",
        article="第二十七条",
        notes="从当事人知道或应当知道其权利被侵害之日起计算。劳动关系存续期间因拖欠劳动报酬发生争议的，不受一年限制，但应自劳动关系终止之日起一年内提出。",
    ),
    "合同纠纷": LimitationRule(
        case_type="合同纠纷",
        period_years=3,
        legal_basis="《民法典》",
        article="第一百八十八条",
        notes="自权利人知道或者应当知道权利受到损害以及义务人之日起计算。",
    ),
    "人身损害": LimitationRule(
        case_type="人身损害",
        period_years=3,
        legal_basis="《民法典》",
        article="第一百八十八条",
        notes="伤害明显的，从受伤害之日起算；伤害当时未发现的，从发现之日起算。",
    ),
    "产品责任": LimitationRule(
        case_type="产品责任",
        period_years=3,
        legal_basis="《民法典》",
        article="第一百八十八条",
        notes="最长保护期限为产品交付之日起 10 年（第一千二百零三条）。",
    ),
    "租赁纠纷": LimitationRule(
        case_type="租赁纠纷",
        period_years=3,
        legal_basis="《民法典》",
        article="第一百八十八条",
        notes="延付或拒付租金的，自知道或应当知道之日起算。",
    ),
    "民间借贷": LimitationRule(
        case_type="民间借贷",
        period_years=3,
        legal_basis="《民法典》",
        article="第一百八十八条",
        notes="有约定还款日的，从还款日届满之日起算；未约定的，从债权人主张权利之日起算。",
    ),
    "知识产权侵权": LimitationRule(
        case_type="知识产权侵权",
        period_years=3,
        legal_basis="《民法典》",
        article="第一百八十八条",
        notes="持续侵权的，损害赔偿数额自权利人向法院起诉之日起向前推算三年计算。",
    ),
    "国际货物买卖": LimitationRule(
        case_type="国际货物买卖",
        period_years=4,
        legal_basis="《联合国国际货物销售时效期限公约》",
        article="第8条",
        notes="中国加入该公约，适用于国际货物买卖合同。",
    ),
    "海事请求": LimitationRule(
        case_type="海事请求",
        period_years=1,
        legal_basis="《海商法》",
        article="第二百五十七条",
        notes="就海上货物运输向承运人要求赔偿的请求权，时效期间为一年。",
    ),
    "保险理赔": LimitationRule(
        case_type="保险理赔",
        period_years=2,
        legal_basis="《保险法》",
        article="第二十六条",
        notes="人寿保险为 5 年，其他保险为 2 年，自知道保险事故发生之日起算。",
    ),
    "行政诉讼": LimitationRule(
        case_type="行政诉讼",
        period_years=0.5,
        legal_basis="《行政诉讼法》",
        article="第四十六条",
        notes="公民、法人或者其他组织直接向法院提起诉讼的，应当自知道或者应当知道作出行政行为之日起六个月内提出。",
    ),
    "工伤认定": LimitationRule(
        case_type="工伤认定",
        period_years=1,
        legal_basis="《工伤保险条例》",
        article="第十七条",
        notes="用人单位应在事故发生之日起 30 日内申请；用人单位未申请的，工伤职工或其近亲属可在 1 年内直接申请。",
    ),
}

DEFAULT_RULE = LimitationRule(
    case_type="一般民事纠纷",
    period_years=3,
    legal_basis="《民法典》",
    article="第一百八十八条",
    notes="向人民法院请求保护民事权利的诉讼时效期间为三年。最长权利保护期间为 20 年（第一百八十八条第二款）。",
)

SUSPENSION_WARNING = (
    "注意：诉讼时效可能因以下情形中止（《民法典》第一百九十四条）：不可抗力、无民事行为能力人没有法定代理人等。"
    "也可能因以下情形中断（第一百九十五条）：权利人向义务人提出履行请求、义务人同意履行、权利人提起诉讼或申请仲裁等。"
    "中断后时效期间重新计算。"
)
