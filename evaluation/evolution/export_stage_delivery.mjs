/** Read-only stage export. No backend imports, model calls, or source writes. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const sha = data => crypto.createHash('sha256').update(data).digest('hex');
const json = value => JSON.stringify(value, null, 2) + '\n';
const pct = value => value == null ? '待测' : `${(value * 100).toFixed(1)}%`;
const reductionText = value => value == null ? '不适用' : value < 0 ? `增加${pct(-value)}` : `下降${pct(value)}`;
const mean = value => value == null ? '待测' : value.toFixed(3);
const ratio = (a, b) => b ? a / b : null;
const arm = (row, label) => row.arms.find(item => item.arm === label);
const failure = a => 'semantic_failure' in a ? Boolean(a.semantic_failure) : a.semantic_verdict?.acceptable !== true;
const cost = a => a.repair_exhausted ? Number(a.max_repair_attempts || 2) + 1 : Number(a.repair_count || 0);
const fenced = (text, language = '') => {
  let fence = '````';
  while (text.includes(fence)) fence += '`';
  return `${fence}${language}\n${text}\n${fence}\n`;
};
const pick = (value, keys) => Object.fromEntries(keys.filter(k => k in value).map(k => [k, value[k]]));

export function metricsFor(rows) {
  const targets = rows.filter(r => r.case_group === 'target_fault');
  const totals = label => targets.reduce((out, row) => {
    const a = arm(row, label);
    out.first_pass += Number(a.first_audit_decision === 'pass');
    out.semantic_failures += Number(failure(a));
    out.actual_repairs += a.repair_attempt_count;
    out.repair_cost += cost(a);
    out.exhausted += Number(a.repair_exhausted);
    out.released += Number(a.release_allowed);
    return out;
  }, { first_pass: 0, semantic_failures: 0, actual_repairs: 0, repair_cost: 0, exhausted: 0, released: 0 });
  const A = totals('A'), B = totals('B'), n = targets.length;
  const improved = targets.filter(r => failure(arm(r, 'A')) && !failure(arm(r, 'B'))).map(r => r.case_id);
  const regressed = targets.filter(r => !failure(arm(r, 'A')) && failure(arm(r, 'B'))).map(r => r.case_id);
  for (const value of [A, B]) {
    value.first_pass_rate = ratio(value.first_pass, n);
    value.semantic_acceptable_rate = ratio(n - value.semantic_failures, n);
    value.actual_repair_mean = ratio(value.actual_repairs, n);
    value.repair_cost_mean = ratio(value.repair_cost, n);
  }
  return {
    scope: 'completed-pairs-stage-snapshot-not-final-100', completed_pairs: rows.length,
    group_counts: rows.reduce((o, r) => (o[r.case_group] = (o[r.case_group] || 0) + 1, o), {}),
    target_count: n, A, B, improved_case_ids: improved, regressed_case_ids: regressed,
    gross_rescue_rate: ratio(improved.length, A.semantic_failures),
    net_rescue_count: improved.length - regressed.length,
    net_rescue_rate: ratio(improved.length - regressed.length, n),
    first_pass_lift_pp: n ? (B.first_pass - A.first_pass) / n * 100 : null,
    actual_repair_reduction: ratio(A.actual_repairs - B.actual_repairs, A.actual_repairs),
    repair_cost_reduction: ratio(A.repair_cost - B.repair_cost, A.repair_cost),
    boundary_semantic_regression_rate: null,
    boundary_status: 'not_observed_in_this_target_only_snapshot',
    independent_human_review: false, final_integrity_claimed: false,
  };
}

function selfTest() {
  const a = (label, bad, exhausted = false) => ({ arm: label, semantic_failure: bad,
    first_audit_decision: bad ? 'revise' : 'pass', repair_attempt_count: bad ? 1 : 0,
    repair_count: bad ? 1 : 0, max_repair_attempts: 1, repair_exhausted: exhausted, release_allowed: !bad });
  const m = metricsFor([
    { case_id: '1', case_group: 'target_fault', arms: [a('A', true, true), a('B', false)] },
    { case_id: '2', case_group: 'target_fault', arms: [a('A', false), a('B', true)] },
  ]);
  assert.equal(m.gross_rescue_rate, 1);
  assert.equal(m.net_rescue_rate, 0);
  assert.equal(m.first_pass_lift_pp, 0);
  assert.equal(m.actual_repair_reduction, 0);
  assert.equal(m.repair_cost_reduction, 0.5);
  assert.equal(m.boundary_semantic_regression_rate, null);
  assert.equal(metricsFor([]).gross_rescue_rate, null);
  assert.equal(failure({ semantic_verdict: { acceptable: true } }), false);
  assert.equal(cost({ repair_exhausted: true, max_repair_attempts: 2 }), 3);
  console.log('Offline exporter self-tests passed');
}

function main() {
  const [checkpoint, dataset, outputParent, referenceZip] = process.argv.slice(2);
  assert(checkpoint && dataset && outputParent && referenceZip, 'checkpoint dataset outputParent referenceZip required');
  // One read only: a running service may advance while this immutable export is generated.
  const checkpointBytes = fs.readFileSync(checkpoint), datasetBytes = fs.readFileSync(dataset);
  const run = JSON.parse(checkpointBytes), exportedAt = new Date().toISOString();
  const cases = datasetBytes.toString('utf8').trim().split(/\r?\n/).map(line => JSON.parse(line));
  const receipts = run.final.receipts;
  const receiptMap = new Map(receipts.map(r => [r.case_id, r]));
  assert.equal(receiptMap.size, receipts.length, 'Duplicate receipts');
  const selectedCases = cases.filter(c => receiptMap.has(c.case_id));
  assert.equal(selectedCases.length, receipts.length, 'Unknown receipt IDs');
  const selected = selectedCases.map(c => receiptMap.get(c.case_id));
  assert(selected.length >= 50, 'At least 50 complete pairs required');
  // This delivery is deliberately target-only. Fail rather than misreport new control data.
  assert(selected.every(r => r.case_group === 'target_fault'), 'Control data now present: extend scorer before export');
  for (const [i, r] of selected.entries()) {
    assert.equal(r.case_id, cases[i].case_id, 'Not a consecutive frozen prefix');
    assert.equal(r.case_group, selectedCases[i].case_group);
    assert.equal(r.arms.length, 2);
    assert.deepEqual([...r.arms.map(a => a.arm)].sort(), ['A', 'B']);
    for (const key of ['context_equal', 'evidence_pack_equal', 'single_treatment_valid']) assert.equal(r[key], true);
    assert.equal(r.formal_environment_write_allowed, false);
    for (const a of r.arms) {
      assert.equal(a.semantic_verdict.status, 'judged');
      assert.equal(typeof a.semantic_verdict.acceptable, 'boolean');
      assert.equal(a.semantic_failure, !a.semantic_verdict.acceptable);
      assert(Number.isInteger(a.repair_attempt_count) && a.repair_attempt_count >= 0);
      assert.equal(typeof a.repair_exhausted, 'boolean');
      assert.equal(a.rule_exposed, a.arm === 'B');
      assert.deepEqual(a.model_input_forbidden_markers, []);
      assert(a.learner_visible_body === null || typeof a.learner_visible_body === 'string');
    }
  }
  const m = metricsFor(selected), n = m.completed_pairs;
  const stamp = exportedAt.replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
  const folder = `evolution-evaluation-20260905-${n}pairs-${stamp}`;
  const root = path.join(outputParent, folder);
  assert(!fs.existsSync(root), 'Never overwrite an earlier delivery');
  const files = [];
  const write = (relative, data) => {
    const target = path.join(root, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, data, { flag: 'wx' });
    files.push({ path: relative, bytes: Buffer.byteLength(data), sha256: sha(data) });
  };
  const limit = '这是已完成目标故障样本的阶段性交付，不是最终100对验收。边界组和正常组均未完成，规则误伤率待测。自动语义Judge与Expert使用同一配置模型，不构成独立人工医学真值审核。样本量超过50不等于赛题全部指标已达标。';
  const missing = '现存回执不保存首轮草稿、逐轮审核意见、每轮修订版本、完整模型请求/响应、工具调用与通信日志、逐token日志及各步耗时。重跑节点是无序去重集合，不代表执行顺序。未发布分支正文为null，不能补造。语义Judge对最终候选正文判定，发布被拦截不必然等于语义失败。';
  const table = [
    '| 核心指标 | A组 | B组 | 改善 |', '|---|---:|---:|---|',
    `| 同类问题改善率 | — | ${pct(m.gross_rescue_rate)}（${m.improved_case_ids.length}/${m.A.semantic_failures}） | 改善${m.improved_case_ids.length}、退步${m.regressed_case_ids.length}；净改善${pct(m.net_rescue_rate)} |`,
    `| 首次审核通过率 | ${pct(m.A.first_pass_rate)}（${m.A.first_pass}/${n}） | ${pct(m.B.first_pass_rate)}（${m.B.first_pass}/${n}） | ${m.first_pass_lift_pp >= 0 ? '+' : ''}${m.first_pass_lift_pp.toFixed(1)} pp |`,
    '| 规则误伤率 | — | 待测（边界组0/20） | 不能填写0% |',
    `| 平均实际返修次数 | ${mean(m.A.actual_repair_mean)} | ${mean(m.B.actual_repair_mean)} | ${reductionText(m.actual_repair_reduction)} |`,
    `| 平均返修成本（含耗尽惩罚，补充项） | ${mean(m.A.repair_cost_mean)} | ${mean(m.B.repair_cost_mean)} | ${reductionText(m.repair_cost_reduction)} |`,
  ].join('\n');
  const metadata = {
    export_schema: 'd1-stage-delivery-1.0', exported_at_utc: exportedAt, run_id: run.run_id,
    source_checkpoint_sha256: sha(checkpointBytes), frozen_dataset_sha256: sha(datasetBytes),
    reference_package_sha256: sha(fs.readFileSync(referenceZip)),
    source_status: run.status, source_final_status: run.final.status,
    selected_case_ids: selected.map(r => r.case_id), selection: 'all complete receipts in frozen order, no outcome filtering',
    included_pairs: n, included_arms: n * 2, total_planned_pairs: 100,
    excluded_half_pairs: Object.keys(run.final.pair_checkpoints || {}).filter(id => !receiptMap.has(id)),
    run: pick(run, ['dataset_id', 'dataset_version', 'execution_protocol_version', 'manifest_sha256',
      'candidate_digest', 'operator_full_blindness', 'operator_blindness_note', 'failure_observation_human_reviewed',
      'model_side_later_stage_unseen_during_extraction', 'formal_environment_write_allowed',
      'protocol_amendments', 'execution_deadlines']),
    limitations: [limit, missing],
  };
  write('README.md', `# 自我进化规则A/B测评交付包（${n}对）\n\n快照时间：${exportedAt}；轮次：${run.run_id}。\n\n${limit}\n\n${table}\n\n## 目录\n- 01_测试方案：实验协议、判定口径、候选规则与复核流程。\n- 02_测试数据：${n}条冻结输入，未混入未完成样本。\n- 03_测试结果：指标、逐样本明细、原始完整配对回执和来源元数据。\n- 04_完整轨迹：每对JSON、完整现存证据Markdown、输入输出精简版和轨迹清单。\n\n## 与参考压缩包的差异\n沿用参考包目录形式，不复用其幻觉率指标或实验数据。${missing}\n\n原样保留的模型输出可能含错误结论，仅用于评测审阅，不作为医疗建议。导出不发起模型调用、不重评分、不暂停服务、不改冻结代码或业务数据。未包含private.json、凭据、数据库快照或无关用户资料。\n`);
  write('01_测试方案/测试方案_自我进化A_B.md', `# 测试方案\n\nA不曝光候选规则；B曝光同一候选规则；材料甲乙与实验A/B不同。使用既有live回执，模型：${[...new Set(selected.map(r => r.model_name))].join(', ')}。\n\n按冻结顺序导出截至快照全部${n}个完整pair，未按效果筛样，不纳入半对。原计划60目标/20兼容边界/20正常；当前${n}/0/0。\n\n所有配对context_equal、evidence_pack_equal、single_treatment_valid均为true，正式写回false。阶段完整性不等于最终100条完整性。\n\n${limit}\n\n## 协议修订\n历史技术故障后延长审核/分支期限，两组对称，保留既有21对；操作人员已见部分结果。冻结数据、候选与评分不变，不能称完全盲测。\n\n${fenced(json(metadata.run), 'json')}`);
  write('01_测试方案/判定口径与人工复审流程.md', `# 判定口径与复审\n\n- 同类毛改善率：A失败且B成功的数量/A语义失败数。\n- 净改善率：(改善数−退步数)/已完成目标样本数。\n- 首审通过：first_audit_decision=pass，不以语义通过替代。\n- 误伤率：兼容边界组A语义正确而B错误的比例；当前没有边界样本，值为null。正常组另作对照。\n- 实际返修：repair_attempt_count均值，技术重试不计。\n- 冻结返修评分：耗尽计(max_repair_attempts or 2)+1，否则repair_count。本次上限1，耗尽计2；不得照搬旧说明“耗尽恒为3”。\n- 相对下降：(A均值−B均值)/A均值；A为0则不适用。\n- 正式评分部分分母固定60/20，本包自行使用实际目标数${n}，不调用最终评分器、不覆盖评分结果。\n\n## 人工复审（尚未执行，不得声称已通过）\n由未参与候选生成、未见A/B标签的独立复核人审阅打乱标签后的问题、材料和输出；分别复核五项语义维度、错误/缺失及理由。保留分歧、仲裁与原始记录。既有自动评估结果不得替换成真人签名。\n\n${missing}\n`);
  write('01_测试方案/候选规则原始记录.json', json(run.candidate));
  write('02_测试数据/已完成冻结测试数据集.jsonl', selectedCases.map(c => JSON.stringify(c)).join('\n') + '\n');
  write('02_测试数据/字段说明.md', '# 字段说明\n\ncase_id为冻结标识；case_group为实验分组；prompt为原始问题；materials为完整课堂材料甲乙；pair_order为AB/BA执行顺序，不是结果优劣；formal_environment_write_allowed=false。只收录已完成配对输入，不将未完成样本冒充已测。原始输入原样导出，未增加金标准或复审答案。\n');
  write('03_测试结果/核心指标与结果分析.md', `# 阶段指标（${n}对）\n\n${table}\n\n语义可接受率：A ${pct(m.A.semantic_acceptable_rate)}，B ${pct(m.B.semantic_acceptable_rate)}。A/B失败${m.A.semantic_failures}/${m.B.semantic_failures}；返修耗尽${m.A.exhausted}/${m.B.exhausted}；实际返修总计${m.A.actual_repairs}/${m.B.actual_repairs}；计分成本总计${m.A.repair_cost}/${m.B.repair_cost}。\n\n${limit}\n\n改善：${m.improved_case_ids.join('、')}。\n\n退步：${m.regressed_case_ids.join('、')}。\n`);
  write('03_测试结果/metrics.json', json(m));
  write('03_测试结果/快照来源与协议.json', json(metadata));
  write('03_测试结果/原始完整配对回执.json', json(selected));
  write('03_测试结果/技术异常原记录.json', json({ note: '原始快照的技术失败尝试；不是样本失败数，可能含当前未完成样本。不推断异常根因。', errors: run.final.technical_errors || [] }));
  const csv = ['case_id,case_group,pair_order,A_first_audit,B_first_audit,A_semantic_failure,B_semantic_failure,A_actual_repairs,B_actual_repairs,A_repair_cost,B_repair_cost,A_release,B_release'];
  const listing = ['# 现存轨迹清单\n', `共${n}对/${2 * n}分支，非逐轮全链路日志。${missing}\n`, '| 序号 | 案例 | JSON | SHA-256 |', '|---:|---|---|---|'];
  const full = ['# 完整现存配对轨迹\n', limit, missing, '\n## 共同候选规则\n', fenced(json(run.candidate), 'json')];
  const brief = ['# 输入输出精简版\n', '按冻结顺序保留全部案例；最终正文不截断、不改写。', missing];
  let nonNullBodies = 0;
  for (const [i, row] of selected.entries()) {
    const c = selectedCases[i], A = arm(row, 'A'), B = arm(row, 'B');
    const filename = `${String(i + 1).padStart(2, '0')}_${row.case_id}.json`;
    const trace = { case: c, receipt: row, trace_scope: 'complete_persisted_evidence_not_full_call_log', unavailable: missing };
    const content = json(trace);
    write(`04_完整轨迹/${filename}`, content);
    listing.push(`| ${i + 1} | ${row.case_id} | ${filename} | ${sha(content)} |`);
    csv.push([row.case_id, row.case_group, row.pair_order, A.first_audit_decision, B.first_audit_decision, failure(A), failure(B), A.repair_attempt_count, B.repair_attempt_count, cost(A), cost(B), A.release_allowed, B.release_allowed].join(','));
    full.push(`\n## 案例 ${i + 1}：${row.case_id}\n`, '### 完整冻结输入\n', fenced(json(c), 'json'), '### 原始配对元数据\n', fenced(json(Object.fromEntries(Object.entries(row).filter(([k]) => k !== 'arms'))), 'json'));
    brief.push(`\n## 案例 ${i + 1}：${row.case_id}\n`, '### 问题\n', fenced(c.prompt), '### 材料\n', fenced(json(c.materials), 'json'));
    for (const a of [A, B]) {
      const text = a.learner_visible_body === null ? '**未发布：原记录正文为null；不能还原被拦截草稿。**\n' : fenced(a.learner_visible_body);
      if (a.learner_visible_body !== null) nonNullBodies++;
      const summary = `规则曝光：${a.rule_exposed}；首审：${a.first_audit_decision}；实际返修：${a.repair_attempt_count}；耗尽：${a.repair_exhausted}；末审：${a.final_audit_decision}；发布：${a.release_allowed}；语义可接受：${a.semantic_verdict.acceptable}。`;
      full.push(`### ${a.arm}组轨迹与完整输出\n`, summary, text, '#### 原始组回执（正文另列，其他字段全保留）\n', fenced(json(Object.fromEntries(Object.entries(a).filter(([k]) => k !== 'learner_visible_body'))), 'json'));
      brief.push(`### ${a.arm}组\n`, summary, text);
    }
    brief.push(`### 差异\n${failure(A) && !failure(B) ? '语义改善' : !failure(A) && failure(B) ? '语义退步' : '语义可接受性不变'}；首审${A.first_audit_decision}→${B.first_audit_decision}；实际返修${A.repair_attempt_count}→${B.repair_attempt_count}。`);
  }
  write('03_测试结果/逐样本指标.csv', '\uFEFF' + csv.join('\n') + '\n');
  write('04_完整轨迹/轨迹清单.md', listing.join('\n') + '\n');
  const fullText = full.join('\n'), briefText = brief.join('\n');
  for (const row of selected) for (const a of row.arms) if (a.learner_visible_body !== null) {
    assert(fullText.includes(fenced(a.learner_visible_body)));
    assert(briefText.includes(fenced(a.learner_visible_body)));
  }
  assert.equal(fullText.split(/\n## 案例 /).length - 1, n);
  write('04_完整轨迹/各组完整现存输出与轨迹.md', fullText + '\n');
  write('04_完整轨迹/输入输出精简版.md', briefText + '\n');
  write('01_测试方案/只读导出与指标复算脚本.mjs', fs.readFileSync(fileURLToPath(import.meta.url)));
  const validation = { checked_at: new Date().toISOString(), pairs: n, arms: n * 2,
    non_null_bodies: nonNullBodies, null_bodies: n * 2 - nonNullBodies,
    dataset_prefix_verified: true, pair_contract_flags_verified: true,
    verbatim_bodies_verified_in_both_markdown_files: true,
    receipts_roundtrip_equal: JSON.stringify(JSON.parse(fs.readFileSync(path.join(root, '03_测试结果/原始完整配对回执.json')))) === JSON.stringify(selected),
    snapshot_only: true, original_checkpoint_not_written: true, service_not_interrupted: true };
  assert(validation.receipts_roundtrip_equal);
  write('03_测试结果/导出核验.json', json(validation));
  for (const file of files) assert.equal(sha(fs.readFileSync(path.join(root, file.path))), file.sha256);
  write('文件校验清单.json', json({ note: '不包含清单自身及外层zip；文件sha256为实际字节摘要', files: [...files] }));
  console.log(json({ root, metrics: m, validation, file_count: files.length }));
}

if (process.argv[2] === '--self-test') selfTest();
else if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();