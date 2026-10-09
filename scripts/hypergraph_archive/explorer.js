/* The scene contains only worker-selected representations; the archive remains complete. */
const ArchivePresentation=(()=>{const status=value=>({SUPPORTED:'声明支持',CONTRADICTED:'声明反驳',UNKNOWN:'未判定',PROPOSED:'候选声明',CLUSTER:'聚合组',REVIEW_REQUIRED:'待复核',ARCHIVED_SOURCE:'已归档来源',ARCHIVED:'已归档',ARCHIVED_SNAPSHOT:'已归档快照',ARCHIVED_VERSION:'历史版本',COPIED_VERIFIED:'副本已核验',VERIFIED_WITH_SOURCE_BOUNDARIES:'来源核验有边界',EXCEEDS_8MIB:'超出旧导入范围',SOURCE_HASH_DIFFERS:'来源已变化',TENSOR_OR_TRANSPORT_ARCHIVE_NOT_IMPORTED:'归档未导入',UNREVIEWED:'未复核',INDEX_ONLY:'仅索引'}[value]||'未判定');
const shortLabel=value=>{const chars=Array.from(String(value));return chars.length<=28?chars.join(''):chars.slice(0,27).join('')+'…';};
function labelIds(points,level,rect){const candidates=points.filter(p=>p.type==='cluster'&&(!rect||p.x>=rect[0]&&p.x<=rect[2]&&p.y>=rect[1]&&p.y<=rect[3])).sort((a,b)=>Number(!!b.primary)-Number(!!a.primary)||(b.count||0)-(a.count||0)||a.id.localeCompare(b.id));return new Set((level===0?candidates:candidates.slice(0,22)).map(p=>p.id));}
function screenRadius(point,level){if(point.type==='density')return point.count?Math.min(7,2+Math.log2(point.count+1)*.3):1.5;if(point.representative)return Math.min(5,2.4+Math.log2((point.degree||0)+1)*.2);const ranges=[[7,14],[5,9],[4,7]][level]||[4,12],weight=point.degree??point.count??0;return Math.min(ranges[1],ranges[0]+Math.log2(weight+1)*.35);}
function nodeStyle(point){
  if(point.type==='cluster'||point.type==='density')return {kind:'聚合',shape:'ring',color:'#9aabb9'};
  let type=String(point.type||'').toLowerCase();
  // Archive adapters keep the original generic type. Typed original identities
  // may supply its presentation category; arbitrary filenames cannot.
  if(type==='original_node'||type==='history_node')type=String(point.record||point.id||'').match(/:(?:owned:)?(run|receipt|fact|observation|output|artifact|hypothesis|claim):/)?.[1]||type;
  if(/hyperedge|and.rule|junction/.test(type))return {kind:'共同前提',shape:'diamond',color:'#b49bd6'};
  if(/receipt|fact/.test(type))return {kind:/fact/.test(type)?'事实':'回执',shape:'ring',color:'#99c4b3'};
  if(/observation|measurement|metric/.test(type))return {kind:'观测',shape:'circle',color:'#d4c17c'};
  if(/artifact|output|file/.test(type))return {kind:'产物',shape:'hexagon',color:'#82b9d3'};
  if(/run|execution|action/.test(type))return {kind:'执行',shape:'square',color:'#b397d0'};
  if(/project|snapshot/.test(type))return {kind:'项目快照',shape:'ring',color:'#8ca7ba'};
  if(/claim|hypothesis|statement|assertion/.test(type))return {kind:'声明',shape:'circle',color:point.primary?'#c5c8d0':'#a6a8b1'};
  return {kind:'记录',shape:'circle',color:'#9aabb9'};
}
return {status,shortLabel,labelIds,screenRadius,nodeStyle};})();
if(typeof module!=='undefined'&&module.exports)module.exports=ArchivePresentation;
if(typeof document!=='undefined')(() => {
  'use strict';
  const $=id=>document.getElementById(id),host=$('graph'),card=$('card'),results=$('results'),hover=$('hover');
  const text=(tag,value,cls)=>{const e=document.createElement(tag);e.textContent=String(value??'');if(cls)e.className=cls;return e;};
  const button=(label,action)=>{const e=text('button',label);e.addEventListener('click',action);return e;};
  const status=ArchivePresentation.status;
  const kind=value=>ArchivePresentation.nodeStyle(typeof value==='string'?{type:value}:value).kind;
  const color=p=>parseInt(ArchivePresentation.nodeStyle(p).color.slice(1),16);
  let membershipSet=new Set(),membershipEdges=new Set();let g,worker,rawWorker,rawReady=null,ready=false,paused=false,fitScale=1,level=0,seq=0,lastQuery=0,lastSignature='',pendingQuery=false,queuedView=null,selected=null,view=new Map(),searchTimer,queryTimer,viewportVersion=0;
  let topology='',showNames=false,growthFrame=null,growthIds=[],lastFrameMeasure=0,currentScene=null,nodeSize=1,edgeWidth=1;
  const relationStyles={dependency:{color:'#bebbc9',dash:'solid',width:.8,opacity:.35},source:{color:'#a9b9c8',dash:'dashed',width:.65,opacity:.16},history:{color:'#a49cb3',dash:'dotted',width:.55,opacity:.05},other:{color:'#7e838b',dash:'solid',width:.5,opacity:.08},mixed:{color:'#8e939b',dash:'solid',width:.6,opacity:.1}};
  const callbacks=new Map(),urls=[];
  const api={viewport:{},totals:{},physics:{},rawNode:async index=>{const n=await request('locate',{index});return n.raw_node_index===null?null:rawRequest('raw-node',n.raw_node_index??index);},rawEdge:async index=>{const e=await request('raw-locator',{index});return rawRequest('raw-edge',e.index);},rawHyperedge:id=>rawRequest('raw-hyperedge',id),locate:index=>locate(index)};
  window.RDSArchive=api;
  function makeWorker(){const code=$('d3-worker-lib').textContent+'\n'+$('archive-worker').textContent,url=URL.createObjectURL(new Blob([code],{type:'text/javascript'}));urls.push(url);return new Worker(url);}
  function request(type,value={}){return new Promise((resolve,reject)=>{const request=++seq;callbacks.set(request,{resolve,reject});worker.postMessage({type,...value,request});});}
  async function rawRequest(type,index){if(type!=='raw-hyperedge'&&(!Number.isInteger(index)||index<0))throw Error('Invalid archive index');if(!rawReady){rawReady=new Promise((resolve,reject)=>{rawWorker=makeWorker();rawWorker.onmessage=e=>{const m=e.data;if(m.type==='raw-ready')resolve();else if(m.type==='error'){const cb=callbacks.get(m.request);if(cb){callbacks.delete(m.request);cb.reject(Error(m.message));}else reject(Error(m.message));}else{const cb=callbacks.get(m.request);if(cb){callbacks.delete(m.request);cb.resolve(m.value);}}};rawWorker.postMessage({type:'raw-init',compressed:$('archive-raw').textContent});});}await rawReady;return new Promise((resolve,reject)=>{const request=++seq;callbacks.set(request,{resolve,reject});rawWorker.postMessage({type,index,id:type==='raw-hyperedge'?index:undefined,request});});}
  function replace(source,needle,value){if(!source.includes(needle))throw Error('Pinned renderer contract differs');return source.replace(needle,value);}
  function rendererSource(){let source=$('archive-renderer').textContent;
    source=replace(source,'const t = this.text = new PIXI.Text(this.label || this.id, this.textStyle());','const t = this.text = new PIXI.Container(); t.style=this.textStyle(); t.anchor={set(){}}; t.__archiveDeferred=true;');
    source=replace(source,'const hl = r.getHighlightNode(), isHl = hl === this, ns = r.nodeScale, text = this.text;',`if(this.text.__archiveDeferred && (r.getHighlightNode()===this || this.rds?.labelAllowed)){const old=this.text,t=this.text=new PIXI.Text(this.label||this.id,this.textStyle());t.eventMode='none';t.resolution=1.5;t.anchor.set(.5,0);t.zIndex=2;t.alpha=0;r.hanger.addChild(t);old.destroy();} const hl = r.getHighlightNode(), isHl = hl === this, ns = r.nodeScale, text = this.text;`);
    // Deferred containers never become labels until the same node is readable or selected.
    source=replace(source,'let textVis = ta > 0.001 &&',"let textVis = !text.__archiveDeferred && (this.rds?.labelAllowed || isHl) && ta > 0.001 &&");
    source=replace(source,'clamp(this.targetScale, 1 / 128, 8)','clamp(this.targetScale, this.archiveMinScale || 1/128, 8)');source=replace(source,'fontSize: 14 + this.getSize() / 4',"fontSize: this.rds?.type==='cluster'?12:14 + this.getSize() / 4");source=replace(source,'if (isHl) ta = 1;',"if (isHl || this.rds?.type==='cluster'&&this.rds.labelAllowed) ta = 1;");source=replace(source,'const s = isHl && r.scale < 1 ? 1 / r.scale : ns;',"const s = this.rds?.type==='cluster' || isHl && r.scale<1 ? 1/r.scale : ns;");source=replace(source,'const hl=this.dragNode || this.highlightNode || this.rdsPinned;', 'if(this.rdsPinned && !this.dragNode && !this.highlightNode && this.archiveMembership){this.rdsLastHL=this.rdsPinned;this.rdsRelated=this.archiveMembership;return this.rdsPinned;} const hl=this.dragNode || this.highlightNode || this.rdsPinned;');source=replace(source,'if (this.rendered || !this.source.rendered || !this.target.rendered) return;','if (this.rendered) return;');source=replace(source,'if (n.rendered || n.rdsHidden) continue;','if(n.rendered || n.rdsHidden || outside(this.viewport,box(n.x,n.y,n.getSize()*this.nodeScale+1))) continue;');source=replace(source,'this.rds?.dash ? r.rdsDashTexture : PIXI.Texture.WHITE',"this.rds?.relation==='history'?(r.archiveDotTexture||r.rdsDashTexture):this.rds?.dash ? r.rdsDashTexture : PIXI.Texture.WHITE");return source;
  }
  function camera(x,y,scale){g.targetScale=Math.min(8,Math.max(g.archiveMinScale||1e-6,scale));g.setScale(g.targetScale);g.setPan(g.width/2-x*g.scale,g.height/2-y*g.scale);g.panvX=g.panvY=0;g.changed();query(true);}
  function fit(){const b=api.bounds;if(!b)return;fitScale=Math.min((g.width-100)/Math.max(100,b[2]-b[0]),(g.height-120)/Math.max(100,b[3]-b[1]));fitScale=Math.max(1e-6,Math.min(2,fitScale*.85));g.archiveMinScale=fitScale/4;camera((b[0]+b[2])/2,(b[1]+b[3])/2,fitScale);}
  function currentLevel(){const ratio=g.scale/fitScale;return ratio<2?0:ratio<5?1:ratio<12?2:3;}
  function sendView(next){
    pendingQuery=true;lastSignature=next.signature;lastQuery=performance.now();const version=++viewportVersion;
    request('viewport',{level:next.level,rect:next.rect,edgeBudget:next.level===0?3500:800,nodeBudget:3500}).then(m=>{
      pendingQuery=false;
      // Keep completed scenes visible during continuous panning, then query the
      // newest camera. Coalescing must bound work without starving the display.
      if(version===viewportVersion&&!m.cancelled){api.viewport={...m.counters,level:m.level+1,queryMs:performance.now()-next.started};paint(m,next.rect);}
      if(queuedView){const latest=queuedView;queuedView=null;sendView(latest);}
    }).catch(error=>{pendingQuery=false;queuedView=null;fail(error);});
  }
  function query(force=false){
    if(!ready)return;const now=performance.now(),scale=g.scale,halo=80/scale,rect=[-g.panX/scale-halo,-g.panY/scale-halo,(g.width-g.panX)/scale+halo,(g.height-g.panY)/scale+halo];level=currentLevel();const signature=[level,...rect.map(v=>Math.round(v*scale/4))].join(':');
    if(signature===lastSignature&&!force){queuedView=null;return;}
    const next={level,rect,signature,started:now};
    if(pendingQuery){queuedView=next;return;}
    if(!force&&now-lastQuery<70){if(!queryTimer)queryTimer=setTimeout(()=>{queryTimer=null;query();},70);return;}
    if(queryTimer){clearTimeout(queryTimer);queryTimer=null;}sendView(next);
  }
  function pattern(dotted){const canvas=document.createElement('canvas');canvas.width=16;canvas.height=4;const c=canvas.getContext('2d');c.fillStyle='#fff';if(dotted){c.beginPath();c.arc(2,2,1.5,0,Math.PI*2);c.fill();}else c.fillRect(0,1,9,2);return PIXI.Texture.from(canvas);}
  let dashTexture,dotTexture;
  function paint(m,rect){
    currentScene={m,rect};cancelGrowth();const started=performance.now(),screenRect=[-g.panX/g.scale,-g.panY/g.scale,(g.width-g.panX)/g.scale,(g.height-g.panY)/g.scale];
    const labels=showNames?new Set(m.points.filter(p=>p.type!=='density'&&p.x>=screenRect[0]&&p.x<=screenRect[2]&&p.y>=screenRect[1]&&p.y<=screenRect[3]).sort((a,b)=>Number(!!b.primary)-Number(!!a.primary)||(b.degree||b.count||0)-(a.degree||a.count||0)).slice(0,80).map(p=>p.id)):new Set();
    const nodes=Object.create(null),links=[];view=new Map(m.points.map(p=>[p.id,p]));for(const p of m.points){nodes[p.id]={label:ArchivePresentation.shortLabel(p.type==='cluster'?p.label+' · '+p.count:p.label),type:'',color:{rgb:color(p),a:p.type==='density'?.55:p.raw&&!p.primary?.72:1},rds:{...p,record:p.record||p.id,labelAllowed:labels.has(p.id),radius:p.representative||p.type==='density'||p.type==='cluster'?ArchivePresentation.screenRadius(p,m.level)/Math.sqrt(g.scale):ArchivePresentation.screenRadius(p,m.level),outline:p.raw&&/CONTRADICTED|SOURCE_HASH_DIFFERS|FAILED/.test(p.status)?{color:'#c87979'}:p.raw&&p.status==='SUPPORTED'?{color:'#7abeb2'}:undefined,size:1,shape:ArchivePresentation.nodeStyle(p).shape}};}
    // GraphRenderer combines identical endpoint pairs. Combine only their display multiplicity here.
    const pairs=new Map();for(const e of m.edges){const key=e.source+'>'+e.target;if(pairs.has(key)){const p=pairs.get(key);p.count+=e.count;if(p.family!==e.family)p.family='mixed';}else pairs.set(key,{...e});}
    for(const e of pairs.values()){const style=relationStyles[e.family]||relationStyles.other;links.push([e.source,e.target,{relation:e.family,dash:style.dash!=='solid',arrow:false,width:style.width,opacity:style.opacity,count:e.count,color:style.color}]);}
    const signature=m.level+'|'+m.points.map(p=>p.id).join(',')+'|'+links.map(l=>l[0]+'>'+l[1]+':'+l[2].relation+':'+l[2].count).join(',');
    if(signature!==topology){g.setData({nodes,links});topology=signature;api.setDataCalls=(api.setDataCalls||0)+1;}
    for(const p of m.points){const n=g.nodeLookup.get(p.id);if(n){n.x=p.x;n.y=p.y;n.color=nodes[p.id].color;n.label=nodes[p.id].label;n.rds=nodes[p.id].rds;}}
    g.rdsPinned=selected!==null?g.nodeLookup.get('n'+selected)||null:null;g.rdsLastHL=undefined;
    for(const l of g.links){const edge=pairs.get(l.source.id+'>'+l.target.id),style=edge&&(relationStyles[edge.family]||relationStyles.other);if(style)l.rds={...(l.rds||{}),color:style.color,width:style.width,opacity:style.opacity,dash:style.dash!=='solid',pattern:style.dash,relation:edge.family};if(l.rendered&&style)l.line.texture=style.dash==='dotted'?dotTexture:style.dash==='dashed'?dashTexture:PIXI.Texture.WHITE;if(selected!==null&&(l.source.id==='n'+selected||l.target.id==='n'+selected))l.rds={...l.rds,color:'#b8cbd7',width:2};}
    g.changed();for(const [k,v] of Object.entries(api.viewport))host.dataset[k]=String(v);host.dataset.defaultLabels=String(labels.size);host.dataset.totalNodes=String(api.totals.nodes);host.dataset.totalEdges=String(api.totals.edges);host.dataset.lod=String(m.level+1);
    $('state').textContent='';
    $('view-counts').textContent=m.level<3?['研究组','来源','分块'][m.level]+' · '+m.counters.visibleNodes+' 个聚合':m.counters.visibleNodes.toLocaleString()+' 条记录'+(m.counters.nodeAggregation?' · 聚合显示':'');
    document.querySelectorAll('#layers button').forEach(b=>b.classList.toggle('active',Number(b.dataset.level)===m.level));api.paintMs=performance.now()-started;host.dataset.paintMs=String(api.paintMs);
  }
  function cancelGrowth(){if(growthFrame!==null)cancelAnimationFrame(growthFrame);growthFrame=null;for(const id of growthIds){const n=g?.nodeLookup.get(id);if(n)n.rdsHidden=false;}growthIds=[];}
  function grow(){
    cancelGrowth();growthIds=[...view.values()].sort((a,b)=>Number(!!b.primary)-Number(!!a.primary)||(b.degree||b.count||0)-(a.degree||a.count||0)||a.id.localeCompare(b.id)).map(p=>p.id);const start=performance.now(),count=growthIds.length;
    for(const id of growthIds){const n=g.nodeLookup.get(id);if(n)n.rdsHidden=true;}
    let shown=0;function reveal(now){const end=Math.min(count,Math.max(1,Math.floor(count*(now-start)/4500)));while(shown<end){const n=g.nodeLookup.get(growthIds[shown++]);if(n)n.rdsHidden=false;}g.changed();if(shown<count)growthFrame=requestAnimationFrame(reveal);else{growthFrame=null;growthIds=[];}}growthFrame=requestAnimationFrame(reveal);
  }
  function appearance(){g.setOptions({nodeSize,lineSize:edgeWidth,textFade:0});if(currentScene)paint(currentScene.m,currentScene.rect);}
  function toggleSettings(open){const panel=$('tools');panel.hidden=typeof open==='boolean'?!open:!panel.hidden;for(const id of ['settings','gear'])$(id).setAttribute('aria-expanded',String(!panel.hidden));}
  function pager(parent,page,total,size,action){const nav=text('div','', 'pager');nav.append(button('上一页',()=>action(Math.max(0,page-1))),text('span',(page+1)+' / '+Math.max(1,Math.ceil(total/size))),button('下一页',()=>action(Math.min(Math.max(0,Math.ceil(total/size)-1),page+1))));nav.firstChild.disabled=page===0;nav.lastChild.disabled=(page+1)*size>=total;parent.append(nav);}
  async function inspect(index,page=0){const m=await request('detail',{index,page});selected=index;g.rdsPinned=g.nodeLookup.get('n'+index)||null;g.rdsLastHL=undefined;g.changed();card.hidden=false;card.replaceChildren(button('×',()=>{card.hidden=true;selected=null;membershipSet=new Set();membershipEdges=new Set();g.archiveMembership=membershipSet;g.rdsPinned=null;g.rdsLastHL=undefined;g.changed();}),text('h2',m.node.label),text('span',kind(m.node),'tag'),text('span',status(m.node.status),'tag'));if(m.node.primary)card.append(text('span','当前记录','tag'));if(m.node.summary&&m.node.summary!==m.node.label)card.append(text('p',m.node.summary));card.append(text('h3',/hyperedge/i.test(m.node.type)?'共同前提与结论 · '+m.total:'关联记录 · '+m.total));
    for(const entry of m.entries){const row=text('div','', 'relation-row');const relation=/premise|共同前提/i.test(entry.type)?'共同前提':/conclusion|结论/i.test(entry.type)?'结论':entry.direction==='out'?'指向':'来自';row.append(button(entry.label,()=>locate(entry.index)),text('small',relation+' · '+status(entry.status)));card.append(row);}if(m.total>m.size)pager(card,m.page,m.total,m.size,p=>inspect(index,p));
    // Complete membership is queried independently of the paginated card.
    const membership=await request('highlight',{index});g.rdsRelated=new Set(membership.nodes.map(i=>'n'+i));api.selection={index,totalMembers:membership.nodes.length,totalEdges:membership.edges.length};membershipSet=new Set(membership.nodes.map(i=>'n'+i));membershipEdges=new Set(membership.edges);g.rdsLastHL=g.rdsPinned;g.rdsRelated=membershipSet;g.archiveMembership=membershipSet;for(const l of g.links)if(l.source.id==='n'+index||l.target.id==='n'+index){l.rds={...(l.rds||{}),color:'#b8cbd7',width:2};}g.changed();
  }
  async function locate(index){const m=await request('locate',{index});camera(m.x,m.y,Math.max(.8,fitScale*18));await inspect(index);}
  async function cluster(index){const m=await request('cluster',{index}),c=m.cluster;card.hidden=false;card.replaceChildren(button('×',()=>card.hidden=true),text('h2',c.label),text('p',c.node_count+' 条记录 · '+c.edge_count+' 条关联'),button(c.level<2?'展开此组':'查看记录',()=>camera(c.x,c.y,fitScale*[3,7,18][c.level])));const counts=text('p',Object.entries(c.status_counts||{}).map(([s,n])=>status(s)+' '+n).join(' · '));card.append(counts);}
  async function search(page=0){const queryText=$('search').value.trim();if(!queryText){results.hidden=true;return;}const m=await request('search',{query:queryText,page});if(queryText!==$('search').value.trim())return;results.hidden=false;results.replaceChildren(text('small',m.total+' 条匹配'));for(const n of m.entries){const row=button(n.label,()=>locate(n.index));row.append(text('small',kind(n)+' · '+status(n.status)));results.append(row);}if(m.total>m.size)pager(results,m.page,m.total,m.size,search);}
  function fail(error){$('state').textContent='暂时无法读取图谱';host.dataset.error=String(error?.message||error);console.error(error);}
  async function init(){try{Function(rendererSource())();g=new window.GraphRenderer(host,function(){self.onmessage=()=>{};});const nativeHandler=g.worker.onmessage;g.worker.terminate();worker=makeWorker();g.worker={onmessage:nativeHandler,postMessage:m=>{if(m.forceNode&&!paused)worker.postMessage({type:'drag',...m.forceNode});},terminate(){}};g.setOptions({nodeSize:1,lineSize:1,textFade:0});dashTexture=pattern(false);dotTexture=pattern(true);g.archiveDotTexture=dotTexture;
      const down=g.onPointerDown.bind(g);g.onPointerDown=(n,e)=>{const p=view.get(n.id);if(p&&(p.raw||p.type==='cluster'))down(n,e);else g.onNodeClick(n);};g.onNodeClick=n=>{const p=view.get(n.id);if(!p)return;if(p.zoomOnly)camera(p.x,p.y,g.scale*2.4);else if(p.raw)inspect(p.index).catch(fail);else cluster(p.index).catch(fail);};g.onNodeHover=n=>{const p=n&&view.get(n.id);if(!p){hover.hidden=true;return;}hover.hidden=false;hover.replaceChildren(text('strong',p.label),text('small',p.raw?status(p.status):p.type==='cluster'?p.count+' 条记录':'聚合密度'));};
      host.addEventListener('pointermove',e=>{hover.style.left=Math.min(e.clientX+14,innerWidth-240)+'px';hover.style.top=Math.min(e.clientY+14,innerHeight-75)+'px';});
      const nativeFrame=g.frame.bind(g);g.frame=()=>{const started=performance.now();nativeFrame();if(started-lastFrameMeasure>500){api.frameMs=performance.now()-started;host.dataset.frameMs=String(api.frameMs);lastFrameMeasure=started;}query();};
      worker.onmessage=e=>{const m=e.data;if(m.request&&callbacks.has(m.request)){const cb=callbacks.get(m.request);callbacks.delete(m.request);m.type==='error'?cb.reject(Error(m.message)):cb.resolve(m);return;}if(m.type==='progress')$('state').textContent='正在准备图谱…';else if(m.type==='ready'){ready=true;api.totals={nodes:m.source.node_count,edges:m.source.edge_count};api.bounds=m.bounds;api.levels=m.levels;api.indexStats=m.indexStats;$('source-name').textContent='本地档案 · 只读';$('totals').textContent=m.source.node_count.toLocaleString()+' 条记录 · '+m.source.edge_count.toLocaleString()+' 条关联';$('state').textContent='';fit();}else if(m.type==='physics'){api.physics={nodes:m.nodes,edges:m.edges,springs:m.springs,steps:m.steps,settled:m.settled};host.dataset.physicsNodes=String(m.nodes||0);host.dataset.physicsSteps=String(m.steps||0);if(m.buffer){g.worker.onmessage({data:{ids:m.ids,buffer:m.buffer}});}}else if(m.type==='index-progress'){api.physics.indexRemaining=m.remaining;host.dataset.indexRemaining=String(m.remaining);}else if(m.type==='bounds-updated'){api.bounds=m.bounds;host.dataset.bounds=JSON.stringify(m.bounds);}else if(m.type==='positions-updated')query(true);else if(m.type==='error')fail(Error(m.message));};
      worker.postMessage({type:'init',compressed:$('archive-data').textContent});$('fit').addEventListener('click',fit);
      $('restart').addEventListener('click',()=>{cancelGrowth();card.hidden=true;results.hidden=true;selected=null;membershipSet=new Set();membershipEdges=new Set();g.archiveMembership=membershipSet;g.rdsPinned=null;g.rdsLastHL=undefined;fit();});
      $('grow').addEventListener('click',grow);
      $('pause').addEventListener('click',()=>{paused=!paused;const label=paused?'恢复布局':'暂停布局';$('pause').textContent=paused?'▷':'Ⅱ';$('pause').setAttribute('aria-pressed',String(paused));$('pause').setAttribute('aria-label',label);$('pause').title=label;if(paused)worker.postMessage({type:'pause'});});
      $('theme').addEventListener('click',()=>{const light=document.body.classList.toggle('theme-light');document.body.classList.toggle('theme-dark',!light);g.readColors();g.changed();});
      for(const id of ['settings','gear'])$(id).addEventListener('click',()=>toggleSettings());
      $('show-names').addEventListener('change',()=>{showNames=$('show-names').checked;appearance();});
      for(const id of ['node-size','edge-width'])$(id).addEventListener('input',()=>{nodeSize=Number($('node-size').value);edgeWidth=Number($('edge-width').value);appearance();});
      $('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>search().catch(fail),160);});document.querySelectorAll('#layers button').forEach(b=>b.addEventListener('click',()=>{const center=[(g.width/2-g.panX)/g.scale,(g.height/2-g.panY)/g.scale];camera(...center,fitScale*[1,3,7,18][Number(b.dataset.level)]);}));addEventListener('resize',()=>query(true));
      addEventListener('keydown',e=>{if(e.key==='Escape')toggleSettings(false);if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='f'){e.preventDefault();toggleSettings(true);$('search').focus();}});
      addEventListener('pagehide',()=>{cancelGrowth();ready=false;worker.terminate();rawWorker?.terminate();for(const u of urls)URL.revokeObjectURL(u);URL.revokeObjectURL(g.workerBlobUrl);clearTimeout(queryTimer);clearTimeout(searchTimer);callbacks.clear();queuedView=null;g.app.destroy(true,{children:true});},{once:true});
    }catch(error){fail(error);}}
  init();
})();
