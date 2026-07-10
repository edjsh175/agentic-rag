from rag_knowledge.services.graph_extraction import EntityCandidate, CandidateNormalizer


def test_normalizer_merges_batch_duplicates_and_preserves_evidence():
    candidates = [
        EntityCandidate(" PostgreSQL（数据库） ", "EnvironmentComponent", evidence_text="PostgreSQL"),
        EntityCandidate("PostgreSQL（数据库）", "EnvironmentComponent", evidence_text="postgresql-16"),
    ]

    result = CandidateNormalizer().normalize_entities(candidates)

    assert len(result) == 1
    assert result[0].name == "PostgreSQL(数据库)"
    assert {item["evidence_text"] for item in result[0].properties["evidences"]} == {
        "PostgreSQL",
        "postgresql-16",
    }


def test_normalizer_matches_catalog_alias_and_fingerprint_is_stable():
    class Catalog:
        def resolve(self, name):
            return ("PostgreSQL", "EnvironmentComponent") if name.lower() in {"postgres", "postgresql"} else None

    normalizer = CandidateNormalizer(catalog=Catalog())
    first = normalizer.normalize_entities([EntityCandidate(" Postgres ", "EnvironmentComponent")])[0]
    second = normalizer.normalize_entities([EntityCandidate("PostgreSQL", "EnvironmentComponent")])[0]

    assert first.name == second.name == "PostgreSQL"
    assert first.properties["fingerprint"] == second.properties["fingerprint"]
