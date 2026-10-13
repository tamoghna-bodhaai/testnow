"""Normalise duplicate Mathematics chapter names.

Revision ID: 0009_normalize_mathematics_taxonomy
Revises: 0008_seed_question_identity
"""
from alembic import op
import sqlalchemy as sa
import re


revision = "0009_normalize_mathematics_taxonomy"
down_revision = "0008_seed_question_identity"
branch_labels = None
depends_on = None


ALIASES = {
    "permutation and combination": "Permutations & Combinations",
    "permutations combinations": "Permutations & Combinations",
    "quadratic equation": "Quadratic Equations",
    "sequence series": "Sequence and Series",
    "complex number": "Complex Numbers",
    "conic sections": "Conic Section",
    "straight lines": "Straight Line",
    "differential equation": "Differential Equations",
    "integral calculus 1": "Integral Calculus-1",
    "integral calculus 2": "Integral Calculus-2",
    "differential calculus 1": "Differential Calculus-1",
    "differential calculus 2": "Differential Calculus-2",
    "vectors": "Vector",
    "matrices and determinants": "Matrices & Determinants",
}


def compact(value):
    value = re.sub(r"\b([A-Za-z])\s+(?=[A-Za-z]{2,}\b)", r"\1", value or "")
    value = value.replace("&", " and ").replace("-", " ")
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def canonical(value):
    key = compact(value)
    if key in ALIASES:
        return ALIASES[key]
    return next((name for name in dict.fromkeys(ALIASES.values()) if compact(name) == key), None)


def upgrade():
    bind = op.get_bind()
    subjects = bind.execute(sa.text("SELECT id FROM taxonomy_nodes WHERE kind = 'subject' AND name = 'Mathematics'" )).mappings()
    for subject in subjects:
        nodes = bind.execute(sa.text("SELECT id, name FROM taxonomy_nodes WHERE parent_id = :parent_id AND kind = 'chapter'"), {"parent_id": subject["id"]}).mappings().all()
        by_name = {node["name"]: node["id"] for node in nodes}
        for node in nodes:
            target_name = canonical(node["name"])
            if not target_name or node["name"] == target_name:
                continue
            target_id = by_name.get(target_name)
            if not target_id:
                bind.execute(sa.text("UPDATE taxonomy_nodes SET name = :name WHERE id = :id"), {"name": target_name, "id": node["id"]})
                by_name.pop(node["name"], None)
                by_name[target_name] = node["id"]
                continue
            links = bind.execute(sa.text("SELECT question_id FROM question_taxonomies WHERE taxonomy_node_id = :id"), {"id": node["id"]}).mappings().all()
            for link in links:
                exists = bind.execute(sa.text("SELECT 1 FROM question_taxonomies WHERE question_id = :question_id AND taxonomy_node_id = :target_id"), {"question_id": link["question_id"], "target_id": target_id}).first()
                if exists:
                    bind.execute(sa.text("DELETE FROM question_taxonomies WHERE question_id = :question_id AND taxonomy_node_id = :source_id"), {"question_id": link["question_id"], "source_id": node["id"]})
                else:
                    bind.execute(sa.text("UPDATE question_taxonomies SET taxonomy_node_id = :target_id WHERE question_id = :question_id AND taxonomy_node_id = :source_id"), {"target_id": target_id, "question_id": link["question_id"], "source_id": node["id"]})
            bind.execute(sa.text("DELETE FROM taxonomy_nodes WHERE id = :id"), {"id": node["id"]})


def downgrade():
    # Merging duplicate taxonomy nodes cannot be reversed safely.
    pass
