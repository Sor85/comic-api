'use strict';
const jm = {page:1, folder:'0', order:'mr', items:[], folders:[], selected:new Set(), request:0};
async function loadJmFavoritesView(){
  if(sourcePlugin('jm')?.enabled===false){await loadJmFavorites();return;}
  await Promise.all([loadSyncSettings('jm'),loadSyncStatus('jm'),loadJmFavorites()]);
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
function jmCard(item){return syncableCard(item,'jm',jm,syncJmSelection);}
function jmCardSignature(item){return [item.title,coverKey(coverFor(item)),item.author,item.authors,(item.variants||[]).map(v=>[v.source,v.id,v.title,v.page_count]),readingLast(item)?.chapterName||''];}
function renderJmBooks(){
  const items=filteredJmBooks(),box=$('jm-favorites-books');
  if(!items.length){
    box.keyedCards=null;
    box.replaceChildren(emptyState(jm.items.length?'本页没有匹配漫画':'收藏夹为空',jm.items.length?'筛选仅作用于当前页。':'远端账号在此收藏夹中暂无漫画。',null,'bookmark'));
  }else for(const el of renderKeyed(box,items,item=>String(item.id),jmCardSignature,jmCard))el.syncSelection?.();
  syncJmSelection();
}
function syncJmSelection(){syncSelectionBar('jm',jm);}
FAV_SYNC.jm.onStatus=syncJmSelection;
$('jm-favorites-refresh').onclick=()=>run(loadJmFavoritesView,$('jm-favorites-refresh'));
$('jm-favorites-filter').onsubmit=event=>{event.preventDefault();renderJmBooks();};
$('jm-favorites-keyword').oninput=renderJmBooks;
$('jm-favorites-folder').onchange=()=>{jm.folder=$('jm-favorites-folder').value;jm.page=1;run(loadJmFavorites);};
$('jm-favorites-order').onchange=()=>{jm.order=$('jm-favorites-order').value;jm.page=1;run(loadJmFavorites);};
$('jm-favorites-prev').onclick=()=>{if(jm.page>1){jm.page--;run(loadJmFavorites);}};
$('jm-favorites-next').onclick=()=>{if(jm.hasNext){jm.page++;run(loadJmFavorites);}};
$('jm-select-page').onclick=()=>toggleSyncPage(jm,filteredJmBooks().map(item=>String(item.id)),renderJmBooks);
$('jm-sync-selected').onclick=()=>run(()=>startFavSync('jm',[...jm.selected]),$('jm-sync-selected'));
