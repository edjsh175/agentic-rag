"""统一的查询主体一致性保护机制（Entity Guard）。

识别当前问题中的显式技术实体，并在改写、扩展和图谱链接过程中保护它，
防止被历史上下文错误替换、修改或降级。
"""
from __future__ import annotations

import re

# 匹配技术实体名词的正则表达式。
# 这里匹配连续英文字符和数字，首字母为英文，支持带有点、下划线、中划线的标识符。
# 例如：UEModelBuilder, ModelBuilder, PipelineBuilder, Node.js, StampServer
_ENTITY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")

# 常见的技术性停用词，不作为显式实体保护
_STOPWORDS = {
    "how", "to", "the", "and", "for", "in", "on", "at", "by", "with", "a", "an", "of",
    "error", "exception", "warning", "info", "debug", "failed", "success", "timeout",
    "yes", "no", "get", "post", "set", "run", "config", "test", "build", "make",
    "using", "use", "with", "from", "into"
}

# 常见的全小写技术名词/工具名词
_KNOWN_TECH_WORDS = {
    "docker", "k8s", "kubernetes", "nginx", "mysql", "redis", "git", "linux", 
    "python", "java", "node", "pip", "npm", "yum", "apt", "brew", "webrtc"
}

_ENTITY_SUFFIXES = (
    "builder",
    "server",
    "tools",
    "webrtc",
    "api",
    "sdk",
)

def extract_explicit_entities(question: str) -> list[str]:
    """识别当前问题中的显式技术实体。
    
    采用规则过滤，排除非技术实体的常规英文词汇。
    要求实体要么包含至少一个大写字母，要么属于知名的全小写技术名词。
    并处理重叠/包含子串的实体，当它们独立出现时均保留，否则仅保留长实体。
    """
    if not question:
        return []
    
    # 1. 提取所有符合条件的英文/拉丁词
    candidates = _ENTITY_PATTERN.findall(question)
    raw_entities = []
    for cand in candidates:
        cand_clean = cand.strip(".-_")
        if len(cand_clean) >= 2 and cand_clean.lower() not in _STOPWORDS:
            # 只有包含大写字母，或者是已知的 lowercase 技术词，才被视为技术实体
            is_uppercase = any(c.isupper() for c in cand_clean)
            lower_name = cand_clean.casefold()
            is_known_tech = lower_name in _KNOWN_TECH_WORDS
            has_entity_suffix = lower_name.endswith(_ENTITY_SUFFIXES)
            if is_uppercase or is_known_tech or has_entity_suffix:
                raw_entities.append(cand_clean)
            
    # 去重并保留顺序
    seen = set()
    deduped = []
    for e in raw_entities:
        e_lower = e.lower()
        if e_lower not in seen:
            seen.add(e_lower)
            deduped.append(e)
            
    # 2. 找到每个实体在原问题中的所有起始和结束索引（casefold）
    q_cf = question.casefold()
    entity_spans = {}
    for ent in deduped:
        ent_cf = ent.casefold()
        spans = []
        start = 0
        while True:
            idx = q_cf.find(ent_cf, start)
            if idx == -1:
                break
            spans.append((idx, idx + len(ent_cf)))
            start = idx + 1
        entity_spans[ent] = spans
        
    # 3. 过滤掉完全被更长实体覆盖的实体（即没有独立出现的短实体）
    filtered_entities = []
    for ent in deduped:
        spans = entity_spans[ent]
        has_independent_occurrence = False
        for s_start, s_end in spans:
            enclosed = False
            for other_ent in deduped:
                if len(other_ent) > len(ent):
                    for o_start, o_end in entity_spans[other_ent]:
                        if o_start <= s_start and s_end <= o_end:
                            enclosed = True
                            break
                    if enclosed:
                        break
            if not enclosed:
                has_independent_occurrence = True
                break
        if has_independent_occurrence:
            filtered_entities.append(ent)
            
    return filtered_entities

def _filter_substrings(entities: list[str]) -> list[str]:
    """通用的过滤子串实体辅助函数，保留最长、最具体的实体。"""
    # 按长度降序排列
    sorted_ents = sorted(list(set(entities)), key=len, reverse=True)
    kept: list[str] = []
    for ent in sorted_ents:
        ent_lower = ent.lower()
        is_sub = False
        for k in kept:
            k_lower = k.lower()
            if ent_lower != k_lower and ent_lower in k_lower:
                is_sub = True
                break
        if not is_sub:
            kept.append(ent)
            
    # 按照原始列表中的相对顺序返回
    return [e for e in entities if e in kept]

def _text_covers_entity(
    text: str,
    entity: str,
    canonical_by_alias: dict[str, str] | None = None,
) -> bool:
    """True if text contains entity or any alias/canonical of the same backbone name."""
    if not text or not entity:
        return False
    text_cf = text.casefold()
    entity_cf = entity.casefold()
    if entity_cf in text_cf:
        return True
    if not canonical_by_alias:
        return False
    canonical = canonical_by_alias.get(entity) or canonical_by_alias.get(entity.strip()) or entity
    forms = {entity, canonical}
    for alias, target in canonical_by_alias.items():
        if target == canonical or alias == canonical:
            forms.add(alias)
            forms.add(target)
    return any(form.casefold() in text_cf for form in forms if form and len(form) >= 2)


