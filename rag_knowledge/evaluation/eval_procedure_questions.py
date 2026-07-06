"""
流程型与操作型多意图自动评估集生成与评测工具
"""
import os
import re
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Set, Tuple

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.query_planner import QueryPlanner
from rag_knowledge.services.rag import RagChain

logger = logging.getLogger(__name__)

# 定义各意图模板
_TEMPLATES = {
    "procedure": [
        "如何使用 {entity} 进行发布？",
        "{entity} 的操作流程是什么？",
        "使用 {entity} 的完整步骤是什么？",
        "{entity} 如何配置和使用？"
    ],
    "deployment": [
        "如何部署 {entity}？",
        "{entity} 的部署步骤是什么？",
        "{entity} 如何安装和启动？"
    ],
    "config": [
        "{entity} 的工程设置包括什么？",
        "{entity} 有哪些配置项？",
        "{entity} 的参数配置是什么？"
    ],
    "troubleshooting": [
        "{entity} 报错如何排查？",
        "{entity} 编译失败怎么处理？",
        "{entity} 常见问题有哪些？"
    ],
    "definition": [
        "什么是 {entity}？",
        "{entity} 的功能是什么？",
        "{entity} 是什么工具？"
    ],
    "comparison": [
        "{entity} 和 {entity2} 有什么区别？",
        "{entity} 和 {entity2} 分别适用于什么场景？"
    ]
}

# 默认实体兜底列表，匹配 GIS / 服务端环境
_FALLBACK_ENTITIES = [
    "DOMBuilder",
    "StampServer",
    "StampTools",
    "StampWebRTC",
    "WebRTC",
    "TongWeb",
    "MySQL",
    "rockyLinux9",
    "DEMBuilder",
    "TINBuilder"
]


def extract_entities_from_vector_store() -> List[Tuple[str, int]]:
    """从向量数据库中提取高频实体。"""
    try:
        vs = VectorStore()
        source_data = vs.get_chunk_stats_source()
        metadatas = source_data.get("metadatas") or []
    except Exception as exc:
        logger.warning("从数据库提取实体失败: %s，使用兜底实体列表", exc)
        return [(e, 10) for e in _FALLBACK_ENTITIES]

    entity_counts = {}
    
    # 规则1: 匹配常见英文实体名
    pattern = re.compile(r"\b[A-Za-z0-9_]{3,}\b")
    
    # 过滤词
    stopwords = {
        "user", "manual", "docx", "doc", "pdf", "txt", "md", "rocky", "rocky9", "linux", 
        "windows", "approved", "pending", "status", "category", "source", "title", "text"
    }

    for meta in metadatas:
        source_name = meta.get("source") or ""
        section_title = meta.get("section_title") or ""
        
        # 移除哈希前缀和扩展名
        clean_source = re.sub(r"^[a-fA-F0-9]{6,}-", "", source_name)
        clean_source = clean_source.rsplit(".", 1)[0] if "." in clean_source else clean_source
        
        # 提取 source 中的词
        for word in pattern.findall(clean_source):
            w_lower = word.lower()
            if w_lower not in stopwords and len(word) >= 3:
                # 规范化大小写，比如 StampTools, DOMBuilder, WebRTC
                entity_counts[word] = entity_counts.get(word, 0) + 1
                
        # 提取 section_title 中的 CamelCase 词
        for word in pattern.findall(section_title):
            w_lower = word.lower()
            if w_lower not in stopwords and len(word) >= 3:
                # 只保留非纯数字/小写词，优先保留混合大小写 CamelCase
                if not word.islower() and not word.isupper() and not word.isdigit():
                    entity_counts[word] = entity_counts.get(word, 0) + 1

    # 如果提取出的实体太少，使用兜底列表补充
    sorted_entities = sorted(entity_counts.items(), key=lambda x: x[1], reverse=True)
    extracted = [e[0] for e in sorted_entities]
    
    for fallback in _FALLBACK_ENTITIES:
        if fallback not in extracted:
            sorted_entities.append((fallback, 3))
            
    # 去重且保持顺序
    seen = set()
    result = []
    for ent, count in sorted_entities:
        ent_lower = ent.lower()
        if ent_lower not in seen:
            seen.add(ent_lower)
            result.append((ent, count))
            
    return result[:15]


