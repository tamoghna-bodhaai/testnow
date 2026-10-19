from io import BytesIO
from datetime import datetime,timezone
import fitz
from fastapi.testclient import TestClient
from app.main import app,question_view
from app.db import get_db
from app.security import current_user
from app.models import AuditEvent,ImportJob,Question,QuestionTaxonomy,TaxonomyNode,User
from test_readiness import db,add_question,content


def client_for(db):
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user]=lambda:db.get(User,'admin')
    return TestClient(app)


def test_library_finds_older_orphan_and_filters_by_issue_and_source_page(db):
    older=add_question(db,'older',kind='numerical',options=[],answer=None,interaction={'source_page':7})
    older.import_job_id=None
    for index in range(110):add_question(db,f'newer-{index:03}')
    db.commit();client=client_for(db)
    try:
        result=client.get('/admin/question-banks/bank/questions?search=older&source_id=unlinked&issue=invalid_answer').json()
        assert result['total']==1 and result['items'][0]['id']=='older'
        assert client.get('/admin/question-banks/bank/questions?source_page=7').json()['total']==1
        page=client.get('/admin/question-banks/bank/questions?page=5&page_size=25').json()
        assert page['total']==111 and len(page['items'])==11
        source=client.get('/admin/question-banks/bank/questions?source_id=source').json()
        assert source['total']==110
        assert client.get('/admin/question-banks/bank/questions/older').json()['kind']=='numerical'
    finally:app.dependency_overrides.clear()


def test_content_update_requires_current_revision_preserves_provenance_and_audits_reason(db):
    question=add_question(db,answer=None,source_spans=[{'page':4,'bbox':[1,2,3,4]}])
    client=client_for(db)
    try:
        opened=client.get('/admin/question-banks/bank/questions/q').json()
        body={'expected_revision':opened['revision'],'content':content(answer={'option':1},source_spans=[]),'approve':True,'reason':'Verified the key against the original source'}
        response=client.put('/admin/question-banks/bank/questions/q/content',json=body)
        assert response.status_code==200 and response.json()['readiness']['publishable']
        assert response.json()['source_spans']==[{'page':4,'bbox':[1,2,3,4]}]
        assert response.json()['answer']=={'option':1}
        assert client.put('/admin/question-banks/bank/questions/q/content',json=body).status_code==409
        assert db.get(Question,'q').answer=={'option':1}
        event=db.query(AuditEvent).filter_by(action='question.content_updated').one()
        assert event.actor_id=='admin' and event.data['reason']==body['reason']
    finally:app.dependency_overrides.clear()


def test_draft_save_invalidates_approval_and_advanced_json_rejects_unknown_fields(db):
    add_question(db);client=client_for(db)
    try:
        opened=client.get('/admin/question-banks/bank/questions/q').json()
        body={'expected_revision':opened['revision'],'content':content(answer=None),'approve':False,'reason':'Repair answer after source comparison'}
        saved=client.put('/admin/question-banks/bank/questions/q/content',json=body)
        assert saved.status_code==200 and not saved.json()['approved'] and not saved.json()['readiness']['content_valid']
        body['expected_revision']=saved.json()['revision'];body['content']['misspelled_field']='bad'
        assert client.put('/admin/question-banks/bank/questions/q/content',json=body).status_code==422
    finally:app.dependency_overrides.clear()


def test_type_specific_numeric_and_matrix_content_can_be_saved_without_rewriting_keys(db):
    question=add_question(db);client=client_for(db)
    try:
        opened=client.get('/admin/question-banks/bank/questions/q').json()
        body={'expected_revision':opened['revision'],'content':content(kind='numerical',options=[],answer={'value':'0'},interaction={'tolerance':0.01}),'approve':True,'reason':'Corrected numerical answer from source'}
        response=client.put('/admin/question-banks/bank/questions/q/content',json=body)
        assert response.status_code==200 and response.json()['answer']=={'value':'0'}
        body['expected_revision']=response.json()['revision'];body['content']=content(kind='matrix_match',options=[],answer={'matches':{'P':['1'],'Q':['2']}},interaction={'matrix_items':['P','Q'],'matrix_choices':['1','2']})
        matrix=client.put('/admin/question-banks/bank/questions/q/content',json=body)
        assert matrix.status_code==200 and matrix.json()['readiness']['publishable']
    finally:app.dependency_overrides.clear()


