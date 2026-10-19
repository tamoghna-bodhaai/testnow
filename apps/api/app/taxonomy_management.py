"""Previewed, audited alias consolidation without deleting taxonomy identifiers."""
import hashlib,json
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session
from .db import get_db
from .models import QuestionTaxonomy,Role,TaxonomyNode,User
from .schemas import TaxonomyAliasIn
from .security import require
from .services import audit

router=APIRouter()


def alias_plan(db,bank_id,data):
    source=db.get(TaxonomyNode,data.source_id);target=db.get(TaxonomyNode,data.target_id)
    if not source or not target or source.bank_id!=bank_id or target.bank_id!=bank_id:raise HTTPException(404,"Taxonomy alias nodes not found in this bank")
    if source.id==target.id or source.kind!=target.kind or source.parent_id!=target.parent_id:raise HTTPException(422,"Aliases must be distinct nodes of the same kind under the same parent")
    if target.alias_of_id:raise HTTPException(422,"Choose a canonical target rather than another alias")
    if db.query(TaxonomyNode.id).filter_by(parent_id=source.id).first():raise HTTPException(422,"Consolidate leaf aliases first; a node with children requires an explicit hierarchy migration")
    ids=sorted(link.question_id for link in db.query(QuestionTaxonomy).filter_by(taxonomy_node_id=source.id))
    impact=hashlib.sha256(json.dumps([source.id,source.name,source.alias_of_id,target.id,target.name,target.alias_of_id,ids],sort_keys=True).encode()).hexdigest()
    return source,target,{"source_id":source.id,"target_id":target.id,"affected_count":len(ids),"question_ids":ids,"expected_impact":impact,"identifiers_preserved":True}


@router.post('/admin/question-banks/{bank_id}/taxonomy-alias-preview')
def preview_alias(bank_id:str,data:TaxonomyAliasIn,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    return alias_plan(db,bank_id,data)[2]


@router.post('/admin/question-banks/{bank_id}/taxonomy-alias')
def apply_alias(bank_id:str,data:TaxonomyAliasIn,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    # Serialize taxonomy consolidation within this bank, including content-link writes.
    from .models import QuestionBank
    bank=db.query(QuestionBank).filter_by(id=bank_id).with_for_update().one_or_none()
    if not bank:raise HTTPException(404,"Question bank not found")
    source,target,preview=alias_plan(db,bank_id,data)
    from .models import Question
    if preview["question_ids"]:
        db.query(Question).filter(Question.id.in_(preview["question_ids"])).with_for_update().all()
        source,target,preview=alias_plan(db,bank_id,data)
    if source.alias_of_id==target.id and not preview['affected_count']:return {**preview,"already_applied":True}
    if data.expected_impact!=preview['expected_impact']:raise HTTPException(409,"Alias impact changed. Preview again before applying")
    existing_targets=[qid for qid in preview['question_ids'] if db.get(QuestionTaxonomy,{'question_id':qid,'taxonomy_node_id':target.id})]
    for qid in preview['question_ids']:
        link=db.get(QuestionTaxonomy,{"question_id":qid,"taxonomy_node_id":source.id})
        if not db.get(QuestionTaxonomy,{"question_id":qid,"taxonomy_node_id":target.id}):db.add(QuestionTaxonomy(question_id=qid,taxonomy_node_id=target.id))
        db.delete(link)
    source.alias_of_id=target.id
    audit(db,user.id,'taxonomy.alias_applied','taxonomy',source.id,{**preview,"source_name":source.name,"target_name":target.name,"target_links_previously_present":existing_targets})
    db.commit();return {**preview,"already_applied":False}
