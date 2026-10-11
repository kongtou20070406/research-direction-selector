
// Display-only credit on shortest dependency paths. Does not infer support.
function goalCredits(spec,goal) {
 const ids=new Set(spec.nodes.map(n=>n.id)),incoming=new Map(),distance=new Map(),nodes=new Map(),edges=new Map();
 if(!ids.has(goal))return {nodes,edges,distance};
 for(const e of spec.hyperedges)if(e.status!=='CONTRADICTED'){
  const w=e.weight===undefined?1:typeof e.weight==='number'&&Number.isFinite(e.weight)?Math.max(0,e.weight):0;
  if(!w)continue;const list=incoming.get(e.conclusion)||[];list.push({e,w});incoming.set(e.conclusion,list);
 }
 const queue=[goal];distance.set(goal,0);nodes.set(goal,1);
 for(let i=0;i<queue.length;i++){const id=queue[i],d=distance.get(id);for(const {e} of incoming.get(id)||[])for(const p of e.premises)if(!distance.has(p)){distance.set(p,d+1);queue.push(p);}}
 for(const id of queue){const d=distance.get(id),credit=nodes.get(id)||0,routes=(incoming.get(id)||[]).filter(({e})=>e.premises.every(p=>distance.get(p)===d+1)),max=routes.reduce((m,r)=>Math.max(m,r.w),0),total=routes.reduce((sum,r)=>sum+r.w/max,0);
  for(const {e,w} of routes){const share=credit*(w/max)/total;edges.set(e.id,share);for(const p of e.premises)nodes.set(p,(nodes.get(p)||0)+share/e.premises.length);}
 }
 return {nodes,edges,distance};
}
// Current display incidences only; these ranks do not infer support or goal credit.
function activeDependencyLevels(ids,links,relations) {
 const adj=new Map(ids.map(id=>[id,[]])),rev=new Map(ids.map(id=>[id,[]])),active=[];
 for(const [s,t,meta] of links){const cfg=relations[meta.relation];if(meta.family==='membership'||cfg&&(cfg.mode==='none'||cfg.strength===0)||!adj.has(s)||!adj.has(t))continue;adj.get(s).push(t);rev.get(t).push(s);active.push([s,t]);}
 const seen=new Set(),order=[];
 for(const first of ids){if(seen.has(first))continue;seen.add(first);const stack=[[first,0]];
  while(stack.length){const frame=stack[stack.length-1],neighbors=adj.get(frame[0]);if(frame[1]===neighbors.length){order.push(frame[0]);stack.pop();continue;}const next=neighbors[frame[1]++];if(!seen.has(next)){seen.add(next);stack.push([next,0]);}}
 }
 const component=new Map();let cid=-1;
 for(let i=order.length-1;i>=0;i--){const first=order[i];if(component.has(first))continue;component.set(first,++cid);const stack=[first];while(stack.length)for(const next of rev.get(stack.pop()))if(!component.has(next)){component.set(next,cid);stack.push(next);}}
 const out=Array.from({length:cid+1},()=>new Set()),indegree=out.map(()=>0),rank=out.map(()=>0);
 for(const [s,t] of active){const a=component.get(s),b=component.get(t);if(a!==b&&!out[a].has(b)){out[a].add(b);indegree[b]++;}}
 const queue=[];for(let i=0;i<indegree.length;i++)if(!indegree[i])queue.push(i);
 for(let i=0;i<queue.length;i++)for(const next of out[queue[i]]){rank[next]=Math.max(rank[next],rank[queue[i]]+1);if(!--indegree[next])queue.push(next);}
 const center=rank.reduce((m,r)=>Math.max(m,r),0)/2;
 return new Map(ids.map(id=>[id,adj.get(id).length||rev.get(id).length?(rank[component.get(id)]-center)*160:null]));
}
(() => {
 'use strict';
 const data=JSON.parse(document.getElementById('snapshot').textContent), view=data.replica_view;
 const $=id=>document.getElementById(id), panel=$('controls'), card=$('note-card');
 const source=(data.source || '').split(/[\\/]/).filter(Boolean);
  $('source-name').textContent=data.readable?.graph?.title?.trim()||(data.demo?'演示':source.includes('rds58-n13-sol-max-fresh-20261004')?'n=13':'RDS');
  $('analysis-note').textContent=data.analysis_notice||'阻断条件分析状态未知。';
 if(!data.graph){$('state').textContent=data.reason || data.status;return;}
 const baseLinks=view.links;
 if(view.scope){view.nodes.scope=view.scope.node;if(data.readable?.graph?.title?.trim())view.nodes.scope.label=data.readable.graph.title.trim();view.links=[...baseLinks,...view.scope.links];}
 const g=new GraphRenderer($('graph'),SIM_WORKER_MAIN), recordById=new Map(data.graph.nodes.map(n=>[n.id,n]));
 const key='rds-replica-v2:'+ (data.snapshot_sha256 || data.graph_sha256);
 const defaults={search:'',colors:true,orphans:true,scope:true,sizeMode:'goal',goal:data.graph.goals[0]||'',nodeStyles:{},arrows:true,nodeSize:1,lineSize:1,textFade:0,damping:.4,growth:true,growthSeconds:12,flowStrength:.025,groupStrength:.035,edgeRepulsion:.25,edgeClearance:45,centerStrength:.055,repelStrength:1000,linkStrength:1,linkDistance:180,relations:{}};
 let opts={...defaults}; try{opts={...defaults,...JSON.parse(localStorage.getItem(key)||'null')};}catch{}
 if(!data.graph.goals.includes(opts.goal))opts.goal=defaults.goal;
 opts.nodeStyles={...opts.nodeStyles};opts.relations=Object.assign(Object.create(null),opts.relations);
 const kindDefaults={'声明':['#c2c8d2','circle'],'执行':['#b39ddb','square'],'回执':['#82c4af','ring'],'产物':['#82b6d4','hexagon'],'观测':['#d5c17e','circle'],'超边':['#9c95af','diamond'],'项目':['#8aa1b8','ring']};
 for(const [kind,[color,shape]] of Object.entries(kindDefaults))opts.nodeStyles[kind]={color,shape,size:1,...opts.nodeStyles[kind]};
 let credits=goalCredits(data.graph,opts.goal);
 const persist=()=>{try{localStorage.setItem(key,JSON.stringify(opts));}catch{}};
 const relationSamples=new Map();for(const [, , meta] of view.links)if(!relationSamples.has(meta.relation))relationSamples.set(meta.relation,meta);
 for(const r of view.relations) {const sample=relationSamples.get(r)||{};opts.relations[r]={mode:'attract',strength:r==='项目归属'?.12:r==='声明输出'?.2:r==='来源绑定'?.45:1,color:sample.color||'#bdc2d2',width:sample.width||1.25,dash:!!sample.dash,...opts.relations[r]};}
 function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
 function button(text,action,cls='btn'){const b=el('button',text,cls);b.type='button';b.addEventListener('click',action);return b;}
 function paintLegend(){const row=el('div',undefined,'legend-row');for(const [kind,style] of Object.entries(opts.nodeStyles)){if(kind==='项目'&&!opts.scope)continue;const chip=el('span'),dot=el('span',({circle:'●',square:'■',triangle:'▲',hexagon:'⬡',ring:'○',diamond:'◇'})[style.shape],'symbol');dot.style.color=opts.colors?style.color:'var(--text-muted)';chip.append(dot,el('span',kind==='项目'?'归属':kind));row.append(chip);}const status=el('div',undefined,'legend-row legend-note');for(const [color,text] of [['#39b872','成功 / 支持'],['#e65b63','失败 / 反驳'],['#9299a6','未判定']]){const chip=el('span'),dot=el('span','○','symbol');dot.style.color=color;chip.append(dot,el('span',text));status.append(chip);}status.append(el('span','虚线 · 来源 / 归属'));$('legend').replaceChildren(row,status);}
 function section(name,open=false){const box=el('div',undefined,'gc-section'+(open?'':' is-collapsed')), head=button('',()=>{box.classList.toggle('is-collapsed');},'gc-head');head.setAttribute('aria-expanded',String(open));head.addEventListener('click',()=>head.setAttribute('aria-expanded',String(!box.classList.contains('is-collapsed'))));head.append(el('span','⌄','chev'),el('span',name,'name'));const body=el('div',undefined,'gc-body');box.append(head,body);panel.append(box);return body;}
 function checkbox(body,label,key,change){const row=el('label',undefined,'gc-item');row.append(el('span',label));const i=el('input');i.type='checkbox';i.checked=opts[key];i.addEventListener('change',()=>{opts[key]=i.checked;persist();change();});row.append(i);body.append(row);}
 function slider(body,label,key,min,max,step,change){const wrap=el('label',undefined,'gc-item col'), row=el('span',undefined,'row'), value=el('span',undefined,'val'),i=el('input');row.append(el('span',label),value);i.type='range';i.min=min;i.max=max;i.step=step;i.value=opts[key];i.setAttribute('aria-label',label);const paint=()=>{value.textContent=Number(i.value).toFixed(step<1?2:0);i.style.setProperty('--p',((i.value-min)/(max-min)*100)+'%');};paint();i.addEventListener('input',()=>{opts[key]=+i.value;paint();persist();change();});wrap.append(row,i);body.append(wrap);}
 const filter=section('筛选');const search=el('input',undefined,'search-input');search.placeholder='搜索节点';search.setAttribute('aria-label','搜索节点');search.value=opts.search;const matches=el('div');matches.setAttribute('aria-label','搜索结果');filter.append(search,matches);let searchTimer;search.addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{opts.search=search.value;persist();build();},180);});checkbox(filter,'显示孤立节点','orphans',build);checkbox(filter,'显示项目归属','scope',build);
 const groups=section('节点样式');checkbox(groups,'按记录类型着色','colors',nodeAppearance);for(const [name,cfg] of Object.entries(opts.nodeStyles)){const row=el('div',undefined,'gc-item');row.append(el('span',name));const color=el('input');color.type='color';color.value=cfg.color;color.setAttribute('aria-label',name+' 节点颜色');color.addEventListener('input',()=>{cfg.color=color.value;persist();nodeAppearance();});row.append(color);groups.append(row);const style=el('div',undefined,'relation-style'),shape=el('select');shape.setAttribute('aria-label',name+' 节点形状');for(const [v,t] of (name==='超边'?[['diamond','菱形']]:[['circle','圆点'],['ring','圆环'],['square','方形'],['triangle','三角'],['hexagon','六边形']])){const o=el('option',t);o.value=v;shape.append(o);}shape.value=cfg.shape;shape.addEventListener('change',()=>{cfg.shape=shape.value;persist();nodeAppearance();});const size=el('input');size.type='range';size.min=.5;size.max=2;size.step=.1;size.value=cfg.size;size.setAttribute('aria-label',name+' 节点倍率');size.addEventListener('input',()=>{cfg.size=+size.value;persist();nodeAppearance();});style.append(shape,size);groups.append(style);}
 const weights=section('目标权重');const sizeChoice=el('select');sizeChoice.setAttribute('aria-label','节点大小依据');for(const [v,t] of [['goal','目标权重'],['degree','连接度']]){const o=el('option',t);o.value=v;sizeChoice.append(o);}sizeChoice.value=opts.sizeMode;sizeChoice.addEventListener('change',()=>{opts.sizeMode=sizeChoice.value;persist();nodeAppearance();});const goalChoice=el('select');goalChoice.setAttribute('aria-label','研究目标');for(const id of data.graph.goals){const o=el('option',Object.values(view.nodes).find(n=>n.rds.record===id)?.label||'研究目标');o.value=id;goalChoice.append(o);}goalChoice.value=opts.goal;goalChoice.addEventListener('change',()=>{opts.goal=goalChoice.value;persist();credits=goalCredits(data.graph,opts.goal);nodeAppearance();if(g.rdsPinned)inspect(g.rdsPinned.id);});weights.append(sizeChoice,goalChoice);
 const display=section('外观');checkbox(display,'显示结论箭头','arrows',displayOptions);slider(display,'节点大小','nodeSize',.4,3,.1,displayOptions);slider(display,'连线粗细','lineSize',.2,4,.1,displayOptions);slider(display,'标签隐去阈值','textFade',-3,3,.1,displayOptions);checkbox(display,'逐步展开','growth',()=>opts.growth?playGrowth():finishGrowth());slider(display,'展开时长（秒）','growthSeconds',4,30,1,()=>{if(growing)playGrowth();});
 const forces=section('力学');slider(forces,'运动阻尼','damping',.1,.85,.05,applyForces);slider(forces,'依赖流向','flowStrength',0,.08,.005,applyForces);slider(forces,'同次执行凝聚','groupStrength',0,.15,.005,applyForces);slider(forces,'超边排斥','edgeRepulsion',0,.6,.025,applyForces);slider(forces,'超边留白','edgeClearance',15,100,5,applyForces);slider(forces,'向心力','centerStrength',0,.3,.005,applyForces);slider(forces,'节点排斥','repelStrength',1,3000,25,applyForces);slider(forces,'节点吸引','linkStrength',0,2,.05,applyForces);slider(forces,'连线距离','linkDistance',80,400,5,applyForces);
 const relations=section('关系'),relationControls=el('div'),relationPager=el('div',undefined,'gc-item'),relationPageText=el('span');let relationPage=0;
 const previousRelations=button('上一页',()=>{relationPage--;renderRelationControls()}),nextRelations=button('下一页',()=>{relationPage++;renderRelationControls()});relationPager.append(previousRelations,relationPageText,nextRelations);relations.append(relationPager,relationControls);
 function renderRelationControls(){const start=relationPage*32;relationControls.replaceChildren();previousRelations.disabled=start===0;nextRelations.disabled=start+32>=view.relations.length;relationPageText.textContent=`${start+1}–${Math.min(start+32,view.relations.length)} / ${view.relations.length}`;for(const name of view.relations.slice(start,start+32)){const cfg=opts.relations[name],row=el('div',undefined,'gc-item');row.append(el('span',name));const select=el('select');select.setAttribute('aria-label',name+' 力学');for(const [v,t] of [['attract','吸引'],['repel','排斥'],['none','无力']]){const o=el('option',t);o.value=v;select.append(o);}select.value=cfg.mode;select.addEventListener('change',()=>{cfg.mode=select.value;persist();applyForces();});row.append(select);relationControls.append(row);const style=el('div',undefined,'relation-style'),color=el('input');color.type='color';color.value=cfg.color;color.setAttribute('aria-label',name+' 颜色');color.addEventListener('input',()=>{cfg.color=color.value;persist();restyle();});const width=el('input');width.type='range';width.min=.3;width.max=3;width.step=.1;width.value=cfg.width;width.setAttribute('aria-label',name+' 粗细');width.addEventListener('input',()=>{cfg.width=+width.value;persist();restyle();});const dash=el('select');dash.setAttribute('aria-label',name+' 线型');for(const [v,t] of [['solid','实线'],['dash','虚线']]){const o=el('option',t);o.value=v;dash.append(o);}dash.value=cfg.dash?'dash':'solid';dash.addEventListener('change',()=>{cfg.dash=dash.value==='dash';persist();restyle();});style.append(color,width,dash);relationControls.append(style);}}
 renderRelationControls();
 const close=button('收起设置',()=>panel.classList.add('is-close'));const actions=el('div',undefined,'gc-actions');actions.append(close);panel.append(actions);
 function displayOptions(){g.setOptions({nodeSize:opts.nodeSize,lineSize:opts.lineSize,textFade:opts.textFade,showArrow:opts.arrows});nodeAppearance();}
 function refreshFlow(){const levels=activeDependencyLevels(g.nodes.map(n=>n.id),g.links.map(l=>[l.source.id,l.target.id,l.rds]),opts.relations);for(const n of g.nodes)n.rds.flowX=levels.get(n.id);}
 function applyForces(){g.setForces({damping:opts.damping,flowStrength:opts.flowStrength,groupStrength:opts.groupStrength,edgeRepulsion:opts.edgeRepulsion,edgeClearance:opts.edgeClearance,centerStrength:opts.centerStrength,repelStrength:opts.repelStrength,linkStrength:opts.linkStrength,linkDistance:opts.linkDistance,relations:opts.relations});refreshFlow();nodeAppearance();$('state').textContent='布局收敛中…';}
 function restyle(){for(const l of g.links){const cfg=opts.relations[l.rds.relation];Object.assign(l.rds,{color:cfg.color,width:cfg.width,dash:cfg.dash});if(l.rendered)l.line.texture=cfg.dash?g.rdsDashTexture:PIXI.Texture.WHITE;}g.changed();}
 function recordFor(n){return n.rds.virtual?{id:'display:project-snapshot',status:'显示层',source:data.source,snapshot_sha256:data.snapshot_sha256,meaning:'同一快照的项目归属；不是原始科研节点',components:view.scope.components,unlinked_records:view.scope.unlinked_records}:n.rds.edge!==undefined?data.graph.hyperedges[n.rds.edge]:recordById.get(n.rds.record);}
 function summaryFor(n){if(n.rds.virtual)return data.readable?.graph?.summary?.trim()||'';const key=n.rds.edge!==undefined?'hyperedges':'nodes',id=recordFor(n).id,map=data.display?.[key];return map&&Object.hasOwn(map,id)?map[id].summary||'':'';}
 function diagnostics(parent,info){if(!info?.count)return;parent.append(el('h4','来源待核对'));for(const item of info.items)parent.append(el('p',item.message,'source-diagnostic'));if(info.omitted)parent.append(el('p',`另有 ${info.omitted} 项待核对`,'hint'));}
 $('graph-info').hidden=!(data.readable?.graph?.summary?.trim()||data.display?.diagnostics?.count);$('graph-info').addEventListener('click',()=>{finishGrowth();card.replaceChildren();card.classList.add('show');const close=button('×',()=>card.classList.remove('show'),'icon-btn close');close.setAttribute('aria-label','关闭详查');card.append(close,el('h3',data.readable?.graph?.title?.trim()||'研究说明'));if(data.readable?.graph?.summary?.trim())card.append(el('p',data.readable.graph.summary.trim(),'readable-summary'));diagnostics(card,data.display?.diagnostics);});
 let lastTargets='';
 function nodeAppearance(force=false){for(const n of g.nodes){const style=opts.nodeStyles[n.rds.kind],weight=n.rds.edge!==undefined?credits.edges.get(data.graph.hyperedges[n.rds.edge].id):credits.nodes.get(n.rds.record);n.rds.shape=style.shape;n.rds.size=style.size*(n.type==='hyperedge'?.65:opts.sizeMode==='degree'?view.nodes[n.id].rds.size:1);n.rds.radius=opts.sizeMode==='goal'?(n.rds.virtual?12:6+20*Math.sqrt(Math.min(1,weight||0))):undefined;n.rds.chargeWeight=n.rds.virtual?1:1+2*Math.sqrt(Math.min(1,weight||0));n.rds.collisionRadius=Math.max(24,Math.min(120,24+n.getSize()*1.8));n.color=opts.colors?{rgb:parseInt(style.color.slice(1),16),a:1}:null;if(n.rendered){n.text.style=n.textStyle();}}
  const targets=Object.fromEntries(g.nodes.map(n=>[n.id,{flowX:n.rds.flowX,group:n.rds.group,members:n.rds.members,chargeWeight:n.rds.chargeWeight,collisionRadius:n.rds.collisionRadius}])),signature=JSON.stringify(targets);if(force||signature!==lastTargets){lastTargets=signature;g.worker.postMessage({layoutTargets:targets,alpha:.3,run:true});}paintLegend();g.changed();}
 function build(){
  finishGrowth();g.rdsPinned=null;g.rdsLastHL=undefined;card.classList.remove('show');matches.replaceChildren();autoFit=true;started=performance.now();
  const query=opts.search.trim().toLowerCase(),nodes=Object.create(null),selected=new Set(),found=[],degree=new Set(baseLinks.flatMap(([s,t])=>[s,t]));
  for(const [id,n] of Object.entries(view.nodes)){
   if(n.rds.virtual&&!opts.scope)continue;const row=recordFor(n);
   if((!query||JSON.stringify(row).toLowerCase().includes(query)||n.label.toLowerCase().includes(query)||summaryFor(n).toLowerCase().includes(query))&&(n.rds.virtual||opts.orphans||degree.has(id))){selected.add(id);if(query)found.push(id);}
  }
  if(query)for(const [id,n] of Object.entries(view.nodes))if(n.rds.members&&(selected.has(id)||n.rds.members.some(m=>found.includes(m)))){selected.add(id);for(const m of n.rds.members)selected.add(m);}
  for(const [id,n] of Object.entries(view.nodes))if(n.type==='hyperedge'&&!n.rds.members.every(x=>selected.has(x)))selected.delete(id);
  for(const id of selected){const n=view.nodes[id];nodes[id]={...n,rds:{...n.rds},color:opts.colors?n.color:null};}
  const links=view.links.filter(([s,t,meta])=>(opts.scope||meta.family!=='membership')&&selected.has(s)&&selected.has(t)).map(([s,t,meta])=>[s,t,{...meta,...opts.relations[meta.relation]}]);
  g.setData({nodes,links});refreshFlow();restyle();nodeAppearance(true);
  $('counts').textContent=`${data.counts.nodes} 节点 · ${data.counts.hyperedges} 超边${query?' · '+nodesCount(nodes)+' 个匹配':''}`;$('state').textContent='布局收敛中…';
  if(query){matches.append(el('p',`${found.length} 个匹配`,'hint'));for(const id of found.slice(0,20)){const n=view.nodes[id],b=button(n.label,()=>inspect(id),'record-link');b.title=n.rds.kind+' · '+stateText(n);matches.append(b);}}
  if(opts.growth && !query)playGrowth();
 }
 function nodesCount(ns){return Object.keys(ns).length;}
 function stateText(n){return n.rds.virtual?'归属':n.rds.status_text||({SUPPORTED:n.rds.kind==='声明'?'声明支持':'已记录',CONTRADICTED:'声明反驳',PROPOSED:'候选',UNKNOWN:'待确认'}[recordFor(n).status]||'待确认');}
 function navigate(id){if(!g.nodeLookup.has(id)){opts.search='';search.value='';opts.orphans=true;persist();build();}inspect(id);}
 function inspect(id){
  const n=g.nodeLookup.get(id);if(!n)return;finishGrowth();g.rdsPinned=n;g.rdsLastHL=undefined;g.changed();card.replaceChildren();card.classList.add('show');
  const close=button('×',()=>{card.classList.remove('show');g.rdsPinned=null;g.rdsLastHL=undefined;g.changed();},'icon-btn close');close.setAttribute('aria-label','关闭详查');card.append(close,el('h3',n.label),el('span',n.rds.virtual?'归属':n.rds.kind,'tag'),el('span',stateText(n),'tag result-tag'));
  if(summaryFor(n))card.append(el('p',summaryFor(n),'readable-summary'));
  function linked(title,ids){if(!ids.length)return;card.append(el('h4',title));for(const target of ids){const v=view.nodes[target];card.append(button(v.label,()=>navigate(target),'record-link'));}}
  const map=data.display?.nodes,info=n.rds.record&&map&&Object.hasOwn(map,n.rds.record)?map[n.rds.record].diagnostics:null;diagnostics(card,info);for(const issue of info?.items||[]){const candidates=new Set(issue.candidates);linked('候选来源',Object.keys(view.nodes).filter(id=>candidates.has(view.nodes[id].rds.record)));if(issue.omitted_candidates)card.append(el('p',`另有 ${issue.omitted_candidates} 个候选`,'hint'));}
  if(n.rds.edge!==undefined){linked('共同前提 · AND',n.rds.members.slice(0,-1));linked('结论',n.rds.members.slice(-1));}
  else{const related=view.links.filter(([s,t,meta])=>(s===id||t===id)&&(opts.scope||meta.family!=='membership'));const routes=related.filter(([,t,meta])=>t===id&&meta.family==='dependency').map(([s])=>s);linked('推导路线 · OR',routes);const others=new Set(related.map(([s,t])=>s===id?t:s).filter(other=>!routes.includes(other)));linked('关联节点',[...others]);}
 }
 g.onNodeClick=n=>inspect(n.id);
 g.onNodeHover=n=>{const tip=$('hover-info');tip.hidden=!n;if(!n)return;tip.textContent=n.label+' · '+stateText(n);if(n.rds.edge!==undefined){const e=data.graph.hyperedges[n.rds.edge],target=view.nodes[n.rds.members.at(-1)];tip.textContent+=`\n${e.premises.length} 个共同前提 → ${target.label}`;}else tip.textContent+=' · '+(n.rds.virtual?'归属':n.rds.kind);if(summaryFor(n))tip.textContent+='\n'+summaryFor(n).slice(0,140);};
 let growing=false,growthTimer=null,growthOrder=[],growthStart=0;
 function finishGrowth(){clearInterval(growthTimer);growthTimer=null;growing=false;for(const n of g.nodes)n.rdsHidden=false;$('growth-info').textContent='';$('skip-growth').hidden=true;g.changed();}
 function playGrowth(){finishGrowth();g.highlightNode=g.rdsPinned=null;g.rdsLastHL=undefined;card.classList.remove('show');$('hover-info').hidden=true;growthOrder=[...g.nodes].sort((a,b)=>(a.rds.flowX??1e8)-(b.rds.flowX??1e8));if(!growthOrder.length)return;growing=true;growthStart=performance.now();for(const n of growthOrder)n.rdsHidden=true;$('state').textContent='';$('skip-growth').hidden=false;let shown=0;const advance=()=>{const desired=Math.min(growthOrder.length,Math.max(1,Math.ceil((performance.now()-growthStart)/(opts.growthSeconds*1000)*growthOrder.length)));while(shown<desired){const n=growthOrder[shown++];n.rdsHidden=false;n.fadeAlpha=0;}g.changed();$('growth-info').textContent=`展开 · ${shown} / ${growthOrder.length}`;if(shown===growthOrder.length)finishGrowth();};advance();growthTimer=setInterval(advance,60);}
 $('grow').addEventListener('click',playGrowth);$('skip-growth').addEventListener('click',finishGrowth);
 function fit(){if(!g.nodes.length)return;const xs=g.nodes.map(n=>n.x),ys=g.nodes.map(n=>n.y),left=Math.min(...xs),right=Math.max(...xs),top=Math.min(...ys),bottom=Math.max(...ys);const free=g.width-(panel.classList.contains('is-close')?60:300),scale=Math.min(1.3,Math.max(1/128,Math.min(free/(right-left+150),(g.height-100)/(bottom-top+150))));g.targetScale=scale;g.setScale(scale);g.setPan(free/2-(left+right)/2*scale,g.height/2-(top+bottom)/2*scale);g.panvX=g.panvY=0;g.changed();}
 $('fit').addEventListener('click',fit);$('restart').addEventListener('click',()=>{g.reheat();$('state').textContent='布局收敛中…';});$('theme').addEventListener('click',()=>{document.body.classList.toggle('theme-dark');g.readColors();});for(const id of ['settings','gear'])$(id).addEventListener('click',()=>panel.classList.toggle('is-close'));
 let lastUpdate=performance.now(), autoFit=true,started=performance.now();const receive=g.worker.onmessage;g.worker.onmessage=e=>{lastUpdate=performance.now();receive(e);};const statusTimer=setInterval(()=>{if(performance.now()-lastUpdate>600){$('state').textContent='';if(autoFit && performance.now()-started>1200){autoFit=false;fit();}}},250);
 $('graph').addEventListener('pointerdown',e=>{if(e.target.tagName==='CANVAS')autoFit=false;});$('graph').addEventListener('wheel',()=>autoFit=false,{passive:true});
 window.addEventListener('pagehide',()=>{clearInterval(statusTimer);clearInterval(growthTimer);clearTimeout(searchTimer);g.worker.terminate();URL.revokeObjectURL(g.workerBlobUrl);g.app.destroy(true,{children:true,texture:true,baseTexture:true});},{once:true});
 displayOptions();applyForces();build();g.resetPan();g.zoomTo(.6);
})();