def generate_procedure_eval_questions(
    max_entities: int = 10,
    templates_per_intent: int = 2
) -> List[Dict]:
    """根据提取的高频实体与模板，生成多意图评测问答集。"""
    entities_with_counts = extract_entities_from_vector_store()
    entities = [e[0] for e in entities_with_counts[:max_entities]]
    
    questions = []
    seen_questions = set()
    
    for idx, entity in enumerate(entities):
        # 1. 处理单实体意图: procedure, deployment, config, troubleshooting, definition
        for intent in ["procedure", "deployment", "config", "troubleshooting", "definition"]:
            templates = _TEMPLATES[intent][:templates_per_intent]
            for temp in templates:
                q_text = temp.format(entity=entity)
                if q_text not in seen_questions:
                    seen_questions.add(q_text)
                    questions.append({
                        "question": q_text,
                        "expected_intent": intent,
                        "entity": entity,
                        "template": temp
                    })
                    
        # 2. 处理双实体意图: comparison (将当前实体与下一个实体对比)
        next_entity = entities[(idx + 1) % len(entities)]
        if entity.lower() != next_entity.lower():
            for temp in _TEMPLATES["comparison"][:templates_per_intent]:
                q_text = temp.format(entity=entity, entity2=next_entity)
                if q_text not in seen_questions:
                    seen_questions.add(q_text)
                    questions.append({
                        "question": q_text,
                        "expected_intent": "comparison",
                        "entity": entity,
                        "template": temp,
                        "entity2": next_entity
                    })
                    
    return questions


