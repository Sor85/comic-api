'use strict';
/*
 * 收藏本地同步（JM、哔咔共用）：页头「本地同步」入口、同步模态窗口、同步设置与服务器目录选择。
 * 同一套窗口按 favSync.source 显示对应图源；各图源的任务状态独立保存，页头入口始终反映自身图源的进度。
 */
const FAV_SYNC = {
  jm:{label:'JM',tab:'jm-favorites',folders:true,ids:['123456','123457'],sortKey:'order',sorts:{mr:'收藏时间',mp:'更新时间'}},
  bika:{label:'哔咔',tab:'bika-favorites',folders:false,ids:['5e7f2a9c1b3d4e6f8a0b2c4d','1'],sortKey:'sort',sorts:{dd:'最新上架',da:'最旧上架',ld:'最多喜欢',vd:'最多观看'}},
};
const favSync = {source:'jm',view:{},jm:{active:false,settings:null,local:{}},bika:{active:false,settings:null,local:{}}};
const LOCAL_STATUS = {downloaded:{label:'已下载',tone:'ok',icon:'check'},error:{label:'本地异常',tone:'danger',icon:'alert'},missing:{label:'未下载',tone:'muted',icon:'download'}};
const JM_SYNC_LABELS = {running:'同步中',cancelling:'停止中',cancelled:'已停止',interrupted:'重启中断',completed:'已完成',partial:'部分失败',failed:'失败'};
const syncField = name => $('fav-sync-form').elements.namedItem(name);
const jmDate = value => value ? new Date(value*1000).toLocaleString() : '尚未运行';
const jmBytes = value => value<1024*1024 ? `${(value/1024).toFixed(1)} KiB` : `${(value/1024/1024).toFixed(1)} MiB`;

