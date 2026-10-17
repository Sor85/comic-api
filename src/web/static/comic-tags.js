'use strict';

function comicLabels(values){
  if(!Array.isArray(values))return [];
  const seen=new Set(),result=[];
  for(const item of values){
    const label=typeof item==='string'?item:item?.label;
    const value=typeof item==='string'?item:item?.value;
    if(typeof label!=='string'||typeof value!=='string'||!label.trim()||!value.trim()||seen.has(value.trim()))continue;
    seen.add(value.trim());result.push({label:label.trim(),value:value.trim()});
  }
  return result;
}
function searchKey(search){return JSON.stringify([search.keyword,search.mode||'keyword',search.page||1,search.deduplicate===true]);}
function searchParams(search){
  const params=new URLSearchParams({keyword:search.keyword,page:search.page||1});
  if(search.deduplicate===true)params.set('deduplicate','true');
  if(search.mode==='tag')params.set('mode','tag');
  if(search.source){
    params.set('source',search.source);
    if(search.sort)params.set('sort',search.sort);
    if(search.category)params.set('category',search.category);
    if(search.mode==='tag'&&search.tag_value)params.set('tag_value',search.tag_value);
  }
  return params;
}
function tagSearchHref(detail,tag){
  const params=searchParams({keyword:tag.label,source:detail.source,mode:'tag',tag_value:tag.value});
  params.set('q',params.get('keyword'));params.delete('keyword');
  return '?'+params;
}
function renderComicLabels(detail){
  const root=node('div',undefined,'comic-labels');
  const add=(title,labels,searchable)=>{
    if(!labels.length)return;
    const group=node('div',undefined,'comic-label-group'),list=node('div',undefined,'tags');
    group.append(node('span',title,'comic-label-title'),list);
    const entries=labels.map(entry=>searchable?searchLink(entry.label,tagSearchHref(detail,entry),()=>searchComicTag(detail,entry),'tag tag-link'):node('span',entry.label,'tag'));
    entries.forEach((entry,index)=>{entry.hidden=index>=12;list.append(entry);});
    if(entries.length>12){
      let expanded=false;
      const more=button(`展开全部（${entries.length}）`,()=>{expanded=!expanded;entries.forEach((entry,index)=>entry.hidden=!expanded&&index>=12);more.textContent=expanded?'收起标签':`展开全部（${entries.length}）`;},'link-btn');
      list.append(more);
    }
    root.append(group);
  };
  add('标签',comicLabels(detail.tags),(sourcePlugin(detail.source)?.capabilities||[]).includes('tag_search'));
  const categories=Array.isArray(detail.categories)?comicLabels(detail.categories):comicLabels(String(detail.category||'').split(/[·,，、/|]/).filter(Boolean));
  add('分类',categories,false);
  return root;
}
function searchComicTag(detail,tag){
  if($('reader-dialog').open)closeReader(true);
  ++state.detailRequest;if($('detail-dialog').open)$('detail-dialog').close();
  return startSearch(tag.label,detail.source,{mode:'tag',tag_value:tag.value});
}
