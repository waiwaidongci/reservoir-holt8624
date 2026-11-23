import {feedback, field} from '../components/common/fields.js';
import {empty, panel} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {READING_KIND} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

function calibrationPanel(spec, reservoirId, refresh) {
  const message = feedback();
  const list = el('div', {class:'calibration-list'}, p('加载中…', 'subtle'));
  async function load() {
    if (!reservoirId) { list.replaceChildren(p('该批次暂无水池编号。', 'subtle')); return; }
    const rows = await workspace.calibrations(reservoirId);
    list.replaceChildren(table([
      {key:'reading_date', label:'核尺日期'},
      {key:'level_m', label:'核尺液位（m）'},
      {key:'note', label:'备注'},
      {label:'录入人', render:row=>row.author},
      {label:'录入时间', render:row=>date(row.created_at)},
    ], rows, {caption:'核尺实测', empty:'暂无核尺记录。晚到核尺录入后重新约束后续运行。', pageSize:8}));
  }
  const submitCal = button('录入/覆盖当日核尺', {type:'submit', class:'primary', 'data-testid':'save-calibration'});
  const form = session.canWrite && el('form', {class:'fields', onSubmit:handler(submitCal,message,async event=>{
    const data = new FormData(event.target);
    await workspace.saveCalibration({
      reservoir_id: data.get('reservoir_id') || reservoirId,
      reading_date: data.get('reading_date'),
      level_m: Number(data.get('level_m')),
      note: data.get('note') || '',
    });
    event.target.reset();
    await Promise.all([load(), refresh()]);
  })},
    field({key:'reservoir_id', label:'水池编号（默认本批次）', type:'string', required:false, max_length:40}, reservoirId),
    field({key:'reading_date', label:'核尺日期', type:'date'}),
    field({key:'level_m', label:'核尺液位（m）', type:'number', min:0, max:20}),
    field({key:'note', label:'备注', type:'string', required:false, max_length:500}),
    submitCal,
    message);
  load().catch(error => { list.replaceChildren(p(error.message, 'feedback')); });
  return panel('核尺实测（晚到补录）',
    p('核尺是确定值约束：录入后重新约束该日前后可行液位，并可桥接缺报日；历史报告保持当时冻结快照。', 'subtle'),
    list, form);
}

export async function inputs(route, refresh) {
  const {spec,datasets} = workspace;
  const selected = datasets.find(item => item.id === route.id) || datasets[0];
  const error = feedback();
  const sample = button('下载示例 CSV', {class:'quiet',onClick:event=>handler(sample,error,()=>workspace.download('/template',spec.sample_name))(event)});
  const detail = selected && await workspace.getDataset(selected.id,route.offset);
  const columns = spec.fields.map(item => item.key === 'reading_kind'
    ? {key:'reading_kind', label:item.label, render:row=>READING_KIND[row.reading_kind] || value(row.reading_kind)}
    : {key:item.key, label:item.label});
  const reservoirId = detail?.rows?.[0]?.reservoir_id || '';
  const preview = detail ? panel('批次明细', el('div',{class:'actions'},
    el('label',{for:'dataset-id',class:'sr'}, '选择数据批次'),
    el('select',{id:'dataset-id','aria-label':'选择数据批次',onChange:event=>{location.hash=`#inputs/${event.target.value}`;}},
      datasets.map(item=>el('option',{value:item.id,selected:item.id === selected.id}, item.name))),
    link('使用此批次运行',`#run/${detail.id}`,{class:'quiet'})),
    p(`${detail.filename} · ${detail.row_count} 行 · ${date(detail.created_at)}`,'filename'),
    table(columns,detail.rows,{caption:'批次明细',pageSize:16}),
    el('div',{class:'pagination'},
      route.offset > 0 && link('上一批明细',`#inputs/${detail.id}/${Math.max(0,route.offset-16)}`,{class:'quiet'}),
      p(`${detail.rows.length ? route.offset+1 : route.offset}–${route.offset+detail.rows.length} / ${detail.row_count}`),
      route.offset+16 < detail.row_count && link('下一批明细',`#inputs/${detail.id}/${route.offset+16}`,{class:'quiet'})))
    : empty('没有可用的批次。请导入示例或业务 CSV。');
  const submit = button('校验并导入', {type:'submit',class:'primary'});
  const upload = session.canWrite && el('form',{class:'fields',onSubmit:handler(submit,error,async event=>{
    const data = new FormData(event.target);
    const file = data.get('source');
    if (!file?.size) throw new Error('请选择一个 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const item = await workspace.upload({name:data.get('batch_name'),filename:file.name,csv_text:await file.text()});
    const target = `#inputs/${item.id}`;
    if (location.hash === target) await refresh();
    else location.hash = target;
  })},field({key:'batch_name',label:'批次名称',type:'string',max_length:120}),
    el('label',{class:'field',for:'source'}, el('span',{},'业务 CSV'),el('input',{id:'source',type:'file',name:'source',accept:'.csv,text/csv',required:true})), submit);
  return el('div',{class:'two-col'},el('div',{class:'wide-panel'},preview,
    detail && calibrationPanel(spec, reservoirId, refresh)),el('aside',{class:'sidebar-panel'},
    panel('数据导入',p('导入后保留原批次和校验指纹，可用同一份数据复跑。满格与缺报按读数状态区分。','subtle'),sample,upload,error),
    panel('字段说明',el('dl',{class:'dictionary'},spec.fields.map(item=>el('div',{},el('dt',{},item.label),
      el('dd',{},item.key,item.required === false ? ' · 可空' : ' · 必填',
        item.options ? ` · ${item.options.map(o=>READING_KIND[o]||o).join(' / ')}` : '')))))));
}
