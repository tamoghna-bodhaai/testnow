"""Stable server pagination for bounded admin review summaries."""
from collections import Counter
from .models import Question
from .readiness import validate_question


def question_page(query, *, page=1, page_size=25, review_filter="all", search=""):
    counts=Counter();selected=[]
    for question in query.order_by(Question.created_at,Question.id).yield_per(250):
        result=validate_question(question)
        generated=(question.interaction or {}).get("solution_generation",{}).get("status")=="completed"
        counts["all"]+=1;counts["attention"]+=not result.publishable;counts["published"]+=result.publishable;counts["generated"]+=generated
        if review_filter=="attention" and result.publishable:continue
        if review_filter=="published" and not result.publishable:continue
        if review_filter=="generated" and not generated:continue
        if search and search.casefold() not in f"{question.id} {question.source_number or ''} {question.stem_markdown}".casefold():continue
        selected.append(question.id)
    total=len(selected);ids=selected[(page-1)*page_size:page*page_size]
    by_id={q.id:q for q in query.filter(Question.id.in_(ids))} if ids else {}
    items=[]
    for qid in ids:
        q=by_id[qid]
        items.append({"id":q.id,"source_number":q.source_number,"kind":q.kind.value,
                      "preview":q.stem_markdown[:180],"source_page":(q.interaction or {}).get("source_page"),
                      "readiness":validate_question(q).to_dict()})
    return {"items":items,"page":page,"page_size":page_size,"total":total,"counts":dict(counts),"has_more":page*page_size<total}
