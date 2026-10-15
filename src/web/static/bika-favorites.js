'use strict';
const bikaFavorites = {page:1, sort:'dd', items:[], hasNext:false, request:0, selected:new Set()};

async function loadBikaFavoritesView(){
  const plugin=sourcePlugin('bika'),canRead=plugin?.enabled!==false&&plugin?.capabilities.includes('favorites')&&(plugin.account?.authenticated||plugin.account?.configured);
  await Promise.all([canRead?loadSyncSettings('bika'):null,canRead?loadSyncStatus('bika'):null,loadBikaFavorites()]);
}
// Reloading the page already shown (tab re-entry, refresh) keeps its cards and only patches what changed remotely.
async function loadBikaFavorites(){
  const view=bikaFavorites,request=++view.request,box=$('bika-favorites-books'),key=`${view.page}|${view.sort}`,plugin=sourcePlugin('bika');
  const canRead=plugin?.enabled!==false&&plugin?.capabilities.includes('favorites')&&(plugin.account?.authenticated||plugin.account?.configured);
  const keep=canRead&&view.loadedKey===key&&view.items.length>0;
  if(!keep){
    view.loadedKey='';view.items=[];view.hasNext=false;box.keyedCards=null;view.selected.clear();syncBikaSelection();
    $('bika-favorites-message').replaceChildren();$('bika-favorites-pager').hidden=true;$('bika-favorites-count').textContent='';
  }
  if(plugin?.enabled===false){box.replaceChildren(emptyState('哔咔图源已关闭','账号和收藏保留；图源管理中可重新启用。',null,'bookmark'));return;}
  if(!plugin?.capabilities.includes('favorites')||!(plugin.account?.authenticated||plugin.account?.configured)){
    box.replaceChildren(emptyState('哔咔收藏需要登录','账号凭证加密保存在当前服务器。',plugin?bindButton('bika'):null,'bookmark'));
    return;
  }
  if(!keep)box.replaceChildren(...skeletons(8));
  try{
    const data=await api('/api/bika/favorites?'+new URLSearchParams({page:view.page,sort:view.sort}),{cache:'no-store'});
    if(request!==view.request)return;
    if(keep){
      // Drop selections that vanished remotely; unchanged items keep their cards and loaded covers.
      const ids=new Set(data.items.map(item=>String(item.id)));
      for(const id of view.selected)if(!ids.has(id))view.selected.delete(id);
      $('bika-favorites-message').replaceChildren();
    }
    view.loadedKey=key;view.items=data.items;view.hasNext=data.has_next;
    $('bika-favorites-count').textContent=`${data.total} 本`;
    $('bika-favorites-page').textContent=`第 ${data.page} / ${Math.max(1,Math.ceil(data.total/data.page_size))} 页`;
    $('bika-favorites-prev').disabled=view.page<=1;$('bika-favorites-next').disabled=!data.has_next;
    $('bika-favorites-pager').hidden=!data.total;
    renderBikaFavorites();
    loadLocalStatus('bika',data.items.map(item=>String(item.id))).then(changed=>{if(changed&&request===view.request)renderBikaFavorites();});
  }catch(error){
    if(request!==view.request)return;
    view.loadedKey='';view.items=[];view.hasNext=false;box.keyedCards=null;view.selected.clear();syncBikaSelection();
    $('bika-favorites-pager').hidden=true;$('bika-favorites-count').textContent='';
    box.replaceChildren(emptyState('收藏加载失败',error.message,button('重新加载',loadBikaFavorites),'alert'));
    if(error.code==='login_required'||error.code==='login_failed')$('bika-favorites-message').append(bindButton('bika'));
  }
}
function refreshBikaAccountViews(){
  ++bikaFavorites.request;bikaFavorites.loadedKey='';bikaFavorites.items=[];bikaFavorites.hasNext=false;bikaFavorites.page=1;bikaFavorites.selected.clear();
  if(state.detail?.source==='bika'&&$('detail-dialog').open)renderDetail();
  if(state.tab==='bika-favorites')run(loadBikaFavoritesView);
}
function bikaCardSignature(item){return [localStatusSignature('bika',String(item.id)),item.title,coverKey(coverFor(item)),item.author,item.authors,(item.variants||[]).map(v=>[v.source,v.id,v.title,v.page_count]),readingLast(item)?.chapterName||''];}
function filteredBikaFavorites(){
  const keyword=$('bika-favorites-keyword').value.trim().toLocaleLowerCase();
  return bikaFavorites.items.filter(item=>!keyword||`${item.title} ${item.author||''}`.toLocaleLowerCase().includes(keyword));
}
function bikaCard(item){return syncableCard(item,'bika',bikaFavorites,syncBikaSelection);}
function renderBikaFavorites(){
  const items=filteredBikaFavorites(),box=$('bika-favorites-books');
  if(items.length)for(const el of renderKeyed(box,items,item=>String(item.id),bikaCardSignature,bikaCard))el.syncSelection?.();
  else{box.keyedCards=null;box.replaceChildren(emptyState(bikaFavorites.items.length?'本页没有匹配漫画':'收藏为空',bikaFavorites.items.length?'筛选仅作用于当前页。':'远端账号暂无收藏漫画。',null,'bookmark'));}
  syncBikaSelection();
}
function syncBikaSelection(){syncSelectionBar('bika',bikaFavorites);}
FAV_SYNC.bika.onStatus=syncBikaSelection;
$('bika-favorites-refresh').onclick=()=>run(loadBikaFavoritesView,$('bika-favorites-refresh'));
$('bika-favorites-filter').onsubmit=event=>{event.preventDefault();renderBikaFavorites();};
$('bika-favorites-keyword').oninput=renderBikaFavorites;
$('bika-favorites-sort').onchange=()=>{bikaFavorites.sort=$('bika-favorites-sort').value;bikaFavorites.page=1;run(loadBikaFavorites);};
$('bika-favorites-prev').onclick=()=>{if(bikaFavorites.page>1){bikaFavorites.page--;run(loadBikaFavorites);}};
$('bika-favorites-next').onclick=()=>{if(bikaFavorites.hasNext){bikaFavorites.page++;run(loadBikaFavorites);}};
$('bika-select-page').onclick=()=>toggleSyncPage(bikaFavorites,filteredBikaFavorites().map(item=>String(item.id)),renderBikaFavorites);
$('bika-sync-selected').onclick=()=>run(()=>startFavSync('bika',[...bikaFavorites.selected]),$('bika-sync-selected'));
FAV_SYNC.bika.onLocalChange=async()=>{if(bikaFavorites.items.length&&await loadLocalStatus('bika',bikaFavorites.items.map(item=>String(item.id))))renderBikaFavorites();};
