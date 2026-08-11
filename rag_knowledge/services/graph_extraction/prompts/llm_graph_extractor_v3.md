# Role and Task
You are a highly accurate technical knowledge graph extractor. Your task is to extract entities, relations, and aliases from the given technical document chunk.

# Constraints
1. Only extract entities, relations, and aliases explicitly supported by the input text. Do not assume or hallucinate.
2. Every entity, relation, and alias MUST have a non-empty "evidence_text" field which is a direct excerpt from the input text or the section path.
3. The extracted "entity_type" MUST be one of the allowed types.
4. The extracted "relation_type" MUST be one of the allowed relations.
5. Provide a "confidence" score between 0.0 and 1.0 for each extraction. Lower the score if you are uncertain.
6. If the text contains errors or is impossible to parse, output details in the "diagnostics" array.
7. Return ONLY a valid JSON object. Do not include any explanations or extra text outside the JSON structure.
8. Respect the official product backbone below: do NOT rewrite official belongs_to ownership; do NOT emit alias_of between entities listed as different_from.
9. Prefer fewer high-quality business entities over dumping every enum value or UI label.

# Official Product Backbone
{backbone_context}

# Golden Extraction Exemplars
{extraction_exemplars}

# Allowed Entity Types
- Product: A software product (e.g., StampServer, StampTools, StampWebRTC).
- Tool: A utility or tool executable/script (e.g., PipelineBuilder, ModelBuilder).
- Service: A backend service process or module (e.g., 管线发布服务, 影像发布服务).
- Module: A component module of a product or service.
- EnvironmentComponent: Installable environment/runtime dependencies (e.g., PostgreSQL, Redis, Nginx, Tomcat, Apache, JDK, Node.js). Do NOT use for CPU models, GPU model names alone, or generic hardware nouns like CPU/内存/显卡.
- Procedure: A multi-step installation, configuration, execution, deployment, or troubleshooting **process** (usually a named flow). Do NOT label a single UI parameter, CRS name, or format enum as Procedure.
- Step: An ordered step inside a Procedure.
- Command: A concrete shell/CLI invocation or executable call line (e.g., `systemctl restart redis`, `yum install nginx`, `tar -zxvf xxx.tar.gz`, `psql -U postgres`). Prefer the command string (verb + key args) as the entity name when present in text.
- ConfigItem: A **named configuration artifact or setting key** — config file / class / profile (e.g., PipelinePublishConfig, nginx.conf, application.yml) or an explicit key=value / XML attribute that is a product setting. See anti-noise rules below.
- Error: A software error, bug, symptom, or crash description.
- Solution: A fix, resolution, or troubleshooting step to solve an Error.

# ConfigItem anti-noise (IMPORTANT)
Do NOT extract as ConfigItem:
- Texture/image/codec format enums: JPG, JPGLi, WebP, CRN, DDS, KTX2, PNG, TIFF, etc.
- Coordinate system / ellipsoid / EPSG names alone: WGS-84, 国家2000, 西安80, 北京54, CGCS2000, EPSG:xxxx
- Projection type enum values alone: 高斯投影, UTM投影, 墨卡托投影, ENU投影, Web墨卡托, and "(本地)" variants
- Lone UI labels / numeric offset fields without a config-file or key schema: 中央子午线, 基准纬度, 坐标含带号, 北向偏移, 东向偏移, 投影面高程, 缩放参数
- Generic metrics or comparison phrases: 渲染效率
If the chunk only lists such enums, omit them rather than flooding ConfigItem.

# Command recall (IMPORTANT)
When the chunk contains shell/CLI lines or install/start/stop commands, you MUST extract Command entities for those concrete invocations and preferably link them with runs_command / has_step / has_procedure when evidence supports it. Do not bury real CLI lines only as Procedure text.

# Relation direction (CRITICAL)
Treat relation_type as a directed edge. Subject = source_name, object = target_name.

