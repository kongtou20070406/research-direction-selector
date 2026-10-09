/* Complete archive indexes and bounded view physics. No evidence inference. */
'use strict';
const ArchiveCore = (() => {
  const family = (semantic, type) => /snapshot_contains|membership|归属/i.test(semantic+' '+type) ? 'source' : /history|historical|revision|snapshot|历史|版本/i.test(semantic+' '+type) ? 'history' : /source|provenance|record_binding|来源|归属/i.test(semantic+' '+type) ? 'source' : /dependency|depends|premise|conclusion|前提|结论|依赖/i.test(semantic+' '+type) ? 'dependency' : 'other';
  function intersects(a,b,r) {
    let lo=0,hi=1;const dx=b.x-a.x,dy=b.y-a.y;
    for(const [p,q] of [[-dx,a.x-r[0]],[dx,r[2]-a.x],[-dy,a.y-r[1]],[dy,r[3]-a.y]]) {
      if(p===0){if(q<0)return false;continue;}const t=q/p;if(p<0)lo=Math.max(lo,t);else hi=Math.min(hi,t);if(lo>hi)return false;
    }return true;
  }
  class Spatial {
    constructor(points,edges,bounds) {this.points=points;this.edges=edges;this.cell=Math.max(80,Math.max(bounds[2]-bounds[0],bounds[3]-bounds[1])/48);this.ng=new Map();this.eg=new Map();this.occupied=new Set();this.extent=[Infinity,Infinity,-Infinity,-Infinity];this.nc=[];this.ec=[];points.forEach((_,i)=>this.node(i));edges.forEach((_,i)=>this.edge(i));}
    key(x,y){return Math.floor(x/this.cell)+','+Math.floor(y/this.cell);}
    add(map,key,id){if(!map.has(key))map.set(key,new Set());map.get(key).add(id);if(!this.occupied.has(key)){this.occupied.add(key);const [x,y]=key.split(',').map(Number);this.extent[0]=Math.min(this.extent[0],x);this.extent[1]=Math.min(this.extent[1],y);this.extent[2]=Math.max(this.extent[2],x);this.extent[3]=Math.max(this.extent[3],y);}}
    remove(map,key,id){const entries=map.get(key);if(!entries)return;entries.delete(id);if(!entries.size)map.delete(key);if(!this.ng.has(key)&&!this.eg.has(key))this.occupied.delete(key);}
    node(i){if(this.nc[i])this.remove(this.ng,this.nc[i],i);const p=this.points[i],key=this.key(p.x,p.y);this.nc[i]=key;this.add(this.ng,key,i);}
    cells(a,b) {
      const c=this.cell,keys=new Set();let x=Math.floor(a.x/c),y=Math.floor(a.y/c),endX=Math.floor(b.x/c),endY=Math.floor(b.y/c);
      const dx=b.x-a.x,dy=b.y-a.y,sx=Math.sign(dx),sy=Math.sign(dy),tx=dx?c/Math.abs(dx):Infinity,ty=dy?c/Math.abs(dy):Infinity;
      let nx=dx?((sx>0?(x+1)*c:x*c)-a.x)/dx:Infinity,ny=dy?((sy>0?(y+1)*c:y*c)-a.y)/dy:Infinity;
      keys.add(x+','+y);
      while(x!==endX||y!==endY){if(Math.min(nx,ny)>=1-1e-12){keys.add(endX+','+endY);break;}if(Math.abs(nx-ny)<1e-12){keys.add((x+sx)+','+y);keys.add(x+','+(y+sy));x+=sx;y+=sy;nx+=tx;ny+=ty;}else if(nx<ny){x+=sx;nx+=tx;}else{y+=sy;ny+=ty;}keys.add(x+','+y);}return [...keys];
    }
    edge(i){for(const key of this.ec[i]||[])this.remove(this.eg,key,i);const e=this.edges[i];this.ec[i]=this.cells(this.points[e.a],this.points[e.b]);for(const key of this.ec[i])this.add(this.eg,key,i);}
    query(rect) {
      if(!Array.isArray(rect)||rect.length!==4||!rect.every(Number.isFinite)||rect[0]>rect[2]||rect[1]>rect[3])throw Error('Viewport must have finite ordered bounds');
      const ns=new Set(),es=new Set();let cells=0,examinedCells=0;const c=this.cell;
      const x0=Math.max(this.extent[0],Math.floor(rect[0]/c)),y0=Math.max(this.extent[1],Math.floor(rect[1]/c)),x1=Math.min(this.extent[2],Math.floor(rect[2]/c)),y1=Math.min(this.extent[3],Math.floor(rect[3]/c));
      const visit=key=>{cells++;for(const i of this.ng.get(key)||[])ns.add(i);for(const i of this.eg.get(key)||[])es.add(i);};
      // Use the cheaper of a cropped rectangle and the finite occupied set.
      // Extents may conservatively grow after motion; sparse scans stay bounded.
      if(x0<=x1&&y0<=y1&&this.occupied.size){
        if((x1-x0+1)*(y1-y0+1)<=this.occupied.size){for(let dx=0;dx<x1-x0+1;dx++)for(let dy=0;dy<y1-y0+1;dy++){examinedCells++;visit((x0+dx)+','+(y0+dy));}}
        else for(const key of this.occupied){examinedCells++;const [x,y]=key.split(',').map(Number);if(x>=x0&&x<=x1&&y>=y0&&y<=y1)visit(key);}
      }
      const nodes=[...ns].filter(i=>{const p=this.points[i];return p.x>=rect[0]&&p.x<=rect[2]&&p.y>=rect[1]&&p.y<=rect[3];});
      const edges=[...es].filter(i=>{const e=this.edges[i];return intersects(this.points[e.a],this.points[e.b],rect);});
      return {nodes,edges,counters:{cells,examinedCells,occupiedCells:this.occupied.size,nodeCandidates:ns.size,edgeCandidates:es.size,visibleNodes:nodes.length,crossingEdges:edges.length}};
    }
  }
  function create(data,progress=()=>{}) {
    if(data.schema!=='rds-archive-display-v1')throw Error('Unsupported archive display schema');
    const nodes=data.nodes,rawEdges=data.edges,clusters=data.clusters,levels=[],byId=new Map(),search=[];
    const indexStats={spatialBuilds:0,spatialEdgeVisits:0,rawSpatialBuilds:0,rawSpatialEdgeVisits:0};
    const degree=new Uint32Array(nodes.length),counts=new Uint32Array(nodes.length+1);
    rawEdges.forEach(e=>{degree[e[0]]++;degree[e[1]]++;counts[e[0]+1]++;if(e[0]!==e[1])counts[e[1]+1]++;});
    for(let i=1;i<counts.length;i++)counts[i]+=counts[i-1];const adjacency=new Uint32Array(counts.at(-1)),cursor=counts.slice();
    rawEdges.forEach((e,i)=>{adjacency[cursor[e[0]]++]=i;if(e[0]!==e[1])adjacency[cursor[e[1]]++]=i;});
    nodes.forEach((n,i)=>{byId.set(n.id,i);search.push([n.label,n.summary,n.id,n.type,n.status,n.group,n.source_id,n.status==='UNKNOWN'?'未判定':n.status==='CONTRADICTED'?'反驳':n.status==='SUPPORTED'?'声明支持':'',/histor|revision|snapshot/i.test(n.type)?'历史':''].join(' ').toLocaleLowerCase());});
    function ancestor(index,level){let c=nodes[index].parent;while(clusters[c].level>level)c=clusters[c].parent;return c;}
    for(let level=0;level<4;level++) {
      const points=[],map=new Map(),nodeMap=new Uint32Array(nodes.length);
      if(level===3)nodes.forEach((n,i)=>{map.set(i,i);points.push({id:'n'+i,record:n.id,index:i,raw:true,x:n.x,y:n.y,label:n.label,type:n.type,status:n.status,primary:n.primary,degree:n.degree??degree[i],count:1});nodeMap[i]=i;});
      else {
        clusters.forEach((c,i)=>{if(c.level===level){map.set(i,points.length);points.push({id:'c'+i,index:i,raw:false,x:c.x,y:c.y,label:c.label,type:'cluster',count:c.node_count,primary:c.nodes.some(n=>nodes[n].primary),status:'CLUSTER'});}});
        nodes.forEach((_,i)=>nodeMap[i]=map.get(ancestor(i,level)));
        nodes.forEach((n,i)=>{if(n.primary)points[nodeMap[i]].primary=true;});
      }
      const edges=[],agg=new Map();let internal=0;
      rawEdges.forEach((e,i)=>{const a=nodeMap[e[0]],b=nodeMap[e[1]],f=family(data.semantics[e[2]],data.edge_types[e[3]]);if(level!==3&&a===b){internal++;return;}const key=a+':'+b+':'+f;if(level===3){edges.push({a,b,family:f,count:1,index:i});return;}if(agg.has(key)){edges[agg.get(key)].count++;}else{agg.set(key,edges.length);edges.push({a,b,family:f,count:1,index:i});}});
      levels.push({points,edges,spatial:null,internal,nodeMap});if(level<3)ensureSpatial(level);progress({level,points:points.length,edges:edges.length,spatialReady:level<3});
    }
    function ensureSpatial(level){const stage=levels[level];if(!stage.spatial){stage.spatial=new Spatial(stage.points,stage.edges,data.bounds);indexStats.spatialBuilds++;indexStats.spatialEdgeVisits+=stage.edges.length;if(level===3){indexStats.rawSpatialBuilds++;indexStats.rawSpatialEdgeVisits+=stage.edges.length;}progress({level,spatialReady:true,points:stage.points.length,edges:stage.edges.length,indexStats:{...indexStats}});}return stage.spatial;}
    function rebuildSpatial(level){const stage=levels[level];if(!stage.spatial)return null;stage.spatial=null;return ensureSpatial(level);}
    const rootMembers=levels[0].points.map(()=>[]),representatives=new Map();
    nodes.forEach((_,i)=>rootMembers[levels[0].nodeMap[i]].push(i));
    rootMembers.forEach((members,root)=>{const chosen=new Set();const sample=(candidates,limit)=>{const count=Math.min(limit,candidates.length);for(let j=0;j<count;j++)chosen.add(candidates[Math.floor((j+.5)*candidates.length/count)]);};sample(members.filter(i=>nodes[i].primary),8);sample(members.filter(i=>!chosen.has(i)&&/hyperedge/i.test(nodes[i].type)),8);sample(members.filter(i=>!chosen.has(i)),32-chosen.size);representatives.set(levels[0].points[root].index,[...chosen]);});
    function neighbors(index,page=0,size=24) {
      if(!Number.isInteger(index)||index<0||index>=nodes.length)throw Error('Node index out of range');
      page=Math.max(0,Math.floor(Number(page)||0));size=Math.min(100,Math.max(1,Math.floor(Number(size)||24)));
      const start=counts[index],total=counts[index+1]-start,entries=[];
      for(let j=start+page*size;j<Math.min(start+total,start+(page+1)*size);j++){const edgeIndex=adjacency[j],e=rawEdges[edgeIndex],other=e[0]===index?e[1]:e[0];entries.push({edge:edgeIndex,index:other,label:nodes[other].label,status:nodes[other].status,type:data.edge_types[e[3]],semantic:data.semantics[e[2]],direction:e[0]===index?'out':'in'});}
      return {node:nodes[index],index,page,size,total,entries};
    }
    function find(query,page=0,size=20){const q=query.toLocaleLowerCase().trim(),matches=[];for(let i=0;i<search.length;i++)if(search[i].includes(q))matches.push(i);return {total:matches.length,page,size,entries:matches.slice(page*size,(page+1)*size).map(i=>({index:i,record:nodes[i].id,label:nodes[i].label,type:nodes[i].type,status:nodes[i].status,x:nodes[i].x,y:nodes[i].y}))};}
    function viewport(level,rect,edgeBudget=3500,nodeBudget=3500) {
      nodeBudget=Math.max(16,Math.min(3500,Math.floor(Number(nodeBudget)||3500)));
      const stage=levels[level],found=ensureSpatial(level).query(rect),nodeSet=new Set(found.nodes);let edges=found.edges.map(i=>stage.edges[i]),points;
      for(const e of edges){nodeSet.add(e.a);nodeSet.add(e.b);}
      // Connection bundles add drawing points of their own. Budget their finite
      // maximum before deciding whether to retain all individual visible nodes.
      const extraBundlePoints=edges.length>edgeBudget?Math.min(24*24,2*edges.length):0;
      const nodeAggregation=nodeSet.size>nodeBudget||found.nodes.length+extraBundlePoints>nodeBudget;
      if(nodeAggregation){
        // Display bins account for all visible records, including isolated ones.
        // Offscreen crossing endpoints remain represented but add no visible count.
        const bins=new Map(),bundle=new Map();let divisions=Math.min(32,Math.floor(Math.sqrt(nodeBudget)));
        do{bins.clear();bundle.clear();const size=Math.max(1,Math.max(rect[2]-rect[0],rect[3]-rect[1])/divisions);
          const bin=i=>{const p=stage.points[i],x=Math.max(rect[0],Math.min(rect[2],p.x)),y=Math.max(rect[1],Math.min(rect[3],p.y)),bx=Math.min(divisions-1,Math.floor((x-rect[0])/size)),by=Math.min(divisions-1,Math.floor((y-rect[1])/size)),key=bx+','+by;if(!bins.has(key))bins.set(key,{id:'v'+level+':'+key,index:p.index,raw:false,type:'density',zoomOnly:true,label:level===3?'记录聚合':'分组汇总',count:0,x:Math.min(rect[2],rect[0]+(bx+.5)*size),y:Math.min(rect[3],rect[1]+(by+.5)*size)});return bins.get(key);};
          for(const i of found.nodes)bin(i).count++;
          for(const e of edges){const a=bin(e.a),b=bin(e.b),key=a.id+'>'+b.id+':'+e.family;if(bundle.has(key))bundle.get(key).count+=e.count;else bundle.set(key,{source:a.id,target:b.id,family:e.family,count:e.count,index:e.index});}
          if(bundle.size<=Math.max(4,edgeBudget)||divisions<=1)break;divisions=Math.max(1,Math.floor(divisions/2));
        }while(true);
        edges=[...bundle.values()];points=[...bins.values()];
      }else if(edges.length>edgeBudget) {
        // A view-only density representation retains the sum of every intersecting edge.
        const bins=new Map(),bundle=new Map();let divisions=24,s=Math.max(1,Math.max(rect[2]-rect[0],rect[3]-rect[1])/divisions);
        const bin=(p)=>{const px=Math.max(rect[0],Math.min(rect[2],p.x)),py=Math.max(rect[1],Math.min(rect[3],p.y)),bx=Math.min(divisions-1,Math.floor((px-rect[0])/s)),by=Math.min(divisions-1,Math.floor((py-rect[1])/s)),key=bx+','+by;if(!bins.has(key))bins.set(key,{id:'b'+level+':'+key,index:p.index,raw:false,type:'density',zoomOnly:true,label:'连接汇总',count:0,x:rect[0]+(bx+.5)*s,y:rect[1]+(by+.5)*s});bins.get(key).count++;return bins.get(key);};
        do{bins.clear();bundle.clear();s=Math.max(1,Math.max(rect[2]-rect[0],rect[3]-rect[1])/divisions);for(const e of edges){const a=bin(stage.points[e.a]),b=bin(stage.points[e.b]),key=a.id+'>'+b.id+':'+e.family;if(bundle.has(key))bundle.get(key).count+=e.count;else bundle.set(key,{source:a.id,target:b.id,family:e.family,count:e.count,index:e.index});}if(bundle.size<=Math.max(4,edgeBudget)||divisions<=1)break;divisions=Math.max(1,Math.floor(divisions/2));}while(true);
        edges=[...bundle.values()];points=[...bins.values(),...found.nodes.map(i=>stage.points[i])];
      }else{for(const e of edges){nodeSet.add(e.a);nodeSet.add(e.b);}points=[...nodeSet].map(i=>stage.points[i]);edges=edges.map(e=>({...e,source:stage.points[e.a].id,target:stage.points[e.b].id}));}
      let representativeCount=0,representativeCandidates=0;
      if(level===0){const records=[];for(const p of points){if(p.type!=='cluster')continue;const selected=representatives.get(p.index)||[];representativeCandidates+=selected.length;for(const i of selected){const n=levels[3].points[i];if(n.x<rect[0]||n.x>rect[2]||n.y<rect[1]||n.y>rect[3])continue;records.push({...n,representative:true});}}const room=Math.max(0,nodeBudget-points.length),count=Math.min(room,records.length);for(let j=0;j<count;j++)points.push(records[Math.floor((j+.5)*records.length/count)]);representativeCount=count;}
      return {level,points,edges,counters:{...found.counters,...indexStats,totalNodes:nodes.length,totalEdges:rawEdges.length,stagePoints:stage.points.length,stageEdges:stage.edges.length,internalEdges:stage.internal,renderedPoints:points.length,renderedEdges:edges.length,nodeBudget,nodeAggregation,aggregatedNodes:nodeAggregation?nodeSet.size:0,representedVisibleNodes:found.nodes.length,representatives:representativeCount,representativeCandidates,densityStars:0,representedCrossingEdges:found.edges.reduce((sum,i)=>sum+stage.edges[i].count,0),densityBundles:nodeAggregation||found.edges.length>edgeBudget}};
    }
    return {data,levels,degree,counts,adjacency,byId,neighbors,find,viewport,indexStats,ensureSpatial,rebuildSpatial};
  }
  return {family,intersects,Spatial,create};
})();
if(typeof module!=='undefined'&&module.exports)module.exports=ArchiveCore;
if(typeof self!=='undefined'&&typeof self.postMessage==='function') {
  let index=null,raw=null,sim=null,timer=null,physicsIds=[],physicsEdges=[],steps=0,physicsSprings=0,aggregate=null,indexUpdating=false,repairCursor=0,repairTimer=null,waitingViews=[],dirtyEdges=new Set(),repairList=[];
  const send=(type,value,request)=>self.postMessage({type,...value,request});
  async function unpack(value){const bytes=Uint8Array.from(atob(value.trim()),c=>c.charCodeAt(0));const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));return JSON.parse(await new Response(stream).text());}
  function stop(){if(timer!==null)clearTimeout(timer);timer=null;if(sim)sim.stop();}
  function refreshBounds(){const bounds=[Infinity,Infinity,-Infinity,-Infinity];const include=(x,y)=>{bounds[0]=Math.min(bounds[0],x);bounds[1]=Math.min(bounds[1],y);bounds[2]=Math.max(bounds[2],x);bounds[3]=Math.max(bounds[3],y);};for(const n of index.data.nodes)include(n.x,n.y);for(const c of index.data.clusters){include(c.bounds[0],c.bounds[1]);include(c.bounds[2],c.bounds[3]);}index.data.bounds=Number.isFinite(bounds[0])?bounds:[0,0,0,0];send('bounds-updated',{bounds:index.data.bounds});}
  function flushViews(){const views=waitingViews;waitingViews=[];for(const m of views)send('viewport',index.viewport(m.level,m.rect,Math.max(100,Math.min(3500,Number(m.edgeBudget)||3500)),m.nodeBudget),m.request);}
  function repairEdges(){if(repairTimer!==null)clearTimeout(repairTimer);const started=performance.now();let visited=0;while(repairCursor<repairList.length&&visited<2000&&performance.now()-started<6){index.levels[3].spatial.edge(repairList[repairCursor++]);visited++;}send('index-progress',{remaining:repairList.length-repairCursor,visited});if(repairCursor<repairList.length){repairTimer=setTimeout(repairEdges,8);}else{repairTimer=null;dirtyEdges.clear();indexUpdating=false;refreshBounds();send('positions-updated',{});flushViews();}}
  function commitAggregate(){
    if(!aggregate?.dirty)return;stop();const stage=index.levels[aggregate.level],shifts=new Map(aggregate.points.map(p=>[p.id,{x:p.x-p.ox,y:p.y-p.oy}]));
    index.data.nodes.forEach((n,i)=>{const shift=shifts.get(stage.nodeMap[i]);if(shift){n.x+=shift.x;n.y+=shift.y;index.levels[3].points[i].x=n.x;index.levels[3].points[i].y=n.y;}});
    const clusterToPoint=new Map(stage.points.map((p,i)=>[p.index,i]));index.data.clusters.forEach((c,i)=>{if(c.level<aggregate.level)return;let ancestor=i;while(index.data.clusters[ancestor].level>aggregate.level)ancestor=index.data.clusters[ancestor].parent;const shift=shifts.get(clusterToPoint.get(ancestor));if(shift){c.x+=shift.x;c.y+=shift.y;c.bounds=c.bounds.map((v,j)=>v+(j%2?shift.y:shift.x));}});
    refreshBounds();for(let level=0;level<4;level++){const st=index.levels[level];if(level<3)st.points.forEach(p=>{const c=index.data.clusters[p.index];p.x=c.x;p.y=c.y;});index.rebuildSpatial(level);}
    aggregate.points.forEach(p=>{p.ox=p.x;p.oy=p.y;p.ax=p.x;p.ay=p.y;});aggregate.dirty=false;dirtyEdges.clear();indexUpdating=false;send('positions-updated',{});flushViews();
  }
  function aggregatePhysics(clusterIndex,force){
    if(!index||typeof d3==='undefined')return;stop();if(repairTimer!==null){clearTimeout(repairTimer);repairTimer=null;}
    const level=index.data.clusters[clusterIndex].level,stage=index.levels[level],seed=stage.points.findIndex(p=>p.index===clusterIndex);
    if(!aggregate||aggregate.level!==level||!aggregate.points.some(p=>p.id===seed)){commitAggregate();const ids=new Set([seed]);for(const e of stage.edges){if(ids.size>=512)break;if(e.a===seed||e.b===seed){ids.add(e.a);ids.add(e.b);}}const points=[...ids].map(id=>({id,x:stage.points[id].x,y:stage.points[id].y,ox:stage.points[id].x,oy:stage.points[id].y,ax:stage.points[id].x,ay:stage.points[id].y})),links=[];for(const e of stage.edges)if(links.length<1024&&ids.has(e.a)&&ids.has(e.b))links.push({source:e.a,target:e.b,family:e.family,restLength:Math.hypot(stage.points[e.a].x-stage.points[e.b].x,stage.points[e.a].y-stage.points[e.b].y)});aggregate={level,points,dirty:false,simulation:d3.forceSimulation(points).stop().alphaDecay(.06).velocityDecay(.6).force('charge',d3.forceManyBody().strength(-450)).force('links',d3.forceLink(links).id(p=>p.id).distance(e=>Math.max(e.restLength,e.family==='dependency'?450:e.family==='source'?650:800)).strength(e=>e.family==='dependency'?.025:e.family==='source'?.012:.005)).force('x',d3.forceX(p=>p.ax).strength(.02)).force('y',d3.forceY(p=>p.ay).strength(.02)),springs:links.length};}
    sim=aggregate.simulation;const anchor=aggregate.points.find(p=>p.id===seed);if(anchor&&force){anchor.fx=force.x;anchor.fy=force.y;if(force.x!==null){anchor.x=force.x;anchor.y=force.y;}else{anchor.ax=anchor.x;anchor.ay=anchor.y;anchor.vx=anchor.vy=0;sim.force('x').x(p=>p.ax);sim.force('y').y(p=>p.ay);}}sim.alpha(.3);steps=0;aggregate.dirty=true;indexUpdating=true;
    const tick=()=>{sim.tick();steps++;const coords=new Float32Array(aggregate.points.length*2);aggregate.points.forEach((p,i)=>{stage.points[p.id].x=p.x;stage.points[p.id].y=p.y;coords[i*2]=p.x;coords[i*2+1]=p.y;});const settled=sim.alpha()<.006;self.postMessage({type:'physics',ids:aggregate.points.map(p=>stage.points[p.id].id),buffer:coords.buffer,nodes:aggregate.points.length,edges:aggregate.springs,springs:aggregate.springs,steps,settled},[coords.buffer]);if(settled){timer=null;commitAggregate();return;}timer=setTimeout(tick,35);};timer=setTimeout(tick,0);
  }
  function physics(seed,force) {
    if(!index||typeof d3==='undefined')return;commitAggregate();index.ensureSpatial(3);
    if(repairTimer!==null){clearTimeout(repairTimer);repairTimer=null;}
    if(!sim||sim===aggregate?.simulation||!physicsIds.includes(seed)) {
      stop();const ids=new Set([seed]),offset=index.counts[seed],end=index.counts[seed+1];
      for(let j=offset;j<end&&ids.size<512;j++){const e=index.data.edges[index.adjacency[j]];ids.add(e[0]);ids.add(e[1]);}
      physicsIds=[...ids];const chosen=new Set(physicsIds),points=physicsIds.map(i=>{const p=index.levels[3].points[i];return {id:i,x:p.x,y:p.y,ax:p.x,ay:p.y};}),edges=[];
      const seen=new Set();springs:for(const i of physicsIds)for(let j=index.counts[i];j<index.counts[i+1];j++){if(edges.length>=1024)break springs;const ei=index.adjacency[j],e=index.data.edges[ei];if(!seen.has(ei)&&chosen.has(e[0])&&chosen.has(e[1])){seen.add(ei);edges.push({source:e[0],target:e[1],family:ArchiveCore.family(index.data.semantics[e[2]],index.data.edge_types[e[3]]),restLength:Math.hypot(index.levels[3].points[e[0]].x-index.levels[3].points[e[1]].x,index.levels[3].points[e[0]].y-index.levels[3].points[e[1]].y)});}}
      physicsSprings=edges.length;physicsEdges=[...new Set(physicsIds.flatMap(i=>Array.from(index.adjacency.subarray(index.counts[i],index.counts[i+1]))))];
      sim=d3.forceSimulation(points).stop().alphaDecay(.045).velocityDecay(.55).force('charge',d3.forceManyBody().strength(p=>-35*Math.min(6,Math.sqrt(index.degree[p.id]+1)))).force('collision',d3.forceCollide(12)).force('links',d3.forceLink(edges).id(p=>p.id).distance(e=>Math.max(e.restLength,e.family==='dependency'?45:e.family==='source'?80:e.family==='history'?110:95)).strength(e=>e.family==='dependency'?.12:e.family==='source'?.045:e.family==='history'?.018:.01)).force('x',d3.forceX(p=>p.ax).strength(.035)).force('y',d3.forceY(p=>p.ay).strength(.035));steps=0;
    }
    const anchor=sim.nodes().find(p=>p.id===seed);if(anchor&&force){anchor.fx=force.x;anchor.fy=force.y;if(force.x!==null){anchor.x=force.x;anchor.y=force.y;}else{anchor.ax=anchor.x;anchor.ay=anchor.y;anchor.vx=anchor.vy=0;sim.force('x').x(p=>p.ax);sim.force('y').y(p=>p.ay);}}
    for(const ei of physicsEdges)dirtyEdges.add(ei);repairList=[...dirtyEdges];repairCursor=0;sim.alpha(.4);stop();
    indexUpdating=true;
    const tick=()=>{sim.tick();steps++;const positions=new Float32Array(physicsIds.length*2);
      sim.nodes().forEach((p,j)=>{const point=index.levels[3].points[p.id];point.x=p.x;point.y=p.y;index.data.nodes[p.id].x=p.x;index.data.nodes[p.id].y=p.y;index.levels[3].spatial.node(p.id);positions[j*2]=p.x;positions[j*2+1]=p.y;});
      // During motion queries await an exact index; rebuild incident segments in bounded batches after cooling.
      self.postMessage({type:'physics',ids:physicsIds.map(i=>'n'+i),buffer:positions.buffer,nodes:physicsIds.length,edges:physicsEdges.length,springs:physicsSprings,steps,settled:sim.alpha()<.005},[positions.buffer]);
      if(sim.alpha()<.005){timer=null;repairCursor=0;repairEdges();return;}timer=setTimeout(tick,35);};timer=setTimeout(tick,0);
  }
  self.onmessage=async event=>{const m=event.data;try {
    if(m.type==='init'){stop();if(repairTimer!==null)clearTimeout(repairTimer);repairTimer=null;sim=null;aggregate=null;physicsIds=[];physicsEdges=[];dirtyEdges.clear();repairList=[];indexUpdating=false;const data=await unpack(m.compressed);index=ArchiveCore.create(data,p=>send('progress',p));send('ready',{source:data.source,bounds:data.bounds,roots:data.roots,indexStats:{...index.indexStats},levels:index.levels.map(s=>({points:s.points.length,edges:s.edges.length,internal:s.internal,spatialReady:!!s.spatial}))});}
    else if(m.type==='viewport'){if(indexUpdating){for(const old of waitingViews)send('viewport',{cancelled:true},old.request);waitingViews=[m];}else send('viewport',index.viewport(m.level,m.rect,Math.max(100,Math.min(3500,Number(m.edgeBudget)||3500)),m.nodeBudget),m.request);}
    else if(m.type==='search'){send('search',index.find(m.query,m.page),m.request);}
    else if(m.type==='detail'){send('detail',index.neighbors(m.index,m.page),m.request);}
    else if(m.type==='locate'){const n=index.data.nodes[m.index];send('locate',{index:m.index,x:n.x,y:n.y,parent:n.parent,raw_node_index:n.raw_node_index,raw_hyperedge_id:n.raw_hyperedge_id},m.request);}
    else if(m.type==='raw-locator'){send('raw-locator',{index:index.data.edges[m.index][4]},m.request);}
    else if(m.type==='cluster'){const c=index.data.clusters[m.index];send('cluster',{index:m.index,cluster:c},m.request);}
    else if(m.type==='highlight'){const found=new Set([m.index]),edges=[];for(let j=index.counts[m.index];j<index.counts[m.index+1];j++){const ei=index.adjacency[j],e=index.data.edges[ei];found.add(e[0]);found.add(e[1]);edges.push(ei);}send('highlight',{nodes:[...found],edges},m.request);}
    else if(m.type==='drag'&&m.id.startsWith('n')){physics(Number(m.id.slice(1)),{x:m.x,y:m.y});}
    else if(m.type==='drag'&&m.id.startsWith('c')){aggregatePhysics(Number(m.id.slice(1)),{x:m.x,y:m.y});}
    else if(m.type==='pause'){stop();if(aggregate?.dirty)commitAggregate();else if(indexUpdating){repairCursor=0;repairEdges();}send('physics',{settled:true,nodes:physicsIds.length,steps});}
    else if(m.type==='raw-init'){raw=await unpack(m.compressed);send('raw-ready',{});}
    else if(m.type==='raw-node'){send('raw-result',{value:(raw.nodes||raw.dependency_map?.nodes||[])[m.index]},m.request);}
    else if(m.type==='raw-hyperedge'){send('raw-result',{value:(raw.hyperedges||raw.dependency_map?.hyperedges||[]).find(e=>e.id===m.id)},m.request);}
    else if(m.type==='raw-edge'){send('raw-result',{value:(raw.edges||raw.hyperedges||raw.dependency_map?.hyperedges||[])[m.index]},m.request);}
  }catch(error){send('error',{message:String(error.message||error)},m.request);}};
}
