'use strict';

// A single metadata request leaves capacity for reading; image bytes remain browser-lazy.
class ChapterPreviews {
  constructor({load, render, Observer=globalThis.IntersectionObserver, now=Date.now}) {
    this.load=load;this.render=render;this.Observer=Observer;this.now=now;
    this.cache=new Map();this.items=new Map();this.queue=[];
    this.generation=0;this.active=false;this.observer=null;
  }
  key(detail, chapter) {return JSON.stringify([detail.source,String(detail.id),String(chapter.id)]);}
  start(root) {
    this.stop();this.root=root;
    if(!this.Observer)return;
    const generation=this.generation;
    this.observer=new this.Observer(entries=>{
      if(generation!==this.generation)return;
      for(const entry of entries){
        const item=this.items.get(entry.target);if(!item)continue;
        item.visible=entry.isIntersecting;
        if(item.visible&&!item.queued){item.queued=true;this.queue.push(item);}
      }
      this.pump();
    },{root,rootMargin:'0px',threshold:0});
  }
  watch(element, detail, chapter) {
    if(!this.observer)return;
    const item={element,detail,chapter,key:this.key(detail,chapter),generation:this.generation,visible:false,queued:false};
    this.items.set(element,item);this.observer.observe(element);
  }
  stop() {
    ++this.generation;this.observer?.disconnect();this.observer=null;
    this.items.clear();this.queue=[];
    // Let an already issued request settle into the cache without updating the old view.
  }
  valid(item) {return item.generation===this.generation&&item.element.isConnected&&this.root?.open!==false;}
  remember(key, url) {
    this.cache.delete(key);this.cache.set(key,{url,expires:this.now()+(url?600000:30000)});
    while(this.cache.size>256)this.cache.delete(this.cache.keys().next().value);
  }
  failed(detail, chapter) {this.remember(this.key(detail,chapter),null);}
  pump() {
    if(this.active)return;
    while(this.queue.length){
      const item=this.queue.shift();item.queued=false;
      if(!this.valid(item)||!item.visible)continue;
      this.observer?.unobserve(item.element);this.items.delete(item.element);
      const cached=this.cache.get(item.key);
      if(cached&&cached.expires>this.now()){
        this.cache.delete(item.key);this.cache.set(item.key,cached);
        this.render(item.element,cached.url,item.detail,item.chapter);continue;
      }
      this.cache.delete(item.key);this.active=true;this.fetch(item);return;
    }
  }
  async fetch(item) {
    let url=null;
    try{
      const result=await this.load(item.detail,item.chapter);
      if(typeof result==='string'&&result)url=result;
    }catch{}
    this.remember(item.key,url);
    try{if(this.valid(item))this.render(item.element,url,item.detail,item.chapter);}
    finally{this.active=false;this.pump();}
  }
}