def protect_rewritten_query(
    original_question: str,
    rewritten_query: str,
    last_user_question: str = "",
    *,
    canonical_by_alias: dict[str, str] | None = None,
) -> str:
    """对 LLM 改写结果或启发式改写结果做实体一致性保护。
    
    如果当前问题包含显式实体，但改写后的问题丢失了该实体（例如被历史实体的缩写替换了），
    则将改写后问题中的对应部分替换回当前显式实体。
    对于多实体问题，若改写后任意显式实体缺失，则安全回退到原问题以防止信息丢失。

    canonical_by_alias：可选，主干 alias→canonical。有则允许「StampTools」被「StampGIS Tools」覆盖视为未丢失。
    """
    orig_entities = extract_explicit_entities(original_question)
    if not orig_entities:
        return rewritten_query
        
    rewritten_cf = rewritten_query.casefold()
    
    # 针对多显式实体的问题，如果缺失了任何一个，回退到 original_question
    if len(orig_entities) > 1:
        all_present = all(
            _text_covers_entity(rewritten_query, oe, canonical_by_alias)
            for oe in orig_entities
        )
        if all_present:
            return rewritten_query
        return original_question
            
    # 针对单显式实体的问题
    orig_ent = orig_entities[0]
    
    # 如果改写后已经包含该实体（或等价 canonical/alias），则无需替换
    if _text_covers_entity(rewritten_query, orig_ent, canonical_by_alias):
        return rewritten_query
        
    rewritten_entities = extract_explicit_entities(rewritten_query)
    last_entities = extract_explicit_entities(last_user_question)
    
    result = rewritten_query
    replaced = False
    
    # 遍历改写后的实体，检查是否属于子串匹配或上一轮被继承的历史实体
    for rew_ent in rewritten_entities:
        rew_ent_cf = rew_ent.casefold()
        is_sub = (rew_ent_cf in orig_ent.casefold()) or (orig_ent.casefold() in rew_ent_cf)
        is_last = any(rew_ent_cf == le.casefold() for le in last_entities)
        # 同一主干 canonical 下的别名替换（StampTools → StampGIS Tools）视为合法，不再回写表面词
        same_backbone = False
        if canonical_by_alias:
            left = canonical_by_alias.get(orig_ent) or canonical_by_alias.get(orig_ent.strip())
            right = canonical_by_alias.get(rew_ent) or canonical_by_alias.get(rew_ent.strip())
            if left and right and left == right:
                same_backbone = True
        
        if same_backbone:
            return rewritten_query
        if is_sub or is_last:
            # 执行大小写不敏感替换
            pattern = re.compile(re.escape(rew_ent), re.IGNORECASE)
            result = pattern.sub(orig_ent, result)
            replaced = True
            
    if not replaced:
        # 如果没有找到明显的替换目标，但显式实体确实丢失了
        if rewritten_entities:
            pattern = re.compile(re.escape(rewritten_entities[0]), re.IGNORECASE)
            result = pattern.sub(orig_ent, result)
        else:
            result = f"{orig_ent} {result}"
            
    return result

def protect_query_list(
    original_question: str,
    queries: list[str],
    last_user_question: str = "",
    *,
    canonical_by_alias: dict[str, str] | None = None,
) -> list[str]:
    """对多路检索/扩展检索查询列表进行实体一致性保护。"""
    return [
        protect_rewritten_query(
            original_question,
            q,
            last_user_question,
            canonical_by_alias=canonical_by_alias,
        )
        for q in queries
    ]

def filter_entity_candidates(
    original_question: str,
    candidate_entities: list[str],
) -> list[str]:
    """对候选实体列表做显式实体优先以及长实体优先过滤。
    
    如果原始问题存在显式实体，候选实体中与显式实体构成包含冲突的短实体（且短实体本身未独立显式出现）将被过滤丢弃。
    """
    orig_entities = extract_explicit_entities(original_question)
    if not orig_entities:
        return _filter_substrings(candidate_entities)
        
    filtered: list[str] = []
    orig_entities_cf = [oe.casefold() for oe in orig_entities]
    
    # 1. 过滤与显式实体有冲突但未独立在原问题中出现的候选实体
    for cand in candidate_entities:
        cand_cf = cand.casefold()
        if cand_cf in orig_entities_cf:
            filtered.append(cand)
            continue
            
        conflict = False
        for oe in orig_entities:
            oe_cf = oe.casefold()
            if (cand_cf in oe_cf) or (oe_cf in cand_cf):
                conflict = True
                break
        if not conflict:
            filtered.append(cand)
            
    # 2. 在保留的候选中过滤子串，但永远不丢弃显式存在于 orig_entities 中的实体
    sorted_ents = sorted(list(set(filtered)), key=len, reverse=True)
    kept: list[str] = []
    for ent in sorted_ents:
        ent_cf = ent.casefold()
        if ent_cf in orig_entities_cf:
            kept.append(ent)
            continue
            
        is_sub = False
        for k in kept:
            k_cf = k.casefold()
            if ent_cf != k_cf and ent_cf in k_cf:
                is_sub = True
                break
        if not is_sub:
            kept.append(ent)
            
    return [e for e in filtered if e in kept]
