
'use strict';
const data=JSON.parse(document.getElementById('snapshot').textContent);
const $=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text);if(cls)el.className=cls;return el};
const shell=$('section',undefined,'hg-shell');document.getElementById('app').append(shell);
const presentation=new WeakMap();if(data.graph){for(const key of ['nodes','hyperedges'])for(const r of data.graph[key])presentation.set(r,data.display?.[key]?.[r.id]);}
const display=r=>presentation.get(r),label=r=>display(r)?.label||(typeof r.label==='object'?['zh','en'].map(k=>r.label?.[k]).find(v=>typeof v==='string'&&v)||String(r.id):typeof r.label==='string'&&r.label?r.label:String(r.id));
const stateText=r=>display(r)?.status_text||({SUPPORTED:'声明支持',UNKNOWN:'待确认',CONTRADICTED:'声明反驳',PROPOSED:'候选'}[r.status]||'待确认');
const relation=r=>String(r.relation||r.kind||r.type||'依赖关系');
function button(text,title,action,cls='hg-button'){const b=$('button',text,cls);b.type='button';b.title=title;b.setAttribute('aria-label',title);b.addEventListener('click',action);return b}
if(data.status!=='AVAILABLE'||!data.graph?.nodes.length){
 const empty=$('div',undefined,'hg-empty');empty.append($('h2',data.status==='DISPLAY_LIMIT'?'图超过当前显示上限':data.status==='UNAVAILABLE'?'超图不可用':'尚无超图'));
 empty.append($('p',data.reason||(data.status==='DISPLAY_LIMIT'?'最多 4,096 个主张、8,192 条超边、32,768 条关联。未截断原图。':'使用 --hypergraph 指定依赖文件，或读取项目中保存的 TMS 快照。')));shell.append(empty);
}else startGraph();

