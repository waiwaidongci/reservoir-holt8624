import {plot} from '../components/common/chart.js';
import {feedback} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {CONTACT, READING_KIND, REVIEW, STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

const contactPill = category => pill(CONTACT[category] || category, `contact-${category}`);
const kindCell = row => READING_KIND[row.kind] || value(row.kind);

function renderEvidenceTable(item) {
  const columns = item.columns.map(col => {
    if (col.key === 'contact') return {...col, render: row => contactPill(row.category)};
    if (col.key === 'kind') return {...col, render: kindCell};
    if (col.key === 'feasible') return {...col, render: row => pill(row.feasible ? '成立' : '无解', row.feasible ? 'ok' : 'conflict')};
    return col;
  });
  const isConflict = item.title.startsWith('无解冲突区间');
  return panel(item.title,
    isConflict && item.rows.length > 0 && p('约束无解时优先处理最早的最短冲突区间；调整日最大涨跌、核尺或读数后重跑。', 'subtle'),
    table(columns, item.rows, {caption: item.title}));
}

export async function results(route, refresh) {
  const {spec,runs,datasets} = workspace;
  const id = route.id || runs[0]?.id;
  if (!id) return panel(spec.pages[3],empty('还没有可查看的运行结果。','#run','前往运行'));
  const job = await workspace.getRun(id);
  const source = datasets.find(item => item.id === job.dataset_id);
  const message = feedback();
  const finished = item => item.status === 'complete' || item.status === 'failed';
  if (!finished(job)) {
    const original = location.hash;
    const status = el('p',{role:'status'},'后台正在计算，完成后会自动展示。');
    poll(()=>workspace.getRun(id),finished,()=>location.hash===original)
      .then(result=>{if (result && status.isConnected && location.hash===original) refresh();
        else if(status.isConnected) status.textContent='仍在执行，请点击刷新检查。';})
      .catch(error=>{if(status.isConnected) status.textContent=error.message;});
    return panel(job.name,pill(STATUS[job.status],job.status),status,button('刷新',{class:'quiet',onClick:refresh}));
  }
  const download = button('下载结果 CSV',{class:'quiet','data-testid':'export',onClick:event=>
    handler(download,message,()=>workspace.download(`/runs/${job.id}/export`,`${spec.short_name}-${job.id}.csv`))(event)});
  const history = el('label',{class:'field run-picker',for:'run-picker'},el('span',{},'切换运行记录'),
    el('select',{id:'run-picker',onChange:event=>{location.hash=`#results/${event.target.value}`;}},
      (runs.some(item=>item.id===job.id) ? runs : [job,...runs]).map(item=>el('option',{value:item.id,selected:item.id===job.id},item.name))));
  const result = job.result;
  const reuseNote = job.reused && p('同一批读数、限值与核尺已有运行，本次直接复用，未新增报告。', 'subtle');
  const analysis = result ? el('section',{class:'analysis','data-testid':'analysis-complete'},
    el('div',{class:'result-head'},p(job.name,'eyeline'),h(2,result.headline),
      p(result.band_headline, result.has_conflict ? 'band-headline conflict' : 'band-headline'),p(result.note,'intro-copy'),
      el('div',{class:'actions'},pill(STATUS[job.status],job.status),
        result.has_conflict ? pill('约束无解','conflict') : pill('约束成立','ok'),download,message)),
    reuseNote,
    metrics(result.metrics),
    result.charts.map(plot),
    (result.sections || []).map(item=>panel(item.title,p(item.text))),
    result.tables.map(renderEvidenceTable))
    : panel(job.name,pill(STATUS[job.status],job.status),p(job.error,'feedback'),link('返回调整参数',`#run/${job.dataset_id}`,{class:'quiet'}));
  const inputs = spec.params.map(param=>el('div',{},el('dt',{},param.label),el('dd',{},value(job.params[param.key]))));
  const snapshot = job.input_snapshot;
  const snapshotPanel = snapshot && panel('运行时刻输入快照',
    p('旧报告永久保留当时冻结的读数、限值与核尺；晚到核尺只影响之后的新运行。', 'subtle'),
    el('dl',{class:'dictionary'},
      el('div',{},el('dt',{},'数据指纹'),el('dd',{},job.fingerprint?.slice(0,16) || '—')),
      el('div',{},el('dt',{},'生效核尺'),el('dd',{},`${snapshot.calibrations?.length || 0} 条`)),
      el('div',{},el('dt',{},'量程顶/池顶/日最大涨跌'),
        el('dd',{},`${value(snapshot.limits?.gauge_max_m)} / ${value(snapshot.limits?.pool_top_m)} / ${value(snapshot.limits?.max_daily_change_m)} m`))));
  return el('div',{},el('div',{class:'two-col'},el('div',{class:'wide-panel'},analysis),
    el('aside',{class:'sidebar-panel'},panel('运行来源',history,
      source && link(source.name,`#inputs/${source.id}`),p(date(job.finished_at),'subtle'),
      el('dl',{class:'dictionary'},inputs)),
    snapshotPanel,
    result && panel(spec.decision, pill(REVIEW[job.review],job.review),
      link('打开复核记录',`#review/${job.id}`,{class:'quiet'})))));
}