def run_procedure_eval(
    questions: List[Dict] = None,
    output_path: str | Path = "./data/eval_procedure_results.json",
    verbose: bool = True
) -> Dict:
    """运行流程型问答系统多维度意图与检索评估。"""
    if not questions:
        questions = generate_procedure_eval_questions()
        
    planner = QueryPlanner()
    rag = RagChain()
    
    total_questions = len(questions)
    correct_intents = 0
    total_retrieval_hits = 0
    total_latency = 0.0
    
    by_intent_metrics = {}
    by_entity_metrics = {}
    
    detailed_results = []
    
    print(f"\n开始评估，共 {total_questions} 个测试问题...")
    
    for i, item in enumerate(questions):
        question = item["question"]
        expected_intent = item["expected_intent"]
        entity = item["entity"]
        
        # 初始化统计
        if expected_intent not in by_intent_metrics:
            by_intent_metrics[expected_intent] = {"total": 0, "correct_intent": 0, "hits": 0}
        if entity not in by_entity_metrics:
            by_entity_metrics[entity] = {"total": 0, "hits": 0}
            
        by_intent_metrics[expected_intent]["total"] += 1
        by_entity_metrics[entity]["total"] += 1
        
        # 1. 评测意图分类（不依赖LLM网络请求的启发式函数，提高测试速度与稳定性）
        t0 = time.time()
        try:
            classified_intent, _ = planner._classify_heuristic(question)
        except Exception:
            classified_intent = "definition"
            
        is_intent_correct = (classified_intent == expected_intent)
        if is_intent_correct:
            correct_intents += 1
            by_intent_metrics[expected_intent]["correct_intent"] += 1
            
        # 2. 评测文档召回率
        retrieval_start = time.time()
        hit = False
        source_docs = []
        try:
            # 模拟检索层调用
            plan = planner.plan(question)
            source_docs, _ = rag._retrieve_multi(
                plan.queries,
                plan_top_k=plan.top_k,
                plan_candidate_k=plan.candidate_k,
                expand_neighbors=plan.expand_neighbors
            )
            
            # 判断检索到的文档是否包含实体关键字 (不区分大小写)
            for doc in source_docs:
                doc_content = doc.get("content", "").lower()
                doc_source = doc.get("metadata", {}).get("source", "").lower()
                doc_title = doc.get("metadata", {}).get("section_title", "").lower()
                
                ent_lower = entity.lower()
                # 如果文档内容、标题或文件名中包含实体名，则认为召回正确
                if ent_lower in doc_content or ent_lower in doc_source or ent_lower in doc_title:
                    hit = True
                    break
        except Exception as exc:
            logger.warning("问题 '%s' 检索阶段失败: %s", question, exc)
            
        latency = time.time() - t0
        total_latency += latency
        
        if hit:
            total_retrieval_hits += 1
            by_intent_metrics[expected_intent]["hits"] += 1
            by_entity_metrics[entity]["hits"] += 1
            
        detailed_results.append({
            "question": question,
            "expected_intent": expected_intent,
            "classified_intent": classified_intent,
            "is_intent_correct": is_intent_correct,
            "entity": entity,
            "retrieved_count": len(source_docs),
            "hit_entity_doc": hit,
            "latency_seconds": round(latency, 3)
        })
        
        if verbose and (i + 1) % 10 == 0:
            print(f"进度: {i + 1}/{total_questions} | 当前意图分类准确率: {correct_intents / (i + 1):.2%} | 召回率: {total_retrieval_hits / (i + 1):.2%}")
            
    # 汇总结果
    intent_accuracy = correct_intents / total_questions if total_questions else 0.0
    overall_hit_rate = total_retrieval_hits / total_questions if total_questions else 0.0
    avg_latency = total_latency / total_questions if total_questions else 0.0
    
    # 格式化各个维度的统计数据
    by_intent_summary = {}
    for intent, stat in by_intent_metrics.items():
        by_intent_summary[intent] = {
            "total": stat["total"],
            "intent_accuracy": round(stat["correct_intent"] / stat["total"], 4) if stat["total"] else 0.0,
            "hit_rate": round(stat["hits"] / stat["total"], 4) if stat["total"] else 0.0
        }
        
    by_entity_summary = {}
    for ent, stat in by_entity_metrics.items():
        by_entity_summary[ent] = {
            "total": stat["total"],
            "hit_rate": round(stat["hits"] / stat["total"], 4) if stat["total"] else 0.0
        }
        
    summary = {
        "total_questions": total_questions,
        "overall_intent_accuracy": round(intent_accuracy, 4),
        "overall_retrieval_hit_rate": round(overall_hit_rate, 4),
        "average_latency_seconds": round(avg_latency, 3),
        "by_intent": by_intent_summary,
        "by_entity": by_entity_summary,
        "detailed_questions": detailed_results
    }
    
    # 写入结果文件
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        
    print(f"\n评估完成！结果已存入 {output_path}")
    print(f"总体意图分类准确率: {intent_accuracy:.2%}")
    print(f"总体检索实体召回率: {overall_hit_rate:.2%}")
    print(f"平均处理延迟: {avg_latency:.3f}秒")
    
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="多意图流程型评估集评测")
    parser.add_argument("--run", action="store_true", help="是否运行评估流程")
    parser.add_argument("--entities", type=int, default=8, help="评测用高频实体数量上限")
    args = parser.parse_args()
    
    questions = generate_procedure_eval_questions(max_entities=args.entities)
    print(f"成功生成评测问答集，实体数量: {args.entities}，总问题数: {len(questions)}")
    
    # 打印前5个生成的样本问题
    print("\n样本问题：")
    for q in questions[:5]:
        print(f"  [{q['expected_intent']}] -> {q['question']}")
        
    if args.run:
        run_procedure_eval(questions)
