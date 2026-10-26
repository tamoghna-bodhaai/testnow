"""Evidence-first learning diagnostics for published assessment snapshots."""
from collections import defaultdict
from datetime import datetime, timezone
import json
import re

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .models import Attempt, AttemptEvent, QuestionTaxonomy, Response, TaxonomyNode, Test, TestVersion
from .services import score


def _topic(question:dict, kind:str)->str:
    return next((str(node.get("name")) for node in question.get("taxonomy",[]) if node.get("kind")==kind), "Unclassified")


def _local_labels(question:dict)->dict[str,str]:
    """Useful deterministic fallback when the classifier cannot be reached."""
    stem=re.sub(r"\\[A-Za-z]+|\$|[{}]"," ",str(question.get("stem_markdown") or "")).lower()
    chapter=_topic(question,"chapter");topic=_topic(question,"topic")
    if re.search(r"area|region|enclosed|bounded|shaded",stem) and re.search(r"curve|parabola|graph|x.axis|y.axis",stem):
        return {"subject":"Mathematics","chapter":chapter if chapter!="Unclassified" else "Applications of Integrals","topic":topic if topic!="Unclassified" else "Area under curves","skill":"Area between curves" if re.search(r"two curves|between the curves|curves .* and",stem) else "Area bounded by a curve and axes"}
    if "relative velocity" in stem or "velocity of a relative to b" in stem:
        return {"subject":"Physics","chapter":chapter if chapter!="Unclassified" else "Kinematics","topic":topic if topic!="Unclassified" else "Relative velocity","skill":"Relative velocity in two dimensions" if re.search(r"angle|direction|vector|east|north|river",stem) else "Relative velocity"}
    return {"subject":_topic(question,"subject"),"chapter":chapter,"topic":topic,"skill":str((question.get("interaction") or {}).get("diagnostic_skill") or topic)}


def classify_skills(questions:list[dict])->dict[str,str]:
    """Classify once, filling missing taxonomy as well as a finer skill."""
    labels={};missing={q["id"]:{kind for kind in ("subject","chapter","topic") if _topic(q,kind)=="Unclassified"} for q in questions}
    for question in questions:
        fallback=_local_labels(question)
        taxonomy=list(question.get("taxonomy") or [])
        for kind in ("subject","chapter","topic"):
            if _topic(question,kind)=="Unclassified" and fallback[kind]!="Unclassified":taxonomy.append({"kind":kind,"name":fallback[kind]})
        question["taxonomy"]=taxonomy
        labels[question["id"]]=str((question.get("interaction") or {}).get("diagnostic_skill") or question.get("skill") or fallback["skill"])[:100]
    eligible=[q for q in questions if not (q.get("interaction") or {}).get("diagnostic_skill") and (not q.get("skill") or _topic(q,"topic")=="Unclassified")]
    if not eligible or not settings().openrouter_api_key:return labels
    payload=[{"id":q["id"],"subject":_topic(q,"subject"),"chapter":_topic(q,"chapter"),"topic":_topic(q,"topic"),"question":str(q.get("stem_markdown") or "")[:1200]} for q in eligible]
    try:
        response=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings().openrouter_api_key}","HTTP-Referer":"https://testnow.app","X-Title":"TestNow diagnostics"},json={"model":settings().openrouter_model,"messages":[{"role":"system","content":"Return a JSON object with a labels array. Every item must contain id, subject, chapter, topic, and label. Use short established curriculum names; label is the specific skill tested, not a claim about the student. Reuse the same names for equivalent questions. Replace Unclassified where the question supports a clear label. Do not invent a misconception or include student data."},{"role":"user","content":json.dumps(payload)}],"response_format":{"type":"json_object"},"max_tokens":min(4096,settings().openrouter_max_output_tokens)},timeout=min(20,settings().openrouter_timeout_seconds))
        response.raise_for_status()
        generated=json.loads(response.json()["choices"][0]["message"]["content"])
        for item in generated.get("labels",[]):
            if isinstance(item,dict) and item.get("id") in labels and isinstance(item.get("label"),str):
                label=re.sub(r"\s+"," ",item["label"]).strip()
                if 3<=len(label)<=100:labels[item["id"]]=label
                question=next(q for q in eligible if q["id"]==item["id"])
                for kind in ("subject","chapter","topic"):
                    if kind not in missing[question["id"]]:continue
                    name=re.sub(r"\s+"," ",str(item.get(kind) or "")).strip()
                    if not 3<=len(name)<=100 or name.lower()=="unclassified":continue
                    taxonomy=[node for node in question["taxonomy"] if node.get("kind")!=kind]
                    taxonomy.append({"kind":kind,"name":name})
                    question["taxonomy"]=taxonomy
    except (httpx.HTTPError,KeyError,ValueError,TypeError):
        pass
    return labels


