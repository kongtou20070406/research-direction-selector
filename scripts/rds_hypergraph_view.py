#!/usr/bin/env python3
"""Export a standalone, offline, read-only force-directed hypergraph explorer."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

from rds_hypergraph import ASSURANCE, _validate, analyze_hypergraph
from rds_hypergraph_input import load_input, prepare_input
from rds_hypergraph_readable import graph_digest
from rds_bounded_io import read_regular_bytes

MAX_INPUT_BYTES = 8 * 1024 * 1024
VIEW_LIMITS = {"nodes": 4096, "hyperedges": 8192, "incidences": 32768}
ANALYSIS_LIMITS = {"nodes": 200, "hyperedges": 400, "incidences": 2000}


def graph_view(value, source, *, snapshot_sha256=None, demo=False):
    """Render large maps without requiring combinatorial blocker analysis."""
    spec, review = prepare_input(value, str(source))
    if review["errors"]:
        raise ValueError("Hypergraph input: " + json.dumps(review["errors"], ensure_ascii=False))
    validation = deepcopy(spec)
    if "limits" in validation and not isinstance(validation["limits"], dict):
        raise ValueError("Hypergraph limits must be an object")
    validation_limits = validation.setdefault("limits", {})
    validation_limits.setdefault("max_nodes", VIEW_LIMITS["nodes"])
    validation_limits.setdefault("max_hyperedges", 16384)
    _validate(validation)
    counts = {"nodes": len(spec["nodes"]), "hyperedges": len(spec["hyperedges"]),
              "incidences": sum(len(e["premises"]) + 1 for e in spec["hyperedges"])}
    result = {"status": "AVAILABLE", "source": str(source), "snapshot_sha256": snapshot_sha256,
              "demo": demo, "assurance": ASSURANCE, "counts": counts, "limits": VIEW_LIMITS,
              "input_review": review, "receipt_audit": "NOT_RUN", "analysis": None}
    if any(counts[key] > cap for key, cap in VIEW_LIMITS.items()):
        return {**result, "status": "DISPLAY_LIMIT", "graph": None, "analysis_status": "NOT_RUN"}
    result["graph"] = spec
    result["graph_sha256"] = graph_digest(spec)
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
            raw = read_regular_bytes(path, MAX_INPUT_BYTES, label='Hypergraph input',
                                     limit_message='Hypergraph input exceeds 8 MiB')
            value, repairs = load_input(raw.decode("utf-8-sig"))
            result = graph_view(value, path, demo=demo)
            result["input_review"]["repairs"][:0] = [{"path": "$", "reason": repair} for repair in repairs]
            return result
        from rds_tms_store import current
        saved = current(root)
        if saved is None:
            return {"status": "MISSING", "graph": None, "source": None, "demo": False}
        result = graph_view(saved["dependency_map"], Path(root).resolve() / ".rds" / "project.sqlite3",
                            snapshot_sha256=saved["sha256"])
        if saved.get("source_base_dir") is not None:
            result["record_source_base_dir"] = saved["source_base_dir"]
        return result
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
        return {"status": "UNAVAILABLE", "graph": None, "source": str(graph_path or root), "reason": str(exc), "demo": demo}


def analysis_notice(result):
    return {"NOT_RUN_LARGE_GRAPH": "阻断条件分析未运行：图规模超过分析上限。图仍完整显示。",
            "INCOMPLETE": "阻断条件分析不完整：已达到计算上限。",
            "COMPLETE": "阻断条件分析已完成。",
            "NOT_RUN": "阻断条件分析未运行。"}.get(result.get("analysis_status"), "阻断条件分析状态未知。")


def render_html(result):
    payload = json.dumps({**result, "display": display_records(result), "analysis_notice": analysis_notice(result)}, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c")
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
.hg-tip{white-space:pre-line}
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
'''


# The user-selected replica has no published license at this pinned revision.
# Read its local checkout at export time; do not redistribute it in this repository.
REPLICA_COMMIT = "b811f4d12f909d496d44c7f2d98b3a408eb1a627"
REPLICA_FILES = {
    "renderer.js": "e3b0aed506ab9ed6717ef1ef2fb177eb08c52be574bdbba5cff612dd62c75ca7",
    "sim-worker.js": "cd618311568bd64bef2b52ea0c302d17c25eeb4bc6c8041f5d312c284bc07316",
    "style.css": "3f3c8ce502225c01b664d3842643301bd926a8e81252152fecd7c438f29eb59d",
}
PIXI_SHA256 = "712a3e1943b062a907d86c5bb56b67c7b32a3f03b3654768b00b7016d30a24c6"


def dependency_levels(ids, links):
    """Longest-path ranks on the SCC condensation; cycles share a rank.

    Iterative traversal keeps the full display limit independent of Python's
    recursion limit. Disconnected records with no directed relation are unranked.
    """
    adj, rev = {i: [] for i in ids}, {i: [] for i in ids}
    for s, t, _ in links:
        adj[s].append(t)
        rev[t].append(s)
    seen, order = set(), []
    for first in ids:
        if first in seen:
            continue
        seen.add(first)
        stack = [(first, iter(adj[first]))]
        while stack:
            n, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                order.append(n)
                stack.pop()
            elif nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, iter(adj[nxt])))
    component, cid = {}, -1
    for first in reversed(order):
        if first in component:
            continue
        cid += 1
        component[first] = cid
        stack = [first]
        while stack:
            for nxt in rev[stack.pop()]:
                if nxt not in component:
                    component[nxt] = cid
                    stack.append(nxt)
    out, indegree = {c: set() for c in component.values()}, {c: 0 for c in component.values()}
    for s, t, _ in links:
        a, b = component[s], component[t]
        if a != b and b not in out[a]:
            out[a].add(b)
            indegree[b] += 1
    rank = {c: 0 for c in out}
    queue = [c for c in out if not indegree[c]]
    for c in queue:
        for nxt in out[c]:
            rank[nxt] = max(rank[nxt], rank[c] + 1)
            indegree[nxt] -= 1
            if not indegree[nxt]:
                queue.append(nxt)
    center = max(rank.values(), default=0) / 2
    return {i: (rank[component[i]] - center) * 160 if adj[i] or rev[i] else None for i in ids}


def _adapt_record_view(spec):
    """Resolve reported identities without running inference or auditing research.

    Older owned snapshots predate record_kind. Adapt only reserved run/receipt
    identities and structured byte references; never recover IDs from prose.
    The adapter operates on a copy so the exported snapshot stays unchanged.
    """
    adapted = deepcopy(spec)
    # Record matching is linear in the displayed records, independent of the
    # smaller default limits intended for combinatorial dependency analysis.
    limits = adapted.setdefault("limits", {})
    limits.setdefault("max_nodes", VIEW_LIMITS["nodes"])
    limits.setdefault("max_hyperedges", VIEW_LIMITS["hyperedges"])
    legacy = set()
    for row in adapted["nodes"]:
        if "record_kind" in row:
            continue
        ident, src = row["id"], row.get("source")
        if ident.startswith("owned:run:") and isinstance(row.get("manifest_sha256"), str):
            row["record_kind"] = "run"
            row.setdefault("run_id", ident.removeprefix("owned:run:"))
        elif ident.startswith("owned:receipt:") and isinstance(row.get("receipt_sha256"), str):
            row["record_kind"] = "receipt"
            row.setdefault("run_id", ident.removeprefix("owned:receipt:"))
        elif row.get("interpretation") and isinstance(row.get("run_id"), str):
            if isinstance(row["interpretation"], str) and row["interpretation"] in {"MISSING", "PENDING"}:
                row["record_kind"] = "declared_output"
            elif isinstance(src, dict) and src.get("sha256") and (src.get("file") or src.get("path")):
                row["record_kind"] = "artifact"
        elif isinstance(src, dict) and src.get("receipt_id") and src.get("sha256") and (src.get("path") or src.get("file")):
            row["record_kind"] = "observation"
        if "record_kind" in row:
            legacy.add(ident)
    # Legacy observations carry an exact receipt identity rather than run_id;
    # a unique receipt supplies the run. Old artifact inventories lack receipt_id.
    receipts, by_run = {}, {}
    for row in adapted["nodes"]:
        if row.get("record_kind") == "receipt":
            src = row.get("source") if isinstance(row.get("source"), dict) else {}
            aliases = [v for v in (row.get("receipt_id"), row.get("receipt_sha256"), src.get("receipt_id")) if v is not None]
            if aliases and all(isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{64}", v) for v in aliases) and len({v.lower() for v in aliases}) == 1:
                key = aliases[0].lower()
                receipts.setdefault(key.lower(), []).append(row)
                if isinstance(row.get("run_id"), str):
                    by_run.setdefault(row["run_id"], []).append(key)
    for row in adapted["nodes"]:
        if row["id"] not in legacy:
            continue
        src = row.get("source") if isinstance(row.get("source"), dict) else {}
        key = src.get("receipt_id")
        matches = receipts.get(key.lower(), []) if isinstance(key, str) else []
        if row["record_kind"] == "observation" and "run_id" not in row and len(matches) == 1:
            row["run_id"] = matches[0].get("run_id")
        if row["record_kind"] == "artifact" and not any(row.get(k) for k in ("receipt_id", "receipt_sha256")) and not key:
            candidates = by_run.get(row.get("run_id"), [])
            if len(candidates) == 1:
                row["receipt_id"] = candidates[0]
    return adapted


def _record_view_report(spec, *, adapted=None, source_base=None):
    adapted = _adapt_record_view(spec) if adapted is None else adapted
    if source_base is not None and "record_source_base_dir" not in adapted:
        adapted = deepcopy(adapted)
        adapted["record_source_base_dir"] = source_base
    import rds_hypergraph
    if callable(getattr(rds_hypergraph, "record_topology", None)):
        return rds_hypergraph.record_topology(adapted)
    return _legacy_record_report(adapted)


def _legacy_record_report(spec):
    """Conservative typed resolver for installations without record_topology."""
    rows, issues, indexes, relations = {}, [], {k: {} for k in ("run", "receipt", "artifact")}, []
    kinds = {"contract", "run", "receipt", "artifact", "declared_output", "lifecycle_fact", "observation"}
    required = {"run": ("run_id",), "receipt": ("run_id", "receipt_id"),
                "artifact": ("run_id", "path", "sha256"), "declared_output": ("run_id", "path"),
                "lifecycle_fact": ("run_id",), "observation": ("run_id",)}
    source_base = spec.get("record_source_base_dir")
    if source_base is not None:
        try:
            if not (isinstance(source_base, (str, Path)) and str(source_base) and "\0" not in str(source_base)
                    and Path(source_base).is_absolute()):
                raise ValueError("Invalid reported source base")
            source_base = Path(source_base).resolve()
        except (OSError, ValueError, RuntimeError):
            source_base = None  # Match original strings, without inferring cwd.
            issues.append({"node_id": None, "scope": "dependency_map", "field": "record_source_base_dir",
                           "reason": "INVALID_BINDING", "candidates": [], "omitted_candidates": 0})

    def issue(row, field, reason, candidates=()):
        issues.append({"node_id": row["id"], "field": field, "reason": reason,
                       "candidates": sorted(candidates)[:3], "omitted_candidates": max(0, len(candidates) - 3)})

    for node in spec["nodes"]:
        if not isinstance(node.get("record_kind"), str) or node["record_kind"] not in kinds:
            if node.get("record_kind") is not None:
                issue(node, "record_kind", "INVALID_RECORD_KIND")
            continue
        src = node.get("source") if isinstance(node.get("source"), dict) else {}
        row = {"node": node, "kind": node["record_kind"], "invalid_fields": set()}
        for field, aliases in (("run_id", [node.get("run_id")]),
                ("receipt_id", [node.get("receipt_id"), node.get("receipt_sha256"), src.get("receipt_id")]),
                ("path", [node.get("artifact_path"), node.get("output_path"), src.get("file"), src.get("path")]),
                ("sha256", [src.get("sha256")])):
            values = [v for v in aliases if v is not None]
            digest = field in {"receipt_id", "sha256"}
            if any(not isinstance(v, str) or not v or len(v) > (64 if digest else 2048)
                   or field == "path" and "\0" in v
                   or digest and re.fullmatch(r"[0-9a-fA-F]{64}", v) is None for v in values):
                issue(node, field, "INVALID_BINDING")
                row["invalid_fields"].add(field)
                continue
            values = [v.lower() if digest else v for v in values]
            if field == "path" and source_base is not None:
                try:
                    values = [str((source_base / v).resolve()) for v in values]
                except (OSError, ValueError, RuntimeError):
                    issue(node, field, "INVALID_BINDING")
                    row["invalid_fields"].add(field)
                    continue
            if len(set(values)) > 1:
                issue(node, field, "CONFLICTING_BINDINGS")
                row["invalid_fields"].add(field)
            elif values:
                row[field] = values[0]
        for field in required.get(row["kind"], ()):
            if field not in row and field not in row["invalid_fields"]:
                issue(node, field, "RUN_NOT_REGISTERED" if field == "run_id" and node.get("route_id")
                      and row["kind"] in {"lifecycle_fact", "observation"} else "MISSING_BINDING")
        rows[node["id"]] = row
        kind = row["kind"]
        key = row.get("run_id") if kind == "run" else row.get("receipt_id") if kind == "receipt" else (
            row.get("run_id"), row.get("path"), row.get("sha256")) if kind == "artifact" else None
        if key is not None and not (isinstance(key, tuple) and None in key):
            indexes[kind].setdefault(key, []).append(node["id"])

    def match(row, kind, key, field):
        if key is None or isinstance(key, tuple) and None in key:
            return None
        candidates = indexes[kind].get(key, [])
        if len(candidates) != 1:
            issue(row["node"], field, "AMBIGUOUS_BINDING" if candidates else "UNMATCHED_BINDING", candidates)
            return None
        return candidates[0]

    def link(kind, origin, target):
        if origin is not None:
            relations.append({"kind": kind, "from": origin, "to": target["node"]["id"],
                              "binding": "DECLARED_OUTPUT" if kind == "declared_output" else "REPORTED_EXACT_ID_MATCH",
                              "scientific_support": "UNKNOWN"})

    for row in rows.values():
        kind = row["kind"]
        if kind == "run":
            match(row, "run", row.get("run_id"), "run_id")
            continue  # Check origins without consumers; never add a self link.
        if kind == "contract":
            continue
        run = match(row, "run", row.get("run_id"), "run_id")
        receipt = match(row, "receipt", row.get("receipt_id"), "receipt_id") if kind != "receipt" else None
        receipt_conflict = False
        if receipt is not None:
            receipt_run = rows[receipt].get("run_id")
            if receipt_run is None:
                receipt_conflict = True  # Origin ownership is missing or invalid.
            elif row.get("run_id") is not None and receipt_run != row["run_id"]:
                issue(row["node"], "receipt.run_id", "CONFLICTING_BINDINGS", [receipt])
                receipt_conflict = True
        if kind == "receipt":
            # Preserve origin ambiguity diagnostics independently of membership.
            match(row, "receipt", row.get("receipt_id"), "receipt_id")
            link("run_receipt", run, row)
        elif kind == "artifact":
            match(row, "artifact", (row.get("run_id"), row.get("path"), row.get("sha256")), "artifact_identity")
            link("run_artifact", run, row)
            if row.get("run_id") is not None and not receipt_conflict:
                link("receipt_artifact", receipt, row)
        elif kind == "declared_output":
            if row.get("path"):
                link("declared_output", run, row)
        elif kind == "lifecycle_fact":
            link("run_lifecycle_fact", run, row)
        elif kind == "observation":
            link("run_observation", run, row)
            if receipt_conflict or "receipt_id" in row["invalid_fields"]:
                continue  # The independently exact run link remains available.
            artifact = match(row, "artifact", (row.get("run_id"), row.get("path"), row.get("sha256")), "artifact_identity")
            if artifact is not None and "receipt_id" in rows[artifact]["invalid_fields"]:
                continue  # Invalid receipt metadata cannot match absent metadata.
            if artifact is not None and rows[artifact].get("receipt_id") != row.get("receipt_id"):
                issue(row["node"], "artifact.receipt_id", "CONFLICTING_BINDINGS", [artifact])
            else:
                link("artifact_observation", artifact, row)
    return {"record_relations": sorted(relations, key=lambda r: (r["kind"], r["from"], r["to"])),
            "record_topology": {"assurance": "REPORTED_RECORD_IDENTITIES_NOT_SCIENTIFIC_SUPPORT",
                                "issues": sorted(issues, key=lambda i: (i["node_id"] or "", i["field"], i["reason"])),
                                "resolver": "CONSERVATIVE_LEGACY_FALLBACK"}}


def _locale_label(row, default):
    label = row.get("label")
    if isinstance(label, dict):
        label = next((label[k] for k in ("zh", "en") if isinstance(label.get(k), str) and label[k]), None)
    return label if isinstance(label, str) and label else default


DISPLAY_STYLES = {"声明": ("#c2c8d2", "circle"), "执行": ("#b39ddb", "square"),
                  "回执": ("#82c4af", "ring"), "产物": ("#82b6d4", "hexagon"),
                  "观测": ("#d5c17e", "circle"), "超边": ("#9c95af", "diamond")}
LIFECYCLE_ID = re.compile(r"^(?:owned:fact:)?run\.(.+)\.(succeeded|failed|timed_out|status|completed|running|registered|not_registered)$")
HASH_NAME_TOKEN = re.compile(r"(?<![a-zA-Z0-9])[0-9a-fA-F]{32,}(?![a-zA-Z0-9])")


def readable_text(text, *, machine=False):
    # Decimal quantities in human labels and filenames are not hash identities.
    return HASH_NAME_TOKEN.sub(lambda m: "" if machine or re.search(r"[a-fA-F]", m[0]) else m[0], text)


def display_record(row, *, edge=False):
    """Private presentation only: a short name and the recorded status basis.

    An execution predicate being false does not establish its opposite. Neither
    a receipt outcome nor a declaration status is an independently audited proof.
    """
    ident = row["id"]
    typed = row.get("record_kind")
    fact = row.get("owned_fact")
    src = row.get("source")
    lifecycle = LIFECYCLE_ID.fullmatch(ident) if isinstance(fact, dict) or typed == "lifecycle_fact" or ident.startswith("owned:fact:") else None
    kind = {"run": "执行", "receipt": "回执", "artifact": "产物", "declared_output": "产物",
            "fact": "观测", "observation": "观测", "lifecycle_fact": "观测"}.get(typed if isinstance(typed, str) else "")
    missing_output = ident.startswith("owned:output:") and row.get("interpretation") == "MISSING"
    prefixes = (("owned:run:", "执行"), ("owned:receipt:", "回执"), ("owned:artifact:", "产物"),
                ("owned:output:", "产物"),
                ("owned:fact:", "观测"), ("observation.", "观测"), ("run.", "观测"),
                ("receipt.", "回执"))
    observation_identity = typed == "observation" or isinstance(fact, dict) or (isinstance(src, dict) and
                            isinstance(src.get("sha256"), str) and isinstance(src.get("receipt_id"), str))
    receipt_identity = typed == "receipt" or any(isinstance(row.get(k), str) and row[k]
                                                for k in ("receipt_id", "receipt_sha256", "outcome", "run_status"))
    prefix = next(((p, k) for p, k in prefixes if ident.startswith(p) and
                   (p != "run." or lifecycle) and (p != "observation." or observation_identity) and
                   (p != "receipt." or receipt_identity) and (p != "owned:output:" or missing_output)), None)
    kind = "超边" if edge else kind or (prefix[1] if prefix else "声明")
    machine_identity = bool(prefix or lifecycle or ident.startswith("owned:"))
    short = ident
    if lifecycle:
        short = lifecycle[1]
    elif prefix:
        short = ident[len(prefix[0]):]
    if prefix or lifecycle or kind in {"执行", "回执"}:
        short = re.sub(r"[._:/\\-]+", " ", short).strip() or ident
    path_name = False
    if kind == "产物":
        src = row.get("source")
        path = (src.get("file") or src.get("path")) if isinstance(src, dict) else None
        if missing_output and isinstance(src, dict) and isinstance(row.get("run_id"), str):
            locator = src.get("locator")
            marker = "missing declared output " + row["run_id"] + ":"
            if isinstance(locator, str) and locator.startswith(marker):
                path = locator[len(marker):]
        if isinstance(path, str) and path:
            short = path.replace("\\", "/").rsplit("/", 1)[-1] or ident
            path_name = True
    short = readable_text(short, machine=machine_identity and not path_name)
    if path_name and short.startswith("."):
        short = kind + short
    short = short.strip(" ._:/\\-")
    if not short or short.startswith("owned:"):
        run_name = row.get("run_id")
        short = re.sub(r"[._:/\\-]+", " ", run_name).strip() + " · " + kind if isinstance(run_name, str) and run_name and not re.fullmatch(r"[0-9a-fA-F]{32,}", run_name) else kind
    claim = row.get("claim")
    label = _locale_label(row, claim if isinstance(claim, str) and claim else short)
    if label == ident and machine_identity:
        label = short
    label = readable_text(label).strip() or kind
    outline = {"color": "#9299a6", "state": "neutral", "basis": "中性：UNKNOWN / 待验证；不表示科研证明"}
    execution = kind in {"执行", "回执"} or lifecycle is not None
    if execution:
        states, notes = [], []
        outcomes = {"SUCCEEDED": "success", "FAILED": "failure", "TIMED_OUT": "failure",
                    "TIMEOUT": "failure", "CANCELLED": "failure", "CANCELED": "failure"}
        for key in ("outcome", "run_status", "lifecycle_status"):
            if key in row:
                value = row[key]
                notes.append(key + "=" + json.dumps(value, ensure_ascii=False))
                states.append(outcomes.get(value) if isinstance(value, str) else None)
        if lifecycle:
            fact = row.get("owned_fact")
            fact = fact if isinstance(fact, dict) else {}
            value = fact.get("value")
            notes.append(lifecycle[2] + "=" + json.dumps(value, ensure_ascii=False))
            fact_id = fact.get("id")
            valid_id = fact_id is None or fact_id == ident.removeprefix("owned:fact:")
            states.append(("success" if lifecycle[2] == "succeeded" else "failure")
                          if value is True and fact.get("reliable") is True and valid_id and
                          lifecycle[2] in {"succeeded", "failed", "timed_out"} else None)
        state = states[0] if states and all(s == states[0] for s in states) else None
        outline["basis"] = "执行记录：" + ("；".join(notes) or "未记录明确结果")
        if row.get("status") != "SUPPORTED":
            state = None
            outline["basis"] += "；原始记录状态=" + str(row.get("status", "UNKNOWN")) + "（待验证或冲突）"
        if state:
            outline.update(state=state, color="#39b872" if state == "success" else "#e65b63")
        else:
            outline["basis"] += "；中性（否定 / 待定 / 无效或冲突，不能推断相反结果）"
        outline["basis"] += "；未审计，不表示科研证明"
    elif kind in {"声明", "超边"}:
        state = {"SUPPORTED": "success", "CONTRADICTED": "failure"}.get(row.get("status"))
        outline["basis"] = "声明状态：" + str(row.get("status", "UNKNOWN")) + "；未独立验收，不表示科研证明"
        if state:
            outline.update(state=state, color="#39b872" if state == "success" else "#e65b63")
    color, shape = DISPLAY_STYLES[kind]
    if missing_output:
        status_text = "输出缺失"
    elif kind in {"声明", "超边"}:
        status_text = {"SUPPORTED": "声明支持", "CONTRADICTED": "声明反驳", "PROPOSED": "候选", "UNKNOWN": "待确认"}.get(row.get("status"), "待确认")
    elif execution:
        status_text = {"success": "执行成功", "failure": "执行失败"}.get(outline["state"], "未判定")
    else:
        status_text = "已记录" if row.get("status") == "SUPPORTED" else "待确认"
    return {"label": label, "kind": kind, "color": color, "shape": shape, "outline": outline, "status_text": status_text}


def display_records(result, *, adapted=None, report=None):
    spec = result.get("graph") or {}
    adapted = (_adapt_record_view(spec) if spec else spec) if adapted is None else adapted
    report = (_record_view_report(spec, adapted=adapted, source_base=result.get("record_source_base_dir")) if spec else {"record_topology": {"issues": []}}) if report is None else report
    issues = report["record_topology"]["issues"]
    effective = {row["id"]: row for row in adapted.get("nodes", [])}
    by_node = {}
    for issue in issues:
        by_node.setdefault(issue.get("node_id"), []).append(issue)
    readable = result.get("readable")
    if readable is not None:
        from rds_hypergraph_readable import validate_readable
        readable = validate_readable(readable, result)
    presentation = {}
    for key in ("nodes", "hyperedges"):
        presentation[key] = {}
        for row in spec.get(key, []):
            info = display_record(effective.get(row["id"], row) if key == "nodes" else row, edge=key == "hyperedges")
            entry = (readable or {}).get(key, {}).get(row["id"], {})
            if entry.get("title", "").strip():
                info["label"] = entry["title"].strip()
            info["summary"] = entry.get("summary", "").strip()
            scoped = by_node.get(row["id"], []) if key == "nodes" else []
            info["diagnostics"] = readable_diagnostics(scoped)
            presentation[key][row["id"]] = info
    presentation["diagnostics"] = readable_diagnostics(issues, graph=True)
    return presentation


def readable_diagnostics(issues, *, graph=False):
    """Bounded human explanations; raw resolver evidence stays in the payload."""
    reasons = {"MISSING_BINDING": "未记录", "UNMATCHED_BINDING": "未找到匹配",
               "AMBIGUOUS_BINDING": "有多个匹配", "CONFLICTING_BINDINGS": "记录冲突",
               "INVALID_BINDING": "记录无效", "RUN_NOT_REGISTERED": "执行尚未登记"}
    messages = []
    for issue in issues:
        field = issue.get("field", "")
        subject = "执行来源" if "run" in field else "回执来源" if "receipt" in field else "产物来源" if "artifact" in field else "来源"
        message = subject + "：" + reasons.get(issue.get("reason"), "待核对") + "。"
        if not any(item["message"] == message for item in messages) or not graph:
            candidates = issue.get("candidates", [])
            messages.append({"message": message, "candidates": candidates[:8],
                             "omitted_candidates": issue.get("omitted_candidates", 0) + max(0, len(candidates) - 8)})
    return {"items": messages[:8], "omitted": max(0, len(messages) - 8), "count": len(issues)}


def write_html_atomic(output, page):
    """Replace the output directory entry without writing through input aliases."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".rds-hypergraph-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(page)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def replica_view(result, *, presentation=None, report=None):
    """Incidence geometry plus explicitly bound provenance; never alter the map.

    Provenance is a separate view relation, not an inference or scientific edge.
    Do not join records by similar text, orphan IDs, or status.
    """
    spec = result.get("graph")
    if not spec:
        return {"nodes": {}, "links": [], "provenance_count": 0, "relations": []}
    nodes, links, groups = {}, [], {}
    adapted = _adapt_record_view(spec) if presentation is None or report is None else None
    report = _record_view_report(spec, adapted=adapted, source_base=result.get("record_source_base_dir")) if report is None else report
    presentation = display_records(result, adapted=adapted, report=report) if presentation is None else presentation
    ids = {n["id"]: f"c{i}" for i, n in enumerate(spec["nodes"])}
    goals = set(spec["goals"])
    palette = {"声明": "#c2c8d2", "执行": "#b39ddb", "回执": "#82c4af", "产物": "#82b6d4", "观测": "#d5c17e"}
    run_ids = {n["id"]: n.get("run_id", n["id"].removeprefix("owned:run:")) for n in spec["nodes"]
               if n.get("record_kind") == "run" and isinstance(n.get("run_id"), str) and n["run_id"]
               or n["id"].startswith("owned:run:") and isinstance(n.get("manifest_sha256"), str)}
    for i, row in enumerate(spec["nodes"]):
        ident = row["id"]
        display = presentation["nodes"][ident]
        kind, label = display["kind"], display["label"]
        nodes[f"c{i}"] = {"type": "", "label": label, "color": {"rgb": int(palette[kind][1:], 16), "a": 1},
                            "rds": {"record": ident, "kind": kind, "size": 1.5 if ident in goals else 1,
                                    "group": None, "color": palette[kind], "outline": display["outline"], "status_text": display["status_text"]}}
    for i, edge in enumerate(spec["hyperedges"]):
        hub = f"h{i}"
        relation = edge.get("relation") if isinstance(edge.get("relation"), str) and edge["relation"].strip() else "依赖"
        members = [ids[p] for p in edge["premises"]] + [ids[edge["conclusion"]]]
        edge_display = presentation["hyperedges"][edge["id"]]
        edge_title = (result.get("readable") or {}).get("hyperedges", {}).get(edge["id"], {}).get("title", "").strip()
        nodes[hub] = {"type": "hyperedge", "label": edge_title or ("AND" if len(edge["premises"]) > 1 else "") + " ◇",
                      "color": {"rgb": 0x9c95af, "a": 1},
                      "rds": {"edge": i, "kind": "超边", "members": members, "size": .65, "group": None,
                              "outline": edge_display["outline"], "status_text": edge_display["status_text"]}}
        style = {"relation": relation, "family": "dependency", "hyperedge": hub, "color": "#bdc2d2",
                 "width": 1.25, "opacity": .55, "dash": edge["status"] != "SUPPORTED", "arrow": False}
        links.extend([[ids[p], hub, dict(style)] for p in edge["premises"]])
        links.append([hub, ids[edge["conclusion"]], {**style, "arrow": True}])
    pairs = {(s, t) for s, t, _ in links}
    provenance, declarations = 0, 0

    def bind(s, t, relation):
        nonlocal provenance, declarations
        if s == t or (s, t) in pairs:
            return
        pairs.add((s, t))
        declaration = relation["kind"] == "declared_output"
        provenance += not declaration
        declarations += declaration
        links.append([s, t, {"relation": "声明输出" if declaration else "来源绑定", "family": "declaration" if declaration else "provenance", "color": "#b7c6c3",
                            "width": .65, "opacity": .32, "dash": True, "arrow": False,
                            "binding": dict(relation)}])

    completions = {(e["premises"][0], e["conclusion"]): f"h{i}" for i, e in enumerate(spec["hyperedges"]) if len(e["premises"]) == 1}
    for relation in report["record_relations"]:
        origin, target = relation["from"], relation["to"]
        bind(ids[origin], ids[target], relation)
        if origin in run_ids:
            if relation["kind"] != "run_receipt" or (origin, target) in completions:
                groups[ids[target]] = run_ids[origin]
            if relation["kind"] == "run_receipt" and (origin, target) in completions:
                groups[completions[origin, target]] = run_ids[origin]
    for ident, run in run_ids.items():
        groups[ids[ident]] = run
    for uid, group in groups.items():
        nodes[uid]["rds"]["group"] = group
    levels = dependency_levels(nodes, links)
    for uid, level in levels.items():
        nodes[uid]["rds"]["flowX"] = level
    # Project containment is a display layer, never a new logical assertion.
    # Join one representative per existing component, rather than a full clique.
    parent = {uid: uid for uid in nodes}
    degree = {uid: 0 for uid in nodes}

    def find(uid):
        while parent[uid] != uid:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    for s, t, _ in links:
        parent[find(t)] = find(s)
        degree[s] += 1
        degree[t] += 1
    components = {}
    for uid in nodes:
        components.setdefault(find(uid), []).append(uid)
    scope_links = []
    for members in components.values():
        representative = max(members, key=lambda uid: (
            nodes[uid]["rds"].get("record") in goals,
            nodes[uid]["rds"]["kind"] == "执行", degree[uid]))
        scope_links.append(["scope", representative, {
            "relation": "项目归属", "family": "membership", "color": "#8196b0",
            "width": .6, "opacity": .23, "dash": True, "arrow": False,
            "binding": {"field": "displayed_snapshot", "source": result.get("source"),
                        "sha256": result.get("snapshot_sha256")}}])
    scope = {"node": {"type": "", "label": "项目快照", "color": {"rgb": 0x8aa1b8, "a": 1},
                      "rds": {"virtual": True, "kind": "项目", "size": 1, "flowX": None,
                              "group": None, "color": "#8aa1b8"}},
             "links": scope_links, "components": len(components),
             "unlinked_records": sum(degree[uid] == 0 for uid in ids.values())}
    return {"nodes": nodes, "links": links, "provenance_count": provenance, "declaration_count": declarations,
            "record_topology": report["record_topology"], "record_relations": report["record_relations"],
            "scope": scope, "relations": sorted({meta["relation"] for _, _, meta in links} | {"项目归属"})}


