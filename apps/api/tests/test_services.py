from app.services import score

def test_scoring_uses_question_policy():
    content={"questions":[
      {"id":"a","answer":{"option":1},"scoring":{"correct":4,"incorrect":-1,"unanswered":0}},
      {"id":"b","answer":{"value":"42"},"scoring":{"correct":3,"incorrect":0,"unanswered":0}},
    ]}
    total,review=score(content,{"a":{"option":1},"b":{"value":"0"}})
    assert total==4
    assert review[0]["correct"] is True
    assert review[1]["points"]==0

def test_unanswered_is_not_wrong():
    total,review=score({"questions":[{"id":"a","answer":{"option":0},"scoring":{"correct":4,"incorrect":-1,"unanswered":0}}]}, {})
    assert total==0 and review[0]["points"]==0
