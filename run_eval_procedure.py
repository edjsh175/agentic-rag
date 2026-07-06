"""
运行流程型多意图评测并输出汇总对比表
"""
import sys
sys.path.insert(0, ".")

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from rag_knowledge.evaluation.eval_procedure_questions import (
    generate_procedure_eval_questions,
    run_procedure_eval
)

# 1. 生成评估集
print("\n" + "=" * 60)
print(" 步骤 1: 自动提取知识库高频实体，生成多意图评测集")
print("=" * 60 + "\n")

questions = generate_procedure_eval_questions(max_entities=8, templates_per_intent=2)
print(f"生成的测试集规模: {len(questions)} 个问题")

# 2. 运行检索与意图识别评估
print("\n" + "=" * 60)
print(" 步骤 2: 运行意图识别与相邻文档召回率联合评测")
print("=" * 60 + "\n")

results = run_procedure_eval(questions, verbose=True)

# 3. 输出漂亮的控制台对比汇总表
print("\n" + "=" * 60)
print(" 意图分类与检索召回率汇总表 (按意图划分)")
print("=" * 60)

print("\n┌─────────────────┬──────────┬──────────────────────┬──────────────────────┐")
print("│    问题意图     │  总题数  │   意图分类准确率     │    检索实体召回率    │")
print("├─────────────────┼──────────┼──────────────────────┼──────────────────────┤")

for intent, metrics in results["by_intent"].items():
    total = metrics["total"]
    accuracy = metrics["intent_accuracy"]
    hit_rate = metrics["hit_rate"]
    print(f"│ {intent:<15} │ {total:>8} │ {accuracy:>20.2%} │ {hit_rate:>20.2%} │")

print("└─────────────────┴──────────┴──────────────────────┴──────────────────────┘")


print("\n" + "=" * 60)
print(" 实体文档检索召回率汇总表 (按实体划分)")
print("=" * 60)

print("\n┌──────────────────────────────┬──────────┬──────────────────────┐")
print("│           评估实体           │  总题数  │    检索实体召回率    │")
print("├──────────────────────────────┼──────────┼──────────────────────┤")

for entity, metrics in results["by_entity"].items():
    total = metrics["total"]
    hit_rate = metrics["hit_rate"]
    print(f"│ {entity:<28} │ {total:>8} │ {hit_rate:>20.2%} │")

print("└──────────────────────────────┴──────────┴──────────────────────┘")

print(f"\n评估成功结束！")
print(f"总体意图识别准确率: {results['overall_intent_accuracy']:.2%}")
print(f"总体检索实体召回率: {results['overall_retrieval_hit_rate']:.2%}")
print(f"平均响应时间: {results['average_latency_seconds']:.3f} 秒")
print("详细测试用例与评测日志见: ./data/eval_procedure_results.json\n")