def _replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError("Pinned replica patch no longer matches: " + old[:70])
    return source.replace(old, new, 1)


def patch_replica_renderer(source):
    """Small, auditable patches to the exact user-selected renderer."""
    source = _replace_once(source, "new Blob([`(${workerMain.toString()})()`]", "new Blob([document.getElementById('d3-worker-lib').textContent, `\\n(${workerMain.toString()})()`]")
    source = _replace_once(source, "this.worker = new Worker(blobUrl", "this.workerBlobUrl = blobUrl; this.worker = new Worker(blobUrl")
    source = _replace_once(source, "getSize() { return this.r.opts.nodeSize * Math.max(8, Math.min(3 * Math.sqrt(this.weight + 1), 30)); }", """getSize() { return (this.rds?.size || 1) * this.r.opts.nodeSize * (this.rds?.radius ?? Math.max(8, Math.min(3*Math.sqrt(this.weight+1),30))); }
    drawBody(c) {
      const shape=this.type==='hyperedge'?'diamond':this.rds?.shape || 'circle';
      this.rdsShape=shape;c.clear();
      if(shape==='ring'){c.lineStyle(22,0xffffff,1).drawCircle(0,0,88);return;}
      c.beginFill(0xffffff);
      if(shape==='diamond')c.drawPolygon([0,-100,100,0,0,100,-100,0]);
      else if(shape==='square')c.drawRect(-82,-82,164,164);
      else if(shape==='triangle')c.drawPolygon([0,-100,95,75,-95,75]);
      else if(shape==='hexagon')c.drawPolygon([100,0,50,87,-50,87,-100,0,-50,-87,50,-87]);
      else c.drawCircle(0,0,100);
      c.endFill();
    }
    renderStatusOutline(x,y,size,ns,alpha,visible) {
      const state=this.rds?.outline;
      if(!state){if(this.statusOutline)this.statusOutline.visible=false;return;}
      let c=this.statusOutline;
      if(!c){c=this.statusOutline=new PIXI.Graphics();this.statusOutlineShape=null;c.eventMode='none';c.zIndex=1.5;this.r.hanger.addChild(c);}
      const shape=this.type==='hyperedge'?'diamond':this.rds?.shape || 'circle';
      // Unit geometry follows the body transform; only shape changes redraw it.
      if(this.statusOutlineShape!==shape){
        this.statusOutlineShape=shape;c.clear().lineStyle(14,0xffffff,1);
        if(shape==='diamond')c.drawPolygon([0,-116,116,0,0,116,-116,0]);
        else if(shape==='square')c.drawRect(-98,-98,196,196);
        else if(shape==='triangle')c.drawPolygon([0,-116,110,87,-110,87]);
        else if(shape==='hexagon')c.drawPolygon([116,0,58,101,-58,101,-116,0,-58,-101,58,-101]);
        else c.drawCircle(0,0,116);
      }
      c.visible=visible;c.tint=parseInt(state.color.slice(1),16);
      c.x=x;c.y=y;c.scale.set(size/100*ns);c.alpha=alpha;
    }""")
    source = _replace_once(source, "if (this.rendered) return false;", "if (this.rendered || this.rdsHidden) return false;")
    source = _replace_once(source, "const r = this.r, { x, y } = this", "if(this.rdsHidden){this.circle.visible=this.text.visible=false;if(this.highlight)this.highlight.visible=false;if(this.statusOutline)this.statusOutline.visible=false;return;}\n      const r = this.r, { x, y } = this")
    source = _replace_once(source, "['circle', 'highlight', 'text']", "['circle', 'highlight', 'text', 'statusOutline']")
    source = _replace_once(source, "text.visible = textVis;", "this.renderStatusOutline(x,y,size,ns,nodeAlpha,circleVis);\n      text.visible = textVis;")
    source = _replace_once(source, "const r = this.r, s = this.source, t = this.target, hl = r.getHighlightNode();", "const r = this.r, s = this.source, t = this.target, hl = r.getHighlightNode();\n      if(s.rdsHidden || t.rdsHidden){this.px.visible=this.arrow.visible=false;return;}")
    source = _replace_once(source, "if (n.rendered) continue;", "if (n.rendered || n.rdsHidden) continue;")
    source = _replace_once(source, "c.beginFill(0xffffff).drawCircle(0, 0, 100).endFill();", "this.drawBody(c);")
    source = _replace_once(source, "const c = this.circle;\n      c.tint", "const c = this.circle;\n      if(this.rdsShape!==(this.type==='hyperedge'?'diamond':this.rds?.shape || 'circle'))this.drawBody(c);\n      c.tint")
    source = _replace_once(source, "const related = !hl || isHl || this.neighbors.has(hl.id);", "const related = !hl || isHl || r.rdsRelated?.has(this.id) || this.neighbors.has(hl.id);")
    source = _replace_once(source, "const on = s === hl || t === hl;", "const on = s === hl || t === hl || (this.rds?.hyperedge && r.rdsRelated?.has(this.rds.hyperedge));")
    source = _replace_once(source, "const col = on ? r.colors.lineHighlight : r.colors.line;", "const col = on ? r.colors.lineHighlight : this.rds?.color ? hexToColor(this.rds.color) : r.colors.line;")
    source = _replace_once(source, "a *= col.a;", "a *= col.a * (this.rds?.opacity ?? 1);")
    source = _replace_once(source, "const w = r.opts.lineSize / r.scale;", "const w = r.opts.lineSize * (this.rds?.width || 1) / r.scale;")
    source = _replace_once(source, "let showArrow = r.opts.showArrow && vis", "let showArrow = r.opts.showArrow && this.rds?.arrow && vis")
    source = _replace_once(source, "new PIXI.Sprite(PIXI.Texture.WHITE)", "new PIXI.TilingSprite(this.rds?.dash ? r.rdsDashTexture : PIXI.Texture.WHITE,1,1)")
    source = _replace_once(source, "line.tint = lerpColor(line.tint, col.rgb);", "line.tint = lerpColor(line.tint, col.rgb); line.tileScale.set(1/r.scale, w/4);")
    source = _replace_once(source, "this.nodes = []; this.nodeLookup", "const dashCanvas=document.createElement('canvas'); dashCanvas.width=12; dashCanvas.height=4; const dc=dashCanvas.getContext('2d'); dc.fillStyle='#fff'; dc.fillRect(0,0,6,4); this.rdsDashTexture=PIXI.Texture.from(dashCanvas);\n      this.nodes = []; this.nodeLookup")
    source = _replace_once(source, "for (const [s, t] of data.links)", "for (const [s, t, meta] of data.links)")
    source = _replace_once(source, "this.links.push(l); this.linkLookup.set(key, l); changed = true;", "l.rds = meta; this.links.push(l); this.linkLookup.set(key, l); changed = true;")
    source = _replace_once(source, "n.label = spec.label || id;\n        } else", "n.label = spec.label || id; n.rds = spec.rds;\n        } else")
    source = _replace_once(source, "n.color = spec.color || null; n.label = spec.label || id;", "n.color = spec.color || null; n.label = spec.label || id; n.rds = spec.rds;")
    source = _replace_once(source, "links: this.links.map(l => [l.source.id, l.target.id])", "links: this.links.map(l => [l.source.id, l.target.id, l.rds])")
    # Keep upstream node dragging: pin just the grabbed node, reheat to .3,
    # and let adjacent particles follow through their configured forces.
    source = _replace_once(source, "app.stage.on('pointerup', up).on('pointerupoutside', up);", "app.stage.on('pointerup', up).on('pointerupoutside', up);\n      window.addEventListener('blur', up); view.addEventListener('pointercancel', up);")
    source = _replace_once(source, "getHighlightNode() { return this.dragNode || this.highlightNode; }", """getHighlightNode() {
      const hl=this.dragNode || this.highlightNode || this.rdsPinned;
      if(this.rdsLastHL !== hl) {
        this.rdsLastHL=hl; this.rdsRelated=new Set(hl ? [hl.id] : []);
        if(hl) for(const n of this.nodes) if(n.rds?.members && (n===hl || n.rds.members.includes(hl.id))) {
          this.rdsRelated.add(n.id); for(const id of n.rds.members) this.rdsRelated.add(id);
        }
      }
      return hl;
    }""")
    return source


