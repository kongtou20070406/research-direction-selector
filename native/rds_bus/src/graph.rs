//! In-memory Topology Graph (Read-side Derived View)
//! Pure causal relationships and dependency traversal; no persistent state side-effects.

pub const NODE_CLAIM: u8 = 1;
pub const NODE_PREMISE: u8 = 2;
pub const NODE_OBSTRUCTION: u8 = 3;
pub const NODE_ANALOGY: u8 = 4;
pub const NODE_GOAL: u8 = 5;

pub const TRUTH_UNKNOWN: u8 = 0;
pub const TRUTH_TRUE: u8 = 1;
pub const TRUTH_FALSE: u8 = 2;

pub const EDGE_AND_DEDUCTION: u8 = 1;
pub const EDGE_OR_ALTERNATIVE: u8 = 2;
pub const EDGE_OBSTRUCTS: u8 = 3;
pub const EDGE_ISOMORPHISM: u8 = 4;

#[derive(Clone, Debug)]
pub struct Node {
    pub id: u32,
    pub kind: u8,
    pub fact_hash: [u8; 32],
    pub truth: u8,
}

#[derive(Clone, Debug)]
pub struct HyperEdge {
    pub id: u32,
    pub kind: u8,
    pub sources: Box<[u32]>,
    pub target: u32,
}


pub struct TopologyGraph {
    max_nodes: usize,
    max_edges: usize,
    max_bytes: usize,
    bytes: usize,
    nodes: Vec<Node>,
    edges: Vec<HyperEdge>,
    incoming: Vec<Vec<u32>>, // target -> edge_ids
    outgoing: Vec<Vec<u32>>, // source -> edge_ids
}

impl TopologyGraph {
    pub fn new() -> Self {
        Self::bounded(4096, 16384, 8 * 1024 * 1024).unwrap()
    }

    pub fn bounded(max_nodes: usize, max_edges: usize, max_bytes: usize) -> Option<Self> {
        if max_nodes == 0 || max_nodes > 4096 || max_edges == 0 || max_edges > 16384
            || max_bytes == 0 || max_bytes > 8 * 1024 * 1024 { return None; }
        Some(Self { max_nodes, max_edges, max_bytes, bytes: 0, nodes: Vec::new(),
            edges: Vec::new(), incoming: Vec::new(), outgoing: Vec::new() })
    }
    pub fn add_node(&mut self, kind: u8, fact_hash: [u8; 32], truth: u8) -> u32 {
        if !(1..=5).contains(&kind) || truth > TRUTH_FALSE || self.nodes.len() >= self.max_nodes
            || 128 > self.max_bytes - self.bytes { return u32::MAX; }
        self.bytes += 128;
        let id = self.nodes.len() as u32;
        self.nodes.push(Node {
            id,
            kind,
            fact_hash,
            truth,
        });
        self.incoming.push(Vec::new());
        self.outgoing.push(Vec::new());
        id
    }

    pub fn set_truth(&mut self, node_id: u32, truth: u8) -> bool {
        if truth > TRUTH_FALSE { return false; }
        if let Some(node) = self.nodes.get_mut(node_id as usize) {
            node.truth = truth;
            true
        } else {
            false
        }
    }

    pub fn add_edge(&mut self, kind: u8, sources: &[u32], target: u32) -> Option<u32> {
        let n = self.nodes.len() as u32;
        if !(1..=4).contains(&kind) || sources.len() > 4096 || target >= n || sources.iter().any(|&s| s >= n)
            || self.edges.len() >= self.max_edges || 128 + 16 * sources.len() > self.max_bytes - self.bytes {
            return None;
        }
        self.bytes += 128 + 16 * sources.len();
        let edge_id = self.edges.len() as u32;
        let edge = HyperEdge {
            id: edge_id,
            kind,
            sources: sources.to_vec().into_boxed_slice(),
            target,
        };
        self.incoming[target as usize].push(edge_id);
        for &s in sources {
            self.outgoing[s as usize].push(edge_id);
        }
        self.edges.push(edge);
        Some(edge_id)
    }

    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    /// Backward reachable prerequisites from a given target node.
    pub fn backward_dependencies(&self, target: u32) -> Vec<u32> {
        if (target as usize) >= self.nodes.len() {
            return Vec::new();
        }
        let mut visited = vec![false; self.nodes.len()];
        let mut queue = std::collections::VecDeque::new();
        queue.push_back(target);
        visited[target as usize] = true;
        let mut deps = Vec::new();

        while let Some(curr) = queue.pop_front() {
            for &edge_id in &self.incoming[curr as usize] {
                let edge = &self.edges[edge_id as usize];
                if edge.kind != EDGE_AND_DEDUCTION && edge.kind != EDGE_OR_ALTERNATIVE { continue; }
                for &src in edge.sources.iter() {
                    if !visited[src as usize] {
                        visited[src as usize] = true;
                        deps.push(src);
                        queue.push_back(src);
                    }
                }
            }
        }
        deps
    }