async function loadSyncSettings(source=favSync.source){
  const data=await api(`/api/${source}/sync/settings`);favSync[source].settings=data.settings;favSync[source].options=data;
  if(favSync.source===source)renderSyncSchedule();
  return data;
}
function renderSyncSchedule(){
  const source=favSync.source,s=favSync[source].settings,schedule=$('fav-sync-schedule');
  if(!s)return;
  $('fav-sync-scope').textContent=s.folder_ids?.length?`${s.folder_ids.length} 个指定收藏夹`:'全部收藏';
  $('fav-sync-dir').textContent=s.download_dir||'—';
  if(favSync[source].paused||sourcePlugin(source)?.enabled===false){schedule.replaceChildren(node('span',`${FAV_SYNC[source].label} 图源已关闭，定时同步暂停；重新启用后按原计划恢复。`,'schedule-state paused'));return;}
  const parts=[node('span',s.enabled?`定时同步 · ${favSync[source].options?.schedule||s.cron}`:'定时同步关闭','schedule-state'+(s.enabled?' on':''))];
  if(s.enabled&&favSync[source].nextRun)parts.push(node('span',`下次 ${jmDate(favSync[source].nextRun)}`));
  schedule.replaceChildren(...parts);
}
// Favorites cards share selection and the cover-corner "同步本书" control across sources.
function syncableCard(item,source,view,onSelect){
  const box=card(item,{showSource:false}),label=node('label',undefined,'jm-check select-mark'),check=node('input'),id=String(item.id);
  check.type='checkbox';check.setAttribute('aria-label',`选择 ${item.title}`);
  const sync=()=>{box.classList.toggle('selected',check.checked);};
  check.onchange=()=>{if(check.checked)view.selected.add(id);else view.selected.delete(id);sync();onSelect();};
  label.title='选择同步';label.append(check);
  // Reused cards re-read the selection on every render.
  box.syncSelection=()=>{check.checked=view.selected.has(id);sync();};
  box.classList.add('card-fixed');
  // Sync floats on the cover's top-left corner (the select mark owns top-right), so the text block keeps a fixed height.
  const syncOne=iconBtn('download','同步本书',()=>startFavSync(source,[id]),'card-sync');
  syncOne.removeAttribute('title');syncOne.dataset.tipText='同步本书';syncOne.setAttribute('aria-label',`同步《${item.title||'未命名'}》`);
  // Right-click offers the same actions as the cover controls.
  box.addEventListener('contextmenu',event=>{
    if(event.target.closest?.('.author-link'))return;event.preventDefault();
    dropdown.open({point:{x:event.clientX,y:event.clientY},label:`《${item.title||'未命名'}》操作`,items:[
      {label:'同步本书',icon:svgIcon('download'),act:()=>syncOne.click()},
      {label:check.checked?'取消选择':'选择',icon:svgIcon('check'),act:()=>{check.checked=!check.checked;check.onchange();}},
    ],onPick:entry=>entry.act()});
  });
  // Local state is a quiet hint next to the authors, revealed on hover or focus rather than drawn on the cover.
  const local=favSync[source]?.local?.[id];
  if(local&&local.status!=='missing'){
    const meta=LOCAL_STATUS[local.status],hint=node('span',undefined,`card-local ${meta.tone}`);
    hint.innerHTML=svgIcon(meta.icon);hint.append(node('span',meta.label));
    hint.dataset.tipText=local.status==='error'?`本地检查异常：${local.message||'原因未记录'}`:`本地 ${local.images} 张 · ${local.path}`;
    const authors=box.querySelector?.('.comic-authors');
    if(authors){box.classList.add('has-local');authors.append(hint);}else box.append(hint);
  }
  box.append(label,syncOne);return box;
}
// Local state for the cards on the current page; failures leave cards without badges.
async function loadLocalStatus(source,ids){
  if(!ids.length)return false;
  try{
    const data=await api(`/api/${source}/sync/local-status?`+new URLSearchParams({ids:ids.join(',')}),{cache:'no-store'});
    const next={...favSync[source].local};for(const id of ids)delete next[id];Object.assign(next,data.items);
    const changed=JSON.stringify(ids.map(id=>favSync[source].local[id]||null))!==JSON.stringify(ids.map(id=>next[id]||null));
    favSync[source].local=next;return changed;
  }catch{return false;}
}
// Toolbar filter by local state: "downloaded" keeps verified copies; "missing" keeps everything else, including comics flagged as broken.
function matchesLocalFilter(source,id,filter){if(!filter)return true;const downloaded=favSync[source]?.local?.[id]?.status==='downloaded';return filter==='downloaded'?downloaded:!downloaded;}
// With a local-state filter the whole collection is served from the favorites catalog recorded on the server;
// JM custom folders keep filtering the current upstream page.
function favCatalogMode(source,view){return Boolean($(`${source}-favorites-local`).value)&&(!FAV_SYNC[source].folders||view.folder==='0');}
async function loadFavCatalog(source,view,request,reload){
  const data=await api(`/api/${source}/sync/catalog?`+new URLSearchParams({local:$(`${source}-favorites-local`).value,page:view.page}),{cache:'no-store'});
  if(request!==view.request)return false;
  const ids=new Set(data.items.map(item=>String(item.id))),known=favSync[source].local;
  for(const id of view.selected)if(!ids.has(id))view.selected.delete(id);
  for(const id of ids)delete known[id];Object.assign(known,data.local);
  view.items=data.items;view.hasNext=data.has_next;view.catalog=data;
  $(`${source}-favorites-count`).textContent=`${data.total} 本`;
  $(`${source}-favorites-page`).textContent=`第 ${data.page} / ${Math.max(1,Math.ceil(data.total/data.page_size))} 页`;
  $(`${source}-favorites-prev`).disabled=view.page<=1;$(`${source}-favorites-next`).disabled=!data.has_next;
  $(`${source}-favorites-pager`).hidden=!data.total;
  clearTimeout(view.catalogPoll);
  if(data.refresh.active)view.catalogPoll=setTimeout(()=>{if(state.tab===FAV_SYNC[source].tab&&favCatalogMode(source,view))run(reload);},1500);
  return true;
}
async function refreshFavCatalog(source,view,reload){
  await api(`/api/${source}/sync/catalog/refresh?`+new URLSearchParams({sort:view[FAV_SYNC[source].sortKey]}),{method:'POST'});
  await reload();
}
const catalogTime=value=>value?new Date(value.replace(' ','T')+'Z').toLocaleString():'';
function catalogIcon(name,className){const n=node('span',undefined,'fav-catalog-icon'+(className?' '+className:''));n.innerHTML=svgIcon(name);return n;}
function catalogStat(label,value,icon,tone,active){
  const n=node('span',undefined,`fav-catalog-stat ${tone}${active?' active':''}`);
  n.innerHTML=svgIcon(icon);n.append(node('span',label),node('b',String(value)));return n;
}
// 本地记录摘要：计数在左、当前筛选项高亮；排序、更新时间与「更新收藏列表」靠右。
function renderFavCatalogNotice(source,view,reload){
  const box=$(`${source}-favorites-catalog`),filter=$(`${source}-favorites-local`).value;
  delete box.dataset.state;
  if(!filter){box.replaceChildren();return;}
  if(!favCatalogMode(source,view)){box.dataset.state='note';box.replaceChildren(catalogIcon('alert'),node('span','下载状态筛选在自建收藏夹中仅作用于当前页。'));return;}
  const data=view.catalog;if(!data){box.replaceChildren();return;}
  const refresh=data.refresh,counts=data.counts;
  if(refresh.active){
    box.dataset.state='busy';
    const parts=[catalogIcon('refresh-cw','spin'),node('span','正在更新收藏列表')];
    if(refresh.total_pages){
      const bar=node('span',undefined,'fav-catalog-progress'),fill=node('span');
      fill.style.width=`${Math.min(100,refresh.pages/refresh.total_pages*100)}%`;bar.append(fill);
      parts.push(bar,node('span',`第 ${refresh.pages} / ${refresh.total_pages} 页`,'fav-catalog-meta'));
    }
    box.replaceChildren(...parts);return;
  }
  const end=node('span',undefined,'fav-catalog-end'),parts=[];
  if(data.complete){
    parts.push(catalogStat('本地记录',counts.all,'bookmark','all',false),
      ...['downloaded','missing'].map(key=>catalogStat(LOCAL_STATUS[key].label,counts[key],LOCAL_STATUS[key].icon,LOCAL_STATUS[key].tone,filter===key)));
    const meta=node('span',`按${FAV_SYNC[source].sorts[data.sort]||'记录'}排列 · 更新于 ${timeAgo(data.updated_at)}`,'fav-catalog-meta');
    meta.dataset.tipText=`按本地记录的 ${counts.all} 本收藏筛选，更新于 ${catalogTime(data.updated_at)}`;
    end.append(meta);
  }else{
    box.dataset.state='warn';
    parts.push(catalogIcon('alert'),node('span',`本地尚未记录完整收藏列表，当前按已记录的 ${counts.all} 本筛选。`));
  }
  end.append(labelBtn('refresh-cw','更新收藏列表',()=>refreshFavCatalog(source,view,reload),'small'));
  parts.push(end);
  if(refresh.error)parts.push(node('span',`上次更新失败：${refresh.error}`,'error fav-catalog-error'));
  box.replaceChildren(...parts);
}
function favCatalogEmpty(source,view){
  const label=$(`${source}-favorites-local`).value==='downloaded'?'已下载':'未下载';
  return emptyState(`没有${label}的漫画`,view.catalog.complete?'按本地记录的全部收藏筛选。':'本地尚未记录完整收藏列表，更新收藏列表后重新筛选。',null,'bookmark');
}
function localStatusSignature(source,id){const local=favSync[source]?.local?.[id];return local?[local.status,local.images,local.message,local.path]:null;}
function syncSelectionBar(source,view){
  const selected=$(`${source}-sync-selected`);selected.hidden=!view.selected.size;selected.textContent=`同步所选（${view.selected.size}）`;selected.disabled=favSync[source].active;
  $(`${source}-favorites-books`).classList?.toggle('selecting',view.selected.size>0);
  // 有选中项时按钮变为 X，点击清空选择；无选中项时为「选择本页」。
  const toggle=$(`${source}-select-page`),selecting=view.selected.size>0,label=selecting?'取消选择':'选择本页';
  toggle.disabled=!selecting&&!view.items.length;toggle.title=label;toggle.setAttribute('aria-label',label);toggle.setAttribute('aria-pressed',String(selecting));
}
function toggleSyncPage(view,ids,render){
  if(view.selected.size)view.selected.clear();else for(const id of ids)view.selected.add(id);
  render();
}
async function startFavSync(source,albumIds){
  const s=(await loadSyncSettings(source)).settings;
  if(!albumIds&&!await ask({title:'同步收藏到服务器',message:`范围：${s.folder_ids?.length?'设置中指定的收藏夹':'全部收藏'}。\n目标：${s.download_dir}\n已完成图片会校验并跳过。此操作不修改远端收藏，不删除本地文件。`,ok:'开始同步'}))return;
  await api(`/api/${source}/sync/run`,write('POST',albumIds?{album_ids:albumIds}:{}));
  toast(`${FAV_SYNC[source].label} 收藏同步已启动`);await loadSyncStatus(source);
}
async function repairFavLocal(source=favSync.source,albumIds){
  const s=(await loadSyncSettings(source)).settings,count=albumIds?.length||favSync[source].counts?.repairable||0;
  const backup=`${s.download_dir.replace(/[\\/]$/,'')}/.comic-api-repair/`;
  if(!await ask({title:'修复异常漫画',message:`范围：本地检查标记为异常的 ${count} 本漫画。\n目录：${s.download_dir}\n标题相同、仅「第N話」序号与当前章节顺序不同的已有章节目录唯一时改名对上；逐页核对已有图片：可正常解码的图片保留不动，缺失页从图源补下载；损坏、重复或多余的图片移入 ${backup} 备份目录，不删除任何文件。修复完成后重新校验并登记为已下载。`,ok:'开始修复'}))return;
  await api(`/api/${source}/sync/repair`,write('POST',albumIds?{album_ids:albumIds}:{}));
  toast('异常漫画修复已启动');await loadSyncStatus(source);
}
async function checkFavLocal(source=favSync.source){
  const s=(await loadSyncSettings(source)).settings;
  const naming=s.directory_rule==='Bd_Aauthoroname_Pindextitle'?'按“【作者】原标题 / 第N話 章节标题”规则匹配':'按漫画 ID 或完整标题匹配';
  if(!await ask({title:'检查本地漫画',message:`范围：${s.folder_ids?.length?'设置中指定的收藏夹':'全部收藏'}。\n目录：${s.download_dir}\n${naming}，核对章节、页码并解码验证图片，建立本地 SHA-256 校验记录。检查需要读取远端目录信息，但不下载、修改或删除图片；不进行模糊标题匹配。`,ok:'开始检查'}))return;
  await api(`/api/${source}/sync/check-local`,{method:'POST'});
  toast('本地漫画检查已启动');await loadSyncStatus(source);
}
function jmRunCard(item){
  const checking=item.mode==='check_local',repairing=item.mode==='repair',live=item.status==='running'||item.status==='cancelling';
  const detailFailure=checking&&item.status==='failed'?item.errors?.find(error=>error.stage==='matching'):null;
  const otherErrors=(item.errors||[]).filter(error=>error!==detailFailure);
  const tone=item.status==='completed'?'ok':['failed','partial'].includes(item.status)?'danger':live?'info':'muted';
  const box=node('article',undefined,'jm-run'),head=node('div',undefined,'jm-run-heading'),kind=node('div',undefined,'run-kind');
  kind.append(node('strong',checking?'本地漫画检查':repairing?'异常漫画修复':item.automatic?'定时同步':'手动同步'),node('span',checking&&item.status==='running'?'检查中':repairing&&item.status==='running'?'修复中':JM_SYNC_LABELS[item.status]||item.status,`status-pill ${tone}`));
  head.append(kind,node('span',`${jmDate(item.started_at)}${item.finished_at?' → '+jmDate(item.finished_at):''}`,'run-time'));
  box.setAttribute('data-tone',tone);box.append(head);
  if(live){
    const progress=node('div',undefined,'run-progress'),label=node('div',undefined,'run-progress-label');
    label.append(node('span',item.stage==='scanning'?'正在扫描收藏分页…':item.stage==='matching'?`正在读取漫画详情并核对重名目录：${item.title||''}`:`${item.title||''} / ${item.chapter||''}${item.pages?` · 本章第 ${item.page} / ${item.pages} 张`:''}`,'run-current'));
    if(item.albums_total)label.append(node('span',`${Math.min(100,Math.round(item.albums_done/item.albums_total*100))}%`,'run-percent'));
    progress.append(label);
    if(item.albums_total){const bar=node('div',undefined,'bar'),fill=node('i');fill.style.width=`${Math.min(100,Math.round(item.albums_done/item.albums_total*100))}%`;bar.append(fill);progress.append(bar);}
    box.append(progress);
  }
  const stats=node('div',undefined,'run-stats');
  if(checking){
    stats.append(
      jmStat(`${item.albums_done} / ${item.albums_total}`,'漫画已检查'),
      jmStat(`${item.local_albums} 本`,`已下载 · ${item.local_images} 张`,item.local_albums?'ok':''),
      jmStat(`${item.local_missing} 本`,'未匹配'),
      jmStat(`${item.failed} 本`,'异常',item.failed?'danger':''),
    );
  }else if(repairing){
    stats.append(
      jmStat(`${item.albums_done} / ${item.albums_total}`,'漫画已处理'),
      jmStat(`${item.repaired_albums||0} 本`,'已修复',item.repaired_albums?'ok':''),
      jmStat(`${item.downloaded} 张`,`补下载 · 移入备份 ${item.moved_images||0} 张`),
      jmStat(`${item.failed} 本`,'仍异常',item.failed?'danger':''),
    );
  }else{
    stats.append(
      jmStat(`${item.albums_done} / ${item.albums_total}`,'漫画已处理'),
      jmStat(`${item.downloaded} 张`,'新下载',item.downloaded?'ok':''),
      jmStat(`${item.skipped} 张`,'校验跳过'),
      jmStat(`${item.failed} 项`,'失败',item.failed?'danger':''),
    );
  }
  box.append(stats);
  const meta=node('div',undefined,'run-meta');
  if(!checking){
    const elapsed=Math.max(1,(item.finished_at||Date.now()/1000)-item.started_at);
    meta.append(node('span',`累计接收 ${jmBytes(item.received_bytes)}`),node('span',`本次平均 ${(item.received_bytes/1024/elapsed).toFixed(1)} KiB/s`));
  }
  if(item.settings?.download_dir)meta.append(node('span',`保存目录：${item.settings.download_dir}`,'jm-path'));
  if(repairing&&item.renamed_chapters)meta.append(node('span',`章节目录改名 ${item.renamed_chapters} 个`));
  if(repairing&&item.moved_images)meta.append(node('span',`备份目录：${item.backup_dir}`,'jm-path'));
  if(meta.children.length)box.append(meta);
  if(checking&&item.local_results?.length){
    const details=node('details');details.append(node('summary',`已下载漫画（保留前 ${item.local_results.length} 本）`));
    for(const book of item.local_results)details.append(node('p',`${book.title} [${book.id}] · ${book.images} 张 · ${book.path}`,'jm-path'));
    box.append(details);
  }
  if(repairing&&item.renames?.length){
    const details=node('details');details.append(node('summary',`章节目录改名（保留前 ${item.renames.length} 项）`));
    for(const entry of item.renames)details.append(node('p',`${entry.title}：${entry.from} → ${entry.to}`,'jm-path'));
    box.append(details);
  }
  if(detailFailure){
    const notice=node('div',undefined,'jm-detail-failure');notice.setAttribute('role','note');
    notice.setAttribute('aria-label','漫画详情预读取失败说明');
    notice.append(node('strong','详情预读取失败，本轮检查已中止'));
    notice.append(node('p',`失败漫画：${detailFailure.title||'标题不可用'} · ID：${detailFailure.album_id}`));
    notice.append(node('p',`原因：${detailFailure.message}`,'error'));
    notice.append(node('p','本轮尚未开始本地图片校验，也未新增下载登记。此错误不代表本地图片损坏，已有文件和此前下载登记保持不变，无需删除本地文件。'));
    notice.append(node('p','对应收藏恢复访问后可重新检查，或在“同步设置 → 同步范围”中选择不包含该漫画的收藏夹后重试。'));
    notice.append(node('p','调整同步范围前，定时同步需关闭并保存，避免保存范围时立即触发下载。','muted'));
    box.append(notice);
  }else if(item.error)box.append(node('p',item.error,'error run-error'));
  if(otherErrors.length){
    const details=node('details','','run-errors');details.append(node('summary',`失败明细（保留前 ${otherErrors.length} 项）`));
    for(const error of otherErrors)details.append(node('p',`${error.title}：${error.message}`,'error'));
    box.append(details);
  }
  return box;
}
function jmStat(value,label,tone){
  const box=node('div',undefined,'run-stat'+(tone?` ${tone}`:''));box.append(node('strong',String(value)),node('span',label));return box;
}
function jmSyncEmpty(){
  const box=node('div',undefined,'sync-empty');box.innerHTML=svgIcon('inbox');
  const text=node('div');text.append(node('strong','尚无同步记录'),node('span','同步文件保存在服务器目录。'));box.append(text);return box;
}
function syncProgressLabel(data){
  const current=data.current,checking=current?.mode==='check_local';
  const status=current?.status==='cancelling'?'停止中':checking?'检查中':current?.mode==='repair'?'修复中':'同步中';
  const percent=data.active&&current?.albums_total?` ${Math.min(100,Math.round(current.albums_done/current.albums_total*100))}%`:'';
  return {status,percent};
}
async function loadSyncStatus(source=favSync.source){
  const data=await api(`/api/${source}/sync`),s=favSync[source],finished=s.active&&!data.active;
  s.active=data.active;s.nextRun=data.next_run_at;s.paused=data.paused;s.counts=data.local_counts||{};
  // A finished check, repair or sync changes per-comic local state; refresh the visible cards once.
  if(finished)FAV_SYNC[source].onLocalChange?.();
  const {status,percent}=syncProgressLabel(data),mode=data.active?'running':data.paused?'paused':'idle';
  // The page-header trigger mirrors the running task so progress stays visible with the dialog closed.
  const trigger=$(`${source}-sync-open`);trigger.dataset.state=mode;
  $(`${source}-sync-open-label`).textContent=data.active?`${status}${percent}`:'本地同步';
  trigger.setAttribute('aria-label',data.active?`本地同步：${status}${percent}`:'本地同步');
  FAV_SYNC[source].onStatus?.();
  if(favSync.source===source)renderSyncDialog(data,status,mode);
  return data;
}
function renderSyncDialog(data,status,mode){
  const view=favSync.view,latest=data.active?data.current:data.history[0];
  const signature=JSON.stringify(latest||null);
  if(signature!==view.status){view.status=signature;$('fav-sync-status').replaceChildren(latest?jmRunCard(latest):jmSyncEmpty());}
  const history=data.history.filter(item=>item.id!==latest?.id),historySignature=JSON.stringify(history);
  if(historySignature!==view.history){view.history=historySignature;$('fav-sync-history-list').replaceChildren(...history.map(jmRunCard));}
  $('fav-sync-history').hidden=!history.length;$('fav-sync-history-count').textContent=history.length||'';
  // While a task runs, its stop control replaces the start controls.
  $('fav-sync-run').hidden=$('fav-sync-check-local').hidden=data.active;
  $('fav-sync-run').disabled=$('fav-sync-check-local').disabled=data.active||data.paused;
  const broken=data.local_counts?.repairable||0,repair=$('fav-sync-repair');
  repair.hidden=data.active||!broken;repair.disabled=data.active||data.paused;
  repair.lastElementChild.textContent=`修复异常漫画（${broken}）`;
  $('fav-sync-cancel').hidden=!data.active;$('fav-sync-cancel').disabled=data.current?.status==='cancelling';
  const chip=$('fav-sync-chip');chip.hidden=!data.active;chip.lastElementChild.textContent=status;
  $('fav-sync-dialog').dataset.state=mode;
  $('fav-sync-save').disabled=$('fav-cron-open').disabled=data.active;
  renderSyncSchedule();
}
function openFavSync(source){
  if(favSync.source!==source){
    favSync.source=source;favSync.view={};
    $('fav-sync-status').replaceChildren();$('fav-sync-history-list').replaceChildren();$('fav-sync-history').hidden=true;
    $('fav-sync-schedule').replaceChildren();$('fav-sync-scope').textContent=$('fav-sync-dir').textContent='—';
  }
  $('fav-sync-panel-title').textContent=`${FAV_SYNC[source].label} 本地同步`;
  $('fav-sync-dialog').showModal();
  run(()=>Promise.all([loadSyncSettings(source),loadSyncStatus(source)]));
}
async function openSyncSettings(){
  const source=favSync.source,config=FAV_SYNC[source];
  await loadSyncSettings(source);await loadSyncStatus(source);
  const s=favSync[source].settings,o=favSync[source].options;
  $('fav-sync-settings-title').textContent=`${config.label} 收藏同步设置`;
  $('fav-sync-roots-env').textContent=o.roots_env;
  options(syncField('directory_rule'),o.directory_rules,s.directory_rule);
  for(const element of $('fav-sync-settings').querySelectorAll('[data-source]'))element.hidden=element.dataset.source!==source;
  for(const [name,value] of Object.entries(s)){
    const field=syncField(name);if(!field||name==='folder_ids')continue;
    if(field.type==='checkbox')field.checked=value;else field.value=value;
  }
  $('fav-cron-summary').textContent=`${o.schedule} · ${s.cron}`;
  $('fav-sync-settings-message').textContent=favSync[source].active?'同步进行中，停止后才能修改设置。':'';
  $('fav-sync-folders').replaceChildren();
  if(config.folders){
    const folders=new Map(jm.folders.filter(f=>f.id!=='0').map(f=>[f.id,f]));
    for(const id of s.folder_ids)if(!folders.has(id))folders.set(id,{id,name:id==='0'?'全部收藏':`收藏夹 ${id}（尚未加载）`});
    $('fav-sync-folders').replaceChildren(...[...folders.values()].map(folder=>{
      const label=node('label',undefined,'jm-check folder-chip'),input=node('input');input.type='checkbox';input.name='folder_ids';input.value=folder.id;input.checked=s.folder_ids.includes(folder.id);
      label.append(input,node('span',folder.name));return label;
    }));
    if(!folders.size)$('fav-sync-folders').append(node('span','没有已加载的自建收藏夹；当前范围为全部收藏。','muted'));
  }
  updateSyncPreview();$('fav-sync-settings').showModal();
}
function updateSyncPreview(){
  const rule=syncField('directory_rule').value,format=syncField('image_format').value,root=syncField('download_dir').value.replaceAll('\\','/').replace(/\/$/,'');
  const [comic,chapter]=FAV_SYNC[favSync.source].ids;
  const paths={
    Bd_Aauthoroname_Pindextitle:'【作者】原标题/第1話 章节标题',
    id:`${comic}/${chapter}`,
    title:`漫画标题 [${comic}]/001 - 章节标题 [${chapter}]`,
    title_plain:'漫画标题/第1話 章节标题',
    author:`作者/漫画标题 [${comic}]/001 - 章节标题 [${chapter}]`,
  };
  const path=paths[rule]||paths.title;
  const extension=format==='original'?'原格式':format==='jpeg'?'jpg':format;
  $('fav-directory-preview').textContent=`目录示例：${root}/${path}/00001.${extension}`;
  syncField('quality').disabled=!['jpeg','webp'].includes(format);
}
async function browseSyncDirectory(path=''){
  const view=favSync.view,request=(view.directoryRequest||0)+1;view.directoryRequest=request;
  $('fav-directory-select').disabled=true;$('fav-directory-parent').disabled=true;
  $('fav-directory-list').replaceChildren();$('fav-directory-message').textContent='正在读取目录…';
  try{
    const data=await api(`/api/${favSync.source}/sync/directories?`+new URLSearchParams({path}));if(request!==view.directoryRequest)return;
    view.directory=data.path;
    $('fav-directory-roots').replaceChildren(...data.roots.map(root=>{const control=button(root,()=>browseSyncDirectory(root),'small root-chip');control.classList.toggle('active',data.path===root||data.path.startsWith(root.replace(/[\\/]$/,'')+'/'));return control;}));
    $('fav-directory-current').textContent=data.path;
    $('fav-directory-parent').disabled=!data.parent;$('fav-directory-parent').onclick=()=>run(()=>browseSyncDirectory(data.parent));
    $('fav-directory-select').disabled=false;
    $('fav-directory-list').replaceChildren(...data.directories.map(dir=>{const row=labelBtn('folder',dir.name,()=>browseSyncDirectory(dir.path),'link-row');row.insertAdjacentHTML('beforeend',svgIcon('chevron-right'));return row;}));
    $('fav-directory-message').textContent=data.truncated?'目录较多，仅显示前 500 项。':data.directories.length?'':'当前目录没有可选子目录；不存在的目录会在同步时创建。';
  }catch(error){if(request===view.directoryRequest)$('fav-directory-message').textContent=error.message;}
}
for(const source of Object.keys(FAV_SYNC))$(`${source}-sync-open`).onclick=()=>openFavSync(source);
$('fav-sync-run').onclick=()=>run(()=>startFavSync(favSync.source),$('fav-sync-run'));
$('fav-sync-check-local').onclick=()=>run(()=>checkFavLocal(favSync.source),$('fav-sync-check-local'));
$('fav-sync-repair').onclick=()=>run(()=>repairFavLocal(favSync.source),$('fav-sync-repair'));
$('fav-sync-cancel').onclick=()=>run(async()=>{const source=favSync.source;await api(`/api/${source}/sync/cancel`,{method:'POST'});await loadSyncStatus(source);},$('fav-sync-cancel'));
$('fav-sync-settings-open').onclick=()=>run(openSyncSettings,$('fav-sync-settings-open'));
$('fav-sync-form').onsubmit=event=>{
  event.preventDefault();
  const source=favSync.source,values={};
  for(const key of ['enabled','add_to_library'])values[key]=syncField(key).checked;
  for(const key of ['speed_limit_kbps','quality','retries'])values[key]=Number(syncField(key).value);
  for(const key of ['cron','download_dir','image_format','directory_rule'])values[key]=syncField(key).value;
  if(FAV_SYNC[source].folders)values.folder_ids=[...$('fav-sync-folders').querySelectorAll('input:checked')].map(input=>input.value);
  run(async()=>{const data=await api(`/api/${source}/sync/settings`,write('PUT',values));favSync[source].settings=data.settings;favSync[source].options=data;$('fav-sync-settings').close();toast('同步设置已保存');await loadSyncStatus(source);},event.submitter);
};
for(const name of ['download_dir','directory_rule','image_format'])syncField(name).addEventListener('input',updateSyncPreview);
$('fav-cron-open').onclick=event=>cronEditor.open(event.currentTarget,syncField('cron').value,({expression,description})=>{syncField('cron').value=expression;$('fav-cron-summary').textContent=`${description} · ${expression}`;});
$('fav-directory-open').onclick=()=>run(async()=>{
  $('fav-directory-dialog').showModal();
  $('fav-directory-roots').replaceChildren(...(favSync[favSync.source].options?.roots||[]).map(root=>button(root,()=>browseSyncDirectory(root),'small root-chip')));
  await browseSyncDirectory(syncField('download_dir').value);
});
$('fav-directory-select').onclick=()=>{syncField('download_dir').value=favSync.view.directory;updateSyncPreview();$('fav-directory-dialog').close();};
setInterval(()=>{
  if(document.hidden)return;
  const sources=new Set($('fav-sync-dialog').open?[favSync.source]:[]);
  for(const [source,config] of Object.entries(FAV_SYNC))if(state.tab===config.tab)sources.add(source);
  for(const source of sources){
    const s=favSync[source];if(s.polling)continue;s.polling=true;
    loadSyncStatus(source).catch(error=>{if(favSync.source===source)$('fav-sync-schedule').textContent=`同步状态读取失败：${error.message}`;}).finally(()=>s.polling=false);
  }
},2500);