def patch_replica_worker(source):
    source, count = re.subn(r"importScripts\([\s\S]*?\);", "", source, count=1)
    if count != 1:
        raise ValueError("Pinned replica worker import no longer matches")
    source = _replace_once(source, "schedule();\n  if (nodes.length === 0) return;", "if (nodes.length === 0) return;\n  schedule();")
    source = _replace_once(source, "repelStrength: 1000", "repelStrength: 1000, damping: 0.4, flowStrength: 0.025, groupStrength: 0.035, edgeRepulsion: 0.25, edgeClearance: 45, relations: {}")
    source = _replace_once(source, "const VELOCITY_KEEP = 0.6;", "// Damping is adjustable; .4 retains upstream velocity multiplier .6.")
    source = _replace_once(source, "n.vx *= VELOCITY_KEEP;", "n.vx *= 1 - Math.max(0.1, Math.min(0.85, params.damping));")
    source = _replace_once(source, "n.vy *= VELOCITY_KEEP;", "n.vy *= 1 - Math.max(0.1, Math.min(0.85, params.damping));")
    source = _replace_once(source, "const forces = [fx, fy, fLink, fCharge, fCollide];", """function fFlow(a) {
  if(!params.flowStrength) return;
  for(const l of links) {
    const s=l.source,t=l.target;
    const cfg=params.relations[l.relation];if(cfg && (cfg.mode==='none'||cfg.strength===0))continue;
    if(l.family==='membership' || s.flowX == null || t.flowX == null || t.flowX<=s.flowX) continue;
    const k=Math.min(5,Math.max(0,params.linkDistance*.6-(t.x-s.x))*params.flowStrength)*a;
    s.vx-=k*.5;t.vx+=k*.5;
  }
}
function fGroups(a) {
  if(!params.groupStrength) return;
  const centers=new Map();
  for(const n of nodes) if(n.group) { const c=centers.get(n.group)||[0,0,0]; c[0]+=n.x;c[1]+=n.y;c[2]++;centers.set(n.group,c); }
  for(const n of nodes) if(n.group) {const c=centers.get(n.group); n.vx+=(c[0]/c[2]-n.x)*params.groupStrength*a; n.vy+=(c[1]/c[2]-n.y)*params.groupStrength*a; }
}
function fSigned(a) {
  for(const l of links) {
    const cfg=params.relations[l.relation] || {mode:'attract',strength:1};
    if(cfg.mode !== 'repel' || !cfg.strength || !params.linkStrength) continue;
    const s=l.source,t=l.target; let dx=t.x-s.x,dy=t.y-s.y,d=Math.hypot(dx,dy);
    if(d<.001){dx=.001;dy=0;d=.001;}
    const k=Math.min(8,Math.max(0,params.linkDistance-d)*.12)*cfg.strength*params.linkStrength*a/d;
    s.vx-=dx*k*.5;s.vy-=dy*k*.5;t.vx+=dx*k*.5;t.vy+=dy*k*.5;
  }
}
// A capsule field along each incidence leg, excluding the whole hyperedge's
// membership. Spatial bins and a rotating bounded budget keep work local.
let edgeOffset=0;
function fEdgeField(a) {
  if(!params.edgeRepulsion) return;
  const radius=Math.max(5,params.edgeClearance),cell=radius*2,grid=new Map();
  for(const n of nodes) {const key=Math.floor(n.x/cell)+','+Math.floor(n.y/cell);const bucket=grid.get(key)||[];bucket.push(n);grid.set(key,bucket);}
  const edges=links.filter(l=>l.hyperedge),budget=60000;let checks=0,processed=0;
  for(let i=0;i<edges.length && checks<budget;i++) {
    const l=edges[(edgeOffset+i)%edges.length],s=l.source,t=l.target,cfg=params.relations[l.relation];
    processed++;if(cfg && (cfg.mode==='none'||cfg.strength===0))continue;
    const members=nodeById.get(l.hyperedge)?.members || new Set([s.id,t.id]);
    const dx=t.x-s.x,dy=t.y-s.y,length2=dx*dx+dy*dy;if(length2<1e-8)continue;
    const samples=Math.min(64,Math.max(1,Math.ceil(Math.sqrt(length2)/cell))),seen=new Set();
    for(let j=0;j<=samples && checks<budget;j++) {
      const bx=Math.floor((s.x+dx*j/samples)/cell),by=Math.floor((s.y+dy*j/samples)/cell);
      for(let gx=bx-1;gx<=bx+1;gx++)for(let gy=by-1;gy<=by+1;gy++)for(const n of grid.get(gx+','+gy)||[]) {
        if(checks++>=budget)break;if(n===s||n===t||members.has(n.id)||n.id===l.hyperedge||seen.has(n.id))continue;seen.add(n.id);
        const u=Math.max(0,Math.min(1,((n.x-s.x)*dx+(n.y-s.y)*dy)/length2));
        let nx=n.x-(s.x+dx*u),ny=n.y-(s.y+dy*u),d=Math.hypot(nx,ny);if(d>=radius)continue;
        if(d<.001){const len=Math.sqrt(length2);nx=-dy/len;ny=dx/len;d=1;}
        const force=Math.min(6,(radius-d)*params.edgeRepulsion)*a,px=nx/d*force,py=ny/d*force;
        n.vx+=px;n.vy+=py;s.vx-=px*(1-u);s.vy-=py*(1-u);t.vx-=px*u;t.vy-=py*u;
      }
    }
  }
  if(edges.length)edgeOffset=(edgeOffset+Math.max(1,processed))%edges.length;
}
const forces = [fx, fy, fLink, fCharge, fCollide, fFlow, fGroups, fSigned, fEdgeField];""")
    source = _replace_once(source, "fLink.strength((l, i, ls) => params.linkStrength * baseLinkStrength(l, i, ls));", """fLink.strength((l,i,ls) => { const cfg=params.relations[l.relation] || {mode:'attract',strength:1}; return cfg.mode === 'attract' ? params.linkStrength*cfg.strength*baseLinkStrength(l,i,ls) : 0; });
  // Relative flow uses current parameters; no fixed column coordinates.""")
    source = _replace_once(source, ".map(([s, t]) => ({ source: nodeById.get(s), target: nodeById.get(t) }));", ".map(([s,t,meta]) => ({...meta, source:nodeById.get(s), target:nodeById.get(t)}));")
    source = _replace_once(source, "fLink.distance(params.linkDistance);", "fLink.distance(l=>params.linkDistance*(l.family==='membership'?2:1));")
    source = _replace_once(source, "fCharge.strength(-Math.max(1, Math.abs(params.repelStrength)));", """fCharge.strength(n=>-Math.max(1,Math.abs(params.repelStrength))*Math.max(1,Math.min(3,n.chargeWeight || 1)));
  fCollide.radius(n=>Math.max(24,Math.min(120,n.collisionRadius || 60)));""")
    source = _replace_once(source, "if (m.forceNode) {", """if(m.layoutTargets) {
    for(const n of nodes) {const target=m.layoutTargets[n.id]; n.flowX=target?.flowX ?? null; n.group=target?.group ?? null; n.members=new Set(target?.members || []);n.chargeWeight=target?.chargeWeight ?? 1;n.collisionRadius=target?.collisionRadius ?? 60;}
    needParams=true; needInit=true;
  }
  if (m.forceNode) {""")
    return source


