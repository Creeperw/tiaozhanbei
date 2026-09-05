import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const [source, target] = process.argv.slice(2);
assert(source && target && !fs.existsSync(target), 'Require existing source and new target');
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const read = p => fs.readFileSync(path.join(source, p));
const parse = p => JSON.parse(read(p));
const originalManifest = parse('文件校验清单.json');
for (const f of originalManifest.files) assert.equal(hash(read(f.path)), f.sha256);
const rows = parse('03_测试结果/原始完整配对回执.json');
assert.equal(rows.length, 55);
const definitions = [
  ['task_completion_rate', '任务完成率', 'semantic_verdict.request_fulfilled === true', a => a.semantic_verdict.request_fulfilled],
  ['conflict_relationship_correct_rate', '冲突关系处理正确率', 'semantic_verdict.relationship_handled_correctly === true', a => a.semantic_verdict.relationship_handled_correctly],
  ['deliverable_qualified_rate', '最终可交付合格率', 'semantic_verdict.acceptable === true AND release_allowed === true', a => a.semantic_verdict.acceptable && a.release_allowed],
  ['unsupported_resolution_avoidance_rate', '无依据消解冲突避免率', 'semantic_verdict.no_unsupported_resolution === true', a => a.semantic_verdict.no_unsupported_resolution],
];
const metrics = definitions.map(([id, name, definition, predicate]) => {
  let A = 0, B = 0;
  const improved = [], regressed = [];
  for (const row of rows) {
    assert.equal(row.arms.length, 2);
    const a = row.arms.find(x => x.arm === 'A'), b = row.arms.find(x => x.arm === 'B');
    assert(a && b);
    assert.equal(a.semantic_verdict.status, 'judged');
    assert.equal(b.semantic_verdict.status, 'judged');
    const x = predicate(a), y = predicate(b);
    assert.equal(typeof x, 'boolean');
    assert.equal(typeof y, 'boolean');
    A += Number(x); B += Number(y);
    if (!x && y) improved.push(row.case_id);
    if (x && !y) regressed.push(row.case_id);
  }
  return { id, name, definition, denominator: rows.length, A_count: A, B_count: B,
    A_rate: A / rows.length, B_rate: B / rows.length,
    absolute_change_pp: (B - A) / rows.length * 100,
    improved_case_ids: improved, regressed_case_ids: regressed };
});
assert.deepEqual(metrics.map(m => [m.A_count, m.B_count]), [[46,52],[45,55],[34,45],[40,51]]);
const note = '本版按用户要求，在观察结果后调整主展示指标；属于事后探索性报告，不改变原冻结评分合同，不声称预先注册或赛题官方指定。保持2026-09-05T09:42:25.621Z的55对目标故障快照，未重新调用模型或裁判。边界组及正常组未完成，误伤率待测；自动Judge与Expert共享配置模型，尚非独立人工复核结论。';
const table = ['| 核心指标 | A组 | B组 | 变化 |', '|---|---:|---:|---:|', ...metrics.map(m =>
  `| ${m.name} | ${(m.A_rate*100).toFixed(1)}%（${m.A_count}/55） | ${(m.B_rate*100).toFixed(1)}%（${m.B_count}/55） | +${m.absolute_change_pp.toFixed(1)} pp |`)].join('\n');
fs.cpSync(source, target, { recursive: true, errorOnExist: true, force: false });
const write = (p, data) => fs.writeFileSync(path.join(target, p), data);
write('03_测试结果/附录_原核心指标与结果分析.md', read('03_测试结果/核心指标与结果分析.md'));
write('03_测试结果/附录_原metrics.json', read('03_测试结果/metrics.json'));
const result = { schema: 'd1-stage-four-quality-metrics-v2', source_snapshot_utc: '2026-09-05T09:42:25.621Z',
  pairs: rows.length, arms: rows.length*2, selection_timing: 'post_hoc_user_requested',
  frozen_scoring_contract_modified: false, limitations: note, core_metrics: metrics };
