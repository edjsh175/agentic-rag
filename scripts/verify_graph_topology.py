"""
Knowledge Graph Topology & Health Baseline Diagnostic Script.

Performs read-only analytics on the SQLite relational database to extract:
1. Product Backbone Breakdown (Product, Tool, Service, FunctionArea, etc.)
2. Tool Direct Belongs_To Distribution (Exposure of direct child entity types)
3. FunctionArea Analysis & Branch Factor (Child count & noisy/pure label exposure)
4. Path Depth Distribution (Tool -> Leaf min, max, avg depth)
5. Orphan/Floating Entities (Entities missing structural edges & evidence links)
6. Evidence Link Coverage (derived_from / entity_chunk_links ratio)
"""
import os
import sys
import sqlite3
import re
from collections import defaultdict, deque
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rag_knowledge.config import Config


def connect_db_readonly(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        print(f"[ERROR] Database file not found at: {db_path}")
        sys.exit(1)
    
    # SQLite read-only URI connection
    conn_uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(conn_uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def is_suspicious_noise(name: str) -> bool:
    """Identify potential noise without strict entity_type blacklisting."""
    # Pure numbers / port numbers (e.g. 6379, 8080)
    if re.match(r'^\d{2,5}$', name):
        return True
    # HTTP status codes or error numbers alone
    if re.match(r'^(err_|code_)?\d{3}$', name, re.IGNORECASE):
        return True
    # Localhost / IP addresses
    if re.match(r'^(localhost|127\.0\.0\.1|0\.0\.0\.1|https?://)', name, re.IGNORECASE):
        return True
    # Single character or pure symbols
    if len(name.strip()) <= 1:
        return True
    return False


def run_diagnostics():
    cfg = Config()
    db_path = cfg.relational_db_path
    report_path = PROJECT_ROOT / "data" / "graph_topology_report.md"
    report_lines = []
    
    def log(text: str = ""):
        print(text)
        report_lines.append(text)

    log("=" * 80)
    log(" GRAPH TOPOLOGY & HEALTH BASELINE DIAGNOSTIC")
    log(f" Target DB: {db_path}")
    log("=" * 80)

    conn = connect_db_readonly(db_path)
    
    # -------------------------------------------------------------------------
    # 1. Backbone & Overview
    # -------------------------------------------------------------------------
    log("\n--- 1. Backbone & Entity Overview ---")
    entities = conn.execute("SELECT id, name, entity_type, review_status FROM entities").fetchall()
    relations = conn.execute("SELECT id, source_entity_id, target_entity_id, relation_type FROM relations").fetchall()
    chunk_links = conn.execute("SELECT id, entity_id, chunk_id, link_type FROM entity_chunk_links").fetchall()

    log(f"Total Entities: {len(entities)}")
    log(f"Total Relations: {len(relations)}")
    log(f"Total Chunk Links: {len(chunk_links)}")

    type_counts = defaultdict(int)
    for e in entities:
        type_counts[e["entity_type"]] += 1
    
    log("\nEntity Count by Type:")
    for etype, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
        log(f"  - {etype:<20}: {count}")

    rel_type_counts = defaultdict(int)
    for r in relations:
        rel_type_counts[r["relation_type"]] += 1
    
    log("\nRelation Count by Type:")
    for rtype, count in sorted(rel_type_counts.items(), key=lambda x: x[1], reverse=True):
        log(f"  - {rtype:<20}: {count}")

    # Build graph mappings for traversal
    id2entity = {e["id"]: dict(e) for e in entities}
    name2id = {e["name"]: e["id"] for e in entities}
    
    # Parent-child adjacency list (child -> list of parents, parent -> list of children)
    children_of = defaultdict(list)
    parents_of = defaultdict(list)
    
    for r in relations:
        src = r["source_entity_id"]
        tgt = r["target_entity_id"]
        rtype = r["relation_type"]
        if rtype in ("belongs_to", "has_table", "uses_config", "has_procedure", "has_step", "defined_in"):
            # src belongs_to tgt OR tgt has_table/uses_config/has_procedure src
            if rtype == "belongs_to":
                # source is child, target is parent
                children_of[tgt].append((src, rtype))
                parents_of[src].append((tgt, rtype))
            else:
                # source is parent, target is child
                children_of[src].append((tgt, rtype))
                parents_of[tgt].append((src, rtype))

    # -------------------------------------------------------------------------
    # 2. Tool Direct Belongs_To / Children Distribution
    # -------------------------------------------------------------------------
    print("\n--- 2. Tool Direct Children Distribution ---")
    tools = [e for e in entities if e["entity_type"] in ("Tool", "Service", "Product")]
    
    if not tools:
        print("No Tool/Service/Product entities found.")
    else:
        for t in tools:
            tid = t["id"]
            tname = t["name"]
            ttype = t["entity_type"]
            direct_children = children_of[tid]
            
            print(f"\n[Tool/Service/Product] {tname} ({ttype}) -> Direct Children Count: {len(direct_children)}")
            child_type_dist = defaultdict(list)
            for cid, rtype in direct_children:
                centity = id2entity.get(cid)
                if centity:
                    child_type_dist[centity["entity_type"]].append(centity["name"])
            
            for ctype, names in sorted(child_type_dist.items(), key=lambda x: len(x[1]), reverse=True):
                sample = names[:5]
                more_suffix = f" ... and {len(names)-5} more" if len(names) > 5 else ""
                print(f"  * {ctype:<15}: {len(names):>3} nodes | Sample: {sample}{more_suffix}")

    # -------------------------------------------------------------------------
    # 3. FunctionArea Analysis & Branch Factor
    # -------------------------------------------------------------------------
    print("\n--- 3. FunctionArea Analysis & Branch Factor ---")
    fa_entities = [e for e in entities if e["entity_type"] == "FunctionArea"]
    print(f"Total FunctionArea Nodes: {len(fa_entities)}")

    for fa in fa_entities:
        fa_id = fa["id"]
        fa_name = fa["name"]
        direct_children = children_of[fa_id]
        branch_factor = len(direct_children)
        
        print(f"\n[FunctionArea] '{fa_name}' -> Branch Factor (Direct Children): {branch_factor}")
        
        child_type_dist = defaultdict(list)
        noisy_names = []
        
        for cid, rtype in direct_children:
            centity = id2entity.get(cid)
            if centity:
                cname = centity["name"]
                ctype = centity["entity_type"]
                child_type_dist[ctype].append(cname)
                if is_suspicious_noise(cname):
                    noisy_names.append(f"{cname} ({ctype})")
        
        for ctype, names in sorted(child_type_dist.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  * {ctype:<15}: {len(names):>3} nodes")
            
        if noisy_names:
            print(f"  [!] Potential Noise/Non-Business Labels Detected ({len(noisy_names)}): {noisy_names[:8]}")
        
        if branch_factor > 200:
            print(f"  [WARNING] High Branching Factor (>200)! Risk of Node Explosion.")

    # -------------------------------------------------------------------------
    # 4. Path Depth Distribution
    # -------------------------------------------------------------------------
    print("\n--- 4. Path Depth Distribution (Tool -> Leaf) ---")
    root_nodes = [e for e in entities if e["entity_type"] in ("Product", "Tool", "Service")]
    
    if root_nodes:
        all_depths = []
        for rnode in root_nodes:
            rid = rnode["id"]
            rname = rnode["name"]
            
            # BFS to find max/min depth to leaves
            queue = deque([(rid, 0)])
            visited = {rid}
            leaf_depths = []
            
            while queue:
                curr_id, depth = queue.popleft()
                c_list = children_of[curr_id]
                unvisited_children = [cid for cid, _ in c_list if cid not in visited]
                
                if not unvisited_children:
                    # Leaf node reached
                    if depth > 0:
                        leaf_depths.append(depth)
                else:
                    for cid in unvisited_children:
                        visited.add(cid)
                        queue.append((cid, depth + 1))
            
            if leaf_depths:
                min_d = min(leaf_depths)
                max_d = max(leaf_depths)
                avg_d = sum(leaf_depths) / len(leaf_depths)
                all_depths.extend(leaf_depths)
                print(f"Root '{rname}' ({rnode['entity_type']}): Min Depth = {min_d}, Max Depth = {max_d}, Avg Depth = {avg_d:.2f}")
            else:
                print(f"Root '{rname}' ({rnode['entity_type']}): No downstream leaves found.")

        if all_depths:
            print(f"\nOverall Graph Depth Summary: Min = {min(all_depths)}, Max = {max(all_depths)}, Avg = {sum(all_depths)/len(all_depths):.2f}")

    # -------------------------------------------------------------------------
    # 5. Orphan / Floating Entities
    # -------------------------------------------------------------------------
    print("\n--- 5. Orphan / Floating Entities ---")
    linked_entity_ids = set()
    for r in relations:
        linked_entity_ids.add(r["source_entity_id"])
        linked_entity_ids.add(r["target_entity_id"])

    evidence_linked_ids = {cl["entity_id"] for cl in chunk_links}

    orphan_entities = []
    for e in entities:
        eid = e["id"]
        has_relation = eid in linked_entity_ids
        has_evidence = eid in evidence_linked_ids
        if not has_relation and not has_evidence:
            orphan_entities.append(e)

    print(f"Total Orphan Entities (No relations AND no chunk evidence): {len(orphan_entities)} / {len(entities)} ({len(orphan_entities)/max(1, len(entities))*100:.1f}%)")
    if orphan_entities:
        print("Sample Orphan Entities:")
        for oe in orphan_entities[:10]:
            print(f"  - [{oe['entity_type']}] {oe['name']} (ID: {oe['id']})")

    # -------------------------------------------------------------------------
    # 6. Entity Source Evidence (derived_from / Chunk Links) Coverage
    # -------------------------------------------------------------------------
    print("\n--- 6. Entity Source Evidence Coverage ---")
    total_approved_entities = len([e for e in entities if e["review_status"] == "approved"])
    entities_with_chunk = len(evidence_linked_ids)
    
    print(f"Entities with Chunk Evidence Link: {entities_with_chunk} / {len(entities)} ({entities_with_chunk / max(1, len(entities))*100:.1f}%)")
    print(f"Approved Entities Coverage Ratio: {entities_with_chunk / max(1, total_approved_entities)*100:.1f}%")

    log("\n" + "=" * 80)
    log(" DIAGNOSTIC COMPLETE - HEALTH BASELINE EXPOSED")
    log("=" * 80)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"\n[REPORT SAVED] Report generated at: {report_path}")


if __name__ == "__main__":
    run_diagnostics()