def nodes(db):
    db.add_all([TaxonomyNode(id='subject',bank_id='bank',kind='subject',name='Physics'),TaxonomyNode(id='chapter',bank_id='bank',kind='chapter',name='Matter',parent_id='subject'),TaxonomyNode(id='topic',bank_id='bank',kind='topic',name='Elasticity',parent_id='chapter'),TaxonomyNode(id='alias',bank_id='bank',kind='topic',name='Elasticity variant',parent_id='chapter',code='legacy-topic-code')]);db.commit()


def test_classification_and_missing_taxonomy_queue_cover_unclassified_records(db):
    add_question(db);nodes(db);client=client_for(db)
    try:
        assert client.get('/admin/question-banks/bank/questions?issue=missing_topic').json()['total']==1
        opened=client.get('/admin/question-banks/bank/questions/q').json()
        body={'expected_revision':opened['revision'],'content':content(),'approve':True,'taxonomy_ids':['subject','chapter','topic'],'reason':'Classified against the chapter heading'}
        result=client.put('/admin/question-banks/bank/questions/q/content',json=body)
        assert result.status_code==200
        assert client.get('/admin/question-banks/bank/questions?issue=missing_topic').json()['total']==0
        body['expected_revision']=result.json()['revision'];body['taxonomy_ids']=['topic']
        assert client.put('/admin/question-banks/bank/questions/q/content',json=body).status_code==422
    finally:app.dependency_overrides.clear()


def test_alias_requires_unchanged_preview_and_preserves_node_ids(db):
    add_question(db);nodes(db);db.add(QuestionTaxonomy(question_id='q',taxonomy_node_id='alias'));db.commit();client=client_for(db)
    try:
        body={'source_id':'alias','target_id':'topic'}
        preview=client.post('/admin/question-banks/bank/taxonomy-alias-preview',json=body).json()
        assert preview['affected_count']==1
        assert client.post('/admin/question-banks/bank/taxonomy-alias',json=body).status_code==409
        body['expected_impact']=preview['expected_impact']
        result=client.post('/admin/question-banks/bank/taxonomy-alias',json=body)
        assert result.status_code==200
        assert db.get(TaxonomyNode,'alias').alias_of_id=='topic'
        assert db.get(TaxonomyNode,'alias').code=='legacy-topic-code'
        assert db.get(TaxonomyNode,'topic') is not None
        assert db.get(QuestionTaxonomy,{'question_id':'q','taxonomy_node_id':'topic'})
        assert client.post('/admin/question-banks/bank/taxonomy-alias',json=body).json()['already_applied']
    finally:app.dependency_overrides.clear()


def test_source_relink_verifies_page_records_prior_provenance_and_invalidates_review(db,monkeypatch):
    question=add_question(db,interaction={'source_document_id':'missing-source'},source_spans=[{'page':9}]);question.import_job_id=None;db.commit()
    document=fitz.open();document.new_page();raw=document.tobytes();document.close()
    monkeypatch.setattr('app.figure_review.get',lambda _key:raw)
    client=client_for(db)
    try:
        opened=client.get('/admin/question-banks/bank/questions/q').json()
        body={'expected_revision':opened['revision'],'source_id':'source','page':1,'reason':'Matched text to the retained original page'}
        response=client.put('/admin/question-banks/bank/questions/q/source',json=body)
        assert response.status_code==200 and not response.json()['approved']
        assert response.json()['interaction']['previous_source_spans']==[{'page':9}]
        assert response.json()['source_spans']==[{'page':1}]
        assert client.get('/questions/q/figure-source').json()['source_page']==1
        assert client.get('/questions/q/source').content==raw
    finally:app.dependency_overrides.clear()


