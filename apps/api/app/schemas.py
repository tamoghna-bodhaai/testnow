from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from .models import QuestionKind, Role

class APIModel(BaseModel): model_config=ConfigDict(from_attributes=True)
class RegisterIn(BaseModel): name:str=Field(min_length=2,max_length=160); email:EmailStr; password:str=Field(min_length=12,max_length=128); role:Role
class LoginIn(BaseModel): email:EmailStr; password:str
class QuestionIn(BaseModel):
    source_number:str|None=None; kind:QuestionKind; stem_markdown:str=Field(min_length=1); options:list[dict]=Field(default_factory=list); answer:dict|None=None; solution_markdown:str|None=None; scoring:dict=Field(default_factory=lambda:{"correct":4,"incorrect":-1,"unanswered":0}); diagrams:list[dict]=Field(default_factory=list); source_spans:list[dict]=Field(default_factory=list); confidence:float|None=None
class QuestionOut(QuestionIn): id:str; approved:bool=False
class AnswerKeyIn(BaseModel): answer:dict=Field(min_length=1); confidence:float|None=Field(default=None,ge=0,le=1)
class TestCreate(BaseModel): title:str=Field(min_length=2,max_length=255); subject:str=Field(min_length=2,max_length=120); duration_seconds:int=Field(ge=60,le=28800); opens_at:datetime|None=None; closes_at:datetime|None=None; max_attempts:int=Field(default=1,ge=1,le=10); results_policy:dict=Field(default_factory=lambda:{"mode":"immediate"}); question_ids:list[str]=Field(min_length=1)
class ResponseIn(BaseModel): answer:dict|None=None; marked_for_review:bool=False; revision:int=Field(ge=0)
class EnrolIn(BaseModel): email:EmailStr