def render_replica_html(result, replica_root, pixi_js):
    sources = {}
    for name, expected in REPLICA_FILES.items():
        raw = (Path(replica_root) / "replica" / name).read_bytes()
        # A checkout with LF rather than CRLF must match the same upstream bytes.
        normalized = raw.replace(b"\r\n", b"\n")
        if hashlib.sha256(normalized).hexdigest() != expected:
            raise ValueError("Replica source does not match pinned commit: " + name)
        sources[name] = raw.decode("utf-8").replace("\r\n", "\n")
    pixi = Path(pixi_js).read_bytes()
    if hashlib.sha256(pixi).hexdigest() != PIXI_SHA256:
        raise ValueError("PixiJS must be the official 7.4.3 dist/pixi.min.js")
    spec = result.get("graph") or {}
    adapted = _adapt_record_view(spec) if spec else spec
    report = _record_view_report(spec, adapted=adapted, source_base=result.get("record_source_base_dir")) if spec else {"record_topology": {"issues": []}}
    presentation = display_records(result, adapted=adapted, report=report)
    payload = {**result, "display": presentation, "analysis_notice": analysis_notice(result), "replica_view": replica_view(result, presentation=presentation, report=report),
               "renderer": {"repository": "https://github.com/runningZ1/obsidian-graph-replica", "commit": REPLICA_COMMIT,
                            "pixi": "7.4.3", "runtime_network": False}}
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c")
    data = data.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    # Template replacement happens before data insertion. No data enters JS/HTML.
    assets = {"__STYLE__": sources["style.css"] + REPLICA_EXTRA_CSS,
              "__PIXI__": "/* " + PIXI_LICENSE + " */\n" + pixi.decode("utf-8"), "__D3__": D3_BUNDLE,
              "__WORKER__": patch_replica_worker(sources["sim-worker.js"]),
              "__RENDERER__": patch_replica_renderer(sources["renderer.js"]), "__APP__": REPLICA_APP}
    page = REPLICA_HTML
    for key, value in assets.items():
        page = page.replace(key, value.replace("</script", "<\\/script"))
    return page.replace("__DATA__", data)