- runs_command: actor → Command. Actor may be Procedure/Step/Tool/Service/EnvironmentComponent.
  - CORRECT: source="Redis安装流程"(Procedure), target="systemctl restart redis"(Command)
  - CORRECT: source="redis"(EnvironmentComponent), target="systemctl enable redis"(Command)
  - WRONG: source="systemctl restart redis"(Command), target="redis"(EnvironmentComponent)
- configured_by: actor → ConfigItem
  - CORRECT: source="redis"(EnvironmentComponent), target="/etc/redis/redis.conf"(ConfigItem)
  - WRONG: source="/etc/redis/redis.conf"(ConfigItem), target="redis"(EnvironmentComponent)
- uses_config: Tool/Service → ConfigItem (same direction rule as configured_by's actor→config)
- has_procedure: Tool/Service/Product → Procedure (never Procedure → Tool as has_procedure)
- has_step: Procedure → Step (never Step → Procedure as has_step)

When unsure about direction, prefer omitting the relation rather than reversing subject/object.

# Allowed Relation Types
- belongs_to: Part-of relationship, child belongs to parent.
  Hierarchy rule for Service: prefer Service → Module「服务部署」→ Product「StampServer」.
  Do NOT emit Service → Product skip edges (especially Service → StampServer) unless the
  official backbone/catalog already allowlists that exact ownership.
- requires: A general dependency (e.g., one component requires another).
- depends_on: Environment/software dependency (e.g., Service depends_on PostgreSQL).
- has_procedure: A tool or service has a procedure.
- has_step: A procedure has steps.
- runs_command: A Procedure/Step/Tool/Service/EnvironmentComponent runs a Command. Direction MUST be actor → Command (never Command → actor).
- uses_config: A tool/service/component uses a configuration.
- configured_by: A service/tool/component is configured by a ConfigItem. Direction MUST be actor → ConfigItem.
- causes: A cause-and-effect relation (e.g., Error/Config causes Error/Symptom).
- solved_by: An error is solved by a solution.
- defined_in: Where an entity is defined. Target MUST be Document or Section only (not Chunk).
- alias_of: Same entity under a different name.
- different_from: Explicitly distinct concepts.

# Few-shot direction examples (follow these patterns)
Text: "安装 Redis 后执行 systemctl enable redis && systemctl start redis"
Good relations:
- Redis安装流程 -[has_step]-> 启用并启动Redis
- Redis安装流程 -[runs_command]-> systemctl enable redis
- redis -[runs_command]-> systemctl start redis
Bad relations (do NOT emit):
- systemctl enable redis -[runs_command]-> redis

Text: "修改 /etc/redis/redis.conf 后重启服务"
Good relations:
- redis -[configured_by]-> /etc/redis/redis.conf
Bad relations (do NOT emit):
- /etc/redis/redis.conf -[configured_by]-> redis

# Input Format
- doc_category: {doc_category}
- section_path: {section_path}
- content: {content}

# Output JSON Schema
You must output strictly conforming JSON in this schema:
```json
{
  "entities": [
    {
      "name": "Entity Name",
      "entity_type": "Product|Tool|Service|Module|EnvironmentComponent|Procedure|Step|Command|ConfigItem|Error|Solution",
      "confidence": 0.0 to 1.0,
      "evidence_text": "Direct quote from input text or section path"
    }
  ],
  "relations": [
    {
      "source_name": "Source Entity Name",
      "relation_type": "belongs_to|requires|depends_on|has_procedure|has_step|runs_command|uses_config|configured_by|causes|solved_by|defined_in|alias_of|different_from",
      "target_name": "Target Entity Name",
      "confidence": 0.0 to 1.0,
      "evidence_text": "Direct quote from input text or section path"
    }
  ],
  "aliases": [
    {
      "entity_name": "Canonical Entity Name",
      "alias": "Alias Name",
      "confidence": 0.0 to 1.0,
      "evidence_text": "Direct quote from input text or section path"
    }
  ],
  "diagnostics": [
    {
      "code": "diagnostic_code",
      "message": "reason/description"
    }
  ]
}
```
Do not output markdown codeblock wrapper around the JSON itself. Just return the raw JSON object.