    /// Unresolved prerequisite frontier, not a minimum cut or proof obligation set.
    pub fn unresolved_frontier(&self, target: u32) -> Vec<u32> {
        if (target as usize) >= self.nodes.len() {
            return Vec::new();
        }
        let mut visited = vec![false; self.nodes.len()];
        let seeds: Vec<_> = self.nodes.iter().filter(|n| n.truth == TRUTH_TRUE).map(|n| n.id).collect();
        let mut supported = vec![false; self.nodes.len()];
        for id in seeds.iter().copied().chain(self.forward_closure(&seeds)) { supported[id as usize] = true; }
        let mut obstructions = Vec::new();
        let mut queue = std::collections::VecDeque::new();
        queue.push_back(target);
        visited[target as usize] = true;

        while let Some(curr) = queue.pop_front() {
            let node = &self.nodes[curr as usize];
            if supported[curr as usize] { continue; }
            if curr != target && node.truth != TRUTH_TRUE {
                obstructions.push(curr);
                // Do not expand further behind an unresolved obstruction
                continue;
            }
            for &edge_id in &self.incoming[curr as usize] {
                let edge = &self.edges[edge_id as usize];
                if edge.kind != EDGE_AND_DEDUCTION && edge.kind != EDGE_OR_ALTERNATIVE { continue; }
                for &src in edge.sources.iter() {
                    if !visited[src as usize] {
                        visited[src as usize] = true;
                        queue.push_back(src);
                    }
                }
            }
        }
        obstructions
    }

    /// Forward closure: all nodes deductively derived if the given premises are true.
    pub fn forward_closure(&self, known_true: &[u32]) -> Vec<u32> {
        let mut true_set = vec![false; self.nodes.len()];
        for &k in known_true {
            if (k as usize) < self.nodes.len() && self.nodes[k as usize].truth != TRUTH_FALSE {
                true_set[k as usize] = true;
            }
        }

        let mut newly_derived = Vec::new();
        let mut changed = true;

        while changed {
            changed = false;
            for edge in &self.edges {
                if true_set[edge.target as usize] || self.nodes[edge.target as usize].truth == TRUTH_FALSE {
                    continue;
                }
                let satisfied = match edge.kind {
                    EDGE_AND_DEDUCTION => edge.sources.iter().all(|&s| true_set[s as usize]),
                    EDGE_OR_ALTERNATIVE => edge.sources.iter().any(|&s| true_set[s as usize]),
                    _ => false,
                };
                if satisfied {
                    true_set[edge.target as usize] = true;
                    newly_derived.push(edge.target);
                    changed = true;
                }
            }
        }
        newly_derived
    }