PIXI_LICENSE = '''The MIT License
Copyright (c) 2013-2023 Mathew Groves, Chad Engler

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.'''

REPLICA_HTML = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; img-src data: blob:; worker-src blob:; connect-src 'none'">
<title>RDS · 关系图谱</title><style>__STYLE__</style></head><body class="theme-dark">
<header class="view-header"><span id="source-name">RDS</span><span class="title">关系图谱</span><span class="spacer"></span>
<button class="icon-btn" id="graph-info" aria-label="研究说明" title="研究说明" hidden>ⓘ</button>
<button class="icon-btn" id="fit" aria-label="适应画布" title="适应画布">⊡</button>
<button class="icon-btn" id="restart" aria-label="重新布局" title="重新布局">↻</button>
<button class="icon-btn" id="grow" aria-label="重播生长" title="重播生长">▷</button>
<button class="icon-btn" id="skip-growth" aria-label="立即显示全部" title="立即显示全部" hidden>»</button>
<button class="icon-btn" id="theme" aria-label="切换主题" title="切换主题">◐</button>
<button class="icon-btn" id="settings" aria-label="图谱设置" title="图谱设置">⚙</button></header>
<main id="graph" aria-label="RDS 超图画布"><div class="graph-controls" id="controls"></div>
<button class="icon-btn graph-controls-gear" id="gear" aria-label="展开图谱设置">⚙</button>
<section class="note-card" id="note-card" aria-label="节点详查"></section>
<aside id="legend" aria-label="图例"></aside><div id="counts"></div><div id="state" role="status"></div><div id="analysis-note" role="note"></div><div id="hover-info" role="tooltip" hidden></div><div id="growth-info"></div></main>
<script id="snapshot" type="application/json">__DATA__</script>
<script id="d3-worker-lib" type="text/plain">__D3__</script>
<script>__PIXI__</script><script>__WORKER__</script><script>__RENDERER__</script><script>__APP__</script></body></html>'''

REPLICA_EXTRA_CSS = '''
#source-name{font-size:12px;color:var(--text-muted);max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#legend,#counts{position:absolute;bottom:12px;font-size:12px;color:var(--text-muted);z-index:1}
#legend{left:12px;bottom:34px;max-width:calc(100% - 36px);border:1px solid var(--background-modifier-border);border-radius:6px;background:var(--background-primary);padding:6px 9px;line-height:1.6;pointer-events:none}
#legend .legend-row{display:flex;flex-wrap:wrap;gap:2px 12px}#legend .symbol{font-size:14px;margin-right:4px}#legend .legend-note{font-size:11px;color:var(--text-muted)}
#counts{right:16px;pointer-events:none}#state{position:absolute;left:16px;top:14px;font-size:12px;color:var(--text-muted);pointer-events:none;z-index:1}
#analysis-note{position:absolute;left:16px;top:36px;max-width:calc(100% - 36px);font-size:12px;color:var(--text-muted);pointer-events:none;z-index:1}
#hover-info{position:absolute;left:12px;top:35px;max-width:calc(100% - 36px);z-index:2;background:var(--background-secondary);border:1px solid var(--background-modifier-border);padding:7px 10px;border-radius:5px;color:var(--text-normal);font-size:12px;pointer-events:none;white-space:pre-line}
#growth-info{position:absolute;left:16px;top:14px;color:var(--text-muted);font-size:12px;pointer-events:none;z-index:2}.icon-btn[hidden],#hover-info[hidden]{display:none}
.note-card{width:340px;max-height:65%;bottom:110px}
.note-card .summary-text{line-height:1.6}.note-card .result-tag{color:var(--text-normal)}
button.record-link{display:block;border:0;background:none;color:var(--text-normal);text-align:left;font:inherit;cursor:pointer;padding:4px;width:100%;border-radius:4px}
.record-link:hover{background:var(--background-modifier-hover)}
.gc-head{border:0;background:none;width:calc(100% - 8px);color:inherit;font:inherit;text-align:left}.gc-head .chev{font-size:14px}
.gc-body select{background:var(--background-primary);color:var(--text-normal);border:1px solid var(--background-modifier-border);border-radius:4px;padding:4px;font:inherit}
.gc-item input[type=checkbox]{accent-color:var(--interactive-accent)}.gc-item input[type=color]{width:25px;height:23px;border:0;background:none}
.relation-style{display:flex;gap:7px;align-items:center;margin:6px 0 10px}.relation-style input[type=range]{flex:1;width:70px}.relation-style select{width:70px}
@media(max-width:600px){.view-header .title{display:none}.graph-controls{width:245px}.note-card{width:calc(100% - 24px);bottom:118px}#legend{font-size:11px}}
'''

REPLICA_APP = r'''
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
'''


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--hypergraph", help="Explicit dependency JSON instead of the saved TMS snapshot")
    parser.add_argument("--archive", help="Complete read-only archive JSON or ZIP, browsed with semantic zoom")
    parser.add_argument("--archive-node", help="Original node ID to inspect with --archive --export-agent-input")
    parser.add_argument("--archive-edge", help="Original edge ID to inspect with --archive --export-agent-input")
    parser.add_argument("--demo", action="store_true", help="Synthetic visual preview only")
    parser.add_argument("--large", action="store_true", help="With --demo: 1,200 synthetic claim nodes")
    parser.add_argument("--output", default="rds-hypergraph.html")
    parser.add_argument("--replica-root", help="Local checkout of the pinned runningZ1/obsidian-graph-replica")
    parser.add_argument("--pixi-js", help="Official pixi.js 7.4.3 dist/pixi.min.js (required with --replica-root)")
    parser.add_argument("--export-agent-input", action="store_true", help="Print separate raw/readable JSON layers to stdout; no HTML is written")
    parser.add_argument("--readable-json", help="Agent-authored readable object bound to this raw graph and snapshot")
    args = parser.parse_args(argv)
    try:
        root, output = Path(args.root).resolve(), Path(args.output).resolve()
        if args.export_agent_input and args.readable_json:
            raise ValueError("Export fresh agent input or consume --readable-json, not both")
        if not args.export_agent_input and (output.suffix.lower() != ".html" or output.is_relative_to((root / ".rds").resolve())):
            raise ValueError("Output must be an .html file outside the .rds ledger")
        if args.demo and args.hypergraph or args.large and not args.demo:
            raise ValueError("Use --large only with --demo, and --demo without --hypergraph")
        if args.archive and (args.hypergraph or args.demo or args.large):
            raise ValueError("Use --archive separately from --hypergraph, --demo and --large")
        if args.archive_node or args.archive_edge:
            if not args.archive or not args.export_agent_input or args.archive_node and args.archive_edge:
                raise ValueError("Inspect one --archive-node or --archive-edge with --archive --export-agent-input")
        if args.archive and args.readable_json:
            raise ValueError("Archive names and summaries come from its readable records; dependency sidecars use --hypergraph")
        if bool(args.replica_root) != bool(args.pixi_js):
            raise ValueError("Use --replica-root and --pixi-js together")
        if args.archive and not args.export_agent_input and not args.replica_root:
            raise ValueError("Archive browsing requires the existing --replica-root and --pixi-js assets")
        if (args.hypergraph or args.archive) and not args.export_agent_input:
            source = Path(args.hypergraph or args.archive).resolve()
            if output == source or output.exists() and source.exists() and output.samefile(source):
                raise ValueError("Output must not overwrite the hypergraph input")
        if args.archive:
            from rds_hypergraph_archive import read_archive, render_archive_html, archive_agent_input
            result = read_archive(args.archive)
            if args.export_agent_input:
                print(json.dumps(archive_agent_input(result, node_id=args.archive_node, edge_id=args.archive_edge),
                                 ensure_ascii=False, allow_nan=False))
                return 0
            page = render_archive_html(result, args.replica_root, args.pixi_js)
        else:
            result = read_graph(root, args.hypergraph, demo=args.demo, large=args.large)
            if args.hypergraph and result["status"] == "UNAVAILABLE":
                raise ValueError(result["reason"])
            if args.export_agent_input:
                from rds_hypergraph_readable import agent_input
                print(json.dumps(agent_input(result), ensure_ascii=False, allow_nan=False))
                return 0
            if args.readable_json:
                from rds_hypergraph_readable import load_readable, with_readable
                readable_path = Path(args.readable_json).resolve()
                if output == readable_path or output.exists() and output.samefile(readable_path):
                    raise ValueError("Output must not overwrite readable information")
                result = with_readable(result, load_readable(readable_path, result))
            page = render_replica_html(result, args.replica_root, args.pixi_js) if args.replica_root else render_html(result)
        output.parent.mkdir(parents=True, exist_ok=True)
        write_html_atomic(output, page)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(output), "status": result["status"], "source": result["source"],
                      "demo": result["demo"], "counts": result.get("counts"),
                      "analysis_status": result.get("analysis_status")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
