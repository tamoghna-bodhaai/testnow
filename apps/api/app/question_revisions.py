"""Content revision tokens for optimistic admin editing, including classification."""
import hashlib
import json
from fastapi import HTTPException


def question_revision(question,taxonomy_ids=()):
    interaction={key:value for key,value in (question.interaction or {}).items() if key!="readiness"}
    payload={key:getattr(question,key,None) for key in ("id","kind","stem_markdown","options","answer","solution_markdown","diagrams","source_spans","scoring","approved_at")}
    payload["interaction"]=interaction;payload["taxonomy_ids"]=sorted(taxonomy_ids)
    return hashlib.sha256(json.dumps(payload,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


def require_revision(question,expected,taxonomy_ids=()):
    if expected!=question_revision(question,taxonomy_ids):
        raise HTTPException(409,"This question changed since it was opened. Your unsaved work was kept; reload the saved version and compare before applying it")
