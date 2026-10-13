'use strict';

const siteSettings = {auth:null, request:0, sourcesRequest:0, session:0, open:false, loading:false, saving:false, stale:false, refreshAfterSave:false};

function validSiteAuth(data){
  return data&&typeof data.enabled==='boolean'&&typeof data.username==='string'
    &&typeof data.password_configured==='boolean'&&typeof data.revision==='string'&&data.revision.length>0;
}
function siteAuthMessage(text,tone=''){
  const message=$('site-auth-message');message.textContent=text;message.className='status'+(tone?' '+tone:'');
}
function syncSiteAuthControls(){
  const auth=siteSettings.auth,busy=siteSettings.loading||siteSettings.saving;
  $('site-auth-fields').disabled=busy||!auth;
  $('site-auth-save').disabled=busy||!auth||siteSettings.stale;
  $('site-auth-reload').disabled=busy;
  const toggle=$('site-auth-toggle');toggle.disabled=busy||!auth||siteSettings.stale;
  toggle.setAttribute('aria-checked',String(auth?.enabled===true));
  toggle.classList.toggle('on',auth?.enabled===true);
  toggle.innerHTML=svgIcon(auth?.enabled?'toggle-right':'toggle-left');
  toggle.append(node('span',auth?(auth.enabled?'已开启':'已关闭'):'状态未读取'));
  $('site-auth-form').setAttribute('aria-busy',String(busy));
}
function applySiteAuth(data){
  siteSettings.auth=data;siteSettings.stale=false;
  $('site-auth-username').value=data.username;
  $('site-auth-password').value='';$('site-auth-confirm').value='';
  $('site-auth-password').placeholder=data.password_configured?'留空保留现有密码':'尚未设置密码';
  $('site-auth-password-note').textContent=data.password_configured?'密码已配置，不回显原密码。留空时保留现有密码。':'密码尚未配置，开启鉴权前需要设置用户名和密码。';
  $('site-auth-storage').textContent=data.storage==='database'?'当前使用服务器保存的配置，重启后保留，优先于环境变量。':'当前使用部署默认配置；保存后由服务器配置接管。';
  syncSiteAuthControls();
}
function siteAuthDirty(){
  return !!siteSettings.auth&&($('site-auth-username').value!==siteSettings.auth.username
    ||!!$('site-auth-password').value||!!$('site-auth-confirm').value);
}
async function loadSiteAuth(){
  if(siteSettings.saving){siteSettings.refreshAfterSave=true;syncSiteAuthControls();siteAuthMessage('正在等待保存完成…');return;}
  const request=++siteSettings.request;siteSettings.loading=true;
  syncSiteAuthControls();siteAuthMessage('正在读取鉴权设置…');
  try{
    const data=await api('/api/settings/auth',{cache:'no-store'});
    if(request!==siteSettings.request)return;
    if(!validSiteAuth(data))throw new Error('服务器未返回有效鉴权设置');
    applySiteAuth(data);siteAuthMessage('');
  }catch(error){
    if(request!==siteSettings.request)return;
    siteSettings.auth=null;
    siteAuthMessage(error.message,'error');
  }finally{
    if(request===siteSettings.request){siteSettings.loading=false;syncSiteAuthControls();}
  }
}
async function saveSiteAuth(enabled){
  const auth=siteSettings.auth,session=siteSettings.session;
  if(!auth||siteSettings.loading||siteSettings.saving||siteSettings.stale)return;
  const username=$('site-auth-username').value,password=$('site-auth-password').value;
  if(password!==$('site-auth-confirm').value){siteAuthMessage('两次输入的新密码不一致。','error');return;}
  if(enabled&&(!username.trim()||(!password&&!auth.password_configured))){siteAuthMessage('启用鉴权需要用户名和密码。','error');return;}
  siteSettings.saving=true;syncSiteAuthControls();
  try{
    if(auth.enabled&&!enabled&&!await ask({title:'关闭站点鉴权',message:'关闭后，所有能连接本站的访问者均可操作图源账号、书库和下载。已保存的用户名和密码保留。',ok:'关闭鉴权',danger:true}))return;
    if(session!==siteSettings.session)return;
    siteAuthMessage('正在保存鉴权设置…');
    const values={enabled,username,revision:auth.revision};if(password)values.password=password;
    const data=await api('/api/settings/auth',write('PUT',values));
    if(!data.success||!validSiteAuth(data.settings)||typeof data.reauthenticate!=='boolean')throw new Error('服务器未返回有效保存结果，重新读取后可确认状态。');
    if(session===siteSettings.session){
      applySiteAuth(data.settings);
      siteAuthMessage(data.reauthenticate?'设置已保存，正在重新认证…':'设置已保存，后续请求立即生效。');
    }
    if(data.reauthenticate)location.reload();
  }catch(error){
    if(session===siteSettings.session){
      if(error.code==='auth_settings_changed')siteSettings.stale=true;
      siteAuthMessage(error.message,'error');
    }
  }finally{
    siteSettings.saving=false;syncSiteAuthControls();
    if(siteSettings.refreshAfterSave){siteSettings.refreshAfterSave=false;if(siteSettings.open)run(loadSiteAuth);}
  }
}
async function refreshSettingsSources(){
  const request=++siteSettings.sourcesRequest;
  const control=$('settings-sources-refresh'),message=$('settings-sources-message');control.disabled=true;message.textContent='正在读取图源…';
  try{await loadSources();if(request===siteSettings.sourcesRequest){message.textContent='';runHealthChecks(false);}}
  catch(error){if(request===siteSettings.sourcesRequest)message.textContent=error.message;}
  finally{if(request===siteSettings.sourcesRequest)control.disabled=false;}
}
function loadSiteSettings(){return Promise.all([loadSiteAuth(),refreshSettingsSources()]);}
function leaveSiteSettings(){
  ++siteSettings.session;++siteSettings.request;++siteSettings.sourcesRequest;
  siteSettings.loading=false;siteSettings.auth=null;siteSettings.stale=false;siteSettings.refreshAfterSave=false;
  $('site-auth-username').value='';$('site-auth-password').value='';$('site-auth-confirm').value='';
  siteAuthMessage('');syncSiteAuthControls();
}
function syncSiteSettingsDialog(){
  document.body.classList.toggle('site-settings-open',siteSettings.open);
  document.querySelectorAll('[data-settings-open]').forEach(control=>control.setAttribute('aria-expanded',String(siteSettings.open)));
}
function openSiteSettings(){
  const dialog=$('settings-dialog');if(dialog.open)return;
  if(siteSettings.open)closeSiteSettings();
  ++siteSettings.session;siteSettings.open=true;
  dialog.showModal();syncSiteSettingsDialog();
  return loadSiteSettings();
}
function closeSiteSettings(){
  const dialog=$('settings-dialog');if(!dialog.open&&!siteSettings.open)return;
  siteSettings.open=false;
  for(const id of ['ask-dialog','credentials-dialog','source-settings-dialog'])if($(id).open)$(id).close();
  if(dialog.open)dialog.close();
  leaveSiteSettings();syncSiteSettingsDialog();
}
// Two panes share one dialog; switching keeps both forms mounted so drafts and scroll survive.
function showSettingsPane(name){
  document.querySelectorAll('[data-settings-pane]').forEach(tab=>{
    const on=tab.dataset.settingsPane===name;tab.setAttribute('aria-selected',String(on));tab.tabIndex=on?0:-1;
    $(tab.getAttribute('aria-controls')).hidden=!on;
  });
}
function initSiteSettings(){
  const tabs=[...document.querySelectorAll('[data-settings-pane]')];
  tabs.forEach((tab,index)=>{
    tab.onclick=()=>showSettingsPane(tab.dataset.settingsPane);
    tab.onkeydown=event=>{
      const step=event.key==='ArrowRight'||event.key==='ArrowDown'?1:event.key==='ArrowLeft'||event.key==='ArrowUp'?-1:0;if(!step)return;
      event.preventDefault();const next=tabs[(index+step+tabs.length)%tabs.length];showSettingsPane(next.dataset.settingsPane);next.focus();
    };
  });
  document.querySelectorAll('[data-settings-open]').forEach(control=>control.onclick=()=>run(openSiteSettings));
  const dialog=$('settings-dialog');
  dialog.addEventListener('close',()=>{if(!dialog.open)closeSiteSettings();});
  dialog.addEventListener('cancel',event=>{event.preventDefault();closeSiteSettings();});
  $('site-auth-form').onsubmit=event=>{event.preventDefault();saveSiteAuth(siteSettings.auth?.enabled);};
  $('site-auth-toggle').onclick=()=>saveSiteAuth(!siteSettings.auth?.enabled);
  $('site-auth-reload').onclick=()=>run(async()=>{
    const session=siteSettings.session;
    if(siteAuthDirty()&&!await ask({title:'重新读取鉴权设置',message:'未保存的用户名和密码修改将被清除。',ok:'重新读取'}))return;
    if(session===siteSettings.session)await loadSiteAuth();
  });
  $('settings-sources-refresh').onclick=refreshSettingsSources;
  window.addEventListener('pagehide',()=>{$('site-auth-password').value='';$('site-auth-confirm').value='';});
}
