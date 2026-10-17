'use strict';

function comicAuthors(item){
  const unknown=new Set(['佚名','未知','未知作者','不详','不詳','作者不详','作者不詳','无','無','暂无','暫無','多人','多位作者','unknown','anonymous','n/a','-']);
  const read=values=>{
    if(typeof values==='string')values=unknown.has(values.trim().normalize('NFKC').toLowerCase())?[]:values.split(/[/／、;；|\n]+/);
    if(!Array.isArray(values))return [];
    const names=[],seen=new Set();
    for(const value of values){
      const raw=typeof value==='string'?value:value?.name;
      if(typeof raw!=='string'||!raw.trim())continue;
      const name=raw.trim();
      const key=name.normalize('NFKC').toLowerCase();
      if(unknown.has(key)||seen.has(key))continue;
      seen.add(key);names.push(name);
    }
    return names;
  };
  const authors=read(item.authors);
  return authors.length?authors:read(item.author);
}
function renderComicAuthors(item){
  const root=node('span',undefined,'comic-authors'),authors=comicAuthors(item);
  if(!authors.length){root.append(node('span','佚名'));return root;}
  for(const author of authors){
    const link=searchLink(author,'?q='+encodeURIComponent(author),()=>searchComicAuthor(author),'author-link');
    link.title=`搜索作者：${author}`;link.setAttribute('aria-label',link.title);root.append(link);
  }
  return root;
}
function searchComicAuthor(author){
  if($('reader-dialog').open)closeReader(true);
  ++state.detailRequest;if($('detail-dialog').open)$('detail-dialog').close();
  return startSearch(author,'',{mode:'keyword'});
}
