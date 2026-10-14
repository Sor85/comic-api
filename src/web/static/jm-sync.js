'use strict';
const jm = {page:1, folder:'0', order:'mr', items:[], folders:[], selected:new Set(), request:0, settings:null, active:false};
const JM_SYNC_LABELS = {running:'同步中',cancelling:'停止中',cancelled:'已停止',interrupted:'重启中断',completed:'已完成',partial:'部分失败',failed:'失败'};
const jmField = name => $('jm-sync-form').elements.namedItem(name);
const jmDate = value => value ? new Date(value*1000).toLocaleString() : '尚未运行';
const jmBytes = value => value<1024*1024 ? `${(value/1024).toFixed(1)} KiB` : `${(value/1024/1024).toFixed(1)} MiB`;

async function loadJmFavoritesView(){
  if(sourcePlugin('jm')?.enabled===false){await loadJmFavorites();return;}
  await Promise.all([loadJmSettings(),loadJmStatus(),loadJmFavorites()]);
}
async function loadJmSettings(){
  const data=await api('/api/jm/sync/settings');jm.settings=data.settings;jm.options=data;renderJmSchedule();return data;
}
function renderJmSchedule(){
  if(!jm.settings)return;
  const s=jm.settings,schedule=$('jm-sync-schedule');
  $('jm-sync-scope').textContent=s.folder_ids.length?`${s.folder_ids.length} 个指定收藏夹`:'全部收藏';
  $('jm-sync-dir').textContent=s.download_dir||'—';
  if(jm.paused||sourcePlugin('jm')?.enabled===false){schedule.replaceChildren(node('span','JM 图源已关闭，定时同步暂停；重新启用后按原计划恢复。','schedule-state paused'));return;}
  const parts=[node('span',s.enabled?`定时同步 · 每 ${s.interval_minutes} 分钟`:'定时同步关闭','schedule-state'+(s.enabled?' on':''))];
  if(s.enabled&&jm.nextRun)parts.push(node('span',`下次 ${jmDate(jm.nextRun)}`));
  schedule.replaceChildren(...parts);
}
function jmCanRead(){const p=sourcePlugin('jm');return p?.enabled!==false&&p?.capabilities.includes('favorites')&&(p.account?.authenticated||p.account?.configured);}
// Reloading the page already shown (tab re-entry, refresh) keeps its cards and only patches what changed remotely.
async function loadJmFavorites(){
  const request=++jm.request,box=$('jm-favorites-books'),key=`${jm.page}|${jm.folder}|${jm.order}`;
  const keep=jm.loadedKey===key&&jm.items.length>0&&jmCanRead();
  if(!keep){
    jm.loadedKey='';box.keyedCards=null;jm.selected.clear();jm.items=[];syncJmSelection();
    $('jm-favorites-message').replaceChildren();$('jm-favorites-pager').hidden=true;$('jm-favorites-count').textContent='';
  }
  if(sourcePlugin('jm')?.enabled===false){box.replaceChildren(emptyState('JM 图源已关闭','账号、收藏和已完成文件保留；图源管理中可重新启用。',null,'bookmark'));return;}
  if(!jmCanRead()){
    box.replaceChildren(emptyState('禁漫收藏需要登录','账号凭证加密保存在当前服务器。',sourcePlugin('jm')?bindButton('jm'):null,'bookmark'));
    return;
  }
  if(!keep)box.replaceChildren(...skeletons(8));
  try{
    const data=await api('/api/jm/favorites?'+new URLSearchParams({page:jm.page,folder_id:jm.folder,order:jm.order}));
    if(request!==jm.request)return;
    if(keep){
      // Drop selections that vanished remotely; unchanged items keep their cards and loaded covers.
      const ids=new Set(data.items.map(item=>String(item.id)));
      for(const id of jm.selected)if(!ids.has(id))jm.selected.delete(id);
      $('jm-favorites-message').replaceChildren();
    }
    jm.loadedKey=key;jm.items=data.items;jm.folders=data.folders;jm.hasNext=data.has_next;
    options($('jm-favorites-folder'),data.folders.map(f=>({value:f.id,label:f.name})),jm.folder);
    $('jm-favorites-count').textContent=`${data.total} 本`;
    $('jm-favorites-page').textContent=`第 ${data.page} / ${Math.max(1,Math.ceil(data.total/data.page_size))} 页`;
    $('jm-favorites-prev').disabled=jm.page<=1;$('jm-favorites-next').disabled=!data.has_next;
    $('jm-favorites-pager').hidden=!data.total;
    renderJmBooks();
  }catch(error){
    if(request!==jm.request)return;
    jm.loadedKey='';jm.items=[];jm.selected.clear();syncJmSelection();box.keyedCards=null;
    box.replaceChildren(emptyState('收藏加载失败',error.message,button('重新加载',loadJmFavorites),'alert'));
    if(error.code==='login_required'||error.code==='login_failed')$('jm-favorites-message').append(bindButton('jm'));
    if(error.code==='folder_not_found')$('jm-favorites-message').append(button('返回全部收藏',()=>{jm.folder='0';jm.page=1;return loadJmFavorites();}));
  }
}
function filteredJmBooks(){
  const keyword=$('jm-favorites-keyword').value.trim().toLocaleLowerCase();
  return jm.items.filter(item=>!keyword||`${item.title} ${item.author||''}`.toLocaleLowerCase().includes(keyword));
}
function jmCard(item){
  const box=card(item,{showSource:false}),label=node('label',undefined,'jm-check select-mark'),check=node('input');
  check.type='checkbox';check.setAttribute('aria-label',`选择 ${item.title}`);
  const sync=()=>{box.classList.toggle('selected',check.checked);};
  check.onchange=()=>{if(check.checked)jm.selected.add(String(item.id));else jm.selected.delete(String(item.id));sync();syncJmSelection();};
  label.title='选择同步';label.append(check);
  // Reused cards re-read the selection on every render.
  box.syncSelection=()=>{check.checked=jm.selected.has(String(item.id));sync();};
  box.classList.add('card-fixed');
  // Sync floats on the cover's top-left corner (the select mark owns top-right), so the text block keeps a fixed height.
  const syncOne=iconBtn('download','同步本书',()=>startJmSync([String(item.id)]),'card-sync');
  syncOne.removeAttribute('title');syncOne.dataset.tipText='同步本书';syncOne.setAttribute('aria-label',`同步《${item.title||'未命名'}》`);
  // Right-click offers the same actions as the cover controls.
  box.addEventListener('contextmenu',event=>{
    if(event.target.closest?.('.author-link'))return;event.preventDefault();
    dropdown.open({point:{x:event.clientX,y:event.clientY},label:`《${item.title||'未命名'}》操作`,items:[
      {label:'同步本书',icon:svgIcon('download'),act:()=>syncOne.click()},
      {label:check.checked?'取消选择':'选择',icon:svgIcon('check'),act:()=>{check.checked=!check.checked;check.onchange();}},
    ],onPick:entry=>entry.act()});
  });
  box.append(label,syncOne);return box;
}
function jmCardSignature(item){return [item.title,coverKey(coverFor(item)),item.author,item.authors,(item.variants||[]).map(v=>[v.source,v.id,v.title,v.page_count]),readingLast(item)?.chapterName||''];}
function renderJmBooks(){
  const items=filteredJmBooks(),box=$('jm-favorites-books');
  if(!items.length){
    box.keyedCards=null;
    box.replaceChildren(emptyState(jm.items.length?'本页没有匹配漫画':'收藏夹为空',jm.items.length?'筛选仅作用于当前页。':'远端账号在此收藏夹中暂无漫画。',null,'bookmark'));
  }else for(const el of renderKeyed(box,items,item=>String(item.id),jmCardSignature,jmCard))el.syncSelection?.();
  syncJmSelection();
}
function syncJmSelection(){
  const selected=$('jm-sync-selected');selected.hidden=!jm.selected.size;selected.textContent=`同步所选（${jm.selected.size}）`;selected.disabled=jm.active;
  $('jm-favorites-books').classList?.toggle('selecting',jm.selected.size>0);
  $('jm-select-page').disabled=!jm.items.length;
}
async function startJmSync(albumIds){
  await loadJmSettings();
  if(!albumIds&&!await ask({title:'同步收藏到服务器',message:`范围：${jm.settings.folder_ids.length?'设置中指定的收藏夹':'全部收藏'}。\n目标：${jm.settings.download_dir}\n已完成图片会校验并跳过。此操作不修改远端收藏，不删除本地文件。`,ok:'开始同步'}))return;
  await api('/api/jm/sync/run',write('POST',albumIds?{album_ids:albumIds}:{}));
  toast('收藏同步已启动');await loadJmStatus();
}
async function checkJmLocal(){
  await loadJmSettings();
  const naming=jm.settings.directory_rule==='Bd_Aauthoroname_Pindextitle'?'按“【作者】原标题 / 第N話 章节标题”规则匹配':'按漫画 ID 或完整标题匹配';
  if(!await ask({title:'检查本地漫画',message:`范围：${jm.settings.folder_ids.length?'设置中指定的收藏夹':'全部收藏'}。\n目录：${jm.settings.download_dir}\n${naming}，核对章节、页码并解码验证图片，建立本地 SHA-256 校验记录。检查需要读取远端目录信息，但不下载、修改或删除图片；不进行模糊标题匹配。`,ok:'开始检查'}))return;
  await api('/api/jm/sync/check-local',{method:'POST'});
  toast('本地漫画检查已启动');await loadJmStatus();
}
function jmRunCard(item){
  const checking=item.mode==='check_local',live=item.status==='running'||item.status==='cancelling';
  const detailFailure=checking&&item.status==='failed'?item.errors?.find(error=>error.stage==='matching'):null;
  const otherErrors=(item.errors||[]).filter(error=>error!==detailFailure);
  const tone=item.status==='completed'?'ok':['failed','partial'].includes(item.status)?'danger':live?'info':'muted';
  const box=node('article',undefined,'jm-run'),head=node('div',undefined,'jm-run-heading'),kind=node('div',undefined,'run-kind');
  kind.append(node('strong',checking?'本地漫画检查':item.automatic?'定时同步':'手动同步'),node('span',checking&&item.status==='running'?'检查中':JM_SYNC_LABELS[item.status]||item.status,`status-pill ${tone}`));
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
  if(meta.children.length)box.append(meta);
  if(checking&&item.local_results?.length){
    const details=node('details');details.append(node('summary',`已下载漫画（保留前 ${item.local_results.length} 本）`));
    for(const book of item.local_results)details.append(node('p',`${book.title} [${book.id}] · ${book.images} 张 · ${book.path}`,'jm-path'));
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
async function loadJmStatus(){
  const data=await api('/api/jm/sync');jm.active=data.active;jm.nextRun=data.next_run_at;jm.paused=data.paused;
  const latest=data.active?data.current:data.history[0];
  const signature=JSON.stringify(latest||null);
  if(signature!==jm.statusSignature){
    jm.statusSignature=signature;
    $('jm-sync-status').replaceChildren(latest?jmRunCard(latest):jmSyncEmpty());
  }
  const history=data.history.filter(item=>item.id!==latest?.id),historySignature=JSON.stringify(history);
  if(historySignature!==jm.historySignature){jm.historySignature=historySignature;$('jm-sync-history-list').replaceChildren(...history.map(jmRunCard));}
  $('jm-sync-history').hidden=!history.length;$('jm-sync-history-count').textContent=history.length||'';
  // While a task runs, its stop control replaces the start controls.
  $('jm-sync-run').hidden=$('jm-sync-check-local').hidden=data.active;
  $('jm-sync-run').disabled=$('jm-sync-check-local').disabled=data.active||data.paused;
  $('jm-sync-cancel').hidden=!data.active;$('jm-sync-cancel').disabled=data.current?.status==='cancelling';
  const chip=$('jm-sync-chip'),checking=data.current?.mode==='check_local';
  chip.hidden=!data.active;chip.lastElementChild.textContent=data.current?.status==='cancelling'?'停止中':checking?'检查中':'同步中';
  const mode=data.active?'running':data.paused?'paused':'idle',current=data.current,trigger=$('jm-sync-open');
  $('jm-sync-dialog').dataset.state=trigger.dataset.state=mode;
  // The page-header trigger mirrors the running task so progress stays visible with the dialog closed.
  const percent=data.active&&current?.albums_total?` ${Math.min(100,Math.round(current.albums_done/current.albums_total*100))}%`:'';
  $('jm-sync-open-label').textContent=data.active?`${chip.lastElementChild.textContent}${percent}`:'本地同步';
  trigger.setAttribute('aria-label',data.active?`本地同步：${chip.lastElementChild.textContent}${percent}`:'本地同步');
  $('jm-sync-save').disabled=data.active;
  syncJmSelection();renderJmSchedule();
}
async function openJmSettings(){
  await loadJmSettings();await loadJmStatus();
  const s=jm.settings;
  for(const [name,value] of Object.entries(s)){
    const field=jmField(name);if(!field)continue;
    if(field.type==='checkbox')field.checked=value;else field.value=value;
  }
  $('jm-sync-settings-message').textContent=jm.active?'同步进行中，停止后才能修改设置。':'';
  const folders=new Map(jm.folders.filter(f=>f.id!=='0').map(f=>[f.id,f]));
  for(const id of s.folder_ids)if(!folders.has(id))folders.set(id,{id,name:id==='0'?'全部收藏':`收藏夹 ${id}（尚未加载）`});
  $('jm-sync-folders').replaceChildren(...[...folders.values()].map(folder=>{
    const label=node('label',undefined,'jm-check folder-chip'),input=node('input');input.type='checkbox';input.name='folder_ids';input.value=folder.id;input.checked=s.folder_ids.includes(folder.id);
    label.append(input,node('span',folder.name));return label;
  }));
  if(!folders.size)$('jm-sync-folders').append(node('span','没有已加载的自建收藏夹；当前范围为全部收藏。','muted'));
  updateJmPreview();$('jm-sync-settings').showModal();
}
function updateJmPreview(){
  const rule=jmField('directory_rule').value,format=jmField('image_format').value,root=jmField('download_dir').value.replaceAll('\\','/').replace(/\/$/,'');
  const paths={
    Bd_Aauthoroname_Pindextitle:'【作者】原标题/第1話 章节标题',
    id:'123456/123457',
    title:'漫画标题 [123456]/001 - 章节标题 [123457]',
    title_plain:'漫画标题/第1話 章节标题',
    author:'作者/漫画标题 [123456]/001 - 章节标题 [123457]',
  };
  const path=paths[rule]||paths.title;
  const extension=format==='original'?'原格式':format==='jpeg'?'jpg':format;
  $('jm-directory-preview').textContent=`目录示例：${root}/${path}/00001.${extension}`;
  jmField('quality').disabled=!['jpeg','webp'].includes(format);
}
async function browseJmDirectory(path=''){
  const request=(jm.directoryRequest||0)+1;jm.directoryRequest=request;
  $('jm-directory-select').disabled=true;$('jm-directory-parent').disabled=true;
  $('jm-directory-list').replaceChildren();$('jm-directory-message').textContent='正在读取目录…';
  try{
    const data=await api('/api/jm/sync/directories?'+new URLSearchParams({path}));if(request!==jm.directoryRequest)return;
    jm.directory=data.path;
    $('jm-directory-roots').replaceChildren(...data.roots.map(root=>{const control=button(root,()=>browseJmDirectory(root),'small root-chip');control.classList.toggle('active',data.path===root||data.path.startsWith(root.replace(/[\\/]$/,'')+'/'));return control;}));
    $('jm-directory-current').textContent=data.path;
    $('jm-directory-parent').disabled=!data.parent;$('jm-directory-parent').onclick=()=>run(()=>browseJmDirectory(data.parent));
    $('jm-directory-select').disabled=false;
    $('jm-directory-list').replaceChildren(...data.directories.map(dir=>{const row=labelBtn('folder',dir.name,()=>browseJmDirectory(dir.path),'link-row');row.insertAdjacentHTML('beforeend',svgIcon('chevron-right'));return row;}));
    $('jm-directory-message').textContent=data.truncated?'目录较多，仅显示前 500 项。':data.directories.length?'':'当前目录没有可选子目录；不存在的目录会在同步时创建。';
  }catch(error){if(request===jm.directoryRequest)$('jm-directory-message').textContent=error.message;}
}
$('jm-favorites-refresh').onclick=()=>run(loadJmFavoritesView,$('jm-favorites-refresh'));
$('jm-favorites-filter').onsubmit=event=>{event.preventDefault();renderJmBooks();};
$('jm-favorites-keyword').oninput=renderJmBooks;
$('jm-favorites-folder').onchange=()=>{jm.folder=$('jm-favorites-folder').value;jm.page=1;run(loadJmFavorites);};
$('jm-favorites-order').onchange=()=>{jm.order=$('jm-favorites-order').value;jm.page=1;run(loadJmFavorites);};
$('jm-favorites-prev').onclick=()=>{if(jm.page>1){jm.page--;run(loadJmFavorites);}};
$('jm-favorites-next').onclick=()=>{if(jm.hasNext){jm.page++;run(loadJmFavorites);}};
$('jm-select-page').onclick=()=>{const ids=filteredJmBooks().map(item=>String(item.id));const all=ids.every(id=>jm.selected.has(id));for(const id of ids){if(all)jm.selected.delete(id);else jm.selected.add(id);}renderJmBooks();};
$('jm-sync-selected').onclick=()=>run(()=>startJmSync([...jm.selected]),$('jm-sync-selected'));
$('jm-sync-run').onclick=()=>run(()=>startJmSync(),$('jm-sync-run'));
$('jm-sync-check-local').onclick=()=>run(checkJmLocal,$('jm-sync-check-local'));
$('jm-sync-cancel').onclick=()=>run(async()=>{await api('/api/jm/sync/cancel',{method:'POST'});await loadJmStatus();},$('jm-sync-cancel'));
$('jm-sync-open').onclick=()=>{
  $('jm-sync-dialog').showModal();
  run(()=>Promise.all([loadJmSettings(),loadJmStatus()]));
};
$('jm-sync-settings-open').onclick=()=>run(openJmSettings,$('jm-sync-settings-open'));
$('jm-sync-form').onsubmit=event=>{
  event.preventDefault();
  const values={};
  for(const key of ['enabled','add_to_library'])values[key]=jmField(key).checked;
  for(const key of ['interval_minutes','speed_limit_kbps','quality','retries'])values[key]=Number(jmField(key).value);
  for(const key of ['download_dir','image_format','directory_rule'])values[key]=jmField(key).value;
  values.folder_ids=[...$('jm-sync-folders').querySelectorAll('input:checked')].map(input=>input.value);
  run(async()=>{const data=await api('/api/jm/sync/settings',write('PUT',values));jm.settings=data.settings;$('jm-sync-settings').close();toast('同步设置已保存');await loadJmStatus();},event.submitter);
};
for(const name of ['download_dir','directory_rule','image_format'])jmField(name).addEventListener('input',updateJmPreview);
$('jm-directory-open').onclick=()=>run(async()=>{
  $('jm-directory-dialog').showModal();
  $('jm-directory-roots').replaceChildren(...(jm.options?.roots||[]).map(root=>button(root,()=>browseJmDirectory(root),'small root-chip')));
  await browseJmDirectory(jmField('download_dir').value);
});
$('jm-directory-select').onclick=()=>{jmField('download_dir').value=jm.directory;updateJmPreview();$('jm-directory-dialog').close();};
setInterval(()=>{
  if(state.tab!=='jm-favorites'||document.hidden||jm.polling)return;
  jm.polling=true;loadJmStatus().catch(error=>{$('jm-sync-schedule').textContent=`同步状态读取失败：${error.message}`;}).finally(()=>jm.polling=false);
},2500);
