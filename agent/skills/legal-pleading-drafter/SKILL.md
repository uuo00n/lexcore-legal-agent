---
name: legal-pleading-drafter
description: Draft and polish Chinese legal pleadings and litigation documents, including civil complaints, answers, appeals, enforcement applications, preservation applications, evidence lists, and related court filings. Use when the user asks to write a 起诉状, 诉状, 诉讼书, 答辩状, 上诉状, 申请书, 执行申请书, 保全申请书, 证据目录, or asks to remove AI-like wording / 去 AI 腔 from a legal pleading while preserving legal facts, claims, evidence, and procedural risk controls.
---

# Legal Pleading Drafter

## Purpose

Use this skill to turn user facts into a court-facing Chinese legal document. The skill must produce a usable draft structure, keep legal reasoning tied to facts and evidence, and 去 AI 腔 without changing facts, claims, amounts, dates, parties, evidence, legal conclusions, or procedural posture.

This skill supports drafting, revising, and polishing. It does not replace legal research, jurisdiction checks, limitation-period analysis, or lawyer review when the matter is high-risk.

## Reference Routing

Load only the reference files needed for the task:

- `references/document-types.md`: read when choosing or drafting a document type.
- `references/fact-checklist.md`: read when facts are incomplete, claims need calculation, or evidence must be organized.
- `references/pleading-style.md`: read when polishing, removing AI-like wording, or converting a rough draft into court style.
- `references/quality-review.md`: read before final output or when reviewing an existing draft.

## Workflow

1. Identify the document type and the user's litigation role.
   - If the user says "诉讼书" or is unsure, map it to the closest court document from the facts and ask only for facts that block drafting.
   - Distinguish plaintiff/applicant, defendant/respondent, appellant/appellee, enforcement applicant, and preservation applicant.
2. Build a fact matrix before drafting.
   - Separate confirmed facts, user statements, missing facts, evidence-backed facts, and legal inferences.
   - Do not invent party names, ID numbers, addresses, case numbers, court names, dates, amounts, interest rates, evidence, or service addresses.
3. Choose the document structure.
   - Preserve required court headings such as "诉讼请求", "事实与理由", "证据目录", "此致", and "具状人".
   - Use placeholders in brackets for missing required fields, such as `[被告身份证号/统一社会信用代码待补充]`.
4. Draft claims first, then facts.
   - Make each claim concrete, enforceable, and linked to an amount, act, or legal effect.
   - Write facts in chronological order and connect each key fact to evidence where possible.
5. Apply legal pleading style.
   - Use restrained, evidence-oriented wording.
   - Avoid essay transitions, empty emphasis, moral outrage, customer-service disclaimers, and formulaic AI phrasing.
   - Keep necessary legal formulae and fixed pleading labels.
6. Review the draft for safety.
   - Mark missing facts and evidence gaps.
   - Flag claims that need legal research, limitation-period review, jurisdiction review, or amount recalculation.
   - Never state that a court will certainly support a claim.
7. Run punctuation cleanup when editing a Markdown or text file.

```bash
python agent/skills/legal-pleading-drafter/scripts/fix_punctuation.py draft.md
```

## Output Requirements

For a generated pleading, output:

- The full draft document.
- A short "待补充信息" section only when required fields remain missing.
- A short "证据对应关系" section if the evidence list is material to the document.
- A short "风险提示" section for uncertain jurisdiction, limitation period, claim basis, amount calculation, or unsupported facts.

For a polish-only task, output:

- The revised version.
- A brief list of legal-risk edits that were intentionally not made because they would change facts or claims.

## Hard Rules

- 不得虚构事实、证据、法条、案号、法院、金额、日期、当事人身份信息或送达地址。
- Do not fabricate facts, evidence, legal authorities, case numbers, court names, deadlines, or amounts.
- Do not delete legally necessary caution merely to make the writing sound more natural.
- Do not transform a pleading into a blog article, consultation note, or marketing-style explanation.
- Do not cite statutes unless the legal search or project law corpus has verified them in the current task.
- Do not remove fixed legal document labels as AI-like wording.