def test_missing_source_is_explicit_and_figure_writes_reject_stale_versions(db):
    question=add_question(db,interaction={},source_spans=[]);question.import_job_id=None;db.commit();client=client_for(db)
    try:
        assert client.get('/questions/q/figure-source').status_code==422
        response=client.put('/questions/q/figures',headers={'If-Match':'stale'},json={'diagrams':[],'confirmed':True})
        assert response.status_code==409
    finally:app.dependency_overrides.clear()


def test_alias_schema_migration_is_additive_and_reversible_without_repurposing_codes(monkeypatch):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine,MetaData,Table,Column,String,inspect,text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=Path(__file__).parents[1]/'alembic/versions/0016_taxonomy_aliases.py'
    spec=importlib.util.spec_from_file_location('taxonomy_alias_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        metadata=MetaData();table=Table('taxonomy_nodes',metadata,Column('id',String(36),primary_key=True),Column('code',String(120)))
        metadata.create_all(connection);connection.execute(table.insert().values(id='existing',code='existing-code'))
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        assert 'alias_of_id' in {column['name'] for column in inspect(connection).get_columns('taxonomy_nodes')}
        assert connection.execute(text("SELECT code FROM taxonomy_nodes WHERE id='existing'")).scalar()=='existing-code'
        migration.downgrade()
        assert 'alias_of_id' not in {column['name'] for column in inspect(connection).get_columns('taxonomy_nodes')}
        assert connection.execute(text("SELECT code FROM taxonomy_nodes WHERE id='existing'")).scalar()=='existing-code'


def test_teacher_cannot_read_another_owners_source_by_forging_private_question_metadata(db):
    from app.models import Role,QuestionKind,QuestionScope
    teacher=User(id='teacher',name='Teacher',email='teacher@example.com',role=Role.TEACHER)
    question=Question(id='forged',owner_id='teacher',kind=QuestionKind.SUBJECTIVE,scope=QuestionScope.PRIVATE,stem_markdown='Explain the result.',options=[],interaction={'source_document_id':'source'})
    db.add_all([teacher,question]);db.commit()
    app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[current_user]=lambda:db.get(User,'teacher')
    try:
        client=TestClient(app)
        assert client.get('/questions/forged/figure-source').status_code==422
        assert client.get('/questions/forged/source').status_code==422
    finally:app.dependency_overrides.clear()


def test_review_index_migration_supports_stable_pagination_and_reverses_without_data_changes(monkeypatch):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine,MetaData,Table,Column,String,DateTime,inspect,text
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path=Path(__file__).parents[1]/'alembic/versions/0017_review_pagination_indexes.py'
    spec=importlib.util.spec_from_file_location('review_index_migration',path);migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    engine=create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as connection:
        metadata=MetaData();table=Table('questions',metadata,Column('id',String(36),primary_key=True),Column('bank_id',String(36)),Column('import_job_id',String(36)),Column('created_at',DateTime))
        metadata.create_all(connection);connection.execute(table.insert().values(id='existing',bank_id='bank',created_at=datetime.now(timezone.utc)))
        monkeypatch.setattr(migration,'op',Operations(MigrationContext.configure(connection)))
        migration.upgrade()
        plan=connection.execute(text("EXPLAIN QUERY PLAN SELECT id FROM questions WHERE bank_id='bank' ORDER BY created_at,id LIMIT 25")).all()
        assert any('ix_questions_bank_review' in str(row) for row in plan)
        migration.downgrade()
        assert not inspect(connection).get_indexes('questions')
        assert connection.execute(text('SELECT count(*) FROM questions')).scalar()==1