def ensure_version_labels(db:Session, version:TestVersion)->None:
    """Backfill only diagnostic metadata on versions published before this feature."""
    original=version.content.get("questions") or []
    if all(q.get("skill") and (_topic(q,"topic")!="Unclassified" or q.get("diagnostic_label_source")=="backfilled") for q in original):return
    questions=[dict(q) for q in original]
    ids=[q["id"] for q in questions]
    linked=defaultdict(list)
    if ids:
        for qid,node in db.query(QuestionTaxonomy.question_id,TaxonomyNode).join(TaxonomyNode,TaxonomyNode.id==QuestionTaxonomy.taxonomy_node_id).filter(QuestionTaxonomy.question_id.in_(ids)).all():
            linked[qid].append({"kind":node.kind,"name":node.name,"code":node.code})
    for question in questions:
        taxonomy=list(question.get("taxonomy") or [])
        for node in linked.get(question["id"],[]):
            if not any(existing.get("kind")==node["kind"] for existing in taxonomy):taxonomy.append(node)
        question["taxonomy"]=taxonomy
    labels=classify_skills(questions)
    for question in questions:
        question["skill"]=labels[question["id"]]
        question["diagnostic_label_source"]="backfilled"
    version.content={**version.content,"questions":questions}
    db.commit()


def timing_for_attempt(db:Session, attempt:Attempt)->dict[str,dict]:
    events=db.query(AttemptEvent).filter_by(attempt_id=attempt.id,event_type="question_focus_interval").order_by(AttemptEvent.created_at,AttemptEvent.id).all()
    grouped=defaultdict(lambda:{"seconds":0,"visits":0})
    seen=set();remaining=max(0,((attempt.submitted_at or attempt.ends_at)-attempt.started_at).total_seconds())
    for event in events:
        data=event.data or {}; token=data.get("interval_id"); qid=data.get("question_id")
        if not token or token in seen or not qid:continue
        seen.add(token)
        duration=data.get("duration_ms")
        if not isinstance(duration,(int,float)) or duration<0 or duration>900000:continue
        used=min(duration/1000,remaining)
        remaining-=used
        grouped[qid]["seconds"]+=used
        grouped[qid]["visits"]+=1
    return {qid:{"seconds":round(item["seconds"],1),"visits":item["visits"]} for qid,item in grouped.items()}


def attempt_diagnostics(db:Session, attempt:Attempt)->dict:
    version=db.get(TestVersion,attempt.version_id)
    ensure_version_labels(db,version)
    answers={r.question_id:r.answer for r in db.query(Response).filter_by(attempt_id=attempt.id)}
    _,review=score(version.content,answers)
    timing=timing_for_attempt(db,attempt)
    questions=[]
    for ordinal,item in enumerate(review,1):
        response=item["response"]
        outcome="unanswered" if response is None else "correct" if item["correct"] else "incorrect"
        question={"id":item["id"],"number":ordinal,"stem_markdown":item.get("stem_markdown",""),"subject":_topic(item,"subject"),"chapter":_topic(item,"chapter"),"topic":_topic(item,"topic"),"skill":item.get("skill") or (item.get("interaction") or {}).get("diagnostic_skill") or _topic(item,"topic"),"outcome":outcome,"points":item["points"],"timing":timing.get(item["id"]),"attempt_id":attempt.id,"test_id":attempt.test_id}
        diagnostic_choices=(item.get("interaction") or {}).get("diagnostic_choices") or {}
        if outcome=="incorrect" and isinstance(diagnostic_choices,dict) and isinstance(response,dict) and "option" in response:
            meaning=diagnostic_choices.get(str(response["option"]))
            if isinstance(meaning,str) and meaning.strip():question["possible_misconception"]=meaning.strip()[:240]
        questions.append(question)
    test=db.get(Test,attempt.test_id)
    return {"attempt_id":attempt.id,"test_id":attempt.test_id,"test_title":test.title if test else "Assessment","submitted_at":attempt.submitted_at,"questions":questions,"skills":summarize(questions,"skill"),"topics":summarize(questions,"topic")}


def summarize(questions:list[dict], key:str)->list[dict]:
    groups=defaultdict(list)
    for question in questions:groups[(question["subject"],question["chapter"],question["topic"] if key=="skill" else "",question[key])].append(question)
    summaries=[]
    for (subject,chapter,topic,label),items in groups.items():
        counts={name:sum(q["outcome"]==name for q in items) for name in ("correct","incorrect","unanswered")}
        students={q.get("student_id") for q in items if q.get("student_id")}
        summaries.append({"label":label,"subject":subject,"chapter":chapter,"topic":topic if key=="skill" else label,"question_count":len({(q["test_id"],q["id"]) for q in items}),"evidence_count":len(items),"student_count":len(students),**counts,"finding":"Needs attention" if counts["incorrect"]+counts["unanswered"]>=2 and counts["correct"]==0 else "Mixed evidence" if counts["incorrect"]+counts["unanswered"] else "Answered correctly","confidence":"limited evidence" if len(items)<3 else "multiple questions","questions":[{"id":q["id"],"number":q["number"],"test_id":q["test_id"],"attempt_id":q["attempt_id"],"student_id":q.get("student_id"),"outcome":q["outcome"]} for q in items]})
    return sorted(summaries,key=lambda row:(-row["incorrect"]-row["unanswered"],row["label"]))


def progress(db:Session, attempts:list[Attempt])->dict:
    records=[attempt_diagnostics(db,a) for a in attempts if a.submitted_at and not a.cancelled_at]
    questions=[q for record in records for q in record["questions"]]
    return {"attempts":records,"skills":summarize(questions,"skill"),"topics":summarize(questions,"topic")}
