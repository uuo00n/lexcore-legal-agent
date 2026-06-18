# Document Types

Use this file to choose a Chinese litigation document structure. Keep headings conventional and court-facing.

## Civil Complaint

Use for 民事起诉状 / 起诉状 / 诉状 where the user is starting a civil lawsuit.

Structure:

```text
民事起诉状

原告：[姓名/名称，身份信息，住所地，联系方式]
被告：[姓名/名称，身份信息，住所地，联系方式]

诉讼请求：
1. 请求判令被告……
2. 请求判令被告承担本案诉讼费用。

事实与理由：
[按时间顺序写事实、法律关系、违约或侵权事实、损失或后果、协商经过。]

证据目录：
1. [证据名称]，证明目的：[对应事实]。

此致
[有管辖权的人民法院]

具状人：[姓名/名称]
[日期]
```

Drafting notes:

- Claims must be executable: payment amount, delivery act, return act, confirmation act, cessation act, compensation amount.
- If money is claimed, state principal, interest/liquidated damages basis, calculation period, and "暂计至" date when needed.
- Facts should not argue every legal theory. Plead the facts that support each claim.

## Civil Answer

Use for 民事答辩状 / 答辩意见 where the user responds to a lawsuit.

Structure:

```text
民事答辩状

答辩人：[身份信息]
被答辩人：[身份信息]
案由：[案由待补充]

答辩请求：
1. 请求驳回原告的全部/部分诉讼请求。
2. 本案诉讼费用由原告承担。

事实与理由：
[逐项回应对方请求和事实，说明承认、否认、需其举证的部分。]

证据目录：
[如有]

此致
[受理法院]

答辩人：[姓名/名称]
[日期]
```

Drafting notes:

- Separate "不认可事实" from "即使事实成立，法律后果也不成立".
- Avoid insulting the other party. Use "与事实不符", "缺乏证据证明", "计算依据不足".

## Appeal

Use for 上诉状 where a party challenges a first-instance judgment or ruling.

Structure:

```text
民事上诉状

上诉人：[身份信息]
被上诉人：[身份信息]
原审案号：[待补充]

上诉请求：
1. 请求撤销/变更[法院][案号]民事判决第[X]项；
2. 请求依法改判……/发回重审；
3. 一、二审诉讼费用由被上诉人承担。

事实与理由：
[围绕事实认定、证据采信、法律适用、程序问题逐项写。]

此致
[二审法院]

上诉人：[姓名/名称]
[日期]
```

Drafting notes:

- Do not re-tell the whole case unless needed. Focus on first-instance error.
- Mark missing judgment date and appeal deadline if the user has not provided them.

## Enforcement Application

Use for 强制执行申请书 / 执行申请书 after an effective judgment, mediation statement, arbitral award, or notarized creditor document.

Structure:

```text
强制执行申请书

申请执行人：[身份信息]
被执行人：[身份信息]
执行依据：[生效法律文书名称、案号、生效时间]

申请事项：
1. 请求强制执行被执行人应支付的款项/应履行的义务；
2. 请求被执行人承担迟延履行期间的债务利息/迟延履行金；
3. 请求被执行人承担执行费用。

事实与理由：
[说明执行依据已经生效、义务内容、对方未履行情况。]

此致
[有管辖权的人民法院]

申请执行人：[姓名/名称]
[日期]
```

## Preservation Application

Use for 财产保全申请书 / 行为保全申请书 / 证据保全申请书 before or during litigation.

Structure:

```text
财产保全申请书

申请人：[身份信息]
被申请人：[身份信息]

申请事项：
请求查封、扣押、冻结被申请人名下价值人民币[金额]元的财产。

事实与理由：
[说明基础法律关系、请求金额、保全必要性、紧急性或执行风险。]

担保情况：
[担保方式待补充]

此致
[有管辖权的人民法院]

申请人：[姓名/名称]
[日期]
```

## Evidence List

Use for 证据目录 or as an appendix to any pleading.

Format:

```text
证据目录

证据一：[名称]
来源/形式：[原件、复印件、电子数据、截图、录音等]
证明目的：[证明哪一项事实或金额]
页码/位置：[如有]
```

Evidence list rules:

- Avoid "证明我方主张成立" as a proof purpose. State the concrete fact.
- For chat records, mention account identity, time range, complete context, and original carrier.
- For transfers, mention payer, payee, date, amount, channel, and transaction note.
