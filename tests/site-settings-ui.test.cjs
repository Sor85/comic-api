const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {test}=require('node:test');
const vm=require('node:vm');
const settings=readFileSync(join(__dirname,'../src/web/static/site-settings.js'),'utf8');
const app=readFileSync(join(__dirname,'../src/web/static/app.js'),'utf8');
const html=readFileSync(join(__dirname,'../src/web/templates/index.html'),'utf8');
const css=readFileSync(join(__dirname,'../src/web/static/app.css'),'utf8');
const plain=value=>JSON.parse(JSON.stringify(value));
const flush=()=>new Promise(resolve=>setImmediate(resolve));

class Element{
  constructor(){this.children=[];this.attributes={};this.value='';this.disabled=false;this.hidden=false;this.open=false;this.events={};this.modalShows=0;this.dataset={};this.className='';this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return (this.text||'')+this.children.map(child=>child.textContent).join('');}
  set innerHTML(value){this.markup=value;this.children=[];this.text='';}
  append(...children){this.children.push(...children);}
  setAttribute(name,value){this.attributes[name]=String(value);}
  addEventListener(name,handler){(this.events[name]||=[]).push(handler);}
  dispatch(name,event={}){for(const handler of this.events[name]||[])handler(event);}
  showModal(){this.open=true;this.modalShows++;}
  close(){if(!this.open)return;this.open=false;queueMicrotask(()=>this.dispatch('close'));}
  cancel(){let prevented=false;this.dispatch('cancel',{preventDefault(){prevented=true;}});if(!prevented)this.close();}
  getBoundingClientRect(){return {left:10,right:500,top:10,bottom:500};}
}
function harness(initial={}){
  const roots={},root=id=>roots[id]||(roots[id]=new Element());
  const calls=[],prompts=[],events={},counts={reload:0,sources:0,health:0};
  const openers=[new Element(),new Element()],closeButton=new Element();closeButton.dataset.close='settings-dialog';
  const document={body:new Element(),querySelectorAll:selector=>selector==='[data-settings-open]'?openers:selector==='[data-close]'?[closeButton]:selector==='dialog:not(#reader-dialog)'?['settings-dialog','source-settings-dialog','credentials-dialog','ask-dialog'].map(root):[]};
  let server={enabled:false,username:'',password_configured:false,revision:'first',storage:'environment',...initial},version=0;
  const context=vm.createContext({
    document,$:root,node:(_tag,text)=>{const el=new Element();if(text!==undefined)el.textContent=text;return el;},svgIcon:name=>`<svg data-icon="${name}"></svg>`,
    write:(method,data)=>({method,body:JSON.stringify(data)}),
    api:async(path,options={})=>{
      calls.push({path,options});if(!options.method)return {...server};
      const values=JSON.parse(options.body),previous=server;
      server={...server,enabled:values.enabled,username:values.username,password_configured:server.password_configured||!!values.password,revision:`revision-${++version}`,storage:'database'};
      return {success:true,settings:{...server},reauthenticate:server.enabled&&(!previous.enabled||previous.username!==server.username||!!values.password)};
    },
    ask:async options=>{prompts.push(options);return true;},run:async action=>action(),
    loadSources:async()=>{counts.sources++;},runHealthChecks:()=>{counts.health++;},
    window:{addEventListener:(name,handler)=>events[name]=handler},location:{reload:()=>counts.reload++},
  });
  vm.runInContext(settings,context);context.initSiteSettings();
  for(const prefix of ["document.querySelectorAll('[data-close]')","document.querySelectorAll('dialog:not(#reader-dialog)')"])
    vm.runInContext(app.split('\n').find(line=>line.startsWith(prefix)),context);
  const state=vm.runInContext('siteSettings',context);
  return {context,state,root,calls,prompts,counts,events,openers,closeButton,document,setServer:value=>server=value};
}

function fill(h,username,password,confirmation=password){
  h.root('site-auth-username').value=username;h.root('site-auth-password').value=password;h.root('site-auth-confirm').value=confirmation;
}

