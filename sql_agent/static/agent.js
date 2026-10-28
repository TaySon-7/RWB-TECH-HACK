const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const date = (value, fallback) => { const d = new Date(/^\d+$/.test(value || '') ? Number(value) : value || fallback); return isNaN(d) ? new Date(fallback) : d; };
const from = date(params.get('from'), '2016-09-01T00:00:00Z');
const to = date(params.get('to'), '2018-08-31T23:59:59Z');
const iso = d => d.toISOString().slice(0, 10);
const key = `capibara-agent:${iso(from)}:${iso(to)}`;
const grafanaRoot = location.pathname.startsWith('/agent') ? '' : location.protocol+'//'+location.hostname+':3000';
let saved; try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch { saved = {}; }
let conversation = saved.conversation || null, turns = saved.turns || [], busy = false, waiting = false;
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
function persist() { localStorage.setItem(key, JSON.stringify({conversation, turns})); }
function link(text, href) { const a = node('a', text); a.href = href; a.target = '_blank'; a.rel = 'noopener'; return a; }
function details(title, content) { const d = node('details'); d.append(node('summary', title), content); return d; }
function warning(text) { return node('div', text, 'warning'); }
function evidence(q) {
  const quality = q.quality || {}, box = node('div');
  const names = {input_scope: 'Входные записи до агрегации', source: 'Общий отчёт источников', unavailable: 'Охват не установлен'};
  box.append(node('p', names[quality.coverage] || 'Проверка источников', 'source-note'));
  for (const source of quality.sources || []) {
    box.append(node('b', source.source)); const grid = node('div', null, 'quality-grid');
    for (const [label, count] of [['Допущено', source.accepted], ['Отклонено', source.rejected], ['Дубликатов', source.duplicates]]) {
      const stat = node('div', label, 'quality-stat'); stat.prepend(node('strong', Number(count || 0).toLocaleString('ru-RU'))); grid.append(stat);
    } box.append(grid);
    for (const rule of source.rules || []) box.append(node('p', `${rule.records} · ${rule.message || rule.rule_id} (${rule.action})`, 'source-note'));
    if (source.examples?.length) box.append(details('Исходные отклонённые записи и причины', node('pre', JSON.stringify(source.examples, null, 2))));
  }
  const unknown = Object.entries(quality.unattributed_source_rejections || {});
  if (unknown.length) box.append(warning('Без ключей атрибуции в источнике: ' + unknown.map(([t,n]) => `${t}: ${n}`).join(', ') + '. Нельзя достоверно отнести эти строки к выбранному продавцу или периоду.'));
  if (quality.files) box.append(details('Исходные файлы', node('pre', JSON.stringify(quality.files, null, 2))));
  if (quality.source_warnings?.length) box.append(details('Подозрительные данные во всём core · не исключения этого запроса', node('pre', JSON.stringify(quality.source_warnings, null, 2))));
  for (const text of quality.limitations || []) box.append(warning(text));
  if (!quality.sources?.length && !quality.limitations?.length) box.append(node('p', 'Построчный охват для этой формы запроса не установлен.', 'source-note'));
  return box;
}
function explore(sql) {
  const source = {uid:'capibara-clickhouse',type:'grafana-clickhouse-datasource'};
  const pane = {datasource:source.uid,queries:[{refId:'A',datasource:source,rawSql:sql,queryType:'sql',editorType:'sql',format:1,meta:{builderOptions:{mode:'sql',queryType:'table'}}}],range:{from:String(+from),to:String(+to)}};
  return grafanaRoot+'/explore?' + new URLSearchParams({schemaVersion:'1',orgId:'1',panes:JSON.stringify({agent:pane})});
}
function renderResult(result, target) {
  target.replaceChildren(); const label = node('div', 'CAPIBARA / АГЕНТ', 'answer-label');
  label.append(node('span', {completed:'Расчёт выполнен',partial:'Есть ограничения',failed:'Расчёт не завершён'}[result.status] || result.status, 'status ' + result.status));
  target.append(label, node('p', result.summary));
  for (const q of result.queries || []) {
    const card = node('section', null, 'query'), head = node('div', null, 'query-head');
    head.append(node('span', `${q.query_id} · ${q.elapsed_ms} мс · ${q.rows.length} строк`));
    if ($('backend').dataset.backend === 'clickhouse') head.append(link('Этот SQL в Grafana ↗', explore(q.sql)));
    card.append(head);
    if (q.rows.length) { const wrap = node('div', null, 'table-wrap'), table = node('table'), row = node('tr');
      for (const col of q.columns) row.append(node('th', col)); const thead = node('thead'); thead.append(row); table.append(thead);
      const tbody = node('tbody'); for (const values of q.rows) { const tr = node('tr'); for (const value of values) tr.append(node('td', value == null ? 'NULL' : String(value))); tbody.append(tr); } table.append(tbody); wrap.append(table); card.append(wrap);
    } else card.append(node('p', 'По этим условиям строк не найдено.', 'empty'));
    if (q.truncated) card.append(warning('Таблица обрезана лимитом вывода. Агрегаты рассчитаны до ограничения строк.'));
    card.append(details('Выполненный SQL', node('pre', q.sql)), details('Из чего получен результат / качество данных', evidence(q)), node('p', `Версия данных: ${q.data_version || 'не установлена'} · ${q.tables.join(', ')}`, 'source-note'));
    target.append(card);
  }
  for (const text of result.limitations || []) target.append(warning(text));
}
function renderTurn(turn) {
  const article = node('article', null, 'turn'); article.append(node('div', turn.question, 'question'));
  const answer = node('div', null, 'answer'); article.append(answer); $('chat').append(article);
  if (turn.result) renderResult(turn.result, answer);
  else if (turn.clarification) answer.append(node('p', turn.clarification.question));
  return answer;
}
function setBusy(value) { busy = value; $('send').disabled = value; $('question').disabled = value; document.querySelectorAll('[data-question]').forEach(b => b.disabled=value); $('activity').className = value ? 'pulse' : ''; if (!value) $('activity').textContent=''; }
async function api(path, options) { const response = await fetch(path, options); const result = await response.json(); if (!response.ok) throw Error(result.detail || `Ошибка ${response.status}`); return result; }
async function poll(target) {
  setBusy(true);
  try {
    while (true) {
      const state = await api('api/chat/' + conversation);
      if (state.status === 'running') { $('activity').textContent=state.stage || 'Считаю'; await new Promise(r=>setTimeout(r,900)); continue; }
      if (state.status === 'waiting') {
        turns[turns.length-1].clarification=state.clarification; persist();
        waiting = true; target.replaceChildren(node('p', state.clarification.question)); const choices = node('div', null, 'choices');
        for (const option of state.clarification.options) { const b = node('button', option); b.onclick=()=>send(option); choices.append(b); } target.append(choices);
        $('question').placeholder='Ответьте на уточнение или выберите вариант выше';
      } else { waiting=false; renderResult(state.result, target); turns[turns.length-1].result=state.result; persist(); }
      break;
    }
  } catch (error) { target.append(warning(error.message)); }
  finally { setBusy(false); $('question').focus(); }
}
async function send(text) {
  if (busy || !text.trim()) return; $('intro').hidden=true;
  const resume=waiting; turns.push({question:text}); const target=renderTurn(turns[turns.length-1]);
  if (resume) document.querySelectorAll('.choices button').forEach(b=>b.disabled=true);
  setBusy(true); $('activity').textContent=resume ? 'Продолжаю с вашим уточнением' : 'Подключаю агента'; $('question').value='';
  try {
    const context = !resume && $('use-period').checked ? `\nКонтекст дашборда: период с ${iso(from)} по ${iso(to)} включительно. Используй его, если в вопросе не указан другой период.` : '';
    const result=await api('api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:text+context,conversation_id:conversation,resume})});
    conversation=result.conversation_id; persist(); await poll(target);
  } catch(error) { target.append(warning(error.message)); setBusy(false); }
}
const displayDate = d => d.toLocaleDateString('ru-RU', {timeZone:'UTC'});
$('period').textContent=`${displayDate(from)} — ${displayDate(to)}`;
$('dashboard').href=grafanaRoot+'/d/capibara-commerce?'+new URLSearchParams({from:String(+from),to:String(+to)});
$('composer').onsubmit=e=>{e.preventDefault();send($('question').value);};
$('question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send($('question').value);}};
$('question').oninput=()=>{$('question').style.height='auto';$('question').style.height=$('question').scrollHeight+'px';};
$('reset').onclick=()=>{if(busy)return;localStorage.removeItem(key);location.reload();};
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>send(b.dataset.question));
async function init() {
  try { const health=await api('health'); $('backend').textContent=health.backend; $('backend').dataset.backend=health.backend;
    for (const turn of turns) renderTurn(turn); if(turns.length) $('intro').hidden=true;
    if(conversation&&turns.length&&!turns[turns.length-1].result) await poll($('chat').lastElementChild.querySelector('.answer'));
    else if(!conversation&&params.get('autostart')==='1'&&health.llm_configured) send('Какова общая выручка от товаров за выбранный период?');
    if(!health.llm_configured) $('activity').textContent='Нужно настроить подключение LLM на сервере (.env.agent).';
  } catch(error) { $('activity').textContent=error.message; }
}
init();
