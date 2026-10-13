"""F2 pure reference projection; compatible with existing hypergraph schema 1.

No content is copied into the graph and no OBSERVED tag implies support.
Projection does not inspect files, audit receipts, authorize or execute work.
"""
from dataclasses import dataclass

from rds_core import object_digest
from rds_decision_packet import DecisionPacket


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    source: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v for v in (self.id, self.source)):
            raise ValueError("node must contain string references")

    def to_dict(self):
        return {"id": self.id, "source": self.source, "status": "UNKNOWN"}


@dataclass(frozen=True, slots=True)
class Hyperedge:
    id: str
    premises: tuple[str, ...]
    conclusion: str
    source: str

    def __post_init__(self):
        if not (all(isinstance(v, str) and v for v in (self.id, self.conclusion, self.source))
                and type(self.premises) is tuple and all(isinstance(p, str) for p in self.premises)):
            raise ValueError("premises must be immutable references")

    def to_dict(self):
        return {"id": self.id, "premises": list(self.premises), "conclusion": self.conclusion,
                "source": self.source, "status": "PROPOSED"}


@dataclass(frozen=True, slots=True)
class PacketGraph:
    packet_id: str
    nodes: tuple[Node, ...]
    hyperedges: tuple[Hyperedge, ...]
    goals: tuple[str, ...]
    status: str

    def __post_init__(self):
        if not (type(self.nodes) is tuple and all(type(n) is Node for n in self.nodes)
                and type(self.hyperedges) is tuple and all(type(e) is Hyperedge for e in self.hyperedges)
                and type(self.goals) is tuple and all(isinstance(g, str) for g in self.goals)
                and isinstance(self.packet_id, str) and self.status in ("COMPLETE", "PARTIAL")):
            raise ValueError("graph values must be immutable")

    def to_dict(self):
        return {"schema": 1, "nodes": [n.to_dict() for n in self.nodes],
                "hyperedges": [e.to_dict() for e in self.hyperedges], "goals": list(self.goals),
                "packet_id": self.packet_id, "import_status": self.status}


def project_packet(packet):
    """Explicit premises form AND edges; each goal-linked candidate is an OR route."""
    if type(packet) is not DecisionPacket:
        raise TypeError("expected DecisionPacket")
    packet_id, nodes, edges, index = packet.id, [], [], {}

    def ref(block, name):
        return packet_id + "#/" + block + "/" + name.replace("~", "~0").replace("/", "~1")

    for block in ("goal", "evidence", "candidates"):
        for field in getattr(packet, block):
            source = ref(block, field.name)
            ident = "node:" + object_digest(source)
            index[(block, field.name)] = ident
            nodes.append(Node(ident, source))
    for field in packet.candidates:
        value, source = field.value, ref("candidates", field.name)
        candidate = index[("candidates", field.name)]
        premises = tuple(sorted(index[("evidence", p)] for p in value["premises"]))
        if premises:
            edges.append(Hyperedge("edge:" + object_digest([source, "premises"]), premises, candidate, source))
        if value["goal"] is not None:
            edges.append(Hyperedge("edge:" + object_digest([source, "goal"]), (candidate,),
                                   index[("goal", value["goal"])], source))
    status = next(f.value for f in packet.provenance if f.name == "status")
    return PacketGraph(packet_id, tuple(sorted(nodes, key=lambda n: n.id)),
                       tuple(sorted(edges, key=lambda e: e.id)), (index[("goal", "statement")],), status)
