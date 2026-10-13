'use strict';
const jm = {page:1, folder:'0', order:'mr', items:[], folders:[], selected:new Set(), request:0, settings:null, active:false};
const JM_SYNC_LABELS = {running:'同步中',cancelling:'停止中',cancelled:'已停止',interrupted:'重启中断',completed:'已完成',partial:'部分失败',failed:'失败'};
const jmField = name => $('jm-sync-form').elements.namedItem(name);
const jmDate = value => value ? new Date(value*1000).toLocaleString() : '尚未运行';
const jmBytes = value => value<1024*1024 ? `${(value/1024).toFixed(1)} KiB` : `${(value/1024/1024).toFixed(1)} MiB`;

async function loadJmFavoritesView(){
  if(sourcePlugin('jm')?.enabled===false){await loadJmFavorites();return;}
  await Promise.all([loadJmSettings(),loadJmStatus()]);
  await loadJmFavorites();
}
async function loadJmSettings(){
  const data=await api('/api/jm/sync/settings');jm.settings=data.settings;jm.options=data;renderJmSchedule();return data;
}
function renderJmSchedule(){
  if(!jm.settings)return;
  const s=jm.settings,scope=s.folder_ids.length?`${s.folder_ids.length} 个指定收藏夹`:'全部收藏';
  const next=jm.nextRun?` · 下次：${jmDate(jm.nextRun)}`:'';
  if(jm.paused||sourcePlugin('jm')?.enabled===false){$('jm-sync-schedule').textContent='JM 图源已关闭，定时同步暂停；重新启用后按原计划恢复。';return;}
  const schedule=$('jm-sync-schedule');schedule.replaceChildren();
  const status=node('span',s.enabled?`定时同步 · 每 ${s.interval_minutes} 分钟${next}`:'定时同步关闭','schedule-state'+(s.enabled?' on':''));
  schedule.append(status,node('span',scope),node('span',s.download_dir,'jm-path'));
}
function jmCanRead(){const p=sourcePlugin('jm');return p?.enabled!==false&&p?.capabilities.includes('favorites')&&(p.account?.authenticated||p.account?.configured);}
async function loadJmFavorites(){
  const request=++jm.request,box=$('jm-favorites-books');jm.selected.clear();jm.items=[];syncJmSelection();
  $('jm-favorites-message').replaceChildren();$('jm-favorites-pager').hidden=true;$('jm-favorites-count').textContent='';
  if(sourcePlugin('jm')?.enabled===false){box.replaceChildren(emptyState('JM 图源已关闭','账号、收藏和已完成文件保留；图源管理中可重新启用。',null,'bookmark'));return;}
  if(!jmCanRead()){
    box.replaceChildren(emptyState('禁漫收藏需要登录','账号凭证加密保存在当前服务器。',sourcePlugin('jm')?bindButton('jm'):null,'bookmark'));
    return;
  }
  box.replaceChildren(...skeletons(8));
  try{
    const data=await api('/api/jm/favorites?'+new URLSearchParams({page:jm.page,folder_id:jm.folder,order:jm.order}));
    if(request!==jm.request)return;
    jm.items=data.items;jm.folders=data.folders;jm.hasNext=data.has_next;
    options($('jm-favorites-folder'),data.folders.map(f=>({value:f.id,label:f.name})),jm.folder);
    $('jm-favorites-count').textContent=`${data.total} 本`;
    $('jm-favorites-page').textContent=`第 ${data.page} / ${Math.max(1,Math.ceil(data.total/data.page_size))} 页`;
    $('jm-favorites-prev').disabled=jm.page<=1;$('jm-favorites-next').disabled=!data.has_next;
    $('jm-favorites-pager').hidden=!data.total;
    renderJmBooks();
  }catch(error){
    if(request!==jm.request)return;
    box.replaceChildren(emptyState('收藏加载失败',error.message,button('重新加载',loadJmFavorites),'alert'));
    if(error.code==='login_required'||error.code==='login_failed')$('jm-favorites-message').append(bindButton('jm'));
    if(error.code==='folder_not_found')$('jm-favorites-message').append(button('返回全部收藏',()=>{jm.folder='0';jm.page=1;return loadJmFavorites();}));
  }
}
function filteredJmBooks(){
  const keyword=$('jm-favorites-keyword').value.trim().toLocaleLowerCase();
  return jm.items.filter(item=>!keyword||`${item.title} ${item.author||''}`.toLocaleLowerCase().includes(keyword));
}
function renderJmBooks(){
  const items=filteredJmBooks();
  $('jm-favorites-books').replaceChildren(...items.map(item=>{
    const box=card(item,{showSource:false}),label=node('label',undefined,'jm-check select-mark'),check=node('input');
    check.type='checkbox';check.checked=jm.selected.has(String(item.id));check.setAttribute('aria-label',`选择 ${item.title}`);
    const sync=()=>{box.classList.toggle('selected',check.checked);};
    check.onchange=()=>{if(check.checked)jm.selected.add(String(item.id));else jm.selected.delete(String(item.id));sync();syncJmSelection();};
    label.title='选择同步';label.append(check);sync();
    const actions=node('div',undefined,'card-actions');actions.append(labelBtn('download','同步本书',()=>startJmSync([String(item.id)]),'small ghost-pill'));
    box.append(label,actions);return box;
  }));
  if(!items.length)$('jm-favorites-books').append(emptyState(jm.items.length?'本页没有匹配漫画':'收藏夹为空',jm.items.length?'筛选仅作用于当前页。':'远端账号在此收藏夹中暂无漫画。',null,'bookmark'));
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
  const checking=item.mode==='check_local';
  const detailFailure=checking&&item.status==='failed'?item.errors?.find(error=>error.stage==='matching'):null;
  const otherErrors=(item.errors||[]).filter(error=>error!==detailFailure);
  const box=node('article',undefined,'jm-run'),head=node('div',undefined,'jm-run-heading');
  const tone=item.status==='completed'?'ok':['failed','partial'].includes(item.status)?'danger':item.status==='running'?'info':'muted';
  head.append(node('strong',checking?'本地漫画检查':item.automatic?'定时同步':'手动同步'),node('span',checking&&item.status==='running'?'检查中':JM_SYNC_LABELS[item.status]||item.status,`status-pill ${tone}`));
  box.append(head,node('p',`${jmDate(item.started_at)}${item.finished_at?' → '+jmDate(item.finished_at):''}`,'muted'));
  if(checking){
    box.append(node('p',`漫画已检查 ${item.albums_done} / ${item.albums_total} · 已下载 ${item.local_albums} 本（${item.local_images} 张） · 未匹配 ${item.local_missing} 本 · 异常 ${item.failed} 本`));
    if(item.local_results?.length){
      const details=node('details');details.append(node('summary',`已下载漫画（保留前 ${item.local_results.length} 本）`));
      for(const book of item.local_results)details.append(node('p',`${book.title} [${book.id}] · ${book.images} 张 · ${book.path}`,'jm-path'));
      box.append(details);
    }
  }else{
    box.append(node('p',`漫画已处理 ${item.albums_done} / ${item.albums_total} · 新下载 ${item.downloaded} 张 · 校验跳过 ${item.skipped} 张 · 失败 ${item.failed} 项`));
    const elapsed=Math.max(1,(item.finished_at||Date.now()/1000)-item.started_at);
    box.append(node('p',`累计接收 ${jmBytes(item.received_bytes)} · 本次平均 ${(item.received_bytes/1024/elapsed).toFixed(1)} KiB/s`,'muted'));
  }
  if(item.albums_total&&(item.status==='running'||item.status==='cancelling')){
    const bar=node('div',undefined,'bar'),fill=node('i');fill.style.width=`${Math.min(100,Math.round(item.albums_done/item.albums_total*100))}%`;bar.append(fill);box.append(bar);
  }
  if(item.status==='running'||item.status==='cancelling'){
    box.append(node('p',item.stage==='scanning'?'正在扫描收藏分页…':item.stage==='matching'?`正在读取漫画详情并核对重名目录：${item.title||''}`:`${item.title||''} / ${item.chapter||''}${item.pages?` · 本章第 ${item.page} / ${item.pages} 张`:''}`));
  }
  if(item.settings?.download_dir)box.append(node('p',`保存目录：${item.settings.download_dir}`,'jm-path'));
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
  }else if(item.error)box.append(node('p',item.error,'error'));
  if(otherErrors.length){
    const details=node('details');details.append(node('summary',`失败明细（保留前 ${otherErrors.length} 项）`));
    for(const error of otherErrors)details.append(node('p',`${error.title}：${error.message}`,'error'));
    box.append(details);
  }
  return box;
}
async function loadJmStatus(){
  const data=await api('/api/jm/sync');jm.active=data.active;jm.nextRun=data.next_run_at;jm.paused=data.paused;
  const latest=data.active?data.current:data.history[0];
  const signature=JSON.stringify(latest||null);
  if(signature!==jm.statusSignature){
    jm.statusSignature=signature;
    $('jm-sync-status').replaceChildren(latest?jmRunCard(latest):node('p','尚无同步记录。同步文件保存在服务器目录。','muted'));
  }
  const history=data.history.filter(item=>item.id!==latest?.id),historySignature=JSON.stringify(history);
  if(historySignature!==jm.historySignature){jm.historySignature=historySignature;$('jm-sync-history-list').replaceChildren(...history.map(jmRunCard));}
  $('jm-sync-history').hidden=!history.length;
  $('jm-sync-run').disabled=data.active||data.paused;$('jm-sync-check-local').disabled=data.active||data.paused;$('jm-sync-cancel').hidden=!data.active;$('jm-sync-cancel').disabled=data.current?.status==='cancelling';
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
