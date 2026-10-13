'use strict';
const bikaFavorites = {page:1, sort:'dd', items:[], hasNext:false, request:0};

async function loadBikaFavorites(){
  const view=bikaFavorites,request=++view.request,box=$('bika-favorites-books');view.items=[];view.hasNext=false;
  $('bika-favorites-message').replaceChildren();$('bika-favorites-pager').hidden=true;$('bika-favorites-count').textContent='';
  const plugin=sourcePlugin('bika');
  if(plugin?.enabled===false){box.replaceChildren(emptyState('哔咔图源已关闭','账号和收藏保留；图源管理中可重新启用。',null,'bookmark'));return;}
  if(!plugin?.capabilities.includes('favorites')||!(plugin.account?.authenticated||plugin.account?.configured)){
    box.replaceChildren(emptyState('哔咔收藏需要登录','账号凭证加密保存在当前服务器。',plugin?bindButton('bika'):null,'bookmark'));
    return;
  }
  box.replaceChildren(...skeletons(8));
  try{
    const data=await api('/api/bika/favorites?'+new URLSearchParams({page:view.page,sort:view.sort}),{cache:'no-store'});
    if(request!==view.request)return;
    view.items=data.items;view.hasNext=data.has_next;
    $('bika-favorites-count').textContent=`${data.total} 本`;
    $('bika-favorites-page').textContent=`第 ${data.page} / ${Math.max(1,Math.ceil(data.total/data.page_size))} 页`;
    $('bika-favorites-prev').disabled=view.page<=1;$('bika-favorites-next').disabled=!data.has_next;
    $('bika-favorites-pager').hidden=!data.total;
    renderBikaFavorites();
  }catch(error){
    if(request!==view.request)return;
    box.replaceChildren(emptyState('收藏加载失败',error.message,button('重新加载',loadBikaFavorites),'alert'));
    if(error.code==='login_required'||error.code==='login_failed')$('bika-favorites-message').append(bindButton('bika'));
  }
}
function refreshBikaAccountViews(){
  ++bikaFavorites.request;bikaFavorites.items=[];bikaFavorites.hasNext=false;bikaFavorites.page=1;
  if(state.detail?.source==='bika'&&$('detail-dialog').open)renderDetail();
  if(state.tab==='bika-favorites')run(loadBikaFavorites);
}
function renderBikaFavorites(){
  const keyword=$('bika-favorites-keyword').value.trim().toLocaleLowerCase();
  const items=bikaFavorites.items.filter(item=>!keyword||`${item.title} ${item.author||''}`.toLocaleLowerCase().includes(keyword));
  $('bika-favorites-books').replaceChildren(...items.map(item=>card(item,{showSource:false})));
  if(!items.length)$('bika-favorites-books').append(emptyState(bikaFavorites.items.length?'本页没有匹配漫画':'收藏为空',bikaFavorites.items.length?'筛选仅作用于当前页。':'远端账号暂无收藏漫画。',null,'bookmark'));
}
$('bika-favorites-refresh').onclick=()=>run(loadBikaFavorites,$('bika-favorites-refresh'));
$('bika-favorites-filter').onsubmit=event=>{event.preventDefault();renderBikaFavorites();};
$('bika-favorites-keyword').oninput=renderBikaFavorites;
$('bika-favorites-sort').onchange=()=>{bikaFavorites.sort=$('bika-favorites-sort').value;bikaFavorites.page=1;run(loadBikaFavorites);};
$('bika-favorites-prev').onclick=()=>{if(bikaFavorites.page>1){bikaFavorites.page--;run(loadBikaFavorites);}};
$('bika-favorites-next').onclick=()=>{if(bikaFavorites.hasNext){bikaFavorites.page++;run(loadBikaFavorites);}};
