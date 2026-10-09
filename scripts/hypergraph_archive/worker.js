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
    query(rect,nodesOnly=false) {
      if(!Array.isArray(rect)||rect.length!==4||!rect.every(Number.isFinite)||rect[0]>rect[2]||rect[1]>rect[3])throw Error('Viewport must have finite ordered bounds');
      const ns=new Set(),es=new Set();let cells=0,examinedCells=0;const c=this.cell;
      const x0=Math.max(this.extent[0],Math.floor(rect[0]/c)),y0=Math.max(this.extent[1],Math.floor(rect[1]/c)),x1=Math.min(this.extent[2],Math.floor(rect[2]/c)),y1=Math.min(this.extent[3],Math.floor(rect[3]/c));
      const visit=key=>{cells++;for(const i of this.ng.get(key)||[])ns.add(i);if(!nodesOnly)for(const i of this.eg.get(key)||[])es.add(i);};
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
      if(level===3)nodes.forEach((n,i)=>{map.set(i,i);points.push({...n,id:'n'+i,record:n.id,index:i,raw:true,degree:n.degree??degree[i],clusterPath:[ancestor(i,0),ancestor(i,1),ancestor(i,2)],count:1});nodeMap[i]=i;});
      else {
        clusters.forEach((c,i)=>{if(c.level===level){map.set(i,points.length);points.push({id:'c'+i,index:i,raw:false,x:c.x,y:c.y,label:c.label,type:'cluster',count:c.node_count,primary:c.nodes.some(n=>nodes[n].primary),status:'CLUSTER'});}});
        nodes.forEach((_,i)=>nodeMap[i]=map.get(ancestor(i,level)));
        nodes.forEach((n,i)=>{if(n.primary)points[nodeMap[i]].primary=true;});
      }
      const edges=[],agg=new Map(),edgeMap=new Int32Array(rawEdges.length).fill(-1);let internal=0;
      rawEdges.forEach((e,i)=>{const a=nodeMap[e[0]],b=nodeMap[e[1]],semantic=data.semantics[e[2]],type=data.edge_types[e[3]],f=family(semantic,type),relationKey=JSON.stringify([semantic,type]),membership=/snapshot_contains|membership|归属/i.test(semantic+' '+type);if(level!==3&&a===b){internal++;return;}const key=a+':'+b+':'+f+':'+membership;if(level===3){edgeMap[i]=edges.length;edges.push({a,b,family:f,relationKey,semantic,type,membership,count:1,index:i});return;}if(agg.has(key)){edgeMap[i]=agg.get(key);const bundled=edges[edgeMap[i]];bundled.count++;const term=bundled.forceTerms.find(t=>t.semantic===semantic&&t.type===type);if(term)term.count++;else bundled.forceTerms.push({semantic,type,membership,count:1});if(bundled.relationKey!==relationKey){bundled.relationKey=null;bundled.mixedRelation=true;}}else{edgeMap[i]=edges.length;agg.set(key,edges.length);edges.push({a,b,family:f,relationKey,semantic,type,membership,count:1,index:i,forceTerms:[{semantic,type,membership,count:1}]});}});
      levels.push({points,edges,spatial:null,internal,nodeMap,edgeMap});if(level<3)ensureSpatial(level);progress({level,points:points.length,edges:edges.length,spatialReady:level<3});
    }
    function ensureSpatial(level){const stage=levels[level];if(!stage.spatial){stage.spatial=new Spatial(stage.points,stage.edges,data.bounds);indexStats.spatialBuilds++;indexStats.spatialEdgeVisits+=stage.edges.length;if(level===3){indexStats.rawSpatialBuilds++;indexStats.rawSpatialEdgeVisits+=stage.edges.length;}progress({level,spatialReady:true,points:stage.points.length,edges:stage.edges.length,indexStats:{...indexStats}});}return stage.spatial;}
    function rebuildSpatial(level){const stage=levels[level];if(!stage.spatial)return null;stage.spatial=null;return ensureSpatial(level);}
    const rootMembers=levels[0].points.map(()=>[]),representatives=new Map();
    nodes.forEach((_,i)=>rootMembers[levels[0].nodeMap[i]].push(i));
    rootMembers.forEach((members,root)=>{const chosen=new Set();const sample=(candidates,limit)=>{const count=Math.min(limit,candidates.length);for(let j=0;j<count;j++)chosen.add(candidates[Math.floor((j+.5)*candidates.length/count)]);};sample(members.filter(i=>nodes[i].primary),8);sample(members.filter(i=>!chosen.has(i)&&/hyperedge/i.test(nodes[i].type)),8);sample(members.filter(i=>!chosen.has(i)),32-chosen.size);representatives.set(levels[0].points[root].index,[...chosen]);});
    function presentation(n){const fields={};for(const key of ['label','summary','type','status','status_text','display_kind','color','shape','outline','goal_weights','group','cohesion_group','source_id','primary'])if(Object.hasOwn(n,key))fields[key]=n[key];return fields;}
    let filterCache=null;
    function clearFilters(){filterCache=null;}
    function filtered(filters={}){
      const q=String(filters.query||'').trim().toLocaleLowerCase(),scope=Number.isInteger(filters.scope)?filters.scope:null,key=JSON.stringify([filters.membership!==false,filters.showOrphans!==false,q,scope]);
      if(scope!==null&&!clusters[scope])throw Error('Cluster index out of range');
      if(filterCache?.key===key)return filterCache;
      const eligible=new Uint8Array(nodes.length),activeDegree=new Uint32Array(nodes.length),keep=new Uint8Array(nodes.length),sizes=levels.map(s=>new Uint32Array(s.points.length)),edgeSizes=levels.map(s=>new Uint32Array(s.edges.length)),internal=[0,0,0,0];
      for(let i=0;i<nodes.length;i++)eligible[i]=Number(!q||search[i].includes(q));
      for(const e of levels[3].edges)if(eligible[e.a]&&eligible[e.b]&&(filters.membership!==false||!e.membership)){activeDegree[e.a]+=e.count;activeDegree[e.b]+=e.count;}
      for(let i=0;i<nodes.length;i++)if(eligible[i]&&(filters.showOrphans!==false||activeDegree[i]>0)&&(scope===null||ancestor(i,clusters[scope].level)===scope)){keep[i]=1;for(let l=0;l<4;l++)sizes[l][levels[l].nodeMap[i]]++;}
      for(let i=0;i<rawEdges.length;i++){const e=levels[3].edges[i];if(!eligible[e.a]||!eligible[e.b]||scope!==null&&!keep[e.a]&&!keep[e.b]||filters.membership===false&&e.membership)continue;for(let l=0;l<4;l++){const j=levels[l].edgeMap[i];if(j<0)internal[l]++;else edgeSizes[l][j]++;}}
      return filterCache={key,keep,sizes,edgeSizes,internal,degree:activeDegree};
    }
    function clusterView(index,filters={},position=i=>nodes[i]){
      const c=clusters[index];if(!c)throw Error('Cluster index out of range');
      const selection=filtered({...filters,scope:index}),bounds=[Infinity,Infinity,-Infinity,-Infinity];let total=0;
      for(let i=0;i<nodes.length;i++)if(selection.keep[i]){const n=position(i);total++;bounds[0]=Math.min(bounds[0],n.x);bounds[1]=Math.min(bounds[1],n.y);bounds[2]=Math.max(bounds[2],n.x);bounds[3]=Math.max(bounds[3],n.y);}
      return {index,cluster:c,memberBounds:total?bounds:[c.x,c.y,c.x,c.y],total};
    }
    function neighbors(index,page=0,size=24) {
      if(!Number.isInteger(index)||index<0||index>=nodes.length)throw Error('Node index out of range');
      page=Math.max(0,Math.floor(Number(page)||0));size=Math.min(100,Math.max(1,Math.floor(Number(size)||24)));
      const start=counts[index],total=counts[index+1]-start,entries=[];
      for(let j=start+page*size;j<Math.min(start+total,start+(page+1)*size);j++){const edgeIndex=adjacency[j],e=rawEdges[edgeIndex],other=e[0]===index?e[1]:e[0];entries.push({...presentation(nodes[other]),edge:edgeIndex,index:other,node_type:nodes[other].type,type:data.edge_types[e[3]],semantic:data.semantics[e[2]],direction:e[0]===index?'out':'in'});}
      const n=nodes[index];return {node:{...presentation(n),id:n.id,x:n.x,y:n.y,parent:n.parent,degree:n.degree??degree[index]},index,page,size,total,entries};
    }
    function find(query,page=0,size=20){const q=query.toLocaleLowerCase().trim(),matches=[];for(let i=0;i<search.length;i++)if(search[i].includes(q))matches.push(i);return {total:matches.length,page,size,entries:matches.slice(page*size,(page+1)*size).map(i=>({...presentation(nodes[i]),index:i,record:nodes[i].id,x:nodes[i].x,y:nodes[i].y}))};}
    function cellMembers(cell,filters={},page=0,size=24){
      if(!Array.isArray(cell)||cell.length!==3||!cell.every(Number.isFinite)||cell[0]<=0||!Number.isInteger(cell[1])||!Number.isInteger(cell[2]))throw Error('Invalid spatial cell');
      const [width,x,y]=cell,rect=[x*width,y*width,(x+1)*width,(y+1)*width],selection=filtered(filters);
      const matches=ensureSpatial(3).query(rect,true).nodes.filter(i=>selection.keep[i]&&Math.floor(nodes[i].x/width)===x&&Math.floor(nodes[i].y/width)===y&&i!==filters.selected);
      page=Math.max(0,Math.floor(Number(page)||0));size=Math.max(1,Math.min(100,Math.floor(Number(size)||24)));
      return {page,size,total:matches.length,entries:matches.slice(page*size,(page+1)*size).map(i=>({...presentation(nodes[i]),index:i}))};
    }
    function viewport(level,rect,edgeBudget=3500,nodeBudget=3500,filters={}) {
      nodeBudget=Math.max(16,Math.min(3500,Math.floor(Number(nodeBudget)||3500)));
      edgeBudget=Math.max(4,Math.min(3500,Math.floor(Number(edgeBudget)||3500)));
      const stage=levels[level],found=ensureSpatial(level).query(rect),q=String(filters.query||'').trim().toLocaleLowerCase(),selection=filtered(filters);
      const keep=i=>selection.sizes[level][i]>0;
      const indexedCrossingEdges=found.edges.reduce((sum,i)=>sum+stage.edges[i].count,0),indexedVisibleNodes=found.nodes.length;
      found.nodes=found.nodes.filter(keep);found.edges=found.edges.filter(i=>selection.edgeSizes[level][i]>0);found.counters.visibleNodes=found.nodes.length;found.counters.crossingEdges=found.edges.length;
      // Screen-external connection endpoints are geometry, not visible records.
      // They must never replace an affordable set of original record glyphs.
      let nodeAggregation=found.nodes.length>nodeBudget;const display=new Map();let points;
      if(nodeAggregation||level===3&&Number.isFinite(filters.cellSize)&&filters.cellSize>0){
        let size=Number.isFinite(filters.cellSize)&&filters.cellSize>0?filters.cellSize:2**Math.ceil(Math.log2(Math.max(rect[2]-rect[0],rect[3]-rect[1],1)/Math.max(1,Math.floor(Math.sqrt(nodeBudget))))),bins;
        do{bins=new Map();display.clear();for(const i of found.nodes){const p=stage.points[i],key=i===filters.selected?'selected:'+i:Math.floor(p.x/size)+','+Math.floor(p.y/size);if(!bins.has(key))bins.set(key,{id:'v'+level+':'+size+':'+key,index:p.index,raw:false,type:'density',zoomOnly:true,label:level===3?'附近记录':'附近分组',cell:[size,Math.floor(p.x/size),Math.floor(p.y/size)],count:0,x:0,y:0,bounds:[Infinity,Infinity,-Infinity,-Infinity],first:i});const bin=bins.get(key);bin.count++;bin.x+=p.x;bin.y+=p.y;bin.bounds[0]=Math.min(bin.bounds[0],p.x);bin.bounds[1]=Math.min(bin.bounds[1],p.y);bin.bounds[2]=Math.max(bin.bounds[2],p.x);bin.bounds[3]=Math.max(bin.bounds[3],p.y);display.set(i,bin);}if(bins.size>nodeBudget)size*=2;}while(bins.size>nodeBudget);
        points=[...bins.values()];for(let j=0;j<points.length;j++){const p=points[j];if(p.count===1){const original=stage.points[p.first];points[j]=level===3?original:{...original,count:selection.sizes[level][p.first]};display.set(p.first,points[j]);}else{p.x/=p.count;p.y/=p.count;p.label+=' · '+p.count;}}
        nodeAggregation=points.some(p=>p.type==='density');
      }else{points=found.nodes.map(i=>level===3?stage.points[i]:{...stage.points[i],count:selection.sizes[level][i]});for(let j=0;j<found.nodes.length;j++)display.set(found.nodes[j],points[j]);}
      const exact=new Map(),context=[];
      const merge=(to,e)=>{to.count+=e.count;if(to.relationKey!==e.relationKey){to.relationKey=null;to.semantic=to.type=undefined;to.membership=undefined;to.mixedRelation=true;}};
      for(const i of found.edges){const e={...stage.edges[i],count:selection.edgeSizes[level][i]},a=display.get(e.a),b=display.get(e.b);
        if(a&&b){const key=a.id+'>'+b.id+':'+e.family+':'+e.relationKey;if(exact.has(key))merge(exact.get(key),e);else exact.set(key,{...e,source:a.id,target:b.id});}
        else context.push(e);
      }
      const candidates=[...exact.values()],reserve=context.length||candidates.length>edgeBudget?4:0,kept=candidates.slice(0,Math.max(0,edgeBudget-reserve));
      for(let i=kept.length;i<candidates.length;i++)context.push(candidates[i]);
      // Summary geometry is a deterministic representative ORIGINAL segment.
      // Counts describe the whole partition; it is not a new causal relation.
      const clip=e=>{const a=display.get(e.a)||stage.points[e.a],b=display.get(e.b)||stage.points[e.b],dx=b.x-a.x,dy=b.y-a.y;let lo=0,hi=1;for(const [p,q] of [[-dx,a.x-rect[0]],[dx,rect[2]-a.x],[-dy,a.y-rect[1]],[dy,rect[3]-a.y]]){if(p===0)continue;const t=q/p;if(p<0)lo=Math.max(lo,t);else hi=Math.min(hi,t);}return [{x:a.x+dx*lo,y:a.y+dy*lo},{x:a.x+dx*hi,y:a.y+dy*hi}];};
      const geometry=context.map(e=>({e,line:clip(e)})),bundle=new Map(),summaryBudget=Math.min(128,edgeBudget-kept.length);let divisions=8;
      if(geometry.length)do{bundle.clear();const cell=p=>Math.min(divisions-1,Math.max(0,Math.floor((p.x-rect[0])/Math.max(1,rect[2]-rect[0])*divisions)))+','+Math.min(divisions-1,Math.max(0,Math.floor((p.y-rect[1])/Math.max(1,rect[3]-rect[1])*divisions)));
        for(const item of geometry){const {e,line}=item,key=e.family+':'+cell(line[0])+'>'+cell(line[1]);if(bundle.has(key))merge(bundle.get(key),e);else bundle.set(key,{...e,line,summary:true});}
        if(bundle.size<=summaryBudget||divisions===1)break;divisions=Math.max(1,Math.floor(divisions/2));
      }while(true);
      const anchors=[],summaries=[];for(const e of bundle.values()){const ids=e.line.map((p,j)=>{const id='a'+level+':'+summaries.length+':'+j;anchors.push({id,x:p.x,y:p.y,anchor:true});return id;});const {line,...fields}=e;summaries.push({...fields,source:ids[0],target:ids[1]});}
      const edges=[...kept,...summaries];
      let representativeCount=0,representativeCandidates=0;
      if(level===0){const records=[];for(const p of points){if(p.type!=='cluster')continue;const selected=representatives.get(p.index)||[];representativeCandidates+=selected.length;for(const i of selected){const n=levels[3].points[i];if(n.x<rect[0]||n.x>rect[2]||n.y<rect[1]||n.y>rect[3]||!selection.keep[i])continue;records.push({...n,representative:true});}}const room=Math.max(0,nodeBudget-points.length),count=Math.min(room,records.length);for(let j=0;j<count;j++)points.push(records[Math.floor((j+.5)*records.length/count)]);representativeCount=count;}
      return {level,points,anchors,edges,counters:{...found.counters,...indexStats,totalNodes:nodes.length,totalEdges:rawEdges.length,indexedCrossingEdges,indexedVisibleNodes,displayFiltered:filters.membership===false||filters.showOrphans===false||!!q||Number.isInteger(filters.scope),scope:filters.scope??null,stagePoints:stage.points.length,stageEdges:stage.edges.length,internalEdges:selection.internal[level],renderedPoints:points.length,renderedAnchors:anchors.length,exactEdges:kept.length,summaryEdges:summaries.length,renderedEdges:edges.length,nodeBudget,nodeAggregation,aggregatedNodes:nodeAggregation?found.nodes.length:0,representedVisibleNodes:found.nodes.length,representatives:representativeCount,representativeCandidates,densityStars:0,representedCrossingEdges:found.edges.reduce((sum,i)=>sum+selection.edgeSizes[level][i],0),densityBundles:nodeAggregation||summaries.length>0}};
    }
    return {data,levels,degree,counts,adjacency,byId,neighbors,find,viewport,indexStats,ensureSpatial,rebuildSpatial,filtered,clearFilters,clusterView,cellMembers};
  }
  return {family,intersects,Spatial,create,relationKey:(semantic,type)=>JSON.stringify([semantic,type])};
})();
if(typeof module!=='undefined'&&module.exports)module.exports=ArchiveCore;
if(typeof self!=='undefined'&&typeof self.postMessage==='function') {
  // Indexed geometry is a stable snapshot while local coordinates animate. Camera
  // queries use that complete snapshot; after commit, incident segments are repaired
  // in yielded batches before any exact raw-coordinate query is answered.
  let relationAliases=new Map(),index=null,raw=null,sim=null,timer=null,steps=0,aggregate=null,physicsIds=[],physicsEdges=[],physicsLinks=[],paused=false,simSeed=null;
  let repairTimer=null,repairList=[],repairCursor=0,waitingViews=[],revision=0,dirtyEdges=new Set(),pendingPositions=new Map(),dirtyStageEdges=new Map(),dirtyStageNodes=new Map(),blockedLevels=new Set(),levelRemaining=new Map(),commitJob=null,commitTimer=null,deferredMotion=null;
  const defaults={damping:.55,flowStrength:.005,groupStrength:.025,edgeRepulsion:.15,edgeClearance:35,centerStrength:.035,repelStrength:35,linkStrength:1,linkDistance:80,membership:true,weightMode:'degree',goal:'',relations:{dependency:{mode:'attract',strength:1},source:{mode:'attract',strength:.35},history:{mode:'attract',strength:.15},other:{mode:'none',strength:1}}};
  let options={...defaults,relations:Object.assign(Object.create(null),defaults.relations)};
  const send=(type,value,request)=>self.postMessage({type,...value,request});
  async function unpack(value){const bytes=Uint8Array.from(atob(value.trim()),c=>c.charCodeAt(0));const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));return JSON.parse(await new Response(stream).text());}
  function stop(){if(timer!==null)clearTimeout(timer);timer=null;if(sim)sim.stop();}
  function refreshBounds(){const bounds=[Infinity,Infinity,-Infinity,-Infinity];const include=(x,y)=>{bounds[0]=Math.min(bounds[0],x);bounds[1]=Math.min(bounds[1],y);bounds[2]=Math.max(bounds[2],x);bounds[3]=Math.max(bounds[3],y);};for(const n of index.data.nodes)include(n.x,n.y);for(const c of index.data.clusters){include(c.bounds[0],c.bounds[1]);include(c.bounds[2],c.bounds[3]);}index.data.bounds=Number.isFinite(bounds[0])?bounds:[0,0,0,0];send('bounds-updated',{bounds:index.data.bounds});}
  function viewport(m){const value=index.viewport(m.level,m.rect,Math.max(100,Math.min(3500,Number(m.edgeBudget)||3500)),m.nodeBudget,m.filters||{});Object.assign(value.counters,{stableViewport:!!timer||pendingPositions.size>0||!!aggregate?.dirty,movingNodes:aggregate?.dirty?aggregate.points.length:pendingPositions.size,geometryRevision:revision,indexRemaining:repairList.length-repairCursor});send('viewport',value,m.request);}
  function flushViews(){const views=waitingViews;waitingViews=[];for(const m of views){if(blockedLevels.has(m.level))waitingViews.push(m);else viewport(m);}}
  function repairEdges(){repairTimer=null;const started=performance.now();let visited=0;while(repairCursor<repairList.length&&visited<2000&&performance.now()-started<6){const task=repairList[repairCursor++],spatial=index.levels[task.level].spatial;if(task.node)spatial.node(task.index);else spatial.edge(task.index);visited++;const remaining=levelRemaining.get(task.level)-1;levelRemaining.set(task.level,remaining);if(remaining===0)blockedLevels.delete(task.level);}send('index-progress',{remaining:repairList.length-repairCursor,visited});flushViews();if(repairCursor<repairList.length)repairTimer=setTimeout(repairEdges,8);else{repairList=[];repairCursor=0;dirtyEdges.clear();dirtyStageEdges.clear();dirtyStageNodes.clear();blockedLevels.clear();revision++;refreshBounds();send('positions-updated',{geometryRevision:revision});flushViews();}}
  function markNode(level,i){if(index.levels[level].spatial){if(!dirtyStageNodes.has(level))dirtyStageNodes.set(level,new Set());dirtyStageNodes.get(level).add(i);}}
  function maintain(){if(repairTimer!==null)clearTimeout(repairTimer);repairList=[];repairCursor=0;blockedLevels.clear();levelRemaining.clear();for(const [level,ids] of dirtyStageNodes)for(const i of ids)repairList.push({level,index:i,node:true});for(const [level,ids] of dirtyStageEdges)for(const i of ids)repairList.push({level,index:i});if(index.levels[3].spatial)for(const i of dirtyEdges)repairList.push({level:3,index:i});for(const task of repairList){blockedLevels.add(task.level);levelRemaining.set(task.level,(levelRemaining.get(task.level)||0)+1);}if(repairList.length){if(repairList.length<=2000)repairEdges();else repairTimer=setTimeout(repairEdges,0);}else{repairTimer=null;dirtyEdges.clear();dirtyStageNodes.clear();dirtyStageEdges.clear();revision++;refreshBounds();send('positions-updated',{geometryRevision:revision});flushViews();}}
  function commitRaw(){if(!pendingPositions.size)return;const stage=index.levels[3];for(const [i,p] of pendingPositions){stage.points[i].x=index.data.nodes[i].x=p.x;stage.points[i].y=index.data.nodes[i].y=p.y;markNode(3,i);}pendingPositions.clear();maintain();}
  function aggregateCommitStep(sync=false){
    commitTimer=null;const job=commitJob;if(!job)return;const started=performance.now();let visited=0;
    while(commitJob&&visited<2000&&(sync||performance.now()-started<6)){
      if(job.phase===0){
        if(job.cursor>=index.data.nodes.length){job.phase=1;job.cursor=0;continue;}
        const i=job.cursor++,n=index.data.nodes[i],shift=job.shifts.get(job.stage.nodeMap[i]);visited++;
        if(shift){n.x+=shift.x;n.y+=shift.y;job.points[3][i]={...index.levels[3].points[i],x:n.x,y:n.y};markNode(3,i);if(index.levels[3].spatial)for(let j=index.counts[i];j<index.counts[i+1];j++)dirtyEdges.add(index.adjacency[j]);}
      }else if(job.phase===1){
        if(job.cursor>=index.data.clusters.length){job.phase=2;job.level=0;job.cursor=0;continue;}
        const c=index.data.clusters[job.cursor],i=job.cursor++;visited++;if(c.level<job.aggregate.level)continue;let ancestor=i;while(index.data.clusters[ancestor].level>job.aggregate.level)ancestor=index.data.clusters[ancestor].parent;const shift=job.shifts.get(job.clusterToPoint.get(ancestor));if(shift){c.x+=shift.x;c.y+=shift.y;c.bounds=c.bounds.map((v,j)=>v+(j%2?shift.y:shift.x));}
      }else if(job.phase===2){
        if(job.level>=3){job.phase=3;job.level=0;job.cursor=0;continue;}
        const stage=index.levels[job.level];if(job.cursor>=stage.points.length){job.level++;job.cursor=0;continue;}const i=job.cursor++,p=stage.points[i],c=index.data.clusters[p.index];visited++;if(p.x!==c.x||p.y!==c.y){job.points[job.level][i]={...p,x:c.x,y:c.y};markNode(job.level,i);}
      }else if(job.phase===3){
        if(job.level>=3){
          for(let level=0;level<4;level++){index.levels[level].points=job.points[level];if(index.levels[level].spatial)index.levels[level].spatial.points=job.points[level];}
          job.aggregate.points.forEach(p=>{p.ox=p.x;p.oy=p.y;p.ax=p.x;p.ay=p.y;});job.aggregate.simulation.force('x').x(p=>p.ax);job.aggregate.simulation.force('y').y(p=>p.ay);job.aggregate.dirty=false;commitJob=null;maintain();const next=deferredMotion;deferredMotion=null;if(next)self.onmessage({data:next});break;
        }
        const stage=index.levels[job.level],changed=dirtyStageNodes.get(job.level);if(!changed||job.cursor>=stage.edges.length){job.level++;job.cursor=0;continue;}const i=job.cursor++,e=stage.edges[i];visited++;if(changed.has(e.a)||changed.has(e.b)){if(!dirtyStageEdges.has(job.level))dirtyStageEdges.set(job.level,new Set());dirtyStageEdges.get(job.level).add(i);}
      }
    }
    if(commitJob){send('index-progress',{remaining:Math.max(0,index.data.nodes.length-job.cursor),visited,phase:'aggregate-commit'});if(!sync)commitTimer=setTimeout(aggregateCommitStep,0);}
  }
  function commitAggregate(){
    if(commitJob||!aggregate?.dirty)return;stop();if(repairTimer!==null){clearTimeout(repairTimer);repairTimer=null;}const stage=index.levels[aggregate.level],shifts=new Map();for(const p of aggregate.points){const x=p.x-p.ox,y=p.y-p.oy;if(x!==0||y!==0)shifts.set(p.id,{x,y});}
    commitJob={aggregate,stage,shifts,clusterToPoint:new Map(stage.points.map((p,i)=>[p.index,i])),points:index.levels.map(s=>s.points.slice()),phase:0,cursor:0,level:0};
    // Tiny fixtures and small archives commit immediately. Large archives keep
    // their indexed camera snapshot intact until yielded preparation is published.
    if(index.data.nodes.length<=2000){while(commitJob)aggregateCommitStep(true);}else commitTimer=setTimeout(aggregateCommitStep,0);
  }
  function configure(value){
    const ranges={damping:[.1,.9],flowStrength:[0,.08],groupStrength:[0,.15],edgeRepulsion:[0,.6],edgeClearance:[0,200],centerStrength:[0,.3],repelStrength:[0,3000],linkStrength:[0,2],linkDistance:[0,1000]};
    for(const [key,[lo,hi]] of Object.entries(ranges))if(Object.hasOwn(value,key)){if(!Number.isFinite(value[key])||value[key]<lo||value[key]>hi)throw Error('Invalid force option '+key);}
    const relations=Object.assign(Object.create(null),options.relations);if(value.relations!==undefined){if(!value.relations||typeof value.relations!=='object'||Array.isArray(value.relations))throw Error('Invalid relation options');for(const [key,r] of Object.entries(value.relations)){if(!r||!['attract','repel','none'].includes(r.mode)||!Number.isFinite(r.strength)||r.strength<0||r.strength>2)throw Error('Invalid relation '+key);relations[key]={mode:r.mode,strength:r.strength};}}
    if(value.weightMode!==undefined&&!['uniform','degree','goal'].includes(value.weightMode))throw Error('Invalid charge weighting');
    if(value.membership!==undefined&&typeof value.membership!=='boolean')throw Error('Invalid membership option');
    const next={...options,relations};for(const key of Object.keys(ranges))if(Object.hasOwn(value,key))next[key]=value[key];if(value.weightMode!==undefined)next.weightMode=value.weightMode;if(value.goal!==undefined)next.goal=String(value.goal);if(value.membership!==undefined)next.membership=value.membership;options=next;
    if(sim){const seed=simSeed,p=sim.nodes().find(p=>('n'+p.id===seed||sim===aggregate?.simulation&&'c'+index.levels[aggregate.level].points[p.id].index===seed));if(p&&(value.relations!==undefined||value.membership!==undefined)){const force={x:p.x,y:p.y,released:p.fx===null||p.fx===undefined};simSeed=null;if(seed[0]==='n')physics(Number(seed.slice(1)),force);else aggregatePhysics(Number(seed.slice(1)),force);}else{applyForces();reheat();}}
  }
  function relation(e){return options.relations[ArchiveCore.relationKey(e.semantic,e.type)]||(relationAliases.get(e.semantic+'|'+e.type)===ArchiveCore.relationKey(e.semantic,e.type)?options.relations[e.semantic+'|'+e.type]:null)||options.relations[e.family]||{mode:'none',strength:1};}
  function active(e){const r=relation(e);return (options.membership||!e.membership)&&r.mode!=='none'&&r.strength>0;}
  function priority(a,b){return relation(b).strength-relation(a).strength||Number(b.family==='dependency')-Number(a.family==='dependency')||(a.index??0)-(b.index??0);}
  function rawTerm(e,i){const semantic=index.data.semantics[e[2]],type=index.data.edge_types[e[3]];return {source:e[0],target:e[1],semantic,type,family:ArchiveCore.family(semantic,type),membership:/snapshot_contains|membership|归属/i.test(semantic+' '+type),count:1,index:i};}
  function forceTerms(e){return (e.forceTerms||(!e.mixedRelation?[{semantic:e.semantic,type:e.type,membership:e.membership,count:e.count}]:[])).map(t=>({...t,family:ArchiveCore.family(t.semantic,t.type),source:e.a,target:e.b,index:e.index,bundleCount:e.count})).filter(active);}
  function cohesionGroup(point){return Object.hasOwn(point,'cohesion_group')?point.cohesion_group:point.group;}
  function clusterProvenance(ci){
    const identities=new Map(),stack=[ci];
    while(stack.length){const p=index.data.clusters[stack.pop()];for(const ni of p.nodes||[]){const n=index.data.nodes[ni],group=cohesionGroup(n),identity=n.source_id?['source',n.source_id]:group?['group',group]:null;identities.set(JSON.stringify(identity),identity);}for(const child of p.children||[])stack.push(child);}
    const identity=identities.size===1?[...identities.values()][0]:null;
    return identity?.[0]==='source'?{source_id:identity[1],cohesion_group:null}:{cohesion_group:identity?.[0]==='group'?identity[1]:null};
  }
  function weight(p){if(options.weightMode==='uniform')return 1;if(options.weightMode==='goal'){const v=p.goal_weights?.[options.goal];return Number.isFinite(v)&&v>=0?1+Math.min(5,v):1;}return Math.min(6,Math.sqrt((p.degree??p.count??0)+1));}
  function applyForces(){
    if(!sim)return;const coarse=sim===aggregate?.simulation,scale=coarse?10:1;
    sim.velocityDecay(options.damping).force('charge',d3.forceManyBody().strength(p=>-options.repelStrength*scale*weight(p))).force('collision',d3.forceCollide(coarse?0:12));
    // Preserve the archived separation so long historical spans cannot spring to 80px.
    const desired=e=>Math.max(e.restLength,80*scale*(e.family==='dependency'?.56:e.family==='history'?1.375:1))*(options.linkDistance/80);
    const contribution=e=>(e.count||1)/(e.bundleCount||1);
    sim.force('links',d3.forceLink(physicsLinks).id(p=>p.id).distance(desired).strength(e=>{const r=relation(e);return active(e)&&r.mode==='attract'?options.linkStrength*r.strength*contribution(e)*(coarse?.025:.12):0;}));
    sim.force('x',d3.forceX(p=>p.ax).strength(options.centerStrength)).force('y',d3.forceY(p=>p.ay).strength(options.centerStrength));
    const nodes=sim.nodes(),groups=new Map(),members=new Map(),ownMembers=new Map(),localIds=new Set(nodes.map(p=>p.id)),incidence=e=>/premise|conclusion|共同前提|结论/i.test(e.type||'');
    for(const p of nodes){const group=p.source_id||cohesionGroup(p);if(group){if(!groups.has(group))groups.set(group,[]);groups.get(group).push(p);}if(/hyperedge|junction|and.rule/i.test(p.type||'')){members.set(p.id,new Set());const own=new Set();if(!coarse)for(let j=index.counts[p.id];j<index.counts[p.id+1];j++){const ei=index.adjacency[j],e=rawTerm(index.data.edges[ei],ei),other=e.source===p.id?e.target:e.source;if(incidence(e)&&localIds.has(other))own.add(other);}ownMembers.set(p.id,own);}}
    for(const e of physicsLinks)if(incidence(e)){members.get(e.source.id)?.add(e.target.id);members.get(e.target.id)?.add(e.source.id);}
    sim.force('archive-controls',alpha=>{
      for(const e of physicsLinks){if(!active(e))continue;const r=relation(e),a=e.source,b=e.target,dx=b.x-a.x,dy=b.y-a.y,d=Math.max(1,Math.hypot(dx,dy));if(r.mode==='repel'&&d<desired(e)){const f=Math.min(20*scale,(desired(e)-d)*options.linkStrength*r.strength*contribution(e)*.06*alpha);a.vx-=dx/d*f;a.vy-=dy/d*f;b.vx+=dx/d*f;b.vy+=dy/d*f;}if(e.family==='dependency'&&options.flowStrength){const f=Math.min(10*scale,Math.max(0,options.linkDistance*scale-dx)*options.flowStrength*alpha)*contribution(e);a.vx-=f;b.vx+=f;}}
      // Cohesion shares displacement within the original group without collapsing
      // the archived geometry of records separated by thousands of world units.
      if(options.groupStrength)for(const group of groups.values()){if(group.length<2)continue;const x=group.reduce((s,p)=>s+p.x-p.ax,0)/group.length,y=group.reduce((s,p)=>s+p.y-p.ay,0)/group.length;for(const p of group){p.vx+=(x-(p.x-p.ax))*options.groupStrength*alpha;p.vy+=(y-(p.y-p.ay))*options.groupStrength*alpha;}}
      if(options.edgeRepulsion){
        const lookup=new Map(nodes.map(p=>[p.id,p])),clearance=options.edgeClearance*scale;
        // Only real local incidence links form the clearance geometry. Test its
        // segments as well as the hub; own premises/conclusions are exempt.
        for(const h of nodes){const linked=members.get(h.id);if(!linked)continue;for(const p of nodes){if(p===h||ownMembers.get(h.id)?.has(p.id))continue;let nearest={x:h.x,y:h.y,d:Math.hypot(p.x-h.x,p.y-h.y),t:0,end:null};
          for(const id of linked){const end=lookup.get(id);if(!end||p.x<Math.min(h.x,end.x)-clearance||p.x>Math.max(h.x,end.x)+clearance||p.y<Math.min(h.y,end.y)-clearance||p.y>Math.max(h.y,end.y)+clearance)continue;const dx=end.x-h.x,dy=end.y-h.y,length2=dx*dx+dy*dy,t=length2?Math.max(0,Math.min(1,((p.x-h.x)*dx+(p.y-h.y)*dy)/length2)):0,x=h.x+t*dx,y=h.y+t*dy,d=Math.hypot(p.x-x,p.y-y);if(d<nearest.d)nearest={x,y,d,t,end};}
          if(nearest.d>=clearance)continue;let dx=p.x-nearest.x,dy=p.y-nearest.y,d=nearest.d;if(d<1e-6){dx=nearest.end?-(nearest.end.y-h.y):1;dy=nearest.end?nearest.end.x-h.x:0;d=Math.hypot(dx,dy)||1;}const f=Math.min(20*scale,(clearance-nearest.d)*options.edgeRepulsion*alpha),fx=dx/d*f,fy=dy/d*f;p.vx+=fx;p.vy+=fy;h.vx-=fx*(1-nearest.t);h.vy-=fy*(1-nearest.t);if(nearest.end){nearest.end.vx-=fx*nearest.t;nearest.end.vy-=fy*nearest.t;}
        }}
      }
    });
  }
  function emitPhysics(settled){if(!sim)return;const points=sim.nodes(),coarse=sim===aggregate?.simulation,buffer=new Float32Array(points.length*2);points.forEach((p,i)=>{buffer[i*2]=p.x;buffer[i*2+1]=p.y;});self.postMessage({type:'physics',ids:points.map(p=>coarse?index.levels[aggregate.level].points[p.id].id:'n'+p.id),buffer:buffer.buffer,nodes:points.length,edges:physicsEdges.length,springs:physicsLinks.length,steps,settled,paused,stableViewport:!settled,aggregateLevel:coarse?aggregate.level:null,clusterShifts:coarse?points.map(p=>({index:index.levels[aggregate.level].points[p.id].index,dx:p.x-p.ox,dy:p.y-p.oy})):[]},[buffer.buffer]);}
  function tick(){timer=null;if(!sim||paused)return;sim.tick();steps++;if(sim===aggregate?.simulation)aggregate.dirty=true;else for(const p of sim.nodes())pendingPositions.set(p.id,{x:p.x,y:p.y});const settled=sim.alpha()<.005;emitPhysics(settled);if(settled){if(sim===aggregate?.simulation)commitAggregate();else commitRaw();}else timer=setTimeout(tick,35);}
  function reheat(){if(!sim||paused||commitJob)return;sim.alpha(Math.max(sim.alpha(),.4));if(timer===null)timer=setTimeout(tick,0);}
  function anchor(seed,force){const p=sim.nodes().find(p=>p.id===seed);if(!p)return;if(force.x===null?force.y!==null:!Number.isFinite(force.x)||!Number.isFinite(force.y))throw Error('Drag needs finite coordinates or a null release');p.fx=force.x;p.fy=force.y;if(force.x!==null){p.x=force.x;p.y=force.y;}else{p.ax=p.x;p.ay=p.y;p.vx=p.vy=0;sim.force('x').x(p=>p.ax);sim.force('y').y(p=>p.ay);}if(sim===aggregate?.simulation)aggregate.dirty=true;else pendingPositions.set(p.id,{x:p.x,y:p.y});reheat();}
  function physics(seed,force){
    if(!index||typeof d3==='undefined')return;if(!Number.isInteger(seed)||seed<0||seed>=index.data.nodes.length)throw Error('Node index out of range');
    if(force.x===null&&(!sim||sim===aggregate?.simulation||simSeed!=='n'+seed))return;
    if(sim===aggregate?.simulation){commitAggregate();if(commitJob){deferredMotion={type:'drag',id:'n'+seed,...force};return;}}
    if(!sim||sim===aggregate?.simulation||simSeed!=='n'+seed){
      stop();const ids=new Set([seed]),neighbors=[];for(let j=index.counts[seed];j<index.counts[seed+1];j++){const ei=index.adjacency[j],e=rawTerm(index.data.edges[ei],ei);if(active(e))neighbors.push(e);}neighbors.sort(priority);for(const e of neighbors){if(ids.size>=512)break;ids.add(e.source);ids.add(e.target);}
      physicsIds=[...ids];const chosen=new Set(physicsIds),points=physicsIds.map(i=>({...index.levels[3].points[i],id:i,...pendingPositions.get(i),ax:pendingPositions.get(i)?.x??index.levels[3].points[i].x,ay:pendingPositions.get(i)?.y??index.levels[3].points[i].y}));
      const seen=new Set(),links=[];const incident=new Set();for(const i of physicsIds)for(let j=index.counts[i];j<index.counts[i+1];j++){const ei=index.adjacency[j],e=index.data.edges[ei];incident.add(ei);if(!seen.has(ei)&&chosen.has(e[0])&&chosen.has(e[1])){seen.add(ei);const term=rawTerm(e,ei),a=index.levels[3].points[e[0]],b=index.levels[3].points[e[1]];if(active(term))links.push({...term,restLength:Math.hypot(a.x-b.x,a.y-b.y)});}}
      physicsLinks=links.sort(priority).slice(0,1024);physicsEdges=[...incident];for(const ei of physicsEdges)dirtyEdges.add(ei);sim=d3.forceSimulation(points).stop().alphaDecay(.045);simSeed='n'+seed;steps=0;applyForces();
    }
    anchor(seed,force);if(force.released)anchor(seed,{x:null,y:null});
  }
  function aggregatePhysics(clusterIndex,force){
    if(!index||typeof d3==='undefined')return;const cluster=index.data.clusters[clusterIndex];if(!cluster)throw Error('Cluster index out of range');const level=cluster.level,stage=index.levels[level],seed=stage.points.findIndex(p=>p.index===clusterIndex);
    if(force.x===null&&(!aggregate||aggregate.level!==level||simSeed!=='c'+clusterIndex))return;
    if(!aggregate||sim!==aggregate.simulation||aggregate.level!==level||simSeed!=='c'+clusterIndex){
      stop();if(sim&&sim!==aggregate?.simulation)commitRaw();commitAggregate();if(commitJob){deferredMotion={type:'drag',id:'c'+clusterIndex,...force};return;}const ids=new Set([seed]),neighbors=[];for(const e of stage.edges)if(e.a===seed||e.b===seed)neighbors.push(...forceTerms(e));neighbors.sort(priority);for(const e of neighbors){if(ids.size>=512)break;ids.add(e.source);ids.add(e.target);}
      const points=[...ids].map(id=>({...stage.points[id],...clusterProvenance(stage.points[id].index),id,ox:stage.points[id].x,oy:stage.points[id].y,ax:stage.points[id].x,ay:stage.points[id].y})),links=[];for(const e of stage.edges)if(ids.has(e.a)&&ids.has(e.b))for(const term of forceTerms(e))links.push({...term,restLength:Math.hypot(stage.points[e.a].x-stage.points[e.b].x,stage.points[e.a].y-stage.points[e.b].y)});physicsLinks=links.sort(priority).slice(0,1024);aggregate={level,points,dirty:false,simulation:d3.forceSimulation(points).stop().alphaDecay(.06)};sim=aggregate.simulation;simSeed='c'+clusterIndex;physicsIds=[];physicsEdges=[];steps=0;applyForces();
    }
    anchor(seed,force);if(force.released)anchor(seed,{x:null,y:null});
  }
  function explicitReheat(id){
    if(!index)return;
    // Only the explicit layout command seeds a first local simulation. Initial
    // configure messages retain the archived layout without starting any timer.
    if(id===undefined&&sim){reheat();return;}
    if(id===undefined){const primary=index.data.nodes.findIndex(n=>n.primary);id='n'+Math.max(0,primary);}
    if(typeof id!=='string'||!/^[nc]\d+$/.test(id))throw Error('Invalid layout seed');
    const seed=Number(id.slice(1));
    if(id[0]==='n'){
      const point=pendingPositions.get(seed)||index.levels[3].points[seed];if(!point)throw Error('Node index out of range');
      physics(seed,{x:point.x,y:point.y,released:true});
    }else{
      const cluster=index.data.clusters[seed];if(!cluster)throw Error('Cluster index out of range');
      const stage=index.levels[cluster.level],position=stage.points.findIndex(p=>p.index===seed);
      const point=aggregate?.level===cluster.level&&sim===aggregate.simulation?aggregate.points.find(p=>p.id===position)||stage.points[position]:stage.points[position];
      aggregatePhysics(seed,{x:point.x,y:point.y,released:true});
    }
  }
  function livePosition(i){
    const old=index.levels[3].points[i];if(!old)throw Error('Node index out of range');
    if(commitJob){const s=commitJob.shifts.get(commitJob.stage.nodeMap[i]);return {x:old.x+(s?.x||0),y:old.y+(s?.y||0)};}
    if(aggregate?.dirty){const mapped=index.levels[aggregate.level].nodeMap[i],p=aggregate.points.find(p=>p.id===mapped);if(p)return {x:old.x+p.x-p.ox,y:old.y+p.y-p.oy};}
    return pendingPositions.get(i)||old;
  }
  self.onmessage=async event=>{const m=event.data;try{
    if(commitJob&&['drag','resume','reheat'].includes(m.type)){
      if(m.type==='drag'&&m.x===null){if(deferredMotion?.type==='drag'&&deferredMotion.id===m.id)deferredMotion={...deferredMotion,released:true};else if(!deferredMotion&&simSeed===m.id)deferredMotion=m;}
      else deferredMotion=m;return;
    }
    if(m.type==='init'){
      stop();if(repairTimer!==null)clearTimeout(repairTimer);if(commitTimer!==null)clearTimeout(commitTimer);commitTimer=null;commitJob=null;deferredMotion=null;dirtyStageEdges.clear();dirtyStageNodes.clear();blockedLevels.clear();levelRemaining.clear();repairTimer=null;sim=null;aggregate=null;physicsIds=[];physicsEdges=[];physicsLinks=[];dirtyEdges.clear();pendingPositions.clear();repairList=[];repairCursor=0;waitingViews=[];paused=false;revision=0;
      const data=await unpack(m.compressed);index=ArchiveCore.create(data,p=>send('progress',p));const catalog=new Map(),goals=new Set();relationAliases=new Map();for(const n of data.nodes)for(const [key,value] of Object.entries(n.goal_weights||{}))if(Number.isFinite(value)&&value>=0)goals.add(key);for(const e of data.edges){const semantic=data.semantics[e[2]],type=data.edge_types[e[3]],key=ArchiveCore.relationKey(semantic,type),legacyKey=semantic+'|'+type;if(!relationAliases.has(legacyKey))relationAliases.set(legacyKey,key);else if(relationAliases.get(legacyKey)!==key)relationAliases.set(legacyKey,null);if(!catalog.has(key))catalog.set(key,{key,semantic,type,family:ArchiveCore.family(semantic,type),count:0});catalog.get(key).count++;}
      const exactKeys=new Set([...catalog.keys(),...Object.keys(defaults.relations),'mixed']);for(const [alias] of relationAliases)if(exactKeys.has(alias))relationAliases.set(alias,null);
      send('ready',{source:data.source,bounds:data.bounds,roots:data.roots,relationCatalog:[...catalog.values()].map(r=>({...r,legacyKey:relationAliases.get(r.semantic+'|'+r.type)===r.key?r.semantic+'|'+r.type:null})),goals:[...goals].map(id=>({id,label:data.goals?.find(g=>g.id===id)?.label||id})),options,indexStats:{...index.indexStats},levels:index.levels.map(s=>({points:s.points.length,edges:s.edges.length,internal:s.internal,spatialReady:!!s.spatial}))});
    }else if(m.type==='viewport'){
      if((repairTimer!==null||commitJob)&&blockedLevels.has(m.level)||commitJob&&m.level===3&&!index.levels[3].spatial){for(const old of waitingViews)send('viewport',{cancelled:true},old.request);waitingViews=[m];}else viewport(m);
    }else if(m.type==='configure'){configure(m.options||{});send('configured',{options},m.request);
    }else if(m.type==='search')send('search',index.find(m.query,m.page),m.request);
    else if(m.type==='detail')send('detail',index.neighbors(m.index,m.page),m.request);
    else if(m.type==='locate'){const n=index.data.nodes[m.index],p=livePosition(m.index);send('locate',{index:m.index,x:p.x,y:p.y,parent:n.parent,raw_node_index:n.raw_node_index,raw_hyperedge_id:n.raw_hyperedge_id},m.request);}
    else if(m.type==='raw-locator')send('raw-locator',{index:index.data.edges[m.index][4]},m.request);
    else if(m.type==='cluster')send('cluster',index.clusterView(m.index,m.filters||{},livePosition),m.request);
    else if(m.type==='cell-members')send('cell-members',index.cellMembers(m.cell,m.filters||{},m.page),m.request);
    else if(m.type==='highlight'){const found=new Set([m.index]),edges=[];for(let j=index.counts[m.index];j<index.counts[m.index+1];j++){const ei=index.adjacency[j],e=index.data.edges[ei];found.add(e[0]);found.add(e[1]);edges.push(ei);}send('highlight',{nodes:[...found],edges},m.request);}
    else if(m.type==='drag'&&m.id.startsWith('n'))physics(Number(m.id.slice(1)),{x:m.x,y:m.y,released:!!m.released});
    else if(m.type==='drag'&&m.id.startsWith('c'))aggregatePhysics(Number(m.id.slice(1)),{x:m.x,y:m.y,released:!!m.released});
    else if(m.type==='pause'){deferredMotion=null;paused=true;stop();if(sim){for(const p of sim.nodes()){p.fx=p.fy=null;p.ax=p.x;p.ay=p.y;}sim.force('x').x(p=>p.ax);sim.force('y').y(p=>p.ay);}emitPhysics(true);if(aggregate?.dirty)commitAggregate();else commitRaw();}
    else if(m.type==='resume'){paused=false;reheat();}
    else if(m.type==='reheat'){paused=false;explicitReheat(m.id);}
    else if(m.type==='raw-init'){raw=await unpack(m.compressed);send('raw-ready',{});}
    else if(m.type==='raw-node')send('raw-result',{value:(raw.nodes||raw.dependency_map?.nodes||[])[m.index]},m.request);
    else if(m.type==='raw-hyperedge')send('raw-result',{value:(raw.hyperedges||raw.dependency_map?.hyperedges||[]).find(e=>e.id===m.id)},m.request);
    else if(m.type==='raw-edge')send('raw-result',{value:(raw.edges||raw.hyperedges||raw.dependency_map?.hyperedges||[])[m.index]},m.request);
  }catch(error){send('error',{message:String(error.message||error)},m.request);}};
}
