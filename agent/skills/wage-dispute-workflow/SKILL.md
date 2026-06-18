---
name: wage-dispute-workflow
description: 工资争议咨询流程。Use when the legal agent handles salary, wage, payroll, labor remuneration, unpaid wages, delayed payment, wage deduction, minimum wage, probation wage, overtime pay, commission, performance pay, bonus, final paycheck, or payroll-cycle questions in Chinese labor-law consultations.
---

# Wage Dispute Workflow

## Purpose

Use this skill when the user asks about wages or labor remuneration. The goal is to classify the wage issue, collect blocking facts, verify current legal rules through the legal search tools, calculate claim items conservatively, and output evidence-backed next steps.

This skill covers labor-law wage disputes. If the facts show a contractor, freelancer, platform worker, civil servant, shareholder, or business-to-business payment dispute rather than an employment relationship, route the issue to the appropriate contract, platform labor, administrative, or company-law workflow.

## Workflow

1. Classify the wage issue.
   - Identify whether the question concerns unpaid wages, delayed wages, wage deductions, probation wages, minimum wage, overtime pay, commission, performance pay, bonus, piece-rate pay, part-time pay, final wages after resignation, or the monthly wage base for compensation.
   - Separate wage-payment claims from termination compensation, social-insurance contribution, tax withholding, work-injury wage benefits, and contract-service remuneration.
2. Collect only blocking facts before analysis.
   - Ask for city or workplace, employer type, job role, employment start/end dates, written contract or offer terms, agreed wage structure, actual payment cycle, unpaid period, amount paid and unpaid, attendance and overtime facts, resignation or termination status, and existing evidence.
   - If the user needs urgent guidance, give a provisional answer with assumptions and mark the missing facts.
3. Verify legal basis in the current task.
   - Use the legal search tools or law corpus before citing statutes, local wage-payment rules, minimum wage standards, overtime rules, arbitration limitation rules, or local average-wage data.
   - Prioritize labor-law, labor-contract, labor-dispute arbitration, wage-payment, minimum-wage, and local implementing rules that match the user's city and date.
   - Do not rely on memory for current local minimum wage, average salary, or regional wage-payment cycle rules.
4. Build a wage claim table.
   - For each item, list `claim item`, `period`, `base`, `rate or formula`, `evidence`, `amount`, and `uncertainty`.
   - Common items include unpaid regular wages, overtime pay, wage deduction difference, minimum-wage shortfall, probation wage shortfall, unpaid commission or performance pay, year-end bonus if rules support it, final paycheck, and wage-based compensation calculations.
   - Do not invent workdays, overtime hours, wage bases, bonus conditions, commission rates, tax amounts, or social-insurance figures.
5. Assess proof and risk.
   - Check whether the employment relationship is proven.
   - Check whether the wage amount is agreed, actually paid, or shown by payroll/bank/tax records.
   - Check whether overtime was arranged or approved by the employer and whether attendance records support the hours.
   - Check whether bonus, commission, or performance pay is discretionary or tied to measurable conditions.
   - Check arbitration limitation and any demand, complaint, mediation, or employer acknowledgment that may affect timing.
6. Give the path forward.
   - Start with evidence preservation and a written wage demand when appropriate.
   - Distinguish 劳动监察投诉, labor arbitration, mediation, lawsuit after arbitration, and emergency reporting paths.
   - When wage arrears are linked to resignation, termination, or economic compensation, state the link but keep the wage claim and termination claim separately calculated.

## Evidence Checklist

Use this checklist when the user asks what to prepare:

- Labor relationship: labor contract, offer, onboarding record, work badge, roster, work chat, email, social-insurance or tax records.
- Wage agreement: contract wage clause, offer salary, handbook, pay policy, commission plan, bonus policy, performance rules.
- Payment proof: bank statements, wage slips, payroll screenshots, tax app records, transfer notes, accounting confirmation.
- Attendance and overtime: attendance records, schedules, clock-in logs, overtime applications, manager approvals, work chat, deliverables.
- Wage arrears and demands: unpaid-period list, written demand, chat records, complaint records, employer acknowledgments, resignation or termination documents.

## Output Requirements

For a wage consultation, output:

- A short issue classification.
- Key missing facts, only if they materially affect the answer.
- Verified legal basis, with statutes or local rules only when retrieved in the current task.
- A claim table or formula list when money is involved.
- Evidence gaps and next-step path.
- Risk notes for limitation period, proof gaps, local-rule uncertainty, discretionary pay, and calculation assumptions.

## Hard Rules

- Do not cite statutes, minimum wage standards, local average wages, or regional wage-payment rules unless verified in the current task.
- Do not promise that arbitration or court will support a claim.
- Do not treat all bonuses, commissions, reimbursements, equity incentives, or allowances as wages without checking the agreement and payment conditions.
- Do not advise the user to withhold company property, threaten the employer, fabricate attendance, or alter evidence.
- Preserve privacy: avoid exposing ID numbers, bank account numbers, payroll screenshots, or employer trade secrets beyond what is needed for analysis.