test('统一设置模态窗口包含图源列表、独立站点表单与桌面移动入口',()=>{
  assert.equal((html.match(/data-settings-open/g)||[]).length,2);
  assert.doesNotMatch(html,/data-tab="settings"|<section id="settings"/);
  const start=html.indexOf('<dialog id="settings-dialog"');
  assert.ok(start>html.indexOf('</main>'));
  const page=html.slice(start,html.indexOf('</dialog>',start));
  assert.match(page,/data-close="settings-dialog"/);
  assert.match(page,/id="source-list"/);assert.match(page,/id="site-auth-form"/);
  assert.equal((html.match(/id="source-list"/g)||[]).length,1);
  assert.doesNotMatch(html,/id="account-dialog"/);assert.doesNotMatch(app,/\$\('account-dialog'\)/);
  assert.match(html,/id="source-settings-dialog"/);assert.match(html,/id="login-form"/);
  assert.ok(html.indexOf('/static/site-settings.js')<html.indexOf('/static/app.js'));
  assert.match(css,/\.site-settings-dialog\{font-size:15px;line-height:1\.6;/);
  assert.doesNotMatch(css.slice(css.indexOf('/* unified settings */'),css.indexOf('/* source management */')),/cursor\s*:|::-webkit-scrollbar/);
  assert.doesNotMatch(settings,/localStorage|sessionStorage/);
});

test('首次读取显示真实开关与密码配置状态，不回填密码',async()=>{
  const h=harness();await h.context.loadSiteAuth();
  assert.equal(h.state.auth.enabled,false);
  assert.equal(h.root('site-auth-toggle').attributes['aria-checked'],'false');
  assert.equal(h.root('site-auth-fields').disabled,false);
  assert.match(h.root('site-auth-password-note').textContent,/尚未配置/);
  assert.equal(h.root('site-auth-password').value,'');
  assert.equal(h.calls[0].options.cache,'no-store');
});

test('可先保存凭据而不开启鉴权，成功后清空两次密码输入',async()=>{
  const h=harness();await h.context.loadSiteAuth();fill(h,'reader','private');
  await h.context.saveSiteAuth(false);
  const request=h.calls.find(call=>call.options.method==='PUT');
  assert.equal(request.path,'/api/settings/auth');
  assert.deepEqual(JSON.parse(request.options.body),{enabled:false,username:'reader',revision:'first',password:'private'});
  assert.equal(h.state.auth.password_configured,true);
  assert.equal(h.root('site-auth-password').value,'');assert.equal(h.root('site-auth-confirm').value,'');
  assert.equal(h.counts.reload,0);
  assert.doesNotMatch(JSON.stringify(h.state),/private/);
});

test('密码留空时只提交用户名和开关，不覆盖已保存密码',async()=>{
  const h=harness({enabled:true,username:'reader',password_configured:true});await h.context.loadSiteAuth();
  fill(h,'renamed','');await h.context.saveSiteAuth(true);
  const body=JSON.parse(h.calls.at(-1).options.body);
  assert.equal(body.username,'renamed');assert.equal('password' in body,false);
  assert.equal(h.counts.reload,1);
});

test('开启鉴权等待服务器确认，不提前翻转开关，并阻止重复保存',async()=>{
  const h=harness({username:'reader',password_configured:true});await h.context.loadSiteAuth();
  let resolve,requests=0;h.context.api=()=>{requests++;return new Promise(done=>resolve=done);};
  const saving=h.context.saveSiteAuth(true);
  assert.equal(h.root('site-auth-toggle').attributes['aria-checked'],'false');
  assert.equal(h.root('site-auth-fields').disabled,true);
  assert.equal(h.root('site-auth-toggle').disabled,true);
  await h.context.saveSiteAuth(true);assert.equal(requests,1);
  resolve({success:true,settings:{...plain(h.state.auth),enabled:true,revision:'new'},reauthenticate:true});await saving;
  assert.equal(h.root('site-auth-toggle').attributes['aria-checked'],'true');assert.equal(h.counts.reload,1);
});

test('首次开启可同时保存新凭据，缺失或两次密码不一致时不发请求',async()=>{
  const h=harness();await h.context.loadSiteAuth();
  await h.context.saveSiteAuth(true);assert.equal(h.calls.length,1);
  assert.match(h.root('site-auth-message').textContent,/需要用户名和密码/);
  fill(h,'reader','first','second');await h.context.saveSiteAuth(true);
  assert.equal(h.calls.length,1);assert.match(h.root('site-auth-message').textContent,/不一致/);
  fill(h,'reader','private');await h.context.saveSiteAuth(true);
  assert.equal(h.state.auth.enabled,true);assert.equal(h.counts.reload,1);
});

test('关闭鉴权需确认，取消不写入，关闭保留已配置凭据',async()=>{
  const h=harness({enabled:true,username:'reader',password_configured:true});await h.context.loadSiteAuth();
  h.context.ask=async()=>false;await h.context.saveSiteAuth(false);
  assert.equal(h.calls.length,1);assert.equal(h.state.auth.enabled,true);
  h.context.ask=async options=>{h.prompts.push(options);return true;};await h.context.saveSiteAuth(false);
  assert.equal(h.prompts[0].danger,true);assert.match(h.prompts[0].message,/所有能连接本站的访问者/);
  assert.equal(h.state.auth.enabled,false);assert.equal(h.state.auth.password_configured,true);assert.equal(h.counts.reload,0);
});

test('保存失败保留旧开关与输入，不假报成功或重新认证',async()=>{
  const h=harness();await h.context.loadSiteAuth();fill(h,'reader','private');
  h.context.api=async()=>{throw new Error('磁盘写入失败');};await h.context.saveSiteAuth(true);
  assert.equal(h.state.auth.enabled,false);assert.equal(h.root('site-auth-password').value,'private');
  assert.equal(h.root('site-auth-username').value,'reader');assert.equal(h.root('site-auth-save').disabled,false);
  assert.match(h.root('site-auth-message').textContent,/磁盘写入失败/);assert.equal(h.counts.reload,0);
});

test('旧版本保存冲突阻止继续覆盖，重新读取前提示丢弃未保存内容',async()=>{
  const h=harness();await h.context.loadSiteAuth();fill(h,'reader','private');
  h.context.api=async()=>{throw Object.assign(new Error('鉴权设置已变更'),{code:'auth_settings_changed'});};
  await h.context.saveSiteAuth(true);
  assert.equal(h.root('site-auth-save').disabled,true);assert.equal(h.root('site-auth-toggle').disabled,true);
  assert.equal(h.root('site-auth-reload').disabled,false);assert.equal(h.root('site-auth-password').value,'private');
  h.context.api=async()=>({enabled:false,username:'other',password_configured:true,revision:'newer',storage:'database'});
  await h.root('site-auth-reload').onclick();
  assert.match(h.prompts[0].message,/未保存/);assert.equal(h.state.stale,false);
  assert.equal(h.root('site-auth-username').value,'other');assert.equal(h.root('site-auth-password').value,'');
});

test('读取鉴权失败不显示默认关闭，图源区域仍独立可用',async()=>{
  const h=harness();h.context.api=async()=>{throw new Error('读取失败');};
  await h.context.loadSiteSettings();
  assert.equal(h.state.auth,null);assert.match(h.root('site-auth-toggle').textContent,/状态未读取/);
  assert.equal(h.root('site-auth-save').disabled,true);assert.equal(h.root('site-auth-fields').disabled,true);
  assert.equal(h.root('site-auth-reload').disabled,false);assert.match(h.root('site-auth-message').textContent,/读取失败/);
  assert.equal(h.counts.sources,1);assert.equal(h.counts.health,1);
});

test('站点认证失败不调用图源登录接口，表单保持不可写',async()=>{
  const h=harness();h.context.api=async()=>{throw Object.assign(new Error('站点认证失败'),{code:'auth_required'});};
  await h.context.loadSiteAuth();assert.match(h.root('site-auth-message').textContent,/站点认证失败/);
  assert.equal(h.root('site-auth-toggle').disabled,true);assert.equal(h.state.auth,null);
});

test('离开设置清除密码，迟到读取不会恢复旧字段，pagehide 也清除密码',async()=>{
  const h=harness();await h.context.loadSiteAuth();fill(h,'reader','private');
  let resolve;h.context.api=()=>new Promise(done=>resolve=done);
  const loading=h.context.loadSiteAuth();h.context.leaveSiteSettings();
  resolve({enabled:true,username:'stale',password_configured:true,revision:'old'});await loading;
  assert.equal(h.state.auth,null);assert.equal(h.root('site-auth-username').value,'');assert.equal(h.root('site-auth-password').value,'');
  fill(h,'reader','private');h.events.pagehide();
  assert.equal(h.root('site-auth-password').value,'');assert.equal(h.root('site-auth-confirm').value,'');
});

for(const method of ['button','backdrop','escape'])test(`设置通过 ${method} 关闭时统一清理密码、子窗口和滚动锁`,async()=>{
  const h=harness();await h.openers[0].onclick();fill(h,'reader','private');
  assert.equal(h.root('settings-dialog').open,true);assert.ok(h.document.body.classes.has('site-settings-open'));
  assert.equal(h.openers[1].attributes['aria-expanded'],'true');
  await h.openers[1].onclick();assert.equal(h.root('settings-dialog').modalShows,1);
  if(method==='button')h.closeButton.onclick();
  if(method==='backdrop'){
    const dialog=h.root('settings-dialog');dialog.dispatch('click',{target:dialog,clientX:100,clientY:100});assert.equal(dialog.open,true);
    dialog.dispatch('click',{target:dialog,clientX:0,clientY:0});
  }
  if(method==='escape')h.root('settings-dialog').cancel();
  await flush();
  assert.equal(h.root('settings-dialog').open,false);assert.equal(h.state.open,false);
  assert.equal(h.root('site-auth-password').value,'');assert.equal(h.root('site-auth-confirm').value,'');
  assert.equal(h.openers[0].attributes['aria-expanded'],'false');assert.ok(!h.document.body.classes.has('site-settings-open'));
});

test('嵌套图源与凭据窗口关闭只返回上层，不清除父设置表单',async()=>{
  const h=harness();await h.context.openSiteSettings();fill(h,'reader','draft');
  h.root('source-settings-dialog').showModal();h.root('credentials-dialog').showModal();
  h.root('credentials-dialog').cancel();await flush();
  assert.equal(h.root('source-settings-dialog').open,true);assert.equal(h.root('settings-dialog').open,true);
  assert.equal(h.root('site-auth-password').value,'draft');
  h.root('credentials-dialog').showModal();h.context.closeSiteSettings();await flush();
  assert.equal(h.root('credentials-dialog').open,false);assert.equal(h.root('source-settings-dialog').open,false);
  assert.equal(h.root('settings-dialog').open,false);assert.equal(h.root('site-auth-password').value,'');
});

test('关闭后立即重开不会被旧 close 事件清空新会话',async()=>{
  const h=harness();await h.context.openSiteSettings();fill(h,'reader','draft');
  h.closeButton.onclick();const reopening=h.context.openSiteSettings();
  assert.equal(h.root('site-auth-password').value,'');
  await reopening;await flush();
  assert.equal(h.state.open,true);assert.equal(h.root('settings-dialog').open,true);assert.ok(h.state.auth);
});

test('保存中关闭并重开时等待保存结束，再读取当前状态，不回填旧响应',async()=>{
  const h=harness();await h.context.openSiteSettings();fill(h,'reader','private');
  let resolvePut;h.context.api=(_path,options={})=>options.method?new Promise(resolve=>resolvePut=resolve):Promise.resolve({enabled:false,username:'latest',password_configured:true,revision:'latest',storage:'database'});
  const saving=h.context.saveSiteAuth(false);h.context.closeSiteSettings();await h.context.openSiteSettings();
  assert.equal(h.root('site-auth-fields').disabled,true);assert.match(h.root('site-auth-message').textContent,/等待保存/);
  resolvePut({success:true,settings:{enabled:false,username:'old-result',password_configured:true,revision:'old'},reauthenticate:false});
  await saving;await flush();
  assert.equal(h.root('site-auth-username').value,'latest');assert.equal(h.root('site-auth-password').value,'');
  assert.equal(h.root('site-auth-fields').disabled,false);
});

test('关闭窗口后的成功改密仍触发认证，但不恢复已关闭表单',async()=>{
  const h=harness();await h.context.openSiteSettings();fill(h,'reader','private');
  let resolve;h.context.api=()=>new Promise(done=>resolve=done);
  const saving=h.context.saveSiteAuth(true);h.context.closeSiteSettings();
  resolve({success:true,settings:{enabled:true,username:'reader',password_configured:true,revision:'new'},reauthenticate:true});
  await saving;await flush();
  assert.equal(h.state.auth,null);assert.equal(h.root('site-auth-username').value,'');assert.equal(h.counts.reload,1);
});

test('关闭鉴权确认尚未结束时关闭父窗，不发送迟到的保存请求',async()=>{
  const h=harness({enabled:true,username:'reader',password_configured:true});await h.context.openSiteSettings();
  let confirm;h.context.ask=()=>{h.root('ask-dialog').showModal();return new Promise(resolve=>confirm=resolve);};
  const saving=h.context.saveSiteAuth(false);h.context.closeSiteSettings();confirm(true);await saving;
  assert.equal(h.root('ask-dialog').open,false);assert.ok(!h.calls.some(call=>call.options.method==='PUT'));
});

test('旧图源读取完成不会提前结束新窗口的加载状态',async()=>{
  const h=harness();let resolveFirst,resolveSecond;
  h.context.loadSources=()=>new Promise(resolve=>resolveFirst=resolve);
  const first=h.context.openSiteSettings();h.context.closeSiteSettings();
  h.context.loadSources=()=>new Promise(resolve=>resolveSecond=resolve);
  const second=h.context.openSiteSettings();resolveFirst();await first;
  assert.equal(h.root('settings-sources-refresh').disabled,true);
  resolveSecond();await second;assert.equal(h.root('settings-sources-refresh').disabled,false);
});

function routeHarness(){
  const h=harness(),{root,context}=h,calls=[],pushes=[];
  const tabs=['browse','library','downloads','jm-favorites','bika-favorites'];
  const controls=tabs.map(tab=>{const el=new Element();el.dataset.tab=tab;return el;});
  const query=h.document.querySelectorAll;h.document.querySelectorAll=selector=>selector==='[data-tab]'?controls:query(selector);
  const state={tab:'browse',browseReady:true,view:'home',search:null,discover:{action:'latest'}};
  const location={...context.location,pathname:'/',search:''};
  const history={state:null,pushState(snap,_title,url){this.state=plain(snap);this.url=url;pushes.push({snap:this.state,url});},replaceState(snap,_title,url){this.state=plain(snap);this.url=url;},back(){calls.push('back');}};
  Object.assign(context,{state,location,history,URLSearchParams,
    sourcePlugin:id=>id?{id,enabled:true,capabilities:['favorites']}:undefined,
    renderContinue(){},loadLibrary:()=>calls.push('library'),loadTasks:()=>calls.push('downloads'),loadJmFavoritesView:()=>calls.push('jm'),loadBikaFavorites:()=>calls.push('bika'),
    renderQuick(){},renderRecent(){},syncSearchMode(){},readLocal:()=>null,normalizeSearch(){},
    setView:view=>{state.view=view;},showHome:()=>{state.view='home';state.search=null;},syncClear(){},
    loadSearch:()=>calls.push('search'),loadDiscover:()=>calls.push('discover'),loadShelves:()=>calls.push('shelves'),
    searchParams:query=>new URLSearchParams(query),BROWSE_ACTIONS:{latest:'最新更新'},BROWSE_ORDER:['latest'],
  });
  context.window.scrollY=320;context.window.scrollTo=({top})=>{context.window.scrollY=top;calls.push('scroll');};
  vm.runInContext(app.match(/^const TAB_LABELS = .*$/m)[0],context);
  vm.runInContext(app.slice(app.indexOf('function viewURL('),app.indexOf('function showHome(')),context);
  vm.runInContext(app.slice(app.indexOf('function showTab('),app.indexOf('function card(')),context);
  vm.runInContext(app.slice(app.indexOf('function initBrowse('),app.indexOf('function renderQuick(')),context);
  vm.runInContext(app.slice(app.indexOf('function goHome('),app.indexOf('function initBrowse(')),context);
  vm.runInContext(app.slice(app.indexOf('function restorePage('),app.indexOf("window.addEventListener('popstate'")),context);
  return {...h,siteState:h.state,state,location,history,controls,routeCalls:calls,pushes};
}

for(const tab of ['browse','library','downloads','jm-favorites','bika-favorites'])test(`从 ${tab} 打开和关闭设置不改变底层页面、URL、标题或滚动位置`,async()=>{
  const h=routeHarness();h.state.tab=tab;h.state.view='results';h.state.search={keyword:'test',source:'jm',page:3};
  for(const id of ['browse','library','downloads','jm-favorites','bika-favorites'])h.root(id).hidden=id!==tab;
  h.context.syncHistory(false);const before=plain(h.state),url=h.history.url,title=h.context.document.title;
  await h.openers[0].onclick();assert.equal(h.root('settings-dialog').open,true);
  assert.equal(h.root(tab).hidden,false);assert.deepEqual(plain(h.state),before);
  h.closeButton.onclick();await flush();
  assert.deepEqual(plain(h.state),before);assert.equal(h.history.url,url);assert.equal(h.context.document.title,title);
  assert.equal(h.context.window.scrollY,320);assert.equal(h.pushes.length,0);assert.deepEqual(h.routeCalls,[]);
});

test('旧设置深链接打开首页上的模态窗口，不再创建设置主页面',async()=>{
  const h=routeHarness();h.location.search='?tab=settings';h.context.initBrowse();await flush();
  assert.equal(h.state.tab,'browse');assert.equal(h.root('settings-dialog').open,true);assert.equal(h.history.url,'/');
  h.context.closeSiteSettings();assert.equal(h.state.tab,'browse');assert.equal(h.pushes.length,0);
});

test('从图源设置主动跳转收藏会关闭整组设置窗口，不覆盖目标页面',async()=>{
  const h=routeHarness();await h.context.openSiteSettings();h.root('source-settings-dialog').showModal();
  h.context.showTab('bika-favorites');await flush();
  assert.equal(h.root('settings-dialog').open,false);assert.equal(h.root('source-settings-dialog').open,false);
  assert.equal(h.state.tab,'bika-favorites');assert.equal(h.history.url,'?tab=bika-favorites');
});

test('浏览器导航关闭设置窗口并正常恢复原搜索快照',async()=>{
  const h=routeHarness();await h.context.openSiteSettings();
  h.context.restorePage({tab:'browse',view:'results',search:{keyword:'test',source:'jm'},discover:{action:'latest'}});await flush();
  assert.equal(h.root('settings-dialog').open,false);assert.equal(h.state.search.keyword,'test');assert.ok(h.routeCalls.includes('search'));
  assert.equal(h.pushes.length,0);
});

test('图源状态引起底层导航回退时保留设置窗口和未保存凭据',async()=>{
  const h=routeHarness();h.state.tab='jm-favorites';await h.context.openSiteSettings();fill(h,'reader','draft');
  h.context.showTab('browse',true,false);await flush();
  assert.equal(h.root('settings-dialog').open,true);assert.equal(h.root('site-auth-password').value,'draft');assert.equal(h.state.tab,'browse');
  assert.match(app,/if\(hidden&&state\.tab===tab\)showTab\('browse',true,false\)/);
});

test('从首页搜索后返回按钮回到首页历史记录',()=>{
  const h=routeHarness();h.context.syncHistory(false);
  h.state.search={keyword:'作者',source:''};h.context.openResults();
  assert.equal(h.history.state.fromHome,true);
  h.state.search.page=2;h.context.syncHistory(false);assert.equal(h.history.state.fromHome,true);
  h.context.goHome();assert.equal(h.routeCalls.at(-1),'back');
});

test('搜索后切换到其他页面再返回发现页，返回按钮回到首页而不是上一个页面',()=>{
  const h=routeHarness();h.context.syncHistory(false);
  h.state.search={keyword:'作者',source:''};h.context.openResults();
  h.context.showTab('downloads');h.context.showTab('browse');
  assert.equal(h.history.state.view,'results');assert.equal(h.history.state.fromHome,undefined);
  h.context.goHome();
  assert.ok(!h.routeCalls.includes('back'));assert.equal(h.state.view,'home');assert.equal(h.history.url,'/');
});