write('03_测试结果/metrics.json', JSON.stringify(result, null, 2)+'\n');
const oldReadme = read('README.md').toString('utf8');
const start = oldReadme.indexOf('| 核心指标 |'), end = oldReadme.indexOf('\n## 目录', start);
assert(start >= 0 && end > start);
write('README.md', oldReadme.slice(0,start)+table+'\n\n'+note+'\n\n旧指标完整保留于03_测试结果/附录_原核心指标与结果分析.md和附录_原metrics.json。\n'+oldReadme.slice(end));
const definitionsText = metrics.map(m => `- **${m.name}**：\`${m.definition}\`。分母为全部55对，不只统计获准发布分支。`).join('\n');
const sources = `## 方法依据与对应边界

- [Microsoft Foundry RAG评估文档](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/rag-evaluators)：Relevance关注回答对查询的准确、完整回应；Groundedness关注基于上下文、避免编造。任务完成和无依据消解避免与这些维度相关，但本包没有运行其官方评估器。
- [RAGAS，EACL 2024](https://aclanthology.org/2024.eacl-demo.16/)：讨论回答相关性、证据忠实性等维度。冲突关系处理是本任务的专门判定项，并非RAGAS原生指标；不得称为RAGAS得分。
- 最终可交付合格率是项目定义的交集指标，同时要求语义合格和发布许可，属于用户实际可获得合格结果的代理指标，不声称机构官方指标。
- [Stanford HELM多指标评估原则](https://crfm.stanford.edu/2022/11/17/helm.html)：同时呈现多维效果和权衡。因此原首审/返修等不利结果保留附录，不删去。

以上资料支持评估维度及多指标方法，不构成对本项目评分实现或结果的认证。`;
write('01_测试方案/四项主指标口径与依据.md', '# 四项主展示指标\n\n'+note+'\n\n'+definitionsText+'\n\n冲突关系正确不等于最终回答整体合格；无依据消解避免不等于整篇回答完全无幻觉。最终可交付合格率使用全部55对作分母，未发布样本计为不合格。其余三项使用对最终候选的已存判定，即使正文未发布也不排除。\n\n'+sources+'\n');
write('03_测试结果/核心指标与结果分析.md', '# 四项主指标（55对固定快照，修订版）\n\n'+table+'\n\n'+definitionsText+'\n\n'+note+'\n\n## 解读\n\nB组任务完成、冲突关系处理、可交付合格结果及无依据消解避免均优于A组。关系处理55/55仅表示当前样本全部通过，不保证新样本100%；这些指标相关，不是四份独立实验。旧指标显示首审下降1.8pp、实际返修增加7.7%，仍保留附录。\n\n'+sources+'\n');
for (const file of ['01_测试方案/测试方案_自我进化A_B.md','01_测试方案/判定口径与人工复审流程.md']) {
  write(file, '> 修订说明：本包主展示指标改为任务完成率、冲突关系处理正确率、最终可交付合格率、无依据消解冲突避免率。详见“四项主指标口径与依据.md”。下文保留原冻结实验设计与评分口径，不作追溯修改。\n\n'+read(file).toString('utf8'));
}
const csv = ['case_id,arm,task_completion,conflict_relationship_correct,deliverable_qualified,unsupported_resolution_avoided'];
for (const row of rows) for (const a of row.arms) csv.push([row.case_id,a.arm,...definitions.map(d => Number(d[3](a)))].join(','));
write('03_测试结果/四项主指标逐分支明细.csv', '\uFEFF'+csv.join('\n')+'\n');
write('01_测试方案/四项指标交付修订脚本.mjs', fs.readFileSync(fileURLToPath(import.meta.url)));
// Original evidence and all trajectories must remain byte-identical.
const protectedFiles = originalManifest.files.filter(f => f.path.startsWith('02_测试数据/') || f.path.startsWith('04_完整轨迹/') || ['03_测试结果/原始完整配对回执.json','03_测试结果/快照来源与协议.json','03_测试结果/技术异常原记录.json'].includes(f.path));
for (const f of protectedFiles) assert.equal(hash(fs.readFileSync(path.join(target,f.path))),f.sha256);
write('03_测试结果/主指标修订核验.json', JSON.stringify({ verified_at:new Date().toISOString(),
  source_manifest_sha256:hash(read('文件校验清单.json')), protected_files_byte_identical:protectedFiles.length,
  counts_verified:metrics.map(m=>({name:m.name,A:m.A_count,B:m.B_count})),
  prior_metrics_preserved:true, original_delivery_unchanged:true }, null, 2)+'\n');
const files=[];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, {withFileTypes:true})) {
    const absolute=path.join(dir,entry.name), relative=path.relative(target,absolute);
    if(entry.isDirectory()) walk(absolute);
    else if(relative!=='文件校验清单.json') {const b=fs.readFileSync(absolute);files.push({path:relative,bytes:b.length,sha256:hash(b)});}
  }
}
walk(target);
files.sort((a,b)=>a.path.localeCompare(b.path));
write('文件校验清单.json',JSON.stringify({note:'不含清单自身与外层ZIP',files},null,2)+'\n');
console.log(JSON.stringify({target,metrics,verified_files:files.length,protected_files:protectedFiles.length},null,2));