function startGraph(){
 const graph=data.graph,items=[],byKey=new Map(),links=[],incoming=new Map(),neighbors=[];
 const add=(record,kind)=>{const item={record,kind,key:(kind==='node'?'n:':'e:')+record.id,index:items.length,degree:0};byKey.set(item.key,item);items.push(item);neighbors.push([]);incoming.set(item.index,[])};
 graph.nodes.forEach(r=>add(r,'node'));graph.hyperedges.forEach(r=>add(r,'edge'));
 const link=(a,b,rule)=>{links.push({a,b,rule});items[a].degree++;items[b].degree++;neighbors[a].push(b);neighbors[b].push(a);incoming.get(b).push(a)};
 for(const edge of graph.hyperedges){const e=byKey.get('e:'+edge.id).index;for(const p of edge.premises)link(byKey.get('n:'+p).index,e,edge);link(e,byKey.get('n:'+edge.conclusion).index,edge)}
 const n=items.length,positions=new Float32Array(n*2);
 const ranked=items.map((_,i)=>i).sort((a,b)=>items[b].degree-items[a].degree||a-b);
 ranked.forEach((i,rank)=>{const angle=rank*2.39996323,r=Math.sqrt(rank)*24;positions[i*2]=Math.cos(angle)*r;positions[i*2+1]=Math.sin(angle)*r});
 // Seed connected neighborhoods near degree hubs; the worker then relaxes the
 // actual incidence graph. This is placement only, never a inferred relation.
 const hubs=ranked.filter(i=>items[i].kind==='node'&&items[i].degree>=12).slice(0,48),hubSet=new Set(hubs),groupCounts=new Map(hubs.map(i=>[i,0]));
 hubs.forEach((i,rank)=>{const angle=rank*2.39996323,r=130*Math.sqrt(rank);positions[i*2]=Math.cos(angle)*r;positions[i*2+1]=Math.sin(angle)*r});
 for(const item of items){if(item.kind!=='node'||hubSet.has(item.index))continue;const candidates=new Set();for(const e of neighbors[item.index])for(const i of neighbors[e])if(hubSet.has(i))candidates.add(i);if(!candidates.size)continue;const hub=[...candidates].sort((a,b)=>items[b].degree-items[a].degree||a-b)[0],rank=groupCounts.get(hub)+1;groupCounts.set(hub,rank);const angle=rank*2.39996323,r=22*Math.sqrt(rank);positions[item.index*2]=positions[hub*2]+Math.cos(angle)*r;positions[item.index*2+1]=positions[hub*2+1]+Math.sin(angle)*r}
 // Distinct OR routes remain separate display junctions, including after
 // worker updates. Group by endpoints, then offset by stable edge identity.
 const parallelGroups=new Map(),junctionOffsets=new Map();
 for(const item of items){if(item.kind!=='edge'||!item.record.premises.length)continue;const key=JSON.stringify([[...item.record.premises].sort(),item.record.conclusion]);if(!parallelGroups.has(key))parallelGroups.set(key,[]);parallelGroups.get(key).push(item);}
 for(const group of parallelGroups.values()){if(group.length<2)continue;group.sort((a,b)=>a.key<b.key?-1:a.key>b.key?1:0);group.forEach((item,rank)=>{const angle=rank*2.39996323,r=24*Math.sqrt(rank+1);junctionOffsets.set(item.index,[r*Math.cos(angle),r*Math.sin(angle)]);});}
 function placeJunctions(){for(const item of items){if(item.kind!=='edge'||!neighbors[item.index].length)continue;if(!item.record.premises.length){const target=byKey.get('n:'+item.record.conclusion).index,angle=(item.index+1)*2.39996323;positions[item.index*2]=positions[target*2]+45*Math.cos(angle);positions[item.index*2+1]=positions[target*2+1]+45*Math.sin(angle);}else for(let axis=0;axis<2;axis++)positions[item.index*2+axis]=neighbors[item.index].reduce((sum,i)=>sum+positions[i*2+axis],0)/neighbors[item.index].length+(junctionOffsets.get(item.index)?.[axis]||0);}}
 placeJunctions();
 let selected=null,hovered=null,focus=false,colors=false,light=false,view={x:0,y:0,k:.8},width=1,height=1,ratio=1;
 let edgeMode='relation',framePending=false,worker=null,workerURL=null,layoutRunning=false,userMoved=false,drag=null,query='',matches=new Set(),visibleSet=null,dynamic=true,pausedByVisibility=false;
 let drawCount=0,drawTotal=0,layoutTime=0,layoutTicks=0,bounds={x:-1,y:-1,w:2,h:2},lastMini=0,hoverFrame=false,pointer=null,nearSet=new Set();
 const drawTimes=[],hitGrid=new Map(),targets=positions.slice(),options={center:.08,repulsion:700,spring:.08,length:65,labels:1.2,nodeSize:1,nodeBorder:.3,edgeWidth:1};let interpolating=false,lastDrawTime=0;
 const starPalette=['#c3d8ff','#e1eaff','#f8f1df','#f3d5ab','#f8bd85','#a9c8f4'];let nodeColorMode='types',customNodeColor='#d7e4ff';
 const pastels=['#a9c8ec','#d4c0e8','#b3d8cd','#e5c4b0','#cad4a4','#d3bfce'];
 const relations=new Map([...new Set(graph.hyperedges.map(relation))].map((type,i)=>[type,{style:['solid','dashed','dotted','dashdot'][i%4],color:pastels[i%pastels.length],width:.65,mode:/竞争|冲突|反驳|contradict|conflict|excludes/i.test(type)?'repel':'attract',strength:type==='主题关联'?.55:1,distance:/竞争|冲突|反驳|contradict|conflict|excludes/i.test(type)?180:65}]));
 const canvas=$('canvas');canvas.id='hg-canvas';canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','可交互研究超图。方向键平移，加减号缩放，0 适应全图。可通过搜索选择节点。');shell.append(canvas);const ctx=canvas.getContext('2d');
 const top=$('div',undefined,'hg-top'),title=$('h1',undefined,'hg-title');title.append($('span','RDS'),data.readable?.graph?.title?.trim()||'研究超图');top.append(title);
 function diagnostics(parent,info){if(!info?.count)return;parent.append($('h4','来源待核对'));for(const item of info.items)parent.append($('p',item.message,'source-diagnostic'));if(info.omitted)parent.append($('p',`另有 ${info.omitted} 项待核对`,'hg-hint'));}
 if(data.readable?.graph?.summary?.trim()||data.display?.diagnostics?.count){const info=button('ⓘ','研究说明',()=>{detail.replaceChildren();const head=$('header');head.append($('strong','研究说明'),button('×','关闭详情',()=>{detail.hidden=true},'hg-close'));detail.append(head,$('h3',data.readable?.graph?.title?.trim()||'研究超图'));if(data.readable?.graph?.summary?.trim())detail.append($('p',data.readable.graph.summary.trim(),'readable-summary'));diagnostics(detail,data.display?.diagnostics);detail.hidden=false;settings.hidden=results.hidden=true;},'hg-close');info.style.pointerEvents='auto';title.append(info);}
  const counts=$('div',`${graph.nodes.length.toLocaleString()} 个节点  ·  ${graph.hyperedges.length.toLocaleString()} 条超边  ·  ${links.length.toLocaleString()} 条连接`,'hg-counts');if(data.demo)counts.append($('span','合成示例','hg-demo'));top.append(counts);shell.append(top);
  const analysisNote=$('div',data.analysis_notice||'阻断条件分析状态未知。','hg-counts');analysisNote.id='analysis-note';analysisNote.setAttribute('role','note');top.append(analysisNote);
 const actions=$('div',undefined,'hg-actions'),searchWrap=$('div',undefined,'hg-search-wrap'),search=$('input',undefined,'hg-search'),results=$('div',undefined,'hg-results');search.id='hg-search';search.type='search';search.placeholder='搜索节点或关系…';search.setAttribute('aria-label','搜索节点或关系');results.hidden=true;results.id='hg-search-results';searchWrap.append(search,results);actions.append(searchWrap);
 const settings=$('aside',undefined,'hg-panel'),detail=$('aside',undefined,'hg-panel');settings.id='hg-settings';detail.id='hg-detail';settings.hidden=detail.hidden=true;settings.setAttribute('aria-label','图谱设置');detail.setAttribute('aria-label','节点与关系详情');
 const settingsButton=button('☷','图谱设置',()=>{settings.hidden=!settings.hidden;detail.hidden=true;results.hidden=true});settingsButton.id='hg-settings-toggle';actions.append(settingsButton);shell.append(actions,settings,detail);
 const sh=$('header');sh.append($('strong','图谱设置'),button('×','关闭设置',()=>settings.hidden=true,'hg-close'));settings.append(sh);
 function selectControl(caption,choices,value,action,id){const lab=$('label',caption),select=$('select');select.id=id;lab.htmlFor=id;for(const [key,name]of choices){const opt=$('option',name);opt.value=key;select.append(opt)}select.value=value;select.addEventListener('change',()=>action(select.value));settings.append(lab,select);return select}
 const typeControls=$('div');
 selectControl('连线样式',[['uniform','统一细线'],['status','按声明状态'],['relation','按关系类型']],'relation',value=>{edgeMode=value;requestDraw()},'hg-edge-mode');
 const legend=$('p','实线：声明支持 · 虚线：候选 · 点线：已反驳','hg-hint');settings.append(legend);
 settings.append($('p','声明 ○ · 执行 □ · 回执 ◎ · 产物 ⬡ · 观测 ○ · 超边 ◇','hg-hint'),$('p','绿：成功 / 支持 · 红：失败 / 反驳 · 灰：未判定','hg-hint'));
 const relationNames=[...relations.keys()],relationPager=$('div',undefined,'hg-row'),relationPageText=$('span');let relationPage=0;
 const previousRelations=button('上一页','上一页关系设置',()=>{relationPage--;renderRelationControls()}),nextRelations=button('下一页','下一页关系设置',()=>{relationPage++;renderRelationControls()});relationPager.append(previousRelations,relationPageText,nextRelations);settings.append(relationPager);
 function renderRelationControls(){const start=relationPage*32;typeControls.replaceChildren();previousRelations.disabled=start===0;nextRelations.disabled=start+32>=relationNames.length;relationPageText.textContent=`${start+1}–${Math.min(start+32,relationNames.length)} / ${relationNames.length}`;
 for(const type of relationNames.slice(start,start+32)){const config=relations.get(type),box=$('details',undefined,'hg-type'),summary=$('summary'),swatch=$('i',undefined,'hg-swatch');swatch.style.background=config.color;summary.append(swatch,type);box.append(summary);
  function row(caption,input){const r=$('div',undefined,'hg-row'),lab=$('label',caption);input.setAttribute('aria-label',type+' · '+caption);r.append(lab,input);box.append(r)}
  function choice(caption,key,values,physics){const select=$('select');for(const [value,name]of values){const opt=$('option',name);opt.value=value;select.append(opt)}select.value=config[key];select.addEventListener('change',()=>{config[key]=select.value;if(physics)runLayout(false);else requestDraw()});row(caption,select)}
  choice('线型','style',[['solid','实线'],['dashed','虚线'],['dotted','点线'],['dashdot','点划线']],false);
  choice('力学作用','mode',[['attract','吸引'],['repel','排斥'],['none','无作用']],true);
  const color=$('input');color.type='color';color.value=config.color;color.addEventListener('input',()=>{config.color=color.value;swatch.style.background=color.value;requestDraw()});row('颜色',color);
  for(const [caption,key,min,max,step,physics]of [['线宽','width',.2,3,.1,false],['力度','strength',0,3,.1,true],['作用距离','distance',20,350,5,true]]){const lab=$('label',caption+' · '+config[key]),input=$('input');input.type='range';input.min=min;input.max=max;input.step=step;input.value=config[key];input.setAttribute('aria-label',type+' · '+caption);input.addEventListener('input',()=>{config[key]=Number(input.value);lab.textContent=caption+' · '+input.value;if(!physics)requestDraw()});input.addEventListener('change',()=>{if(physics)runLayout(false)});box.append(lab,input)}
  typeControls.append(box)
 }}renderRelationControls();settings.append(typeControls);
 function checkbox(caption,action,id){const row=$('div',undefined,'hg-row'),lab=$('label',caption),input=$('input');input.type='checkbox';input.id=id;lab.htmlFor=id;input.addEventListener('change',()=>action(input.checked));row.append(lab,input);settings.append(row)}
 checkbox('按声明状态着色',value=>{colors=value;requestDraw()},'hg-color-status');
 checkbox('浅色背景',value=>{light=value;shell.classList.toggle('light',light);requestDraw()},'hg-light');
 selectControl('节点配色',[['types','按记录类型'],['stars','星系：蓝白 / 暖白 / 金橙'],['single','统一自选颜色']],'types',value=>{nodeColorMode=value;requestDraw()},'hg-node-colors');
 const nodeColor=$('input');nodeColor.type='color';nodeColor.value=customNodeColor;nodeColor.setAttribute('aria-label','自选节点颜色');nodeColor.addEventListener('input',()=>{customNodeColor=nodeColor.value;nodeColorMode='single';document.getElementById('hg-node-colors').value='single';requestDraw()});settings.append(nodeColor);
 const forceBox=$('details'),forceTitle=$('summary','力度与外观');forceBox.append(forceTitle);settings.append(forceBox);
 function slider(caption,key,min,max,step,layout){const lab=$('label'),text=$('span',caption),out=$('output',options[key]);lab.append(text,' · ',out);const input=$('input');input.type='range';input.id='hg-'+key;input.min=min;input.max=max;input.step=step;input.value=options[key];lab.htmlFor=input.id;input.addEventListener('input',()=>{options[key]=Number(input.value);out.textContent=input.value;if(!layout)requestDraw()});input.addEventListener('change',()=>{if(layout)runLayout()});forceBox.append(lab,input)}
 slider('图谱向心力','center',.01,.3,.01,true);slider('节点排斥力','repulsion',100,2200,50,true);slider('连接作用倍率','spring',.01,.2,.01,true);slider('整体距离倍率','length',15,160,5,true);slider('标签显示阈值','labels',.3,3,.1,false);slider('节点大小','nodeSize',.5,2,.1,false);slider('节点轮廓粗细','nodeBorder',0,2,.1,false);slider('全局线宽倍率','edgeWidth',.3,3,.1,false);
 const layoutButton=button('重新排布','重新排布',()=>runLayout(true));layoutButton.id='hg-relayout';settings.append(layoutButton);
 const controls=$('div',undefined,'hg-controls'),zoomText=$('output','100%');
 const zoomOut=button('−','缩小',()=>zoom(.8)),zoomIn=button('+','放大',()=>zoom(1.25)),fitButton=button('⊡','适应全图',()=>{userMoved=false;fit()}),focusButton=button('◎','聚焦选中节点的上游依赖',()=>{focus=!focus;focusButton.setAttribute('aria-pressed',String(focus));updateFocus();requestDraw()});
 const pauseButton=button('Ⅱ','暂停或继续布局',()=>{dynamic=!dynamic;pauseButton.textContent=dynamic?'Ⅱ':'▷';pauseButton.setAttribute('aria-pressed',String(!dynamic));if(dynamic)runLayout(false);else{stopLayout();targets.set(positions);interpolating=false;canvas.dataset.layoutState='paused'}requestDraw()});pauseButton.id='hg-pause';pauseButton.setAttribute('aria-pressed','false');
 zoomIn.id='hg-zoom-in';zoomOut.id='hg-zoom-out';fitButton.id='hg-fit';focusButton.id='hg-focus';focusButton.setAttribute('aria-pressed','false');controls.append(zoomOut,zoomText,zoomIn,fitButton,focusButton,pauseButton);shell.append(controls);
 const help=$('div','拖动 · 缩放 · 点击详查','hg-help');shell.append(help);
 const minimap=$('canvas',undefined,'hg-minimap');minimap.id='hg-minimap';minimap.width=312;minimap.height=204;minimap.setAttribute('aria-label','全图位置小地图');shell.append(minimap);const mini=minimap.getContext('2d');
 const tip=$('div',undefined,'hg-tip');tip.hidden=true;shell.append(tip);const diagnostic=$('div',undefined,'hg-status');diagnostic.id='hg-performance';shell.append(diagnostic);
 function updateBounds(){if(!n){bounds={x:-45,y:-45,w:90,h:90};return}let x=Infinity,y=Infinity,x2=-Infinity,y2=-Infinity;for(let i=0;i<n;i++){x=Math.min(x,positions[2*i]);y=Math.min(y,positions[2*i+1]);x2=Math.max(x2,positions[2*i]);y2=Math.max(y2,positions[2*i+1])}bounds={x:x-45,y:y-45,w:Math.max(90,x2-x+90),h:Math.max(90,y2-y+90)}}
 function fit(){updateBounds();view.x=bounds.x+bounds.w/2;view.y=bounds.y+bounds.h/2;view.k=Math.max(.025,Math.min(2,(width-90)/bounds.w,(height-145)/bounds.h));requestDraw()}
 function resize(){const box=canvas.getBoundingClientRect();width=Math.max(1,box.width);height=Math.max(1,box.height);ratio=Math.min(devicePixelRatio||1,1.25);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);if(!userMoved)fit();else requestDraw()}
 const observer=new ResizeObserver(resize);observer.observe(canvas);
 function world(x,y){return {x:(x-width/2)/view.k+view.x,y:(y-height/2)/view.k+view.y}}
 function zoom(factor,x=width/2,y=height/2){const anchor=world(x,y);view.k=Math.max(.02,Math.min(8,view.k*factor));view.x=anchor.x-(x-width/2)/view.k;view.y=anchor.y-(y-height/2)/view.k;userMoved=true;requestDraw()}
 function updateFocus(){visibleSet=null;if(!focus||selected===null)return;visibleSet=new Set([selected]);const queue=[selected];for(let k=0;k<queue.length;k++)for(const i of incoming.get(queue[k]))if(!visibleSet.has(i)){visibleSet.add(i);queue.push(i)}}
 function color(item){if(!colors)return nodeColorMode==='single'?customNodeColor:nodeColorMode==='types'&&display(item.record)?display(item.record).color:(item.kind==='edge'?(light?'#97979f':'#77859e'):(light?'#64738a':starPalette[(item.index*7+item.record.id.length)%starPalette.length]));return {SUPPORTED:'#a6d4be',UNKNOWN:'#d6c5a1',CONTRADICTED:'#dfabb8',PROPOSED:'#beb2de'}[item.record.status]||'#aaa'}
 function nodePath(shape,x,y,r){if(shape==='diamond'){ctx.moveTo(x,y-r);ctx.lineTo(x+r,y);ctx.lineTo(x,y+r);ctx.lineTo(x-r,y);ctx.closePath()}else if(shape==='square'){ctx.rect(x-r*.82,y-r*.82,r*1.64,r*1.64)}else if(shape==='triangle'||shape==='hexagon'){const points=shape==='triangle'?[[0,-1],[.95,.75],[-.95,.75]]:[[1,0],[.5,.87],[-.5,.87],[-1,0],[-.5,-.87],[.5,-.87]];points.forEach(([a,b],i)=>i?ctx.lineTo(x+a*r,y+b*r):ctx.moveTo(x+a*r,y+b*r));ctx.closePath()}else{ctx.moveTo(x+r,y);ctx.arc(x,y,r,0,Math.PI*2)}}
 const dashes={solid:[],dashed:[6,5],dotted:[1,4],dashdot:[7,3,1,3]};
 function style(edge){return edgeMode==='uniform'?'solid':edgeMode==='relation'?relations.get(relation(edge)).style:{SUPPORTED:'solid',PROPOSED:'dashed',CONTRADICTED:'dotted'}[edge.status]||'dashdot'}
 function faded(i){return (visibleSet&&!visibleSet.has(i))||(query&&!matches.has(i)&&i!==selected)}
 function radius(item){return (item.kind==='edge'?1.9:Math.min(19,3+Math.sqrt(item.degree)*1.35))*options.nodeSize}
 function requestDraw(){if(framePending)return;framePending=true;requestAnimationFrame(draw)}
 function draw(){
  framePending=false;const started=performance.now();ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
  if(interpolating){const fraction=1-Math.exp(-Math.min(50,started-lastDrawTime||16)/45);let movement=0;for(let i=0;i<graph.nodes.length*2;i++){const delta=targets[i]-positions[i];movement=Math.max(movement,Math.abs(delta));positions[i]+=delta*fraction}interpolating=movement>.02;placeJunctions();if(interpolating)requestDraw()}lastDrawTime=started;
  const sx=x=>(x-view.x)*view.k+width/2,sy=y=>(y-view.y)*view.k+height/2;
  const activeIndex=hovered??selected;nearSet=new Set(activeIndex===null?[]:[activeIndex,...neighbors[activeIndex]]);
  const groups=new Map();for(const l of links){const near=nearSet.has(l.a)&&nearSet.has(l.b),dim=faded(l.a)||faded(l.b),config=relations.get(relation(l.rule)),tone=dim?'dim':near?'near':'normal';const key=style(l.rule)+':'+tone+':'+config.color+':'+config.width;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(l)}
  for(const [key,group]of groups){const [line,tone,edgeColor,edgeWidth]=key.split(':');ctx.strokeStyle=edgeMode==='uniform'?(light?'#969cac':'#9dabc1'):edgeColor;ctx.globalAlpha=tone==='dim'?.045:tone==='near'?.9:light?.4:.22;ctx.lineWidth=Number(edgeWidth)*options.edgeWidth*(tone==='near'?1.6:1);ctx.setLineDash(dashes[line]);ctx.beginPath();for(const l of group){const ax=sx(positions[l.a*2]),ay=sy(positions[l.a*2+1]),bx=sx(positions[l.b*2]),by=sy(positions[l.b*2+1]);if(Math.max(ax,bx)<-5||Math.min(ax,bx)>width+5||Math.max(ay,by)<-5||Math.min(ay,by)>height+5)continue;ctx.moveTo(ax,ay);ctx.lineTo(bx,by)}ctx.stroke()}
  ctx.setLineDash([]);ctx.font='11px "Segoe UI","Microsoft YaHei",sans-serif';ctx.textAlign='center';ctx.textBaseline='top';let drawn=0;const labelCells=new Set(),nodeBatches=new Map(),labels=[];hitGrid.clear();
  for(const item of items){const i=item.index,x=sx(positions[i*2]),y=sy(positions[i*2+1]),r=Math.max(item.kind==='edge'?.7:1.2,radius(item)*Math.sqrt(view.k));if(x<-40||y<-30||x>width+40||y>height+30)continue;drawn++;const active=i===selected||i===hovered,dim=faded(i),c=active?(light?'#8a75af':'#f1e5ff'):color(item),shape=display(item.record)?.shape||(item.kind==='edge'?'diamond':'circle'),outline=display(item.record)?.outline.color||'',key=[c,item.kind,dim?'dim':'normal',shape,outline].join(':');if(!nodeBatches.has(key))nodeBatches.set(key,[]);nodeBatches.get(key).push({x,y,r,active});
   const cell=Math.floor(x/40)+','+Math.floor(y/40);if(!hitGrid.has(cell))hitGrid.set(cell,[]);hitGrid.get(cell).push({x,y,r:Math.max(6,r+3),i});
   if(!dim&&item.kind==='node'&&item.degree>15&&!light){const glow=ctx.createRadialGradient(x,y,0,x,y,r*3.4);glow.addColorStop(0,c+'70');glow.addColorStop(1,c+'00');ctx.globalAlpha=.7;ctx.fillStyle=glow;ctx.fillRect(x-r*3.4,y-r*3.4,r*6.8,r*6.8)}
   if(!dim&&(active||matches.has(i)&&query||item.kind==='node'&&(view.k>=options.labels||item.degree>15&&view.k>.55)))labels.push({item,x,y,r,active});
  }
  for(const [key,batch]of nodeBatches){const [c,kind,tone,shape,outline]=key.split(':');ctx.globalAlpha=tone==='dim'?.10:1;ctx.fillStyle=ctx.strokeStyle=c;ctx.lineWidth=shape==='ring'?Math.max(1,options.nodeBorder):kind==='edge'?.6:options.nodeBorder;ctx.beginPath();for(const {x,y,r}of batch)nodePath(shape,x,y,r);if(kind==='node'&&shape!=='ring')ctx.fill();if(shape==='ring'||kind==='edge'||options.nodeBorder>0)ctx.stroke();if(outline){ctx.strokeStyle=outline;ctx.lineWidth=1.6;ctx.beginPath();for(const {x,y,r}of batch)nodePath(shape,x,y,r+2);ctx.stroke()}ctx.strokeStyle=c;ctx.beginPath();for(const {x,y,r,active}of batch)if(active){ctx.moveTo(x+r+5,y);ctx.arc(x,y,r+5,0,Math.PI*2)}ctx.lineWidth=1;ctx.stroke()}
  let labelCount=0;for(const {item,x,y,r,active}of labels){if(labelCount>250&&!active)continue;const text=label(item.record),shown=text.length>36?text.slice(0,35)+'…':text,tw=ctx.measureText(shown).width,keys=[];for(let gx=Math.floor((x-tw/2)/45);gx<=Math.floor((x+tw/2)/45);gx++)for(let gy=Math.floor((y+r+5)/15);gy<=Math.floor((y+r+18)/15);gy++)keys.push(gx+','+gy);if(active||!keys.some(key=>labelCells.has(key))){keys.forEach(key=>labelCells.add(key));ctx.fillStyle=light?'#494951':'#d1dae8';ctx.globalAlpha=.95;ctx.fillText(shown,x,y+r+5);labelCount++}}
  ctx.globalAlpha=1;zoomText.value=Math.round(view.k*100)+'%';zoomText.textContent=zoomText.value;
  const now=performance.now();if(now-lastMini>350){drawMini();lastMini=now}const cost=performance.now()-started;drawCount++;drawTotal+=cost;drawTimes.push(cost);if(drawTimes.length>120)drawTimes.shift();canvas.dataset.drawMs=(drawTotal/drawCount).toFixed(2);canvas.dataset.visibleElements=drawn;canvas.dataset.totalElements=n;canvas.dataset.scale=view.k.toFixed(4);canvas.dataset.center=view.x.toFixed(2)+','+view.y.toFixed(2);
  if(drawCount%15===1||!layoutRunning){canvas.dataset.drawP95=[...drawTimes].sort((a,b)=>a-b)[Math.floor((drawTimes.length-1)*.95)].toFixed(2);diagnostic.textContent=(canvas.dataset.layoutState==='unavailable'?'布局线程不可用；显示初始位置，仍可浏览。':dynamic?(layoutRunning?'自然收敛中':'已稳定'):'已暂停')+` · ${n.toLocaleString()} 元素 · ${links.length.toLocaleString()} 连接`}
 }
 function drawMini(){const w=minimap.width,h=minimap.height;mini.clearRect(0,0,w,h);const k=Math.min((w-16)/bounds.w,(h-16)/bounds.h),ox=(w-bounds.w*k)/2,oy=(h-bounds.h*k)/2;mini.fillStyle=light?'#898990':'#98989f';mini.globalAlpha=.7;for(const item of items){if(item.kind==='edge')continue;mini.fillRect(ox+(positions[item.index*2]-bounds.x)*k,oy+(positions[item.index*2+1]-bounds.y)*k,2,2)}mini.globalAlpha=1;mini.strokeStyle='#aa99cf';mini.lineWidth=1.5;mini.strokeRect(ox+(view.x-width/(2*view.k)-bounds.x)*k,oy+(view.y-height/(2*view.k)-bounds.y)*k,width/view.k*k,height/view.k*k)}
 minimap.addEventListener('pointerdown',e=>{const box=minimap.getBoundingClientRect(),w=minimap.width,h=minimap.height,k=Math.min((w-16)/bounds.w,(h-16)/bounds.h);view.x=bounds.x+((e.clientX-box.left)*w/box.width-(w-bounds.w*k)/2)/k;view.y=bounds.y+((e.clientY-box.top)*h/box.height-(h-bounds.h*k)/2)/k;userMoved=true;requestDraw()});
 function inspect(i,center=false){selected=i;const item=items[i],r=item.record;updateFocus();detail.replaceChildren();const head=$('header');head.append($('strong',item.kind==='edge'?'超边详情':'节点详情'),button('×','关闭详情',()=>{detail.hidden=true;selected=null;updateFocus();requestDraw()},'hg-close'));detail.append(head,$('h3',label(r)),$('span',display(r)?.kind||(item.kind==='edge'?'超边':'声明'),'hg-tag'),$('span',stateText(r),'hg-tag'));
  if(display(r)?.summary)detail.append($('p',display(r).summary,'readable-summary'));
  function linked(title,indices){if(!indices.length)return;detail.append($('h4',title));const ul=$('ul');for(const target of indices.slice(0,150)){const li=$('li'),b=button(label(items[target].record),'查看 '+label(items[target].record),()=>inspect(target,true),'hg-close');b.style.fontSize='12px';b.style.lineHeight='1.6';li.append(b);ul.append(li)}detail.append(ul);if(indices.length>150)detail.append($('p',`前 150 项，共 ${indices.length} 项`,'hg-hint'))}
  const info=display(r)?.diagnostics;diagnostics(detail,info);for(const issue of info?.items||[]){linked('候选来源',issue.candidates.map(id=>byKey.get('n:'+id)?.index).filter(index=>index!==undefined));if(issue.omitted_candidates)detail.append($('p',`另有 ${issue.omitted_candidates} 个候选`,'hg-hint'));}
  if(item.kind==='node'){linked('推导路线 · OR',incoming.get(i));linked('关联节点',neighbors[i].filter(target=>!incoming.get(i).includes(target)));}
  else{linked('共同前提 · AND',r.premises.map(id=>byKey.get('n:'+id).index));linked('结论',[byKey.get('n:'+r.conclusion).index]);}
  detail.hidden=false;settings.hidden=true;results.hidden=true;
  if(center){view.x=positions[i*2];view.y=positions[i*2+1];view.k=Math.max(view.k,1.3);userMoved=true}requestDraw();
 }
 function searchNodes(){query=search.value.trim().toLocaleLowerCase();matches=new Set();results.replaceChildren();if(!query){results.hidden=true;requestDraw();return}for(const item of items)if((label(item.record)+' '+(display(item.record)?.summary||'')+' '+item.record.id+' '+relation(item.record)).toLocaleLowerCase().includes(query))matches.add(item.index);results.append($('div',`${matches.size} 项匹配${matches.size>40?' · 显示前 40 项':''}`,'hg-result-count'));for(const i of [...matches].slice(0,40)){const item=items[i],b=button(label(item.record),'查看 '+label(item.record),()=>inspect(i,true),'');b.append($('small',(display(item.record)?.kind||(item.kind==='node'?'声明':'超边'))+' · '+stateText(item.record)));results.append(b)}results.hidden=false;requestDraw()}
 search.addEventListener('input',searchNodes);search.addEventListener('keydown',e=>{if(e.key==='Escape'){search.value='';searchNodes()}if(e.key==='Enter'&&matches.size)inspect(matches.values().next().value,true)});
 function hit(x,y){let best=null,distance=Infinity;const gx=Math.floor(x/40),gy=Math.floor(y/40);for(let dx=-2;dx<=2;dx++)for(let dy=-2;dy<=2;dy++)for(const p of hitGrid.get((gx+dx)+','+(gy+dy))||[]){const d=(p.x-x)**2+(p.y-y)**2;if(d<p.r*p.r&&d<distance){best=p.i;distance=d}}return best}
 canvas.addEventListener('wheel',e=>{e.preventDefault();const b=canvas.getBoundingClientRect();zoom(Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.0025),e.clientX-b.left,e.clientY-b.top)},{passive:false});
 canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;const b=canvas.getBoundingClientRect(),x=e.clientX-b.left,y=e.clientY-b.top;drag={id:hit(x,y),x:e.clientX,y:e.clientY,vx:view.x,vy:view.y,moved:false};canvas.setPointerCapture(e.pointerId);results.hidden=true});
 canvas.addEventListener('pointermove',e=>{const b=canvas.getBoundingClientRect(),x=e.clientX-b.left,y=e.clientY-b.top;if(drag){const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>4){drag.moved=true;userMoved=true;view.x=drag.vx-dx/view.k;view.y=drag.vy-dy/view.k;requestDraw()}tip.hidden=true;return}pointer={x,y};if(hoverFrame)return;hoverFrame=true;requestAnimationFrame(()=>{hoverFrame=false;const {x,y}=pointer,next=hit(x,y);if(next!==hovered){hovered=next;requestDraw()}tip.hidden=next===null;if(next!==null){const r=items[next].record;tip.textContent=label(r)+'\n'+stateText(r)+(display(r)?.summary?'\n'+display(r).summary.slice(0,140):'');tip.style.left=Math.max(10,Math.min(width-285,x+14))+'px';tip.style.top=Math.min(height-50,y+14)+'px'}})});
 canvas.addEventListener('pointerup',()=>{if(drag&&!drag.moved&&drag.id!==null)inspect(drag.id);drag=null});canvas.addEventListener('pointercancel',()=>drag=null);canvas.addEventListener('lostpointercapture',()=>drag=null);canvas.addEventListener('pointerleave',()=>{tip.hidden=true;hovered=null;requestDraw()});
 canvas.addEventListener('keydown',e=>{const delta={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];if(delta){e.preventDefault();view.x+=delta[0]*60/view.k;view.y+=delta[1]*60/view.k;userMoved=true;requestDraw()}else if(['+','=','-','0'].includes(e.key)){e.preventDefault();if(e.key==='0'){userMoved=false;fit()}else zoom(e.key==='-'?.8:1.25)}else if(e.key==='Escape'){detail.hidden=settings.hidden=results.hidden=true;selected=null;focus=false;focusButton.setAttribute('aria-pressed','false');updateFocus();requestDraw()}});
 function stopLayout(){if(worker){worker.terminate();worker=null}if(workerURL){URL.revokeObjectURL(workerURL);workerURL=null}layoutRunning=false}
 function forceWorker(){
  onmessage=event=>{
   const {positions:p,links,degree,options:o,dynamic,warm}=event.data,start=performance.now();let ticks=0;
   const nodes=Array.from({length:p.length/2},(_,i)=>({index:i,x:p[i*2],y:p[i*2+1]})),attractive=[],repulsive=[];
   for(let k=0;k<links.length;k+=5){const edge={source:links[k],target:links[k+1],strength:links[k+2],distance:links[k+3]*o.length/65};if(links[k+4]>0)attractive.push(edge);else if(links[k+4]<0)repulsive.push(edge)}
   // D3 provides quadtree charge/collision and a damped, cooling integrator.
   // Hyperedge junctions remain derived drawing geometry, not extra particles.
   const simulation=d3.forceSimulation(nodes).stop().alpha(warm?.8:.3)
    .alphaDecay(1-Math.pow(.001,1/300)).alphaMin(.001).velocityDecay(.4)
    .force('charge',d3.forceManyBody().strength(d=>-o.repulsion/20*Math.min(4,Math.sqrt(degree[d.index]+1))).distanceMin(18).distanceMax(700))
    .force('collide',d3.forceCollide(d=>6+Math.min(18,Math.sqrt(degree[d.index]+1)*1.4)).strength(.65))
    .force('x',d3.forceX(0).strength(o.center*.05)).force('y',d3.forceY(0).strength(o.center*.05))
    .force('links',d3.forceLink(attractive).distance(d=>d.distance).strength(d=>o.spring*d.strength))
    .force('signedRepulsion',alpha=>{for(const edge of repulsive){
     const a=nodes[edge.source],b=nodes[edge.target];let dx=b.x+b.vx-a.x-a.vx,dy=b.y+b.vy-a.y-a.vy;
     if(dx===0&&dy===0){dx=.01;dy=.01}const distance=Math.max(.01,Math.hypot(dx,dy));if(distance>=edge.distance)continue;
     const force=(edge.distance-distance)/distance*o.spring*edge.strength*alpha;
     const wa=1/Math.sqrt(degree[a.index]||1),wb=1/Math.sqrt(degree[b.index]||1);
     a.vx-=dx*force*wa;a.vy-=dy*force*wa;b.vx+=dx*force*wb;b.vy+=dy*force*wb;
    }});
   function step(){simulation.tick();ticks++}
   function send(ready,stepMs=0,done=false){for(let i=0;i<nodes.length;i++){p[i*2]=nodes[i].x;p[i*2+1]=nodes[i].y}const output=p.slice();postMessage({positions:output,ticks,ms:performance.now()-start,stepMs,ready,done,alpha:simulation.alpha()},[output.buffer])}
   if(warm){for(let tick=0;tick<180;tick++){step();if(performance.now()-start>4500)break}}
   send(true);
   if(!dynamic){simulation.stop();close();return}
   function animate(){const before=performance.now();step();const done=simulation.alpha()<simulation.alphaMin();send(false,performance.now()-before,done);if(done){simulation.stop();close();return}setTimeout(animate,Math.max(10,16-(performance.now()-before)))}
   setTimeout(animate,16);
  };
 }
 function runLayout(warm=false){
  stopLayout();layoutRunning=true;
  const physical=[];for(const edge of graph.hyperedges){const config=relations.get(relation(edge)),b=byKey.get('n:'+edge.conclusion).index;for(const p of edge.premises)physical.push(byKey.get('n:'+p).index,b,config.strength/Math.sqrt(Math.max(1,edge.premises.length)),config.distance,{attract:1,repel:-1,none:0}[config.mode])}
  try{workerURL=URL.createObjectURL(new Blob([document.getElementById('d3-worker-library').textContent,';('+forceWorker.toString()+')()'],{type:'text/javascript'}));worker=new Worker(workerURL);worker.onmessage=e=>{targets.set(e.data.positions);interpolating=true;layoutTicks=e.data.ticks;if(e.data.ready){layoutTime=e.data.ms;if(warm){positions.set(e.data.positions);placeJunctions();updateBounds();if(!userMoved)fit()}canvas.dataset.layoutMs=layoutTime.toFixed(2);if(!dynamic)stopLayout()}else if(layoutTicks%30===0)updateBounds();if(e.data.done)stopLayout();canvas.dataset.layoutTicks=layoutTicks;canvas.dataset.physicsStepMs=Number(e.data.stepMs||0).toFixed(2);canvas.dataset.layoutState=e.data.done?'settled':dynamic?'dynamic':'paused';requestDraw()};worker.onerror=()=>{stopLayout();canvas.dataset.layoutState='unavailable';diagnostic.textContent='布局线程不可用；显示初始位置，仍可浏览。'};canvas.dataset.layoutState='running';canvas.dataset.layoutEngine='d3-force 3.0.0';worker.postMessage({positions:positions.slice(0,graph.nodes.length*2),links:Float32Array.from(physical),degree:items.slice(0,graph.nodes.length).map(x=>x.degree),options,dynamic,warm})}
  catch(error){stopLayout();canvas.dataset.layoutState='unavailable';diagnostic.textContent='布局线程不可用；显示初始位置，仍可浏览。';layoutButton.textContent='重新排布'}
 }
 document.addEventListener('visibilitychange',()=>{if(document.hidden){pausedByVisibility=dynamic&&layoutRunning;stopLayout()}else {const resume=pausedByVisibility&&dynamic;pausedByVisibility=false;if(resume)runLayout(false)}});
 window.addEventListener('pagehide',()=>{stopLayout();observer.disconnect()},{once:true});
 updateBounds();resize();runLayout(true);
}