    /// Drop the relationship view; retained capacities stay bounded, no ledger writes.
    pub fn clear(&mut self) {
        self.bytes = 0;
        self.nodes.clear();
        self.edges.clear();
        self.incoming.clear();
        self.outgoing.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_graph_and_node_creation() {
        let mut g = TopologyGraph::new();
        let n0 = g.add_node(NODE_PREMISE, [0u8; 32], TRUTH_TRUE);
        let n1 = g.add_node(NODE_CLAIM, [1u8; 32], TRUTH_UNKNOWN);
        assert_eq!(n0, 0);
        assert_eq!(n1, 1);
        assert_eq!(g.node_count(), 2);
    }

    #[test]
    fn test_and_deduction_and_unresolved_frontier() {
        let mut g = TopologyGraph::new();
        // Premise 0 (True), Premise 1 (Unknown) -> Lemma 2 -> Theorem 3
        let p0 = g.add_node(NODE_PREMISE, [0u8; 32], TRUTH_TRUE);
        let p1 = g.add_node(NODE_PREMISE, [1u8; 32], TRUTH_UNKNOWN);
        let l2 = g.add_node(NODE_CLAIM, [2u8; 32], TRUTH_UNKNOWN);
        let t3 = g.add_node(NODE_GOAL, [3u8; 32], TRUTH_UNKNOWN);

        g.add_edge(EDGE_AND_DEDUCTION, &[p0, p1], l2);
        g.add_edge(EDGE_AND_DEDUCTION, &[l2], t3);

        // The unresolved intermediate node is the frontier, not a minimum cut.
        let cut = g.unresolved_frontier(t3);
        assert_eq!(cut, vec![l2]); // because l2 is unknown, it stops at l2

        // A proved lemma supports the goal without re-proving its prerequisites.
        g.set_truth(l2, TRUTH_TRUE);
        let cut_after = g.unresolved_frontier(t3);
        assert!(cut_after.is_empty());

        g.set_truth(l2, TRUTH_UNKNOWN);
        assert_eq!(g.unresolved_frontier(t3), vec![l2]);

        // When p1 becomes TRUE:
        g.set_truth(p1, TRUTH_TRUE);
        let cut_resolved = g.unresolved_frontier(t3);
        assert!(cut_resolved.is_empty());
    }

    #[test]
    fn test_speculative_rollback_via_clear() {
        let mut g = TopologyGraph::new();
        g.add_node(NODE_ANALOGY, [9u8; 32], TRUTH_UNKNOWN);
        assert_eq!(g.node_count(), 1);
        g.clear();
        assert_eq!(g.node_count(), 0);
    }

    #[test]
    fn or_alternative_survives_retraction() {
        let mut g = TopologyGraph::new();
        let a = g.add_node(NODE_PREMISE, [0; 32], TRUTH_TRUE);
        let b = g.add_node(NODE_PREMISE, [1; 32], TRUTH_TRUE);
        let goal = g.add_node(NODE_GOAL, [2; 32], TRUTH_UNKNOWN);
        g.add_edge(EDGE_OR_ALTERNATIVE, &[a, b], goal).unwrap();
        assert_eq!(g.forward_closure(&[a]), vec![goal]);
        g.set_truth(a, TRUTH_FALSE);
        assert!(g.unresolved_frontier(goal).is_empty());
        assert_eq!(g.forward_closure(&[a, b]), vec![goal]);
        g.set_truth(b, TRUTH_UNKNOWN);
        assert_eq!(g.unresolved_frontier(goal), vec![a, b]);
    }

    #[test]
    fn contradicted_seeds_and_targets_do_not_propagate() {
        let mut g = TopologyGraph::new();
        let a = g.add_node(NODE_PREMISE, [0; 32], TRUTH_FALSE);
        let b = g.add_node(NODE_CLAIM, [1; 32], TRUTH_UNKNOWN);
        let c = g.add_node(NODE_GOAL, [2; 32], TRUTH_UNKNOWN);
        g.add_edge(EDGE_AND_DEDUCTION, &[a], b).unwrap();
        g.add_edge(EDGE_OR_ALTERNATIVE, &[b], c).unwrap();
        assert!(g.forward_closure(&[a]).is_empty());
        g.set_truth(a, TRUTH_TRUE);
        g.set_truth(b, TRUTH_FALSE);
        assert!(g.forward_closure(&[a, b]).is_empty());
        assert_eq!(g.unresolved_frontier(c), vec![b]);
    }

    #[test]
    fn relation_only_edges_never_prove_or_add_prerequisites() {
        for kind in [EDGE_OBSTRUCTS, EDGE_ISOMORPHISM] {
            let mut g = TopologyGraph::new();
            let source = g.add_node(NODE_ANALOGY, [0; 32], TRUTH_TRUE);
            let target = g.add_node(NODE_GOAL, [1; 32], TRUTH_UNKNOWN);
            g.add_edge(kind, &[source], target).unwrap();
            assert!(g.forward_closure(&[source]).is_empty());
            assert!(g.backward_dependencies(target).is_empty());
            // An empty frontier alone is not proof of support.
            assert!(g.unresolved_frontier(target).is_empty());
        }
    }

    #[test]
    fn graph_bounds_and_repeat_clear() {
        for bounds in [(0, 1, 128), (4097, 1, 128), (1, 0, 128),
                       (1, 16385, 128), (1, 1, 0), (1, 1, 8 * 1024 * 1024 + 1)] {
            assert!(TopologyGraph::bounded(bounds.0, bounds.1, bounds.2).is_none());
        }
        let mut g = TopologyGraph::bounded(2, 1, 400).unwrap();
        for _ in 0..3 {
            assert_eq!(g.add_node(NODE_PREMISE, [0; 32], TRUTH_TRUE), 0);
            assert_eq!(g.add_node(NODE_GOAL, [1; 32], TRUTH_UNKNOWN), 1);
            assert_eq!(g.add_node(NODE_CLAIM, [2; 32], TRUTH_UNKNOWN), u32::MAX);
            assert_eq!(g.add_edge(EDGE_AND_DEDUCTION, &[0], 1), Some(0));
            assert_eq!(g.add_edge(EDGE_AND_DEDUCTION, &[], 1), None);
            assert_eq!(g.forward_closure(&[0]), vec![1]);
            g.clear();
            g.clear();
            assert_eq!((g.node_count(), g.edge_count(), g.bytes), (0, 0, 0));
        }
        let mut g = TopologyGraph::bounded(3, 3, 399).unwrap();
        g.add_node(NODE_PREMISE, [0; 32], TRUTH_UNKNOWN);
        g.add_node(NODE_GOAL, [1; 32], TRUTH_UNKNOWN);
        assert_eq!(g.add_edge(EDGE_AND_DEDUCTION, &[0], 1), None);
        assert_eq!(g.edge_count(), 0);
        assert_eq!(g.bytes, 256);
        assert_eq!(g.add_node(0, [0; 32], TRUTH_UNKNOWN), u32::MAX);
        assert!(!g.set_truth(0, 3));
        assert_eq!(g.add_edge(0, &[0], 1), None);
        assert_eq!(g.add_edge(EDGE_AND_DEDUCTION, &[2], 1), None);
        let mut tiny = TopologyGraph::bounded(1, 1, 127).unwrap();
        assert_eq!(tiny.add_node(NODE_PREMISE, [0; 32], TRUTH_UNKNOWN), u32::MAX);
        let mut edge_limited = TopologyGraph::bounded(2, 1, 4096).unwrap();
        edge_limited.add_node(NODE_GOAL, [0; 32], TRUTH_UNKNOWN);
        assert_eq!(edge_limited.add_edge(EDGE_AND_DEDUCTION, &[], 0), Some(0));
        assert_eq!(edge_limited.add_edge(EDGE_AND_DEDUCTION, &[], 0), None);
        let mut source_limited = TopologyGraph::new();
        source_limited.add_node(NODE_GOAL, [0; 32], TRUTH_UNKNOWN);
        assert_eq!(source_limited.add_edge(EDGE_AND_DEDUCTION, &vec![0; 4097], 0), None);
        assert_eq!(source_limited.edge_count(), 0);
    }

    #[test]
    fn ffi_query_buffer_bounds() {
        unsafe {
            let g = rds_graph_create_bounded(2, 1, 400);
            assert!(!g.is_null());
            let hash = [0; 32];
            assert_eq!(rds_graph_add_node(g, NODE_PREMISE, hash.as_ptr(), TRUTH_TRUE), 0);
            assert_eq!(rds_graph_add_node(g, NODE_GOAL, hash.as_ptr(), TRUTH_UNKNOWN), 1);
            let seeds = [0];
            assert_eq!(rds_graph_add_edge(g, EDGE_AND_DEDUCTION, seeds.as_ptr(), 1, 1), 0);
            let mut len = 99;
            assert_eq!(rds_graph_forward_closure(g, seeds.as_ptr(), 1, std::ptr::null_mut(), 0, &mut len), 0);
            assert_eq!(len, 1);
            let mut out = 99;
            assert_eq!(rds_graph_forward_closure(g, seeds.as_ptr(), 1, &mut out, 0, &mut len), -2);
            assert_eq!(out, 99);
            assert_eq!(rds_graph_forward_closure(g, seeds.as_ptr(), 1, &mut out, 1, &mut len), 0);
            assert_eq!(out, 1);
            assert_eq!(rds_graph_backward_dependencies(g, 2, &mut out, 1, &mut len), -1);
            assert_eq!(rds_graph_clear(g), 0);
            assert_eq!(rds_graph_clear(g), 0);
            rds_graph_destroy(g);
        }
    }
}

// ---------------------------------------------------------

// ---------------------------------------------------------
// C ABI exports for Python / foreign interop
// ---------------------------------------------------------

#[no_mangle]
pub unsafe extern "C" fn rds_graph_create() -> *mut TopologyGraph {
    Box::into_raw(Box::new(TopologyGraph::new()))
}

#[no_mangle]
pub unsafe extern "C" fn rds_graph_destroy(graph: *mut TopologyGraph) {
    if !graph.is_null() {
        unsafe { drop(Box::from_raw(graph)) };
    }
}

#[no_mangle]
pub unsafe extern "C" fn rds_graph_add_node(
    graph: *mut TopologyGraph,
    kind: u8,
    fact_hash: *const u8,
    truth: u8,
) -> u32 {
    if graph.is_null() || fact_hash.is_null() {
        return u32::MAX;
    }
    let mut hash = [0u8; 32];
    unsafe {
        std::ptr::copy_nonoverlapping(fact_hash, hash.as_mut_ptr(), 32);
        (*graph).add_node(kind, hash, truth)
    }
}

#[no_mangle]
pub unsafe extern "C" fn rds_graph_set_truth(
    graph: *mut TopologyGraph,
    node_id: u32,
    truth: u8,
) -> i32 {
    if graph.is_null() {
        return -1;
    }
    unsafe {
        if (*graph).set_truth(node_id, truth) {
            0
        } else {
            -1
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn rds_graph_add_edge(
    graph: *mut TopologyGraph,
    kind: u8,
    sources: *const u32,
    source_count: usize,
    target: u32,
) -> u32 {
    if graph.is_null() || source_count > 4096 || (sources.is_null() && source_count > 0) {
        return u32::MAX;
    }
    unsafe {
        let src_slice = if source_count == 0 {
            &[]
        } else {
            std::slice::from_raw_parts(sources, source_count)
        };
        (*graph).add_edge(kind, src_slice, target).unwrap_or(u32::MAX)
    }
}


#[no_mangle]
pub extern "C" fn rds_graph_create_bounded(nodes: usize, edges: usize, bytes: usize) -> *mut TopologyGraph {
    TopologyGraph::bounded(nodes, edges, bytes).map_or(std::ptr::null_mut(), |g| Box::into_raw(Box::new(g)))
}
/// # Safety
/// All handles/buffers must be live, exclusive, and appropriately sized.
unsafe fn output(values: Vec<u32>, buf: *mut u32, cap: usize, len: *mut usize) -> i32 {
    if len.is_null() { return -1; }
    unsafe { *len = values.len(); }
    if buf.is_null() { return 0; }
    if cap < values.len() { return -2; }
    unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), buf, values.len()); }
    0
}
/// # Safety
/// Graph and writable output buffers must be live; calls are serialized.
#[no_mangle]
pub unsafe extern "C" fn rds_graph_unresolved_frontier(g: *const TopologyGraph, target: u32, buf: *mut u32, cap: usize, len: *mut usize) -> i32 {
    let Some(g) = (unsafe { g.as_ref() }) else { return -1; };
    if target as usize >= g.node_count() { return -1; }
    unsafe { output(g.unresolved_frontier(target), buf, cap, len) }
}
/// # Safety
/// Graph and writable output buffers must be live; calls are serialized.
#[no_mangle]
pub unsafe extern "C" fn rds_graph_backward_dependencies(g: *const TopologyGraph, target: u32, buf: *mut u32, cap: usize, len: *mut usize) -> i32 {
    let Some(g) = (unsafe { g.as_ref() }) else { return -1; };
    if target as usize >= g.node_count() { return -1; }
    unsafe { output(g.backward_dependencies(target), buf, cap, len) }
}
/// # Safety
/// Graph, seeds, and output buffers must be live and appropriately sized.
#[no_mangle]
pub unsafe extern "C" fn rds_graph_forward_closure(g: *const TopologyGraph, seeds: *const u32, count: usize, buf: *mut u32, cap: usize, len: *mut usize) -> i32 {
    let Some(g) = (unsafe { g.as_ref() }) else { return -1; };
    if count > g.node_count() || (count > 0 && seeds.is_null()) { return -1; }
    let seeds = if count == 0 { &[] } else { unsafe { std::slice::from_raw_parts(seeds, count) } };
    if seeds.iter().any(|&s| s as usize >= g.node_count()) { return -1; }
    unsafe { output(g.forward_closure(seeds), buf, cap, len) }
}
/// # Safety
/// Graph must be live and exclusively accessed.
#[no_mangle]
pub unsafe extern "C" fn rds_graph_clear(g: *mut TopologyGraph) -> i32 {
    let Some(g) = (unsafe { g.as_mut() }) else { return -1; };
    g.clear(); 0
}
