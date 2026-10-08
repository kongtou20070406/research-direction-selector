#!/usr/bin/env python3
"""Export a standalone, offline, read-only force-directed hypergraph explorer."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys

from rds_hypergraph import ASSURANCE, _validate, analyze_hypergraph
from rds_hypergraph_input import load_input, prepare_input

MAX_INPUT_BYTES = 8 * 1024 * 1024
VIEW_LIMITS = {"nodes": 4096, "hyperedges": 8192, "incidences": 32768}
ANALYSIS_LIMITS = {"nodes": 200, "hyperedges": 400, "incidences": 2000}


def graph_view(value, source, *, snapshot_sha256=None, demo=False):
    """Render large maps without requiring combinatorial blocker analysis."""
    spec, review = prepare_input(value, str(source))
    if review["errors"]:
        raise ValueError("Hypergraph input: " + json.dumps(review["errors"], ensure_ascii=False))
    _validate(spec)
    counts = {"nodes": len(spec["nodes"]), "hyperedges": len(spec["hyperedges"]),
              "incidences": sum(len(e["premises"]) + 1 for e in spec["hyperedges"])}
    result = {"status": "AVAILABLE", "source": str(source), "snapshot_sha256": snapshot_sha256,
              "demo": demo, "assurance": ASSURANCE, "counts": counts, "limits": VIEW_LIMITS,
              "input_review": review, "receipt_audit": "NOT_RUN", "analysis": None}
    if any(counts[key] > cap for key, cap in VIEW_LIMITS.items()):
        return {**result, "status": "DISPLAY_LIMIT", "graph": None, "analysis_status": "NOT_RUN"}
    result["graph"] = spec
    if any(counts[key] > cap for key, cap in ANALYSIS_LIMITS.items()):
        return {**result, "analysis_status": "NOT_RUN_LARGE_GRAPH"}
    bounded = deepcopy(spec)
    limits = bounded.setdefault("limits", {})
    limits["max_combinations"] = min(limits.get("max_combinations", 50000), 50000)
    limits["max_blocker_sets"] = min(limits.get("max_blocker_sets", 128), 128)
    result["analysis"] = analyze_hypergraph(bounded)
    result["analysis_status"] = "INCOMPLETE" if result["analysis"]["truncated"] else "COMPLETE"
    return result


def large_demo():
    """1,200 synthetic claims; hub/cluster structure, never scientific evidence."""
    nodes = [{"id": "research-map", "label": "研究地图", "status": "UNKNOWN", "source": "synthetic:map"},
             {"id": "research-question", "label": "研究问题", "status": "UNKNOWN", "source": "synthetic:question"}]
    edges = []
    topics = ["表征学习", "因果推断", "优化方法", "实验设计", "统计检验", "机制解释", "数据质量", "泛化边界",
              "鲁棒性", "计算预算", "结构先验", "理论约束", "基线比较", "误差分析", "评估协议", "负面结果",
              "可复现性", "不确定性", "研究假设", "证据追踪", "替代解释", "模型结构", "任务收益", "后续问题"]
    for i, topic in enumerate(topics):
        ident = f"topic-{i:02d}"
        nodes.append({"id": ident, "label": topic, "status": "UNKNOWN", "source": "synthetic:topic"})
        edges.append({"id": f"route-{i:02d}", "premises": ["research-map", "research-question"],
                      "conclusion": ident, "status": "PROPOSED", "relation": "研究依赖", "source": "synthetic:route"})
    for i in range(1174):
        ident, topic = f"N{i+1:04d}", f"topic-{i % 24:02d}"
        status = ("UNKNOWN", "UNKNOWN", "SUPPORTED", "UNKNOWN", "CONTRADICTED")[i % 5]
        nodes.append({"id": ident, "label": ident, "status": status,
                      "source": f"synthetic:claim/{i+1}", "description": f"{topics[i % 24]}的合成节点，仅用于大图界面与性能验证。"})
        edges.append({"id": f"E{i+1:04d}", "premises": [topic], "conclusion": ident,
                      "status": "PROPOSED" if i % 3 else "SUPPORTED", "relation": "主题关联", "source": "synthetic:relation"})
        if i > 30 and i % 5 == 0:
            edges.append({"id": f"X{i:04d}", "premises": [f"N{i-23:04d}", f"N{i-7:04d}"],
                          "conclusion": ident, "status": "PROPOSED", "relation": "竞争解释" if i % 20 == 0 else "共同前提", "source": "synthetic:cross-link"})
    return {"schema": 1, "scope": "SYNTHETIC PERFORMANCE / VISUAL FIXTURE; NO SCIENTIFIC RESULTS",
            "nodes": nodes, "hyperedges": edges, "goals": ["research-map"],
            "limits": {"max_nodes": 4096, "max_hyperedges": 8192}}


def read_graph(root, graph_path=None, *, demo=False, large=False):
    """Read only the saved snapshot and bound CAS, without an external evidence audit."""
    try:
        if demo and large:
            return graph_view(large_demo(), "synthetic:1200-node-preview", demo=True)
        if demo:
            graph_path = Path(__file__).resolve().parents[1] / "examples" / "hypergraph-view.json"
        if graph_path is not None:
            path = Path(graph_path).resolve()
            with path.open("rb") as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
            if len(raw) > MAX_INPUT_BYTES:
                raise ValueError("Hypergraph input exceeds 8 MiB")
            value, repairs = load_input(raw.decode("utf-8-sig"))
            result = graph_view(value, path, demo=demo)
            result["input_review"]["repairs"][:0] = [{"path": "$", "reason": repair} for repair in repairs]
            return result
        from rds_tms_store import current
        saved = current(root)
        if saved is None:
            return {"status": "MISSING", "graph": None, "source": None, "demo": False}
        return graph_view(saved["dependency_map"], Path(root).resolve() / ".rds" / "project.sqlite3",
                          snapshot_sha256=saved["sha256"])
    except (OSError, ValueError, KeyError, TypeError, RecursionError, sqlite3.Error) as exc:
        return {"status": "UNAVAILABLE", "graph": None, "source": str(graph_path or root), "reason": str(exc), "demo": demo}


def render_html(result):
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c")
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    # Substitute code before data so user fields cannot become template tokens.
    return (HTML.replace("__GRAPH_CSS__", CSS).replace("__GRAPH_JS__", JS)
            .replace("__D3_BUNDLE__", D3_BUNDLE).replace("__GRAPH_DATA__", payload))


# Official UMD distributions, pinned and embedded for a single-file offline export.
# Refresh from npm tarballs only; retain each upstream ISC license and version.
D3_BUNDLE = r'''/* Vendored d3-dispatch@3.0.1; npm tarball SHA-1 5fc75284e9c2375c36c839411a0cf550cbfc4d5e.
Copyright 2010-2021 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

*/
// https://d3js.org/d3-dispatch/ v3.0.1 Copyright 2010-2021 Mike Bostock
!function(n,e){"object"==typeof exports&&"undefined"!=typeof module?e(exports):"function"==typeof define&&define.amd?define(["exports"],e):e((n="undefined"!=typeof globalThis?globalThis:n||self).d3=n.d3||{})}(this,(function(n){"use strict";var e={value:()=>{}};function t(){for(var n,e=0,t=arguments.length,o={};e<t;++e){if(!(n=arguments[e]+"")||n in o||/[\s.]/.test(n))throw new Error("illegal type: "+n);o[n]=[]}return new r(o)}function r(n){this._=n}function o(n,e){return n.trim().split(/^|\s+/).map((function(n){var t="",r=n.indexOf(".");if(r>=0&&(t=n.slice(r+1),n=n.slice(0,r)),n&&!e.hasOwnProperty(n))throw new Error("unknown type: "+n);return{type:n,name:t}}))}function i(n,e){for(var t,r=0,o=n.length;r<o;++r)if((t=n[r]).name===e)return t.value}function f(n,t,r){for(var o=0,i=n.length;o<i;++o)if(n[o].name===t){n[o]=e,n=n.slice(0,o).concat(n.slice(o+1));break}return null!=r&&n.push({name:t,value:r}),n}r.prototype=t.prototype={constructor:r,on:function(n,e){var t,r=this._,l=o(n+"",r),a=-1,u=l.length;if(!(arguments.length<2)){if(null!=e&&"function"!=typeof e)throw new Error("invalid callback: "+e);for(;++a<u;)if(t=(n=l[a]).type)r[t]=f(r[t],n.name,e);else if(null==e)for(t in r)r[t]=f(r[t],n.name,null);return this}for(;++a<u;)if((t=(n=l[a]).type)&&(t=i(r[t],n.name)))return t},copy:function(){var n={},e=this._;for(var t in e)n[t]=e[t].slice();return new r(n)},call:function(n,e){if((t=arguments.length-2)>0)for(var t,r,o=new Array(t),i=0;i<t;++i)o[i]=arguments[i+2];if(!this._.hasOwnProperty(n))throw new Error("unknown type: "+n);for(i=0,t=(r=this._[n]).length;i<t;++i)r[i].value.apply(e,o)},apply:function(n,e,t){if(!this._.hasOwnProperty(n))throw new Error("unknown type: "+n);for(var r=this._[n],o=0,i=r.length;o<i;++o)r[o].value.apply(e,t)}},n.dispatch=t,Object.defineProperty(n,"__esModule",{value:!0})}));

/* Vendored d3-quadtree@3.0.1; npm tarball SHA-1 6dca3e8be2b393c9a9d514dabbd80a92deef1a4f.
Copyright 2010-2021 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

*/
// https://d3js.org/d3-quadtree/ v3.0.1 Copyright 2010-2021 Mike Bostock
!function(t,i){"object"==typeof exports&&"undefined"!=typeof module?i(exports):"function"==typeof define&&define.amd?define(["exports"],i):i((t="undefined"!=typeof globalThis?globalThis:t||self).d3=t.d3||{})}(this,(function(t){"use strict";function i(t,i,e,n){if(isNaN(i)||isNaN(e))return t;var r,s,h,o,a,u,l,_,f,c=t._root,x={data:n},y=t._x0,d=t._y0,p=t._x1,v=t._y1;if(!c)return t._root=x,t;for(;c.length;)if((u=i>=(s=(y+p)/2))?y=s:p=s,(l=e>=(h=(d+v)/2))?d=h:v=h,r=c,!(c=c[_=l<<1|u]))return r[_]=x,t;if(o=+t._x.call(null,c.data),a=+t._y.call(null,c.data),i===o&&e===a)return x.next=c,r?r[_]=x:t._root=x,t;do{r=r?r[_]=new Array(4):t._root=new Array(4),(u=i>=(s=(y+p)/2))?y=s:p=s,(l=e>=(h=(d+v)/2))?d=h:v=h}while((_=l<<1|u)==(f=(a>=h)<<1|o>=s));return r[f]=c,r[_]=x,t}function e(t,i,e,n,r){this.node=t,this.x0=i,this.y0=e,this.x1=n,this.y1=r}function n(t){return t[0]}function r(t){return t[1]}function s(t,i,e){var s=new h(null==i?n:i,null==e?r:e,NaN,NaN,NaN,NaN);return null==t?s:s.addAll(t)}function h(t,i,e,n,r,s){this._x=t,this._y=i,this._x0=e,this._y0=n,this._x1=r,this._y1=s,this._root=void 0}function o(t){for(var i={data:t.data},e=i;t=t.next;)e=e.next={data:t.data};return i}var a=s.prototype=h.prototype;a.copy=function(){var t,i,e=new h(this._x,this._y,this._x0,this._y0,this._x1,this._y1),n=this._root;if(!n)return e;if(!n.length)return e._root=o(n),e;for(t=[{source:n,target:e._root=new Array(4)}];n=t.pop();)for(var r=0;r<4;++r)(i=n.source[r])&&(i.length?t.push({source:i,target:n.target[r]=new Array(4)}):n.target[r]=o(i));return e},a.add=function(t){const e=+this._x.call(null,t),n=+this._y.call(null,t);return i(this.cover(e,n),e,n,t)},a.addAll=function(t){var e,n,r,s,h=t.length,o=new Array(h),a=new Array(h),u=1/0,l=1/0,_=-1/0,f=-1/0;for(n=0;n<h;++n)isNaN(r=+this._x.call(null,e=t[n]))||isNaN(s=+this._y.call(null,e))||(o[n]=r,a[n]=s,r<u&&(u=r),r>_&&(_=r),s<l&&(l=s),s>f&&(f=s));if(u>_||l>f)return this;for(this.cover(u,l).cover(_,f),n=0;n<h;++n)i(this,o[n],a[n],t[n]);return this},a.cover=function(t,i){if(isNaN(t=+t)||isNaN(i=+i))return this;var e=this._x0,n=this._y0,r=this._x1,s=this._y1;if(isNaN(e))r=(e=Math.floor(t))+1,s=(n=Math.floor(i))+1;else{for(var h,o,a=r-e||1,u=this._root;e>t||t>=r||n>i||i>=s;)switch(o=(i<n)<<1|t<e,(h=new Array(4))[o]=u,u=h,a*=2,o){case 0:r=e+a,s=n+a;break;case 1:e=r-a,s=n+a;break;case 2:r=e+a,n=s-a;break;case 3:e=r-a,n=s-a}this._root&&this._root.length&&(this._root=u)}return this._x0=e,this._y0=n,this._x1=r,this._y1=s,this},a.data=function(){var t=[];return this.visit((function(i){if(!i.length)do{t.push(i.data)}while(i=i.next)})),t},a.extent=function(t){return arguments.length?this.cover(+t[0][0],+t[0][1]).cover(+t[1][0],+t[1][1]):isNaN(this._x0)?void 0:[[this._x0,this._y0],[this._x1,this._y1]]},a.find=function(t,i,n){var r,s,h,o,a,u,l,_=this._x0,f=this._y0,c=this._x1,x=this._y1,y=[],d=this._root;for(d&&y.push(new e(d,_,f,c,x)),null==n?n=1/0:(_=t-n,f=i-n,c=t+n,x=i+n,n*=n);u=y.pop();)if(!(!(d=u.node)||(s=u.x0)>c||(h=u.y0)>x||(o=u.x1)<_||(a=u.y1)<f))if(d.length){var p=(s+o)/2,v=(h+a)/2;y.push(new e(d[3],p,v,o,a),new e(d[2],s,v,p,a),new e(d[1],p,h,o,v),new e(d[0],s,h,p,v)),(l=(i>=v)<<1|t>=p)&&(u=y[y.length-1],y[y.length-1]=y[y.length-1-l],y[y.length-1-l]=u)}else{var w=t-+this._x.call(null,d.data),N=i-+this._y.call(null,d.data),g=w*w+N*N;if(g<n){var A=Math.sqrt(n=g);_=t-A,f=i-A,c=t+A,x=i+A,r=d.data}}return r},a.remove=function(t){if(isNaN(s=+this._x.call(null,t))||isNaN(h=+this._y.call(null,t)))return this;var i,e,n,r,s,h,o,a,u,l,_,f,c=this._root,x=this._x0,y=this._y0,d=this._x1,p=this._y1;if(!c)return this;if(c.length)for(;;){if((u=s>=(o=(x+d)/2))?x=o:d=o,(l=h>=(a=(y+p)/2))?y=a:p=a,i=c,!(c=c[_=l<<1|u]))return this;if(!c.length)break;(i[_+1&3]||i[_+2&3]||i[_+3&3])&&(e=i,f=_)}for(;c.data!==t;)if(n=c,!(c=c.next))return this;return(r=c.next)&&delete c.next,n?(r?n.next=r:delete n.next,this):i?(r?i[_]=r:delete i[_],(c=i[0]||i[1]||i[2]||i[3])&&c===(i[3]||i[2]||i[1]||i[0])&&!c.length&&(e?e[f]=c:this._root=c),this):(this._root=r,this)},a.removeAll=function(t){for(var i=0,e=t.length;i<e;++i)this.remove(t[i]);return this},a.root=function(){return this._root},a.size=function(){var t=0;return this.visit((function(i){if(!i.length)do{++t}while(i=i.next)})),t},a.visit=function(t){var i,n,r,s,h,o,a=[],u=this._root;for(u&&a.push(new e(u,this._x0,this._y0,this._x1,this._y1));i=a.pop();)if(!t(u=i.node,r=i.x0,s=i.y0,h=i.x1,o=i.y1)&&u.length){var l=(r+h)/2,_=(s+o)/2;(n=u[3])&&a.push(new e(n,l,_,h,o)),(n=u[2])&&a.push(new e(n,r,_,l,o)),(n=u[1])&&a.push(new e(n,l,s,h,_)),(n=u[0])&&a.push(new e(n,r,s,l,_))}return this},a.visitAfter=function(t){var i,n=[],r=[];for(this._root&&n.push(new e(this._root,this._x0,this._y0,this._x1,this._y1));i=n.pop();){var s=i.node;if(s.length){var h,o=i.x0,a=i.y0,u=i.x1,l=i.y1,_=(o+u)/2,f=(a+l)/2;(h=s[0])&&n.push(new e(h,o,a,_,f)),(h=s[1])&&n.push(new e(h,_,a,u,f)),(h=s[2])&&n.push(new e(h,o,f,_,l)),(h=s[3])&&n.push(new e(h,_,f,u,l))}r.push(i)}for(;i=r.pop();)t(i.node,i.x0,i.y0,i.x1,i.y1);return this},a.x=function(t){return arguments.length?(this._x=t,this):this._x},a.y=function(t){return arguments.length?(this._y=t,this):this._y},t.quadtree=s,Object.defineProperty(t,"__esModule",{value:!0})}));

/* Vendored d3-timer@3.0.1; npm tarball SHA-1 6284d2a2708285b1abb7e201eda4380af35e63b0.
Copyright 2010-2021 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

*/
// https://d3js.org/d3-timer/ v3.0.1 Copyright 2010-2021 Mike Bostock
!function(t,n){"object"==typeof exports&&"undefined"!=typeof module?n(exports):"function"==typeof define&&define.amd?define(["exports"],n):n((t="undefined"!=typeof globalThis?globalThis:t||self).d3=t.d3||{})}(this,(function(t){"use strict";var n,e,o=0,i=0,r=0,l=0,u=0,a=0,s="object"==typeof performance&&performance.now?performance:Date,c="object"==typeof window&&window.requestAnimationFrame?window.requestAnimationFrame.bind(window):function(t){setTimeout(t,17)};function f(){return u||(c(_),u=s.now()+a)}function _(){u=0}function m(){this._call=this._time=this._next=null}function p(t,n,e){var o=new m;return o.restart(t,n,e),o}function w(){f(),++o;for(var t,e=n;e;)(t=u-e._time)>=0&&e._call.call(void 0,t),e=e._next;--o}function d(){u=(l=s.now())+a,o=i=0;try{w()}finally{o=0,function(){var t,o,i=n,r=1/0;for(;i;)i._call?(r>i._time&&(r=i._time),t=i,i=i._next):(o=i._next,i._next=null,i=t?t._next=o:n=o);e=t,y(r)}(),u=0}}function h(){var t=s.now(),n=t-l;n>1e3&&(a-=n,l=t)}function y(t){o||(i&&(i=clearTimeout(i)),t-u>24?(t<1/0&&(i=setTimeout(d,t-s.now()-a)),r&&(r=clearInterval(r))):(r||(l=s.now(),r=setInterval(h,1e3)),o=1,c(d)))}m.prototype=p.prototype={constructor:m,restart:function(t,o,i){if("function"!=typeof t)throw new TypeError("callback is not a function");i=(null==i?f():+i)+(null==o?0:+o),this._next||e===this||(e?e._next=this:n=this,e=this),this._call=t,this._time=i,y()},stop:function(){this._call&&(this._call=null,this._time=1/0,y())}},t.interval=function(t,n,e){var o=new m,i=n;return null==n?(o.restart(t,n,e),o):(o._restart=o.restart,o.restart=function(t,n,e){n=+n,e=null==e?f():+e,o._restart((function r(l){l+=i,o._restart(r,i+=n,e),t(l)}),n,e)},o.restart(t,n,e),o)},t.now=f,t.timeout=function(t,n,e){var o=new m;return n=null==n?0:+n,o.restart((e=>{o.stop(),t(e+n)}),n,e),o},t.timer=p,t.timerFlush=w,Object.defineProperty(t,"__esModule",{value:!0})}));

/* Vendored d3-force@3.0.0; npm tarball SHA-1 3e2ba1a61e70888fe3d9194e30d6d14eece155c4.
Copyright 2010-2021 Mike Bostock

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

*/
// https://d3js.org/d3-force/ v3.0.0 Copyright 2010-2021 Mike Bostock
!function(n,t){"object"==typeof exports&&"undefined"!=typeof module?t(exports,require("d3-quadtree"),require("d3-dispatch"),require("d3-timer")):"function"==typeof define&&define.amd?define(["exports","d3-quadtree","d3-dispatch","d3-timer"],t):t((n="undefined"!=typeof globalThis?globalThis:n||self).d3=n.d3||{},n.d3,n.d3,n.d3)}(this,(function(n,t,e,r){"use strict";function i(n){return function(){return n}}function u(n){return 1e-6*(n()-.5)}function o(n){return n.x+n.vx}function f(n){return n.y+n.vy}function a(n){return n.index}function c(n,t){var e=n.get(t);if(!e)throw new Error("node not found: "+t);return e}const l=4294967296;function h(n){return n.x}function v(n){return n.y}var y=Math.PI*(3-Math.sqrt(5));n.forceCenter=function(n,t){var e,r=1;function i(){var i,u,o=e.length,f=0,a=0;for(i=0;i<o;++i)f+=(u=e[i]).x,a+=u.y;for(f=(f/o-n)*r,a=(a/o-t)*r,i=0;i<o;++i)(u=e[i]).x-=f,u.y-=a}return null==n&&(n=0),null==t&&(t=0),i.initialize=function(n){e=n},i.x=function(t){return arguments.length?(n=+t,i):n},i.y=function(n){return arguments.length?(t=+n,i):t},i.strength=function(n){return arguments.length?(r=+n,i):r},i},n.forceCollide=function(n){var e,r,a,c=1,l=1;function h(){for(var n,i,h,y,d,g,x,s=e.length,p=0;p<l;++p)for(i=t.quadtree(e,o,f).visitAfter(v),n=0;n<s;++n)h=e[n],g=r[h.index],x=g*g,y=h.x+h.vx,d=h.y+h.vy,i.visit(M);function M(n,t,e,r,i){var o=n.data,f=n.r,l=g+f;if(!o)return t>y+l||r<y-l||e>d+l||i<d-l;if(o.index>h.index){var v=y-o.x-o.vx,s=d-o.y-o.vy,p=v*v+s*s;p<l*l&&(0===v&&(p+=(v=u(a))*v),0===s&&(p+=(s=u(a))*s),p=(l-(p=Math.sqrt(p)))/p*c,h.vx+=(v*=p)*(l=(f*=f)/(x+f)),h.vy+=(s*=p)*l,o.vx-=v*(l=1-l),o.vy-=s*l)}}}function v(n){if(n.data)return n.r=r[n.data.index];for(var t=n.r=0;t<4;++t)n[t]&&n[t].r>n.r&&(n.r=n[t].r)}function y(){if(e){var t,i,u=e.length;for(r=new Array(u),t=0;t<u;++t)i=e[t],r[i.index]=+n(i,t,e)}}return"function"!=typeof n&&(n=i(null==n?1:+n)),h.initialize=function(n,t){e=n,a=t,y()},h.iterations=function(n){return arguments.length?(l=+n,h):l},h.strength=function(n){return arguments.length?(c=+n,h):c},h.radius=function(t){return arguments.length?(n="function"==typeof t?t:i(+t),y(),h):n},h},n.forceLink=function(n){var t,e,r,o,f,l,h=a,v=function(n){return 1/Math.min(o[n.source.index],o[n.target.index])},y=i(30),d=1;function g(r){for(var i=0,o=n.length;i<d;++i)for(var a,c,h,v,y,g,x,s=0;s<o;++s)c=(a=n[s]).source,v=(h=a.target).x+h.vx-c.x-c.vx||u(l),y=h.y+h.vy-c.y-c.vy||u(l),v*=g=((g=Math.sqrt(v*v+y*y))-e[s])/g*r*t[s],y*=g,h.vx-=v*(x=f[s]),h.vy-=y*x,c.vx+=v*(x=1-x),c.vy+=y*x}function x(){if(r){var i,u,a=r.length,l=n.length,v=new Map(r.map(((n,t)=>[h(n,t,r),n])));for(i=0,o=new Array(a);i<l;++i)(u=n[i]).index=i,"object"!=typeof u.source&&(u.source=c(v,u.source)),"object"!=typeof u.target&&(u.target=c(v,u.target)),o[u.source.index]=(o[u.source.index]||0)+1,o[u.target.index]=(o[u.target.index]||0)+1;for(i=0,f=new Array(l);i<l;++i)u=n[i],f[i]=o[u.source.index]/(o[u.source.index]+o[u.target.index]);t=new Array(l),s(),e=new Array(l),p()}}function s(){if(r)for(var e=0,i=n.length;e<i;++e)t[e]=+v(n[e],e,n)}function p(){if(r)for(var t=0,i=n.length;t<i;++t)e[t]=+y(n[t],t,n)}return null==n&&(n=[]),g.initialize=function(n,t){r=n,l=t,x()},g.links=function(t){return arguments.length?(n=t,x(),g):n},g.id=function(n){return arguments.length?(h=n,g):h},g.iterations=function(n){return arguments.length?(d=+n,g):d},g.strength=function(n){return arguments.length?(v="function"==typeof n?n:i(+n),s(),g):v},g.distance=function(n){return arguments.length?(y="function"==typeof n?n:i(+n),p(),g):y},g},n.forceManyBody=function(){var n,e,r,o,f,a=i(-30),c=1,l=1/0,y=.81;function d(r){var i,u=n.length,f=t.quadtree(n,h,v).visitAfter(x);for(o=r,i=0;i<u;++i)e=n[i],f.visit(s)}function g(){if(n){var t,e,r=n.length;for(f=new Array(r),t=0;t<r;++t)e=n[t],f[e.index]=+a(e,t,n)}}function x(n){var t,e,r,i,u,o=0,a=0;if(n.length){for(r=i=u=0;u<4;++u)(t=n[u])&&(e=Math.abs(t.value))&&(o+=t.value,a+=e,r+=e*t.x,i+=e*t.y);n.x=r/a,n.y=i/a}else{(t=n).x=t.data.x,t.y=t.data.y;do{o+=f[t.data.index]}while(t=t.next)}n.value=o}function s(n,t,i,a){if(!n.value)return!0;var h=n.x-e.x,v=n.y-e.y,d=a-t,g=h*h+v*v;if(d*d/y<g)return g<l&&(0===h&&(g+=(h=u(r))*h),0===v&&(g+=(v=u(r))*v),g<c&&(g=Math.sqrt(c*g)),e.vx+=h*n.value*o/g,e.vy+=v*n.value*o/g),!0;if(!(n.length||g>=l)){(n.data!==e||n.next)&&(0===h&&(g+=(h=u(r))*h),0===v&&(g+=(v=u(r))*v),g<c&&(g=Math.sqrt(c*g)));do{n.data!==e&&(d=f[n.data.index]*o/g,e.vx+=h*d,e.vy+=v*d)}while(n=n.next)}}return d.initialize=function(t,e){n=t,r=e,g()},d.strength=function(n){return arguments.length?(a="function"==typeof n?n:i(+n),g(),d):a},d.distanceMin=function(n){return arguments.length?(c=n*n,d):Math.sqrt(c)},d.distanceMax=function(n){return arguments.length?(l=n*n,d):Math.sqrt(l)},d.theta=function(n){return arguments.length?(y=n*n,d):Math.sqrt(y)},d},n.forceRadial=function(n,t,e){var r,u,o,f=i(.1);function a(n){for(var i=0,f=r.length;i<f;++i){var a=r[i],c=a.x-t||1e-6,l=a.y-e||1e-6,h=Math.sqrt(c*c+l*l),v=(o[i]-h)*u[i]*n/h;a.vx+=c*v,a.vy+=l*v}}function c(){if(r){var t,e=r.length;for(u=new Array(e),o=new Array(e),t=0;t<e;++t)o[t]=+n(r[t],t,r),u[t]=isNaN(o[t])?0:+f(r[t],t,r)}}return"function"!=typeof n&&(n=i(+n)),null==t&&(t=0),null==e&&(e=0),a.initialize=function(n){r=n,c()},a.strength=function(n){return arguments.length?(f="function"==typeof n?n:i(+n),c(),a):f},a.radius=function(t){return arguments.length?(n="function"==typeof t?t:i(+t),c(),a):n},a.x=function(n){return arguments.length?(t=+n,a):t},a.y=function(n){return arguments.length?(e=+n,a):e},a},n.forceSimulation=function(n){var t,i=1,u=.001,o=1-Math.pow(u,1/300),f=0,a=.6,c=new Map,h=r.timer(g),v=e.dispatch("tick","end"),d=function(){let n=1;return()=>(n=(1664525*n+1013904223)%l)/l}();function g(){x(),v.call("tick",t),i<u&&(h.stop(),v.call("end",t))}function x(e){var r,u,l=n.length;void 0===e&&(e=1);for(var h=0;h<e;++h)for(i+=(f-i)*o,c.forEach((function(n){n(i)})),r=0;r<l;++r)null==(u=n[r]).fx?u.x+=u.vx*=a:(u.x=u.fx,u.vx=0),null==u.fy?u.y+=u.vy*=a:(u.y=u.fy,u.vy=0);return t}function s(){for(var t,e=0,r=n.length;e<r;++e){if((t=n[e]).index=e,null!=t.fx&&(t.x=t.fx),null!=t.fy&&(t.y=t.fy),isNaN(t.x)||isNaN(t.y)){var i=10*Math.sqrt(.5+e),u=e*y;t.x=i*Math.cos(u),t.y=i*Math.sin(u)}(isNaN(t.vx)||isNaN(t.vy))&&(t.vx=t.vy=0)}}function p(t){return t.initialize&&t.initialize(n,d),t}return null==n&&(n=[]),s(),t={tick:x,restart:function(){return h.restart(g),t},stop:function(){return h.stop(),t},nodes:function(e){return arguments.length?(n=e,s(),c.forEach(p),t):n},alpha:function(n){return arguments.length?(i=+n,t):i},alphaMin:function(n){return arguments.length?(u=+n,t):u},alphaDecay:function(n){return arguments.length?(o=+n,t):+o},alphaTarget:function(n){return arguments.length?(f=+n,t):f},velocityDecay:function(n){return arguments.length?(a=1-n,t):1-a},randomSource:function(n){return arguments.length?(d=n,c.forEach(p),t):d},force:function(n,e){return arguments.length>1?(null==e?c.delete(n):c.set(n,p(e)),t):c.get(n)},find:function(t,e,r){var i,u,o,f,a,c=0,l=n.length;for(null==r?r=1/0:r*=r,c=0;c<l;++c)(o=(i=t-(f=n[c]).x)*i+(u=e-f.y)*u)<r&&(a=f,r=o);return a},on:function(n,e){return arguments.length>1?(v.on(n,e),t):v.on(n)}}},n.forceX=function(n){var t,e,r,u=i(.1);function o(n){for(var i,u=0,o=t.length;u<o;++u)(i=t[u]).vx+=(r[u]-i.x)*e[u]*n}function f(){if(t){var i,o=t.length;for(e=new Array(o),r=new Array(o),i=0;i<o;++i)e[i]=isNaN(r[i]=+n(t[i],i,t))?0:+u(t[i],i,t)}}return"function"!=typeof n&&(n=i(null==n?0:+n)),o.initialize=function(n){t=n,f()},o.strength=function(n){return arguments.length?(u="function"==typeof n?n:i(+n),f(),o):u},o.x=function(t){return arguments.length?(n="function"==typeof t?t:i(+t),f(),o):n},o},n.forceY=function(n){var t,e,r,u=i(.1);function o(n){for(var i,u=0,o=t.length;u<o;++u)(i=t[u]).vy+=(r[u]-i.y)*e[u]*n}function f(){if(t){var i,o=t.length;for(e=new Array(o),r=new Array(o),i=0;i<o;++i)e[i]=isNaN(r[i]=+n(t[i],i,t))?0:+u(t[i],i,t)}}return"function"!=typeof n&&(n=i(null==n?0:+n)),o.initialize=function(n){t=n,f()},o.strength=function(n){return arguments.length?(u="function"==typeof n?n:i(+n),f(),o):u},o.y=function(t){return arguments.length?(n="function"==typeof t?t:i(+t),f(),o):n},o},Object.defineProperty(n,"__esModule",{value:!0})}));
'''


HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; worker-src blob:; connect-src 'none'; img-src data:; base-uri 'none'; form-action 'none'">
<title>RDS · 研究超图</title><style>__GRAPH_CSS__</style></head><body>
<main id="app"></main><script id="snapshot" type="application/json">__GRAPH_DATA__</script>
<script id="d3-worker-library" type="text/plain">__D3_BUNDLE__</script>
<script>__GRAPH_JS__</script></body></html>'''

CSS = r'''
*{box-sizing:border-box}html,body,#app{margin:0;width:100%;height:100%;overflow:hidden}body{font:13px/1.5 'Segoe UI','Microsoft YaHei',sans-serif;background:#1e1e1e;color:#d6d6da}button,input,select{font:inherit}button,select{cursor:pointer}button{color:inherit}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid #bbabed;outline-offset:3px}[hidden]{display:none!important}
.hg-shell{position:relative;width:100%;height:100%;--panel:rgba(25,28,36,.95);--border:#333945;--muted:#9099ad;--text:#dce3ef;background:radial-gradient(ellipse at 46% 49%,#161d2e55 0%,#151a2640 30%,transparent 65%),#111319;color:var(--text)}.hg-shell.light{--panel:rgba(250,250,252,.97);--border:#d7d7de;--muted:#72727b;--text:#33333a;background:#f4f4f6}
#hg-canvas{display:block;width:100%;height:100%;touch-action:none;user-select:none;cursor:grab;outline:none}#hg-canvas:active{cursor:grabbing}
.hg-top{position:absolute;left:24px;top:19px;pointer-events:none}.hg-title{font-size:15px;font-weight:600;letter-spacing:.04em;margin:0}.hg-title span{font-size:10px;color:var(--muted);font-weight:400;letter-spacing:.14em;margin-right:13px}.hg-counts{font-size:11px;color:var(--muted);margin-top:5px}.hg-demo{color:#b8a9dc;margin-left:10px;font-size:10px}
.hg-actions{position:absolute;top:18px;right:20px;display:flex;gap:8px;align-items:flex-start}.hg-search-wrap{position:relative}.hg-search{width:220px;background:var(--panel);border:1px solid var(--border);border-radius:7px;color:var(--text);padding:8px 12px}.hg-button{background:var(--panel);border:1px solid var(--border);border-radius:7px;min-height:36px;padding:7px 12px;font-size:12px;white-space:nowrap}.hg-button[aria-pressed=true]{border-color:#a992e0;color:#c4b0f3}.hg-results{position:absolute;right:0;top:43px;width:300px;max-height:360px;overflow:auto;background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:6px;z-index:4;box-shadow:0 12px 40px #0004}.hg-results button{display:block;border:0;background:transparent;color:var(--text);width:100%;text-align:left;padding:9px;border-radius:5px}.hg-results button:hover{background:#8882}.hg-results small{display:block;color:var(--muted);font-size:10px}.hg-result-count{padding:6px 9px;font-size:11px;color:var(--muted)}
.hg-panel{position:absolute;top:67px;right:20px;width:286px;max-height:calc(100% - 150px);overflow:auto;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;z-index:3;box-shadow:0 14px 40px #0003;overflow-wrap:anywhere}.hg-panel header{display:flex;align-items:center;justify-content:space-between;margin-bottom:15px}.hg-panel header strong{font-size:13px}.hg-close{background:transparent;border:0;color:var(--muted);font-size:21px;line-height:1;cursor:pointer;padding:2px 4px}.hg-panel h3{font-size:17px;font-weight:550;margin:3px 0 5px}.hg-id{font:11px Consolas,monospace;color:var(--muted);margin-bottom:15px}.hg-tag{display:inline-block;border:1px solid var(--border);border-radius:4px;padding:3px 7px;font-size:10px}.hg-panel h4{font-size:11px;color:var(--muted);font-weight:500;margin:19px 0 7px}.hg-panel p,.hg-panel li{font-size:12px;line-height:1.7}.hg-panel ul{padding-left:18px}.hg-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;font:11px/1.6 Consolas,monospace;background:#8881;padding:10px;border-radius:6px;max-height:300px;overflow:auto}.hg-panel details{border-top:1px solid var(--border);padding-top:12px;margin-top:13px}.hg-panel summary{cursor:pointer;font-size:12px}.hg-panel label{display:block;color:var(--muted);font-size:11px;margin:12px 0 5px}.hg-panel input[type=range]{width:100%;accent-color:#a78bdf}.hg-panel select{width:100%;padding:6px 8px;color:var(--text);background:var(--panel);border:1px solid var(--border);border-radius:5px}.hg-panel .hg-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:11px 0}.hg-panel .hg-row label{margin:0}.hg-panel .hg-row input{accent-color:#a78bdf}.hg-panel .hg-row select{width:110px}.hg-hint{color:var(--muted);font-size:10px!important}
.hg-controls{position:absolute;bottom:24px;left:24px;display:flex;gap:2px;align-items:center;background:var(--panel);padding:4px;border:1px solid var(--border);border-radius:8px}.hg-controls button{border:0;background:transparent;color:var(--text);width:33px;height:31px;border-radius:4px}.hg-controls button:hover{background:#8882}.hg-controls output{font-size:11px;min-width:47px;text-align:center;color:var(--muted)}.hg-help{position:absolute;bottom:28px;left:240px;color:var(--muted);font-size:10px;pointer-events:none}.hg-help span{margin-left:13px}.hg-minimap{position:absolute;bottom:24px;right:20px;width:156px;height:102px;background:var(--panel);border:1px solid var(--border);border-radius:7px;cursor:crosshair}.hg-tip{position:absolute;pointer-events:none;background:var(--panel);border:1px solid var(--border);color:var(--text);font-size:11px;padding:7px 9px;border-radius:6px;max-width:280px;overflow-wrap:anywhere;z-index:5}.hg-status{position:absolute;bottom:7px;left:25px;color:var(--muted);font:9px/1 Consolas,monospace;pointer-events:none}.hg-empty{position:absolute;inset:0;display:grid;place-content:center;padding:30px;text-align:center}.hg-empty h2{font-size:21px}.hg-empty p{max-width:650px;color:var(--muted)}
.hg-panel input[type=color]{width:45px;height:25px;border:1px solid var(--border);padding:2px;background:transparent;border-radius:4px}.hg-panel .hg-type summary{display:flex;align-items:center;gap:9px}.hg-swatch{width:21px;height:2px;display:inline-block}.hg-help{left:270px}.hg-panel .hg-value{color:var(--text);font-size:10px}
@media(max-width:700px){.hg-top{left:15px;top:14px}.hg-actions{left:15px;right:15px;top:73px;justify-content:flex-end}.hg-search-wrap{flex:1}.hg-search{width:100%}.hg-panel{right:15px;top:123px;width:min(310px,calc(100% - 30px));max-height:calc(100% - 195px)}.hg-results{left:0;width:min(300px,calc(100vw - 30px))}.hg-controls{left:15px;bottom:21px}.hg-minimap{right:15px;bottom:21px;width:116px;height:75px}.hg-help{display:none}.hg-status{left:16px;font-size:8px}.hg-demo{margin-left:5px}}
'''

JS = r'''
'use strict';
const data=JSON.parse(document.getElementById('snapshot').textContent);
const $=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text);if(cls)el.className=cls;return el};
const shell=$('section',undefined,'hg-shell');document.getElementById('app').append(shell);
const statusNames={SUPPORTED:'声明支持',UNKNOWN:'未知',CONTRADICTED:'已反驳声明',PROPOSED:'候选关系'};
const label=r=>typeof r.label==='object'?(r.label?.zh||r.label?.en||r.id):String(r.label||r.id);
const relation=r=>String(r.relation||r.kind||r.type||'依赖关系');
function button(text,title,action,cls='hg-button'){const b=$('button',text,cls);b.type='button';b.title=title;b.setAttribute('aria-label',title);b.addEventListener('click',action);return b}
function raw(parent,value){const d=$('details');d.append($('summary','原始记录'),$('pre',JSON.stringify(value,null,2)));parent.append(d)}
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
 for(const item of items){if(item.kind!=='edge'||!neighbors[item.index].length)continue;for(let axis=0;axis<2;axis++)positions[item.index*2+axis]=neighbors[item.index].reduce((sum,i)=>sum+positions[i*2+axis],0)/neighbors[item.index].length+(item.index%7-3)*.4}
 let selected=null,hovered=null,focus=false,colors=false,light=false,view={x:0,y:0,k:.8},width=1,height=1,ratio=1;
 let edgeMode='relation',framePending=false,worker=null,workerURL=null,layoutRunning=false,userMoved=false,drag=null,query='',matches=new Set(),visibleSet=null,dynamic=true,pausedByVisibility=false;
 let drawCount=0,drawTotal=0,layoutTime=0,layoutTicks=0,bounds={x:-1,y:-1,w:2,h:2},lastMini=0,hoverFrame=false,pointer=null,nearSet=new Set();
 const drawTimes=[],hitGrid=new Map(),targets=positions.slice(),options={center:.08,repulsion:700,spring:.08,length:65,labels:1.2,nodeSize:1,nodeBorder:.3,edgeWidth:1};let interpolating=false,lastDrawTime=0;
 const starPalette=['#c3d8ff','#e1eaff','#f8f1df','#f3d5ab','#f8bd85','#a9c8f4'];let nodeColorMode='stars',customNodeColor='#d7e4ff';
 const pastels=['#a9c8ec','#d4c0e8','#b3d8cd','#e5c4b0','#cad4a4','#d3bfce'];
 const relations=new Map([...new Set(graph.hyperedges.map(relation))].map((type,i)=>[type,{style:['solid','dashed','dotted','dashdot'][i%4],color:pastels[i%pastels.length],width:.65,mode:/竞争|冲突|反驳|contradict|conflict|excludes/i.test(type)?'repel':'attract',strength:type==='主题关联'?.55:1,distance:/竞争|冲突|反驳|contradict|conflict|excludes/i.test(type)?180:65}]));
 const canvas=$('canvas');canvas.id='hg-canvas';canvas.tabIndex=0;canvas.setAttribute('role','img');canvas.setAttribute('aria-label','可交互研究超图。方向键平移，加减号缩放，0 适应全图。可通过搜索选择节点。');shell.append(canvas);const ctx=canvas.getContext('2d');
 const top=$('div',undefined,'hg-top'),title=$('h1',undefined,'hg-title');title.append($('span','RDS'),'研究超图');top.append(title);
 const counts=$('div',`${graph.nodes.length.toLocaleString()} 个节点  ·  ${graph.hyperedges.length.toLocaleString()} 条超边  ·  ${links.length.toLocaleString()} 条连接`,'hg-counts');if(data.demo)counts.append($('span','合成示例','hg-demo'));top.append(counts);shell.append(top);
 const actions=$('div',undefined,'hg-actions'),searchWrap=$('div',undefined,'hg-search-wrap'),search=$('input',undefined,'hg-search'),results=$('div',undefined,'hg-results');search.id='hg-search';search.type='search';search.placeholder='搜索节点或关系…';search.setAttribute('aria-label','搜索节点或关系');results.hidden=true;results.id='hg-search-results';searchWrap.append(search,results);actions.append(searchWrap);
 const settings=$('aside',undefined,'hg-panel'),detail=$('aside',undefined,'hg-panel');settings.id='hg-settings';detail.id='hg-detail';settings.hidden=detail.hidden=true;settings.setAttribute('aria-label','图谱设置');detail.setAttribute('aria-label','节点与关系详情');
 const settingsButton=button('☷','图谱设置',()=>{settings.hidden=!settings.hidden;detail.hidden=true;results.hidden=true});settingsButton.id='hg-settings-toggle';actions.append(settingsButton);shell.append(actions,settings,detail);
 const sh=$('header');sh.append($('strong','图谱设置'),button('×','关闭设置',()=>settings.hidden=true,'hg-close'));settings.append(sh);
 function selectControl(caption,choices,value,action,id){const lab=$('label',caption),select=$('select');select.id=id;lab.htmlFor=id;for(const [key,name]of choices){const opt=$('option',name);opt.value=key;select.append(opt)}select.value=value;select.addEventListener('change',()=>action(select.value));settings.append(lab,select);return select}
 const typeControls=$('div');
 selectControl('连线样式',[['uniform','统一细线'],['status','按声明状态'],['relation','按关系类型']],'relation',value=>{edgeMode=value;requestDraw()},'hg-edge-mode');
 const legend=$('p','实线：声明支持 · 虚线：候选 · 点线：已反驳','hg-hint');settings.append(legend);
 for(const [type,config]of relations){const box=$('details',undefined,'hg-type'),summary=$('summary'),swatch=$('i',undefined,'hg-swatch');swatch.style.background=config.color;summary.append(swatch,type);box.append(summary);
  function row(caption,input){const r=$('div',undefined,'hg-row'),lab=$('label',caption);input.setAttribute('aria-label',type+' · '+caption);r.append(lab,input);box.append(r)}
  function choice(caption,key,values,physics){const select=$('select');for(const [value,name]of values){const opt=$('option',name);opt.value=value;select.append(opt)}select.value=config[key];select.addEventListener('change',()=>{config[key]=select.value;if(physics)runLayout(false);else requestDraw()});row(caption,select)}
  choice('线型','style',[['solid','实线'],['dashed','虚线'],['dotted','点线'],['dashdot','点划线']],false);
  choice('力学作用','mode',[['attract','吸引'],['repel','排斥'],['none','无作用']],true);
  const color=$('input');color.type='color';color.value=config.color;color.addEventListener('input',()=>{config.color=color.value;swatch.style.background=color.value;requestDraw()});row('颜色',color);
  for(const [caption,key,min,max,step,physics]of [['线宽','width',.2,3,.1,false],['力度','strength',0,3,.1,true],['作用距离','distance',20,350,5,true]]){const lab=$('label',caption+' · '+config[key]),input=$('input');input.type='range';input.min=min;input.max=max;input.step=step;input.value=config[key];input.setAttribute('aria-label',type+' · '+caption);input.addEventListener('input',()=>{config[key]=Number(input.value);lab.textContent=caption+' · '+input.value;if(!physics)requestDraw()});input.addEventListener('change',()=>{if(physics)runLayout(false)});box.append(lab,input)}
  typeControls.append(box)
 }settings.append(typeControls);
 function checkbox(caption,action,id){const row=$('div',undefined,'hg-row'),lab=$('label',caption),input=$('input');input.type='checkbox';input.id=id;lab.htmlFor=id;input.addEventListener('change',()=>action(input.checked));row.append(lab,input);settings.append(row)}
 checkbox('按声明状态着色',value=>{colors=value;requestDraw()},'hg-color-status');
 checkbox('浅色背景',value=>{light=value;shell.classList.toggle('light',light);requestDraw()},'hg-light');
 selectControl('节点配色',[['stars','星系：蓝白 / 暖白 / 金橙'],['single','统一自选颜色']],'stars',value=>{nodeColorMode=value;requestDraw()},'hg-node-colors');
 const nodeColor=$('input');nodeColor.type='color';nodeColor.value=customNodeColor;nodeColor.setAttribute('aria-label','自选节点颜色');nodeColor.addEventListener('input',()=>{customNodeColor=nodeColor.value;nodeColorMode='single';document.getElementById('hg-node-colors').value='single';requestDraw()});settings.append(nodeColor);
 const forceBox=$('details'),forceTitle=$('summary','力度与外观');forceBox.append(forceTitle);settings.append(forceBox);
 function slider(caption,key,min,max,step,layout){const lab=$('label'),text=$('span',caption),out=$('output',options[key]);lab.append(text,' · ',out);const input=$('input');input.type='range';input.id='hg-'+key;input.min=min;input.max=max;input.step=step;input.value=options[key];lab.htmlFor=input.id;input.addEventListener('input',()=>{options[key]=Number(input.value);out.textContent=input.value;if(!layout)requestDraw()});input.addEventListener('change',()=>{if(layout)runLayout()});forceBox.append(lab,input)}
 slider('图谱向心力','center',.01,.3,.01,true);slider('节点排斥力','repulsion',100,2200,50,true);slider('连接作用倍率','spring',.01,.2,.01,true);slider('整体距离倍率','length',15,160,5,true);slider('标签显示阈值','labels',.3,3,.1,false);slider('节点大小','nodeSize',.5,2,.1,false);slider('节点轮廓粗细','nodeBorder',0,2,.1,false);slider('全局线宽倍率','edgeWidth',.3,3,.1,false);
 settings.append($('p','圆点大小表示连接数；小菱形是超边汇合点。同一汇合点的前提为 AND，不同汇合点保留 OR 路线。','hg-hint'));
 const layoutButton=button('重新排布','重新运行有界预热并恢复动态布局',()=>runLayout(true));layoutButton.id='hg-relayout';settings.append(layoutButton,$('p','力学与颜色是显示偏好，不改写关系或证据状态。排斥仅在作用距离内生效。','hg-hint'));
 const controls=$('div',undefined,'hg-controls'),zoomText=$('output','100%');
 const zoomOut=button('−','缩小',()=>zoom(.8)),zoomIn=button('+','放大',()=>zoom(1.25)),fitButton=button('⊡','适应全图',()=>{userMoved=false;fit()}),focusButton=button('◎','聚焦选中节点的上游依赖',()=>{focus=!focus;focusButton.setAttribute('aria-pressed',String(focus));updateFocus();requestDraw()});
 const pauseButton=button('Ⅱ','暂停或继续布局',()=>{dynamic=!dynamic;pauseButton.textContent=dynamic?'Ⅱ':'▷';pauseButton.setAttribute('aria-pressed',String(!dynamic));if(dynamic)runLayout(false);else{stopLayout();targets.set(positions);interpolating=false;canvas.dataset.layoutState='paused'}requestDraw()});pauseButton.id='hg-pause';pauseButton.setAttribute('aria-pressed','false');
 zoomIn.id='hg-zoom-in';zoomOut.id='hg-zoom-out';fitButton.id='hg-fit';focusButton.id='hg-focus';focusButton.setAttribute('aria-pressed','false');controls.append(zoomOut,zoomText,zoomIn,fitButton,focusButton,pauseButton);shell.append(controls);
 const help=$('div','拖动整图 · 滚轮缩放 · 点击详查','hg-help');help.append($('span','只读 · 布局自然收敛'));shell.append(help);
 const minimap=$('canvas',undefined,'hg-minimap');minimap.id='hg-minimap';minimap.width=312;minimap.height=204;minimap.setAttribute('aria-label','全图位置小地图');shell.append(minimap);const mini=minimap.getContext('2d');
 const tip=$('div',undefined,'hg-tip');tip.hidden=true;shell.append(tip);const diagnostic=$('div',undefined,'hg-status');diagnostic.id='hg-performance';shell.append(diagnostic);
 function updateBounds(){let x=Infinity,y=Infinity,x2=-Infinity,y2=-Infinity;for(let i=0;i<n;i++){x=Math.min(x,positions[2*i]);y=Math.min(y,positions[2*i+1]);x2=Math.max(x2,positions[2*i]);y2=Math.max(y2,positions[2*i+1])}bounds={x:x-45,y:y-45,w:Math.max(90,x2-x+90),h:Math.max(90,y2-y+90)}}
 function fit(){updateBounds();view.x=bounds.x+bounds.w/2;view.y=bounds.y+bounds.h/2;view.k=Math.max(.025,Math.min(2,(width-90)/bounds.w,(height-145)/bounds.h));requestDraw()}
 function resize(){const box=canvas.getBoundingClientRect();width=Math.max(1,box.width);height=Math.max(1,box.height);ratio=Math.min(devicePixelRatio||1,1.25);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);if(!userMoved)fit();else requestDraw()}
 const observer=new ResizeObserver(resize);observer.observe(canvas);
 function world(x,y){return {x:(x-width/2)/view.k+view.x,y:(y-height/2)/view.k+view.y}}
 function zoom(factor,x=width/2,y=height/2){const anchor=world(x,y);view.k=Math.max(.02,Math.min(8,view.k*factor));view.x=anchor.x-(x-width/2)/view.k;view.y=anchor.y-(y-height/2)/view.k;userMoved=true;requestDraw()}
 function updateFocus(){visibleSet=null;if(!focus||selected===null)return;visibleSet=new Set([selected]);const queue=[selected];for(let k=0;k<queue.length;k++)for(const i of incoming.get(queue[k]))if(!visibleSet.has(i)){visibleSet.add(i);queue.push(i)}}
 function color(item){if(!colors)return item.kind==='edge'?(light?'#97979f':'#77859e'):(light?'#64738a':nodeColorMode==='single'?customNodeColor:starPalette[(item.index*7+item.record.id.length)%starPalette.length]);return {SUPPORTED:'#a6d4be',UNKNOWN:'#d6c5a1',CONTRADICTED:'#dfabb8',PROPOSED:'#beb2de'}[item.record.status]||'#aaa'}
 const dashes={solid:[],dashed:[6,5],dotted:[1,4],dashdot:[7,3,1,3]};
 function style(edge){return edgeMode==='uniform'?'solid':edgeMode==='relation'?relations.get(relation(edge)).style:{SUPPORTED:'solid',PROPOSED:'dashed',CONTRADICTED:'dotted'}[edge.status]||'dashdot'}
 function faded(i){return (visibleSet&&!visibleSet.has(i))||(query&&!matches.has(i)&&i!==selected)}
 function radius(item){return (item.kind==='edge'?1.9:Math.min(19,3+Math.sqrt(item.degree)*1.35))*options.nodeSize}
 function requestDraw(){if(framePending)return;framePending=true;requestAnimationFrame(draw)}
 function draw(){
  framePending=false;const started=performance.now();ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,width,height);
  if(interpolating){const fraction=1-Math.exp(-Math.min(50,started-lastDrawTime||16)/45);let movement=0;for(let i=0;i<graph.nodes.length*2;i++){const delta=targets[i]-positions[i];movement=Math.max(movement,Math.abs(delta));positions[i]+=delta*fraction}interpolating=movement>.02;for(const item of items){if(item.kind!=='edge'||!neighbors[item.index].length)continue;for(let axis=0;axis<2;axis++)positions[item.index*2+axis]=neighbors[item.index].reduce((sum,i)=>sum+positions[i*2+axis],0)/neighbors[item.index].length}if(interpolating)requestDraw()}lastDrawTime=started;
  const sx=x=>(x-view.x)*view.k+width/2,sy=y=>(y-view.y)*view.k+height/2;
  const activeIndex=hovered??selected;nearSet=new Set(activeIndex===null?[]:[activeIndex,...neighbors[activeIndex]]);
  const groups=new Map();for(const l of links){const near=nearSet.has(l.a)&&nearSet.has(l.b),dim=faded(l.a)||faded(l.b),config=relations.get(relation(l.rule)),tone=dim?'dim':near?'near':'normal';const key=style(l.rule)+':'+tone+':'+config.color+':'+config.width;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(l)}
  for(const [key,group]of groups){const [line,tone,edgeColor,edgeWidth]=key.split(':');ctx.strokeStyle=edgeMode==='uniform'?(light?'#969cac':'#9dabc1'):edgeColor;ctx.globalAlpha=tone==='dim'?.045:tone==='near'?.9:light?.4:.22;ctx.lineWidth=Number(edgeWidth)*options.edgeWidth*(tone==='near'?1.6:1);ctx.setLineDash(dashes[line]);ctx.beginPath();for(const l of group){const ax=sx(positions[l.a*2]),ay=sy(positions[l.a*2+1]),bx=sx(positions[l.b*2]),by=sy(positions[l.b*2+1]);if(Math.max(ax,bx)<-5||Math.min(ax,bx)>width+5||Math.max(ay,by)<-5||Math.min(ay,by)>height+5)continue;ctx.moveTo(ax,ay);ctx.lineTo(bx,by)}ctx.stroke()}
  ctx.setLineDash([]);ctx.font='11px "Segoe UI","Microsoft YaHei",sans-serif';ctx.textAlign='center';ctx.textBaseline='top';let drawn=0;const labelCells=new Set(),nodeBatches=new Map(),labels=[];hitGrid.clear();
  for(const item of items){const i=item.index,x=sx(positions[i*2]),y=sy(positions[i*2+1]),r=Math.max(item.kind==='edge'?.7:1.2,radius(item)*Math.sqrt(view.k));if(x<-40||y<-30||x>width+40||y>height+30)continue;drawn++;const active=i===selected||i===hovered,dim=faded(i),c=active?(light?'#8a75af':'#f1e5ff'):color(item),key=c+':'+item.kind+':'+(dim?'dim':'normal');if(!nodeBatches.has(key))nodeBatches.set(key,[]);nodeBatches.get(key).push({x,y,r,active});
   const cell=Math.floor(x/40)+','+Math.floor(y/40);if(!hitGrid.has(cell))hitGrid.set(cell,[]);hitGrid.get(cell).push({x,y,r:Math.max(6,r+3),i});
   if(!dim&&item.kind==='node'&&item.degree>15&&!light){const glow=ctx.createRadialGradient(x,y,0,x,y,r*3.4);glow.addColorStop(0,c+'70');glow.addColorStop(1,c+'00');ctx.globalAlpha=.7;ctx.fillStyle=glow;ctx.fillRect(x-r*3.4,y-r*3.4,r*6.8,r*6.8)}
   if(!dim&&(active||matches.has(i)&&query||item.kind==='node'&&(view.k>=options.labels||item.degree>15&&view.k>.55)))labels.push({item,x,y,r,active});
  }
  for(const [key,batch]of nodeBatches){const [c,kind,tone]=key.split(':');ctx.globalAlpha=tone==='dim'?.10:1;ctx.fillStyle=ctx.strokeStyle=c;ctx.lineWidth=kind==='edge'?.6:options.nodeBorder;ctx.beginPath();for(const {x,y,r}of batch){if(kind==='edge'){ctx.moveTo(x,y-r);ctx.lineTo(x+r,y);ctx.lineTo(x,y+r);ctx.lineTo(x-r,y);ctx.closePath()}else{ctx.moveTo(x+r,y);ctx.arc(x,y,r,0,Math.PI*2)}}if(kind==='node')ctx.fill();if(kind==='edge'||options.nodeBorder>0)ctx.stroke();ctx.beginPath();for(const {x,y,r,active}of batch)if(active){ctx.moveTo(x+r+4,y);ctx.arc(x,y,r+4,0,Math.PI*2)}ctx.lineWidth=1;ctx.stroke()}
  let labelCount=0;for(const {item,x,y,r,active}of labels){if(labelCount>250&&!active)continue;const text=label(item.record),shown=text.length>36?text.slice(0,35)+'…':text,tw=ctx.measureText(shown).width,keys=[];for(let gx=Math.floor((x-tw/2)/45);gx<=Math.floor((x+tw/2)/45);gx++)for(let gy=Math.floor((y+r+5)/15);gy<=Math.floor((y+r+18)/15);gy++)keys.push(gx+','+gy);if(active||!keys.some(key=>labelCells.has(key))){keys.forEach(key=>labelCells.add(key));ctx.fillStyle=light?'#494951':'#d1dae8';ctx.globalAlpha=.95;ctx.fillText(shown,x,y+r+5);labelCount++}}
  ctx.globalAlpha=1;zoomText.value=Math.round(view.k*100)+'%';zoomText.textContent=zoomText.value;
  const now=performance.now();if(now-lastMini>350){drawMini();lastMini=now}const cost=performance.now()-started;drawCount++;drawTotal+=cost;drawTimes.push(cost);if(drawTimes.length>120)drawTimes.shift();canvas.dataset.drawMs=(drawTotal/drawCount).toFixed(2);canvas.dataset.visibleElements=drawn;canvas.dataset.totalElements=n;canvas.dataset.scale=view.k.toFixed(4);canvas.dataset.center=view.x.toFixed(2)+','+view.y.toFixed(2);
  if(drawCount%15===1||!layoutRunning){canvas.dataset.drawP95=[...drawTimes].sort((a,b)=>a-b)[Math.floor((drawTimes.length-1)*.95)].toFixed(2);diagnostic.textContent=(dynamic?(layoutRunning?'自然收敛中':'已稳定'):'已暂停')+` · ${n.toLocaleString()} 元素 · ${links.length.toLocaleString()} 连接`}
 }
 function drawMini(){const w=minimap.width,h=minimap.height;mini.clearRect(0,0,w,h);const k=Math.min((w-16)/bounds.w,(h-16)/bounds.h),ox=(w-bounds.w*k)/2,oy=(h-bounds.h*k)/2;mini.fillStyle=light?'#898990':'#98989f';mini.globalAlpha=.7;for(const item of items){if(item.kind==='edge')continue;mini.fillRect(ox+(positions[item.index*2]-bounds.x)*k,oy+(positions[item.index*2+1]-bounds.y)*k,2,2)}mini.globalAlpha=1;mini.strokeStyle='#aa99cf';mini.lineWidth=1.5;mini.strokeRect(ox+(view.x-width/(2*view.k)-bounds.x)*k,oy+(view.y-height/(2*view.k)-bounds.y)*k,width/view.k*k,height/view.k*k)}
 minimap.addEventListener('pointerdown',e=>{const box=minimap.getBoundingClientRect(),w=minimap.width,h=minimap.height,k=Math.min((w-16)/bounds.w,(h-16)/bounds.h);view.x=bounds.x+((e.clientX-box.left)*w/box.width-(w-bounds.w*k)/2)/k;view.y=bounds.y+((e.clientY-box.top)*h/box.height-(h-bounds.h*k)/2)/k;userMoved=true;requestDraw()});
 function inspect(i,center=false){selected=i;const item=items[i],r=item.record;updateFocus();detail.replaceChildren();const head=$('header');head.append($('strong',item.kind==='edge'?'超边详情 · AND':'节点详情'),button('×','关闭详情',()=>{detail.hidden=true;selected=null;updateFocus();requestDraw()},'hg-close'));detail.append(head,$('h3',label(r)),$('div',r.id,'hg-id'),$('span',statusNames[r.status]||r.status,'hg-tag'));detail.append($('p',`连接数 ${item.degree}`,'hg-hint'));
  function field(title,text){detail.append($('h4',title),$('p',text))}
  function linked(title,indices){detail.append($('h4',title));const ul=$('ul');for(const target of indices.slice(0,150)){const li=$('li'),b=button(label(items[target].record),'查看 '+items[target].record.id,()=>inspect(target,true),'hg-close');b.style.fontSize='12px';b.style.lineHeight='1.6';li.append(b);ul.append(li)}detail.append(ul);if(indices.length>150)detail.append($('p',`显示前 150 项，共 ${indices.length} 项；完整关系见原始记录。`,'hg-hint'))}
  if(r.description)field('说明',r.description);
  if(item.kind==='node'){
   if(data.analysis)field('依赖闭包',data.analysis.declared_supported_closure.includes(r.id)?'已进入声明支持闭包；原始声明状态保持不变。':'尚未进入声明支持闭包。');else field('依赖分析','此大图仅展示原始关系，未运行组合阻断分析。');
   linked('进入该节点的路线 · OR',incoming.get(i));
   const goal=data.analysis?.goals[r.id];if(goal){field('最小待补证据',goal.blocker_sets_complete?goal.minimal_missing_evidence_sets.map(x=>x.join(' + ')||'无缺失声明').join('\n')||'暂无完整集合':'分析达到上限，集合不完整。')}
  }else{field('关系类型',relation(r));linked('共同前提 · AND',r.premises.map(id=>byKey.get('n:'+id).index));linked('结论',[byKey.get('n:'+r.conclusion).index])}
  detail.append($('h4','来源'),$('pre',typeof r.source==='object'?JSON.stringify(r.source,null,2):r.source));detail.append($('p','依赖声明不等于科学确认或数学证明；本页未审计回执绑定。','hg-hint'));raw(detail,r);raw(detail,{source:data.source,snapshot_sha256:data.snapshot_sha256,assurance:data.assurance,analysis_status:data.analysis_status,receipt_audit:data.receipt_audit,input_review:data.input_review});detail.hidden=false;settings.hidden=true;results.hidden=true;
  if(center){view.x=positions[i*2];view.y=positions[i*2+1];view.k=Math.max(view.k,1.3);userMoved=true}requestDraw();
 }
 function searchNodes(){query=search.value.trim().toLocaleLowerCase();matches=new Set();results.replaceChildren();if(!query){results.hidden=true;requestDraw();return}for(const item of items)if((label(item.record)+' '+item.record.id+' '+relation(item.record)).toLocaleLowerCase().includes(query))matches.add(item.index);results.append($('div',`${matches.size} 项匹配${matches.size>40?' · 显示前 40 项':''}`,'hg-result-count'));for(const i of [...matches].slice(0,40)){const item=items[i],b=button(label(item.record),'查看 '+item.record.id,()=>inspect(i,true),'');b.append($('small',(item.kind==='node'?'节点':'超边')+' · '+item.record.id));results.append(b)}results.hidden=false;requestDraw()}
 search.addEventListener('input',searchNodes);search.addEventListener('keydown',e=>{if(e.key==='Escape'){search.value='';searchNodes()}if(e.key==='Enter'&&matches.size)inspect(matches.values().next().value,true)});
 function hit(x,y){let best=null,distance=Infinity;const gx=Math.floor(x/40),gy=Math.floor(y/40);for(let dx=-2;dx<=2;dx++)for(let dy=-2;dy<=2;dy++)for(const p of hitGrid.get((gx+dx)+','+(gy+dy))||[]){const d=(p.x-x)**2+(p.y-y)**2;if(d<p.r*p.r&&d<distance){best=p.i;distance=d}}return best}
 canvas.addEventListener('wheel',e=>{e.preventDefault();const b=canvas.getBoundingClientRect();zoom(Math.exp(-Math.max(-100,Math.min(100,e.deltaY))*.0025),e.clientX-b.left,e.clientY-b.top)},{passive:false});
 canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;const b=canvas.getBoundingClientRect(),x=e.clientX-b.left,y=e.clientY-b.top;drag={id:hit(x,y),x:e.clientX,y:e.clientY,vx:view.x,vy:view.y,moved:false};canvas.setPointerCapture(e.pointerId);results.hidden=true});
 canvas.addEventListener('pointermove',e=>{const b=canvas.getBoundingClientRect(),x=e.clientX-b.left,y=e.clientY-b.top;if(drag){const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>4){drag.moved=true;userMoved=true;view.x=drag.vx-dx/view.k;view.y=drag.vy-dy/view.k;requestDraw()}tip.hidden=true;return}pointer={x,y};if(hoverFrame)return;hoverFrame=true;requestAnimationFrame(()=>{hoverFrame=false;const {x,y}=pointer,next=hit(x,y);if(next!==hovered){hovered=next;requestDraw()}tip.hidden=next===null;if(next!==null){tip.textContent=label(items[next].record)+' · '+(statusNames[items[next].record.status]||'');tip.style.left=Math.max(10,Math.min(width-285,x+14))+'px';tip.style.top=Math.min(height-50,y+14)+'px'}})});
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
  try{workerURL=URL.createObjectURL(new Blob([document.getElementById('d3-worker-library').textContent,';('+forceWorker.toString()+')()'],{type:'text/javascript'}));worker=new Worker(workerURL);worker.onmessage=e=>{targets.set(e.data.positions);interpolating=true;layoutTicks=e.data.ticks;if(e.data.ready){layoutTime=e.data.ms;if(warm){positions.set(e.data.positions);for(const item of items){if(item.kind!=='edge'||!neighbors[item.index].length)continue;for(let axis=0;axis<2;axis++)positions[item.index*2+axis]=neighbors[item.index].reduce((sum,i)=>sum+positions[i*2+axis],0)/neighbors[item.index].length}updateBounds();if(!userMoved)fit()}canvas.dataset.layoutMs=layoutTime.toFixed(2);if(!dynamic)stopLayout()}else if(layoutTicks%30===0)updateBounds();if(e.data.done)stopLayout();canvas.dataset.layoutTicks=layoutTicks;canvas.dataset.physicsStepMs=Number(e.data.stepMs||0).toFixed(2);canvas.dataset.layoutState=e.data.done?'settled':dynamic?'dynamic':'paused';requestDraw()};worker.onerror=()=>{stopLayout();canvas.dataset.layoutState='unavailable';diagnostic.textContent='布局线程不可用；显示初始位置，仍可浏览。'};canvas.dataset.layoutState='running';canvas.dataset.layoutEngine='d3-force 3.0.0';worker.postMessage({positions:positions.slice(0,graph.nodes.length*2),links:Float32Array.from(physical),degree:items.slice(0,graph.nodes.length).map(x=>x.degree),options,dynamic,warm})}
  catch(error){stopLayout();canvas.dataset.layoutState='unavailable';diagnostic.textContent='布局线程不可用；显示初始位置，仍可浏览。';layoutButton.textContent='重新排布'}
 }
 document.addEventListener('visibilitychange',()=>{if(document.hidden){pausedByVisibility=dynamic;stopLayout()}else if(pausedByVisibility&&dynamic){pausedByVisibility=false;runLayout(false)}});
 window.addEventListener('pagehide',()=>{stopLayout();observer.disconnect()},{once:true});
 updateBounds();resize();runLayout(true);
}
'''


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--hypergraph", help="Explicit dependency JSON instead of the saved TMS snapshot")
    parser.add_argument("--demo", action="store_true", help="Synthetic visual preview only")
    parser.add_argument("--large", action="store_true", help="With --demo: 1,200 synthetic claim nodes")
    parser.add_argument("--output", default="rds-hypergraph.html")
    args = parser.parse_args(argv)
    root, output = Path(args.root).resolve(), Path(args.output).resolve()
    try:
        if output.suffix.lower() != ".html" or output.is_relative_to(root / ".rds"):
            raise ValueError("Output must be an .html file outside the .rds ledger")
        if args.demo and args.hypergraph or args.large and not args.demo:
            raise ValueError("Use --large only with --demo, and --demo without --hypergraph")
        if args.hypergraph and output == Path(args.hypergraph).resolve():
            raise ValueError("Output must not overwrite the hypergraph input")
        result = read_graph(root, args.hypergraph, demo=args.demo, large=args.large)
        if args.hypergraph and result["status"] == "UNAVAILABLE":
            raise ValueError(result["reason"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_html(result), encoding="utf-8")
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(output), "status": result["status"], "source": result["source"],
                      "demo": result["demo"], "counts": result.get("counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
